#!/usr/bin/env python3
"""
fetch-fulltext-goose-corrected
==============================
Corrected replacement for the skill's buggy `fetch-fulltext` command.

Fixes applied (vs. srw_cli.py cmd_fetch_fulltext):
  1. BUG FIX: `args.source` was referenced but never defined in argparse.
     → Now auto-detects source from each record's fields (openalex_id / pmid).
       An optional `--source` flag is accepted for explicit override.
  2. PDF VALIDATION: The original `download()` wrote any byte stream.
     → Now validates %PDF magic bytes (first 4 bytes) before accepting.
       Non-PDF responses (HTML error pages, XML, etc.) are rejected.
  3. DOI CONTENT-NEGOTIATION: If the direct URL fails, attempts
     `https://doi.org/<doi>` with `Accept: application/pdf` header.
  4. GRACEFUL 404 HANDLING: Paywalled / unavailable PDFs are logged with
     a clear error message and an honest PARTIAL/FAILED audit entry.
     No silent failures; manifest records the reason.

Usage (standalone):
  python3 fetch-fulltext-goose-corrected.py \
      --output-dir /path/to/review \
      [--openalex-api-key KEY] \
      [--source openalex|pubmed|auto] \
      [--max N]

Usage (import from another script):
  import importlib.util, types
  spec = importlib.util.spec_from_file_location(
      "fetch_ft_goose", "path/to/fetch-fulltext-goose-corrected.py")
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  mod.run(output_dir="/path/to/review", source="auto")

Audit-trail compatible: appends to the same lab_notebook.jsonl the skill uses.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AGENT_ID = "Goose-AI / fetch-fulltext-goose-corrected/1.0"
UA = "fetch-fulltext-goose-corrected/1.0 (systematic-review-workflow)"
TIMEOUT = 90
PDF_MAGIC = b"%PDF"

# ---------------------------------------------------------------------------
# Helpers (mirroring srw_cli.py but self-contained)
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def workspace_paths(output_dir: str) -> dict:
    return {
        "root": output_dir,
        "protocol": os.path.join(output_dir, "protocol.json"),
        "dedup": os.path.join(output_dir, "data", "deduplicated_records.json"),
        "abstract_csv": os.path.join(output_dir, "screening", "abstract_decisions.csv"),
        "fulltext_log": os.path.join(output_dir, "screening", "fulltext_exclusion_log.json"),
        "pdfs_dir": os.path.join(output_dir, "pdfs"),
        "pdf_manifest": os.path.join(output_dir, "pdfs", "manifest.json"),
        "audit": os.path.join(output_dir, "audit_trail", "lab_notebook.jsonl"),
    }


def ensure_workspace(output_dir: str) -> dict:
    p = workspace_paths(output_dir)
    if not os.path.exists(p["protocol"]):
        sys.exit(f"Error: {output_dir} is not initialised. Run `init` first.")
    os.makedirs(p["pdfs_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(p["audit"]), exist_ok=True)
    return p


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


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


def _included_indices(p: dict) -> list:
    """Get included indices from abstract screening CSV."""
    if not os.path.exists(p["abstract_csv"]):
        # Fallback: if full-text screening has been done, use that
        if os.path.exists(p["fulltext_log"]):
            ft = read_json(p["fulltext_log"])
            if isinstance(ft, dict) and "included" in ft:
                return ft["included"]
        sys.exit("Error: No abstract screening found. Run `screen-abstract` first.")
    with open(p["abstract_csv"], newline="", encoding="utf-8") as f:
        return [int(r["index"]) for r in csv.DictReader(f) if r["decision"] == "included"]


def append_audit(p: dict, step: str, action: str = "",
                 status: str = "SUCCESS", **metrics) -> None:
    """Append an immutable lab-grade JSONL entry."""
    entry = {
        "timestamp": _utc_z(),
        "step": step,
        "action": action,
        "status": status,
        "agent": AGENT_ID,
    }
    entry.update(metrics)
    # Compute checksum over all prior entries
    checksum = "0" * 64
    if os.path.exists(p["audit"]):
        with open(p["audit"], "rb") as f:
            data = f.read()
        checksum = hashlib.sha256(data + entry["timestamp"].encode()).hexdigest()
    entry["checksum"] = checksum
    with open(p["audit"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# FIXED: Robust PDF download with magic-byte validation + DOI negotiation
# ---------------------------------------------------------------------------

def _try_url(url: str, timeout: int = TIMEOUT) -> bytes:
    """Attempt to fetch a URL and return bytes ONLY if it's a valid PDF.
    Raises RuntimeError with descriptive message on failure."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/pdf, application/x-pdf, */*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            head = resp.read(4)
            if not head.startswith(PDF_MAGIC):
                # Read rest to check if it's an HTML error page
                rest = resp.read(200)
                raise RuntimeError(
                    f"Response from {url} is not a PDF "
                    f"(got: {head[:4]!r} {rest[:60]!r}). Likely paywalled or redirect."
                )
            data = head + resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error for {url}: {e.reason}") from e

    if not data or len(data) < 100:
        raise RuntimeError(f"Response too short ({len(data) if data else 0} bytes) from {url}")
    if not data.startswith(PDF_MAGIC):
        raise RuntimeError(f"Content from {url} does not start with %PDF")
    return data


def download_pdf(url: str, out_path: str, doi: str = None, timeout: int = TIMEOUT) -> None:
    """
    Download a PDF with fallback chain:
      1. Direct URL (with Accept: application/pdf)
      2. If URL contains doi.org, skip (already a DOI)
      3. If doi provided, try https://doi.org/<doi> with Accept: application/pdf
      4. If still fails, raise with all errors combined.
    Validates %PDF magic bytes before writing.
    """
    errors = []

    # Candidate 1: direct URL
    if url:
        try:
            data = _try_url(url, timeout)
            with open(out_path, "wb") as f:
                f.write(data)
            return
        except (RuntimeError, Exception) as e:
            errors.append(str(e))

    # Candidate 2: DOI content-negotiation
    doi_to_try = doi
    if not doi_to_try and url and "doi.org/" in url:
        # Extract DOI from URL
        m = re.search(r"doi\.org/([^/]+)", url)
        if m:
            doi_to_try = m.group(1)
    if not doi_to_try and url:
        # Check if the URL itself looks like a DOI
        m = re.search(r"(10\.\d{4,}/\S+)", url)
        if m:
            doi_to_try = m.group(1).rstrip(".,;")

    if doi_to_try:
        doi_url = f"https://doi.org/{doi_to_try}"
        try:
            data = _try_url(doi_url, timeout)
            with open(out_path, "wb") as f:
                f.write(data)
            return
        except (RuntimeError, Exception) as e:
            errors.append(str(e))

    # Candidate 3: OpenAlex content API (if openalex_id derivable)
    # (handled by caller if openalex_id is available)

    raise RuntimeError(" | ".join(errors) if errors else "No URL or DOI available")


# ---------------------------------------------------------------------------
# FIXED: cmd_fetch_fulltext — no more undefined args.source
# ---------------------------------------------------------------------------

def cmd_fetch_fulltext(args) -> None:
    """
    Corrected fetch-fulltext. Source is auto-detected per-record:
      - Record has openalex_id → try OpenAlex content API first
      - Record has pmid → try NCBI (if available)
      - Record has pdf_url → try direct
      - Record has doi → try DOI content-negotiation
    """
    p = ensure_workspace(args.output_dir)
    data = read_json(p["dedup"])
    records = data["records"]
    included = _included_indices(p)

    # Optional: cap the number of records to fetch (for testing)
    max_records = getattr(args, "max", 0) or 0
    if max_records > 0:
        included = included[:max_records]
        print(f"[fetch-fulltext-goose-corrected] Capped to first {max_records} records", file=sys.stderr)

    key = args.openalex_api_key or load_env_key("OPENALEX_API_KEY")

    # Source override: "openalex" | "pubmed" | "auto" (default: auto)
    source_override = getattr(args, "source", "auto") or "auto"

    # Load existing manifest (resumable)
    manifest = read_json(p["pdf_manifest"]) if os.path.exists(p["pdf_manifest"]) else []
    have = {m["record_index"] for m in manifest if m.get("path")}
    # Also track failed ones so we don't re-attempt in same run unless --retry-failed
    failed_indices = {m["record_index"] for m in manifest if m.get("error")}
    retry_failed = getattr(args, "retry_failed", False)

    fetched, failed, skipped = 0, 0, 0
    failure_reasons = {}  # error → count

    for i in included:
        if i in have:
            skipped += 1
            continue
        if i in failed_indices and not retry_failed:
            skipped += 1
            continue

        rec = records[i]
        doi = (rec.get("doi") or "").strip()
        pdf_url = (rec.get("pdf_url") or "").strip()
        openalex_id = (rec.get("openalex_id") or "").strip()
        pmid = (rec.get("pmid") or "").strip()

        # --- Determine download strategy based on record fields (FIX #1) ---
        candidate_urls = []

        # Strategy priority:
        # 1. OpenAlex content API (if we have an openalex_id)
        if openalex_id and (source_override in ("auto", "openalex")):
            candidate_urls.append(f"https://content.openalex.org/{openalex_id}.pdf")

        # 2. Direct pdf_url if present
        if pdf_url:
            candidate_urls.append(pdf_url)

        # 3. DOI content-negotiation (handled inside download_pdf)

        # 4. NCBI PMC full-text (if pmid and PMC available)
        if pmid and (source_override in ("auto", "pubmed")):
            # Try PMC OA service (no key needed for OA articles)
            candidate_urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmid}/pdf/")

        if not candidate_urls and not doi:
            failed += 1
            failure_reasons["no_url_or_doi"] = failure_reasons.get("no_url_or_doi", 0) + 1
            manifest.append({
                "record_index": i,
                "title": rec.get("title"),
                "doi": doi,
                "path": None,
                "error": "No URL, DOI, openalex_id, or pmid available for download",
                "fetched_at": now_iso(),
            })
            continue

        # --- Attempt download (FIX #2, #3: validated + DOI negotiation) ---
        seq = included.index(i) + 1
        safe_name = "study_%03d" % seq
        out_path = os.path.join(p["pdfs_dir"], f"{safe_name}.pdf")

        try:
            # Try each candidate URL
            last_err = None
            success = False
            for cand_url in candidate_urls:
                try:
                    download_pdf(cand_url, out_path, doi=doi or None)
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    # Clean up partial file
                    if os.path.exists(out_path):
                        os.remove(out_path)
                    continue

            if not success:
                raise last_err or RuntimeError("All download candidates failed")

            # Verify the file is a valid PDF (belt-and-suspenders)
            with open(out_path, "rb") as f:
                magic = f.read(4)
            if not magic.startswith(PDF_MAGIC):
                os.remove(out_path)
                raise RuntimeError(f"File at {out_path} is not a valid PDF (magic: {magic!r})")

            manifest.append({
                "record_index": i,
                "title": rec.get("title"),
                "doi": doi,
                "pmid": pmid or None,
                "openalex_id": openalex_id or None,
                "path": out_path,
                "sha256": sha256_of(out_path),
                "size_bytes": os.path.getsize(out_path),
                "fetched_at": now_iso(),
                "source": "openalex" if openalex_id else ("pubmed" if pmid else "doi"),
            })
            fetched += 1
            print(f"  ✓ [{seq}] {rec.get('title', 'Untitled')[:70]}", file=sys.stderr)

        except Exception as e:
            failed += 1
            err_str = str(e)
            # Categorize failure
            if "404" in err_str or "Not Found" in err_str:
                category = "paywalled_404"
            elif "403" in err_str or "Forbidden" in err_str:
                category = "access_denied_403"
            elif "401" in err_str or "Unauthorized" in err_str:
                category = "auth_required_401"
            elif "not a PDF" in err_str or "No URL" in err_str:
                category = "not_available"
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                category = "timeout"
            else:
                category = "other"
            failure_reasons[category] = failure_reasons.get(category, 0) + 1

            print(f"  ✗ [{seq}] {rec.get('title', 'Untitled')[:70]} — {category}", file=sys.stderr)
            manifest.append({
                "record_index": i,
                "title": rec.get("title"),
                "doi": doi,
                "pmid": pmid or None,
                "openalex_id": openalex_id or None,
                "path": None,
                "error": err_str[:300],
                "failure_category": category,
                "fetched_at": now_iso(),
            })

    # --- Write manifest ---
    write_json(p["pdf_manifest"], manifest)

    # --- Audit entry (FIX #4: honest PARTIAL/FAILED) ---
    status = "SUCCESS" if failed == 0 and fetched > 0 else ("PARTIAL" if fetched > 0 else "FAILED")
    audit_msg = (
        f"Full-text retrieval (goose-corrected): {fetched} fetched, "
        f"{failed} failed, {skipped} already-present. "
        f"Failure breakdown: {failure_reasons}"
    )
    append_audit(
        p, "step_4_fulltext_retrieval",
        audit_msg,
        status=status,
        requested=len(included),
        fetched=fetched,
        failed=failed,
        skipped=skipped,
        failure_reasons=failure_reasons,
        agent=AGENT_ID,
        correction="fetch-fulltext-goose-corrected: fixed args.source bug, "
                   "added PDF magic-byte validation, DOI content-negotiation, "
                   "graceful paywall handling",
    )

    # --- Summary ---
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"fetch-fulltext-goose-corrected — {status}", file=sys.stderr)
    print(f"  Requested:  {len(included)}", file=sys.stderr)
    print(f"  Fetched:    {fetched}", file=sys.stderr)
    print(f"  Failed:     {failed}", file=sys.stderr)
    if failure_reasons:
        print(f"  Reasons:    {failure_reasons}", file=sys.stderr)
    print(f"  Manifest:   {p['pdf_manifest']}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if status == "FAILED":
        print("\n  → All PDFs unavailable (paywalled/no OA). ", file=sys.stderr)
        print("    Continue with abstract-only per skill guidance.", file=sys.stderr)
    elif status == "PARTIAL":
        print("\n  → Partial success. Failed PDFs are logged in manifest.", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="fetch-fulltext-goose-corrected: Download full-text PDFs with validation and DOI negotiation"
    )
    ap.add_argument("--output-dir", required=True,
                    help="Path to the review workspace (must be initialised)")
    ap.add_argument("--openalex-api-key", default="",
                    help="OpenAlex API key (or set OPENALEX_API_KEY env var)")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "openalex", "pubmed"],
                    help="Source priority: auto (detect per-record), openalex, or pubmed")
    ap.add_argument("--max", type=int, default=0,
                    help="Max records to fetch (0 = all, for testing)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="Re-attempt records that previously failed")
    args = ap.parse_args()
    cmd_fetch_fulltext(args)


if __name__ == "__main__":
    main()
