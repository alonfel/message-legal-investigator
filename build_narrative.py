#!/usr/bin/env python3
"""
Phase 4: Build a narrative document from selected sections of the final report.

Uses Claude to synthesize findings + conversation context into two sections:
  - חלק א: ציר זמן  — chronological narrative prose
  - חלק ב: ניתוח משפטי — legal argument: claim → evidence → conclusion

Usage:
  python3 build_narrative.py --sections 3,7,12 --narrative "דפוס שליטה כלכלית לאורך זמן"
  python3 build_narrative.py --sections 3 --narrative "..." --context-lines 10
  python3 build_narrative.py --sections 3,7 --narrative "..." --output results/my_doc.txt
  python3 build_narrative.py --sections 3,7 --narrative "..." --verified-only
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    MODEL, RESULTS_DIR, configure_paths,
    call_claude_streaming, metrics, reset_metrics, log,
)
from validate_report import (
    Finding, Section,
    parse_report, extract_context, _parse_source,
    STATUS_NOT_FOUND,
)

SEP  = "=" * 80
THIN = "─" * 80

NARRATIVE_SYSTEM = """אתה עורך דין המנתח תכתובות ווטסאפ לצורך הכנת מסמך משפטי.

תפקידך: לקחת ממצאים ממוספרים (ציטוטים, תאריכים, הקשר שיחה) ולבנות מהם מסמך נרטיבי מגובש בעברית.

כללים:
1. שמור על כל ציטוט מילה-במילה בין מרכאות.
2. ציין תמיד את המקור (שם קובץ + עמוד) לצד כל ציטוט.
3. אל תמציא עובדות שאינן בחומר שניתן לך.
4. כתוב בסגנון משפטי-עסקי, ברור ותמציתי.
5. פרק א (ציר זמן) — ספר את האירועים בסדר כרונולוגי, חבר בין הציטוטים בפרוזה קצרה.
6. פרק ב (ניתוח משפטי) — הצג טענה → שרשרת ראיות → מסקנה.
"""


def _parse_date(date_str: str) -> datetime.datetime | None:
    """Parse DD.MM.YYYY HH:MM (or just DD.MM.YYYY). Handles ranges by taking first part."""
    s = date_str.strip()
    # Range like "3.10.2022-4.10.2022" — take first segment
    if re.search(r"\d{1,2}\.\d{1,2}\.\d{4}-\d{1,2}", s):
        s = s.split("-")[0].strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _build_user_prompt(
    narrative: str,
    findings: list[tuple[Section, Finding]],
    context_lines: int,
) -> str:
    """Build the Hebrew user message sent to Claude."""
    lines = [
        f"זווית נרטיבית: {narrative}",
        "",
        f"להלן {len(findings)} ממצאים רלוונטיים (ציטוט + הקשר שיחה):",
        THIN,
    ]

    for i, (sec, f) in enumerate(findings, 1):
        ctx = extract_context(f, max_lines=context_lines)
        lines.append(f"\n[{i}] סעיף {sec.num}: {sec.title}")
        lines.append(f"    ציטוט : \"{f.citation}\"")
        lines.append(f"    תאריך : {f.date}")
        lines.append(f"    מקור  : {f.source_raw}")
        if ctx:
            lines.append("    הקשר שיחה:")
            for cl in ctx.splitlines():
                lines.append(f"      {cl}")

    lines += [
        THIN,
        "",
        "אנא כתוב מסמך נרטיבי מגובש המבנה כך:",
        "",
        "## חלק א: ציר זמן",
        "סדר את הממצאים לפי סדר כרונולוגי. כתוב פרוזה קצרה המחברת בין הציטוטים ומראה",
        "כיצד המצב התפתח לאורך הזמן. כלול את הציטוט המדויק + מקור לכל ממצא.",
        "",
        "## חלק ב: ניתוח משפטי",
        f"הצג את הטענה המרכזית בהתבסס על הזווית: '{narrative}'.",
        "מבנה: טענה → שרשרת ראיות (ממוספרת, עם ציטוטים) → מסקנה.",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 4: Build a narrative from selected report sections."
    )
    parser.add_argument(
        "--sections", required=True,
        help="Comma-separated section numbers, e.g. 3,7,12",
    )
    parser.add_argument(
        "--narrative", required=True,
        help='Narrative angle in Hebrew, e.g. "דפוס שליטה כלכלית לאורך זמן"',
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output file path (default: results/narrative_YYYYMMDD_HHMMSS.txt)",
    )
    parser.add_argument(
        "--context-lines", type=int, default=30, metavar="N",
        help="Surrounding conversation lines per finding sent to Claude (default: 30)",
    )
    parser.add_argument(
        "--verified-only", action="store_true",
        help="Skip findings tagged ✗ NOT FOUND (requires Phase 3 to have been run)",
    )
    parser.add_argument(
        "--input", type=Path, default=None,
        help="Input report (default: results/final_report.txt)",
    )
    parser.add_argument("--input-dir", default=None, metavar="DIR")
    parser.add_argument("--project-dir", default=None, metavar="DIR")
    parser.add_argument(
        "--show-prompt", action="store_true",
        help="Print the full system + user prompt to stdout before calling Claude",
    )
    parser.add_argument(
        "--save-prompt", action="store_true",
        help="Save the full prompt to a _prompt_TIMESTAMP.txt file alongside the narrative",
    )
    args = parser.parse_args()

    configure_paths(args.input_dir, args.project_dir)
    reset_metrics()

    if args.input is None:
        args.input = RESULTS_DIR / "final_report.txt"

    if args.output is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = RESULTS_DIR / f"narrative_{ts}.txt"

    # Parse requested section numbers
    try:
        requested = {int(s.strip()) for s in args.sections.split(",")}
    except ValueError:
        print("ERROR: --sections must be comma-separated integers, e.g. 3,7,12", file=sys.stderr)
        sys.exit(1)

    if not args.input.exists():
        print(f"ERROR: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    print(f"\n=== Phase 4: Narrative Builder ===\n")
    print(f"Input   : {args.input}")
    print(f"Sections: {sorted(requested)}")
    print(f"Angle   : {args.narrative}")
    print(f"Output  : {args.output}")
    print()

    # Parse report and filter to requested sections
    log("Parsing report...")
    all_sections = parse_report(args.input)
    selected = [s for s in all_sections if s.num in requested]

    missing = requested - {s.num for s in selected}
    if missing:
        print(f"WARNING: sections not found in report: {sorted(missing)}")

    # Log parsed sections
    log(f"Found {len(selected)} sections:")
    for sec in selected:
        log(f"  [{sec.num}] {sec.title}  ({len(sec.findings)} findings)")

    # Resolve source fields for all findings
    for sec in selected:
        for f in sec.findings:
            f.pdf_name, f.page_num = _parse_source(f.source_raw)

    # Flatten findings, optionally filtering out NOT FOUND
    pairs: list[tuple[Section, Finding]] = []
    skipped = 0
    for sec in selected:
        for f in sec.findings:
            if args.verified_only and f.status == STATUS_NOT_FOUND:
                skipped += 1
                log(f"  SKIP (not verified): \"{f.citation[:60]}...\"  [{f.source_raw}]")
                continue
            pairs.append((sec, f))

    if skipped:
        log(f"Skipped {skipped} unverified findings (--verified-only)")

    if not pairs:
        print("No findings found in the selected sections. Exiting.")
        sys.exit(0)

    log(f"{len(pairs)} findings across {len(selected)} sections")

    # Sort chronologically by date; undated findings go last
    pairs.sort(key=lambda t: (_parse_date(t[1].date) or datetime.datetime.max, t[0].num))

    # Log findings in the order they will be sent to Claude
    log("\nFindings in chronological order:")
    for i, (sec, f) in enumerate(pairs, 1):
        ctx_preview = ""
        ctx = extract_context(f, max_lines=args.context_lines)
        ctx_lines = len(ctx.splitlines()) if ctx else 0
        ctx_preview = f"  ({ctx_lines} context lines)" if ctx_lines else "  (no context)"
        citation_preview = f.citation[:70].replace("\n", " ")
        if len(f.citation) > 70:
            citation_preview += "..."
        log(f"  [{i:2d}] {f.date or '(no date)':>18}  §{sec.num} {sec.title[:30]}  \"{citation_preview}\"{ctx_preview}")

    print()

    # Build prompt and call Claude
    log(f"Building prompt (context_lines={args.context_lines})...")
    user_msg = _build_user_prompt(args.narrative, pairs, args.context_lines)
    prompt_chars = len(NARRATIVE_SYSTEM) + len(user_msg)
    log(f"Prompt size: {prompt_chars:,} chars (~{prompt_chars//2:,} tokens estimated)")
    log(f"System prompt: {len(NARRATIVE_SYSTEM):,} chars  |  User message: {len(user_msg):,} chars")

    if args.show_prompt:
        print(f"\n{'='*80}")
        print("SYSTEM PROMPT:")
        print('─'*80)
        print(NARRATIVE_SYSTEM)
        print(f"\n{'='*80}")
        print("USER MESSAGE:")
        print('─'*80)
        print(user_msg)
        print(f"{'='*80}\n")

    if args.save_prompt:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        prompt_path = RESULTS_DIR / f"narrative_prompt_{ts}.txt"
        prompt_text = (
            f"=== SYSTEM PROMPT ===\n\n{NARRATIVE_SYSTEM}\n\n"
            f"=== USER MESSAGE ===\n\n{user_msg}\n"
        )
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt_text, encoding="utf-8")
        log(f"Prompt saved to: {prompt_path}")

    client = anthropic.Anthropic(api_key=api_key)
    metrics._api_key = api_key

    log("Calling Claude (streaming)...")
    result = call_claude_streaming(
        client,
        system=NARRATIVE_SYSTEM,
        user_message=user_msg,
        max_tokens=12000,
        label="narrative",
    )

    # Build output document
    finding_index_lines = []
    for i, (sec, f) in enumerate(pairs, 1):
        citation_preview = f.citation[:80].replace("\n", " ")
        if len(f.citation) > 80:
            citation_preview += "..."
        finding_index_lines.append(
            f"  [{i:2d}] {f.date or '(no date)':>18}  §{sec.num} {sec.title[:28]}  \"{citation_preview}\""
        )

    header_lines = [
        SEP,
        "מסמך נרטיבי — תכתובות ווטסאפ יונתן-מיטל",
        SEP,
        f"תאריך הפקה  : {datetime.datetime.now().strftime('%d.%m.%Y  %H:%M:%S')}",
        f"זווית נרטיבית: {args.narrative}",
        f"סעיפים      : {', '.join(str(n) for n in sorted(requested))}",
        f"ממצאים      : {len(pairs)}",
        f"מודל        : {MODEL}",
        f"הקשר לממצא  : {args.context_lines} שורות",
        THIN,
        "ממצאים לפי סדר כרונולוגי (כפי שנשלחו ל-Claude):",
        *finding_index_lines,
        THIN,
        "",
    ]

    output_text = "\n".join(header_lines) + result + "\n\n" + metrics.report_block()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output_text, encoding="utf-8")

    print(f"\nDone. Saved to: {args.output}  ({len(output_text):,} chars)")
    print(metrics.report_block())


if __name__ == "__main__":
    main()
