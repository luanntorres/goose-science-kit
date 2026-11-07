#!/usr/bin/env bash
# goose-science-kit installer
# Installs all 218 skills (Google DeepMind science + K-Dense scientific-agent
# + systematic-review-workflow) into the local goose skills directory, and
# seeds ~/.env with API key placeholders if it doesn't already exist.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_SRC="$SCRIPT_DIR/skills"
SKILLS_DST="${GOOSE_SKILLS_DIR:-$HOME/.config/goose/skills}"

echo "==> goose-science-kit installer"
echo "    source:  $SKILLS_SRC"
echo "    target:  $SKILLS_DST"

# 1. Sanity-check prerequisites
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found on PATH. Install Python 3.10+ first." >&2
  exit 1
fi
echo "    python:  $(python3 --version)"

if command -v uv >/dev/null 2>&1; then
  echo "    uv:      $(which uv)"
else
  echo "    uv:      NOT FOUND — recommended for running skill CLIs. Install:"
  echo "                curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# 2. Copy skills (merge, don't clobber existing with a different name)
mkdir -p "$SKILLS_DST"
installed=0; skipped=0
for skill in "$SKILLS_SRC"/*/; do
  name="$(basename "$skill")"
  if [ -f "$SKILLS_DST/$name/SKILL.md" ]; then
    echo "    skip (already present): $name"
    skipped=$((skipped+1))
  else
    cp -r "$skill" "$SKILLS_DST/"
    installed=$((installed+1))
  fi
done
echo "==> Installed $installed skills ($skipped skipped). Total in target: $(ls "$SKILLS_DST" | wc -l)"

# 3. Seed ~/.env with placeholders if it doesn't exist
if [ ! -f "$HOME/.env" ]; then
  echo "==> Creating ~/.env with placeholders"
  cat > "$HOME/.env" <<'EOF'
# Add your API keys below (leave blank if you don't have one)
OPENALEX_API_KEY=
NCBI_API_KEY=
# K-Dense BYOK (optional, for advanced extraction workflows)
# KADY_AUTH_TOKEN=
# KADY_BASE_URL=http://localhost:3000
EOF
  chmod 600 "$HOME/.env"
else
  echo "==> ~/.env already exists — leaving it untouched."
fi

# 4. Record the installed-skill manifest for uninstall.sh
if [ -f "$SCRIPT_DIR/.skill_manifest" ]; then
  cp "$SCRIPT_DIR/.skill_manifest" "$SKILLS_DST/.goose-science-kit.manifest"
  echo "==> Installed-skill manifest recorded in skills dir (for uninstall.sh)"
fi

# 5. Verify the flagship skill is installed
if [ -f "$SKILLS_DST/systematic-review-workflow/SKILL.md" ]; then
  echo "==> systematic-review-workflow: OK"
else
  echo "WARNING: systematic-review-workflow missing after install" >&2
fi

# 6. Run the hermetic self-test (no network, no keys)
echo
echo "==> Running self-test (7-step synthetic pipeline)..."
if python3 "$SKILLS_DST/systematic-review-workflow/scripts/srw_cli.py" selftest; then
  echo
  echo "==> ✅ Installation complete."
  echo
  echo "Next steps:"
  echo "  1. Add your API keys to ~/.env:"
  echo "       OPENALEX_API_KEY=..."
  echo "       NCBI_API_KEY=..."
  echo "  2. Start a NEW goose session."
  echo "  3. Ask: \"Run the systematic-review-workflow skill for [topic], PICO, OpenAlex + PubMed\""
else
  echo
  echo "⚠️  Self-test failed — see output above." >&2
  exit 1
fi
