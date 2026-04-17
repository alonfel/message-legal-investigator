#!/usr/bin/env python3
"""
Fast smoke test on real WhatsApp text — ~15s, 2 pages, 5 items.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 test_real.py
"""

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    BASE_DIR, LOG_FILE, RESULTS_DIR, SYSTEM_PROMPT,
    PDF_FILES, extract_text, call_claude, log,
    metrics, reset_metrics,
)
import anthropic

PAGES = 1  # single page — fastest possible real-text test

QUICK_ITEMS = """להלן 5 סעיפים לבדיקה. עבור כל סעיף — ספק את כל הממצאים הרלוונטיים.

1. חומרים שמעידים שמיטל הורידה את שכר הדירה על דעת עצמה מ-5,000 ש"ח ל-3,500 וללא דיאלוג.
2. עדויות שיונתן מנסה להגיע לעמק השווה ומחפש צדק, ומיטל לא מסכימה.
3. עדויות שמיטל מודה על מה שיונתן עושה עם הבנים.
6. עדויות שיונתן נמצא עם הבנים.
7. עדויות שמיטל מתייחסת ליונתן בכוחניות ובאגרסיביות."""


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    LOG_FILE.write_text("", encoding="utf-8")
    reset_metrics()

    pdf = PDF_FILES[0]
    log(f"=== TEST REAL — {pdf.name}, עמודים 1–{PAGES}, 5 סעיפים ===")

    text = extract_text(pdf, start_page=0, end_page=PAGES)
    log(f"Extracted {len(text):,} chars")

    user_message = f"""להלן תכתובת ווטסאפ אמיתית (עמודים 1–{PAGES} בלבד, לצורך בדיקה):

הטקסט מכיל סמנים בפורמט: === [שם-קובץ.pdf | עמוד NN] ===
יש לציין את שם הקובץ ומספר העמוד בשדה "מקור" של כל ממצא.

---BEGIN WHATSAPP TRANSCRIPT---
{text}
---END WHATSAPP TRANSCRIPT---

{QUICK_ITEMS}

עבוד סעיף אחר סעיף. עבור כל סעיף ציין את מספרו וניסוחו, ולאחר מכן את הממצאים בפורמט הבא:

• ציטוט: "...הטקסט המדויק..."
  תאריך ושעה: DD.MM.YYYY, HH:MM
  מקור: yoni-meitalN.pdf, עמוד NN
  הקשר: [משפט קצר]

אם לא נמצאה עדות בטקסט זה — ציין: "לא נמצאה עדות בחלק זה"."""

    result = call_claude(
        anthropic.Anthropic(api_key=api_key),
        SYSTEM_PROMPT, user_message,
        max_tokens=4096,
        label="test-real",
    )

    run_time = datetime.datetime.now()
    sep = "=" * 80
    report = f"""{sep}
דוח בדיקה — טקסט אמיתי
{sep}
תאריך ריצה : {run_time.strftime('%d.%m.%Y  %H:%M:%S')}
קובץ       : {pdf.name}
עמודים     : 1–{PAGES}
מצב        : TEST — 2 עמודים, 5 סעיפים בלבד
{sep}

{metrics.report_block()}

{result}
"""

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = run_time.strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"test_real_{ts}.txt"
    report_path.write_text(report, encoding="utf-8")

    log("=== RESULT ===")
    print(result)
    log("=== DONE ===")
    for line in metrics.summary_lines():
        log(line)
    log(f"Report → {report_path}")


if __name__ == "__main__":
    main()
