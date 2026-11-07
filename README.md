# goose-science-kit

A portable, self-contained bundle of **218 scientific skills** for Goose AI,
plus the flagship **`systematic-review-workflow`** (a PRISMA 2020 systematic
review pipeline). Designed to be copied to another PC, installed with one
command, verified with a hermetic self-test, and used identically — including
the ability to **independently rebuild any skill from its public GitHub source**
if something is missing.

---

## Table of Contents
1. [What's inside](#1-whats-inside)
2. [Prerequisites](#2-prerequisites)
3. [Install (one command)](#3-install-one-command)
4. [Configure API keys](#4-configure-api-keys)
5. [Use it](#5-use-it)
6. [Verify](#6-verify)
7. [Uninstall (clean rollback)](#7-uninstall-clean-rollback)
8. [Troubleshooting](#8-troubleshooting)
9. [Rebuilding skills from source (no kit needed)](#9-rebuilding-skills-from-source)
10. [Security notes](#10-security-notes)
11. [Layout](#11-layout)

---

## 1. What's inside

| Bundle | Count | Source |
|---|---|---|
| Google DeepMind science skills | 41 | [google-deepmind/science-skills](https://github.com/google-deepmind/science-skills) |
| K-Dense scientific-agent skills | 177 | [k-dense-ai/scientific-agent-skills](https://github.com/k-dense-ai/scientific-agent-skills) |
| `systematic-review-workflow` (flagship) | 1 | Built this session (custom) |
| **Total** | **218 named skills** | — |

Highlights:
- **Search & retrieval:** OpenAlex, PubMed, arXiv, bioRxiv, EuropePMC
- **Biology/structure:** AlphaFold, PDB, UniProt, Reactome, STRING, ClinVar, gnomAD, Ensembl, GTEx, ChEMBL, PubChem, OpenTargets
- **Analysis & writing (K-Dense):** statistical-analysis, statistical-power, scientific-writing, scientific-visualization, scientific-critical-thinking, exploratory-data-analysis, citation-management, peer-review, venue-templates
- **Flagship pipeline:** `systematic-review-workflow` — 12-command CLI, lab-grade audit trail, hermetic self-test

The flagship skill's CLI (`skills/systematic-review-workflow/scripts/srw_cli.py`)
is **pure Python standard library** (no third-party packages), so it runs on
Python 3.10+ with zero installs.

---

## 2. Prerequisites

| Requirement | Needed for | Install |
|---|---|---|
| **Goose AI** | running skills | your existing install |
| **Python 3.10+** | CLI self-test & skill scripts | `sudo apt install python3` / `brew install python` |
| **uv** *(recommended)* | running skill CLIs that declare dependencies | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **git** | only if rebuilding from source (Section 9) | preinstalled on most systems |
| **`@mermaid-js/mermaid-cli`** *(optional)* | rendering PRISMA PNG diagrams | `npm i -g @mermaid-js/mermaid-cli` |

> The workflow CLI itself needs **nothing** beyond `python3`. `uv` is only
> required for the *other* skills' CLIs when they declare Python dependencies.

---

## 3. Install (one command)

```bash
# 1. Copy goose-science-kit.tar.gz (or .zip) to the new PC, then extract:
tar -xzf goose-science-kit.tar.gz     # Linux/macOS
unzip goose-science-kit.zip           # Windows (or use the tar on WSL)

# 2. Install:
cd goose-science-kit
bash install.sh
```

`install.sh` will:
1. Check `python3` / `uv` are present.
2. Copy all 218 skills into `~/.config/goose/skills/` (**skips** any already present).
3. Seed `~/.env` with key placeholders (only if `~/.env` doesn't exist).
4. Record an installed-skill manifest (used by `uninstall.sh`).
5. Run the hermetic **self-test** (7 synthetic steps, no network, no keys) and
   print `✅ Installation complete` only if it passes.

Override the target if you use a non-default skills dir:
```bash
GOOSE_SKILLS_DIR=/path/to/skills bash install.sh
```

---

## 4. Configure API keys

```bash
# On the new PC (values are yours, never printed by any skill):
echo 'OPENALEX_API_KEY=<your_openalex_key>'  >> ~/.env
echo 'NCBI_API_KEY=<your_ncbi_key>'          >> ~/.env
chmod 600 ~/.env
```

| Key | Required for | Without it |
|---|---|---|
| `OPENALEX_API_KEY` | OpenAlex search + PDF download | Works on the shared IP budget (lower limits) |
| `NCBI_API_KEY` | PubMed E-utilities | Works at 3 req/s instead of 10 req/s |

Both keys are **optional** — the pipeline runs without them, just more slowly.
The `credentials` skill verifies presence **without printing** key values.

---

## 5. Use it

**Start a NEW goose session** so the freshly-installed skills are loaded into context.

Run the flagship workflow:
> Run the `systematic-review-workflow` skill for a PRISMA 2020 systematic
> review on **[topic]**, **PICO**, across **OpenAlex** and **PubMed**,
> output to `./reviews/<topic-slug>`.

Or use any individual skill by describing the task:
> - "Search OpenAlex for CRISPR off-target effects" → `literature-search-openalex`
> - "Fetch the AlphaFold structure of PDB 7XYZ" → `alphafold` + `pdb`
> - "Validate these DOIs and clean the BibTeX" → `citation-management`
> - "Run a power analysis for my RCT" → `statistical-power`
> - "Critique this methodology" → `scientific-critical-thinking`

The workflow writes a standardized, auditable tree:
```
./reviews/<topic-slug>/
├── protocol.json                    # PICO/PECO/… + inclusion/exclusion
├── audit_trail/lab_notebook.jsonl   # immutable, checksummed audit log
├── data/   raw .bib + deduped corpus
├── screening/  abstract + fulltext decisions
├── pdfs/   study_001.pdf … + SHA-256 manifest
├── synthesis/ evidence_matrix.csv + extracted_variables.json
└── report/  PRISMA flow (png+mermaid), draft.md/.tex, figures/, checklist
```

---

## 6. Verify

```bash
# Hermetic pipeline regression test (no network, no keys) — exit 0 = pass
python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py selftest

# One-page state + artifact checklist for a real review
python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py status  --output-dir ./reviews/<topic>

# Audit-ledger integrity (checksums + chronology) — detects tampering
python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py verify --output-dir ./reviews/<topic>
```

---

## 7. Uninstall (clean rollback)

Removes **only** the skills this kit installed (tracked via `.skill_manifest`),
leaving the rest of your goose setup intact. Idempotent.

```bash
cd goose-science-kit
bash uninstall.sh                          # interactive confirm
GOOSE_KIT_ASSUME_YES=1 bash uninstall.sh   # non-interactive (CI)
```

- Deletes **only** manifest-listed skill dirs — other goose skills are untouched.
- Removes the seeded `~/.env` **only** if both keys are still blank (no real keys added).
- Removes the skills dir itself if it becomes empty.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR: python3 not found` | Install Python 3.10+: `sudo apt install python3` or `brew install python` |
| `uv: NOT FOUND` warning | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` (only needed for skills with declared deps) |
| `Permission denied` writing skills | `GOOSE_SKILLS_DIR` points elsewhere, or add the dir to your user: `mkdir -p ~/.config/goose/skills` |
| Self-test **FAIL** on new PC | Run the failing step manually: `python3 .../srw_cli.py selftest`. Usually a Python version issue — check `python3 --version` (need 3.10+) |
| Skill "not found" in session | You're in an *old* session. **Start a new goose session** so new skills load |
| Rate-limit / 429 errors | Add `OPENALEX_API_KEY` / `NCBI_API_KEY` to `~/.env` (Section 4) |
| `PRISMA_flowchart.png` missing | Install mermaid-cli: `npm i -g @mermaid-js/mermaid-cli` (the `.mmd` source is always produced) |
| A skill is missing from the kit | Rebuild it from source (Section 9) — no one else's access needed |
| Windows: `bash: command not found` | Use WSL: `wsl bash install.sh`, or run `python3` steps directly |

**Check your environment quickly:**
```bash
python3 --version
which uv || echo "uv not installed"
ls ~/.config/goose/skills | wc -l          # expect ~218 after install
grep -c "API_KEY\|TOKEN" ~/.env 2>/dev/null   # count of key lines (values hidden)
```

---

## 9. Rebuilding skills from source

Everything in this kit comes from **public GitHub repos** (or is in this kit).
If any skill is missing, or a new instance needs to fetch a skill on its own,
it can do so independently — no special access required.

### 9a. Google DeepMind science skills (public)
```bash
git clone https://github.com/google-deepmind/science-skills /tmp/sci-skills
mkdir -p ~/.config/goose/skills
cp -r /tmp/sci-skills/skills/* ~/.config/goose/skills/
```

### 9b. K-Dense scientific-agent skills (public)
```bash
git clone https://github.com/k-dense-ai/scientific-agent-skills /tmp/kd-skills
cp -r /tmp/kd-skills/skills/* ~/.config/goose/skills/
```

### 9c. The flagship `systematic-review-workflow`
This is **custom and only lives in this kit** (it is not on a public repo).
Copy it directly from the kit — it is self-contained (SKILL.md + `scripts/srw_cli.py`):
```bash
cp -r goose-science-kit/skills/systematic-review-workflow ~/.config/goose/skills/
python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py selftest   # must PASS
```
> If you only have the tar/zip and not the unpacked folder:
> `tar -xzf goose-science-kit.tar.gz goose-science-kit/skills/systematic-review-workflow`
> then `cp -r goose-science-kit/skills/systematic-review-workflow ~/.config/goose/skills/`

### 9d. Verify a rebuilt skill
```bash
# Every skill must have a SKILL.md with name + description frontmatter:
head -5 ~/.config/goose/skills/<skill>/SKILL.md
# The flagship must self-test green:
python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py selftest
```

### 9e. No internet / no git?
The entire kit (all 218 skills + flagship + CLI) is already inside
`goose-science-kit/skills/`. You can install **offline** with `bash install.sh`
— it never touches the network. Section 9 is only needed to *add* a skill that
isn't in the kit.

---

## 10. Security notes

- **API keys are never printed** by any skill; the `credentials` skill checks
  presence without revealing values. Keep `~/.env` at `chmod 600`.
- **Audit trail is tamper-evident:** each `lab_notebook.jsonl` entry carries a
  SHA-256 checksum; `verify` recomputes all and flags any mismatch.
- **Self-test is hermetic:** no network, no keys, no writes outside a temp dir.
- The kit contains **no** credentials, no `node_modules`, no `__pycache__`.
- Only install from a kit you trust; you can audit every file before `install.sh`
  runs (`diff -r` against a known-good copy, or `tar -tzf` to list contents).

---

## 11. Layout

```
goose-science-kit/
├── install.sh            # one-command installer (+ self-test)
├── uninstall.sh          # selective, idempotent rollback
├── .skill_manifest       # 218 skill names (drives uninstall)
├── .env.example          # API key template
├── README.md             # this file
└── skills/               # 218 skill directories
    ├── systematic-review-workflow/
    │   ├── SKILL.md
    │   └── scripts/srw_cli.py      (12 commands, stdlib-only)
    ├── literature-search-openalex/
    ├── pubmed_database/
    ├── scientific-writing/
    ├── statistical-analysis/
    └── ... (214 more)
```

---

### Quick reference card

```bash
# Install            cd goose-science-kit && bash install.sh
# Uninstall          cd goose-science-kit && bash uninstall.sh
# Self-test          python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py selftest
# Review status      python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py status --output-dir ./reviews/<topic>
# Audit verify       python3 ~/.config/goose/skills/systematic-review-workflow/scripts/srw_cli.py verify --output-dir ./reviews/<topic>
# Add a key          echo 'OPENALEX_API_KEY=...' >> ~/.env && chmod 600 ~/.env
```
