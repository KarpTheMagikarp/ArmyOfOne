#!/usr/bin/env bash
# Source drop for someone who already has the media. Everything git tracks,
# minus the art and video in assets/, plus the two files in there that are
# generated and therefore gitignored.
#
# That last part is the whole reason this exists as a script. assets/manifest.json
# is built by tools/build_ui_manifest.py and ignored by git, so a zip made from
# `git ls-files` leaves whatever was in the target's assets folder from last
# time. The page fetches that file at boot, so a category unlocked in
# manifests/ still read NOT YET on a machine that had an older copy.
set -euo pipefail

cd "$(dirname "$0")/.."
out="${1:-ffxiii-patcher-src.zip}"
rm -f "$out"

python3 tools/build_ui_manifest.py >/dev/null

{
  git ls-files | grep -v '^assets/'
  # the small tracked things under assets that are data rather than media
  git ls-files 'assets/**/*.txt' 'assets/*.json'
  # and the generated pair the app reads at boot
  ls assets/manifest.json assets/glyphs.json 2>/dev/null || true
} | sort -u | zip -q "$out" -@

printf '%s  %s\n' "$(du -h "$out" | cut -f1)" "$out"
unzip -l "$out" | grep -c assets/ | xargs printf 'assets entries: %s\n'
