#!/usr/bin/env python3
"""Embed review artifacts into viewer.html so it renders with NO fetch() at all.

Works from file:// (double-click) and over HTTP/Caddy.
Files > TRUNCATE_BYTES are capped to a sample (banner shown in the viewer).
"""
import json, os, sys

# Resolution order: CLI arg (--root) > $SRW_OUTPUT_DIR > ./reviews/<first slug> fallback.
# Never hardcode a machine-specific path — this script ships in a portable kit.
if len(sys.argv) >= 3 and sys.argv[1] == "--root":
    ROOT = sys.argv[2]
else:
    ROOT = os.environ.get("SRW_OUTPUT_DIR")
    if not ROOT:
        # Sensible default: first subdirectory of ./reviews/ (common layout)
        _reviews = "./reviews"
        if os.path.isdir(_reviews):
            _subs = sorted(d for d in os.listdir(_reviews) if os.path.isdir(os.path.join(_reviews, d)))
            if _subs:
                ROOT = os.path.join(_reviews, _subs[0])
        if not ROOT:
            sys.exit("ERROR: set SRW_OUTPUT_DIR or pass --root <review-dir> "
                     "(e.g. ./reviews/my-topic).")
# Template: prefer SRW_VIEWER_TEMPLATE env var, else the skill's assets dir,
# else a local copy next to the review root.
_env_tpl = os.environ.get("SRW_VIEWER_TEMPLATE")
_skill_tpl = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "viewer_template.html"))
_local_tpl = ROOT + "/viewer_template.html"
if _env_tpl and os.path.exists(_env_tpl):
    TEMPLATE = _env_tpl
elif os.path.exists(_skill_tpl):
    TEMPLATE = _skill_tpl
elif os.path.exists(_local_tpl):
    TEMPLATE = _local_tpl
else:
    TEMPLATE = _skill_tpl  # default; will error with a clear message below
VIEWER = ROOT + "/viewer.html"
TRUNCATE_BYTES = 1_500_000  # ~1.5 MB per artifact cap (keeps viewer under 4 MB total)
TRUNCATE_NOTE = ("…[truncated for embedded viewer - see 'view raw file' for full content]…\n")

ARTIFACTS = [
    "protocol.json",
    "audit_trail/lab_notebook.jsonl",
    "data/deduplicated_records.json",
    "data/openalex_results.json",
    "data/pubmed_results.json",
    "screening/abstract_decisions.csv",
    "screening/fulltext_exclusion_log.json",
    "pdfs/manifest.json",
    "synthesis/evidence_matrix.csv",
    "report/systematic_review_draft.md",
    "report/systematic_review_draft.tex",
    "report/prisma_checklist.md",
    "report/prisma_flow.mmd",
    "report/synthesis_v2.md",
]

def load(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None, None
    with open(p, "rb") as f:
        data = f.read()
    truncated = len(data) > TRUNCATE_BYTES
    if truncated:
        data = data[:TRUNCATE_BYTES]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += TRUNCATE_NOTE
    return text, truncated

def main():
    data = {}
    truncated_set = set()
    for rel in ARTIFACTS:
        text, truncated = load(rel)
        if text is None:
            print(f"  [skip] {rel} (missing)")
            continue
        data[rel] = text
        if truncated:
            truncated_set.add(rel)
    # JSON with ensure_ascii=False for compactness; then escape "</script>" to
    # "<\/script>" so it can't terminate the <script type="application/json"> block.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</script>", r"<\/script>")
    tpl = open(TEMPLATE).read()
    if "__ARTIFACT_DATA__" not in tpl:
        print("FATAL: __ARTIFACT_DATA__ placeholder not found in viewer_template.html", file=sys.stderr)
        sys.exit(1)
    out = tpl.replace("__ARTIFACT_DATA__", payload)
    with open(VIEWER, "w", encoding="utf-8") as f:
        f.write(out)
    size_mb = os.path.getsize(VIEWER) / 1024 / 1024
    print(f"embedded {len(data)} artifacts ({len(truncated_set)} truncated)")
    for r in sorted(truncated_set):
        print(f"  [truncated] {r}")
    print(f"WROTE {VIEWER}: {size_mb:.2f} MB")

if __name__ == "__main__":
    main()
