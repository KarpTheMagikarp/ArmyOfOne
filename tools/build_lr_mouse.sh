#!/usr/bin/env bash
# Cross-compiles Lightning Returns' mouse support add-on to a 32-bit .asi.
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_lr_mouse.sh
#
# Output: assets/mods/lr-mouse/lr_mouse.asi and MinHook's licence, then every
# manifest pin is refreshed. lr-mouse.ini beside it is committed as is, it's the player's file.
set -euo pipefail

CXX=i686-w64-mingw32-g++
CC=i686-w64-mingw32-gcc
RC=i686-w64-mingw32-windres
MINHOOK_TAG=v1.3.3

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/lr-mouse"
out="$repo/assets/mods/lr-mouse"
work="${TMPDIR:-/tmp}/lr-mouse-build"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work/obj" "$out"

# pinned to a tag: MinHook sits under every input poll the game makes
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$MINHOOK_TAG" \
  https://github.com/TsudaKageyu/minhook.git "$work/minhook"
for c in "$work"/minhook/src/*.c "$work"/minhook/src/hde/*.c; do
  "$CC" -c "$c" -I"$work/minhook/include" -I"$work/minhook/src" -O2 -DNDEBUG \
    -o "$work/obj/$(basename "${c%.c}").o"
done

"$RC" "$src/version.rc" -O coff -o "$work/obj/version.o"
"$CXX" -c "$src/lr_mouse.cpp" -I"$work/minhook/include" \
  -std=c++20 -O2 -DNDEBUG -Wall -Wextra -o "$work/obj/lr_mouse.o"
# static, so there's no libstdc++ dll for the game's folder to be missing
"$CXX" -shared -s -o "$out/lr_mouse.asi" "$work"/obj/*.o \
  -static -static-libgcc -static-libstdc++ -lkernel32 -luser32

# NovaFix skips anything that isn't i386, silently
python3 - "$out/lr_mouse.asi" <<'PY'
import struct, sys, pathlib
data = pathlib.Path(sys.argv[1]).read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0", "not a PE"
machine, = struct.unpack_from("<H", data, pe + 4)
assert machine == 0x014C, f"machine is {machine:#x}, NovaFix only loads 0x14c"
PY
# the add-on borrows the game's dinput8 rather than importing it, so a missing
# or odd one can't stop it loading
if i686-w64-mingw32-objdump -p "$out/lr_mouse.asi" | grep -qi "DLL Name: dinput8"; then
  echo "lr_mouse.asi imports dinput8, it should borrow the game's" >&2
  exit 1
fi

# BSD 2-Clause wants its notice beside the binary, so it rides along
cp "$work/minhook/LICENSE.txt" "$out/LICENSE-MinHook.txt"

python3 "$repo/tools/pin_bundled.py" >/dev/null
rm -rf "$work"
echo "built $out/lr_mouse.asi"
