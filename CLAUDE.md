# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Analyzes 7 WhatsApp conversation PDFs (`yoni-meital0.pdf` – `yoni-meital6.pdf`) against 54 Hebrew legal investigation items using Claude. Output is a structured Hebrew report with verbatim citations, timestamps, and source page references.

## Prerequisites

```bash
export ANTHROPIC_API_KEY=sk-ant-...
pip install anthropic pypdf pymupdf
```

## Recommended workflow

Run these in order:

```bash
# 0. (Optional) Estimate cost and runtime before spending anything
python3 estimate.py                    # all 7 PDFs at default settings
python3 estimate.py -c 3 -p 9 0        # match the fast-validation flags you plan to use

# 1. Smoke test — confirms API key, prompt format, and output structure (~15s)
python3 validate_quick.py              # ~5s, uses sample_chat.txt, no PDF extraction
python3 validate.py                    # 3 pages from real PDF (~15s)
python3 validate.py -p 10              # deeper spot-check

# 2. Extract PDFs to plain text (one-time, speeds up all subsequent runs)
python3 extract_pdfs.py

# 3. Phase 1 — analyze each PDF in 50-page chunks, saves per-PDF results
python3 analyze_phase1.py                         # all 7 PDFs
python3 analyze_phase1.py 0                       # single PDF (index 0)
python3 analyze_phase1.py -c 3 -p 9 0             # fast validation: 3-page chunks, first 9 pages

# 4. Phase 2 — merge all per-PDF results into final_report.txt
python3 analyze_phase2.py
```

Monitor progress live: `tail -f run.log`

## Architecture

**Two-pass pipeline** (`analyze_phase1.py` → `analyze_phase2.py`):

1. **Phase 1**: Each PDF is split into N-page chunks (default 50). Each chunk is sent to `claude-sonnet-4-6` with all 54 investigation items. When a PDF has multiple chunks, a second aggregation call merges them. All intermediate results are cached to `results/chunks/pdf_{i}_chunk_{j}.txt` and `results/pdf_{i}.txt` — rerunning skips completed work automatically. `--max-pages` disables the cache (validation mode).

2. **Phase 2**: Reads the 7 `results/pdf_{i}.txt` files and sends them in one merged call to produce `results/final_report.txt`, deduplicating citations across PDFs.

**Shared utilities** (`analysis_utils.py`):
- `extract_text()` — serves text from `extracted/*.txt` pre-extracted files (fast), falls back to live PyMuPDF extraction. Each page is wrapped with `=== [yoni-meitalN.pdf | עמוד NN] ===` for source tracing.
- `call_claude()` / `call_claude_streaming()` — both route to the same non-streaming call; the streaming alias exists for backwards compatibility.
- `RunMetrics` — global `metrics` singleton accumulates token counts and cost across all API calls in a run. Reset with `reset_metrics()` at script start.
- `log()` — writes timestamped lines to both stdout and `run.log`.

**`estimate.py`** — zero-API-call cost/runtime estimator. Reads the pre-extracted txt files to calculate per-chunk input sizes and projects worst-case output tokens. Run before any production run.

**`validate_quick.py`** — fastest possible smoke test (~5s). Uses a static `sample_chat.txt` snippet and only 5 of the 54 items. Use this when iterating on prompt format changes, before touching real PDFs.

**`analyze.py`** — legacy single-pass script (pypdf, no page markers, no caching). Superseded by the phase1/phase2 pipeline; kept for reference only.

**Hebrew text note:** Hebrew is ~2 chars/token (vs. English ~4), so token estimates use `len(text) // 2`.

## Output files

| File | Description |
|---|---|
| `extracted/yoni-meitalN.txt` | Pre-extracted PDF text with page markers |
| `results/chunks/pdf_{i}_chunk_{j}.txt` | Per-chunk raw findings |
| `results/pdf_{i}.txt` | Aggregated findings for PDF i |
| `results/report_YYYYMMDD_HHMMSS.txt` | Timestamped Phase 1 report |
| `results/validation_YYYYMMDD_HHMMSS.txt` | Output from `validate.py` runs |
| `results/quick_YYYYMMDD_HHMMSS.txt` | Output from `validate_quick.py` runs |
| `results/final_report.txt` | Final merged report (Phase 2 output) |
| `run.log` | Timestamped execution log |

## Model and cost

All analysis uses `claude-sonnet-4-6`. Pricing constants in `analysis_utils.py`: `$3.00/M` input, `$15.00/M` output. Cost is tracked per-call and displayed in the run summary. Claude output speed (~5,000 tok/min) is the bottleneck — each chunk call can take up to ~3m, each aggregate call up to ~6m.
