---
name: systematic-review-workflow
description: >
  Executes an end-to-end, PRISMA 2020 and framework-guided (PICO, PECO, SPIDER, CIMO)
  literature selection, extraction, and synthesis pipeline with immutable lab notebook
  audit logs, finishing with a self-contained HTML web portal and a readable artifact
  viewer. Use when the user asks for a systematic review, meta-analysis, evidence
  synthesis, or structured literature search with screening and reporting.
---

# Systematic Review Workflow

## Overview

End-to-end PRISMA 2020 pipeline: protocol design → multi-database search →
abstract screening → full-text retrieval → full-text screening → evidence
extraction → PRISMA report and manuscript → **self-contained HTML web portal +
artifact viewer**. Every decision is logged to an immutable JSONL audit trail
so the review is fully reproducible.

## Dependencies

### Google Science Skills (search & retrieval)

| Skill | Used for |
|---|---|
| `literature-search-openalex` | Primary literature search, full-text PDF download |
| `pubmed_database` | PubMed / MEDLINE search, abstracts |
| `literature-search-arxiv` | arXiv search (preprints, CS/physics) |
| `literature-search-biorxiv` | bioRxiv search (life-science preprints) |
| `literature-search-europepmc` | EuropePMC search (additional coverage) |
| `credentials` | Safe handling of API keys |
| `uv` | Python environment for CLI script |

### K-Dense Scientific Agent Skills (analysis & output)

| Skill | Used for |
|---|---|
| `scientific-brainstorming` | Step 1 — refine research question, identify gaps, suggest sub-questions |
| `scientific-critical-thinking` | Steps 3 & 5 — critical appraisal of methodology during screening |
| `citation-management` | Step 2 & 7 — validate DOIs, generate clean BibTeX, verify citation accuracy |
| `parallel-web` | Step 2 — supplementary grey-literature search (conference talks, preprint servers) |
| `exploratory-data-analysis` | Step 6 — explore extracted evidence for patterns, outliers, distributions |
| `statistical-analysis` | Step 7 — meta-analysis, effect size estimation, forest plots (if ≥3 studies) |
| `statistical-power` | Step 7 — a-priori or post-hoc power analysis for the review |
| `scientific-writing` | Step 7 — structure and polish the manuscript draft |
| `scientific-visualization` | Step 7 — generate publication-quality figures (PRISMA flow, forest plots, heatmaps) |
| `markdown-mermaid-writing` | Step 7 — PRISMA flow diagram and supplementary diagrams |
| `peer-review` | Step 7 — internal quality check of the draft manuscript before delivery |
| `venue-templates` | Step 7 — format output for a specific journal or venue (if requested) |
| `scientific-slides` | Optional — generate presentation slides from the review |

## Quick Start

```bash
# 1. Initialise workspace
uv run scripts/srw_cli.py init \
  --topic "Photobiological Hydrogen Production" \
  --framework PICO \
  --databases openalex pubmed arxiv \
  --output-dir ./reviews/photobiological-h2

# 2. Build protocol (agent fills inclusion/exclusion criteria)
uv run scripts/srw_cli.py protocol \
  --output-dir ./reviews/photobiological-h2 \
  --inclusion "peer-reviewed" \
  --inclusion "published after 2015" \
  --exclusion "review articles" \
  --exclusion "animal-only studies"

# 3. Search (agent calls existing science skills, then feeds results)
uv run scripts/srw_cli.py search \
  --output-dir ./reviews/photobiological-h2 \
  --source openalex --query "photobiological hydrogen" \
  --source pubmed   --query "photobiological hydrogen production"

# 4. Abstract screening (agent makes decisions, CLI records them)
uv run scripts/srw_cli.py screen-abstract \
  --output-dir ./reviews/photobiological-h2 \
  --include 1,3,7,12 \
  --exclude 2,4,5,6,8,9,10,11

# 5. Fetch full-text PDFs
uv run scripts/srw_cli.py fetch-fulltext \
  --output-dir ./reviews/photobiological-h2

# 6. Full-text screening
uv run scripts/srw_cli.py screen-fulltext \
  --output-dir ./reviews/photobiological-h2 \
  --include 1,7,12 \
  --exclude 3 \
  --exclude-reason "3: animal-only model, not applicable"

# 7. Evidence extraction (agent fills matrix, CLI validates + writes)
uv run scripts/srw_cli.py extract \
  --output-dir ./reviews/photobiological-h2 \
  --matrix-rows 3 \
  --columns "author,year,method,organism,conditions,H2_yield,notes"

# 8. Generate PRISMA report
uv run scripts/srw_cli.py report \
  --output-dir ./reviews/photobiological-h2

# 9. Generate the self-contained HTML web portal + artifact viewer
#    (single-file portal + readable JSON/MD/CSV/JSONL viewer; works from
#     file:// and over HTTP/Caddy). Writes index.html + viewer.html.
uv run scripts/srw_cli.py portal \
  --output-dir ./reviews/photobiological-h2
```

## CLI Reference

All commands accept `--output-dir` (defaults to `./reviews/{topic_slug}`).
All commands write an immutable entry to `audit_trail/lab_notebook.jsonl`.

```
uv run scripts/srw_cli.py <command> [flags]
```

### `init`
Initialises the directory tree, writes `protocol.json` skeleton and
`audit_trail/lab_notebook.jsonl`.

| Flag | Required | Description |
|---|---|---|
| `--topic` | yes | Research subject |
| `--framework` | yes | `PICO`, `PECO`, `SPIDER`, or `CIMO` |
| `--databases` | no | Comma-separated list (default: `openalex,pubmed,arxiv`) |
| `--output-dir` | no | Override output directory |

### `protocol`
Populates `protocol.json` with inclusion/exclusion criteria and framework
dimensions. The **agent** should derive these from the user's research question
before calling this command.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--inclusion` | yes (repeatable) | Inclusion criterion string |
| `--exclusion` | yes (repeatable) | Exclusion criterion string |

### `search`
Records a search query and appends results to `data/raw_search_results.bib`
and `data/deduplicated_records.json`. The **agent** calls the relevant science
skill (OpenAlex, PubMed, arXiv, etc.) and writes the raw JSON to a temp file,
then passes it here via `--results-file`.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--source` | yes | `openalex`, `pubmed`, `arxiv`, `biorxiv`, `europepmc` |
| `--query` | yes | Boolean search string used |
| `--results-file` | no | Path to raw JSON results (agent writes this first) |

### `screen-abstract`
Records abstract-level inclusion/exclusion decisions.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--include` | yes | Comma-separated record indices to include |
| `--exclude` | no | Comma-separated record indices to exclude |

### `fetch-fulltext`
Fetches full-text PDFs for included records. For records with an OpenAlex
work ID, it downloads via `content.openalex.org` (requires
`OPENALEX_API_KEY`), falling back to the record's `pdf_url`. Records from
other sources (e.g. PubMed) can be fetched by the agent via
`literature-search-openalex → download-pdf` or a local PDF and then registered
with `register-pdf --record-index N --pdf-path /path/to.pdf`.
Writes SHA-256 hashes to `pdfs/manifest.json`.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--openalex-api-key` | no | Override key (else reads `OPENALEX_API_KEY` from env/`~/.env`) |

> **Drop-in fix:** `scripts/fetch-fulltext-goose-corrected.py` is a standalone
> replacement that fixes two bugs in the built-in `fetch-fulltext` — (1) the
> undefined `args.source` reference, and (2) missing `%PDF` magic-byte
> validation on downloaded files. Run it the same way when you hit either
> failure:
> `python3 scripts/fetch-fulltext-goose-corrected.py --output-dir ./reviews/<topic> --record-index 3`

### `register-pdf`
Registers an externally fetched PDF for a record, hashing it into the
manifest. Use when the agent fetched a PDF via a different skill or the user
provided one.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--record-index` | yes | Index of the record in `deduplicated_records.json` |
| `--pdf-path` | yes | Local path to the PDF |

### `screen-fulltext`
Records full-text-level inclusion/exclusion with mandatory rationales.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--include` | yes | Comma-separated record indices to include |
| `--exclude` | no | Comma-separated record indices to exclude |
| `--exclude-reason` | no | `index: reason` pairs (repeatable) |

### `extract`
Validates and writes the evidence matrix. Extraction is **performed by the
agent** (no BYOK model required): the agent defines the columns, reads each
included full-text, and writes the extracted values to a temp JSON file
(a list of lists, one row per included record, in record order). The CLI
validates the row count against full-text screening and writes
`synthesis/evidence_matrix.csv` and `synthesis/extracted_variables.json`.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--matrix-rows` | yes | Number of included records |
| `--columns` | yes | Comma-separated column headers |
| `--data-file` | no | Path to CSV data (agent writes this first) |

### `report`
Generates:
- `report/prisma_flow.mmd` — Mermaid PRISMA flow diagram
- `report/PRISMA_flowchart.png` — rendered PNG (requires `npx @mermaid-js/mermaid-cli`)
- `report/systematic_review_draft.md` — full markdown report
- `report/systematic_review_draft.tex` — LaTeX manuscript skeleton
- `report/prisma_checklist.md` — PRISMA 2027 checklist

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |

### `portal`
Generates the deliverable **web portal** (and the **artifact viewer** via a sibling
script). The portal generator (`scripts/gen_portal.py`) is **generic and defensive**:

- **Generic** — it is driven by `protocol.json` (topic, framework, criteria) and by
  the *column headers* of `synthesis/evidence_matrix.csv`, **not** hard-coded to any
  one review. Any review this skill produces renders the same way.
- **Defensive** — it never crashes on a partially-built workspace. **Every one of the
  8 sections is always rendered**; when a section's data source is missing or empty,
  that section shows a clear, styled **placeholder stating exactly why** (see the
  section contract below).
- **Self-contained** — zero external dependencies, pure inline-SVG charts, relative
  links. Works from `file://` (double-click) **and** over HTTP/Caddy with no build step.

Outputs (byte-identical copies):
- `index.html` — the 8-section portal (deliverable).
- `report/index.html` — a copy for `report/` subpath serving.
- `viewer.html` — readable renderer for the JSON/JSONL/MD/CSV/Tex/Mermaid artifacts
  (artifacts are **embedded** into the file, no `fetch`, so it works from `file://`).

**Portal section contract** (always rendered; placeholder explains absence)

| # | Section | Data source(s) | Placeholder if missing |
|---|---|---|---|
| 1 | Executive summary & key numbers | `protocol.json`, `data/deduplicated_records.json`, `screening/*`, `synthesis/evidence_matrix.csv`, `pdfs/manifest.json`, `audit_trail/*.jsonl` | Cards/ chips simply omit absent figures; an empty state is shown if nothing exists yet |
| 2 | Framework (PICO/PECO/…) & PRISMA 2020 | `protocol.json`, `screening/fulltext_exclusion_log.json`, `report/PRISMA_flowchart.svg` | Inclusion/exclusion list shows "(no … recorded yet)"; the flow figure shows a placeholder pointing at `report/prisma_flow.mmd` if the SVG/PNG isn't rendered yet |
| 3 | Statistical overview (distributions + correlations) | `synthesis/evidence_matrix.csv` | "No distributions: evidence matrix missing or empty — run the extract step" |
| 4 | Artifact explorer (directory tree) | existence of the files themselves | Lists only files that exist; a "pending" row names files not yet generated |
| 5 | Evidence matrix | `synthesis/evidence_matrix.csv` | "No evidence matrix: run the extract step" |
| 6 | PDF repository | `pdfs/manifest.json` (+ resolved PDF paths) | "No PDFs on disk: … fetches failed or no resolved paths … abstract-only records still included" |
| 7 | Included studies & full-text exclusions | `screening/fulltext_exclusion_log.json`, `data/deduplicated_records.json` | "No full-text screening decisions recorded yet" |
| 8 | Audit trail | `audit_trail/lab_notebook.jsonl` | "No audit ledger: this workspace has not run the pipeline yet" |

> **Contract rule:** the portal template is fixed and always produces all 8 sections.
> Missing data is *reported*, never silently dropped — the reader always sees the
> section and a clear, actionable reason for what it contains.

**Portal UI/UX design spec (frozen — do not change without explicit approval)**

| Aspect | Spec |
|---|---|
| **Theme** | Dark mode is the **default** (unchanged). A **Light mode** is available via a fixed top-right toggle button (`◐` / `☀` icon + "Light"/"Dark" label). Preference persists in `localStorage['srw-theme']`. Light theme overrides the same CSS custom properties (`--bg`, `--panel`, `--ink`, `--acc`, etc.) via `body.light{…}` — no separate stylesheet. |
| **Scrollbars** | All scrollable regions use **high-contrast scrollbars**: 14 px wide/tall, rounded thumb in `var(--th)` (teal `#0d9488` dark / `#0d9488` light), 3 px border matching `--panel2` for a floating-thumb look. Implemented via `scrollbar-color` (Firefox) **and** `::-webkit-scrollbar-thumb` (WebKit/Chrome). Applies globally via `*{scrollbar-color:…}`. |
| **Included / Excluded lists** | Both panels use **bullet points** (`<ul>`) with a **bold `#NNN` ID** prefix — no `<ol>` auto-numbering, no doubled "1. 1." pattern. Example: `• #5 Neurophysiological Vocal Source Modeling… — https://doi.org/…`. CSS: `.inc-list` and `.excl-list` share the same 13.5 px font, 7 px li margin, 18 px left padding. |
| **Feature-family chart** | "Dominant feature family" classifies each study into a **single dominant family** (max **5 classes**) — it takes the *first* (dominant) token of `acoustic_features`, folds synonyms onto a canonical set (prosody, pitch/F0, rhythm/temporal, spectral/MFCC, other-micro), and merges any tail beyond the top 4 into "Other". It does **not** count the verbatim feature string (which would yield 40+ near-duplicate bars). |
| **Headline stats (exec summary)** | Four headline chips computed **generically** from the evidence matrix: ① Mean AUC/ROC (outcome contains "auc"/"roc", normalised 0–1), ② Mean accuracy (outcome contains "acc" but not "auc", shown as %), ③ Dominant feature (top family from the classifier above, as `n/total`), ④ 2023+ studies (count of rows with year ≥ 2023). Each chip is **omitted** when the data isn't present, so the block degrades gracefully for any review. |
| **Sidenav** | Sticky left sidebar (220 px) with numbered section links; active section highlighted via `IntersectionObserver` scroll-spy. Collapses below 900 px viewport. |
| **Scroll panels** | Section 5 (matrix) and Section 7 (included/excluded) use full-height scroll panels: `.cols-tall{height:560px}` + `.scrollfill{flex:1;overflow:auto}` so the content scrolls independently while the section header and sidenav stay fixed. |
| **Self-contained** | Zero external deps (no CDN, no `fetch`, no build step). All CSS/JS inline. SVG charts hand-built. Works from `file://` and HTTP. |
| **DOI links** | Bare `10.xxx` strings are auto-prefixed to `https://doi.org/10.xxx` in all rendered output. |

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |
| `--template` | no | Override the viewer template path (defaults to the skill's `assets/viewer_template.html`) |

```bash
uv run scripts/srw_cli.py portal --output-dir ./reviews/{topic_slug}
```

> **To re-generate after any change to the underlying artifacts** (a new PDF, an
> updated matrix, a rendered PRISMA SVG), simply re-run `portal` (or run
> `scripts/gen_portal.py` with `SRW_OUTPUT_DIR` set to the review root). It is
> idempotent and cheap.

### `verify`
Re-verifies the audit ledger: recomputes every line's checksum, checks JSON
validity and chronological order. Exits non-zero and lists any mismatches.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |

```bash
uv run scripts/srw_cli.py verify --output-dir ./reviews/{topic_slug}
```

### `status`
One-page view of the review state: protocol, per-artifact checklist (✓/blank),
full-text PDF count, and current PRISMA counts.

| Flag | Required | Description |
|---|---|---|
| `--output-dir` | yes | Workspace directory |

```bash
uv run scripts/srw_cli.py status --output-dir ./reviews/{topic_slug}
```

### `selftest`
Runs a fully synthetic end-to-end review in a temp directory (no network, no
keys) and asserts: all 7 steps succeed, 7 lab-grade audit entries are written
with every required field, all key artifacts exist, and the audit ledger
verifies clean. Ideal as a CI gate.

| Flag | Required | Description |
|---|---|---|
| *(none)* | — | Self-contained; cleans up its temp dir |

```bash
uv run scripts/srw_cli.py selftest   # exit 0 = PASS, non-zero = FAIL
```

## Workflow (Agent Orchestration)

### Step 0 — Workspace Initialisation
1. Call `srw_cli.py init` with the user's topic, framework, and databases.
2. Verify directory tree was created.
3. Read `protocol.json` to confirm skeleton.

### Step 1 — Protocol Design
1. *(Optional, recommended for broad topics)* Load the **`scientific-brainstorming`**
   skill to refine the research question, surface sub-questions and known gaps,
   and pick the most appropriate framework (PICO / PECO / SPIDER / CIMO).
2. **Agent** derives the framework dimensions from the user's topic.
3. **Agent** formulates explicit inclusion and exclusion criteria.
4. Call `srw_cli.py protocol` to persist criteria into `protocol.json`.
5. Log the protocol version.

### Step 2 — Multi-Database Search
1. For each database in `protocol.json → databases`:
   - **Agent** formulates the boolean search string using the framework.
   - **Agent** calls the appropriate science skill:
     - `openalex` → `literature-search-openalex` skill
     - `pubmed` → `pubmed_database` skill
     - `arxiv` → `literature-search-arxiv` skill
     - `biorxiv` → `literature-search-biorxiv` skill
     - `europepmc` → `literature-search-europepmc` skill
   - **Agent** writes raw results JSON to a temp file.
   - Call `srw_cli.py search` to ingest, deduplicate, and log.
2. *(Optional)* For grey literature (conference talks, theses, technical reports),
   load the **`parallel-web`** skill and pass its results through the same
   `srw_cli.py search` ingest step.
3. After all databases: verify `data/deduplicated_records.json` exists.
4. *(Recommended)* Load the **`citation-management`** skill to validate DOIs
   and flag any malformed or unregistered identifiers before screening.

### Step 3 — Abstract Screening
1. Read `data/deduplicated_records.json` (use `jp` or Python to extract title + abstract).
2. **Agent** screens each record against inclusion/exclusion criteria. For
   borderline records, apply the **`scientific-critical-thinking`** skill to
   appraise methodological fit before deciding.
3. Call `srw_cli.py screen-abstract` with include/exclude indices.
4. Verify `screening/abstract_decisions.csv` was written.

### Step 4 — Full-Text Retrieval
1. Call `srw_cli.py fetch-fulltext` (uses OpenAlex `download-pdf`).
2. Verify `pdfs/manifest.json` contains SHA-256 hashes for all included records.
3. If PDFs are unavailable (paywalled), log to audit trail and continue with
   abstract-only records.

### Step 5 — Full-Text Screening
1. **Agent** reads full-text content (or abstract if no PDF) for each included record.
2. **Agent** applies secondary methodological filters — use the
   **`scientific-critical-thinking`** skill to appraise risk of bias, study
   design, and reporting quality, and to write defensible exclusion rationales.
3. Call `srw_cli.py screen-fulltext` with include/exclude indices and rationales.
4. Verify `screening/fulltext_exclusion_log.json` was written.

### Step 6 — Evidence Extraction (agent-performed)
> Extraction is performed **by the agent directly** (no external/BYOK model
> required). The agent reads each included full-text (or its abstract if no
> PDF) and pulls the data points into the matrix.

1. **Agent** defines extraction columns from the framework and topic
   (e.g. for PICO: Population, Intervention, Comparison, Outcome, Sample
   size, Study design; for CIMO: Context, Intervention, Mechanism, Outcome).
2. **Agent** reads each included full-text and extracts the values.
   - If no PDF is available, extract from the abstract and flag the row as
     `"abstract-only"`.
   - Where a value is not reported, leave it empty and note `"not reported"`.
   - Do **not** fabricate values; only record what the source states.
3. **Agent** writes the extracted rows to a temp JSON file (a list of lists,
   in record order).
4. Call `srw_cli.py extract --matrix-rows N --columns "..." --data-file rows.json`
   to validate row count and write `synthesis/evidence_matrix.csv` +
   `synthesis/extracted_variables.json`.
5. *(Recommended)* Load the **`exploratory-data-analysis`** skill to profile
   the matrix — distributions, missingness, outliers, and heterogeneity in the
   key outcome variable — and note observations that inform interpretation.
6. *(Optional, later)* If a BYOK extraction model (e.g. K-Dense) is available,
   the agent may offload per-record extraction to it — but the final matrix is
   always validated and written via the CLI.

### Step 7 — PRISMA Report Generation & Synthesis
1. Call `srw_cli.py report` to generate the PRISMA flow, counts, and draft
   markdown/LaTeX skeleton.
2. Verify all report artifacts exist.
3. *(If ≥3 comparable studies with quantitative outcomes)* Load the
   **`statistical-analysis`** skill to compute effect sizes and heterogeneity
   (I², Q), and the **`statistical-power`** skill for a power/sample-size
   assessment. Record the methods and results in the report.
4. *(Recommended)* Load the **`scientific-visualization`** skill to produce
   publication-quality figures (PRISMA flow, forest plot, evidence heatmap)
   into `report/figures/`.
5. *(Recommended)* Load the **`scientific-writing`** skill to expand
   `systematic_review_draft.md` into a full manuscript (Introduction, Methods,
   Results, Discussion, Limitations, Conclusions) and reconcile the narrative
   with the evidence matrix.
6. *(Optional)* Load the **`markdown-mermaid-writing`** skill to refine the
   PRISMA flow diagram and any supplementary process diagrams.
7. *(Optional, if a venue is specified)* Load the **`venue-templates`** skill to
   reformat the manuscript to the target journal's style.
8. *(Recommended — final quality gate)* Load the **`peer-review`** skill to run
   an internal claim–evidence and methodology critique on the draft, then
   address the findings.
9. Present the PRISMA flow counts and a summary of key findings to the user.

### Step 8 — Web Portal & Artifact Viewer (delivery)
1. Call `srw_cli.py portal` to generate the self-contained deliverables:
   - `index.html` (portal) — the 8-section portal (exec summary; framework & PRISMA
     flow; statistical overview; artifact explorer; evidence matrix; PDF repository;
     included/excluded; audit trail). Also copied to `report/index.html`.
   - `viewer.html` — readable renderer for the JSON / JSONL / Markdown / CSV / LaTeX /
     Mermaid artifacts (embedded, so no `fetch` needed).
2. Both files are zero-dependency and link to each other and to the PDFs via
   **relative paths**, so they work from `file://` (double-click) and over
   HTTP/Caddy with no build step.
3. **Graceful degradation:** the template always renders all 8 sections. Any section
   whose data source is missing/empty shows a clear placeholder explaining *why*
   (e.g. "No PDFs on disk: fetches were paywalled — abstract-only records are still
   included"; "No evidence matrix: run the extract step"). The reader always sees the
   section and an actionable reason — nothing is silently dropped.
4. Open `index.html` to confirm the evidence matrix, PDF cards, and audit trail render,
   and that the artifact-explorer links open each file readably in the viewer.
5. To re-generate after any change to the underlying artifacts (a new PDF, an updated
   matrix, a rendered PRISMA SVG), simply re-run `srw_cli.py portal` (or run
   `scripts/gen_portal.py` with `SRW_OUTPUT_DIR` set to the review root).

> **End-to-end (user prompt → self-contained HTML):** the agent runs steps 0–7 to build
> the workspace (protocol → search → screening → retrieval → extraction → PRISMA report),
> then step 8 (`portal`) turns that workspace into a single portable, self-contained
> `index.html`. Because the generator is driven entirely by the files the pipeline writes
> (at the exact paths in the Directory-Structure contract above), the same command works
> for *any* review topic — and an incomplete review still yields a complete, readable
> portal with honest placeholders for whatever is missing.

## Directory Structure & File-Naming Contract (per review)

> Default output root: `./reviews/{topic_slug}/` (e.g. `./reviews/photobiological-hydrogen-production/`).
> All sub-directories and the core files below are created by `srw_cli.py init` and the
> subsequent steps. **The portal template reads the files exactly at these paths — do not
> rename them or move them**, or the corresponding portal section will fall back to a
> placeholder (see the section contract under the `portal` command).

```
./reviews/{topic_slug}/
├── index.html                       # ★ Web portal (self-contained, Step 8) — DELIVERABLE
├── viewer.html                      # ★ Artifact viewer (embedded, Step 8) — DELIVERABLE
├── protocol.json                    # Locked topic, framework, dimensions & inclusion/exclusion criteria
├── audit_trail/
│   └── lab_notebook.jsonl           # Append-only chronological audit log (one JSON object per line)
├── data/
│   ├── raw_search_results.bib       # Combined raw exports from all databases
│   └── deduplicated_records.json    # {"records":[...]} — the cleaned, deduplicated corpus (canonical order = Study #)
├── screening/
│   ├── abstract_decisions.csv       # cols: index,title,year,source,doi,decision,reason
│   └── fulltext_exclusion_log.json  # screened/included(excluded) indices + rationale, fulltext_available, abstract_only
├── pdfs/
│   ├── study_001.pdf                # Full-text PDFs (sequential: study_NNN.pdf, NNN = record index)
│   ├── study_002.pdf
│   └── manifest.json                # [{record_index,title,doi,path,error,fetched_at}, ...]
├── fulltext/                        # (optional) extracted plain text study_NNN.txt, read for PDF-card digests
│   └── study_001.txt
├── synthesis/
│   ├── evidence_matrix.csv          # cols: source,doi,pmid,first_author,year,study_design,speech_task,acoustic_features,method,outcome,metric_value,mdd_primary,recorded_as
│   └── extracted_variables.json     # Granular parsed variables (interventions, metrics, outcomes)
└── report/
    ├── index.html                   # Copy of the portal (for report/ subpath serving)
    ├── prisma_flow.mmd              # Mermaid PRISMA flow source (for regeneration)
    ├── PRISMA_flowchart.svg         # PRISMA 2020 flow diagram (vector) — inlined into the portal
    ├── PRISMA_flowchart.png         # PRISMA 2020 flow diagram (raster fallback)
    ├── systematic_review_draft.md   # Final manuscript draft
    ├── systematic_review_draft.tex  # LaTeX version
    ├── prisma_checklist.md          # PRISMA checklist
    └── figures/                     # scientific-visualization outputs (forest plots, heatmaps, etc.)
```

**File-naming rules the template depends on**
- `deduplicated_records.json` → top-level `{"records": [...]}`; the list order defines the
  global **Study #** used across the matrix, PDF cards, and included list.
- `screening/fulltext_exclusion_log.json` → `included`/`excluded` are **lists of record
  indices** into `records`; matrix row *i* maps to `included[i]`.
- `pdfs/manifest.json` → an array; each entry's `path` may be absolute or relative to the
  review root *or* the skill root (the portal resolves both). `record_index` links a PDF
  to its Study #.
- `pdfs/study_NNN.pdf` → `NNN` == the record index (zero-padded to 3 digits).
- `synthesis/evidence_matrix.csv` → header names are read by the portal (it is
  column-agnostic), but the columns above are the canonical set.
- `report/PRISMA_flowchart.svg` (preferred) or `.png` (fallback) → inlined into both
  portal copies so the figure renders from `file://` and HTTP.
- `index.html` == `report/index.html` (byte-identical).

## Rate Limiting

- **OpenAlex**: 10 req/s keyless, 30 req/s with free key. The `openalex_cli.py`
  in the `literature-search-openalex` skill handles rate limiting.
- **PubMed E-utilities**: 3 req/s keyless, 10 req/s with `NCBI_API_KEY`.
  The `pubmed_api.py` in the `pubmed_database` skill handles rate limiting.
- **arXiv**: 1 req/s recommended. The `arxiv_cli.py` handles rate limiting.
- This skill's CLI does **not** make API calls directly — it delegates to the
  existing science skills, which enforce their own limits.

## K-Dense Skill Prerequisites

The K-Dense scientific-agent skills referenced above are mostly local/deterministic,
but a few need keys (already present in `~/.env` where applicable):

| Skill | Optional env var | Purpose |
|---|---|---|
| `citation-management` | `OPENALEX_API_KEY`, `NCBI_API_KEY` | Higher-rate metadata/DOI validation |
| `parallel-web` | `PARALLEL_API_KEY` | Grey-literature web search (optional) |
| `scientific-visualization` | — (local: matplotlib/seaborn) | Figures |
| `statistical-analysis` / `statistical-power` | — (local: numpy/scipy/statsmodels) | Effect sizes, power |
| `scientific-writing`, `peer-review`, `scientific-brainstorming`, `scientific-critical-thinking` | — (agent-driven) | Reasoning/writing |

If an optional key is missing, the skill falls back to a local or keyless path;
do **not** block the pipeline on a missing optional key.

## Audit Trail Protocol (Lab-Grade)

Every CLI command appends an **immutable JSONL entry** to
`audit_trail/lab_notebook.jsonl` following the lab notebook standard:

```json
{
  "timestamp": "2026-11-06T12:53:00Z",
  "step": "step_3_abstract_screening",
  "agent": "Goose-AI / K-Dense BYOK",
  "action": "Abstract screening: 42 included, 408 excluded",
  "metrics": { "records_screened": 450, "included": 42, "excluded": 408 },
  "status": "SUCCESS",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

**Field semantics**

| Field | Meaning |
|---|---|
| `timestamp` | UTC, second-precision, `Z` suffix — chronological order |
| `step` | Stable `step_N_name` id (see step map below) |
| `agent` | Execution agent identity (`Goose-AI / K-Dense BYOK`) |
| `action` | Human-readable summary of what happened |
| `metrics` | Structured, machine-readable counts/values for the step |
| `status` | `SUCCESS` \| `PARTIAL` (some sub-items failed) \| `FAILED` |
| `checksum` | SHA-256 of the canonical (sorted-key) JSON of the entry **without** the `checksum` field — tamper-evidence |

**Step id map**

| id | command |
|---|---|
| `step_0_workspace_init` | `init` |
| `step_1_protocol_setup` | `protocol` |
| `step_2_database_search` | `search` (one entry per database) |
| `step_3_abstract_screening` | `screen-abstract` |
| `step_4_fulltext_retrieval` | `fetch-fulltext` / `register-pdf` |
| `step_5_fulltext_screening` | `screen-fulltext` |
| `step_6_matrix_extraction` | `extract` |
| `step_7_prisma_report_generation` | `report` |
| `step_8_web_portal_generation` | `portal` |

**Immutability & integrity**
- The log is **append-only** — never rewrite or delete entries.
- `status` is set honestly: a fetch that partially fails is `PARTIAL`, a fully
  failed retrieval is `FAILED`. Failed steps are kept in the record (they are
  part of the reproducibility trail) — do not remove them.
- Verify integrity (and detect tampering or out-of-order writes) at any point:

```bash
uv run scripts/srw_cli.py verify --output-dir ./reviews/{topic_slug}
# → "Audit ledger intact: N entries, all checksums valid, chronology OK."
#   or lists the exact mismatching lines and exits non-zero.
```

This guarantees a complete, unalterable paper trail that satisfies journal
submission checklist requirements for systematic reviews.

## Quality Gates

Run these at any point to confirm the review is in a valid, reproducible state:

```bash
# 1. Is the pipeline internally consistent right now?
uv run scripts/srw_cli.py status --output-dir ./reviews/{topic_slug}

# 2. Is the audit trail untampered and in order?
uv run scripts/srw_cli.py verify --output-dir ./reviews/{topic_slug}

# 3. (CI / after any change to the skill) Does the whole pipeline still work?
uv run scripts/srw_cli.py selftest
```

`status` and `verify` are cheap and non-destructive — call them between steps
if unsure where the review stands. `selftest` is a hermetic regression test
that never touches network, keys, or the live review workspace.

## Common Mistakes

1. **Skipping deduplication.** Always call `search` for each database; the CLI
   deduplicates by DOI → PMID → title. Do NOT manually merge results.
2. **Screening without rationale.** Every exclusion (abstract or full-text)
   requires a reason string. The CLI enforces this for full-text screening.
3. **Filling the evidence matrix before full-text screening.** Extraction
   (step 6) must only include records that survived full-text screening (step 5).
4. **Ignoring the audit trail.** If a step fails, the audit log records the
   failure. Do NOT delete failed entries — they are part of the reproducibility
   record.
