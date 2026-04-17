#!/usr/bin/env python3
"""
WhatsApp conversation analysis across 7 PDF files.
Each PDF is processed separately (fits in 200K context), then results are aggregated.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 analyze.py
"""

import os
import sys
import json
from pathlib import Path
from pypdf import PdfReader
import anthropic

BASE_DIR = Path(__file__).parent
PDF_FILES = [BASE_DIR / f"yoni-meital{i}.pdf" for i in range(7)]
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ─── The 54-item investigation prompt ────────────────────────────────────────

SYSTEM_PROMPT = """אתה מומחה לניתוח טקסטים וזיהוי דפוסי התנהגות. תפקידך לנתח תכתובות ווטסאפ ולאתר ממצאים ספציפיים.

חוקי ברזל:
1. התבסס אך ורק על הכתוב בטקסט. אל תמציא, אל תשער ואל תשלים פערים.
2. עבור כל ממצא — ספק ציטוט מדויק מילה במילה, עם תאריך ושעה מדויקים.
3. לפני כל ציטוט — נתח את ההקשר וודא שהוא משקף את כוונת הסעיף ולא נאמר בציניות או בהקשר הפוך.
4. אם לא מצאת עדות — כתוב במפורש: "לא נמצאה עדות מפורשת בטקסט".
5. עבוד בצורה שיטתית, סעיף אחר סעיף. השתמש בלשון ישירה ועובדתית."""

INVESTIGATION_ITEMS = """להלן 54 הסעיפים לחקירה. עבור כל סעיף — ספק את כל הממצאים הרלוונטיים מהטקסט המצורף.

1. חומרים שמעידים שמיטל הורידה את שכר הדירה על דעת עצמה מ-5,000 ש"ח ל-3,500 וללא דיאלוג.
2. עדויות שיונתן מנסה להגיע לעמק השווה ומחפש צדק, ומיטל לא מסכימה.
3. עדויות שמיטל מודה על מה שיונתן עושה עם הבנים.
4. עדויות שיונתן מבקש את כספו חזרה ולא נענה או נענה בשלילה או בבוז.
5. עדויות שיונתן בא לקראת מיטל ומציע לעזור.
6. עדויות שיונתן נמצא עם הבנים.
7. עדויות שמיטל מתייחסת ליונתן בכוחניות ובאגרסיביות.
8. עדויות ליחס פוגעני של מיטל כלפי יונתן.
9. עדויות שיונתן מזמין את אליה להיות איתו ושהוא לא הורה מנוכר.
10. עדויות שמיטל מתערבת כשאליה איתו, וכשיש משבר היא לוקחת אותו ממנו על אף שהוא מבקש שלא (גם כשהיא לוקחת על עצמה תפקיד של "מושיעה").
11. עדויות שמיטל כותבת או מבקשת שאליה יהיה עם יונתן.
12. עדויות שמיטל מבקשת מיונתן לבשל עבורה ולילדים.
13. עדויות שמיטל מבקשת שיונתן ינקה את ביתה או ישטוף כלים בביתה.
14. עדויות לדאגה ואיכפתיות של יונתן ממיטל.
15. עדויות שמיטל לא משתפת פעולה בטיפול באליה.
16. עדויות של ניכור של מיטל כלפי יונתן.
17. עדויות של ניכור הורי מצד אליה כלפי יונתן.
18. עדויות לטענות של מיטל שלא מתיישבות עם המציאות.
19. עדויות שמיטל מתערבת ופוגעת ביחסים בין אליה לבין יונתן.
20. עדויות שמיטל סותרת את עצמה ואף משקרת.
21. עדויות שמיטל דוחה את הסידור הכלכלי ביניהם.
22. עדויות לפגיעה ולהתעוללות של מיטל בילדים.
23. עדויות לניכור הורי מצד מיטל.
24. עדויות לחוסר גבולות של מיטל.
25. עדויות לחוסר שיתוף פעולה הורי מצד מיטל.
26. עדויות לכך שמיטל קנתה נייד לאליה בלי דיאלוג עם יונתן.
27. עדויות שמיטל דרשה מיונתן לצאת מהפמילי לינק.
28. עדויות שמיטל לא נוכחת או לא מתפקדת כהורה מיטיב.
29. עדויות שמיטל הזניחה את הבנים, יצאה בלילה, והילדים דואגים ומיטל לא זמינה.
30. עדויות שמיטל אינה משתפת את יונתן פעולה בהחלטות הוריות.
31. עדויות לחוסר הגינות בסידור הכלכלי.
32. בקשה או דרישה של מיטל שיונתן ישלם 5,000 ש"ח מזונות לחודש ובנוסף את שכר הדירה.
33. עדויות שיונתן מתנגד ולא מסכים לדרישה שישלם את הרווח משכר הדירה בנוסף למזונות, או שמגביל זאת לזמן קצוב.
34. עדות לכך שמיטל מודיעה שהיא לוקחת כספים עבור מזונות מהסכום שיונתן נתן להוריה, ללא הסכמתו.
35. עדות לכך שיונתן מתנגד נחרצות שתיגע בכסף הזה, ושמבחינתו זה גזל ולא הוגן.
36. עדות שמיטל אומרת שיונתן הסכים לה לקחת מהכספים שלו שנמצאים בידי הוריה, ושיונתן שולל את דבריה ולא מסכים לכך.
37. עדות לכך שמיטל מתעכבת במתן תשובה להצעת יונתן לישוב הסכסוך, ואף מצטערת או מתנצלת על כך.
38. הוכחה לכך שמיטל מאחרת להגיע בזמנים שנקבעו.
39. הוכחה לכך שהבית של מיטל מבולגן ושזה פוגע בבנים.
40. הוכחה שלמיטל יש קושי בבוחן מציאות.
41. הוכחה שמיטל מזניחה את הבנים ביחסה.
42. עדות לכך שיונתן כותב שאליה לא בסדר וזקוק לעזרה, וזה לא נענה על ידי מיטל, או שנענה בשלילה, בדחייה, או בהשלכה שיונתן הוא מקור הבעיה.
43. עדויות לכך שמיטל לא מיטיבה לאליה.
44. עדויות לכך שמיטל לא עומדת בהבטחות שלה.
45. עדויות לכך שיש פער בין מה שמיטל אומרת, מבטיחה או מציגה לבין המציאות.
46. עדות לכך שמיטל לא מטפלת בעניין ההרטבה של מיכאל.
47. עדויות שמיטל לועגת או לא מכבדת את יונתן בשיח ביניהם.
48. מקומות בהם מיטל לא מוכנה לדיאלוג, בהקשר של החלטות ביחס לבנים.
49. עדויות לבקשתה שיונתן ינקה את ביתה.
50. עדויות לבקשות שיונתן יכין לה ולבנים אוכל.
51. עדויות להתנהגות תוקפנית של אליה או התנהגות שמביעה קושי ומצוקה.
52. עדויות לנוכחות מיטיבה של יונתן עם הבנים.
53. האשמות של מיטל כלפי יונתן.
54. עדויות לאגרסיביות ויחס פוגע של מיטל כלפי הבנים."""


def extract_text(pdf_path: Path) -> str:
    """Extract all text from a PDF file."""
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


def analyze_file(client: anthropic.Anthropic, pdf_path: Path, file_index: int) -> str:
    """Send one PDF's text to Claude for analysis of all 54 items."""
    result_path = RESULTS_DIR / f"result_{file_index}.txt"

    # Skip if already done
    if result_path.exists():
        print(f"  [skip] {pdf_path.name} — result already exists")
        return result_path.read_text(encoding="utf-8")

    print(f"  Extracting text from {pdf_path.name}...", flush=True)
    text = extract_text(pdf_path)
    token_estimate = len(text) // 4
    print(f"  {len(text):,} chars (~{token_estimate:,} tokens). Sending to Claude...", flush=True)

    user_message = f"""להלן תכתובות ווטסאפ (חלק {file_index + 1} מתוך 7):

---BEGIN WHATSAPP TRANSCRIPT---
{text}
---END WHATSAPP TRANSCRIPT---

{INVESTIGATION_ITEMS}

עבוד סעיף אחר סעיף. עבור כל סעיף ציין את מספרו, ולאחר מכן את הממצאים עם ציטוטים מדויקים (כולל תאריך ושעה)."""

    result_chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text_chunk in stream.text_stream:
            print(text_chunk, end="", flush=True)
            result_chunks.append(text_chunk)

    result = "".join(result_chunks)
    result_path.write_text(result, encoding="utf-8")
    print(f"\n\n  Saved to {result_path}\n", flush=True)
    return result


def aggregate_results(client: anthropic.Anthropic, per_file_results: list[str]) -> str:
    """Merge findings from all 7 files into one consolidated report."""
    aggregation_path = RESULTS_DIR / "final_report.txt"

    print("\n=== AGGREGATING RESULTS FROM ALL FILES ===\n", flush=True)

    combined = ""
    for i, result in enumerate(per_file_results):
        combined += f"\n\n=== תוצאות מקובץ {i + 1} ===\n{result}"

    user_message = f"""להלן תוצאות הניתוח מ-7 קבצים שונים של אותן תכתובות ווטסאפ:

{combined}

משימתך:
1. אחד את כל הממצאים לדוח אחד מסודר, מאורגן לפי 54 הסעיפים.
2. עבור כל סעיף — רשום את כל הציטוטים מכל הקבצים (ללא כפילויות).
3. אם בכמה קבצים נמצא אותו ציטוט — כלול אותו פעם אחת בלבד.
4. שמור על הפורמט: מספר הסעיף, ניסוח הסעיף, ולאחריו הממצאים עם ציטוטים.
5. אם לא נמצאה עדות בשום קובץ — ציין: "לא נמצאה עדות מפורשת בטקסט"."""

    result_chunks = []
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text_chunk in stream.text_stream:
            print(text_chunk, end="", flush=True)
            result_chunks.append(text_chunk)

    final_report = "".join(result_chunks)
    aggregation_path.write_text(final_report, encoding="utf-8")
    print(f"\n\nFinal report saved to: {aggregation_path}\n", flush=True)
    return final_report


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        print("Run: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("=== WhatsApp Analysis — 7 PDFs, 54 Investigation Items ===\n")
    print(f"Results directory: {RESULTS_DIR}\n")

    per_file_results = []
    for i, pdf_path in enumerate(PDF_FILES):
        print(f"[{i+1}/7] Processing {pdf_path.name}...")
        result = analyze_file(client, pdf_path, i)
        per_file_results.append(result)

    # Aggregate all results into one final report
    final = aggregate_results(client, per_file_results)
    print("\nDone! Final report: results/final_report.txt")


if __name__ == "__main__":
    main()
