#!/usr/bin/env bash
# build-explore-skill.sh — rebuild Perplexity-format java-codebase-explore.zip
#
# Prerequisites:
#   - bash 3.2+
#   - cp, mktemp, rm, touch (POSIX); zip(1) in PATH
#
# Usage (from repo root):
#   ./scripts/build-explore-skill.sh
#
# When to run:
#   - After editing docs/skills/java-codebase-explore.md (keeps zip in sync).
#   - As release / ontology-bump hygiene alongside README + cheat sheet updates.
#
# Output:
#   docs/skills/java-codebase-explore.zip  (overwritten; commit the result)
#
# Determinism: file mtimes inside the archive are normalized so repeated runs
# on the *same* machine / zip(1) build usually yield identical bytes. Do not
# treat a checksum from another maintainer or CI image as a portable contract:
# zip implementations and extra fields can still differ across OS releases.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_MD="$ROOT/docs/skills/java-codebase-explore.md"
OUT_ZIP="$ROOT/docs/skills/java-codebase-explore.zip"

if ! command -v zip >/dev/null 2>&1; then
  echo "error: zip(1) is required but not found in PATH" >&2
  exit 1
fi

if [[ ! -f "$SRC_MD" ]]; then
  echo "error: missing skill source: $SRC_MD" >&2
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/explore-skill.XXXXXX")"
cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

# Perplexity bundle: canonical body + SKILL.md manifest (same body + §3.5 YAML).
cp "$SRC_MD" "$TMP/java-codebase-explore.md"
cp "$SRC_MD" "$TMP/SKILL.md"

# Normalized member mtimes for reproducible archives (portable: BSD/GNU touch).
touch -t 200001010000 "$TMP/java-codebase-explore.md" "$TMP/SKILL.md"

rm -f "$OUT_ZIP"
( cd "$TMP" && zip -X -q "$OUT_ZIP" java-codebase-explore.md SKILL.md )

echo "wrote $OUT_ZIP"
