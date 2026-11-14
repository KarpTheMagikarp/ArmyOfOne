#!/usr/bin/env bash
# Cross-compiles the NovaFix add-on to a 32-bit Windows .asi from Linux.
#
# The CMake path needs Visual Studio, which means a Windows machine to produce
# a file that is 600KB and changes about twice a year. mingw-w64 builds the
# same thing anywhere, including CI, and links against nothing the target
# machine does not already have (KERNEL32 and msvcrt only, because MinHook is
# C and the C++ runtime is statically linked).
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_addon.sh
#
# Output: assets/mods/ff13-exe-redirect/00_ff13_exe_redirect.asi
#
# The hash changes on every build even with identical sources, because mingw
# stamps a link timestamp into the PE header. That is why pin_bundled runs at
# the end rather than being something to remember.
set -euo pipefail

CXX=i686-w64-mingw32-g++
CC=i686-w64-mingw32-gcc
MINHOOK_TAG=v1.3.3

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/ff13-exe-redirect"
work="${TMPDIR:-/tmp}/ff13-addon-build"
out="$repo/assets/mods/ff13-exe-redirect"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work/obj" "$out"

# the one network step. pinned to a tag rather than a branch: MinHook sits in
# the hot path of every CreateFile the game makes, and "whatever master was
# that day" is not a thing to be vague about.
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$MINHOOK_TAG" \
  https://github.com/TsudaKageyu/minhook.git "$work/minhook"

for c in "$work"/minhook/src/*.c "$work"/minhook/src/hde/*.c; do
  "$CC" -c "$c" -I"$work/minhook/include" -I"$work/minhook/src" \
    -O2 -DNDEBUG -o "$work/obj/$(basename "${c%.c}").o"
done

"$CXX" -c "$src/ff13_exe_redirect.cpp" \
  -I"$src" -I"$work/minhook/include" \
  -std=c++20 -O2 -DNDEBUG -Wall -Wextra -municode \
  -o "$work/obj/addon.o"

# static everything, or the .asi needs libstdc++-6.dll beside it and NovaFix
# loads it into a process that has never heard of mingw
"$CXX" -shared -o "$out/00_ff13_exe_redirect.asi" "$work"/obj/*.o \
  -static -static-libgcc -static-libstdc++ \
  -Wl,--enable-stdcall-fixup -lkernel32 -luser32

# the guards are platform-free on purpose, so they run here rather than only
# on whoever next opens Visual Studio
g++ -std=c++20 -Wall -Wextra -Werror \
  -o "$work/policy_test" "$src/redirect_policy_test.cpp"
"$work/policy_test" >/dev/null

python3 "$repo/tools/pin_bundled.py" >/dev/null
python3 - "$out/00_ff13_exe_redirect.asi" <<'PY'
import hashlib, struct, sys, pathlib
data = pathlib.Path(sys.argv[1]).read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
machine, = struct.unpack_from("<H", data, pe + 4)
assert data[:2] == b"MZ" and data[pe:pe+4] == b"PE\0\0", "not a PE"
assert machine == 0x014C, f"machine is {machine:#x}, NovaFix only loads 0x14c"
print(f"built {len(data):,} bytes  sha256 {hashlib.sha256(data).hexdigest()}")
PY

rm -rf "$work"
