#!/usr/bin/env bash
# Cross-compiles the code patch add-on to a 32-bit Windows .asi from Linux.
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_code_patches.sh
#
# Output: assets/mods/ff13-code-patches/01_ff13_code_patches.asi
#
# No MinHook here: it writes memory once at load and hooks nothing. The parser
# tests run first, so a build never ships a reader the suite hasn't passed.
set -euo pipefail

CXX=i686-w64-mingw32-g++
RC=i686-w64-mingw32-windres

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/ff13-code-patches"
out="$repo/assets/mods/ff13-code-patches"
work="${TMPDIR:-/tmp}/ff13-code-patches-build"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work" "$out"

g++ -std=c++20 -Wall -Wextra -Werror -o "$work/nccp_test" "$src/nccp_test.cpp"
"$work/nccp_test"

"$RC" "$src/version.rc" -O coff -o "$work/version.o"
# static, so there's no libstdc++ dll for the game's folder to be missing
"$CXX" -std=c++20 -O2 -s -shared -static -static-libgcc -static-libstdc++ \
  -Wall -Wextra -o "$out/01_ff13_code_patches.asi" "$src/ff13_code_patches.cpp" "$work/version.o"

# NovaFix skips anything that isn't i386, silently
machine=$(od -An -tx2 -j $(( $(od -An -tu4 -j 60 -N4 "$out/01_ff13_code_patches.asi") + 4 )) \
  -N2 "$out/01_ff13_code_patches.asi" | tr -d ' ')
[ "$machine" = "014c" ] || { echo "built for machine $machine, not i386" >&2; exit 1; }

echo "built $out/01_ff13_code_patches.asi"
