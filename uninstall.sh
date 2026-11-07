#!/usr/bin/env bash
# goose-science-kit uninstaller
# Removes ONLY the skills this kit installed (per the manifest). Leaves the
# rest of your goose installation untouched. Safe to re-run; idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DST="${GOOSE_SKILLS_DIR:-$HOME/.config/goose/skills}"

# Manifest lives inside the kit; fallback to the installed copy.
if [ -f "$SCRIPT_DIR/.skill_manifest" ]; then
  MANIFEST="$SCRIPT_DIR/.skill_manifest"
elif [ -f "$SKILLS_DST/.goose-science-kit.manifest" ]; then
  MANIFEST="$SKILLS_DST/.goose-science-kit.manifest"
else
  echo "ERROR: no skill manifest found (looked for .skill_manifest)." >&2
  exit 1
fi

total="$(grep -cve '^[[:space:]]*$' "$MANIFEST" || true)"
echo "==> goose-science-kit uninstaller"
echo "    target skills dir: $SKILLS_DST"
echo "    manifest skills  : $total"
echo

if [ ! -d "$SKILLS_DST" ]; then
  echo "Skills dir not present — nothing to remove."
  exit 0
fi

# --- Safety: confirm before deleting ---
if [ "${GOOSE_KIT_ASSUME_YES:-0}" != "1" ]; then
  printf "This will remove up to %s skill directories from:\n  %s\n\nProceed? [y/N] " "$total" "$SKILLS_DST"
  read -r ans
  case "$ans" in
    [yY]|[yY][eE][sS]) : ;;
    *) echo "Aborted — nothing removed."; exit 0 ;;
  esac
fi

removed=0; notfound=0; failed=0
while IFS= read -r name; do
  [ -z "$name" ] && continue
  [ "$name" = "." ] && continue
  path="$SKILLS_DST/$name"
  if [ -e "$path" ]; then
    if rm -rf "$path"; then
      removed=$((removed+1))
      # echo "  removed: $name"
    else
      failed=$((failed+1))
      echo "  FAILED to remove: $name" >&2
    fi
  else
    notfound=$((notfound+1))
  fi
done < "$MANIFEST"

# Clean up the installed manifest marker if present
rm -f "$SKILLS_DST/.goose-science-kit.manifest"

# Optionally remove the seeded ~/.env only if it still looks like our template
# (i.e. it has the exact placeholder markers and no real-looking keys).
ENV="$HOME/.env"
if [ -f "$ENV" ] && grep -q "K-Dense BYOK (optional" "$ENV" 2>/dev/null; then
  # Only delete if the two main keys are empty (we seeded them blank)
  if ! grep -Eq '^OPENALEX_API_KEY=.+' "$ENV" && ! grep -Eq '^NCBI_API_KEY=.+' "$ENV"; then
    rm -f "$ENV"
    echo "==> Removed seeded ~/.env (contained no real keys)."
  fi
fi

# Remove the skills dir itself if it's now empty
if [ -d "$SKILLS_DST" ] && [ -z "$(ls -A "$SKILLS_DST" 2>/dev/null)" ]; then
  rmdir "$SKILLS_DST" 2>/dev/null || true
  echo "==> Skills dir was empty — removed it."
fi

echo
echo "==> ✅ Uninstall complete."
echo "    removed:    $removed"
echo "    not found:  $notfound"
echo "    failed:     $failed"
if [ -f "$SKILLS_DST/systematic-review-workflow/SKILL.md" ]; then
  echo "    ⚠️  systematic-review-workflow still present (check for typos)." >&2
fi
remaining="$(ls "$SKILLS_DST" 2>/dev/null | wc -l | tr -d ' ')"
echo "    remaining skills in dir: $remaining"
echo
echo "Tip: start a new goose session to clear any in-memory skill cache."
