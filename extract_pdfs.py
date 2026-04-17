#!/usr/bin/env python3
"""
One-time PDF → plain-text conversion. Run this before any analysis script.

Reads:  yoni-meital0.pdf … yoni-meital6.pdf
Writes: extracted/yoni-meital0.txt … extracted/yoni-meital6.txt

Each text file contains the full PDF content with page markers:
  === [yoni-meitalN.pdf | עמוד NN] ===

All analysis scripts (validate.py, analyze_phase1.py, etc.) check for these
files first and skip PDF extraction entirely — much faster and easier to inspect.

Usage:
  python3 extract_pdfs.py           # convert all 7 PDFs
  python3 extract_pdfs.py 0 2       # convert only PDFs 0 and 2
  python3 extract_pdfs.py --force   # re-extract even if files already exist
"""

import argparse
import sys
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).parent))
from analysis_utils import PDF_FILES, EXTRACTED_DIR, _extract_text_from_pdf


def convert_pdf(pdf_path: Path, force: bool = False) -> None:
    out_path = EXTRACTED_DIR / f"{pdf_path.stem}.txt"

    if out_path.exists() and not force:
        size = out_path.stat().st_size
        total = len(fitz.open(str(pdf_path)))
        print(f"  [skip] {out_path.name} already exists ({size:,} bytes, {total} pages)")
        return

    print(f"  Extracting {pdf_path.name} ...", end="", flush=True)
    total = len(fitz.open(str(pdf_path)))
    text = _extract_text_from_pdf(pdf_path)
    out_path.write_text(text, encoding="utf-8")
    size = out_path.stat().st_size
    print(f" done — {total} pages, {size:,} bytes → {out_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert WhatsApp PDFs to plain-text files for fast reuse."
    )
    parser.add_argument(
        "pdfs", nargs="*", type=int,
        help="PDF indices to convert (0–6). Omit for all 7.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract even if output file already exists.",
    )
    args = parser.parse_args()

    EXTRACTED_DIR.mkdir(exist_ok=True)

    indices = args.pdfs if args.pdfs else list(range(len(PDF_FILES)))
    invalid = [i for i in indices if i < 0 or i >= len(PDF_FILES)]
    if invalid:
        print(f"Invalid PDF indices: {invalid}. Valid range: 0–{len(PDF_FILES)-1}", file=sys.stderr)
        sys.exit(1)

    print(f"Extracting {len(indices)} PDF(s) → {EXTRACTED_DIR}/\n")
    for i in indices:
        pdf = PDF_FILES[i]
        if not pdf.exists():
            print(f"  [missing] {pdf.name} — skipping")
            continue
        convert_pdf(pdf, force=args.force)

    print(f"\nDone. Files in {EXTRACTED_DIR}/")
    print("All analysis scripts will now use these files automatically.")


if __name__ == "__main__":
    main()
