#!/usr/bin/env bash
# Cross-compiles XIII-2's audio fix add-on to a 32-bit .asi.
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_xiii2_audio.sh
#
# Output: assets/mods/xiii2-audio/xiii2_audio.asi and MinHook's licence, then
# every manifest pin is refreshed. xiii2-audio.ini beside it is committed as
# is, it's the player's file.
set -euo pipefail

CXX=i686-w64-mingw32-g++
CC=i686-w64-mingw32-gcc
RC=i686-w64-mingw32-windres
MINHOOK_TAG=v1.3.3

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/xiii2-audio"
out="$repo/assets/mods/xiii2-audio"
work="${TMPDIR:-/tmp}/xiii2-audio-build"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work/obj" "$out"

# the sums first, natively. They're what a player hears, and the only part
# that runs without the game
g++ -std=c++20 -Wall -Wextra -Werror -o "$work/mix_test" "$src/mix_test.cpp"
"$work/mix_test"

# pinned to a tag: MinHook sits under the game's audio thread
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$MINHOOK_TAG" \
  https://github.com/TsudaKageyu/minhook.git "$work/minhook"
for c in "$work"/minhook/src/*.c "$work"/minhook/src/hde/*.c; do
  "$CC" -c "$c" -I"$work/minhook/include" -I"$work/minhook/src" -O2 -DNDEBUG \
    -o "$work/obj/$(basename "${c%.c}").o"
done

"$RC" "$src/version.rc" -O coff -o "$work/obj/version.o"
"$CXX" -c "$src/xiii2_audio.cpp" -I"$work/minhook/include" \
  -std=c++20 -O2 -DNDEBUG -Wall -Wextra -o "$work/obj/xiii2_audio.o"
# static, so there's no libstdc++ dll for the game's folder to be missing
"$CXX" -shared -s -o "$out/xiii2_audio.asi" "$work"/obj/*.o \
  -static -static-libgcc -static-libstdc++ -lkernel32

# NovaFix skips anything that isn't i386, silently
python3 - "$out/xiii2_audio.asi" <<'PY'
import struct, sys, pathlib
data = pathlib.Path(sys.argv[1]).read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0", "not a PE"
machine, = struct.unpack_from("<H", data, pe + 4)
assert machine == 0x014C, f"machine is {machine:#x}, NovaFix only loads 0x14c"
PY

# BSD 2-Clause wants its notice beside the binary, so it rides along
cp "$work/minhook/LICENSE.txt" "$out/LICENSE-MinHook.txt"

python3 "$repo/tools/pin_bundled.py" >/dev/null
rm -rf "$work"
echo "built $out/xiii2_audio.asi"
