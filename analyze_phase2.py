#!/usr/bin/env python3
"""
Phase 2: Merge the 7 per-PDF reports into one final consolidated report.

Reads:  results/pdf_0.txt … results/pdf_6.txt
Writes: results/final_report.txt

Run this after analyze_phase1.py has completed all 7 PDFs.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 analyze_phase2.py
"""

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import (
    RESULTS_DIR, SYSTEM_PROMPT,
    call_claude_streaming, log,
    metrics, reset_metrics,
)
import anthropic


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.\nRun: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    # Check which per-PDF result files exist
    available = []
    missing = []
    for i in range(7):
        path = RESULTS_DIR / f"pdf_{i}.txt"
        if path.exists():
            available.append((i, path))
        else:
            missing.append(i)

    if not available:
        print("ERROR: No per-PDF result files found in results/.")
        print("Run python3 analyze_phase1.py first.")
        sys.exit(1)

    if missing:
        print(f"WARNING: Missing results for PDFs: {missing}")
        print("The final report will be based only on available files.")
        print()

    final_path = RESULTS_DIR / "final_report.txt"
    if final_path.exists():
        print(f"final_report.txt already exists at {final_path}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    print(f"=== Phase 2: Merging {len(available)} per-PDF reports ===\n")

    combined = ""
    for i, path in available:
        content = path.read_text(encoding="utf-8")
        combined += f"\n\n{'='*40}\n=== תוצאות PDF {i+1} ({path.name}) ===\n{'='*40}\n{content}"
        print(f"  Loaded {path.name} ({len(content):,} chars)")

    print(f"\nTotal combined input: {len(combined):,} chars (~{len(combined)//4:,} tokens)")
    print("Sending to Claude for final merge...\n")
    print("-" * 50)

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

    reset_metrics()
    client = anthropic.Anthropic(api_key=api_key)
    result = call_claude_streaming(client, SYSTEM_PROMPT, user_message, max_tokens=32000,
                                   label="phase2-merge")

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
    print(f"\n{'='*50}")
    for line in metrics.summary_lines():
        print(line)
    print(f"Final report saved to: {final_path}")
    print(f"Size: {len(report):,} chars")


if __name__ == "__main__":
    main()
