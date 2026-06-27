#!/usr/bin/env bash
# Build Claude Console–compatible skill zips for r1-market-tech-watcher.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PKG="$ROOT/skill-package"
BUNDLE="$PKG/r1-market-tech-watcher"
OUT="$ROOT"

cd "$PKG"

# Strip macOS metadata (common cause of "invalid format" on upload)
export COPYFILE_DISABLE=1

# Full bundle (SKILL.md only — self-contained, no references/)
rm -f "$OUT/r1-market-tech-watcher.zip" "$OUT/r1-market-tech-watcher.skill"
zip -r -X "$OUT/r1-market-tech-watcher.zip" r1-market-tech-watcher/SKILL.md

# .skill is the same zip; some Console UIs expect this extension
cp "$OUT/r1-market-tech-watcher.zip" "$OUT/r1-market-tech-watcher.skill"

echo "Built:"
ls -lh "$OUT/r1-market-tech-watcher.zip" "$OUT/r1-market-tech-watcher.skill"
echo ""
echo "Zip contents:"
unzip -l "$OUT/r1-market-tech-watcher.zip"
