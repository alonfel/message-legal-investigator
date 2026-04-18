# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Analyzes 7 WhatsApp conversation PDFs (`yoni-meital0.pdf` – `yoni-meital6.pdf`) against 54 Hebrew legal investigation items using Claude (Haiku 4.5). Operates as a 4-phase pipeline:
- **Phase 1** (`analyze_phase1.py`): splits each PDF into chunks, sends each chunk to Claude, caches per-PDF findings to `results/`
- **Phase 2** (`analyze_phase2.py`): pure text merge — deduplicates all per-PDF findings into `results/final_report.txt` (no API call)
- **Phase 3** (`validate_report.py`): verifies citations exist in source text and/or enriches each finding with surrounding conversation context (no API call)
- **Phase 4** (`build_narrative.py`): takes selected sections from `final_report.txt` and uses Claude to synthesize a narrative document — chronological timeline + legal argument brief

Output is a structured Hebrew report with verbatim citations, timestamps, and source page references.

## Prerequisites

```bash
export ANTHROPIC_API_KEY=sk-ant-...
pip install anthropic pymupdf
```

## Recommended workflow

Run these in order:

```bash
# 0. (Optional) Estimate cost and runtime before spending anything
python3 estimate.py                    # all 7 PDFs at default settings
python3 estimate.py -c 3 -p 9 0        # match the fast-validation flags you plan to use

# 1. Smoke tests — confirm API key, prompt format, and output structure
python3 validate_quick.py              # ~5s, uses sample_chat.txt, no PDF extraction
python3 test_real.py                   # ~15s, 1 page from real PDF, 5 items
python3 validate.py                    # ~15s, 3 pages from real PDF, all 54 items
python3 validate.py -p 10              # deeper spot-check

# 2. Extract PDFs to plain text (one-time, speeds up all subsequent runs)
python3 extract_pdfs.py

# 3. Phase 1 — analyze each PDF in 5-page chunks, saves per-PDF results
python3 analyze_phase1.py                            # all 7 PDFs
python3 analyze_phase1.py 0                          # single PDF (index 0)
python3 analyze_phase1.py -c 3 -p 9 0               # fast validation: 3-page chunks, first 9 pages
python3 analyze_phase1.py -w 8 0 1 2                 # 8 parallel workers for PDFs 0–2
python3 analyze_phase1.py --file extracted/test.txt  # single pre-extracted text file

# 4. Phase 2 — merge all per-PDF results into final_report.txt (no API call)
python3 analyze_phase2.py
python3 analyze_phase2.py results/pdf_0.txt results/pdf_1.txt  # explicit files
python3 analyze_phase2.py --output results/custom_report.txt

# 5. Phase 3 — validate citations + enrich with context (no API call, runs in seconds)
python3 validate_report.py --verify                          # check citations exist in source
python3 validate_report.py --enrich                          # add surrounding conversation context
python3 validate_report.py --verify --enrich                 # both (recommended)
python3 validate_report.py --verify --enrich --context-lines 10  # compact context blocks

# 6. Phase 4 — build a narrative document around a specific legal angle (~$0.04–0.20)
# Context is fetched live from extracted/*.txt (not final_report.txt) — requires extract_pdfs.py to have run
python3 build_narrative.py --sections 3 --narrative "מיטל מודה בתרומת יונתן"
python3 build_narrative.py --sections 3,7,12 --narrative "דפוס שליטה כלכלית"
python3 build_narrative.py --sections 3,7 --narrative "..." --context-lines 10  # cheaper
python3 build_narrative.py --sections 3,7 --narrative "..." --verified-only     # skip unverified
```

Monitor progress live: `tail -f run.log`

## Orchestrator (alternative to manual steps 3–4)

`run_all.py` runs Phase 1 + Phase 2 end-to-end with chunk-level progress tracking and safe interruption/resumption:

```bash
python3 run_all.py              # all 7 PDFs, then Phase 2
python3 run_all.py -c 3         # 3-page chunks
python3 run_all.py -w 8         # 8 parallel workers
python3 run_all.py --status     # print progress summary and exit (no processing)
```

Progress is saved to `results/progress.json` after each chunk — interrupting and re-running picks up where it left off.

## Architecture

**Two-pass pipeline** (`analyze_phase1.py` → `analyze_phase2.py`):

1. **Phase 1**: Each PDF is split into N-page chunks (default 5). Each chunk is sent to `claude-haiku-4-5` with all 54 investigation items. When a PDF has multiple chunks, a second aggregation call merges them. All intermediate results are cached to `results/chunks/pdf_{i}_chunk_{j}.txt` and `results/pdf_{i}.txt` — rerunning skips completed work automatically. `--max-pages` disables the cache (validation mode). Plain-text files can also be passed via `--file`. Chunk workers run in parallel threads (default 2, max 10 via `-w`).

2. **Phase 2**: Pure text parser — reads all `results/pdf_{i}.txt` and `results/file_{stem}.txt` files, deduplicates findings by (citation, date, source), and formats them into `results/final_report.txt`. No API call is made.

3. **Phase 3** (`validate_report.py`): Post-processing on `final_report.txt`. No API call. Two independent modes:
   - `--verify`: Searches each citation's quoted text in the corresponding `extracted/*.txt` file at the stated page (±1 tolerance). Labels each finding `✓ VERIFIED`, `~ NEARBY`, `✗ NOT FOUND`, or `? NO SOURCE`. Flagged citations are written to `results/verification_issues.txt`.
   - `--enrich`: Adds a `הקשר:` block below each citation with the surrounding page text (default 40 lines, configurable with `--context-lines N`).

4. **Phase 4** (`build_narrative.py`): Narrative synthesis on `final_report.txt`. Uses Claude (1 API call). Takes `--sections` (comma-separated section numbers) and `--narrative` (free-text angle in Hebrew). For each finding, fetches the full surrounding page text **live from `extracted/yoni-meitalN.txt`** (not from `final_report.txt` — the report only stores the citation, date, and page reference). This gives Claude the real WhatsApp conversation with speaker names and timestamps around each quote. Findings are sorted chronologically, then Claude produces a two-part Hebrew document:
   - `חלק א: ציר זמן` — chronological narrative prose connecting events
   - `חלק ב: ניתוח משפטי` — legal argument: claim → numbered evidence chain → conclusion

   **Requires** `extracted/*.txt` files to exist (run `python3 extract_pdfs.py` first). Findings with no page number in their source field get no context — Claude receives only the bare citation for those.
   Cost measured at ~$0.036 per section (19 findings, 10 context lines, Haiku 4.5). Use `--context-lines 10` for cheaper runs; default is 30.

**Shared utilities** (`analysis_utils.py`):
- `extract_text()` — serves text from `extracted/*.txt` pre-extracted files (fast), falls back to live PyMuPDF extraction. Each page is wrapped with `=== [yoni-meitalN.pdf | עמוד NN] ===` for source tracing.
- `count_pages()` — counts total pages from pre-extracted file or live PDF.
- `call_claude()` — non-streaming API call, suitable for short outputs.
- `call_claude_streaming()` — streaming API call used for chunk/aggregate calls where output may be large (avoids SDK timeout).
- `fetch_credit_balance()` — fetches remaining Anthropic credit balance via API.
- `RunMetrics` — global `metrics` singleton accumulates token counts and cost across all API calls in a run. Reset with `reset_metrics()` at script start. Set `metrics._api_key` to display remaining credit balance in the run summary.
- `log()` — writes timestamped lines to both stdout and `run.log`.

**`estimate.py`** — zero-API-call cost/runtime estimator. Reads the pre-extracted txt files to calculate per-chunk input sizes and projects realistic cost and runtime using measured averages from real runs (avg chunk output: ~1,300 tokens; avg aggregation output: ~8,300 tokens). Run before any production run.

**`validate_quick.py`** — fastest possible smoke test (~5s). Uses a static `sample_chat.txt` snippet and only 5 of the 54 items. Requires `sample_chat.txt` to exist in the project root. Use when iterating on prompt format changes.

**`test_real.py`** — smoke test on real PDF (~15s). Extracts 1 page from `yoni-meital0.pdf`, tests 5 items. Bridges the gap between the synthetic `sample_chat.txt` and the full `validate.py` run.

**Hebrew text note:** Hebrew is ~2 chars/token (vs. English ~4), so token estimates use `len(text) // 2`.

## Output files

| File | Description |
|---|---|
| `extracted/yoni-meitalN.txt` | Pre-extracted PDF text with page markers |
| `results/chunks/pdf_{i}_chunk_{j}.txt` | Per-chunk raw findings (PDF inputs) |
| `results/chunks/file_{stem}_chunk_{j}.txt` | Per-chunk raw findings (--file inputs) |
| `results/pdf_{i}.txt` | Aggregated findings for PDF i |
| `results/file_{stem}.txt` | Aggregated findings for a --file input |
| `results/report_YYYYMMDD_HHMMSS.txt` | Timestamped Phase 1 report |
| `results/validation_YYYYMMDD_HHMMSS.txt` | Output from `validate.py` runs |
| `results/quick_YYYYMMDD_HHMMSS.txt` | Output from `validate_quick.py` runs |
| `results/test_real_YYYYMMDD_HHMMSS.txt` | Output from `test_real.py` runs |
| `results/final_report.txt` | Final merged report (Phase 2 output) |
| `results/phase3_verified_report.txt` | Phase 3: final_report + ✓/✗ verification tags |
| `results/phase3_enriched_report.txt` | Phase 3: final_report + conversation context blocks |
| `results/phase3_verified_enriched_report.txt` | Phase 3: both verification tags and context |
| `results/verification_issues.txt` | Citations that failed source verification |
| `results/progress.json` | run_all.py chunk-level progress state |
| `results/narrative_YYYYMMDD_HHMMSS.txt` | Phase 4 narrative document (timeline + legal brief) |
| `run.log` | Timestamped execution log |

## Model and cost

All analysis uses `claude-haiku-4-5-20251001`. Pricing constants in `analysis_utils.py`: `$0.80/M` input, `$4.00/M` output. Cost is tracked per-call and displayed in the run summary. Claude output speed (~3,200 tok/min measured, Haiku 4.5 calibration) is the bottleneck — each chunk call can take up to ~12m, each aggregate call up to ~12m.
