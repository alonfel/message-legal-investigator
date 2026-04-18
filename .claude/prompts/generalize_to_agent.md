# Plan Prompt: Generalize to a Reusable Legal Analysis Agent

## What was built (current state)

A 4-phase AI pipeline that analyzes WhatsApp conversation PDFs for legal evidence:

- **Phase 1** (`analyze_phase1.py`): chunks documents → Claude extracts findings against 54 hardcoded Hebrew investigation items → cached per-file results
- **Phase 2** (`analyze_phase2.py`): pure-text deduplication → `results/final_report.txt` (no API)
- **Phase 3** (`validate_report.py`): citation verification + context enrichment from `extracted/*.txt` (no API)
- **Phase 4** (`build_narrative.py`): user picks sections + narrative angle → Claude synthesizes chronological timeline + legal argument brief

**Shared utilities:** `analysis_utils.py` — `call_claude_streaming()`, `RunMetrics`, `log()`, `extract_text()`, page-marker format `=== [filename | עמוד NN] ===`

**What is hardcoded and needs to change:**
- `INVESTIGATION_ITEMS` in `analysis_utils.py` — 54 Hebrew items, specific to one custody case
- `PDF_FILES` list — 7 specific filenames (already partially fixed with `--input-dir`)
- System prompts — Hebrew-only, WhatsApp-specific references
- Output field labels — `ציטוט`, `תאריך`, `מקור` (Hebrew)

---

## Goal: Two-phase generalization

### Phase A — Config-driven CLI (implement first, ~1-2 days)

Make the pipeline reusable for any legal case by externalizing everything case-specific into a config file. No architecture change — same 4 scripts, same flow.

**New file: `case.yaml`** (example schema)
```yaml
name: "Custody Case — Yonatan vs Mital"
language: hebrew               # hebrew | english | arabic | auto
document_format: whatsapp      # whatsapp | email | slack | generic

# --- System prompt configuration ---
# Option 1 (recommended): fill in the template fields below.
# config_loader.py assembles the full system prompt from these.
# Option 2 (advanced): set system_prompt_file to a .txt path to override entirely.

prompt:
  expert_role: "מומחה לניתוח תכתובות ווטסאפ"   # who Claude is
  legal_context: "חקירה משפטית בסכסוך משמורת"   # the legal domain/case type
  document_description: "תכתובות ווטסאפ בין שני הורים לאחר פרידה"  # what the docs are
  # system_prompt_file: "prompts/my_custom_system.txt"  # full override (optional)

# --- Investigation items ---
investigation_items:
  - id: 1
    text: "Evidence that Mital reduced rent unilaterally without dialogue"
  - id: 2
    text: "Evidence that Yonatan seeks fair resolution and Mital refuses"
  # ... up to N items, no hardcoded limit

# --- Output field labels (used in Phase 1 output and Phase 2/3 parsing) ---
output:
  citation_label: "ציטוט"
  date_label: "תאריך"
  source_label: "מקור"
  no_evidence_text: "לא נמצאה עדות מפורשת בטקסט"
```

**How `config_loader.py` builds the system prompt from template fields:**

```python
SYSTEM_PROMPT_TEMPLATE = """
אתה {expert_role} לצורך {legal_context}.

המסמכים שתנתח: {document_description}.

כללים:
1. התבסס אך ורק על הכתוב בטקסט. אל תמציא.
2. דווח רק על ממצאים בולטים וחד-משמעיים — לא על כל סעיף.
3. וודא שהציטוט משקף את כוונת הסעיף ולא נאמר בציניות או בהקשר הפוך.
4. היה תמציתי מאוד. פחות זה יותר.

פורמט לכל ממצא (שורה אחת):
סעיף N | {citation_label}: "טקסט מדויק" | {date_label}: DD.MM.YYYY HH:MM | {source_label}: קובץ עמוד NN
"""
```

If `system_prompt_file` is set in the config, that file is read and used as-is — giving power users full control without touching any code.

**English example** (`cases/employment_harassment.yaml`):
```yaml
name: "Employment Harassment Case — Smith vs Acme Corp"
language: english
document_format: email

prompt:
  expert_role: "expert in analyzing workplace communications"
  legal_context: "employment harassment legal investigation"
  document_description: "email threads between employees and management"

investigation_items:
  - id: 1
    text: "Evidence of hostile work environment directed at the plaintiff"
  - id: 2
    text: "Evidence management was aware of harassment and took no action"

output:
  citation_label: "Quote"
  date_label: "Date"
  source_label: "Source"
  no_evidence_text: "No explicit evidence found in text"
```

**Changes required:**
- `analysis_utils.py`: remove hardcoded `INVESTIGATION_ITEMS`, add `load_case_config(path)` that returns config dict; prompts built dynamically from config
- `analyze_phase1.py`: add `--case` flag pointing to `case.yaml`; pass items and language to prompt builder
- `analyze_phase2.py`: read labels from config for parsing/output
- `validate_report.py`: read labels from config for bullet parsing regex
- `build_narrative.py`: read language + labels from config; system prompt language-aware
- `estimate.py`: read items count from config

**New utility needed:** `config_loader.py` — loads and validates `case.yaml`, provides `get_system_prompt(config)`, `get_investigation_block(config)`, `get_output_labels(config)`

**Backward compatibility:** if `--case` is not passed, fall back to current hardcoded Hebrew defaults so existing workflows don't break.

---

### Phase B — Agentic skill (future, after Phase A is stable)

Rebuild as a proper Claude agent using the Anthropic API tool-use pattern. The agent drives the workflow instead of fixed scripts.

**Core insight:** Investigation items don't need to be predefined. The agent generates them from the user's legal goal.

**Agent tools to define:**
```python
extract_documents(paths: list[str]) -> str          # corpus with page markers
generate_investigation_items(goal: str, sample_text: str) -> list[Item]  # Claude generates checklist
find_evidence(items: list[Item], corpus: str) -> list[Finding]           # Phase 1 logic
verify_citations(findings: list[Finding], corpus: str) -> list[Finding]  # Phase 3 logic  
build_narrative(angle: str, findings: list[Finding], context_lines: int) -> str  # Phase 4 logic
```

**Agent entry point:** `legal_agent.py`
```bash
python3 legal_agent.py \
  --goal "Find evidence of financial abuse and parental alienation" \
  --input-dir /path/to/case/docs/ \
  --narrative "Pattern of financial control and parental exclusion"
```

**User interaction model:**
1. User provides: document folder + legal goal in plain language
2. Agent generates investigation checklist (user can review/edit)
3. Agent runs evidence extraction
4. Agent builds narrative — user can iterate on angle

**Skill packaging (Claude Code skill):**
Once stable, package as a `/legal-analyze` Claude Code skill that can be invoked from any project with a document corpus.

---

## Files to create / modify (Phase A)

| File | Action | Notes |
|---|---|---|
| `case.yaml` | **Create** | Default config for current custody case |
| `config_loader.py` | **Create** | Load/validate config, build prompts |
| `analysis_utils.py` | **Modify** | Remove hardcoded items, add config param |
| `analyze_phase1.py` | **Modify** | Add `--case` flag |
| `analyze_phase2.py` | **Modify** | Config-driven label parsing |
| `validate_report.py` | **Modify** | Config-driven bullet regex |
| `build_narrative.py` | **Modify** | Config-driven language + labels |
| `CLAUDE.md` | **Update** | Document config-driven usage |

## Files to create (Phase B)

| File | Action |
|---|---|
| `legal_agent.py` | New agentic entry point with tool use |
| `agent_tools.py` | Tool implementations (extract, find, verify, narrate) |

---

## Verification

**Phase A:**
```bash
# Existing case still works (backward compat)
python3 analyze_phase1.py -c 3 -p 9 0

# New config-driven run
python3 analyze_phase1.py --case case.yaml -c 3 -p 9 0

# English test case (create a small test corpus)
python3 analyze_phase1.py --case cases/test_english.yaml --file test_docs/sample.txt

# Full pipeline with config
python3 run_all.py --case case.yaml
python3 build_narrative.py --case case.yaml --sections 3,7 --narrative "..."
```

**Phase B:**
```bash
python3 legal_agent.py \
  --goal "Find evidence of harassment and financial abuse" \
  --input-dir test_docs/ \
  --narrative "Pattern of systematic harassment"
```

---

## Design constraints to keep in mind

- **Streaming is required** for Phase 1 chunk calls and Phase 4 narrative — output can be large
- **Hebrew is ~2 chars/token** — token estimates must be language-aware in `estimate.py`
- **Page markers** `=== [filename | page N] ===` are the source-tracing backbone — must be preserved regardless of language
- **Cache behavior** — Phase 1 caches by (filename, chunk_index). Config changes should invalidate cache or use separate cache dirs per case
- **`_parse_source()`** in `validate_report.py` regex is Hebrew-specific (`עמוד`) — must generalize to `page`/`עמוד`/`صفحة` etc. based on config language
