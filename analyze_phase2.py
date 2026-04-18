#!/usr/bin/env python3
"""
Phase 2: Merge per-PDF/file reports into one final consolidated report.

Parses Phase 1 output files directly — no LLM call needed.

Reads:  results/pdf_0.txt … results/pdf_6.txt  (and results/file_*.txt)
Writes: results/final_report.txt

Usage:
  python3 analyze_phase2.py                          # auto-discover all result files
  python3 analyze_phase2.py results/file_test.txt    # single specific file
  python3 analyze_phase2.py results/pdf_0.txt results/pdf_1.txt  # explicit list
  python3 analyze_phase2.py results/file_test.txt --output /tmp/out.txt
"""

import argparse
import datetime
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analysis_utils
from analysis_utils import INVESTIGATION_ITEMS


THIN = "─" * 80
SEP  = "=" * 80


def _parse_section_titles() -> dict[int, str]:
    titles = {}
    for m in re.finditer(r'^(\d+)\.\s+(.+)$', INVESTIGATION_ITEMS, re.MULTILINE):
        titles[int(m.group(1))] = m.group(2).strip()
    return titles


def _parse_finding_line(line: str) -> dict | None:
    """Parse one pipe-delimited finding line into a dict, or return None."""
    # Match: סעיף N[(part)] | ציטוט: "..." | תאריך: ... | מקור: ...
    # Split on ' | ' first to avoid greedy-quote issues inside the citation.
    if not line.startswith('סעיף '):
        return None
    parts = line.split(' | ')
    if len(parts) < 4:
        return None

    section_part = parts[0].strip()
    section_m = re.match(r'^סעיף (\d+)', section_part)
    if not section_m:
        return None
    section_num = int(section_m.group(1))

    citation_raw = parts[1].strip()
    date_raw     = parts[2].strip()
    source_raw   = ' | '.join(parts[3:]).strip()  # source may contain ' | '

    # Strip Hebrew field labels
    citation = re.sub(r'^ציטוט:\s*"?', '', citation_raw).rstrip('"')
    date     = re.sub(r'^תאריך:\s*',  '', date_raw)
    source   = re.sub(r'^מקור:\s*',   '', source_raw)

    return {'section': section_num, 'citation': citation, 'date': date, 'source': source}


def parse_result_file(path: Path) -> list[dict]:
    findings = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        f = _parse_finding_line(line)
        if f:
            findings.append(f)
    return findings


def collect_findings(paths: list[Path]) -> dict[int, list[dict]]:
    """Return findings grouped by section, deduplicated across all files."""
    by_section: dict[int, list[dict]] = defaultdict(list)
    seen: dict[int, set[tuple]] = defaultdict(set)

    for path in paths:
        for f in parse_result_file(path):
            key = (f['citation'].strip(), f['date'].strip(), f['source'].strip())
            if key not in seen[f['section']]:
                seen[f['section']].add(key)
                by_section[f['section']].append(f)

    return by_section


def build_report(by_section: dict[int, list[dict]], source_files: list[Path]) -> str:
    titles = _parse_section_titles()
    run_time = datetime.datetime.now()

    lines = [
        SEP,
        'דוח ממצאים — תכתובות ווטסאפ יונתן-מיטל',
        SEP,
        f'תאריך ריצה : {run_time.strftime("%d.%m.%Y  %H:%M:%S")}',
        f'קבצים שנותחו: {", ".join(p.name for p in source_files)}',
        THIN,
        '',
    ]

    for n in range(1, 55):
        title = titles.get(n, '')
        lines.append(THIN)
        lines.append(f'סעיף {n}: {title}')
        lines.append(THIN)

        findings = by_section.get(n, [])
        if findings:
            for f in findings:
                lines.append(f'• ציטוט: "{f["citation"]}"  |  תאריך: {f["date"]}  |  מקור: {f["source"]}')
        else:
            lines.append('לא נמצאה עדות מפורשת בטקסט')
        lines.append('')

    return '\n'.join(lines)


def discover_result_files() -> list[Path]:
    files = sorted(analysis_utils.RESULTS_DIR.glob('pdf_*.txt')) + sorted(analysis_utils.RESULTS_DIR.glob('file_*.txt'))
    return [f for f in files if f.is_file()]


def main():
    parser = argparse.ArgumentParser(description='Phase 2: merge Phase 1 result files into final report (no LLM).')
    parser.add_argument('files', nargs='*', type=Path, help='Result files to merge (default: auto-discover)')
    parser.add_argument('--output', type=Path, default=None,
                        help='Output path (default: <project-dir>/final_report.txt)')
    parser.add_argument('--input-dir', default=None, metavar='DIR',
                        help='Folder containing input PDFs (used to resolve defaults).')
    parser.add_argument('--project-dir', default=None, metavar='DIR',
                        help='Folder for all outputs (default: <script-dir>/results).')
    args = parser.parse_args()

    analysis_utils.configure_paths(args.input_dir, args.project_dir)
    if args.output is None:
        args.output = analysis_utils.RESULTS_DIR / 'final_report.txt'

    if args.files:
        source_files = args.files
        missing = [f for f in source_files if not f.exists()]
        if missing:
            print(f'ERROR: File(s) not found: {", ".join(str(f) for f in missing)}', file=sys.stderr)
            sys.exit(1)
    else:
        source_files = discover_result_files()
        if not source_files:
            print(f'ERROR: No result files found in {analysis_utils.RESULTS_DIR}. Run python3 analyze_phase1.py first.')
            sys.exit(1)
        print(f'Auto-discovered {len(source_files)} result file(s): {", ".join(f.name for f in source_files)}')

    final_path: Path = args.output
    if final_path.exists():
        print(f'{final_path} already exists.')
        answer = input('Overwrite? [y/N] ').strip().lower()
        if answer != 'y':
            print('Aborted.')
            sys.exit(0)

    print(f'\n=== Phase 2: Merging {len(source_files)} result file(s) ===\n')
    for f in source_files:
        print(f'  Parsing {f.name} …')

    by_section = collect_findings(source_files)
    total = sum(len(v) for v in by_section.values())
    sections_with_findings = len(by_section)
    print(f'\n  {total} unique findings across {sections_with_findings}/54 sections')

    report = build_report(by_section, source_files)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(report, encoding='utf-8')

    print(f'\nFinal report saved to: {final_path}')
    print(f'Size: {len(report):,} chars')


if __name__ == '__main__':
    main()
