#!/usr/bin/env python3
"""
Dev validation smoke test — no files saved, progress logged to run.log.

DEV WORKFLOW (always start here, iterate fast):
  1. python3 validate.py          — 3 pages (default), ~15s → confirm prompt + format
  2. python3 validate.py -p 10    — 10 pages for deeper spot-check
  3. python3 analyze_phase1.py 0  — 1 full PDF      → confirm per-PDF output
  4. python3 analyze_phase1.py    — all 7 PDFs      → full run (~30-40 min)
  5. python3 analyze_phase2.py    — merge           → final_report.txt

Track progress live:
  tail -f run.log

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 validate.py            # 3 pages
  python3 validate.py -p 5      # 5 pages
  python3 validate.py --pages 1 # single page (fastest)
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    LOG_FILE, PDF_FILES, RESULTS_DIR, SYSTEM_PROMPT, INVESTIGATION_ITEMS,
    extract_text, call_claude, log,
    metrics, reset_metrics,
)
import anthropic

DEFAULT_PAGES = 3  # ~4K tokens — fast enough for quick dev iteration


def main():
    parser = argparse.ArgumentParser(
        description="Dev validation smoke test — no files saved."
    )
    parser.add_argument(
        "-p", "--pages", type=int, default=DEFAULT_PAGES, metavar="N",
        help=f"Number of pages to analyse from yoni-meital0.pdf (default: {DEFAULT_PAGES}).",
    )
    args = parser.parse_args()
    validation_pages = args.pages

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    # Clear log for this run
    LOG_FILE.write_text("", encoding="utf-8")
    reset_metrics()

    log("=== API CONNECTIVITY TEST ===")
    client = anthropic.Anthropic(api_key=api_key)
    try:
        ping = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "reply: ok"}],
        )
        log(f"✓ API OK — model responded: '{ping.content[0].text.strip()}'")
    except Exception as e:
        log(f"✗ API FAILED: {e}")
        log("Fix the API key / billing before running the full analysis.")
        sys.exit(1)

    log("=== VALIDATION RUN (dev mode) ===")
    pdf = PDF_FILES[0]
    log(f"File  : {pdf.name}")
    log(f"Pages : 1–{validation_pages}")
    log(f"Log   : {LOG_FILE}")

    log("Extracting text...")
    text = extract_text(pdf, start_page=0, end_page=validation_pages)
    log(f"Extracted {len(text):,} chars from {validation_pages} pages")

    user_message = f"""להלן תכתובות ווטסאפ (עמודים 1–{validation_pages} לצורך בדיקה בלבד):

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

אם לא נמצאה עדות בטקסט זה — ציין: "לא נמצאה עדות בחלק זה"."""

    result = call_claude(client, SYSTEM_PROMPT, user_message, max_tokens=4096, label=f"validate {validation_pages} pages")

    # Build shareable report
    run_time = datetime.datetime.now()
    sep = "=" * 80
    thin = "─" * 80
    report = f"""{sep}
דוח אימות — תכתובות ווטסאפ יונתן ומיטל
{sep}
תאריך ריצה : {run_time.strftime('%d.%m.%Y  %H:%M:%S')}
קובץ       : {pdf.name}
עמודים     : 1–{validation_pages} (בדיקה בלבד)
מצב        : VALIDATION — לא כל הטקסט נותח
{thin}
הדוח מכיל ממצאים לפי 54 הסעיפים שנבדקו.
כל ממצא כולל: ציטוט מדויק | תאריך ושעה | מקור (קובץ + עמוד) | הקשר.
{sep}

{metrics.report_block()}

{result}
"""

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = run_time.strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"validation_{ts}.txt"
    report_path.write_text(report, encoding="utf-8")

    log(f"=== DONE — {len(result):,} chars output ===")
    for line in metrics.summary_lines():
        log(line)
    log(f"Report saved → {report_path}")
    log("If format looks correct → run: python3 analyze_phase1.py -c 3 -p 9 0")


if __name__ == "__main__":
    main()
