#!/usr/bin/env bash
# Cross-compiles XIII's Save Anywhere add-on to a 32-bit .asi.
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_xiii_save_anywhere.sh
#
# Output: assets/mods/xiii-save-anywhere/xiii_save_anywhere.asi and MinHook's
# licence, then every manifest pin is refreshed.
set -euo pipefail

CXX=i686-w64-mingw32-g++
CC=i686-w64-mingw32-gcc
RC=i686-w64-mingw32-windres
MINHOOK_TAG=v1.3.3
# only its plugin header, for the native notification the prompt rides on
NOVAFIX_TAG=v1.0.2

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/xiii-save-anywhere"
out="$repo/assets/mods/xiii-save-anywhere"
work="${TMPDIR:-/tmp}/xiii-save-anywhere-build"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work/obj" "$out"

git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$MINHOOK_TAG" \
  https://github.com/TsudaKageyu/minhook.git "$work/minhook"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$NOVAFIX_TAG" \
  https://github.com/Kartin8/FF13NovaFix.git "$work/novafix"
for c in "$work"/minhook/src/*.c "$work"/minhook/src/hde/*.c; do
  "$CC" -c "$c" -I"$work/minhook/include" -I"$work/minhook/src" -O2 -DNDEBUG \
    -o "$work/obj/$(basename "${c%.c}").o"
done

"$RC" "$src/version.rc" -O coff -o "$work/obj/version.o"
"$CXX" -c "$src/xiii_save_anywhere.cpp" -I"$work/minhook/include" -I"$work/novafix/include" \
  -std=c++20 -O2 -DNDEBUG -Wall -Wextra -o "$work/obj/xiii_save_anywhere.o"
"$CXX" -shared -s -o "$out/xiii_save_anywhere.asi" "$work"/obj/*.o \
  -static -static-libgcc -static-libstdc++ -lkernel32 -luser32

# NovaFix skips anything that isn't i386, silently, and finds the plugin API
# by its undecorated export
python3 - "$out/xiii_save_anywhere.asi" <<'PY'
import struct, sys, pathlib
data = pathlib.Path(sys.argv[1]).read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0", "not a PE"
machine, = struct.unpack_from("<H", data, pe + 4)
assert machine == 0x014C, f"machine is {machine:#x}, NovaFix only loads 0x14c"
assert b"NovaFixPlugin_Query\0" in data, "NovaFixPlugin_Query isn't exported undecorated"
PY

cp "$work/minhook/LICENSE.txt" "$out/LICENSE-MinHook.txt"

python3 "$repo/tools/pin_bundled.py" >/dev/null
rm -rf "$work"
echo "built $out/xiii_save_anywhere.asi"
