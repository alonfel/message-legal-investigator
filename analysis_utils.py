"""Shared utilities for WhatsApp PDF analysis scripts."""

import datetime
import re
import time
from pathlib import Path
import anthropic
import fitz  # PyMuPDF — better spatial text ordering than pypdf

BASE_DIR = Path(__file__).parent
PDF_FILES = [BASE_DIR / f"yoni-meital{i}.pdf" for i in range(7)]
RESULTS_DIR = BASE_DIR / "results"
CHUNKS_DIR = RESULTS_DIR / "chunks"
EXTRACTED_DIR = BASE_DIR / "extracted"  # pre-extracted plain-text files

# ─── Prompts ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """אתה מומחה לניתוח תכתובות ווטסאפ לצורך חקירה משפטית.

כללים:
1. התבסס אך ורק על הכתוב בטקסט. אל תמציא.
2. דווח רק על ממצאים בולטים וחד-משמעיים — לא על כל סעיף.
3. וודא שהציטוט משקף את כוונת הסעיף ולא נאמר בציניות או בהקשר הפוך.
4. היה תמציתי מאוד. פחות זה יותר.

פורמט לכל ממצא (שורה אחת):
סעיף N | ציטוט: "טקסט מדויק" | תאריך: DD.MM.YYYY HH:MM | מקור: קובץ עמוד NN

סמנים בטקסט: === [yoni-meitalN.pdf | עמוד NN] === מציינים שם קובץ ועמוד — השתמש בהם בשדה "מקור"."""
INVESTIGATION_ITEMS = """להלן 54 הסעיפים לחקירה. דווח רק על ממצאים בולטים וחד-משמעיים — לא על כל סעיף.

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

# ─── Helpers ─────────────────────────────────────────────────────────────────

# Matches the page marker lines embedded in extracted text, e.g.:
#   === [yoni-meital0.pdf | עמוד 12] ===
_PAGE_MARKER_RE = re.compile(r"^=== \[.+?\] ===$", re.MULTILINE)


def _slice_pages(full_text: str, start_page: int, end_page: int | None) -> str:
    """Return the requested 0-based page slice from pre-extracted full text."""
    # Split into (marker, body) pairs by finding marker positions
    boundaries = [m.start() for m in _PAGE_MARKER_RE.finditer(full_text)]
    if not boundaries:
        return full_text  # no markers — return as-is

    pages = []
    for i, pos in enumerate(boundaries):
        next_pos = boundaries[i + 1] if i + 1 < len(boundaries) else len(full_text)
        pages.append(full_text[pos:next_pos].strip())

    end = end_page if end_page is not None else len(pages)
    return "\n\n".join(pages[start_page:end])


def _extract_text_from_pdf(pdf_path: Path, start_page: int = 0, end_page: int | None = None) -> str:
    """Raw PDF extraction using PyMuPDF (preserves timestamps via spatial ordering)."""
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    end = end_page if end_page is not None else total
    parts = []
    for page_idx in range(start_page, min(end, total)):
        page_number = page_idx + 1  # 1-based
        text = doc[page_idx].get_text("text")
        if text:
            lines = text.splitlines()
            # Strip watermark header (first line contains the file path/name)
            if lines and pdf_path.stem in lines[0]:
                lines = lines[1:]
            content = "\n".join(lines).strip()
            if content:
                marker = f"=== [{pdf_path.name} | עמוד {page_number}] ==="
                parts.append(f"{marker}\n{content}")
    doc.close()
    return "\n\n".join(parts)


def count_pages(pdf_path: Path) -> int:
    """Count total pages by scanning page markers in the pre-extracted txt file.

    Falls back to opening the PDF with fitz if the txt doesn't exist yet.
    Much faster than opening the PDF when txt files are available.
    """
    extracted_file = EXTRACTED_DIR / f"{pdf_path.stem}.txt"
    if extracted_file.exists():
        text = extracted_file.read_text(encoding="utf-8")
        return len(_PAGE_MARKER_RE.findall(text))
    # Fallback: open the PDF directly
    doc = fitz.open(str(pdf_path))
    n = len(doc)
    doc.close()
    return n


def extract_text(pdf_path: Path, start_page: int = 0, end_page: int | None = None) -> str:
    """Return text for the requested page range, with page markers for source tracing.

    Prefers pre-extracted text files in extracted/ (fast, no pypdf overhead).
    Falls back to live PDF extraction if the file doesn't exist yet.

    Each page is prefixed with: === [yoni-meitalN.pdf | עמוד NN] ===
    Page numbers are 1-based to match what a reader sees in a PDF viewer.
    """
    extracted_file = EXTRACTED_DIR / f"{pdf_path.stem}.txt"
    if extracted_file.exists():
        full_text = extracted_file.read_text(encoding="utf-8")
        return _slice_pages(full_text, start_page, end_page)
    # Fallback: extract live from PDF
    return _extract_text_from_pdf(pdf_path, start_page, end_page)


LOG_FILE = BASE_DIR / "run.log"

MODEL = "claude-haiku-4-5-20251001"

# Pricing for claude-haiku-4-5 ($ per million tokens)
_PRICE_INPUT_PER_M  = 0.80
_PRICE_OUTPUT_PER_M = 4.00

# ─── Run-level metrics accumulator ───────────────────────────────────────────

class RunMetrics:
    """Accumulates token usage and timing across all API calls in a run."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset all counters (mutates in place so imported references stay valid)."""
        self.calls: int = 0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.elapsed_seconds: float = 0.0
        self._run_start: float = time.monotonic()
        self.calls_detail: list[dict] = []

    def record(self, input_tokens: int, output_tokens: int, elapsed: float, label: str = "") -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.elapsed_seconds += elapsed
        call_cost = (input_tokens / 1_000_000 * _PRICE_INPUT_PER_M +
                     output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_M)
        self.calls_detail.append({
            "label": label or f"call-{self.calls}",
            "in": input_tokens,
            "out": output_tokens,
            "elapsed": elapsed,
            "cost": call_cost,
        })

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens  / 1_000_000 * _PRICE_INPUT_PER_M +
            self.output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_M
        )

    @property
    def total_elapsed(self) -> float:
        return time.monotonic() - self._run_start

    def running_total_line(self) -> str:
        wall = self.total_elapsed
        m, s = divmod(int(wall), 60)
        return f"  ↳ running total: {self.calls} calls  ${self.cost_usd:.4f}  wall: {m}m {s:02d}s"

    def breakdown_table(self) -> str:
        if not self.calls_detail:
            return "  (no calls recorded)"
        col = {"label": 18, "in": 10, "out": 10, "time": 8, "cost": 9}
        sep = "─" * (sum(col.values()) + len(col) * 3 + 1)
        hdr = (f"  {'Call':<{col['label']}} │ {'In tok':>{col['in']}} │"
               f" {'Out tok':>{col['out']}} │ {'Time':>{col['time']}} │ {'Cost':>{col['cost']}}")
        rows = [sep, hdr, sep]
        for d in self.calls_detail:
            m, s = divmod(int(d["elapsed"]), 60)
            t = f"{m}m{s:02d}s" if m else f"{s}s"
            rows.append(
                f"  {d['label']:<{col['label']}} │ {d['in']:>{col['in']},} │"
                f" {d['out']:>{col['out']},} │ {t:>{col['time']}} │ ${d['cost']:>{col['cost']-1}.4f}"
            )
        rows.append(sep)
        m, s = divmod(int(self.total_elapsed), 60)
        rows.append(
            f"  {'TOTAL':<{col['label']}} │ {self.input_tokens:>{col['in']},} │"
            f" {self.output_tokens:>{col['out']},} │ {m}m{s:02d}s:>{col['time']-2} │ ${self.cost_usd:>{col['cost']-1}.4f}"
        )
        rows.append(sep)
        return "\n".join(rows)

    def summary_lines(self) -> list[str]:
        total = self.total_elapsed
        m, s = divmod(int(total), 60)
        return [
            f"API calls      : {self.calls}",
            f"Input tokens   : {self.input_tokens:,}",
            f"Output tokens  : {self.output_tokens:,}",
            f"Total tokens   : {self.input_tokens + self.output_tokens:,}",
            f"Cost (USD)     : ${self.cost_usd:.4f}",
            f"Wall time      : {m}m {s:02d}s",
        ]

    def report_block(self) -> str:
        sep = "─" * 80
        summary = "\n".join(self.summary_lines())
        breakdown = self.breakdown_table()
        return f"{sep}\nמדדי ריצה (Metrics)\n{sep}\n{summary}\n\nPer-call breakdown:\n{breakdown}\n{sep}"


# Global metrics instance — call metrics.reset() at the start of each script
metrics = RunMetrics()


def reset_metrics() -> None:
    """Reset the global metrics object in place (imported references stay valid)."""
    metrics.reset()


# ─── Logging ─────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """Write timestamped message to both stdout and run.log."""
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def call_claude(
    client: anthropic.Anthropic,
    system: str,
    user_message: str,
    max_tokens: int = 4096,
    label: str = "",
) -> str:
    """Single non-streaming API call. Records token usage and cost in metrics."""
    input_chars = len(system) + len(user_message)
    preview = user_message[:120].replace("\n", " ").strip()
    log(f"API call{' [' + label + ']' if label else ''} — {input_chars:,} chars (~{input_chars//2:,} tok est.)")
    log(f"  → preview: \"{preview}...\"")
    log(f"  → max_tokens: {max_tokens}")

    t0 = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    elapsed = time.monotonic() - t0

    in_tok  = response.usage.input_tokens
    out_tok = response.usage.output_tokens
    call_cost = (in_tok / 1_000_000 * _PRICE_INPUT_PER_M +
                 out_tok / 1_000_000 * _PRICE_OUTPUT_PER_M)
    metrics.record(in_tok, out_tok, elapsed, label=label)

    result = response.content[0].text
    log(f"  ✓ {elapsed:.1f}s — in:{in_tok:,} out:{out_tok:,} tok  ${call_cost:.4f}  stop={response.stop_reason}")
    log(metrics.running_total_line())
    return result


def call_claude_streaming(
    client: anthropic.Anthropic,
    system: str,
    user_message: str,
    max_tokens: int = 4096,
    label: str = "",
) -> str:
    """Streaming API call — required when max_tokens may exceed the 10-minute SDK timeout threshold."""
    input_chars = len(system) + len(user_message)
    preview = user_message[:120].replace("\n", " ").strip()
    log(f"API call{' [' + label + ']' if label else ''} — {input_chars:,} chars (~{input_chars//2:,} tok est.)")
    log(f"  → preview: \"{preview}...\"")
    log(f"  → max_tokens: {max_tokens}")

    t0 = time.monotonic()
    chunks: list[str] = []
    with client.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
        final = stream.get_final_message()
    elapsed = time.monotonic() - t0

    in_tok  = final.usage.input_tokens
    out_tok = final.usage.output_tokens
    call_cost = (in_tok / 1_000_000 * _PRICE_INPUT_PER_M +
                 out_tok / 1_000_000 * _PRICE_OUTPUT_PER_M)
    metrics.record(in_tok, out_tok, elapsed, label=label)

    result = "".join(chunks)
    log(f"  ✓ {elapsed:.1f}s — in:{in_tok:,} out:{out_tok:,} tok  ${call_cost:.4f}  stop={final.stop_reason}")
    log(metrics.running_total_line())
    return result
