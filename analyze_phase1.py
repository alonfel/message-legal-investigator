#!/usr/bin/env python3
"""
Phase 1: Process each PDF in batches, produce one result file per PDF.

Output:
  results/chunks/pdf_{i}_chunk_{j}.txt  — raw per-chunk findings
  results/pdf_{i}.txt                   — aggregated findings for PDF i
  results/report_YYYYMMDD_HHMMSS.txt   — clean shareable report for this run

Usage:
  python3 analyze_phase1.py                         # all 7 PDFs, 50-page chunks
  python3 analyze_phase1.py 0                       # PDF index 0 only
  python3 analyze_phase1.py 0 2 5                   # PDFs 0, 2, and 5
  python3 analyze_phase1.py -c 3 -p 9 0             # 3-page chunks, first 9 pages (fast validation)
  python3 analyze_phase1.py --chunk-size 5 --max-pages 15 0

Resume: already-completed chunk files and pdf files are skipped automatically.
Note: --max-pages bypasses the skip logic (use for validation, not production).

export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    BASE_DIR, PDF_FILES, RESULTS_DIR, CHUNKS_DIR, EXTRACTED_DIR,
    SYSTEM_PROMPT, INVESTIGATION_ITEMS,
    extract_text, count_pages, call_claude_streaming, log,
    metrics, reset_metrics,
)
import anthropic

DEFAULT_BATCH_SIZE = 50  # pages per chunk


def process_chunk(
    client: anthropic.Anthropic,
    pdf_path: Path,
    pdf_index: int,
    chunk_index: int,
    start_page: int,
    end_page: int,
    total_pages: int,
    skip_cache: bool = False,
) -> str:
    chunk_file = CHUNKS_DIR / f"pdf_{pdf_index}_chunk_{chunk_index}.txt"

    if not skip_cache and chunk_file.exists():
        log(f"  [skip] chunk {chunk_index} (pages {start_page+1}–{end_page}) — already done")
        return chunk_file.read_text(encoding="utf-8")

    log(f"\n  --- Chunk {chunk_index}: pages {start_page+1}–{end_page} of {total_pages} ---")
    text = extract_text(pdf_path, start_page=start_page, end_page=end_page)
    # Hebrew text is ~2 chars/token (denser than English ~4 chars/token)
    estimated_tokens = len(text) // 2
    log(f"  {len(text):,} chars  ~{estimated_tokens:,} tok  ({len(text)//1024} KB)")

    user_message = f"""להלן תכתובות ווטסאפ (קובץ {pdf_index+1}/7, עמודים {start_page+1}–{end_page}):

הטקסט מכיל סמנים בפורמט: === [שם-קובץ.pdf | עמוד NN] ===
יש לציין את שם הקובץ ומספר העמוד בשדה "מקור" של כל ממצא.

---BEGIN WHATSAPP TRANSCRIPT---
{text}
---END WHATSAPP TRANSCRIPT---

{INVESTIGATION_ITEMS}

עבוד סעיף אחר סעיף. עבור כל סעיף ציין את מספרו וניסוחו, ולאחר מכן את הממצאים בפורמט הבא:

• ציטוט: "...הטקסט המדויק..."
  תאריך ושעה: DD.MM.YYYY, HH:MM
  מקור: yoni-meitalN.pdf, עמוד NN
  הקשר: [משפט קצר]

אם לא נמצאה עדות בחלק זה — ציין: "לא נמצאה עדות בחלק זה"."""

    result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=16000,
                                   label=f"chunk-{chunk_index} p{start_page+1}-{end_page}")
    if not skip_cache:
        chunk_file.write_text(result, encoding="utf-8")
        log(f"  Saved: {chunk_file.name}")
    return result


def aggregate_chunks(
    client: anthropic.Anthropic,
    pdf_index: int,
    chunk_results: list[str],
    pdf_name: str,
    pages_label: str,
    skip_cache: bool = False,
) -> str:
    pdf_result_file = RESULTS_DIR / f"pdf_{pdf_index}.txt"

    if not skip_cache and pdf_result_file.exists():
        log(f"  [skip] pdf_{pdf_index}.txt — already aggregated")
        return pdf_result_file.read_text(encoding="utf-8")

    if len(chunk_results) == 1:
        # Single chunk — no need for a merge call
        result = chunk_results[0]
    else:
        log(f"\n  === Aggregating {len(chunk_results)} chunks for {pdf_name} ===")

        combined = ""
        for i, r in enumerate(chunk_results):
            combined += f"\n\n=== תוצאות chunk {i} ===\n{r}"

        user_message = f"""להלן תוצאות ניתוח של {len(chunk_results)} חלקים מאותו קובץ ווטסאפ ({pdf_name}, {pages_label}):

{combined}

משימתך: אחד את הממצאים לדוח מסודר לפי 54 הסעיפים.
כללים:
1. עבור כל סעיף — רשום את כל הממצאים מכל החלקים (ללא כפילויות).
2. אם אותו ציטוט מופיע בכמה חלקים — כלול אותו פעם אחת בלבד.
3. שמור על פורמט הממצא המדויק לכל ציטוט (כולל שדה "מקור" עם שם קובץ ועמוד):

• ציטוט: "...הטקסט המדויק..."
  תאריך ושעה: DD.MM.YYYY, HH:MM
  מקור: yoni-meitalN.pdf, עמוד NN
  הקשר: [משפט קצר]

4. אם לא נמצאה עדות בשום חלק — ציין: "לא נמצאה עדות מפורשת בטקסט"."""

        result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=32000,
                                       label=f"aggregate pdf-{pdf_index}")

    if not skip_cache:
        pdf_result_file.write_text(result, encoding="utf-8")
        log(f"  Saved: {pdf_result_file.name}")
    return result


def build_report_header(
    run_time: datetime.datetime,
    pdf_names: list[str],
    pages_labels: list[str],
    chunk_size: int,
    max_pages: int | None,
) -> str:
    sep = "=" * 80
    thin = "─" * 80
    pages_info = "\n".join(
        f"  • {name}: {label}" for name, label in zip(pdf_names, pages_labels)
    )
    mode = f"עד {max_pages} עמודים ראשונים בלבד (מצב בדיקה)" if max_pages else "כל העמודים (ריצה מלאה)"
    return f"""{sep}
דוח ניתוח תכתובות ווטסאפ — יונתן ומיטל
{sep}
תאריך ריצה : {run_time.strftime('%d.%m.%Y  %H:%M:%S')}
קבצים      :
{pages_info}
גודל chunk : {chunk_size} עמודים
מצב        : {mode}
{thin}
הדוח מכיל ממצאים לפי 54 הסעיפים שנבדקו.
כל ממצא כולל: ציטוט מדויק | תאריך ושעה | מקור (קובץ + עמוד) | הקשר.
{sep}
"""


def process_pdf(
    client: anthropic.Anthropic,
    pdf_index: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_pages: int | None = None,
) -> tuple[str, str, str]:
    """Process one PDF. Returns (pdf_name, pages_label, aggregated_result)."""
    pdf_path = PDF_FILES[pdf_index]
    skip_cache = max_pages is not None  # validation mode: never use cache

    # Count pages from pre-extracted txt (instant — no PDF open needed)
    total_pages = count_pages(pdf_path)
    pages_to_process = min(max_pages, total_pages) if max_pages else total_pages
    num_chunks = (pages_to_process + batch_size - 1) // batch_size
    pages_label = f"עמודים 1–{pages_to_process} (מתוך {total_pages})" if max_pages else f"כל {total_pages} עמודים"

    # Show txt file size for accurate throughput context
    txt_file = EXTRACTED_DIR / f"{pdf_path.stem}.txt"
    txt_kb = txt_file.stat().st_size // 1024 if txt_file.exists() else 0

    log(f"\n{'='*50}")
    log(f"[PDF {pdf_index+1}/7] {pdf_path.name}  ({txt_kb:,} KB txt)")
    log(f"  {pages_to_process}/{total_pages} pages → {num_chunks} chunks of {batch_size} pages")
    log(f"{'='*50}")

    chunk_results = []
    for chunk_idx in range(num_chunks):
        start = chunk_idx * batch_size
        end = min(start + batch_size, pages_to_process)
        result = process_chunk(
            client, pdf_path, pdf_index, chunk_idx,
            start, end, total_pages,
            skip_cache=skip_cache,
        )
        chunk_results.append(result)

    aggregated = aggregate_chunks(
        client, pdf_index, chunk_results,
        pdf_name=pdf_path.name,
        pages_label=pages_label,
        skip_cache=skip_cache,
    )
    log(f"\n[PDF {pdf_index}] Done — {len(aggregated):,} chars")
    return pdf_path.name, pages_label, aggregated


def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: analyze WhatsApp PDFs in page batches."
    )
    parser.add_argument(
        "pdfs", nargs="*", type=int,
        help="PDF indices to process (0–6). Omit for all 7.",
    )
    parser.add_argument(
        "-c", "--chunk-size", type=int, default=DEFAULT_BATCH_SIZE, metavar="N",
        help=f"Pages per chunk (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "-p", "--max-pages", type=int, default=None, metavar="N",
        help="Cap total pages per PDF (validation mode — disables chunk cache).",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    CHUNKS_DIR.mkdir(exist_ok=True)

    indices = args.pdfs if args.pdfs else list(range(len(PDF_FILES)))
    invalid = [i for i in indices if i < 0 or i >= len(PDF_FILES)]
    if invalid:
        print(f"Invalid PDF indices: {invalid}. Valid range: 0–{len(PDF_FILES)-1}", file=sys.stderr)
        sys.exit(1)

    batch_size = args.chunk_size
    max_pages = args.max_pages
    run_time = datetime.datetime.now()
    reset_metrics()
    client = anthropic.Anthropic(api_key=api_key)

    mode_str = f"max {max_pages} pages" if max_pages else "full run"
    log(f"Phase 1 — PDFs: {indices}  chunk: {batch_size} pages  mode: {mode_str}")

    pdf_names, pages_labels, results = [], [], []
    for i in indices:
        name, label, content = process_pdf(client, i, batch_size=batch_size, max_pages=max_pages)
        pdf_names.append(name)
        pages_labels.append(label)
        results.append(content)

    # Build and save the shareable report
    header = build_report_header(run_time, pdf_names, pages_labels, batch_size, max_pages)
    body = "\n\n".join(
        f"{'─'*80}\n[ {name} — {label} ]\n{'─'*80}\n\n{content}"
        for name, label, content in zip(pdf_names, pages_labels, results)
    )
    metrics_block = metrics.report_block()
    report = header + "\n" + metrics_block + "\n\n" + body

    ts = run_time.strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"report_{ts}.txt"
    report_path.write_text(report, encoding="utf-8")

    log(f"\n{'='*50}")
    for line in metrics.summary_lines():
        log(line)
    log(f"Report saved → {report_path}")
    log(f"Size: {len(report):,} chars")
    if not max_pages:
        log("Next: python3 analyze_phase2.py to merge all PDFs into final_report.txt")


if __name__ == "__main__":
    main()
