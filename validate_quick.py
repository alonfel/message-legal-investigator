#!/usr/bin/env python3
"""
Quick smoke test — ~5 seconds, no PDF extraction, no API ping.
Reads sample_chat.txt (tiny static WhatsApp snippet) against 5 items.

Use this to validate prompt format changes instantly before running anything
against real PDFs.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 validate_quick.py
"""

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    BASE_DIR, LOG_FILE, RESULTS_DIR, SYSTEM_PROMPT,
    call_claude, log,
    metrics, reset_metrics,
)
import anthropic

SAMPLE_FILE = BASE_DIR / "sample_chat.txt"

# 5 representative items — covers different categories (financial, presence,
# aggression, acknowledgement, fairness). Edit freely to test specific items.
QUICK_ITEMS = """להלן 5 סעיפים לבדיקה מהירה. עבור כל סעיף — ספק את כל הממצאים הרלוונטיים.

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

    if not SAMPLE_FILE.exists():
        print(f"ERROR: {SAMPLE_FILE} not found.", file=sys.stderr)
        sys.exit(1)

    LOG_FILE.write_text("", encoding="utf-8")
    reset_metrics()

    text = SAMPLE_FILE.read_text(encoding="utf-8")
    log(f"=== QUICK VALIDATE — {len(text):,} chars from {SAMPLE_FILE.name} ===")

    user_message = f"""להלן תכתובת ווטסאפ לדוגמה (טקסט בדיקה בלבד):

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
        max_tokens=2048,
        label="quick-validate",
    )

    # Build report
    run_time = datetime.datetime.now()
    sep = "=" * 80
    report = f"""{sep}
דוח בדיקה מהירה — sample_chat.txt
{sep}
תאריך ריצה : {run_time.strftime('%d.%m.%Y  %H:%M:%S')}
מצב        : QUICK VALIDATE — טקסט לדוגמה בלבד, 5 סעיפים
{sep}

{metrics.report_block()}

{result}
"""

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = run_time.strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"quick_{ts}.txt"
    report_path.write_text(report, encoding="utf-8")

    log("=== RESULT ===")
    print(result)
    log(f"=== DONE ===")
    for line in metrics.summary_lines():
        log(line)
    log(f"Report → {report_path}")


if __name__ == "__main__":
    main()
