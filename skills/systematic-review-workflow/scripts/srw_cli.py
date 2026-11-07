"""Systematic Review Workflow CLI — PRISMA 2020 pipeline helper.

Orchestrates the mechanical parts of a systematic review: workspace
initialisation, protocol persistence, search-result ingestion and
deduplication, screening decision recording, full-text manifest
hashing, evidence-matrix validation, and PRISMA report generation.

Design: stdlib only (urllib for the optional OpenAlex PDF download),
argparse subcommands, file-first output, append-only JSONL audit log.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

FRAMEWORKS = {
    "PICO": ["Population/Patients", "Intervention/Exposure", "Comparison/Control", "Outcome"],
    "PECO": ["Population", "Exposure", "Control", "Outcome"],
    "SPIDER": ["Population/Participants", "Phenomenon of Interest", "Design", "Evaluation", "Research Type"],
    "CIMO": ["Context", "Intervention", "Mechanism", "Outcome"],
}

DB_ALIASES = {
    "openalex": "OpenAlex",
    "pubmed": "PubMed",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "europepmc": "EuropePMC",
}

VALID_SOURCES = ("openalex", "pubmed", "arxiv", "biorxiv", "europepmc")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
  return datetime.now(timezone.utc).astimezone().isoformat()


def topic_slug(topic: str) -> str:
  slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
  return slug or "review"


def workspace_paths(output_dir: str) -> dict:
  return {
      "root": output_dir,
      "protocol": os.path.join(output_dir, "protocol.json"),
      "audit": os.path.join(output_dir, "audit_trail", "lab_notebook.jsonl"),
      "raw_bib": os.path.join(output_dir, "data", "raw_search_results.bib"),
      "dedup": os.path.join(output_dir, "data", "deduplicated_records.json"),
      "abstract_csv": os.path.join(output_dir, "screening", "abstract_decisions.csv"),
      "fulltext_log": os.path.join(output_dir, "screening", "fulltext_exclusion_log.json"),
      "pdfs_dir": os.path.join(output_dir, "pdfs"),
      "pdf_manifest": os.path.join(output_dir, "pdfs", "manifest.json"),
      "matrix_csv": os.path.join(output_dir, "synthesis", "evidence_matrix.csv"),
      "variables_json": os.path.join(output_dir, "synthesis", "extracted_variables.json"),
      "report_dir": os.path.join(output_dir, "report"),
      "flow_mmd": os.path.join(output_dir, "report", "prisma_flow.mmd"),
      "flow_png": os.path.join(output_dir, "report", "PRISMA_flowchart.png"),
      "draft_md": os.path.join(output_dir, "report", "systematic_review_draft.md"),
      "draft_tex": os.path.join(output_dir, "report", "systematic_review_draft.tex"),
      "checklist_md": os.path.join(output_dir, "report", "prisma_checklist.md"),
  }


def ensure_workspace(output_dir: str) -> dict:
  """Validate that init has been run; return workspace paths."""
  p = workspace_paths(output_dir)
  if not os.path.exists(p["protocol"]):
    sys.exit(f"Error: {output_dir} is not initialised. Run `init` first.")
  return p


AGENT_ID = "Goose-AI / K-Dense BYOK"


def _utc_z() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_audit(p: dict, step: str, action: str = "",
                 status: str = "SUCCESS", agent: str = AGENT_ID, **metrics) -> None:
  """Append an immutable lab-grade JSONL entry.

  Entry schema (per lab notebook standard):
    {
      "timestamp": "2026-11-06T12:53:00Z",
      "step":      "step_3_abstract_screening",
      "agent":     "Goose-AI / K-Dense BYOK",
      "action":    "human-readable summary",
      "metrics":   { ...structured counts/values... },
      "status":    "SUCCESS" | "PARTIAL" | "FAILED",
      "checksum":  "sha256 of canonical(entry-minus-checksum)"
    }

  `checksum` is the SHA-256 of the canonical (sorted-key) JSON of the entry
  with the `checksum` field omitted, so any line can be independently
  re-verified for tamper-evidence via `srw_cli.py verify`.
  """
  entry = {
      "timestamp": _utc_z(),
      "step": step,
      "agent": agent,
      "action": action,
      "metrics": metrics,
      "status": status,
  }
  canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  entry["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
  os.makedirs(os.path.dirname(p["audit"]), exist_ok=True)
  with open(p["audit"], "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def verify_audit(p: dict) -> int:
  """Re-verify every ledger line: checksum integrity + chronological order.

  Returns 0 if the ledger is intact, non-zero otherwise.
  """
  if not os.path.exists(p["audit"]):
    print("No audit ledger found at:", p["audit"])
    return 0
  problems = []
  prev_ts = ""
  lines = [l for l in open(p["audit"], encoding="utf-8").read().splitlines() if l.strip()]
  for n, line in enumerate(lines, 1):
    try:
      entry = json.loads(line)
    except Exception as e:
      problems.append(f"line {n}: unparseable JSON ({e})")
      continue
    stored = entry.pop("checksum", None)
    canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    computed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if stored != computed:
      problems.append(f"line {n}: checksum MISMATCH (stored={stored} computed={computed})")
    ts = entry.get("timestamp", "")
    if ts and ts < prev_ts:
      problems.append(f"line {n}: timestamp {ts} is earlier than previous {prev_ts}")
    prev_ts = max(prev_ts, ts)
  if problems:
    print("AUDIT LEDGER INCONSISTENT — potential tampering or corruption:")
    for pr in problems:
      print("  -", pr)
    return 1
  print(f"Audit ledger intact: {len(lines)} entries, all checksums valid, chronology OK.")
  return 0


def read_json(path: str) -> dict:
  with open(path, "r", encoding="utf-8") as f:
    return json.load(f)


def write_json(path: str, obj) -> None:
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    json.dump(obj, f, indent=2, ensure_ascii=False)
    f.write("\n")


def parse_indices(flag: str) -> list:
  if not flag:
    return []
  out = []
  for tok in flag.split(","):
    tok = tok.strip()
    if not tok:
      continue
    if not tok.isdigit():
      sys.exit(f"Error: invalid record index '{tok}' (expected integer)")
    out.append(int(tok))
  return out


def record_key(rec: dict) -> str:
  """Dedup priority: OpenAlex id, DOI, PMID, then normalised title."""
  for field in ("openalex_id", "doi", "pmid"):
    v = rec.get(field)
    if v:
      return f"{field}:{str(v).strip().lower()}"
  title = str(rec.get("title", "")).lower()
  return "title:" + re.sub(r"[^a-z0-9]+", "", title)


def load_env_key(name: str) -> str:
  if os.environ.get(name):
    return os.environ[name]
  env_file = os.path.expanduser("~/.env")
  if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
      for line in f:
        line = line.strip()
        if line.startswith(name + "="):
          return line.split("=", 1)[1].strip()
  return ""


def download(url: str, out_path: str, timeout: int = 60) -> None:
  req = urllib.request.Request(url, headers={"User-Agent": "systematic-review-workflow/1.0"})
  with urllib.request.urlopen(req, timeout=timeout) as resp:
    data = resp.read()
  if not data:
    raise RuntimeError(f"Empty response from {url}")
  with open(out_path, "wb") as f:
    f.write(data)


def sha256_of(path: str) -> str:
  h = hashlib.sha256()
  with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(65536), b""):
      h.update(chunk)
  return h.hexdigest()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args) -> None:
  if args.framework not in FRAMEWORKS:
    sys.exit(f"Error: framework must be one of {sorted(FRAMEWORKS)}")
  databases = [d.strip().lower() for d in (args.databases or "openalex,pubmed,arxiv").split(",")]
  for d in databases:
    if d not in DB_ALIASES:
      sys.exit(f"Error: unknown database '{d}'. Known: {sorted(DB_ALIASES)}")

  output_dir = args.output_dir or os.path.join("reviews", topic_slug(args.topic))
  p = workspace_paths(output_dir)
  for sub in ("audit_trail", "data", "screening", "pdfs", "synthesis", "report"):
    os.makedirs(os.path.join(output_dir, sub), exist_ok=True)

  protocol = {
      "version": 1,
      "created_at": now_iso(),
      "topic": args.topic,
      "framework": args.framework.upper(),
      "framework_dimensions": FRAMEWORKS[args.framework.upper()],
      "databases": [DB_ALIASES[d] for d in databases],
      "inclusion_criteria": [],
      "exclusion_criteria": [],
      "status": "initialised",
  }
  write_json(p["protocol"], protocol)
  append_audit(p, "step_0_workspace_init",
               f"Initialised workspace for topic '{args.topic}' "
               f"(framework={args.framework.upper()}, databases={protocol['databases']})",
               topic=args.topic, framework=args.framework.upper(),
               databases=protocol["databases"], output_dir=output_dir)
  print(f"Success! Workspace initialised at: {output_dir}")


def cmd_protocol(args) -> None:
  p = ensure_workspace(args.output_dir)
  if not args.inclusion:
    sys.exit("Error: provide at least one --inclusion criterion")
  protocol = read_json(p["protocol"])
  protocol["inclusion_criteria"] = args.inclusion
  protocol["exclusion_criteria"] = args.exclusion or []
  protocol["protocol_updated_at"] = now_iso()
  protocol["status"] = "protocol_set"
  write_json(p["protocol"], protocol)
  append_audit(p, "step_1_protocol_setup", "Inclusion/exclusion criteria persisted",
               framework=protocol.get("framework"),
               inclusion_count=len(args.inclusion),
               exclusion_count=len(protocol["exclusion_criteria"]),
               inclusion=args.inclusion, exclusion=protocol["exclusion_criteria"])
  print(f"Success! Protocol updated: {p['protocol']}")


def cmd_search(args) -> None:
  p = ensure_workspace(args.output_dir)
  if args.source not in VALID_SOURCES:
    sys.exit(f"Error: source must be one of {VALID_SOURCES}")

  records = []
  if args.results_file:
    if not os.path.exists(args.results_file):
      sys.exit(f"Error: --results-file not found: {args.results_file}")
    with open(args.results_file, "r", encoding="utf-8") as f:
      raw = json.load(f)
    if isinstance(raw, dict):
      raw = raw.get("results") or raw.get("records") or raw.get("works") or raw.get("esearchresult", {}).get("idlist", [])
    for item in raw or []:
      if isinstance(item, dict):
        records.append({
            "source": args.source,
            "openalex_id": item.get("id") if args.source == "openalex" else None,
            "doi": (item.get("doi") or (item.get("primary_location") or {}).get("doi")
                    if args.source == "openalex" else item.get("doi")),
            "pmid": item.get("pmid") or (item.get("ids") or {}).get("pmid"),
            "title": item.get("title") or item.get("display_name") or item.get("title", ""),
            "abstract": item.get("abstract") or item.get("abstract_inverted_index") is not None
                        and json.dumps(item["abstract_inverted_index"]) or item.get("abstract") or "",
            "year": item.get("publication_year") or item.get("year") or (item.get("published_date") or "")[:4] or None,
            "authors": [a.get("display_name", a) if isinstance(a, dict) else a
                        for a in (item.get("authorships") or [] or item.get("authors") or [])][:20],
            "pdf_url": (item.get("open_access") or {}).get("oa_url") or item.get("pdf_url"),
            "citation_count": item.get("cited_by_count"),
            "raw": item,
        })
      else:
        records.append({"source": args.source, "pmid": str(item), "title": str(item)})
  if not records:
    sys.exit("Error: no records found in --results-file (or none provided). "
             "Run the search with the relevant science skill, save the raw JSON, "
             "and pass it via --results-file.")

  # Deduplicate against existing records
  dedup_path = p["dedup"]
  existing = read_json(dedup_path) if os.path.exists(dedup_path) else {"searches": [], "records": []}
  seen = {record_key(r) for r in existing["records"]}
  added = 0
  for r in records:
    key = record_key(r)
    if key in seen:
      continue
    seen.add(key)
    existing["records"].append(r)
    added += 1

  existing["searches"].append({
      "timestamp": now_iso(),
      "source": DB_ALIASES[args.source],
      "query": args.query,
      "raw_hits": len(records),
      "new_after_dedup": added,
      "total_records": len(existing["records"]),
  })
  write_json(dedup_path, existing)

  # Append BibTeX-ish entries to raw_search_results.bib
  with open(p["raw_bib"], "a", encoding="utf-8") as f:
    f.write(f"\n% {now_iso()} | source={DB_ALIASES[args.source]} | query={args.query} | hits={len(records)}\n")
    for r in records[:500]:
      doi = r.get("doi")
      if doi:
        f.write(f"@article{{srw_{abs(hash(doi)) % 10**10}, doi={doi}, title={r.get('title','')}, year={r.get('year','')}, source={DB_ALIASES[args.source]}}}\n")

  append_audit(p, "step_2_database_search",
               f"Search on {DB_ALIASES[args.source]}: {args.query!r} "
               f"({len(records)} raw hits, {added} new after dedup)",
               database=DB_ALIASES[args.source], query=args.query,
               raw_hits=len(records), new_after_dedup=added,
               total_records=len(existing["records"]))
  print(f"Success! {len(records)} hits ingested ({added} new), "
        f"{len(existing['records'])} total records in: {dedup_path}")


def cmd_screen_abstract(args) -> None:
  p = ensure_workspace(args.output_dir)
  data = read_json(p["dedup"])
  records = data["records"]
  include = set(parse_indices(args.include))
  exclude = set(parse_indices(args.exclude))
  if not include:
    sys.exit("Error: provide at least one --include index")
  unknown = (include | exclude) - set(range(len(records)))
  if unknown:
    sys.exit(f"Error: indices out of range (0..{len(records)-1}): {sorted(unknown)}")

  rows = []
  for i, rec in enumerate(records):
    if i in include:
      decision, reason = "included", ""
    elif i in exclude:
      decision, reason = "excluded", args.exclude_reason or "screened out at abstract stage"
    else:
      decision, reason = "unscreened", ""
    rows.append({
        "index": i,
        "title": rec.get("title", ""),
        "year": rec.get("year"),
        "source": rec.get("source"),
        "doi": rec.get("doi"),
        "decision": decision,
        "reason": reason,
    })

  with open(p["abstract_csv"], "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["index", "title", "year", "source", "doi", "decision", "reason"])
    w.writeheader()
    w.writerows(rows)

  append_audit(p, "step_3_abstract_screening",
               f"Abstract screening: {len(include)} included, {len(exclude)} excluded",
               records_screened=len(records),
               included=len(include), excluded=len(exclude),
               included_indices=sorted(include), excluded_indices=sorted(exclude))
  print(f"Success! Abstract decisions written to: {p['abstract_csv']}")


def _included_indices(p: dict) -> list:
  with open(p["abstract_csv"], newline="", encoding="utf-8") as f:
    return [int(r["index"]) for r in csv.DictReader(f) if r["decision"] == "included"]


def cmd_fetch_fulltext(args) -> None:
  p = ensure_workspace(args.output_dir)
  data = read_json(p["dedup"])
  records = data["records"]
  included = _included_indices(p)

  key = args.openalex_api_key or load_env_key("OPENALEX_API_KEY")
  manifest = read_json(p["pdf_manifest"]) if os.path.exists(p["pdf_manifest"]) else []
  have = {m["record_index"] for m in manifest}

  fetched, failed = 0, 0
  for i in included:
    if i in have:
      continue
    rec = records[i]
    url = rec.get("pdf_url")
    if args.source == "openalex" and rec.get("openalex_id") and key:
      url = f"https://content.openalex.org/{rec['openalex_id']}.pdf"
    if not url:
      failed += 1
      continue
    seq = included.index(i) + 1
    safe_name = "study_%03d" % seq
    out_path = os.path.join(p["pdfs_dir"], f"{safe_name}.pdf")
    try:
      download(url, out_path)
      manifest.append({
          "record_index": i,
          "title": rec.get("title"),
          "doi": rec.get("doi"),
          "path": out_path,
          "sha256": sha256_of(out_path),
          "fetched_at": now_iso(),
      })
      fetched += 1
    except Exception as e:
      failed += 1
      print(f"Warning: failed to fetch PDF for record {i}: {e}", file=sys.stderr)
      manifest.append({
          "record_index": i,
          "title": rec.get("title"),
          "doi": rec.get("doi"),
          "path": None,
          "error": str(e),
          "fetched_at": now_iso(),
      })

  write_json(p["pdf_manifest"], manifest)
  status = "SUCCESS" if failed == 0 else ("PARTIAL" if fetched > 0 else "FAILED")
  append_audit(p, "step_4_fulltext_retrieval",
               f"Full-text retrieval: {fetched} fetched, {failed} failed/skipped",
               status=status, requested=len(included), fetched=fetched, failed=failed)
  if status != "SUCCESS":
    print(f"Success! {fetched} PDFs fetched ({failed} failed/skipped). Manifest: {p['pdf_manifest']}")
  else:
    print(f"Success! {fetched} PDFs fetched. Manifest: {p['pdf_manifest']}")


def cmd_register_pdf(args) -> None:
  p = ensure_workspace(args.output_dir)
  if not os.path.exists(args.pdf_path):
    sys.exit(f"Error: PDF not found: {args.pdf_path}")
  data = read_json(p["dedup"])
  if args.record_index >= len(data["records"]):
    sys.exit(f"Error: record index {args.record_index} out of range")
  rec = data["records"][args.record_index]

  seq = args.record_index + 1
  safe_name = "study_%03d" % seq
  dest = os.path.join(p["pdfs_dir"], f"{safe_name}.pdf")
  if os.path.abspath(args.pdf_path) != os.path.abspath(dest):
    import shutil
    shutil.copyfile(args.pdf_path, dest)

  manifest = read_json(p["pdf_manifest"]) if os.path.exists(p["pdf_manifest"]) else []
  manifest = [m for m in manifest if m["record_index"] != args.record_index]
  manifest.append({
      "record_index": args.record_index,
      "title": rec.get("title"),
      "doi": rec.get("doi"),
      "path": dest,
      "sha256": sha256_of(dest),
      "fetched_at": now_iso(),
  })
  write_json(p["pdf_manifest"], manifest)
  append_audit(p, "step_4_fulltext_retrieval",
               f"Registered external PDF for record {args.record_index}: {os.path.basename(args.pdf_path)}",
               record_index=args.record_index,
               title=rec.get("title"), doi=rec.get("doi"),
               sha256=sha256_of(dest))
  print(f"Success! PDF registered and hashed: {dest}")


def cmd_screen_fulltext(args) -> None:
  p = ensure_workspace(args.output_dir)
  data = read_json(p["dedup"])
  records = data["records"]
  abstract_included = _included_indices(p)
  include = set(parse_indices(args.include))
  exclude = set(parse_indices(args.exclude))
  if not include:
    sys.exit("Error: provide at least one --include index")
  if not (include | exclude) <= set(abstract_included):
    bad = sorted((include | exclude) - set(abstract_included))
    sys.exit(f"Error: indices not included at abstract stage (cannot be fulltext-screened): {bad}")
  if unknown := ((include | exclude) - set(range(len(records)))):
    sys.exit(f"Error: indices out of range: {sorted(unknown)}")

  reasons = {}
  for pair in args.exclude_reason or []:
    if ":" not in pair:
      sys.exit(f"Error: --exclude-reason must be 'index: reason', got {pair!r}")
    idx, why = pair.split(":", 1)
    reasons[int(idx.strip())] = why.strip()
  missing = exclude - set(reasons)
  if missing:
    sys.exit(f"Error: exclusions missing rationales: {sorted(missing)} "
             "(provide 'index: reason' pairs)")

  log = {
      "timestamp": now_iso(),
      "screened": sorted(abstract_included),
      "included": sorted(include),
      "excluded": [
          {"index": i, "title": records[i].get("title"), "reason": reasons[i]}
          for i in sorted(exclude)
      ],
  }
  write_json(p["fulltext_log"], log)
  append_audit(p, "step_5_fulltext_screening",
               f"Full-text screening: {len(include)} included, {len(exclude)} excluded with rationale",
               fulltext_screened=len(abstract_included),
               included=len(include), excluded=len(exclude),
               included_indices=sorted(include), excluded_indices=sorted(exclude))
  print(f"Success! Full-text decisions written to: {p['fulltext_log']}")


def cmd_extract(args) -> None:
  p = ensure_workspace(args.output_dir)
  if not os.path.exists(p["fulltext_log"]):
    sys.exit("Error: full-text screening has not been completed. Run `screen-fulltext` first.")
  log = read_json(p["fulltext_log"])
  included = log["included"]
  data = read_json(p["dedup"])
  records = [data["records"][i] for i in included]

  if args.matrix_rows != len(records):
    sys.exit(f"Error: --matrix-rows {args.matrix_rows} does not match "
             f"the {len(records)} included records from full-text screening.")

  columns = [c.strip() for c in args.columns.split(",") if c.strip()]

  # Agent-provided data: list of lists/objects matching columns
  rows = []
  if args.data_file:
    if not os.path.exists(args.data_file):
      sys.exit(f"Error: --data-file not found: {args.data_file}")
    with open(args.data_file, "r", encoding="utf-8") as f:
      content = f.read().strip()
    if content.startswith("[") or content.startswith("{"):
      rows = json.loads(content)
    else:
      rows = list(csv.reader(filter(None, content.splitlines())))
  else:
    rows = [[] for _ in records]

  if len(rows) != len(records):
    sys.exit(f"Error: data file has {len(rows)} rows, expected {len(records)}.")

  with open(p["matrix_csv"], "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["record_index", "doi", "title"] + columns)
    for idx, rec, row in zip(included, records, rows):
      row = list(row) + [None] * (len(columns) - len(row))
      w.writerow([idx, rec.get("doi"), rec.get("title")] + row[:len(columns)])

  variables = {
      "generated_at": now_iso(),
      "columns": columns,
      "n_records": len(records),
      "records": [
          {"index": idx, "doi": rec.get("doi"), "title": rec.get("title"),
           "extracted": dict(zip(columns, (list(r) + [None] * len(columns))[:len(columns)]))}
          for idx, rec, r in zip(included, records, rows)
      ],
  }
  write_json(p["variables_json"], variables)
  append_audit(p, "step_6_matrix_extraction",
               f"Evidence extraction: {len(records)} records × {len(columns)} columns",
               records=len(records), columns=len(columns),
               column_names=columns)
  print(f"Success! Evidence matrix written to: {p['matrix_csv']}")


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _prisma_counts(p: dict) -> dict:
  data = read_json(p["dedup"])
  searches = data.get("searches", [])
  identified = sum(s.get("raw_hits", 0) for s in searches)
  unique = len(data.get("records", []))
  with open(p["abstract_csv"], newline="", encoding="utf-8") as f:
    abstract_rows = list(csv.DictReader(f))
  screened = len(abstract_rows)
  abstract_included = [r for r in abstract_rows if r["decision"] == "included"]
  abstract_excluded = len([r for r in abstract_rows if r["decision"] == "excluded"])
  fulltext_log = read_json(p["fulltext_log"]) if os.path.exists(p["fulltext_log"]) else None
  if fulltext_log:
    ft_included = len(fulltext_log["included"])
    ft_excluded = len(fulltext_log["excluded"])
    ft_screened = len(fulltext_log.get("screened", []))
  else:
    ft_included = ft_excluded = ft_screened = 0
  return {
      "identified": identified,
      "after_dedup": unique,
      "screened_abstract": screened,
      "abstract_included": len(abstract_included),
      "abstract_excluded": abstract_excluded,
      "fulltext_screened": ft_screened,
      "fulltext_included": ft_included,
      "fulltext_excluded": ft_excluded,
      "searches": searches,
  }


def _write_prisma_flow(p: dict, c: dict) -> None:
  lines = [
      "flowchart TD",
      '  A["Records identified<br/>(n=' + str(c["identified"]) + ')"]',
      '  B["After duplicate removal<br/>(n=' + str(c["after_dedup"]) + ')"]',
      '  C["Screened at title/abstract<br/>(n=' + str(c["screened_abstract"]) + ')"]',
      '  D["Excluded at abstract<br/>(n=' + str(c["abstract_excluded"]) + ')"]',
      '  E["Full-text screened<br/>(n=' + str(c["fulltext_screened"]) + ')"]',
      '  F["Excluded at full-text<br/>(n=' + str(c["fulltext_excluded"]) + '")"]',
      '  G[\"Included in review<br/>(n=' + str(c["fulltext_included"]) + ')"]',
      "  A --> B --> C",
      "  C --> D",
      "  C --> E",
      "  E --> F",
      "  E --> G",
  ]
  with open(p["flow_mmd"], "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")


def _write_report(p: dict, c: dict) -> None:
  protocol = read_json(p["protocol"])
  fulltext_log = read_json(p["fulltext_log"]) if os.path.exists(p["fulltext_log"]) else None
  data = read_json(p["dedup"])

  searches_md = "\n".join(
      f"| {s['source']} | `{s['query']}` | {s['raw_hits']} | {s['new_after_dedup']} |"
      for s in c["searches"]
  )

  exclusions_md = ""
  if fulltext_log:
    exclusions_md = "\n".join(
        f"- **{e['index']}** — {e['title']}: {e['reason']}"
        for e in fulltext_log.get("excluded", [])
    ) or "- None."

  matrix_md = ""
  if os.path.exists(p["matrix_csv"]):
    with open(p["matrix_csv"], newline="", encoding="utf-8") as f:
      rows = list(csv.reader(f))
    if rows:
      header = rows[0]
      body = rows[1:]
      matrix_md = (
          "| " + " | ".join(h for h in header) + " |\n"
          + "|" + "---|" * len(header) + "\n"
          + "\n".join("| " + " | ".join(r + [""] * (len(header) - len(r))) + " |" for r in body)
      )

  included_titles = "\n".join(
      f"{i+1}. {data['records'][idx].get('title', 'Unknown')}"
      f"{' — ' + data['records'][idx].get('doi') if data['records'][idx].get('doi') else ''}"
      for i, idx in enumerate(fulltext_log["included"]) if fulltext_log
  ) or "_No records yet (complete full-text screening first)._"

  md = f"""# Systematic Review: {protocol['topic']}

*Generated {now_iso()} by the `systematic-review-workflow` skill (PRISMA 2020).*

## 1. Protocol

- **Topic:** {protocol['topic']}
- **Framework:** {protocol['framework']}
  - {chr(10).join('  - ' + d for d in protocol.get('framework_dimensions', []))}
- **Databases:** {', '.join(protocol.get('databases', []))}
- **Inclusion criteria:**
{chr(10).join('  - ' + c for c in protocol.get('inclusion_criteria', [])) or '  - _not set_'}
- **Exclusion criteria:**
{chr(10).join('  - ' + c for c in protocol.get('exclusion_criteria', [])) or '  - _not set_'}

## 2. Search

| Database | Query | Raw hits | New after dedup |
|---|---|---|---|
{searches_md or '| — | — | 0 | 0 |'}

## 3. PRISMA 2020 Flow

| Stage | Count |
|---|---|
| Records identified | {c['identified']} |
| After duplicate removal | {c['after_dedup']} |
| Screened at title/abstract | {c['screened_abstract']} |
| Excluded at abstract | {c['abstract_excluded']} |
| Full-text screened | {c['fulltext_screened']} |
| Excluded at full-text (with rationale) | {c['fulltext_excluded']} |
| **Included in review** | **{c['fulltext_included']}** |

## 4. Full-text Exclusions

{exclusions_md}

## 5. Included Studies

{included_titles}

## 6. Evidence Matrix

{matrix_md or '_No extraction yet (run `extract`)._'}

## 7. Audit Trail

See `audit_trail/lab_notebook.jsonl` for the complete, append-only decision log.
PRISMA flow diagram source: `report/prisma_flow.mmd`.
"""
  with open(p["draft_md"], "w", encoding="utf-8") as f:
    f.write(md)

  # LaTeX skeleton
  included_items = "\n".join("\\item " + t.replace("&", "\\&") for t in included_titles.split("\n") if t.strip() and not t.startswith("_"))
  topic_tex = protocol["topic"].replace("&", "\\&")
  tex = f"""\\documentclass{{article}}
\\usepackage[utf8]{{inputenc}}
\\title{{Systematic Review: {topic_tex} (PRISMA 2020)}}
\\author{{Generated by systematic-review-workflow, {now_iso()[:10]}}}
\\begin{{document}}
\\maketitle
\\section{{Protocol}}
Framework: {protocol['framework']}. Databases: {', '.join(protocol.get('databases', []))}.

\\section{{PRISMA 2020 Flow}}
\\begin{{itemize}}
\\item Records identified: {c['identified']}
\\item After duplicate removal: {c['after_dedup']}
\\item Screened at title/abstract: {c['screened_abstract']}
\\item Excluded at abstract: {c['abstract_excluded']}
\\item Full-text screened: {c['fulltext_screened']}
\\item Excluded at full-text: {c['fulltext_excluded']}
\\item Included in review: {c['fulltext_included']}
\\end{{itemize}}

\\section{{Included Studies}}
{included_items}

\\section{{Extraction}}
See \\texttt{{synthesis/evidence\\_matrix.csv}} for the complete evidence matrix.

\\end{{document}}
"""
  with open(p["draft_tex"], "w", encoding="utf-8") as f:
    f.write(tex)

  checklist = """# PRISMA 2020 Checklist

| # | Item | Status |
|---|---|---|
| 1 | Title identifies study as systematic review | ☐ |
| 2 | Structured summary (background, objective, methods, results, conclusion) | ☐ |
| 3 | Rationale explained in introduction | ☐ |
| 4 | Objectives explicitly specified | ☐ |
| 5 | Eligibility criteria (inclusion/exclusion) listed | ✅ (see protocol.json) |
| 6 | Information sources listed (databases) | ✅ |
| 7 | Search strategy presented in full | ✅ (see report §2) |
| 8 | Date range specified | ☐ |
| 9 | Study selection process documented | ✅ (audit trail) |
| 10 | Data collection process described | ✅ (evidence matrix) |
| 11 | Data items listed | ✅ (evidence matrix columns) |
| 12 | Risk of bias assessment described | ☐ |
| 13 | Effect estimation methods described | ☐ |
| 14 | Synthesis methods described | ☐ |
| 15 | Reporting biases assessed | ☐ |
| 16 | Funding sources of included studies reported | ☐ |
| 17 | Funding sources of the review reported | ☐ |
| 18 | Registration and protocol availability | ☐ |
| 19 | Deviations from protocol explained | ☐ |
| 20 | Flow of studies (identifying → included) documented | ✅ (PRISMA flow) |
| 21 | Characteristics of each included study reported | ✅ |
| 22 | Summary of results presented | ✅ (evidence matrix) |
| 23 | Risk of bias results presented | ☐ |
| 24 | Synthesis results presented | ☐ |
| 25 | Certainty of evidence assessed | ☐ |
| 26 | Discussion of results in light of evidence | ☐ |
| 27 | Limitations discussed | ☐ |
| 28 | Conclusions related to objectives | ☐ |
"""
  with open(p["checklist_md"], "w", encoding="utf-8") as f:
    f.write(checklist)

  # PNG via mermaid-cli (best effort)
  png_ok = False
  try:
    import subprocess
    subprocess.run(
        ["npx", "-y", "@mermaid-js/mermaid-cli", "-i", p["flow_mmd"], "-o", p["flow_png"]],
        check=True, capture_output=True, timeout=120,
    )
    png_ok = True
  except Exception:
    png_ok = False
  return png_ok


def cmd_report(args) -> None:
  p = ensure_workspace(args.output_dir)
  c = _prisma_counts(p)
  _write_prisma_flow(p, c)
  png_ok = _write_report(p, c)
  append_audit(p, "step_7_prisma_report_generation",
               f"PRISMA report generated (identified={c['identified']}, "
               f"included={c['fulltext_included']}, png={'yes' if png_ok else 'no — use prisma_flow.mmd'})",
               records_identified=c["identified"],
               after_dedup=c["after_dedup"],
               screened_abstract=c["screened_abstract"],
               abstract_excluded=c["abstract_excluded"],
               fulltext_screened=c["fulltext_screened"],
               fulltext_excluded=c["fulltext_excluded"],
               included_in_review=c["fulltext_included"],
               png_generated=png_ok)
  print(f"Success! Report written to: {p['report_dir']}")
  if not png_ok:
    print("Note: PRISMA_flowchart.png could not be rendered (mermaid-cli unavailable); "
          "the diagram source is at report/prisma_flow.mmd.")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
  p = ensure_workspace(args.output_dir)
  proto = read_json(p["protocol"])
  print(f"Review: {proto.get('topic')}  [{proto.get('framework')}]")
  print(f"Root : {p['root']}")
  print(f"Status: {proto.get('status')}")
  print()
  print("Artifacts:")
  checks = [
      ("protocol.json", p["protocol"]),
      ("audit ledger", p["audit"]),
      ("raw_search_results.bib", os.path.join(p["root"], "data", "raw_search_results.bib")),
      ("deduplicated_records.json", p["dedup"]),
      ("abstract_decisions.csv", p["abstract_csv"]),
      ("fulltext_exclusion_log.json", p["fulltext_log"]),
      ("pdfs/manifest.json", p["pdf_manifest"]),
      ("evidence_matrix.csv", p["matrix_csv"]),
      ("extracted_variables.json", p["variables_json"]),
      ("PRISMA_flowchart.png", p["flow_png"]),
      ("systematic_review_draft.md", p["draft_md"]),
      ("systematic_review_draft.tex", p["draft_tex"]),
  ]
  for label, path in checks:
    ok = os.path.exists(path)
    print(f"  [{'x' if ok else ' '}] {label}")
  n_pdf = len([f for f in os.listdir(p["pdfs_dir"]) if f.endswith(".pdf")]) if os.path.isdir(p["pdfs_dir"]) else 0
  if n_pdf:
    print(f"  [x] {n_pdf} full-text PDF(s) in pdfs/")
  print()
  try:
    c = _prisma_counts(p)
    print("PRISMA counts:")
    print(f"  identified={c['identified']}  dedup={c['after_dedup']}  "
          f"abstract_in={c['abstract_included']}  abstract_out={c['abstract_excluded']}  "
          f"fulltext_in={c['fulltext_included']}  fulltext_out={c['fulltext_excluded']}")
  except Exception:
    pass
  print()
  print("Run `srw_cli.py verify --output-dir <root>` to check audit integrity.")


def _selftest_run(root: str) -> int:
  """Drive a synthetic end-to-end review in a temp workspace; return 0 on success."""
  import tempfile, shutil as _sh
  s = "scripts/srw_cli.py"
  cli = os.path.abspath(__file__)
  def run(*argv):
    import subprocess
    r = subprocess.run([sys.executable, cli] + list(argv),
                       capture_output=True, text=True)
    if r.returncode != 0:
      print(f"FAIL: {' '.join(argv)}\n{r.stdout}\n{r.stderr}")
      return False
    return True

  tmp = tempfile.mkdtemp(prefix="srw_selftest_")
  try:
    od = os.path.join(tmp, "rv")
    hits = [
        {"doi": "10.1/a", "title": "Alpha effect", "publication_year": 2020, "pdf_url": "http://x/a.pdf"},
        {"doi": "10.1/b", "title": "Beta review", "publication_year": 2015},
        {"doi": "10.1/c", "title": "Gamma trial", "publication_year": 2021, "pdf_url": "http://x/c.pdf"},
        {"doi": "10.1/a", "title": "Alpha effect", "publication_year": 2020},  # duplicate
    ]
    hf = os.path.join(tmp, "hits.json")
    with open(hf, "w") as f:
      json.dump(hits, f)
    rows = [
        ["Mouse, 10 mg/kg", "2020", "LC50 reported"],
    ]
    df = os.path.join(tmp, "data.json")
    with open(df, "w") as f:
      json.dump(rows, f)

    steps = [
        ["init", "--topic", "Self Test", "--framework", "PICO",
         "--databases", "openalex,pubmed", "--output-dir", od],
        ["protocol", "--output-dir", od, "--inclusion", "peer-reviewed",
         "--inclusion", "post-2018", "--exclusion", "reviews"],
        ["search", "--output-dir", od, "--source", "openalex",
         "--query", "self test", "--results-file", hf],
        ["screen-abstract", "--output-dir", od, "--include", "0,2", "--exclude", "1"],
        ["screen-fulltext", "--output-dir", od, "--include", "0", "--exclude", "2",
         "--exclude-reason", "2: no dose-response"],
        ["extract", "--output-dir", od, "--matrix-rows", "1",
         "--columns", "exposure,year,outcome", "--data-file", df],
        ["report", "--output-dir", od],
        ["verify", "--output-dir", od],
    ]
    for argv in steps:
      if not run(*argv):
        return 1

    # Confirm key artifacts exist.
    required = ["protocol.json", "audit_trail/lab_notebook.jsonl",
                "data/deduplicated_records.json", "screening/abstract_decisions.csv",
                "screening/fulltext_exclusion_log.json", "synthesis/evidence_matrix.csv",
                "report/systematic_review_draft.md", "report/prisma_flow.mmd"]
    missing = [rel for rel in required if not os.path.exists(os.path.join(od, rel))]
    if missing:
      print("FAIL: missing artifacts:", missing)
      return 1

    # Confirm audit ledger has 7 well-formed entries.
    lines = [l for l in open(os.path.join(od, "audit_trail/lab_notebook.jsonl")).read().splitlines() if l.strip()]
    if len(lines) != 7:
      print(f"FAIL: expected 7 audit entries, got {len(lines)}")
      return 1
    for l in lines:
      e = json.loads(l)
      for k in ("timestamp", "step", "agent", "action", "metrics", "status", "checksum"):
        if k not in e:
          print(f"FAIL: audit entry missing field '{k}': {l}")
          return 1
    print(f"SELFTEST PASS: 7 steps, 7 audit entries, all artifacts present. (tmp={tmp})")
    return 0
  finally:
    _sh.rmtree(tmp, ignore_errors=True)


def cmd_selftest(_args) -> None:
  rc = _selftest_run(os.getcwd())
  sys.exit(rc)


def main() -> None:
  parser = argparse.ArgumentParser(prog="srw_cli.py",
                                   description="PRISMA 2020 systematic review pipeline CLI.")
  sub = parser.add_subparsers(dest="command", required=True)

  sp = sub.add_parser("init", help="Initialise review workspace")
  sp.add_argument("--topic", required=True)
  sp.add_argument("--framework", required=True, choices=sorted(FRAMEWORKS))
  sp.add_argument("--databases", default="openalex,pubmed,arxiv")
  sp.add_argument("--output-dir")
  sp.set_defaults(func=cmd_init)

  sp = sub.add_parser("protocol", help="Persist inclusion/exclusion criteria")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--inclusion", action="append", required=True)
  sp.add_argument("--exclusion", action="append", default=[])
  sp.set_defaults(func=cmd_protocol)

  sp = sub.add_parser("search", help="Ingest and deduplicate search results")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--source", required=True, choices=VALID_SOURCES)
  sp.add_argument("--query", required=True)
  sp.add_argument("--results-file", help="Raw JSON results written by the search skill")
  sp.set_defaults(func=cmd_search)

  sp = sub.add_parser("screen-abstract", help="Record abstract screening decisions")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--include", required=True)
  sp.add_argument("--exclude", default="")
  sp.add_argument("--exclude-reason", default="")
  sp.set_defaults(func=cmd_screen_abstract)

  sp = sub.add_parser("fetch-fulltext", help="Download full-text PDFs and hash them")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--openalex-api-key", default="")
  sp.set_defaults(func=cmd_fetch_fulltext)

  sp = sub.add_parser("register-pdf", help="Register an externally fetched PDF")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--record-index", required=True, type=int)
  sp.add_argument("--pdf-path", required=True)
  sp.set_defaults(func=cmd_register_pdf)

  sp = sub.add_parser("screen-fulltext", help="Record full-text screening with rationales")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--include", required=True)
  sp.add_argument("--exclude", default="")
  sp.add_argument("--exclude-reason", action="append",
                  help="'index: reason' pair (repeatable, required for every exclusion)")
  sp.set_defaults(func=cmd_screen_fulltext)

  sp = sub.add_parser("extract", help="Validate and write the evidence matrix")
  sp.add_argument("--output-dir", required=True)
  sp.add_argument("--matrix-rows", required=True, type=int)
  sp.add_argument("--columns", required=True)
  sp.add_argument("--data-file", help="JSON (list of rows) or CSV data written by the agent")
  sp.set_defaults(func=cmd_extract)

  sp = sub.add_parser("report", help="Generate PRISMA report, flow diagram and manuscript")
  sp.add_argument("--output-dir", required=True)
  sp.set_defaults(func=cmd_report)

  sp = sub.add_parser("verify", help="Verify the audit ledger (checksums + chronology)")
  sp.add_argument("--output-dir", required=True)
  sp.set_defaults(func=lambda a: sys.exit(verify_audit(workspace_paths(a.output_dir))))

  sp = sub.add_parser("status", help="Show review state and artifact checklist")
  sp.add_argument("--output-dir", required=True)
  sp.set_defaults(func=cmd_status)

  sp = sub.add_parser("selftest", help="Run a synthetic end-to-end self-test (no network)")
  sp.set_defaults(func=cmd_selftest)

  args = parser.parse_args()
  try:
    args.func(args)
  except SystemExit:
    raise
  except Exception as e:
    sys.exit(f"Error: {e}")


if __name__ == "__main__":
  main()
