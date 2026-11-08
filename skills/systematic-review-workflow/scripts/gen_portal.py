#!/usr/bin/env python3
"""Generate a self-contained review web portal (index.html + report/index.html).

Design goals
------------
1. GENERIC: driven by protocol.json and the evidence-matrix column headers -
   not hard-coded to one review. Any review produced by this skill renders.
2. DEFENSIVE: if a data source is missing (or empty), the corresponding
   section is STILL rendered, with a clear placeholder explaining WHY it is
   empty. The script never crashes on a partially-built workspace.
3. SELF-CONTAINED: zero external dependencies, pure inline SVG charts,
   relative links - works from file:// (double-click) and over HTTP/Caddy.

Environment
-----------
SRW_OUTPUT_DIR    (required)  absolute path to the review workspace root.

Output
------
<root>/index.html            the portal (deliverable)
<root>/report/index.html     a byte-identical copy for report/ subpath serving
"""
import json, os, csv, html, re, datetime
from collections import Counter

ROOT = os.environ.get("SRW_OUTPUT_DIR") or os.getcwd()
E = lambda s: html.escape(str(s if s is not None else ""))
def _p(*a): return os.path.join(ROOT, *a)
def _exists(rel): return os.path.exists(_p(*rel))
def _rel(rel): return "/".join(rel)

# Route "open" links: render text-ish files via viewer.html so they read nicely;
# PDFs and the portal link directly (browsers already render PDFs natively).
def viewlink(rel):
    ext = (rel.rsplit('.', 1)[-1].lower() if '.' in rel else '')
    if ext in ('json', 'jsonl', 'md', 'csv', 'tex', 'mmd'):
        from urllib.parse import quote
        return 'viewer.html?file=' + quote(rel, safe='/')
    return rel

def load_json(rel):
    try:
        with open(_p(*rel), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
def load_csv(rel):
    try:
        with open(_p(*rel), encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return None
def read_text(rel, limit=None):
    try:
        with open(_p(*rel), encoding="utf-8", errors="replace") as f:
            s = f.read()
        return s[:limit] if limit else s
    except Exception:
        return ""

# ---------------------------------------------------------------------------
# Load every data source defensively. Each is (value, present-flag, reason).
# ---------------------------------------------------------------------------
proto = load_json(["protocol.json"])
ad_rows = load_csv(["screening", "abstract_decisions.csv"])
ft = load_json(["screening", "fulltext_exclusion_log.json"])
matrix = load_csv(["synthesis", "evidence_matrix.csv"])
audit = None
try:
    with open(_p("audit_trail", "lab_notebook.jsonl"), encoding="utf-8") as f:
        audit = [json.loads(l) for l in f if l.strip()]
except Exception:
    audit = None
recs_data = load_json(["data", "deduplicated_records.json"])
recs = (recs_data or {}).get("records", []) if isinstance(recs_data, dict) else (recs_data or [])
manifest = load_json(["pdfs", "manifest.json"])
manifest = manifest if isinstance(manifest, list) else []

# Optional, per-review customisation. LIVES IN THE REVIEW WORKSPACE (not the
# skill) so each topic can override labels, which columns to chart, headline
# chips, and the interpretive note. Without this file the generator auto-
# infers everything from the evidence-matrix headers, so any review renders.
cfg = load_json(["portal_config.json"]) or {}

PRESENT = {
    "proto":    proto is not None,
    "ad":       ad_rows is not None,
    "ft":       ft is not None,
    "matrix":   matrix is not None and len(matrix) > 0,
    "audit":    audit is not None and len(audit) > 0,
    "recs":     recs is not None and len(recs) > 0,
    "manifest": manifest is not None and len(manifest) > 0,
}
def present(flag):
    return PRESENT.get(flag, False)

def recget(rec, key, default=""):
    return (rec or {}).get(key, default) if isinstance(rec, dict) else default

def ph(text):
    """A clear, styled placeholder for a missing/empty section."""
    return (f'<div class="ph"><b>Section placeholder</b> &mdash; {E(text)}</div>')

# ---------------------------------------------------------------------------
# Derived counts (all guarded)
# ---------------------------------------------------------------------------
N = len(recs) if recs else 0
ABS_IN  = sum(1 for r in ad_rows if (r.get("decision") or "").lower() == "included") if ad_rows else None
ABS_OUT = sum(1 for r in ad_rows if (r.get("decision") or "").lower() == "excluded") if ad_rows else None
if isinstance(ft, dict):
    FT_SCR   = ft.get("screened"); FT_INC = len(ft.get("included", []) or []); FT_EXC = len(ft.get("excluded", []) or [])
    FT_AVAIL = ft.get("fulltext_available"); ABS_ONLY = ft.get("abstract_only")
else:
    FT_SCR = FT_INC = FT_EXC = FT_AVAIL = ABS_ONLY = None
N_MATRIX = len(matrix) if matrix else 0
N_AUDIT  = len(audit) if audit else 0

# ---------------------------------------------------------------------------
# PDFs on disk
# ---------------------------------------------------------------------------
_SKILL_ROOT = ROOT.rsplit("/reviews/", 1)[0] if "/reviews/" in ROOT else os.path.dirname(ROOT)
def resolve(pth):
    if not pth:
        return None
    if os.path.isabs(pth) and os.path.exists(pth):
        return pth
    # manifest paths may be relative to the review root OR the skill root
    # (they embed a "reviews/{topic}/..." prefix).
    for base in (ROOT, _SKILL_ROOT, os.getcwd()):
        c = os.path.join(base, pth)
        if os.path.exists(c):
            return c
    return None

pdfs, _seen = [], set()
for e in manifest or []:
    if not isinstance(e, dict) or not e.get("path"):
        continue
    rp = resolve(e["path"])
    if not rp or rp in _seen:
        continue
    _seen.add(rp)
    pdfs.append({"idx": e.get("record_index"), "path": rp,
                 "title": e.get("title"), "size": os.path.getsize(rp)})
pdfs.sort(key=lambda x: (x["idx"] is None, x["idx"]))
NPDF = len(pdfs)
pdf_ids = {p["idx"] for p in pdfs if p["idx"] is not None}
manifest_missing = sum(1 for e in manifest if isinstance(e, dict) and not e.get("path")) if manifest else 0

# ---------------------------------------------------------------------------
# Evidence-matrix helpers (column-agnostic)
# ---------------------------------------------------------------------------
def _cell(row, col):
    v = row.get(col, "") if isinstance(row, dict) else ""
    return (v or "").strip()

if matrix:
    COLS = [c for c in matrix[0].keys() if c and c.lower() != "nan"]
    HAS = {c: any((r.get(c) or "").strip() for r in matrix) for c in COLS}
else:
    COLS, HAS = {}, {}
    HAS = {}

def has(col):
    return bool(matrix) and col in matrix[0] and HAS.get(col)

def cell(col, default=""):
    return HAS.get(col, default)

# map matrix row i -> record index (matrix built by iterating ft["included"])
row_idx = {}
_incl = (ft or {}).get("included", []) if isinstance(ft, dict) else []
for _i, _r in enumerate(matrix or []):
    if _i < len(_incl) and isinstance(_incl[_i], int):
        row_idx[id(_r)] = _incl[_i]

def rec_by_idx(iid):
    if isinstance(iid, int) and 0 <= iid < len(recs):
        return recs[iid]
    return {}

def _author_cell(r):
    name = E(_cell(r, "first_author") or "—")
    iid = row_idx.get(id(r))
    rec = rec_by_idx(iid)
    title = (recget(rec, "title") or "").strip()
    d = (recget(rec, "doi") or "").strip()
    pm = (recget(rec, "pmid") or "").strip()
    url = ""
    if d:
        url = ("https://doi.org/" + d) if d.startswith("10.") else d if d.startswith("http") else ""
    elif pm:
        url = "https://www.ncbi.nlm.nih.gov/pubmed/" + pm
    sid = _sid_cell(r)
    if url and title:
        return (f'<td class="auth"><div>{name}</div>'
                f'<a class="ttl" href="{E(url)}" target="_blank" rel="noopener" '
                f'title="Open the full text (publisher)">{E(title)}</a></td>')
    if url:
        return f'<td class="auth"><div>{name}</div><a class="ttl" href="{E(url)}" target="_blank" rel="noopener">Open publisher page</a></td>'
    return f'<td class="auth"><div>{name}</div><span class="ttl-none">{E(title) or "&mdash;"}</span></td>'

def _sid_cell(r):
    iid = row_idx.get(id(r))
    if iid is None:
        return '<span class="sid-dim">&#8212;</span>'
    if iid in pdf_ids:
        return f'<a class="sid-link" href="#p-{iid}" title="Jump to this study&#39;s full-text card">#{iid}</a>'
    return f'<span class="sid" title="Record index in deduplicated_records.json">#{iid}</span>'

def _doi_cell(r):
    """DOI column: a short 'DOI' link on line 1 + the study title & first author on line 2."""
    d = _cell(r, "doi"); pm = _cell(r, "pmid")
    title = _cell(r, "title") or _cell(r, "paper_title") or _cell(r, "name")
    author = _cell(r, "first_author") or _cell(r, "author") or _cell(r, "authors")
    def _first_author(authors):
        if not authors:
            return ""
        a = authors[0] if isinstance(authors, (list, tuple)) else authors
        if isinstance(a, dict):
            if isinstance(a.get("author"), dict):
                return a["author"].get("display_name") or ""
            return a.get("display_name") or a.get("name") or ""
        return str(a)
    rr = rec_by_idx(row_idx.get(id(r)))
    d = (d or recget(rr, "doi") or "").strip()
    pm = (pm or recget(rr, "pmid") or "").strip()
    title = (title or recget(rr, "title") or "").strip()
    author = (author or _first_author(recget(rr, "authors")) or "").strip()
    if d:
        if d.startswith("10."):
            d = "https://doi.org/" + d
        elif not d.startswith("http"):
            d = ""
    link = ""
    if d:
        link = (f'<a class="doi-link" href="{E(d)}" target="_blank" rel="noopener" '
                f'title="{E(d)}">DOI</a>')
    elif pm:
        pmid_num = pm.split("/")[-1].strip() if "/" in pm else pm
        link = (f'<a class="doi-link" href="https://www.ncbi.nlm.nih.gov/pubmed/{E(pmid_num)}" '
                f'target="_blank" rel="noopener" title="PubMed">PubMed</a>')
    else:
        link = '<span class="sid-dim">&mdash;</span>'
    sub = ""
    if title or author:
        t = (title or "").strip()
        if len(t) > 90:
            t = t[:88] + "…"
        sub = (f'<span class="doi-sub">{E(t)}</span>'
               + (f'<span class="doi-auth">{E(author)}</span>' if author else ""))
    return link + sub

# ---------------------------------------------------------------------------
# Generic metrics: detect numeric metric column(s) & categorical column(s)
# ---------------------------------------------------------------------------
def _num(v):
    try:
        return float(str(v).strip().rstrip("%").rstrip("."))
    except Exception:
        return None

METRIC_COLS = []
if matrix:
    _id_cols = ("year", "doi", "pmid", "source", "index", "id", "first_author", "record_index", "openalex_id")
    for c in COLS:
        if c.lower() in _id_cols or "index" in c.lower() or c.lower().endswith("_id"):
            continue
        vals = [_num(r.get(c)) for r in matrix]
        vals = [x for x in vals if x is not None]
        if len(vals) >= 3:
            METRIC_COLS.append((c, vals))
METRIC_COLS.sort(key=lambda kv: -len(kv[1]))

def _numvals(c):
    out = []
    for r in matrix or []:
        x = _num(r.get(c))
        if x is not None:
            out.append(x)
    return out

CAT_COLS = []
if matrix:
    for c in COLS:
        if c.lower() in ("year", "doi", "pmid", "source", "index", "id"):
            continue
        vals = [_cell(r, c).lower() for r in matrix if _cell(r, c)]
        if not vals:
            continue
        uniq = len(set(vals))
        if 2 <= uniq <= 24 and uniq / len(matrix) < 0.9 and all(len(v) < 80 for v in set(vals)):
            CAT_COLS.append(c)
CAT_COLS = CAT_COLS[:6]

def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs)/n; my = sum(ys)/n
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    vx = sum((xs[i]-mx)**2 for i in range(n)); vy = sum((ys[i]-my)**2 for i in range(n))
    if vx == 0 or vy == 0:
        return None
    r_ = cov / ((vx*vy)**0.5)
    slope = cov / vx; intercept = my - slope * mx
    return {"r": r_, "slope": slope, "intercept": intercept, "n": n}

# ---------------------------------------------------------------------------
# SVG chart builders (self-contained, no external JS)
# ---------------------------------------------------------------------------
def _nice(v):
    a = abs(v)
    if a >= 1000: return str(int(round(v)))
    if abs(v - round(v)) < 1e-9: return str(int(v))
    if a >= 1: return f"{v:.0f}"
    return f"{v:.3f}".rstrip("0").rstrip(".")

def _vbars(items, w=340, h=210, xlab=""):
    items = [(str(l), float(v)) for l, v in items]
    n = len(items)
    if n == 0: return ""
    pl, pr, pt, pb = 34, 12, 22, 44
    pw, ph = w - pl - pr, h - pt - pb
    vmax = max(v for _, v in items) or 1
    bw = pw / n
    out = [f'<svg class="stat-svg" viewBox="0 0 {w} {h}" role="img" aria-hidden="true">']
    for i in range(5):
        yy = pt + ph * (1 - i / 4)
        out.append(f'<line class="grid" x1="{pl}" y1="{yy:.1f}" x2="{w-pr}" y2="{yy:.1f}"/>')
        out.append(f'<text class="axis" x="{pl-5}" y="{yy+3:.1f}" text-anchor="end">{_nice(vmax*i/4)}</text>')
    for idx, (lab, val) in enumerate(items):
        bh = ph * (val / vmax) if vmax else 0
        x0 = pl + idx * bw
        xw = max(8, bw * 0.6)
        xx = x0 + (bw - xw) / 2
        yy = pt + ph - bh
        out.append(f'<rect class="bar" x="{xx:.1f}" y="{yy:.1f}" width="{xw:.1f}" height="{max(0,bh):.1f}" rx="2" data-tip="{E(lab)}: {val:g}"/>')
        out.append(f'<text class="val" x="{xx+xw/2:.1f}" y="{yy-3:.1f}" text-anchor="middle">{val:g}</text>')
        if idx == n - 1 or idx % max(1, n // 6) == 0:
            labd = lab if len(lab) <= 12 else lab[:11] + "…"
            out.append(f'<text class="cat" x="{xx+xw/2:.1f}" y="{h-pb+16}" text-anchor="middle">{E(labd)}</text>')
    out.append(f'<text class="axis" x="{pl+pw/2}" y="{h-4}" text-anchor="middle">{E(xlab)}</text>')
    out.append('</svg>')
    return "".join(out)

def _hbars(counter, top=None, w=340, unit="studies"):
    items = counter.most_common()
    if top: items = items[:top]
    items = [(str(l), float(v)) for l, v in items if v > 0]
    if not items: return ""
    vmax = max(v for _, v in items) or 1
    row_h = 27
    h = len(items) * row_h + 4
    lab_w = 130
    bar_x = lab_w + 8
    val_w = 44
    max_bar = w - bar_x - val_w
    out = [f'<svg class="stat-svg" viewBox="0 0 {w} {h}" role="img" aria-hidden="true">']
    for idx, (lab, val) in enumerate(items):
        y = idx * row_h + 2
        bw = max(4, max_bar * (val / vmax))
        labd = lab if len(lab) <= 22 else lab[:21] + "…"
        out.append(f'<text class="cat" x="0" y="{y+15}">{E(labd)}</text>')
        out.append(f'<rect class="bar" x="{bar_x}" y="{y+3}" width="{bw:.1f}" height="{row_h-11}" rx="3" data-tip="{E(lab)}: {val:g} {unit}"/>')
        out.append(f'<text class="val" x="{w-4}" y="{y+15}" text-anchor="end">{val:g}</text>')
    out.append('</svg>')
    return "".join(out)

def _vbars_year(items, w=360, h=230, xlab=""):
    yrs = sorted({int(l) for l, _ in items})
    if not yrs: return ""
    ymin, ymax = min(yrs), max(yrs)
    span = max(1, ymax - ymin)
    pl, pr, pt, pb = 30, 12, 18, 52
    pw, ph = w - pl - pr, h - pt - pb
    vmax = max(int(v) for _, v in items) or 1
    def X(y): return pl + pw * ((y - ymin) / span)
    def Xc(y):
        lo, hi = pl + 4, w - pr - 4
        return min(max(X(y), lo), hi)
    bw = min(10, (pw / span) * 0.7)
    out = [f'<svg class="stat-svg" viewBox="0 0 {w} {h}" role="img" aria-hidden="true">']
    for i in range(5):
        yy = pt + ph * (1 - i / 4)
        out.append(f'<line class="grid" x1="{pl}" y1="{yy:.1f}" x2="{w-pr}" y2="{yy:.1f}"/>')
        out.append(f'<text class="axis" x="{pl-5}" y="{yy+3:.1f}" text-anchor="end">{int(round(vmax*i/4))}</text>')
    t0 = (ymin // 5 + 1) * 5
    for ty in range(t0, ymax + 1, 5):
        tx = X(ty)
        out.append(f'<line class="grid" x1="{tx:.1f}" y1="{pt}" x2="{tx:.1f}" y2="{pt+ph}"/>')
    for (lab, val) in items:
        y = int(lab); val = int(val)
        cx = Xc(y); bh = ph * (val / vmax) if vmax else 0
        xx = cx - bw / 2; yy = pt + ph - bh
        out.append(f'<rect class="bar" x="{xx:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="{max(0,bh):.1f}" rx="1.5" data-tip="{lab}: {val} studies"/>')
        if val > 0:
            out.append(f'<text class="val" x="{cx:.1f}" y="{yy-3:.1f}" text-anchor="middle" style="font-size:9px">{val}</text>')
    for ty in range(t0, ymax + 1, 5):
        tx = X(ty)
        out.append(f'<text class="cat" x="{tx:.1f}" y="{h-pb+14}" text-anchor="end" style="font-size:9px" transform="rotate(-90 {tx:.1f} {h-pb+14})">{ty}</text>')
    out.append(f'<line class="tick" x1="{pl}" y1="{pt+ph}" x2="{w-pr}" y2="{pt+ph}"/>')
    out.append(f'<text class="axis" x="{pl+pw/2}" y="{h-2}" text-anchor="middle">{E(xlab)}</text>')
    out.append('</svg>')
    return "".join(out)

def _scatter(pts, xk, yk, xlabel, ylabel, w=360, h=280, stat=None, default_cls="dot"):
    if len(pts) < 2:
        return ph("Not enough numeric data to plot this relationship.")
    pl, pr, pt, pb = 44, 16, 18, 40
    pw, ph = w - pl - pr, h - pt - pb
    xs = [p[xk] for p in pts]; ys = [p[yk] for p in pts]
    xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
    spx = (xmax - xmin) or 1; spy = (ymax - ymin) or 1
    xmin -= spx * 0.07; xmax += spx * 0.07; ymin -= spy * 0.09; ymax += spy * 0.09
    SX = pw / ((xmax - xmin) or 1); SY = ph / ((ymax - ymin) or 1)
    def X(v): return pl + (v - xmin) * SX
    def Y(v): return pt + (1 - (v - ymin) / ((ymax - ymin) or 1)) * ph
    out = [f'<svg class="stat-svg" viewBox="0 0 {w} {h}" role="img" aria-hidden="true">']
    for i in range(5):
        gx = xmin + (xmax - xmin) * i / 4
        out.append(f'<line class="grid" x1="{X(gx):.1f}" y1="{pt}" x2="{X(gx):.1f}" y2="{pt+ph}"/>')
        out.append(f'<text class="axis" x="{X(gx):.1f}" y="{h-pb+16}" text-anchor="middle">{_nice(gx)}</text>')
        gy = ymin + (ymax - ymin) * i / 4
        out.append(f'<line class="grid" x1="{pl}" y1="{Y(gy):.1f}" x2="{pl}" y2="{Y(gy):.1f}"/>')
        out.append(f'<line class="grid" x1="{pl}" y1="{Y(gy):.1f}" x2="{pl+pw}" y2="{Y(gy):.1f}"/>')
        out.append(f'<text class="axis" x="{pl-5}" y="{Y(gy)+3:.1f}" text-anchor="end">{_nice(gy)}</text>')
    for p in pts:
        cx = X(p[xk]); cy = Y(p[yk])
        tip = p.get("tip") or f'{p.get("who","study")}: {p[xk]}, {p[yk]:.3f}'
        cls = p.get("cls") or default_cls
        out.append(f'<circle class="{cls}" cx="{cx:.1f}" cy="{cy:.1f}" r="3.6" data-tip="{E(tip)}"/>')
    if stat:
        x1 = xmin + (xmax - xmin) * 0.04; x2 = xmax - (xmax - xmin) * 0.04
        y1 = min(max(stat["slope"]*x1+stat["intercept"], ymin), ymax)
        y2 = min(max(stat["slope"]*x2+stat["intercept"], ymin), ymax)
        out.append(f'<line class="reg" x1="{X(x1):.1f}" y1="{Y(y1):.1f}" x2="{X(x2):.1f}" y2="{Y(y2):.1f}"/>')
        out.append(f'<text class="reglbl" x="{pl+pw-2}" y="{pt+12}" text-anchor="end">r = {stat["r"]:+.3f}</text>')
        out.append(f'<text class="r2" x="{pl+pw-2}" y="{pt+26}" text-anchor="end">R² = {stat["r"]**2:.3f} · n = {stat["n"]}</text>')
    out.append(f'<text class="axis" x="{pl+pw/2}" y="{h-3}" text-anchor="middle">{E(xlabel)}</text>')
    out.append(f'<text class="axis" transform="rotate(-90 11 {pt+ph/2})" x="11" y="{pt+ph/2}" text-anchor="middle">{E(ylabel)}</text>')
    out.append('</svg>')
    return "".join(out)

# ---------------------------------------------------------------------------
# Build the data structures each section needs (all guarded)
# ---------------------------------------------------------------------------
# Artifact tree (only rows whose file actually exists; note the rest)
TREE_ALL = [
    ("protocol.json", "Framework, criteria & research question"),
    ("audit_trail/lab_notebook.jsonl", "Immutable append-only execution ledger"),
    ("data/deduplicated_records.json", "De-duplicated record corpus"),
    ("screening/abstract_decisions.csv", "Title/abstract include-exclude decisions"),
    ("screening/fulltext_exclusion_log.json", "Full-text exclusions + rationales"),
    ("pdfs/manifest.json", "Full-text PDF manifest (hashes, sizes)"),
    ("synthesis/evidence_matrix.csv", "Structured extraction matrix"),
    ("report/systematic_review_draft.md", "Full PRISMA manuscript (Markdown)"),
    ("report/systematic_review_draft.tex", "LaTeX manuscript"),
    ("report/prisma_checklist.md", "PRISMA checklist"),
    ("report/prisma_flow.mmd", "PRISMA flow diagram (Mermaid source)"),
    ("report/PRISMA_flowchart.svg", "PRISMA flow diagram (vector)"),
    ("report/PRISMA_flowchart.png", "PRISMA flow diagram (raster)"),
    ("report/index.html", "< You are here"),
]
tree_rows = [(rel, desc) for rel, desc in TREE_ALL if _exists(rel.split("/"))]
tree_missing = [rel for rel, _ in TREE_ALL if not _exists(rel.split("/")) and not rel.endswith("index.html")]
tree_html = "".join(
    f'<li><span class="tl">{E(rel)}</span><span class="td">{E(desc)}</span>'
    f'<a class="fl" href="{viewlink(rel)}" target="_blank" rel="noopener">open</a></li>'
    for rel, desc in tree_rows)
if tree_missing:
    tree_html += (f'<li style="background:#151b2b"><span class="tl" style="color:var(--mut)">…</span>'
                  f'<span class="td">{E(len(tree_missing))} more file(s) not yet generated '
                  f'(e.g. {E(tree_missing[0])})</span>'
                  f'<span class="fl" style="opacity:.5;pointer-events:none">pending</span></li>')

# (evidence-matrix rows are built after the panel config / PRIMARY_COL is known)

# stats
year_c = Counter()
if matrix and has("year"):
    for r in matrix:
        y = _cell(r, "year")
        if y and y.strip().isdigit():
            year_c[int(y)] += 1
dist_year = _vbars_year(sorted(year_c.items()), xlab="Year of publication") if year_c else ""

def _counter(col):
    return Counter((_cell(r, col) or "unspecified") for r in matrix if (r.get(col) or "").strip()) if matrix else Counter()

def _cap_n(counter, n=5):
    """Fold a long tail of categories into 'Other' so a chart stays legible."""
    if not counter or len(counter) <= n:
        return counter
    top = counter.most_common(n - 1)          # list of (label, count) pairs
    others = sum(counter.values()) - sum(v for _, v in top)
    out = Counter()
    for k, v in top:
        out[k] = v
    out["Other"] += others
    return out

# --- Per-review chart configuration ----------------------------------------
# Which matrix columns to summarise, the panel title for each, and the chart
# type ("hbars" = categorical breakdown, "count" = how many ;-delimited items
# each study reports). A review may override ANY of this in portal_config.json
# (stored in ITS OWN workspace dir, not the skill). With no config, the
# generator auto-detects the categorical columns and gives them generic titles
# so a brand-new topic still renders sensibly.
_DEFAULT_PANELS = [
    ("cof_type",        "Material / system type",   "hbars"),
    ("tumor_model",     "Tumor model",              "hbars"),
    ("therapy_mode",    "Therapy / treatment mode", "hbars"),
    ("immune_mechanism","Immune mechanism",         "hbars"),
    ("in_vivo",         "In-vivo evidence",         "hbars"),
    ("study_design",    "Study design",             "hbars"),
    ("speech_task",     "Speech task",              "hbars"),
    ("acoustic_features","Acoustic features",       "hbars"),
    ("method",          "Analysis method",          "hbars"),
    ("outcome",         "Outcome reported",         "hbars"),
]
_NONPANEL = {"year","doi","pmid","source","index","id","record_index","openalex_id",
             "first_author","metric_value","title","recorded_as"}
def _guess_title(col):
    for dcol, dtitle, _ in _DEFAULT_PANELS:
        if dcol == col:
            return dtitle
    return (re.sub(r"[_\-]+", " ", col).strip().title() or col)
def _norm_panels(raw):
    out = []
    for it in raw or []:
        if isinstance(it, str):
            out.append([it, "", "", None])
        elif isinstance(it, (list, tuple)):
            it = list(it) + ["", "", None]
            out.append([str(it[0]), str(it[1]), str(it[2]),
                        (None if it[3] in (None, "") else int(it[3]))])
        elif isinstance(it, dict):
            out.append([str(it.get("column") or it.get("col")),
                        str(it.get("title") or it.get("label")),
                        str(it.get("type") or "hbars"),
                        (None if it.get("cap") in (None, "") else int(it.get("cap")))])
    return out

# Auto-detect (no config): multi-item columns (";"-separated lists) default to
# the "family" type (dominant token, capped) so long verbatim bars don't appear;
# single-value columns use plain categorical bars.
def _is_multi(col):
    return sum(1 for r in matrix if ";" in (_cell(r, col) or "")) >= 3

_panels = []
if cfg.get("chart_panels") is not None:
    _panels = _norm_panels(cfg["chart_panels"])
elif matrix:
    for col in COLS:
        lc = col.lower()
        if lc in _NONPANEL or lc.endswith("_id") or "index" in lc:
            continue
        if not HAS.get(col):
            continue
        ctype = "family" if _is_multi(col) else "hbars"
        _panels.append([col, _guess_title(col), ctype, None])

# Panel types:
#   "hbars"  – categorical breakdown (each unique value = a bar)
#   "count"  – how many ;-delimited items each study reports
#   "family" – collapse each cell to its *dominant* (first) token, fold via the
#              config's "family_synonyms" map, cap to N classes (default 5).
#              Reproduces the MDD "5 acoustic-feature families" chart while
#              staying fully config-driven (any topic can use it).
_family_syn = {k.lower(): v for k, v in (cfg.get("family_synonyms") or {}).items()}
def _family_of(r, col):
    raw = (_cell(r, col) or "").strip()
    if not raw or raw.lower() in ("unspecified", "n/a", "-", ""):
        return "unspecified"
    first = raw.split(";")[0].strip().lower()
    return _family_syn.get(first, first)

# Counters for each configured panel (capped per-panel for legibility).
panel_counters = {}
for col, title, ctype, cap in _panels:
    if not has(col):
        continue
    if ctype == "count":
        def _nitems(r, c=col):
            f = _cell(r, c).lower()
            if not f or f in ("unspecified", "n/a", "-"):
                return 1
            return max(1, len([p for p in f.split(";") if p.strip()]))
        c = Counter(_nitems(r) for r in matrix)
    elif ctype == "family":
        c = _cap_n(Counter(_family_of(r, col) for r in matrix), cap or 5)
    else:
        c = _counter(col)
        if cap:
            c = _cap_n(c, cap)
    if c:
        panel_counters[col] = c

# The "primary" axis = the first configured panel with data. It feeds the
# "Dominant X" bar chart and the two correlation scatter plots.
PRIMARY_COL = PRIMARY_TITLE = None
PRIMARY_C = Counter()
for col, title, ctype, cap in _panels:
    if col in panel_counters:
        PRIMARY_COL, PRIMARY_TITLE = col, (title or _guess_title(col))
        PRIMARY_C = panel_counters[col]
        break
# Config may explicitly override which column is the "primary" axis.
_pa = cfg.get("primary_axis")
if isinstance(_pa, dict) and _pa.get("column") in panel_counters:
    PRIMARY_COL = _pa["column"]
    PRIMARY_TITLE = _pa.get("title") or _guess_title(_pa["column"])
    PRIMARY_C = panel_counters[PRIMARY_COL]
elif isinstance(_pa, str) and _pa in panel_counters:
    PRIMARY_COL = _pa
    PRIMARY_TITLE = _guess_title(_pa)
    PRIMARY_C = panel_counters[PRIMARY_COL]
# Legacy aliases so downstream code keeps working (MDD used 'acoustic_features').
feat_c = PRIMARY_C if PRIMARY_COL else Counter()
feat_bars = _hbars(feat_c) if feat_c else ""
rec_as_c = _counter("recorded_as")
dist_recas = _hbars(rec_as_c, unit="studies") if rec_as_c else ""

# scatter: year -> primary-axis item count  (e.g. year -> #features / #mechanisms)
scat_year = ""; stat_year_feat = None
if matrix and has("year") and PRIMARY_COL:
    def _nitems(r, c=PRIMARY_COL):
        f = _cell(r, c).lower()
        if not f or f in ("unspecified", "n/a", "-"):
            return 1
        return max(1, len([p for p in f.split(";") if p.strip()]))
    ypts = []
    for r in matrix:
        y = _num(_cell(r, "year"))
        if y is None: continue
        nval = _nitems(r)
        ypts.append({"x": y, "y": max(1, nval), "who": _cell(r, "first_author")})
    if ypts:
        yxs = [p["x"] for p in ypts]; yys = [p["y"] for p in ypts]
        stat_year_feat = _pearson(yxs, yys)
        for p in ypts:
            p["tip"] = f'{p["who"] or "study"} — {int(p["x"])}: {p["y"]} item(s)'
        scat_year = _scatter(ypts, "x", "y", "Year", "items reported", stat=stat_year_feat, default_cls="dot acc")

# scatter: primary-axis item count -> reported numeric effect
scat_metric = ""; stat_feat_metric = None
if matrix and PRIMARY_COL and METRIC_COLS:
    mcol = METRIC_COLS[0][0]
    def _nitems(r, c=PRIMARY_COL):
        f = _cell(r, c).lower()
        if not f or f in ("unspecified", "n/a", "-"):
            return 1
        return max(1, len([p for p in f.split(";") if p.strip()]))
    fpts = []
    for r in matrix:
        mv = _num(r.get(mcol))
        if mv is None: continue
        nval = _nitems(r)
        fpts.append({"x": nval, "y": mv, "who": _cell(r, "first_author"),
                     "tip": f'{_cell(r,"first_author") or "study"} — {nval} item(s) · {E(_cell(r, mcol))}'})
    if fpts:
        stat_feat_metric = _pearson([p["x"] for p in fpts], [p["y"] for p in fpts])
        scat_metric = _scatter(fpts, "x", "y", "items reported", mcol, stat=stat_feat_metric)

# numeric metric summary (mean of the richest numeric column)
METRIC_SUMMARY = None
if METRIC_COLS:
    mcol, mvals = METRIC_COLS[0]
    METRIC_SUMMARY = {"col": mcol, "n": len(mvals), "mean": sum(mvals)/len(mvals)}

# PDF cards (generic 3-line digest) — uses the configured primary column so the
# "key findings" line is topic-aware (e.g. immune mechanism for COF, features for MDD).
def _pdf_summary(idx, title):
    rec = rec_by_idx(idx)
    r = None
    if matrix:
        r = next((mr for mr in matrix if (mr.get("doi") or "") and (mr.get("doi") or "") == recget(rec, "doi")), None)
    def _c(col):
        return (_cell(r, col) if r else "").strip()
    design  = _c("study_design") or _c("tumor_model") or _c("cof_type")
    method  = _c("method") or _c("therapy_mode")
    outcome = _c("outcome")
    feats   = _c(PRIMARY_COL) if PRIMARY_COL else ""
    metric  = _c("metric_value")
    full    = read_text(["fulltext", f"study_{idx:03d}.txt"], 20000) if isinstance(idx, int) else ""
    a = f"{design or 'Primary study'}" + (f" — {method}" if method else "") + "."
    if metric:
        b = f"Reports {outcome or 'a key outcome'} of {metric}" + (f"; dominant: {feats.split(';')[0].strip()}." if feats else ".")
    elif feats:
        b = f"Focus: {feats}; outcome: {outcome or 'not reported'} (metric not stated in source)."
    else:
        b = f"Outcome: {outcome or 'not reported in the available source'}."
    num = re.search(r"\b(accuracy|AUC|sensitivity|specificity|F1|kappa)\b[^0-9]{0,15}([0-9]{1,3}(\.[0-9]+)?|0\.[0-9]+)\s*%?", full, re.I)
    if num:
        c = f"Capstone takeaway: reproducible signal — {num.group(1)} ~ {num.group(2)}% in this cohort; a solid baseline to reproduce."
    elif feats:
        c = f"Capstone takeaway: {feats.split(';')[0].strip()} is the natural entry point for a replication project."
    else:
        c = "Capstone takeaway: a readable, citable example of the full record → extract → classify → validate pipeline."
    return a, b, c

pdf_html = ""
for p in pdfs:
    a, b, c = _pdf_summary(p["idx"], p["title"])
    pdf_html += (f'<div class="pdf-card" id="p-{p["idx"]}"><div class="pdf-head"><b>#{p["idx"]}</b> {E(p["title"] or "Untitled")}'
                 f'<span class="pdf-size">{p["size"]//1024} KB</span></div>'
                 f'<a class="pdf-link" href="pdfs/{os.path.basename(p["path"])}">Open PDF</a>'
                 f'<ul><li><b>Design &amp; sample:</b> {E(a)}</li><li><b>Key findings:</b> {E(b)}</li>'
                 f'<li><b>Undergraduate takeaway:</b> {E(c)}</li></ul></div>')

# inclusion / exclusion / audit / included-list
inclusion_html = "".join(f"<li>{E(c)}</li>" for c in (proto or {}).get("inclusion_criteria", [])) or "<li>(no inclusion criteria recorded yet)</li>"
exclusion_html = "".join(f"<li>{E(c)}</li>" for c in (proto or {}).get("exclusion_criteria", [])) or "<li>(no exclusion criteria recorded yet)</li>"
excl_html = "".join(
    f'<li><b>#{e.get("index")}</b> {E(e.get("title"))} — {E(e.get("reason"))}</li>'
    for e in (ft or {}).get("excluded", []) if isinstance(e, dict))
audit_html = "".join(
    f'<tr><td>{E(e.get("timestamp"))[:19]}</td><td>{E(e.get("step"))}</td>'
    f'<td class="st-{E(e.get("status")).lower()}">{E(e.get("status"))}</td><td>{E(e.get("action"))[:120]}</td></tr>'
    for e in (audit or []))
inc_html = "".join(
    f'<li><b>#{idx}</b> {E(recget(rec_by_idx(idx), "title") or f"Record #{idx}")}{(" — " + E(recget(rec_by_idx(idx), "doi"))) if recget(rec_by_idx(idx), "doi") else ""}</li>'
    for idx in (ft or {}).get("included", []) if isinstance(idx, int))

# PICO (or other framework) rows
_framework = ((proto or {}).get("framework") or "PICO").upper()
_pico_letters = {"PICO": "PICO", "PECO": "PECO", "SPIDER": "SPIDER", "CIMO": "CIMO"}
_pico_labels = {
    "P": "Population", "I": "Intervention / Exposure", "C": "Comparator / Control", "O": "Outcome",
    "E": "Expectation", "S": "Setting / Sample", "D": "Design", "R": "Reporting / Research type",
    "M": "Mechanism",
}
def _pico_rows():
    dims = (proto or {}).get("framework_dimensions") or []
    incs = (proto or {}).get("inclusion_criteria", []) or []
    letters = list(_pico_labels.keys())[:len(dims)]
    rows = []
    for i, dim in enumerate(dims):
        letter = letters[i] if i < len(letters) else dim[0].upper() if dim else "•"
        text = incs[i] if i < len(incs) else "—"
        rows.append(f'<div class="row"><div class="tag {letter}">{letter}</div><div><div class="lab">{E(dim)}</div><div class="txt">{E(text)}</div></div></div>')
    return "".join(rows) if rows else "<p class='sub'>Framework dimensions are defined in <span class='mono'>protocol.json</span> once the protocol step runs.</p>"

# topic / badges
TOPIC = (proto or {}).get("topic") or ""
FRAMEWORK = _framework
DBS = (proto or {}).get("databases") or []
badges = [f'<span class="badge">Framework: {E(FRAMEWORK)}</span>']
if DBS:
    badges.append(f'<span class="badge">Databases: {E(" + ".join(DBS))}</span>')
if N: badges.append(f'<span class="badge">{N} records identified</span>')
if isinstance(FT_INC, int): badges.append(f'<span class="badge">{FT_INC} studies included</span>')
if N_AUDIT: badges.append(f'<span class="badge">{N_AUDIT} audit entries</span>')
badges_html = "".join(badges)

# exec cards (skip the ones with no data)
_cards = []
for val, label in [(N,"Records identified"), (ABS_IN,"Abstract-included"), (FT_INC,"Final included"),
                   (FT_AVAIL,"Full texts retrieved"), (N_MATRIX,"Evidence rows"),
                   (NPDF,"PDFs on disk"), (N_AUDIT,"Audit entries"), (ABS_ONLY,"Abstract-only studies")]:
    if isinstance(val, int) and val is not None:
        _cards.append(f'<div class="card"><div class="card-n">{val}</div><div class="card-l">{label}</div></div>')
cards_html = "".join(_cards)
# ---- Headline stats (generic, defensive) ---------------------------------
# A review can declare its exact headline chips in portal_config.json
# (headline_chips: [{label, value, sub}]). Without that, we reconstruct
# generic, topic-appropriate numbers from the evidence matrix:
#   * AUC/ROC & accuracy  -> opt-in via config "metrics": ["auc","accuracy"]
#                            (most topics — e.g. COF immunotherapy — don't
#                            report classifier metrics, so they're omitted)
#   * Dominant <primary>  -> top value of the configured primary axis
#   * Recent (2023+)      -> studies with year >= 2023
# Each stat is omitted when the data isn't there, so this works for any review.
def _num01(v):
    x = _num(v)
    if x is None: return None
    return x if x <= 1.0 else x / 100.0          # AUC-style: force onto 0–1
def _numpct(v):
    x = _num(v)
    if x is None: return None
    return x * 100.0 if x <= 1.0 else x          # accuracy-style: force onto %
def _outcome_match(r, *kws):
    oc = (_cell(r, "outcome") or "").lower()
    return any(k in oc for k in kws)
_metrics = cfg.get("metrics") or []
_head = []
_AUC_MEAN = _AUC_K = None
_ACC_MEAN = _ACC_K = None
if matrix:
    if "auc" in _metrics or "roc" in _metrics:
        auc = [_num01(r.get("metric_value")) for r in matrix if _outcome_match(r, "auc", "roc") and _num01(r.get("metric_value")) is not None]
        if auc:
            _AUC_MEAN, _AUC_K = sum(auc)/len(auc), len(auc)
            _head.append(("Mean AUC/ROC", f"{_AUC_MEAN:.3f}", f"k={_AUC_K}"))
    if "accuracy" in _metrics or "acc" in _metrics:
        acc = [_numpct(r.get("metric_value")) for r in matrix if _outcome_match(r, "accuracy", "acc") and not _outcome_match(r, "auc") and _numpct(r.get("metric_value")) is not None]
        if acc:
            _ACC_MEAN, _ACC_K = sum(acc)/len(acc), len(acc)
            _head.append(("Mean accuracy", f"{_ACC_MEAN:.1f}%", f"k={_ACC_K}"))
    # Dominant <primary axis> (top value of the configured primary column).
    # Skip the folded "Other" / "unspecified" buckets so the chip names a real
    # category, not a catch-all tail.
    if PRIMARY_C:
        for top_lab, top_n in PRIMARY_C.most_common():
            if top_lab.lower() in ("other", "unspecified", "n/a", "-", "not specified", "unspecified / n/a"):
                continue
            _head.append((f"Dominant {PRIMARY_TITLE.lower()}", top_lab, f"{top_n}/{N_MATRIX}"))
            break
    # Recent (2023+) studies
    recent = [r for r in matrix if (_num(_cell(r, "year")) or 0) >= 2023]
    if recent:
        _head.append(("2023+ studies", len(recent), f"of {N_MATRIX}"))
# A review may fully override the chips with explicit values.
if cfg.get("headline_chips"):
    _head = [(str(c.get("label")), str(c.get("value")), str(c.get("sub", ""))) for c in cfg["headline_chips"]]
if _head:
    chips_html = "".join(
        f'<span class="chip-metric"><span style="color:var(--mut);font-size:11px">{E(lab)} </span>'
        f'<b>{E(val)}</b> <span style="color:var(--mut);font-size:11px">({E(sub)})</span></span>'
        for lab, val, sub in _head)
elif METRIC_SUMMARY:
    chips_html = (f'<span class="chip-metric">Mean {E(METRIC_SUMMARY["col"])} <b>{METRIC_SUMMARY["mean"]:.3f}</b> '
                  f'<span style="color:var(--mut);font-size:11px">(k={METRIC_SUMMARY["n"]})</span></span>')
else:
    chips_html = (f'<span class="chip-metric">{E(N_MATRIX)} studies extracted'
                  + (f' · {NPDF} full texts on disk</span>' if NPDF else '</span>'))

# ---- Section 3 "Summary effect sizes" panel (original style) ---------------
# AUC + accuracy chips (when configured) plus a "Reading the numbers" note.
# The note is taken from portal_config.json when present, otherwise a
# topic-neutral generic sentence. Degrades gracefully to the richest numeric
# column when no classifier metrics are configured.
if _AUC_MEAN is not None or _ACC_MEAN is not None:
    _se_chips = []
    if _AUC_MEAN is not None:
        _se_chips.append(f'<span class="chip-metric" style="text-align:left">Mean AUC/ROC <b>{_AUC_MEAN:.3f}</b> <span style="color:var(--mut);font-size:11px">(k={_AUC_K})</span></span>')
    if _ACC_MEAN is not None:
        _se_chips.append(f'<span class="chip-metric" style="text-align:left">Mean accuracy <b>{_ACC_MEAN:.1f}</b> <span style="color:var(--mut);font-size:11px">(k={_ACC_K})</span></span>')
    _se_sub = []
    if _AUC_MEAN is not None: _se_sub.append(f"{_AUC_K} studies reporting a numeric AUC")
    if _ACC_MEAN is not None: _se_sub.append(f"{_ACC_K} reporting accuracy")
    _se_note = cfg.get("summary_note") or (
        "Reading the numbers: heterogeneity is very high — treat these as "
        "<b>descriptive summaries</b>, not a single pooled effect.")
    summary_eff_panel = (f'<div class="panel"><h3>Summary effect sizes</h3>'
                         f'<p class="sub">Across the {" and ".join(_se_sub)}.</p>'
                         f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:6px">{" ".join(_se_chips)}</div>'
                         f'<p class="scatter-cap" style="margin-top:10px">{_se_note}</p></div>')
else:
    summary_eff_panel = ('<div class="panel"><h3>Summary effect sizes</h3>'
                         f'<p class="sub">Across the studies reporting a numeric metric.</p>'
                         + (f'<div style="display:flex;gap:14px;margin-top:6px"><div class="chip-metric" style="text-align:left">Mean {E(METRIC_SUMMARY["col"])} <b>{METRIC_SUMMARY["mean"]:.3f}</b> <span style="color:var(--mut);font-size:11px">(k={METRIC_SUMMARY["n"]})</span></div></div>' if METRIC_SUMMARY else ph("No numeric metric values found in the matrix."))
                         + (f'<p class="scatter-cap" style="margin-top:10px">{cfg.get("summary_note") or ""}</p>' if cfg.get("summary_note") else '')
                         + '</div>')

# PRISMA stage rows (guard each)
def _stage(label, val, cls=""):
    if isinstance(val, int):
        disp = f"&minus;{val}" if cls == "excl" else val
    else:
        disp = "—"
    return f'<div class="stg {cls}"><span class="lab">{label}</span><span class="val">{disp}</span></div>'
stages_html = "".join([
    _stage("Records identified", N),
    _stage("Screened at title/abstract", N if ABS_IN is not None else "—"),
    _stage("Excluded at abstract", ABS_OUT, "excl"),
    _stage("Full-text screened", FT_SCR),
    _stage("Excluded at full text (rationale logged)", FT_EXC, "excl"),
    _stage("Included in review", FT_INC, "final"),
    _stage("Open-access full texts fetched", FT_AVAIL),
])

# PRISMA figure placeholder (filled at the end)
prisma_note = ""
if not (_exists(["report", "PRISMA_flowchart.svg"]) or _exists(["report", "PRISMA_flowchart.png"])):
    if _exists(["report", "prisma_flow.mmd"]):
        prisma_note = "<p class='sub'>Figure not rendered yet — the Mermaid source is at <span class='mono'>report/prisma_flow.mmd</span>. Run the report step (mermaid render) to generate the SVG/PNG.</p>"
    else:
        prisma_note = ph("No PRISMA flow diagram found (report/prisma_flow.mmd is absent). Run the <b>report</b> step to generate it, then re-run <b>portal</b>.")

# honesty note (abstract-only bias)
honesty_note = ""
if isinstance(ABS_ONLY, int) and ABS_ONLY > 0:
    honesty_note = (f'<div class="note"><b>Honesty note:</b> {ABS_ONLY} of '
                    f'{FT_INC if isinstance(FT_INC,int) else N_MATRIX} included studies were screened from the <b>abstract</b> only, '
                    f'because their full text is paywalled or publisher-blocked (HTTP 403/404). This is a known selection bias and is '
                    f'flagged per-row in the evidence matrix (<span class="mono">recorded_as</span> column).</div>')

# year-range for stats caption
yr_min = min(year_c) if year_c else None
yr_max = max(year_c) if year_c else None
yr_recent3 = (year_c.get(yr_max,0)+year_c.get(yr_max-1,0)+year_c.get(yr_max-2,0)) if yr_max else 0

CSS = """
:root{--bg:#0b1220;--panel:#121b2e;--panel2:#0f172a;--ink:#e6ecf5;--mut:#9fb0c9;--acc:#5eead4;--acc2:#7dd3fc;--line:#243049;--good:#4ade80;--warn:#fbbf24;--bad:#f87171;--card:#16213b;--chip:#1e2a44;--th:var(--acc)}
body.light{--bg:#f3f6fc;--panel:#ffffff;--panel2:#eef3fb;--ink:#0b1220;--mut:#4b5a75;--acc:#0d9488;--acc2:#0284c7;--line:#c9d6ec;--good:#15803d;--warn:#b45309;--bad:#dc2626;--card:#f8fafd;--chip:#e6eefb;--th:#0d9488}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:radial-gradient(1200px 600px at 20% -10%,#13213d 0%,var(--bg) 55%);color:var(--ink);line-height:1.55}
html{scroll-behavior:smooth}
section{scroll-margin-top:20px}
.layout{max-width:1360px;margin:0 auto;padding:0 22px;display:grid;grid-template-columns:214px minmax(0,1fr);gap:28px;align-items:start}
.sidenav{position:sticky;top:18px;align-self:start;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 10px;margin-top:14px}
.sidenav .sn-title{font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);padding:4px 8px 8px}
.sidenav a{display:flex;align-items:baseline;gap:9px;padding:7px 9px;border-radius:9px;color:var(--mut);text-decoration:none;font-size:12.5px;line-height:1.35;border-left:2px solid transparent;transition:background .12s,color .12s,border-color .12s}
.sidenav a .sn-n{flex:0 0 18px;height:18px;border-radius:6px;background:#182541;color:var(--mut);font-size:10.5px;font-weight:700;display:flex;align-items:center;justify-content:center}
.sidenav a:hover{background:var(--panel2);color:var(--ink)}
.sidenav a.active{background:var(--panel2);color:var(--ink);border-left-color:var(--acc)}
.sidenav a.active .sn-n{background:#12403a;color:var(--acc)}
.sidenav a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wrap{max-width:none;margin:0;padding:0 0 90px}
header.hero{padding:52px 0 28px;border-bottom:1px solid var(--line)}
.kicker{color:var(--acc);letter-spacing:.14em;text-transform:uppercase;font-size:12px;font-weight:700}
h1{font-size:34px;line-height:1.15;margin:12px 0 10px;font-weight:800}
.sub{color:var(--mut);max-width:780px;font-size:15px}
.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}
.badge{background:var(--chip);border:1px solid var(--line);color:var(--acc2);padding:5px 11px;border-radius:999px;font-size:12px;font-weight:600}
section{margin-top:46px}
h2{font-size:22px;margin:0 0 6px;display:flex;align-items:center;gap:10px}
h2 .n{width:26px;height:26px;border-radius:8px;background:linear-gradient(135deg,var(--acc),var(--acc2));color:#04251f;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800}
.lead{color:var(--mut);margin:0 0 20px;max-width:840px}
.grid{display:grid;gap:14px}
.cards{grid-template-columns:repeat(4,1fr)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.card-n{font-size:28px;font-weight:800;color:var(--acc)}
.card-l{color:var(--mut);font-size:12.5px;margin-top:4px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.cols-tall{height:560px;align-items:stretch}
.cols-tall>.panel{display:flex;flex-direction:column;min-height:0;min-width:0;height:100%}
.cols-tall .scrollfill{flex:1 1 auto;min-height:0;min-width:0;overflow:auto;padding-right:6px}
.cols-tall .scrollfill::-webkit-scrollbar{width:14px}
.cols-tall .scrollfill::-webkit-scrollbar-track{background:transparent}
.cols-tall .scrollfill::-webkit-scrollbar-thumb{background:var(--th);border-radius:7px;border:3px solid var(--panel)}
.cols-tall .scrollfill::-webkit-scrollbar-thumb:hover{background:var(--acc2)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
.panel h3{margin:0 0 12px;font-size:15px;color:var(--acc2)}
.pico{display:grid;gap:10px}
.pico .row{display:flex;gap:12px;padding:12px;background:var(--panel2);border:1px solid var(--line);border-radius:10px}
.pico .tag{width:34px;height:34px;flex:0 0 34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px}
.tag.P{background:#134e4a;color:#5eead4}.tag.I{background:#1e3a8a;color:#93c5fd}.tag.C{background:#4c1d95;color:#c4b5fd}.tag.O{background:#7c2d12;color:#fdba74}
.tag.E{background:#7c2d12;color:#fdba74}.tag.S{background:#134e4a;color:#5eead4}.tag.D{background:#1e3a8a;color:#93c5fd}.tag.R{background:#4c1d95;color:#c4b5fd}.tag.M{background:#4c1d95;color:#c4b5fd}
.pico .lab{font-weight:700;font-size:13px}.pico .txt{color:var(--mut);font-size:13px;margin-top:3px}
.concept{display:grid;gap:12px}
.concept .c{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:13px}
.concept .c b{color:var(--acc)}.concept .c p{margin:5px 0 0;color:var(--mut);font-size:13.5px}
.flow{display:flex;flex-wrap:wrap;gap:10px;align-items:stretch}
.stage{flex:1 1 150px;background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:14px}
.stage .big{font-size:26px;font-weight:800;color:var(--acc)}
.stage .cap{color:var(--mut);font-size:12px;margin-top:3px}
.stage.excl .big{color:var(--bad)}
.arrow{align-self:center;color:var(--mut);font-size:20px;padding:0 2px}
.bar-row{display:flex;align-items:center;gap:10px;margin:9px 0}
.bar-row .bl{width:150px;font-size:13px;flex:0 0 150px}
.bar{flex:1;height:12px;background:#0c1526;border-radius:6px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2))}
.bar-row b{width:34px;text-align:right;font-size:13px;color:var(--mut)}
.tree{list-style:none;margin:0;padding:0;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.tree li{display:flex;align-items:center;gap:12px;padding:11px 16px;border-bottom:1px solid var(--line);font-size:13.5px}
.tree li:last-child{border-bottom:none}
.tree .tl{font-family:ui-monospace,Menlo,Consolas,monospace;flex:0 0 auto}
.tree .td{color:var(--mut);flex:1;font-size:12.5px}
.tree .fl{flex:0 0 auto;color:var(--acc);text-decoration:none;font-size:12px;font-weight:700;border:1px solid var(--line);padding:3px 9px;border-radius:7px}
.tree .fl:hover{background:var(--chip)}
.tbl-wrap{overflow:auto;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:820px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{position:sticky;top:0;background:#0e1830;color:var(--acc2);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;z-index:1}
tr:hover td{background:#131f38}
.sid{font-family:ui-monospace,monospace;color:var(--mut);font-size:12px;white-space:nowrap}
.sid-dim{color:var(--mut);opacity:.6}
.sid-link{font-family:ui-monospace,monospace;color:var(--acc2);font-size:12px;text-decoration:none;font-weight:600;white-space:nowrap;border-bottom:1px dotted var(--acc2)}
.sid-link:hover{color:#7fffe6;border-bottom-style:solid}
.doi-link{display:inline-block;color:var(--acc);font-size:11px;font-weight:700;letter-spacing:.04em;text-decoration:none;background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:1px 8px;vertical-align:top;min-width:44px;text-align:center}
.doi-link:hover{color:#7fffe6;border-color:var(--acc);background:#0d2a3a}
.doi-sub{display:block;margin-top:5px;font-size:11.5px;line-height:1.45;color:var(--ink);max-width:200px}
.doi-auth{display:block;margin-top:3px;font-size:11px;color:var(--mut);max-width:200px}
td.auth{min-width:200px;max-width:340px}
td.auth .ttl{display:block;margin-top:3px;font-size:12.5px;color:var(--mut);text-decoration:none;line-height:1.35}
td.auth .ttl:hover{color:#7fffe6;text-decoration:underline}
td.auth .ttl-none{display:block;margin-top:3px;font-size:12.5px;color:var(--mut);opacity:.75;line-height:1.35}
.mono{font-family:ui-monospace,monospace}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.filters input,.filters select{background:var(--panel2);border:1px solid var(--line);color:var(--ink);padding:9px 12px;border-radius:9px;font-size:13px;min-width:180px}
.filters input{flex:1}
.pdf-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
.pdf-head{font-size:15px;margin-bottom:8px}
.pdf-size{color:var(--mut);font-size:12px;margin-left:8px}
.pdf-link{display:inline-block;color:#04251f;background:linear-gradient(135deg,var(--acc),var(--acc2));font-weight:800;font-size:12.5px;padding:6px 13px;border-radius:8px;text-decoration:none;margin-bottom:10px}
.pdf-card ul{margin:6px 0 0;padding-left:18px;color:var(--mut);font-size:13.5px}
.pdf-card li{margin:5px 0}.pdf-card b{color:var(--ink)}
.audit{max-height:520px;overflow:auto}
.st-success{color:var(--good);font-weight:700}.st-partial{color:var(--warn);font-weight:700}.st-failed{color:var(--bad);font-weight:700}
.inc-list{padding-left:18px;margin:0;font-size:13.5px}
.inc-list li{margin:7px 0;color:var(--mut);overflow-wrap:anywhere}
.excl-list{padding-left:18px;margin:0;font-size:13.5px}
.excl-list li{margin:7px 0;color:var(--mut)}
.note{background:#1a2540;border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:10px;padding:12px 14px;font-size:13.5px;color:var(--mut);margin-top:14px}
.note b{color:var(--ink)}
.ph{background:#151b2b;border:1px dashed var(--line);border-left:4px solid var(--acc2);border-radius:10px;padding:14px 16px;font-size:13.5px;color:var(--mut);margin:12px 0}
.ph b{color:var(--acc2)}
/* PRISMA two-panel: figure left, counts (flex rows, number on right) */
.prisma-cols{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start;margin-top:18px}
.prisma-cols>*{min-width:0}
.prisma-fig{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;overflow:auto}
.stages{display:flex;flex-direction:column;gap:8px}
.stg{display:flex;align-items:center;justify-content:space-between;gap:14px;background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--acc2);border-radius:10px;padding:9px 13px}
.stg .lab{font-size:13px;color:var(--mut);min-width:0;overflow-wrap:anywhere}
.stg .val{flex:0 0 auto;font-size:19px;font-weight:800;color:var(--acc);font-variant-numeric:tabular-nums;white-space:nowrap}
.stg.excl{border-left-color:var(--bad)}
.stg.excl .val{color:var(--bad)}
.stg.final{background:#15331f;border-color:#245c37;border-left-color:var(--acc)}
.stg.final .lab{color:var(--ink)}
.stg.final .val{color:#5be38a}
/* stats: distributions + scatter plots (self-contained SVG) */
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.stat-grid .panel h3{margin:0 0 4px;font-size:14px}
.stat-grid .panel .sub{font-size:11.5px;color:var(--mut);margin:0 0 12px}
.stat-svg{width:100%;height:auto;display:block}
.stat-svg text{font:11px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif}
.stat-svg .axis{fill:var(--mut);font-size:10px}
.stat-svg .grid{stroke:var(--line);stroke-width:1}
.stat-svg .tick{stroke:var(--line);stroke-width:1}
.stat-svg .lbl{fill:var(--ink);font-size:11px}
.stat-svg .val{fill:var(--mut);font-size:10.5px}
.stat-svg .bar{fill:var(--acc);opacity:.9;transition:opacity .12s}
.stat-svg .bar:hover{opacity:1;fill:#7fffe6}
.stat-svg .cat{fill:var(--ink)}
.stat-svg .dot{fill:var(--acc);fill-opacity:.62;stroke:#0b1220;stroke-width:.5}
.stat-svg .dot:hover{fill:#7fffe6;fill-opacity:1;stroke:#0b1220}
.stat-svg .dot.acc{fill:var(--acc)}
.stat-svg .dot.auc{fill:var(--acc2)}
.stat-svg .reg{stroke:var(--warn);stroke-width:2;fill:none;stroke-dasharray:6 4}
.stat-svg .reglbl{fill:var(--warn);font-size:11px;font-weight:700}
.stat-svg .r2{fill:var(--warn);font-size:10.5px}
.stat-svg .legend{fill:var(--mut);font-size:11px}
.scatter-cap{font-size:11.5px;color:var(--mut);margin:10px 0 0;line-height:1.55}
.scatter-cap b{color:var(--ink)}
.scatter-cap .r2{color:var(--warn);font-weight:700}
#charttip{position:fixed;z-index:60;pointer-events:none;background:#0b1220;border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:12px;color:var(--ink);max-width:280px;box-shadow:0 8px 24px rgba(0,0,0,.55);display:none;line-height:1.5}
#charttip b{color:var(--acc)}
@media(max-width:720px){.stat-grid{grid-template-columns:1fr}}
footer{margin-top:60px;padding-top:24px;border-top:1px solid var(--line);color:var(--mut);font-size:12.5px}
.chip-metric{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 6px 0 0;font-size:13px}
.chip-metric b{color:var(--acc);font-size:18px}
@media(max-width:980px){.layout{grid-template-columns:1fr;gap:0}.sidenav{position:sticky;top:0;z-index:30;flex-direction:row;flex-wrap:wrap;gap:4px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px;margin-top:10px;display:flex;overflow-x:auto}.sidenav .sn-title{display:none}.sidenav a{white-space:nowrap;border-left:none;border-bottom:2px solid transparent;border-radius:8px;padding:6px 9px}.sidenav a.active{border-bottom-color:var(--acc)}.sidenav a .sn-n{display:none}}
@media(max-width:720px){.cards{grid-template-columns:repeat(2,1fr)}.cols{grid-template-columns:1fr}.prisma-cols{grid-template-columns:1fr}.cols-tall{height:auto}.cols-tall>.panel{height:auto}.cols-tall .scrollfill{max-height:420px}}
/* high-contrast scrollbars (every scrollable region) */
*{scrollbar-color:var(--th) var(--panel2);scrollbar-width:auto}
*::-webkit-scrollbar{width:14px;height:14px}
*::-webkit-scrollbar-track{background:var(--panel2)}
*::-webkit-scrollbar-thumb{background:var(--th);border-radius:7px;border:3px solid var(--panel2)}
*::-webkit-scrollbar-thumb:hover{background:var(--acc2)}
*::-webkit-scrollbar-corner{background:var(--panel2)}
/* theme toggle */
.theme-toggle{position:fixed;top:14px;right:16px;z-index:70;display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);color:var(--ink);padding:7px 13px;border-radius:999px;font:600 12.5px/1 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.28);transition:border-color .12s,transform .06s}
.theme-toggle:hover{border-color:var(--acc)}
.theme-toggle:active{transform:scale(.97)}
.theme-toggle .ico{font-size:15px;line-height:1}
"""

JS = """
(function(){
  // theme toggle (dark is default; light persisted in localStorage)
  var tgl=document.getElementById('themeToggle');
  function setTheme(t){
    var light=(t==='light');
    document.body.classList.toggle('light',light);
    var ico=tgl.querySelector('.ico'),lbl=tgl.querySelector('.lbl');
    if(ico)ico.textContent=light?'☀':'◐';
    if(lbl)lbl.textContent=light?'Dark':'Light';
    try{localStorage.setItem('srw-theme',t);}catch(e){}
  }
  var saved='';try{saved=localStorage.getItem('srw-theme')||'';}catch(e){}
  if(saved)setTheme(saved);
  if(tgl)tgl.addEventListener('click',function(){
    var light=document.body.classList.contains('light');
    setTheme(light?'dark':'light');
  });
  var tip=document.createElement('div');tip.id='charttip';document.body.appendChild(tip);
  function show(e){var t=e.target;var txt=t.getAttribute&&t.getAttribute('data-tip');if(!txt){hide();return;}
    tip.textContent=txt;tip.style.display='block';move(e);}
  function move(e){var x=(e.clientX||0)+14,y=(e.clientY||0)+14;
    var w=tip.offsetWidth,h=tip.offsetHeight;
    if(x+w>window.innerWidth-8)x=(e.clientX||0)-w-14;
    if(y+h>window.innerHeight-8)y=(e.clientY||0)-h-14;
    tip.style.left=x+'px';tip.style.top=y+'px';}
  function hide(){tip.style.display='none';}
  document.addEventListener('mouseover',function(e){var t=e.target;
    if(t&&t.hasAttribute&&t.hasAttribute('data-tip'))show(e);else hide();});
  document.addEventListener('mousemove',function(e){var t=e.target;
    if(t&&t.hasAttribute&&t.hasAttribute('data-tip'))move(e);});
  document.addEventListener('mouseleave',hide);
  var q=document.getElementById('q');
  if(q){
    var tb=document.querySelectorAll('#ev tbody tr');
    var sels=[].slice.call(document.querySelectorAll('#ev ~ .filters select, .filters select[id^="f-"]'));
    sels=sels.length?sels:[].slice.call(document.querySelectorAll('select[id^="f-"]'));
    // populate each filter from the rows
    sels.forEach(function(sel){
      var ci=sel.getAttribute('data-col');
      var seen={};
      tb.forEach(function(tr){
        var td=tr.querySelector('td[data-f="'+ci+'"]');
        if(td){var v=td.getAttribute('data-v');if(v)seen[v]=1;}
      });
      Object.keys(seen).sort().forEach(function(v){
        var o=document.createElement('option');o.value=v;o.textContent=v;sel.appendChild(o);});
    });
    function apply(){
      var t=(q.value||'').toLowerCase();
      var active=sels.map(function(s){return s.value;});
      tb.forEach(function(tr){
        var txt=tr.textContent.toLowerCase();
        var ok=t?txt.indexOf(t)>=0:true;
        for(var k=0;k<sels.length;k++){
          if(active[k]&&ok){
            var td=tr.querySelector('td[data-f="'+sels[k].getAttribute('data-col')+'"]');
            if(!td||td.getAttribute('data-v')!==active[k])ok=false;
          }
        }
        tr.style.display=ok?'':'none';});
    }
    q.addEventListener('input',apply);
    sels.forEach(function(s){s.addEventListener('change',apply);});
  }
  var links=[].slice.call(document.querySelectorAll('.sidenav a'));
  var secIds=links.map(function(a){return a.getAttribute('href').slice(1);});
  var secs=secIds.map(function(id){return document.getElementById(id);}).filter(Boolean);
  function setActive(id){links.forEach(function(a){a.classList.toggle('active',a.getAttribute('href')==='#'+id);});}
  if(links.length) setActive(links[0].getAttribute('href').slice(1));
  if('IntersectionObserver' in window && secs.length){
    var cur=null;
    var io=new IntersectionObserver(function(entries){
      entries.forEach(function(en){if(en.isIntersecting)cur=en.target.id;});
      if(cur)setActive(cur);
    },{rootMargin:'-15% 0px -70% 0px',threshold:0});
    secs.forEach(function(s){io.observe(s);});
  }
})();
"""

# ---------------------------------------------------------------------------
# Assemble the page
# ---------------------------------------------------------------------------
TITLE = TOPIC or "Systematic Review"
GEN_DATE = datetime.date.today().isoformat()

parts = []
parts.append(f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(TITLE)} — Systematic Review Portal</title>
<style>{CSS}</style></head><body>
<button class="theme-toggle" id="themeToggle" type="button" aria-label="Toggle light / dark theme"><span class="ico">◐</span><span class="lbl">Light</span></button>
<div class="layout">
<nav class="sidenav" aria-label="Sections">
<div class="sn-title">Jump to</div>
<a href="#s-exec"><span class="sn-n">1</span><span>Executive summary</span></a>
<a href="#s-method"><span class="sn-n">2</span><span>Framework &amp; PRISMA</span></a>
<a href="#s-stats"><span class="sn-n">3</span><span>Statistical overview</span></a>
<a href="#s-artifacts"><span class="sn-n">4</span><span>Artifact explorer</span></a>
<a href="#s-matrix"><span class="sn-n">5</span><span>Evidence matrix</span></a>
<a href="#s-pdf"><span class="sn-n">6</span><span>PDF repository</span></a>
<a href="#s-included"><span class="sn-n">7</span><span>Included &amp; exclusions</span></a>
<a href="#s-audit"><span class="sn-n">8</span><span>Audit trail</span></a>
</nav>
<div class="wrap">
<header class="hero">
<div class="kicker">PRISMA 2020 — Systematic Review</div>
<h1>{E(TITLE) or "Systematic Review"}</h1>
<p class="sub">A self-contained companion to a full, reproducible systematic-review pipeline
(search → screening → retrieval → extraction → synthesis). Every number below is generated
from the live workspace data; sections that lack data show a clear placeholder explaining why.</p>
<div class="badges">{badges_html}</div>
</header>''')

# 1 exec summary
parts.append(f'''
<section id="s-exec"><h2><span class="n">1</span>Executive Summary &amp; Key Numbers</h2>
<p class="lead">A reproducible, PRISMA 2020 systematic review. The figures below are computed directly
from the workspace artifacts — open any of them in the <a href="#s-artifacts" style="color:var(--acc)">Artifact Explorer</a>.</p>
<div class="grid cards">{cards_html or '<div class="card"><div class="card-n">—</div><div class="card-l">No data yet</div></div>'}</div>
<div style="margin-top:16px">{chips_html}</div></section>''')

# 2 framework + PRISMA
parts.append(f'''
<section id="s-method"><h2><span class="n">2</span>Methodological Framework — {E(FRAMEWORK)} &amp; PRISMA 2020</h2>
<div class="cols">
<div class="panel"><h3>{E(FRAMEWORK)} criteria (locked in protocol.json)</h3><div class="pico">
{_pico_rows()}
</div></div>
<div class="panel"><h3>Inclusion / exclusion</h3>
<p style="font-size:12px;color:var(--acc2);margin:0 0 6px;font-weight:700">INCLUDE</p>
<ul style="font-size:12.5px;color:var(--mut);padding-left:18px;margin:0 0 12px">{inclusion_html}</ul>
<p style="font-size:12px;color:var(--bad);margin:0 0 6px;font-weight:700">EXCLUDE</p>
<ul style="font-size:12.5px;color:var(--mut);padding-left:18px;margin:0">{exclusion_html}</ul>
</div></div>
<div class="prisma-cols">
<figure class="prisma-fig">
__PRISMA_FIGURE__
<figcaption style="margin-top:12px;font-size:10.5px;color:var(--mut)">
<b>PRISMA 2020 flow</b>{(" &middot; " + str(N) + " identified → " + str(FT_SCR if isinstance(FT_SCR,int) else "—") + " full-text screened → " + str(FT_INC if isinstance(FT_INC,int) else "—") + " included") if N else ""}.
{('<a href="' + viewlink("report/PRISMA_flowchart.svg") + '" target="_blank" rel="noopener" style="color:var(--acc)">SVG</a> &middot; ' if _exists(["report","PRISMA_flowchart.svg"]) else "")}
{('<a href="' + viewlink("report/PRISMA_flowchart.png") + '" target="_blank" rel="noopener" style="color:var(--acc)">PNG</a>' if _exists(["report","PRISMA_flowchart.png"]) else "")}
</figcaption>
</figure>
<div class="panel">
<h3 style="margin:0 0 6px;font-size:14px">PRISMA 2020 — stage counts</h3>
<p style="font-size:11.5px;color:var(--mut);margin:0 0 12px">Derived from the audit ledger. Counts on the right; exclusions in red.</p>
<div class="stages">{stages_html}</div>
</div>
</div>
{honesty_note}
</section>''')

# 3 statistical overview
stats_lead = (f"Descriptive statistics over the {N_MATRIX} included studies, computed directly from the "
              "evidence matrix (pure inline SVG — hover any bar/point for detail). These are <b>descriptive</b> "
              "summaries of a heterogeneous corpus; treat correlations as directional signals, not causal effects.")
stat_blocks = []
if year_c:
    stat_blocks.append(f'<div class="panel"><h3>Publications by year</h3><p class="sub">{N_MATRIX} studies, {yr_min}–{yr_max}.</p>{dist_year}</div>')
# One panel per configured chart column (order from portal_config.json, or the
# auto-detected columns). "count" type = how many items each study reports;
# "hbars" = the categorical breakdown. Titles come from the config, so the
# labels are always topic-correct (no "Speech task" on a COF review, etc.).
for col, title, ctype, cap in _panels:
    c = panel_counters.get(col)
    if not c:
        continue
    ttl = (title or _guess_title(col))
    if ctype == "count":
        stat_blocks.append(f'<div class="panel"><h3>{E(ttl)} reported</h3><p class="sub">How many distinct {E(ttl.lower())} each study reports.</p>{_hbars(c, unit="studies")}</div>')
    else:
        top = c.most_common(1)[0]
        stat_blocks.append(f'<div class="panel"><h3>{E(ttl)}</h3><p class="sub">Dominant: {E(top[0])} ({top[1]} studies).</p>{_hbars(c, unit="studies")}</div>')
# Full-text vs abstract coverage (recorded_as) — generic, always useful.
if rec_as_c:
    stat_blocks.append(f'<div class="panel"><h3>Recorded as (full text vs abstract)</h3><p class="sub">Where each row was read from.</p>{dist_recas}</div>')

scatter_html = ""
if scat_year or scat_metric:
    scat_panels = []
    if scat_year:
        rtxt = f"r = {stat_year_feat['r']:+.2f}" if stat_year_feat else "n/a"
        scat_panels.append(f'<div class="panel"><h3>Year → {E(PRIMARY_TITLE.lower())} coverage</h3><p class="sub">Each point = one study.</p>{scat_year}'
                           f'<p class="scatter-cap"><b>{rtxt}</b> — the trend in coverage over time.</p></div>')
    if scat_metric:
        rtxt = f"r = {stat_feat_metric['r']:+.2f}" if stat_feat_metric else "n/a"
        scat_panels.append(f'<div class="panel"><h3>{E(PRIMARY_TITLE.lower())} coverage → reported effect</h3><p class="sub">Only studies reporting a numeric value for <b>{E(METRIC_SUMMARY["col"] if METRIC_SUMMARY else "metric")}</b> are plotted.</p>{scat_metric}'
                           f'<p class="scatter-cap"><b>{rtxt}</b> — a descriptive association only; a self-selected subset.</p></div>')
    scatter_html = f'<h3 style="font-size:15px;margin:22px 0 12px;color:var(--acc2)">Correlation scatter plots</h3><div class="stat-grid">{"".join(scat_panels)}</div>'
else:
    scatter_html = ph("No scatter plots: the evidence matrix has no usable pair of numeric/categorical columns "
                      "(e.g. no year or no numeric metric values). Add a numeric metric column to enable these.")

stat_note = ("<div class='note'><b>How to read these:</b> distributions describe <i>what the corpus reports</i>; "
             "scatter plots are <i>descriptive associations</i> (Pearson r). Neither implies causation, and metric-based "
             "plots use only the studies reporting a numeric value — a non-random, self-selected subset. "
             "Hover any element for the underlying value.</div>")

parts.append(f'''
<section id="s-stats"><h2><span class="n">3</span>Statistical overview — key distributions &amp; correlations</h2>
<p class="lead">{stats_lead}</p>
{('<h3 style="font-size:15px;margin:6px 0 12px;color:var(--acc2)">Key distributions</h3><div class="stat-grid">' + "".join(stat_blocks) + '</div>') if stat_blocks else ph("No distributions: the evidence matrix (synthesis/evidence_matrix.csv) is missing or empty. Run the extraction step to populate it.")}
{scatter_html}
{stat_note if (stat_blocks or scatter_html and 'ph' not in scatter_html) else ''}
{('<h3 style="font-size:15px;margin:22px 0 12px;color:var(--acc2)">Reported effect sizes (numeric)</h3><div class="stat-grid">'
  + (f'<div class="panel"><h3>Dominant {E(PRIMARY_TITLE.lower())} (n={N_MATRIX})</h3><p class="sub">The dominant {E(PRIMARY_TITLE.lower())} each study reports.</p>{feat_bars}</div>' if feat_bars else '')
  + f'{summary_eff_panel}'
  + "</div>") if (feat_bars or summary_eff_panel) else ''}
</section>''')

# 4 artifact explorer
parts.append(f'''
<section id="s-artifacts"><h2><span class="n">4</span>Artifact Explorer — directory tree</h2>
<p class="lead">Every file in the review workspace. Click <b>open</b> to view it — JSON, JSONL, Markdown, and CSV open in a
clean, readable viewer (with copy / raw toggle); PDFs open directly. Links use relative paths, so everything works both from
disk and when served over HTTP / Caddy.</p>
<ul class="tree">{tree_html or ph("No workspace files found yet — run the pipeline steps to generate them.")}</ul></section>''')

# 5 evidence matrix — column order + display are config-driven (or auto-detected)
# ---------------------------------------------------------------------------
# Config:  "matrix_columns": ["col_a", "col_b", ...]  → exact display order.
# Without it we auto-detect: fixed-first (author, year) → remaining content
# cols in CSV order → fixed-last (outcome, metric, source). All headers are
# humanised (underscores → spaces, title-cased) so any column reads well.
_ID_SKIP = {"record_index","doi","pmid","source","index","id","openalex_id","title"}
_FIXED_FIRST = ["first_author","year"]
_FIXED_LAST  = ["outcome","metric_value","recorded_as"]
def _humanize(col):
    return re.sub(r"[_\-]+", " ", col).strip().title() or col

if matrix:
    if cfg.get("matrix_columns"):
        _disp_cols = [c for c in cfg["matrix_columns"] if c in COLS]
    else:
        _content_cols = [c for c in COLS
                         if c not in _ID_SKIP and c not in _FIXED_FIRST
                         and c not in _FIXED_LAST and HAS.get(c)]
        _disp_cols = ([c for c in _FIXED_FIRST if c in COLS and HAS.get(c)]
                      + _content_cols
                      + [c for c in _FIXED_LAST if c in COLS and HAS.get(c)])

    # Filter columns: config "filter_columns" or auto (first 2 categorical cols)
    _flt_cols = []
    if cfg.get("filter_columns"):
        _flt_cols = [c for c in cfg["filter_columns"] if c in COLS]
    else:
        # pick up to 2 cols with 2–20 unique values (good dropdown candidates)
        for c in _disp_cols:
            if len(_flt_cols) >= 2: break
            vals = set((_cell(r, c) or "").strip().lower() for r in matrix if (_cell(r, c) or "").strip())
            if 2 <= len(vals) <= 20:
                _flt_cols.append(c)

    # Build header
    _th_cells = "".join(f'<th>{E(_humanize(c))}</th>' for c in _disp_cols)
    # Build rows
    _data_rows = []
    for r in matrix:
        tds = "".join(
            f'<td data-f="{i}" data-v="{E(_cell(r,c).lower())}">{E(_cell(r,c) or "—")}</td>'
            for i, c in enumerate(_disp_cols))
        _data_rows.append(
            f'<tr id="m-{row_idx.get(id(r))}"><td>{_sid_cell(r)}</td>{tds}<td>{_doi_cell(r)}</td></tr>')

    # Filter dropdowns
    _flt_html = ""
    for i, c in enumerate(_flt_cols):
        _flt_html += (f'<select id="f-{i}" data-col="{i}">'
                      f'<option value="">All {E(_humanize(c).lower())}</option></select>')

    _matrix_html = f'''
<section id="s-matrix"><h2><span class="n">5</span>Evidence Matrix — all {N_MATRIX} included studies</h2>
<p class="lead">Structured extraction from the evidence matrix. The <b>Study&nbsp;#</b> column is the
record's global id (same <span class="mono">#NNN</span> as the PDF cards and the included list); a coloured
<span class="mono">#NNN</span> links to that study's full-text card. The <b>DOI</b> column opens the publisher
page. <span class="mono">recorded_as</span> = where the row was read from.</p>
<div class="filters"><input id="q" type="text" placeholder="Search across all columns…">{_flt_html}</div>
<div class="tbl-wrap"><table id="ev"><thead><tr><th>Study&nbsp;#</th>{_th_cells}<th>DOI</th></tr></thead>
<tbody>{"".join(_data_rows)}</tbody></table></div></section>'''
    parts.append(_matrix_html)
else:
    parts.append(f'''
<section id="s-matrix"><h2><span class="n">5</span>Evidence Matrix</h2>
{ph("No evidence matrix: synthesis/evidence_matrix.csv is missing or empty. Run the <b>extract</b> step (step 6) to populate it, then re-run <b>portal</b>.")}
</section>''')

# 6 pdf repository
if pdfs:
    parts.append(f'''
<section id="s-pdf"><h2><span class="n">6</span>Full-Text PDF Repository — line-by-line summaries</h2>
<p class="lead">{NPDF} open-access full texts were retrieved, validated (%PDF magic bytes), hashed, and extracted.
Each card links directly to the PDF and gives a 3-line digest.</p>
{pdf_html}</section>''')
else:
    why = (f"{manifest_missing} fetch(es) in pdfs/manifest.json failed or returned no path (paywalled / publisher-blocked)."
           if manifest_missing else "pdfs/manifest.json is missing or contains no resolved PDF paths.")
    parts.append(f'''
<section id="s-pdf"><h2><span class="n">6</span>Full-Text PDF Repository</h2>
{ph("No PDFs on disk: " + why + " Abstract-only records are still fully included in the review and flagged in the evidence matrix.")}
</section>''')

# 7 included + exclusions
inc_block = f'<ul class="inc-list">{inc_html}</ul>' if inc_html else ph("No full-text screening decisions recorded yet (screening/fulltext_exclusion_log.json missing).")
excl_block = f'<ul class="excl-list">{excl_html}</ul>' if excl_html else ph("No full-text exclusions recorded yet.")
parts.append(f'''
<section id="s-included"><h2><span class="n">7</span>Included studies ({FT_INC if isinstance(FT_INC,int) else "—"}) &amp; full-text exclusions ({FT_EXC if isinstance(FT_EXC,int) else "—"})</h2>
<div class="cols cols-tall">
<div class="panel"><h3>Included (with DOI)</h3><div class="scrollfill">{inc_block}</div></div>
<div class="panel"><h3>Excluded at full text (rationale)</h3><div class="scrollfill">{excl_block}</div></div>
</div></section>''')

# 8 audit
if audit:
    parts.append(f'''
<section id="s-audit"><h2><span class="n">8</span>Audit Trail — immutable execution ledger</h2>
<p class="lead">Append-only JSONL with SHA-256 checksums. <b>{N_AUDIT}</b> entries.</p>
<div class="tbl-wrap audit"><table><thead><tr><th>Timestamp (UTC)</th><th>Step</th><th>Status</th><th>Action</th></tr></thead>
<tbody>{audit_html}</tbody></table></div></section>''')
else:
    parts.append(f'''
<section id="s-audit"><h2><span class="n">8</span>Audit Trail</h2>
{ph("No audit ledger: audit_trail/lab_notebook.jsonl is missing. It is written by every CLI step — this workspace has not run the pipeline yet.")}
</section>''')

parts.append(f'''
<footer>
<p>Generated {GEN_DATE} — systematic-review-workflow skill (PRISMA 2020) — topic: {E(TOPIC) or "—"}.
Workspace: <span class="mono">{ROOT}</span></p>
<p>Full manuscript: <a href="{viewlink('report/systematic_review_draft.md')}" target="_blank" rel="noopener" style="color:var(--acc)">systematic_review_draft.md</a>
&middot; Audit: <a href="{viewlink('audit_trail/lab_notebook.jsonl')}" target="_blank" rel="noopener" style="color:var(--acc)">lab_notebook.jsonl</a></p>
</footer></div></div>
<script>{JS}</script></body></html>''')

HTML = "".join(parts)

# PRISMA flow figure — use the shared prisma_svg module (same code the
# `srw_cli.py report` step uses, so the SVG is identical at both stages).
import sys as _sys
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from prisma_svg import prisma_svg as _prisma_svg, normalise_ft_scr as _norm_ft

# Build the PRISMA SVG
_svg = ""
if N is not None or FT_INC is not None:
    _svg = _prisma_svg(
        N,                                    # identified
        N,                                    # dedup (same as identified in our pipeline)
        N if ABS_IN is not None else None,    # screened
        ABS_OUT,                              # excluded at abstract
        _norm_ft(FT_SCR),                     # full-text screened (normalise list→int)
        FT_EXC,                               # excluded at full-text
        FT_INC,                               # included
    )
    # Also write to report/ for standalone viewing
    os.makedirs(_p("report"), exist_ok=True)
    with open(_p("report", "PRISMA_flowchart.svg"), "w", encoding="utf-8") as _f:
        _f.write(_svg)

_prisma_html = HTML.replace("__PRISMA_FIGURE__", _svg) if _svg else HTML.replace(
    "__PRISMA_FIGURE__",
    ("<img src=\"PRISMA_flowchart.png\" alt=\"PRISMA 2020 flow diagram\" "
     "style=\"max-width:100%;height:auto;display:block;margin:0 auto;\">") if _exists(["report", "PRISMA_flowchart.png"]) else "")

os.makedirs(_p("report"), exist_ok=True)
with open(_p("index.html"), "w", encoding="utf-8") as f:
    f.write(_prisma_html)
with open(_p("report", "index.html"), "w", encoding="utf-8") as f:
    f.write(_prisma_html)
print(f"WROTE index.html: {len(HTML)} bytes | {NPDF} pdf cards, {N_MATRIX} matrix rows, "
      f"{N_AUDIT} audit rows | prisma fig={'inline-svg' if _svg else ('png' if _exists(['report','PRISMA_flowchart.png']) else 'none')}")
