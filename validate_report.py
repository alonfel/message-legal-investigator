#!/usr/bin/env python3
"""
Phase 3: Validate and enrich the final report. No LLM calls — runs in seconds.

Modes (combinable):
  --verify   Check each citation exists in the source extracted text
  --enrich   Add surrounding conversation context below each citation

Outputs (auto-named based on modes):
  results/phase3_verified_report.txt        (--verify)
  results/phase3_enriched_report.txt        (--enrich)
  results/phase3_verified_enriched_report.txt  (--verify --enrich)
  results/verification_issues.txt          (when --verify finds problems)

Usage:
  python3 validate_report.py --verify
  python3 validate_report.py --enrich
  python3 validate_report.py --verify --enrich
  python3 validate_report.py --input results/final_report.txt --output /tmp/out.txt --verify --enrich
"""

import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analysis_utils
from analysis_utils import INVESTIGATION_ITEMS, _PAGE_MARKER_RE, _slice_pages

THIN = "─" * 80
SEP  = "=" * 80

STATUS_VERIFIED  = "✓ VERIFIED"
STATUS_NEARBY    = "~ NEARBY"
STATUS_NOT_FOUND = "✗ NOT FOUND"
STATUS_NO_SOURCE = "? NO SOURCE"

_ALL_PDF_NAMES = [f"yoni-meital{i}.pdf" for i in range(7)]


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Finding:
    raw: str
    citation: str
    date: str
    source_raw: str
    pdf_name: str | None = None
    page_num: int | None = None
    status: str = ""
    context: str = ""


@dataclass
class Section:
    num: int
    title: str
    findings: list[Finding] = field(default_factory=list)
    empty: bool = False


# ─── Parsing the report ──────────────────────────────────────────────────────

def _parse_bullet(line: str) -> Finding | None:
    """Parse a bullet line: • ציטוט: "..." | תאריך: ... | מקור: ..."""
    if not line.startswith("• ציטוט:"):
        return None
    parts = re.split(r"\s+\|\s+", line[1:].strip())
    if len(parts) < 3:
        return None
    citation = re.sub(r'^ציטוט:\s*"?', "", parts[0]).rstrip('"').strip()
    date     = re.sub(r'^תאריך:\s*', "", parts[1]).strip()
    source   = re.sub(r'^מקור:\s*',  "", " | ".join(parts[2:])).strip()
    return Finding(raw=line, citation=citation, date=date, source_raw=source)


def parse_report(path: Path) -> list[Section]:
    """Parse final_report.txt into Section objects (preserving all 54 sections)."""
    section_re = re.compile(r"^סעיף (\d+):\s*(.+)$")
    sections: list[Section] = []
    current: Section | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        m = section_re.match(line)
        if m:
            if current is not None:
                sections.append(current)
            current = Section(num=int(m.group(1)), title=m.group(2).strip())
            continue
        if current is not None:
            if "לא נמצאה עדות מפורשת בטקסט" in line:
                current.empty = True
            elif line.startswith("• ציטוט:"):
                f = _parse_bullet(line)
                if f:
                    current.findings.append(f)

    if current is not None:
        sections.append(current)
    return sections


# ─── Source field parsing ─────────────────────────────────────────────────────

_SOURCE_RE = re.compile(r"(yoni-meital\d+\.pdf)?[,\s]*עמוד\s+(\d+)", re.UNICODE)


def _parse_source(source_raw: str) -> tuple[str | None, int | None]:
    """Extract (pdf_name, page_num) from the מקור field. Page ranges take first number."""
    m = _SOURCE_RE.search(source_raw)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


# ─── Extracted text helpers ──────────────────────────────────────────────────

_extracted_cache: dict[str, str | None] = {}


def _load_extracted(pdf_name: str) -> str | None:
    if pdf_name not in _extracted_cache:
        path = analysis_utils.EXTRACTED_DIR / pdf_name.replace(".pdf", ".txt")
        _extracted_cache[pdf_name] = path.read_text(encoding="utf-8") if path.exists() else None
    return _extracted_cache[pdf_name]


def _get_page_text(full_text: str, page_num: int, margin: int) -> str:
    """Return text for page_num (1-based) ± margin pages."""
    start = max(0, page_num - 1 - margin)
    end = page_num + margin
    return _slice_pages(full_text, start, end)


# ─── Citation verification ────────────────────────────────────────────────────

_RTL_RE    = re.compile(r"[\u200f\u200e\u202a-\u202e]")
_QUOTES_RE = re.compile(r'[״""\'`]')


def _normalize(text: str) -> str:
    text = _RTL_RE.sub("", text)
    text = _QUOTES_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _found_in(citation: str, page_text: str) -> bool:
    nc = _normalize(citation)
    np = _normalize(page_text)

    if nc in np:
        return True

    # Composite citations joined by " + " — check each segment
    if "+" in nc:
        segments = [s.strip().strip('"') for s in nc.split("+") if s.strip()]
        hits = sum(1 for s in segments if s and s in np)
        if hits >= max(1, len(segments) // 2):
            return True

    # Sliding 4-word window — handles minor OCR/whitespace differences
    words = nc.split()
    window = 4 if len(words) >= 4 else (2 if len(words) >= 2 else 0)
    for start in range(len(words) - window + 1):
        chunk = " ".join(words[start : start + window])
        if chunk in np:
            return True

    return False


def verify_finding(f: Finding) -> None:
    """Set f.status and confirm f.pdf_name by searching the extracted text."""
    if f.page_num is None:
        f.status = STATUS_NO_SOURCE
        return

    candidates = [f.pdf_name] if f.pdf_name else _ALL_PDF_NAMES

    for pdf in candidates:
        full = _load_extracted(pdf)
        if full is None:
            continue
        if _found_in(f.citation, _get_page_text(full, f.page_num, 0)):
            f.pdf_name = pdf
            f.status = STATUS_VERIFIED
            return
        if _found_in(f.citation, _get_page_text(full, f.page_num, 1)):
            f.pdf_name = pdf
            f.status = STATUS_NEARBY
            return

    f.status = STATUS_NOT_FOUND


# ─── Context extraction ───────────────────────────────────────────────────────

def extract_context(f: Finding, max_lines: int = 40) -> str:
    """Return up to max_lines of surrounding page text for a finding."""
    if f.page_num is None:
        return ""

    candidates = [f.pdf_name] if f.pdf_name else _ALL_PDF_NAMES
    for pdf in candidates:
        full = _load_extracted(pdf)
        if full is None:
            continue
        text = _get_page_text(full, f.page_num, 0)
        if not text.strip():
            continue
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["  [...]"]
        return "\n".join(lines)

    return ""


# ─── Output rendering ─────────────────────────────────────────────────────────

def _format_finding(f: Finding, verify: bool, enrich: bool) -> list[str]:
    bullet = f"{f.raw}  [{f.status}]" if (verify and f.status) else f.raw
    out = [bullet]
    if enrich and f.context:
        out.append("  הקשר:")
        out.append("  " + "·" * 38)
        for cl in f.context.splitlines():
            out.append(f"  {cl}")
        out.append("  " + "·" * 38)
    return out


def _section_titles() -> dict[int, str]:
    titles = {}
    for m in re.finditer(r"^(\d+)\.\s+(.+)$", INVESTIGATION_ITEMS, re.MULTILINE):
        titles[int(m.group(1))] = m.group(2).strip()
    return titles


def build_output(sections: list[Section], verify: bool, enrich: bool) -> str:
    titles = _section_titles()
    section_map = {s.num: s for s in sections}
    all_findings = [f for s in sections for f in s.findings]

    out = [
        SEP,
        "דוח ממצאים — תכתובות ווטסאפ יונתן-מיטל",
        SEP,
        f"תאריך ריצה : {datetime.datetime.now().strftime('%d.%m.%Y  %H:%M:%S')}",
    ]

    if verify:
        total     = len(all_findings)
        verified  = sum(1 for f in all_findings if f.status == STATUS_VERIFIED)
        nearby    = sum(1 for f in all_findings if f.status == STATUS_NEARBY)
        not_found = sum(1 for f in all_findings if f.status == STATUS_NOT_FOUND)
        no_src    = sum(1 for f in all_findings if f.status == STATUS_NO_SOURCE)
        out.append(
            f"אימות ציטוטים: {verified}/{total} ✓VERIFIED  "
            f"{nearby} ~NEARBY  {not_found} ✗NOT FOUND  {no_src} ?NO SOURCE"
        )

    out += [THIN, ""]

    for n in range(1, 55):
        out.append(THIN)
        out.append(f"סעיף {n}: {titles.get(n, '')}")
        out.append(THIN)

        sec = section_map.get(n)
        if sec and sec.findings:
            for f in sec.findings:
                out.extend(_format_finding(f, verify, enrich))
        else:
            out.append("לא נמצאה עדות מפורשת בטקסט")

        out.append("")

    return "\n".join(out)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3: Validate and enrich the final report (no LLM)."
    )
    parser.add_argument("--verify", action="store_true",
                        help="Check each citation exists in the source extracted text")
    parser.add_argument("--enrich", action="store_true",
                        help="Add surrounding conversation context below each citation")
    parser.add_argument("--input", type=Path, default=None,
                        help="Input report (default: <project-dir>/final_report.txt)")
    parser.add_argument("--output", type=Path,
                        help="Output file path (default: auto-named)")
    parser.add_argument("--context-lines", type=int, default=40, metavar="N",
                        help="Max lines of context per citation when --enrich is set (default: 40)")
    parser.add_argument("--input-dir", default=None, metavar="DIR",
                        help="Folder containing input PDFs and extracted/ subdir.")
    parser.add_argument("--project-dir", default=None, metavar="DIR",
                        help="Folder for all outputs (default: <script-dir>/results).")
    args = parser.parse_args()

    analysis_utils.configure_paths(args.input_dir, args.project_dir)
    if args.input is None:
        args.input = analysis_utils.RESULTS_DIR / "final_report.txt"

    if not args.verify and not args.enrich:
        print("No mode selected — use --verify and/or --enrich.")
        parser.print_help()
        sys.exit(1)

    if not args.input.exists():
        print(f"ERROR: Input not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if args.output is None:
        suffix = "_".join(filter(None, [
            "verified" if args.verify else "",
            "enriched" if args.enrich else "",
        ]))
        args.output = analysis_utils.RESULTS_DIR / f"phase3_{suffix}_report.txt"

    mode_label = " + ".join(filter(None, [
        "Verification" if args.verify else "",
        "Enrichment"   if args.enrich else "",
    ]))
    print(f"\n=== Phase 3: {mode_label} ===\n")
    print(f"Input:  {args.input}")
    print(f"Output: {args.output}")

    print("\nParsing report...")
    sections = parse_report(args.input)
    all_findings = [f for s in sections for f in s.findings]
    print(f"  {len(sections)} sections parsed, {len(all_findings)} findings")

    # Always parse source fields (needed for both verify and enrich)
    for f in all_findings:
        f.pdf_name, f.page_num = _parse_source(f.source_raw)

    if args.verify:
        print("\nVerifying citations...")
        for i, f in enumerate(all_findings):
            verify_finding(f)
            if (i + 1) % 25 == 0 or (i + 1) == len(all_findings):
                print(f"  {i + 1}/{len(all_findings)} checked")

        verified  = sum(1 for f in all_findings if f.status == STATUS_VERIFIED)
        nearby    = sum(1 for f in all_findings if f.status == STATUS_NEARBY)
        not_found = sum(1 for f in all_findings if f.status == STATUS_NOT_FOUND)
        no_src    = sum(1 for f in all_findings if f.status == STATUS_NO_SOURCE)
        print(f"\n  {verified} ✓VERIFIED  {nearby} ~NEARBY  "
              f"{not_found} ✗NOT FOUND  {no_src} ?NO SOURCE")

        flagged = [
            (s, f) for s in sections for f in s.findings
            if f.status in (STATUS_NOT_FOUND, STATUS_NO_SOURCE)
        ]
        if flagged:
            issues_path = analysis_utils.RESULTS_DIR / "verification_issues.txt"
            issue_lines = [f"ציטוטים שלא אומתו ({len(flagged)}):\n"]
            for sec, f in flagged:
                issue_lines.append(f"[סעיף {sec.num}] {f.raw}")
                issue_lines.append(f"  {f.status}  |  מקור: {f.source_raw}\n")
            issues_path.write_text("\n".join(issue_lines), encoding="utf-8")
            print(f"\n  Flagged citations written to: {issues_path}")

    if args.enrich:
        print(f"\nExtracting context (max {args.context_lines} lines per citation)...")
        for i, f in enumerate(all_findings):
            f.context = extract_context(f, max_lines=args.context_lines)
            if (i + 1) % 25 == 0 or (i + 1) == len(all_findings):
                print(f"  {i + 1}/{len(all_findings)} processed")
        with_ctx = sum(1 for f in all_findings if f.context)
        print(f"  Context found for {with_ctx}/{len(all_findings)} findings")

    print("\nBuilding output report...")
    report = build_output(sections, args.verify, args.enrich)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"\nDone. Report saved to: {args.output}  ({len(report):,} chars)")


if __name__ == "__main__":
    main()
