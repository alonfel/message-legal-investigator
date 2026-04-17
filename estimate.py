#!/usr/bin/env python3
"""
Pre-run cost and runtime estimator — zero API calls, runs in <1 second.

Run this BEFORE analyze_phase1.py to know what you're about to spend.

Usage:
  python3 estimate.py                    # all 7 PDFs, 50-page chunks
  python3 estimate.py 0                  # PDF 0 only
  python3 estimate.py -c 3 -p 9 0        # 3-page chunks, first 9 pages, PDF 0
  python3 estimate.py -c 5 0 1 2         # PDFs 0,1,2 with 5-page chunks
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import PDF_FILES, EXTRACTED_DIR, _slice_pages, _PAGE_MARKER_RE, count_pages

# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 5
PROMPT_OVERHEAD_TOK = 5_000     # fixed system prompt + investigation items per call
CHUNK_MAX_OUT_TOK   = 32_000    # max_tokens for chunk calls (3-page chunk already hit 12K; 5 pages needs ~20K)
AGG_MAX_OUT_TOK     = 32_000    # max_tokens for aggregate calls
CHARS_PER_TOKEN     = 2         # Hebrew text ~2 chars/token
OUTPUT_TOK_PER_MIN  = 2_750     # measured: 3,781 tok in 83s = 2,733 tok/min (Sonnet 4.6)
PRICE_IN            = 0.80      # $ per million input tokens (Haiku 4.5)
PRICE_OUT           = 4.00      # $ per million output tokens (Haiku 4.5)


# ─── Estimation logic ─────────────────────────────────────────────────────────

def estimate_pdf(pdf_path: Path, chunk_size: int, max_pages: int | None) -> dict:
    txt = EXTRACTED_DIR / f"{pdf_path.stem}.txt"
    if not txt.exists():
        return {"error": f"No extracted txt — run: python3 extract_pdfs.py"}

    full_text = txt.read_text(encoding="utf-8")
    total_pages = len(_PAGE_MARKER_RE.findall(full_text))
    pages = min(max_pages, total_pages) if max_pages else total_pages
    num_chunks = (pages + chunk_size - 1) // chunk_size

    # Input tokens: actual text slice sizes + fixed prompt overhead per call
    chunk_in_tok = 0
    for c in range(num_chunks):
        start = c * chunk_size
        end = min(start + chunk_size, pages)
        chunk_text = _slice_pages(full_text, start, end)
        chunk_in_tok += len(chunk_text) // CHARS_PER_TOKEN + PROMPT_OVERHEAD_TOK

    # Output tokens: bounded by max_tokens, assume full utilisation for worst-case
    chunk_out_tok = num_chunks * CHUNK_MAX_OUT_TOK
    agg_calls = 1 if num_chunks > 1 else 0
    agg_in_tok = chunk_out_tok + PROMPT_OVERHEAD_TOK if agg_calls else 0
    agg_out_tok = AGG_MAX_OUT_TOK if agg_calls else 0

    total_in  = chunk_in_tok + agg_in_tok
    total_out = chunk_out_tok + agg_out_tok
    total_calls = num_chunks + agg_calls

    cost = total_in / 1_000_000 * PRICE_IN + total_out / 1_000_000 * PRICE_OUT
    est_min = total_out / OUTPUT_TOK_PER_MIN  # output speed is the bottleneck

    return {
        "name": pdf_path.stem,
        "total_pages": total_pages,
        "pages": pages,
        "chunks": num_chunks,
        "calls": total_calls,
        "in_tok": total_in,
        "out_tok": total_out,
        "cost": cost,
        "est_min": est_min,
    }


# ─── Display ──────────────────────────────────────────────────────────────────

def fmt_tok(n: int) -> str:
    return f"{n/1000:.0f}K" if n >= 1000 else str(n)

def fmt_time(minutes: float) -> str:
    if minutes < 1:
        return f"~{int(minutes*60)}s"
    h, m = divmod(int(minutes), 60)
    return f"~{h}h {m}m" if h else f"~{m}m"

def print_table(rows: list[dict], chunk_size: int, max_pages: int | None) -> None:
    mode = f"max {max_pages} pages/pdf" if max_pages else "full run"
    print(f"\n  Run Estimate — chunk: {chunk_size} pages  mode: {mode}")
    print(f"  Pricing: Haiku 4.5  in=$0.80/M  out=$4/M  output speed ~{OUTPUT_TOK_PER_MIN:,} tok/min\n")

    W = {"name": 16, "pages": 8, "chunks": 7, "calls": 6, "in": 9, "out": 9, "time": 9, "cost": 9}
    sep = "  " + "─" * (sum(W.values()) + len(W) * 3 + 1)
    hdr = (f"  {'PDF':<{W['name']}} │ {'Pages':>{W['pages']}} │ {'Chunks':>{W['chunks']}} │"
           f" {'Calls':>{W['calls']}} │ {'In tok':>{W['in']}} │ {'Out tok':>{W['out']}} │"
           f" {'Est time':>{W['time']}} │ {'Cost':>{W['cost']}}")
    print(sep); print(hdr); print(sep)

    tot_pages = tot_chunks = tot_calls = tot_in = tot_out = tot_cost = tot_min = 0
    for r in rows:
        if "error" in r:
            print(f"  {r['name']:<{W['name']}}  ERROR: {r['error']}")
            continue
        print(f"  {r['name']:<{W['name']}} │ {r['pages']:>{W['pages']}} │"
              f" {r['chunks']:>{W['chunks']}} │ {r['calls']:>{W['calls']}} │"
              f" {fmt_tok(r['in_tok']):>{W['in']}} │ {fmt_tok(r['out_tok']):>{W['out']}} │"
              f" {fmt_time(r['est_min']):>{W['time']}} │ ${r['cost']:>{W['cost']-1}.3f}")
        tot_pages  += r["pages"];  tot_chunks += r["chunks"]
        tot_calls  += r["calls"];  tot_in     += r["in_tok"]
        tot_out    += r["out_tok"]; tot_cost   += r["cost"]
        tot_min    += r["est_min"]

    print(sep)
    print(f"  {'TOTAL':<{W['name']}} │ {tot_pages:>{W['pages']}} │"
          f" {tot_chunks:>{W['chunks']}} │ {tot_calls:>{W['calls']}} │"
          f" {fmt_tok(tot_in):>{W['in']}} │ {fmt_tok(tot_out):>{W['out']}} │"
          f" {fmt_time(tot_min):>{W['time']}} │ ${tot_cost:>{W['cost']-1}.3f}")
    print(sep)

    print(f"\n  Bottleneck: Claude output speed (~{OUTPUT_TOK_PER_MIN:,} tok/min).")
    print(f"  Each chunk call:     ~{fmt_time(CHUNK_MAX_OUT_TOK/OUTPUT_TOK_PER_MIN)} "
          f"({CHUNK_MAX_OUT_TOK:,} tok output max)")
    print(f"  Each aggregate call: ~{fmt_time(AGG_MAX_OUT_TOK/OUTPUT_TOK_PER_MIN)} "
          f"({AGG_MAX_OUT_TOK:,} tok output max)")
    print(f"  Estimates assume worst-case (full max_tokens used per call).\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Estimate runtime and cost before running.")
    parser.add_argument("pdfs", nargs="*", type=int,
                        help="PDF indices (0–6). Omit for all 7.")
    parser.add_argument("-c", "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, metavar="N",
                        help=f"Pages per chunk (default: {DEFAULT_CHUNK_SIZE}).")
    parser.add_argument("-p", "--max-pages", type=int, default=None, metavar="N",
                        help="Cap pages per PDF (mirrors analyze_phase1.py -p flag).")
    args = parser.parse_args()

    indices = args.pdfs if args.pdfs else list(range(len(PDF_FILES)))
    invalid = [i for i in indices if i < 0 or i >= len(PDF_FILES)]
    if invalid:
        print(f"Invalid PDF indices: {invalid}. Valid: 0–{len(PDF_FILES)-1}", file=sys.stderr)
        sys.exit(1)

    rows = [estimate_pdf(PDF_FILES[i], args.chunk_size, args.max_pages) for i in indices]
    print_table(rows, args.chunk_size, args.max_pages)


if __name__ == "__main__":
    main()
