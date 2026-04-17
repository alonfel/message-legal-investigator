#!/usr/bin/env python3
"""
End-to-end pipeline orchestrator with chunk-level progress tracking.

Runs Phase 1 (all 7 PDFs, chunk by chunk) then Phase 2 (merge).
Saves progress to results/progress.json after each chunk — safe to interrupt
and resume at any time.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 run_all.py              # full pipeline, 50-page chunks
  python3 run_all.py -c 30        # custom chunk size
  python3 run_all.py --status     # print progress and exit (no API call)
"""

import argparse
import datetime
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import anthropic
from analysis_utils import (
    PDF_FILES, RESULTS_DIR, CHUNKS_DIR, SYSTEM_PROMPT,
    call_claude_streaming, log, metrics, reset_metrics, count_pages,
)
from analyze_phase1 import process_chunk, aggregate_chunks, DEFAULT_BATCH_SIZE

PROGRESS_FILE = RESULTS_DIR / "progress.json"


# ─── Progress Tracker ────────────────────────────────────────────────────────

class ProgressTracker:
    def __init__(self, chunk_size: int) -> None:
        self.chunk_size = chunk_size
        self._data: dict = {}

    def load(self) -> None:
        if PROGRESS_FILE.exists():
            self._data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
            self._data["chunk_size"] = self.chunk_size
        else:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            self._data = {
                "schema_version": 1,
                "started_at": now,
                "last_updated": now,
                "chunk_size": self.chunk_size,
                "pdfs": {
                    str(i): {
                        "status": "pending",
                        "total_chunks": None,
                        "chunks_done": [],
                        "aggregated": False,
                        "completed_at": None,
                    }
                    for i in range(len(PDF_FILES))
                },
                "phase2": {"status": "pending", "completed_at": None},
            }
        self.reconcile()

    def reconcile(self) -> None:
        """Sync progress.json with actual files on disk."""
        changed = False
        for i in range(len(PDF_FILES)):
            key = str(i)
            entry = self._data["pdfs"].setdefault(key, {
                "status": "pending", "total_chunks": None,
                "chunks_done": [], "aggregated": False, "completed_at": None,
            })

            # Discover completed chunks from existing files
            existing = set(entry.get("chunks_done") or [])
            for f in CHUNKS_DIR.glob(f"pdf_{i}_chunk_*.txt"):
                try:
                    idx = int(f.stem.split("_chunk_")[1])
                    existing.add(idx)
                except (IndexError, ValueError):
                    pass
            if set(existing) != set(entry.get("chunks_done") or []):
                entry["chunks_done"] = sorted(existing)
                changed = True

            # Compute total_chunks from extracted text (instant — no PDF open)
            total_pages = count_pages(PDF_FILES[i])
            total_chunks = math.ceil(total_pages / self.chunk_size)
            if entry.get("total_chunks") != total_chunks:
                entry["total_chunks"] = total_chunks
                changed = True

            # Aggregated PDF result
            pdf_result = RESULTS_DIR / f"pdf_{i}.txt"
            if pdf_result.exists() and not entry.get("aggregated"):
                entry["aggregated"] = True
                entry["status"] = "done"
                changed = True

            # Reset any "running" that wasn't completed (interrupted run)
            if entry.get("status") == "running":
                entry["status"] = "pending"
                changed = True

        # Phase 2
        final_report = RESULTS_DIR / "final_report.txt"
        if final_report.exists() and self._data["phase2"]["status"] != "done":
            self._data["phase2"]["status"] = "done"
            changed = True

        if changed:
            self._save()

    def init_pdf(self, i: int, total_chunks: int) -> None:
        entry = self._data["pdfs"][str(i)]
        entry["total_chunks"] = total_chunks
        entry["status"] = "running"
        self._save()

    def mark_chunk_done(self, i: int, chunk_idx: int) -> None:
        entry = self._data["pdfs"][str(i)]
        done = set(entry["chunks_done"])
        done.add(chunk_idx)
        entry["chunks_done"] = sorted(done)
        self._save()

    def mark_pdf_aggregated(self, i: int) -> None:
        entry = self._data["pdfs"][str(i)]
        entry["aggregated"] = True
        entry["status"] = "done"
        entry["completed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        self._save()

    def mark_phase2_running(self) -> None:
        self._data["phase2"]["status"] = "running"
        self._save()

    def mark_phase2_done(self) -> None:
        self._data["phase2"]["status"] = "done"
        self._data["phase2"]["completed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        self._save()

    def is_chunk_done(self, i: int, chunk_idx: int) -> bool:
        return chunk_idx in (self._data["pdfs"][str(i)].get("chunks_done") or [])

    def is_pdf_aggregated(self, i: int) -> bool:
        return bool(self._data["pdfs"][str(i)].get("aggregated"))

    def is_phase2_done(self) -> bool:
        return self._data["phase2"]["status"] == "done"

    def print_status(self) -> None:
        sep = "─" * 62
        print(f"\nPipeline status  (chunk_size={self.chunk_size})")
        print(sep)
        for i in range(len(PDF_FILES)):
            entry = self._data["pdfs"][str(i)]
            status = entry.get("status", "pending")
            done_n = len(entry.get("chunks_done") or [])
            total_n = entry.get("total_chunks")
            chunk_str = f"{done_n}/{total_n}" if total_n else f"{done_n}/?"
            ts = entry.get("completed_at") or "—"
            if ts and ts != "—":
                ts = ts[:16].replace("T", " ")
            name = PDF_FILES[i].name
            status_icon = {"done": "✓", "running": "→", "pending": " "}.get(status, " ")
            print(f"  {status_icon} PDF {i}  {name:<22}  {status:<8}  {chunk_str:>7} chunks  {ts}")

        p2 = self._data["phase2"]
        p2_status = p2.get("status", "pending")
        p2_ts = p2.get("completed_at") or "—"
        if p2_ts and p2_ts != "—":
            p2_ts = p2_ts[:16].replace("T", " ")
        p2_icon = {"done": "✓", "running": "→", "pending": " "}.get(p2_status, " ")
        print(f"  {p2_icon} Phase 2 (merge)              {p2_status:<8}  {'':>15}  {p2_ts}")

        done_count = sum(1 for i in range(len(PDF_FILES))
                         if self._data["pdfs"][str(i)].get("aggregated"))
        print(sep)
        print(f"  Done: {done_count}/{len(PDF_FILES)} PDFs   Phase 2: {p2_status}\n")

    def _save(self) -> None:
        self._data["last_updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PROGRESS_FILE)


# ─── Phase 2 merge (no argparse/input/reset_metrics) ─────────────────────────

def _run_phase2(client: anthropic.Anthropic) -> None:
    final_path = RESULTS_DIR / "final_report.txt"
    available = [(i, RESULTS_DIR / f"pdf_{i}.txt") for i in range(len(PDF_FILES))
                 if (RESULTS_DIR / f"pdf_{i}.txt").exists()]

    if not available:
        log("Phase 2: no per-PDF result files found — skipping")
        return

    log(f"=== Phase 2: merging {len(available)} per-PDF reports ===")
    combined = ""
    for i, path in available:
        content = path.read_text(encoding="utf-8")
        combined += f"\n\n{'='*40}\n=== תוצאות PDF {i+1} ({path.name}) ===\n{'='*40}\n{content}"
        log(f"  Loaded {path.name} ({len(content):,} chars)")

    user_message = f"""להלן תוצאות הניתוח מ-{len(available)} קבצי ווטסאפ:

{combined}

משימתך: צור דוח מאוחד ומסודר לפי 54 הסעיפים.

כללים:
1. עבור כל סעיף — אחד את כל הממצאים מכל הקבצים.
2. הסר כפילויות: אם אותו ציטוט (אותו תוכן ותאריך) מופיע בכמה קבצים — כלול אותו פעם אחת.
3. שמור על פורמט הממצא המדויק לכל ציטוט:

• ציטוט: "...הטקסט המדויק..."
  תאריך ושעה: DD.MM.YYYY, HH:MM
  מקור: yoni-meitalN.pdf, עמוד NN
  הקשר: [משפט קצר]

4. פרמט כל סעיף כך (השתמש בקווים המפרידים בדיוק):

────────────────────────────────────────────────────────────────────────────────
סעיף N: [ניסוח הסעיף]
────────────────────────────────────────────────────────────────────────────────
[ממצאים בפורמט לעיל, או: "לא נמצאה עדות מפורשת בטקסט"]

5. פתח את הדוח בכותרת הבאה (בדיוק):
================================================================================
דוח ממצאים — תכתובות ווטסאפ יונתן-מיטל
================================================================================"""

    result = call_claude_streaming(client, SYSTEM_PROMPT, user_message,
                                   max_tokens=32000, label="phase2-merge")

    run_time = datetime.datetime.now()
    sep = "=" * 80
    thin = "─" * 80
    report = f"""{sep}
דוח ממצאים סופי — תכתובות ווטסאפ יונתן-מיטל
{sep}
תאריך ריצה : {run_time.strftime('%d.%m.%Y  %H:%M:%S')}
קבצים שנותחו: {', '.join(path.name for _, path in available)}
{thin}

{metrics.report_block()}

{result}
"""
    final_path.write_text(report, encoding="utf-8")
    log(f"Final report saved → {final_path}  ({len(report):,} chars)")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run full pipeline end-to-end with chunk-level progress tracking."
    )
    parser.add_argument(
        "-c", "--chunk-size", type=int, default=DEFAULT_BATCH_SIZE, metavar="N",
        help=f"Pages per chunk (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Print progress and exit without running anything.",
    )
    args = parser.parse_args()

    tracker = ProgressTracker(chunk_size=args.chunk_size)

    if args.status:
        if RESULTS_DIR.exists():
            tracker.load()
        else:
            # Build in-memory default without saving (nothing exists yet)
            tracker._data = {
                "schema_version": 1, "chunk_size": args.chunk_size,
                "pdfs": {
                    str(i): {"status": "pending", "total_chunks": None,
                              "chunks_done": [], "aggregated": False, "completed_at": None}
                    for i in range(len(PDF_FILES))
                },
                "phase2": {"status": "pending", "completed_at": None},
            }
        tracker.print_status()
        sys.exit(0)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY=sk-ant-...",
              file=sys.stderr)
        sys.exit(1)

    RESULTS_DIR.mkdir(exist_ok=True)
    CHUNKS_DIR.mkdir(exist_ok=True)

    tracker.load()
    tracker.print_status()

    reset_metrics()
    client = anthropic.Anthropic(api_key=api_key)
    chunk_size = args.chunk_size

    for i in range(len(PDF_FILES)):
        pdf_path = PDF_FILES[i]

        if tracker.is_pdf_aggregated(i):
            log(f"[PDF {i}] skipped — pdf_{i}.txt already exists")
            continue

        total_pages = count_pages(pdf_path)
        num_chunks = math.ceil(total_pages / chunk_size)
        pages_label = f"כל {total_pages} עמודים"

        log(f"\n{'='*50}")
        log(f"[PDF {i+1}/7] {pdf_path.name}  ({total_pages} pages → {num_chunks} chunks of {chunk_size})")
        log(f"{'='*50}")

        tracker.init_pdf(i, num_chunks)

        for chunk_idx in range(num_chunks):
            if tracker.is_chunk_done(i, chunk_idx):
                start = chunk_idx * chunk_size
                end = min(start + chunk_size, total_pages)
                log(f"  [skip] chunk {chunk_idx} (pages {start+1}–{end}) — already done")
                continue

            start = chunk_idx * chunk_size
            end = min(start + chunk_size, total_pages)
            process_chunk(client, pdf_path, i, chunk_idx, start, end, total_pages,
                          skip_cache=False)
            tracker.mark_chunk_done(i, chunk_idx)

        # All chunks done — aggregate
        chunk_results = [
            (CHUNKS_DIR / f"pdf_{i}_chunk_{c}.txt").read_text(encoding="utf-8")
            for c in range(num_chunks)
        ]
        aggregate_chunks(client, i, chunk_results, pdf_path.name, pages_label,
                         skip_cache=False)
        tracker.mark_pdf_aggregated(i)

    if not tracker.is_phase2_done():
        tracker.mark_phase2_running()
        _run_phase2(client)
        tracker.mark_phase2_done()
    else:
        log("[Phase 2] skipped — final_report.txt already exists")

    log(f"\n{'='*50}")
    for line in metrics.summary_lines():
        log(line)

    tracker.print_status()
    log("All done.")


if __name__ == "__main__":
    main()
