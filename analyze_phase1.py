#!/usr/bin/env python3
"""
Phase 1: Process each PDF in batches, produce one result file per PDF.

Output:
  results/chunks/pdf_{i}_chunk_{j}.txt  — raw per-chunk findings
  results/pdf_{i}.txt                   — aggregated findings for PDF i
  results/chunks/file_{stem}_chunk_{j}.txt  — chunks for --file inputs
  results/file_{stem}.txt                   — aggregated findings for a --file input
  results/report_YYYYMMDD_HHMMSS.txt   — clean shareable report for this run

Usage:
  python3 analyze_phase1.py                         # all 7 PDFs, 50-page chunks
  python3 analyze_phase1.py 0                       # PDF index 0 only
  python3 analyze_phase1.py 0 2 5                   # PDFs 0, 2, and 5
  python3 analyze_phase1.py -c 3 -p 9 0             # 3-page chunks, first 9 pages (fast validation)
  python3 analyze_phase1.py --chunk-size 5 --max-pages 15 0
  python3 analyze_phase1.py --file extracted/test.txt  # single text file
  python3 analyze_phase1.py --file a.txt --file b.txt  # multiple text files

Resume: already-completed chunk files and pdf files are skipped automatically.
Note: --max-pages bypasses the skip logic (use for validation, not production).

export ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import concurrent.futures
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    BASE_DIR, PDF_FILES, RESULTS_DIR, CHUNKS_DIR, EXTRACTED_DIR,
    SYSTEM_PROMPT, INVESTIGATION_ITEMS,
    extract_text, count_pages, call_claude_streaming, log,
    metrics, reset_metrics, _PAGE_MARKER_RE, _slice_pages,
)
from estimate import estimate_pdf, fmt_time, CHUNK_MAX_OUT_TOK, AGG_MAX_OUT_TOK, OUTPUT_TOK_PER_MIN
import anthropic

DEFAULT_BATCH_SIZE = 5  # pages per chunk — small chunks finish fast and don't truncate


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
    estimated_tokens = len(text) // 2
    log(f"  {len(text):,} chars  ~{estimated_tokens:,} tok  ({len(text)//1024} KB)")

    page_count = end_page - start_page
    max_tokens = min(32000, max(4000, page_count * 2500))

    user_message = f"""להלן תכתובות ווטסאפ (קובץ {pdf_index+1}/7, עמודים {start_page+1}–{end_page}):

---BEGIN WHATSAPP TRANSCRIPT---
{text}
---END WHATSAPP TRANSCRIPT---

{INVESTIGATION_ITEMS}

דווח רק על ממצאים בולטים. פורמט: סעיף N | ציטוט: "טקסט" | תאריך: DD.MM.YYYY HH:MM | מקור: קובץ עמוד NN
אם לא נמצאה עדות בחלק זה — ציין: "לא נמצאה עדות בחלק זה"."""

    result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=max_tokens,
                                   label=f"chunk-{chunk_index} p{start_page+1}-{end_page}")
    if not skip_cache:
        chunk_file.write_text(result, encoding="utf-8")
        log(f"  Saved: {chunk_file.name}")
    return result


def aggregate_chunks(
    client: anthropic.Anthropic,
    pdf_index: int | str,
    chunk_results: list[str],
    pdf_name: str,
    pages_label: str,
    skip_cache: bool = False,
    result_file: Path | None = None,
) -> str:
    pdf_result_file = result_file or RESULTS_DIR / f"pdf_{pdf_index}.txt"

    if not skip_cache and pdf_result_file.exists():
        log(f"  [skip] {pdf_result_file.name} — already aggregated")
        return pdf_result_file.read_text(encoding="utf-8")

    if len(chunk_results) == 1:
        # Single chunk — no need for a merge call
        result = chunk_results[0]
    else:
        log(f"\n  === Aggregating {len(chunk_results)} chunks for {pdf_name} ===")

        combined = ""
        for i, r in enumerate(chunk_results):
            combined += f"\n\n=== תוצאות chunk {i} ===\n{r}"

        agg_max_tokens = min(32000, max(8000, len(chunk_results) * 8000))

        user_message = f"""להלן תוצאות ניתוח של {len(chunk_results)} חלקים מאותו קובץ ווטסאפ ({pdf_name}, {pages_label}):

{combined}

אחד את הממצאים לדוח תמציתי לפי הסעיפים. הסר כפילויות. שמור על פורמט:
סעיף N | ציטוט: "טקסט" | תאריך: DD.MM.YYYY HH:MM | מקור: קובץ עמוד NN
אם לא נמצאה עדות — ציין: "לא נמצאה עדות מפורשת בטקסט"."""

        result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=agg_max_tokens,
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


def process_text_file(
    client: anthropic.Anthropic,
    file_path: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_pages: int | None = None,
    workers: int = 1,
) -> tuple[str, str, str]:
    """Process a plain-text file with page markers. Returns (file_name, pages_label, aggregated_result)."""
    skip_cache = max_pages is not None
    stem = file_path.stem
    full_text = file_path.read_text(encoding="utf-8")

    total_pages = len(_PAGE_MARKER_RE.findall(full_text))
    if total_pages == 0:
        # No markers — treat the whole file as one page
        total_pages = 1
    pages_to_process = min(max_pages, total_pages) if max_pages else total_pages
    num_chunks = (pages_to_process + batch_size - 1) // batch_size
    pages_label = f"עמודים 1–{pages_to_process} (מתוך {total_pages})" if max_pages else f"כל {total_pages} עמודים"

    log(f"\n{'='*50}")
    log(f"[FILE] {file_path.name}  ({len(full_text)//1024:,} KB)")
    log(f"  {pages_to_process}/{total_pages} pages → {num_chunks} chunks of {batch_size} pages")
    log(f"{'='*50}")

    def _run_chunk(chunk_idx: int) -> tuple[int, str]:
        start = chunk_idx * batch_size
        end = min(start + batch_size, pages_to_process)
        chunk_file = CHUNKS_DIR / f"file_{stem}_chunk_{chunk_idx}.txt"

        if not skip_cache and chunk_file.exists():
            log(f"  [skip] chunk {chunk_idx} (pages {start+1}–{end}) — already done")
            return chunk_idx, chunk_file.read_text(encoding="utf-8")

        log(f"\n  --- Chunk {chunk_idx}: pages {start+1}–{end} of {total_pages} ---")
        text = _slice_pages(full_text, start, end) if total_pages > 1 else full_text
        estimated_tokens = len(text) // 2
        log(f"  {len(text):,} chars  ~{estimated_tokens:,} tok  ({len(text)//1024} KB)")

        page_count = end - start
        max_tokens = min(32000, max(4000, page_count * 2500))

        user_message = f"""להלן תכתובות ווטסאפ (קובץ: {file_path.name}, עמודים {start+1}–{end}):

---BEGIN WHATSAPP TRANSCRIPT---
{text}
---END WHATSAPP TRANSCRIPT---

{INVESTIGATION_ITEMS}

דווח רק על ממצאים בולטים. פורמט: סעיף N | ציטוט: "טקסט" | תאריך: DD.MM.YYYY HH:MM | מקור: קובץ עמוד NN
אם לא נמצאה עדות בחלק זה — ציין: "לא נמצאה עדות בחלק זה"."""

        result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=max_tokens,
                                       label=f"file-{stem}-chunk-{chunk_idx} p{start+1}-{end}")
        if not skip_cache:
            chunk_file.write_text(result, encoding="utf-8")
            log(f"  Saved: {chunk_file.name}")
        return chunk_idx, result

    chunk_results_map: dict[int, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_chunk, i): i for i in range(num_chunks)}
        for future in concurrent.futures.as_completed(futures):
            idx, result = future.result()
            chunk_results_map[idx] = result
    chunk_results = [chunk_results_map[i] for i in range(num_chunks)]

    result_file = RESULTS_DIR / f"file_{stem}.txt"
    aggregated = aggregate_chunks(
        client, f"file_{stem}", chunk_results,
        pdf_name=file_path.name,
        pages_label=pages_label,
        skip_cache=skip_cache,
        result_file=result_file,
    )
    log(f"\n[FILE {file_path.name}] Done — {len(aggregated):,} chars")
    return file_path.name, pages_label, aggregated


def process_pdf(
    client: anthropic.Anthropic,
    pdf_index: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_pages: int | None = None,
    workers: int = 1,
) -> tuple[str, str, str]:
    """Process one PDF. Returns (pdf_name, pages_label, aggregated_result)."""
    pdf_path = PDF_FILES[pdf_index]
    skip_cache = max_pages is not None

    total_pages = count_pages(pdf_path)
    pages_to_process = min(max_pages, total_pages) if max_pages else total_pages
    num_chunks = (pages_to_process + batch_size - 1) // batch_size
    pages_label = f"עמודים 1–{pages_to_process} (מתוך {total_pages})" if max_pages else f"כל {total_pages} עמודים"

    txt_file = EXTRACTED_DIR / f"{pdf_path.stem}.txt"
    txt_kb = txt_file.stat().st_size // 1024 if txt_file.exists() else 0

    log(f"\n{'='*50}")
    log(f"[PDF {pdf_index+1}/7] {pdf_path.name}  ({txt_kb:,} KB txt)")
    log(f"  {pages_to_process}/{total_pages} pages → {num_chunks} chunks of {batch_size} pages")
    log(f"{'='*50}")

    def _run_chunk(chunk_idx: int) -> tuple[int, str]:
        start = chunk_idx * batch_size
        end = min(start + batch_size, pages_to_process)
        return chunk_idx, process_chunk(
            client, pdf_path, pdf_index, chunk_idx,
            start, end, total_pages,
            skip_cache=skip_cache,
        )

    chunk_results_map: dict[int, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_chunk, i): i for i in range(num_chunks)}
        for future in concurrent.futures.as_completed(futures):
            idx, result = future.result()
            chunk_results_map[idx] = result
    chunk_results = [chunk_results_map[i] for i in range(num_chunks)]

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
    parser.add_argument(
        "--file", action="append", dest="files", metavar="PATH", default=[],
        help="Path to a pre-extracted .txt file to analyze (can be repeated).",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=2, metavar="N",
        help="Parallel chunk workers per PDF (default: 2, max: 3).",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    CHUNKS_DIR.mkdir(exist_ok=True)

    # Resolve --file paths
    file_paths: list[Path] = []
    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            p = BASE_DIR / p
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)
        file_paths.append(p)

    # When --file is the only input, skip the default "all PDFs" behaviour
    if file_paths and not args.pdfs:
        indices = []
    else:
        indices = args.pdfs if args.pdfs else list(range(len(PDF_FILES)))

    invalid = [i for i in indices if i < 0 or i >= len(PDF_FILES)]
    if invalid:
        print(f"Invalid PDF indices: {invalid}. Valid range: 0–{len(PDF_FILES)-1}", file=sys.stderr)
        sys.exit(1)

    if not indices and not file_paths:
        print("Nothing to process. Provide PDF indices or --file paths.", file=sys.stderr)
        sys.exit(1)

    batch_size = args.chunk_size
    max_pages = args.max_pages
    workers = min(max(1, args.workers), 3)
    run_time = datetime.datetime.now()
    reset_metrics()
    client = anthropic.Anthropic(api_key=api_key, timeout=anthropic.Timeout(connect=30.0, read=1800.0, write=30.0, pool=30.0))

    mode_str = f"max {max_pages} pages" if max_pages else "full run"
    log(f"Phase 1 — PDFs: {indices}  files: {[p.name for p in file_paths]}  chunk: {batch_size} pages  workers: {workers}  mode: {mode_str}")

    # ── Pre-run estimate (PDFs only) ──────────────────────────────────────────
    if indices:
        estimates = [estimate_pdf(PDF_FILES[i], batch_size, max_pages) for i in indices]
        total_calls = sum(e["calls"] for e in estimates if "error" not in e)
        total_min   = sum(e["est_min"] for e in estimates if "error" not in e)
        total_cost  = sum(e["cost"] for e in estimates if "error" not in e)
        log(f"{'─'*50}")
        log(f"Pre-run estimate ({OUTPUT_TOK_PER_MIN:,} tok/min measured):")
        for i, e in zip(indices, estimates):
            if "error" in e:
                log(f"  PDF {i}: {e['error']}")
            else:
                log(f"  PDF {i} ({e['name']}): {e['chunks']} chunks, "
                    f"{fmt_time(e['est_min'])}, ${e['cost']:.2f}")
        log(f"  TOTAL: {total_calls} API calls  {fmt_time(total_min)}  ${total_cost:.2f}")
        log(f"{'─'*50}")
    # ─────────────────────────────────────────────────────────────────────────

    pdf_names, pages_labels, results = [], [], []
    for i in indices:
        name, label, content = process_pdf(client, i, batch_size=batch_size, max_pages=max_pages, workers=workers)
        pdf_names.append(name)
        pages_labels.append(label)
        results.append(content)

    for fp in file_paths:
        name, label, content = process_text_file(client, fp, batch_size=batch_size, max_pages=max_pages, workers=workers)
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
