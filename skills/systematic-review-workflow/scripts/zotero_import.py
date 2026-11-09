#!/usr/bin/env python3
"""zotero_import.py — Import full-text PDFs from a local Zotero library into a
systematic-review workspace, then re-run the downstream pipeline.

Workflow (all mutating steps go through srw_cli.py so the audit ledger stays
consistent):
  1. Discover  Query the Zotero Local API (localhost:23119) for the target
               collection's PDF attachments (paginated). Resolve each
               attachment's parent item for its DOI + title.
  2. Match     Build a DOI -> record_index map from synthesis/evidence_matrix.csv.
               Identify PDFs that (a) match an included record, (b) exist on disk,
               and (c) are not already registered in pdfs/manifest.json.
               Duplicates (same record_index) are collapsed — the on-disk copy wins.
  3. Register  For each new match, call `srw_cli.py register-pdf` (copy + SHA-256
               + manifest + audit entry).
  4. Re-screen `srw_cli.py screen-fulltext` (recomputes fulltext_available from the
               manifest automatically).
  5. Extract   Rebuild the extract data-file from the current evidence matrix,
               flipping `recorded_as` to "full-text" for every record that now has
               a PDF, then `srw_cli.py extract`.
  6. Report    `srw_cli.py report` (PRISMA SVG + markdown + LaTeX).
  7. Portal    `srw_cli.py portal` (index.html + viewer.html).
  8. Verify    `srw_cli.py verify` (audit checksums + chronology).

Usage:
  # Dry-run (discover + match only, print plan, change nothing)
  python3 scripts/zotero_import.py --output-dir ./reviews/<topic> \
      --collection photobiological-hydrogen-production --dry-run

  # Full import + re-run
  python3 scripts/zotero_import.py --output-dir ./reviews/<topic> \
      --collection photobiological-hydrogen-production

Notes:
  - Zotero's Local API must be enabled: Edit/Tools > Settings > Advanced >
    "Allow other applications on this computer to communicate with Zotero".
  - Reads only; never writes to the Zotero library.
  - stdlib-only (urllib, json, csv, hashlib, subprocess, argparse).
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRW_CLI = os.path.join(SCRIPT_DIR, "srw_cli.py")

DEFAULT_API = "http://localhost:23119/api"
DEFAULT_EXTRACT_COLS = (
    "organism,mechanism,light_regime,medium_nutrient,h2_yield,"
    "oxygen_tolerance,recorded_as"
)


# --------------------------------------------------------------------------- #
# HTTP helpers (Local API)
# --------------------------------------------------------------------------- #
class ZoteroAPI:
    """Minimal read-only client for the Zotero Local API."""

    def __init__(self, base: str, server_id: str = None, timeout: int = 60):
        self.base = base.rstrip("/")
        self.server_id = server_id
        self.timeout = timeout

    def _headers(self):
        h = {"Accept": "application/json"}
        if self.server_id:
            h["Zotero-Server-ID"] = self.server_id
        return h

    def _request(self, path):
        url = self.base + path
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"HTTP {e.code} for {path}: {body[:300]}")

    def ping(self):
        """Return (connector_version, server_id) from /connector/ping."""
        root = self.base
        if root.endswith("/api"):
            root = root[:-4]
        url = root + "/connector/ping"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            ver = r.headers.get("Zotero-Connector-API-Version", "?")
            sid = r.headers.get("Zotero-Server-ID")
            return ver, sid

    def ensure_server_id(self):
        if self.server_id:
            return self.server_id
        try:
            _, sid = self.ping()
            self.server_id = sid
        except Exception:
            pass
        return self.server_id

    def get_json(self, path):
        st, _, body = self._request(path)
        return st, json.loads(body)

    def list_collections(self):
        _, data = self.get_json("/users/0/collections")
        out = []
        for c in data:
            d = c.get("data", {})
            out.append({
                "key": c.get("key"),
                "name": d.get("name"),
                "library": (c.get("library") or {}).get("name"),
            })
        return out

    def resolve_collection(self, name_or_key):
        """Accept a collection name (substring) or an 8-char key."""
        cols = self.list_collections()
        # exact key match
        for c in cols:
            if c["key"] == name_or_key:
                return c
        # key contained
        for c in cols:
            if name_or_key and name_or_key in (c["key"] or ""):
                return c
        # case-insensitive name substring
        needle = name_or_key.lower()
        for c in cols:
            if needle and needle in (c["name"] or "").lower():
                return c
        raise RuntimeError(
            f"No collection matching {name_or_key!r}. Available: "
            + ", ".join(c["name"] for c in cols)
        )

    def _paged(self, path):
        """Generic Link-header pagination. Returns list of raw items."""
        items = []
        while path:
            st, hdrs, body = self._request(path)
            page = json.loads(body)
            items.extend(page)
            link = hdrs.get("Link", "")
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if m:
                url = m.group(1)
                if url.startswith(self.base):
                    url = url[len(self.base):]
                path = url
            else:
                path = None
        return items

    def get_items(self, keys):
        """Fetch items by key list (batched at 50)."""
        out = []
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            path = "/users/0/items?keys=" + ",".join(chunk)
            _, hdrs, body = self._request(path)
            out.extend(json.loads(body))
        return out


def file_path_from_href(href):
    """file:///C:/x/y.pdf  ->  C:\\x\\y.pdf"""
    if not href:
        return ""
    p = urllib.parse.unquote(urllib.parse.urlparse(href).path)
    if p.startswith("/"):
        p = p[1:]
    return p.replace("/", "\\")


def norm_doi(d):
    if not d:
        return ""
    d = str(d).strip().lower()
    for pre in ("https://", "http://", "doi.org/", "dx.doi.org/"):
        if d.startswith(pre):
            d = d[len(pre):]
    return d.strip("/").rstrip(".")


def discover(api, col_key):
    """Return list of PDF-attachment descriptors with resolved DOI/title/path."""
    items = api._paged(f"/users/0/collections/{col_key}/items?limit=100")
    atts = [it for it in items if it.get("data", {}).get("itemType") == "attachment"]
    pdf_atts = [a for a in atts if (a.get("data", {}).get("contentType", "")).startswith("application/pdf")]

    parent_keys = []
    info = []
    for a in pdf_atts:
        d = a["data"]
        enc = a.get("links", {}).get("enclosure", {})
        fp = file_path_from_href(enc.get("href", ""))
        pk = d.get("parentItem", "")
        info.append({
            "attach_key": a["key"],
            "parent_key": pk,
            "file_path": fp,
            "file_exists": bool(fp) and os.path.exists(fp),
            "file_size_kb": (os.path.getsize(fp) // 1024) if fp and os.path.exists(fp) else 0,
        })
        if pk and pk not in parent_keys:
            parent_keys.append(pk)

    parent_meta = {}
    if parent_keys:
        for it in api.get_items(parent_keys):
            d = it.get("data", {})
            parent_meta[it["key"]] = {
                "title": d.get("title", ""),
                "doi": norm_doi(d.get("DOI") or d.get("doi") or ""),
            }
    for e in info:
        pm = parent_meta.get(e["parent_key"], {})
        e["title"] = pm.get("title", "")
        e["doi"] = pm.get("doi", "")
    return items, info


def build_plan(review_dir, info):
    """Match discovered PDFs to the evidence matrix; classify new/already/missing."""
    mpath = os.path.join(review_dir, "synthesis", "evidence_matrix.csv")
    matrix = list(csv.DictReader(open(mpath, encoding="utf-8", errors="replace")))
    by_doi = {}
    for r in matrix:
        d = norm_doi(r.get("doi", ""))
        if d:
            by_doi.setdefault(d, r)

    # Already-registered records (PDF on disk in pdfs/)
    have = set()
    for fn in os.listdir(os.path.join(review_dir, "pdfs")):
        if fn.startswith("study_") and fn.endswith(".pdf"):
            have.add(int(fn[6:9]))

    # Dedupe by record_index, preferring on-disk copy
    by_rec = {}
    for e in info:
        m = by_doi.get(e["doi"])
        if not m:
            continue
        ri = int(m["record_index"])
        cur = by_rec.get(ri)
        if cur is None or (e["file_exists"] and not cur["file_exists"]):
            e2 = dict(e)
            e2["record_index"] = ri
            e2["matrix_title"] = m.get("title", "")
            by_rec[ri] = e2

    new, already, matched_missing = [], [], []
    for ri, e in by_rec.items():
        if e["file_exists"] and ri not in have:
            new.append(e)
        elif e["file_exists"] and ri in have:
            already.append(e)
        else:
            matched_missing.append(e)

    unmatched = [e for e in info if norm_doi(e["doi"]) not in by_doi]
    return {
        "new": sorted(new, key=lambda x: x["record_index"]),
        "already": sorted(already, key=lambda x: x["record_index"]),
        "matched_missing": sorted(matched_missing, key=lambda x: x["record_index"]),
        "unmatched": unmatched,
        "matrix_rows": len(matrix),
    }


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #
def run_cli(args, timeout=180):
    cmd = [sys.executable, SRW_CLI] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r


def read_ft_log(review_dir):
    p = os.path.join(review_dir, "screening", "fulltext_exclusion_log.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8", errors="replace"))


def have_ids_from_manifest(review_dir):
    p = os.path.join(review_dir, "pdfs", "manifest.json")
    if not os.path.exists(p):
        return set()
    man = json.load(open(p, encoding="utf-8", errors="replace"))
    return {m["record_index"] for m in man
            if isinstance(m, dict) and m.get("path") and os.path.exists(m["path"])}


def rebuild_extract_data(review_dir, out_data):
    """Derive the extract data-file (list of lists) from the current evidence
    matrix, setting recorded_as='full-text' for every record that now has a PDF."""
    mpath = os.path.join(review_dir, "synthesis", "evidence_matrix.csv")
    matrix = list(csv.DictReader(open(mpath, encoding="utf-8", errors="replace")))
    cols = ["organism", "mechanism", "light_regime", "medium_nutrient",
            "h2_yield", "oxygen_tolerance", "recorded_as"]
    have = have_ids_from_manifest(review_dir)
    rows = []
    for r in matrix:
        ri = int(r.get("record_index", -1))
        ra = "full-text" if ri in have else r.get("recorded_as", "abstract-only")
        rows.append([r.get(c, "") for c in cols[:-1]] + [ra])
    json.dump(rows, open(out_data, "w", encoding="utf-8"), ensure_ascii=False)
    return len(rows), cols


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", required=True, help="Review workspace directory")
    ap.add_argument("--collection", required=True, help="Zotero collection name (substring) or key")
    ap.add_argument("--api", default=DEFAULT_API, help=f"Zotero base URL (default {DEFAULT_API})")
    ap.add_argument("--server-id", default="", help="Zotero-Server-ID (auto-detected if omitted)")
    ap.add_argument("--extract-cols", default=DEFAULT_EXTRACT_COLS, help="Comma-separated extract columns")
    ap.add_argument("--dry-run", action="store_true", help="Discover + match only; change nothing")
    ap.add_argument("--no-rerun", action="store_true", help="Register PDFs but skip the screen/extract/report/portal/verify re-run")
    ap.add_argument("--json-out", default="", help="Also write the plan to this JSON path")
    args = ap.parse_args()

    review = os.path.abspath(args.output_dir)
    if not os.path.isdir(os.path.join(review, "synthesis")):
        sys.exit(f"Error: {review} does not look like a review workspace (no synthesis/).")

    api = ZoteroAPI(args.api, server_id=args.server_id or None)
    api.ensure_server_id()

    print("=== 1. Discover (Zotero Local API) ===")
    col = api.resolve_collection(args.collection)
    print(f"  collection: {col['name']}  key={col['key']}  library={col.get('library')}")
    items, info = discover(api, col["key"])
    n_pdf = len(info)
    n_on_disk = sum(1 for e in info if e["file_exists"])
    print(f"  collection items: {len(items)}  | PDF attachments: {n_pdf}  | on-disk: {n_on_disk}")

    print("\n=== 2. Match to evidence matrix ===")
    plan = build_plan(review, info)
    print(f"  matrix rows: {plan['matrix_rows']}")
    print(f"  NEW to register:   {len(plan['new'])}")
    print(f"  already registered:{len(plan['already'])}")
    print(f"  matched (no PDF yet): {len(plan['matched_missing'])}")
    print(f"  unmatched (not in matrix): {len(plan['unmatched'])}")

    if args.json_out:
        json.dump({k: v for k, v in plan.items()}, open(args.json_out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print(f"  plan -> {args.json_out}")

    if args.dry_run:
        print("\n=== DRY RUN — new candidates (no changes made) ===")
        for e in plan["new"]:
            print(f"  rec {e['record_index']:3d}  {e['doi'][:44]:44s}  {e['title'][:50]}")
        return

    # ---- 3. Register ----
    print(f"\n=== 3. Register {len(plan['new'])} new PDFs ===")
    ok = fail = 0
    for i, e in enumerate(plan["new"], 1):
        ri, fp, title = e["record_index"], e["file_path"], e["title"][:48]
        r = run_cli(["register-pdf", "--output-dir", review,
                     "--record-index", str(ri), "--pdf-path", fp], timeout=120)
        status = "OK " if r.returncode == 0 else "FAIL"
        print(f"  [{i:2d}/{len(plan['new'])}] {status} rec {ri:3d}  {title}")
        if r.returncode != 0:
            fail += 1
            print(f"         {r.stderr.strip()[:200]}")
        else:
            ok += 1
    print(f"  registered={ok}  failed={fail}")

    if args.no_rerun:
        print("\n(no-rerun set; stopping after register)")
        return

    # ---- 4. Re-screen ----
    print("\n=== 4. screen-fulltext ===")
    log = read_ft_log(review)
    if not log:
        sys.exit("Error: no fulltext_exclusion_log.json to re-screen from.")
    include_str = ",".join(str(i) for i in log["included"])
    exclude_str = ",".join(str(e["index"]) for e in log.get("excluded", []))
    reasons = [f"{e['index']}: {e['reason']}" for e in log.get("excluded", [])]
    cmd = ["screen-fulltext", "--output-dir", review,
           "--include", include_str, "--exclude", exclude_str]
    for rr in reasons:
        cmd += ["--exclude-reason", rr]
    r = run_cli(cmd, timeout=120)
    print(f"  {'OK ' if r.returncode == 0 else 'FAIL'} {r.stdout.strip()[:200]}")
    if r.returncode != 0:
        print(f"  STDERR: {r.stderr.strip()[:300]}"); sys.exit(1)
    log2 = read_ft_log(review)
    print(f"  fulltext_available={log2['fulltext_available']}  abstract_only={log2['abstract_only']}")

    # ---- 5. Extract ----
    print("\n=== 5. extract ===")
    data_path = os.path.join(review, "synthesis", "_extract_data.json")
    nrows, cols = rebuild_extract_data(review, data_path)
    cols_str = ",".join(cols)
    r = run_cli(["extract", "--output-dir", review,
                 "--matrix-rows", str(nrows), "--columns", cols_str,
                 "--data-file", data_path], timeout=120)
    print(f"  {'OK ' if r.returncode == 0 else 'FAIL'} {r.stdout.strip()[:200]}")
    if r.returncode != 0:
        print(f"  STDERR: {r.stderr.strip()[:300]}"); sys.exit(1)

    # ---- 6. Report ----
    print("\n=== 6. report ===")
    r = run_cli(["report", "--output-dir", review], timeout=180)
    print(f"  {'OK ' if r.returncode == 0 else 'FAIL'} {r.stdout.strip()[:160]}")

    # ---- 7. Portal ----
    print("\n=== 7. portal ===")
    r = run_cli(["portal", "--output-dir", review], timeout=240)
    print(f"  {'OK ' if r.returncode == 0 else 'FAIL'} {r.stdout.strip()[:160]}")

    # ---- 8. Verify ----
    print("\n=== 8. verify ===")
    r = run_cli(["verify", "--output-dir", review], timeout=120)
    print(f"  {'OK ' if r.returncode == 0 else 'FAIL'} {r.stdout.strip()}")

    # ---- Final state ----
    print("\n=== FINAL STATE ===")
    pdfs = [f for f in os.listdir(os.path.join(review, "pdfs")) if f.endswith(".pdf")]
    print(f"  PDFs on disk: {len(pdfs)}")
    for label, path in [("index.html", os.path.join(review, "index.html")),
                        ("viewer.html", os.path.join(review, "viewer.html")),
                        ("PRISMA_flowchart.svg", os.path.join(review, "report", "PRISMA_flowchart.svg"))]:
        print(f"  {label}: " + (f"{os.path.getsize(path)//1024} KB" if os.path.exists(path) else "MISSING"))
    print("\nDONE")


if __name__ == "__main__":
    main()
