#!/usr/bin/env bash
# Cross-compiles the dinput8 shim to a 32-bit Windows DLL from Linux.
#
# This one sits BESIDE ffxiiiimg.exe as dinput8.dll, which the game imports
# statically, so the loader runs its DllMain before the executable's entry
# point. That timing is the entire point of it and it is why the NovaFix add-on
# it replaces could never work.
#
#   sudo apt-get install -y g++-mingw-w64-i686
#   tools/build_shim.sh
#
# Output: assets/mods/ff13-dinput8-shim/dinput8.dll
#
# A DLL the game imports statically is one the game cannot start without, so
# the link is deliberately bare: no C++ runtime, no exceptions, no RTTI, and
# -nostdlib++ so a stray STL reference fails the build here rather than pulling
# libstdc++ into somebody's game folder. Check the import table afterwards, it
# is asserted below.
set -euo pipefail

CXX=i686-w64-mingw32-g++
CC=i686-w64-mingw32-gcc
RC=i686-w64-mingw32-windres
MINHOOK_TAG=v1.3.3

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo/addon-src/ff13-dinput8-shim"
work="${TMPDIR:-/tmp}/ff13-shim-build"
out="$repo/assets/mods/ff13-dinput8-shim"

command -v "$CXX" >/dev/null || {
  echo "no $CXX on PATH. apt-get install -y g++-mingw-w64-i686" >&2
  exit 1
}

rm -rf "$work"
mkdir -p "$work/obj" "$out"

git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$MINHOOK_TAG" \
  https://github.com/TsudaKageyu/minhook.git "$work/minhook"

for c in "$work"/minhook/src/*.c "$work"/minhook/src/hde/*.c; do
  "$CC" -c "$c" -I"$work/minhook/include" -I"$work/minhook/src" \
    -O2 -DNDEBUG -o "$work/obj/$(basename "${c%.c}").o"
done

"$CXX" -c "$src/dinput8_shim.cpp" \
  -I"$src" -I"$work/minhook/include" \
  -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror \
  -fno-exceptions -fno-rtti -fno-asynchronous-unwind-tables \
  -o "$work/obj/shim.o"

# the name NovaFix's add-on popup shows for it, rather than DINPUT8
"$RC" "$src/version.rc" -O coff -o "$work/obj/version.o"

# The base ld picked for the shipped build. Left to ld it moves from one build
# to the next, and every absolute address in .text moves with it, so it's
# pinned along with the timestamp: the same source builds the same code
"$CXX" -shared -o "$out/dinput8.dll" "$work"/obj/*.o \
  "$src/dinput8.def" \
  -static -static-libgcc -nostdlib++ \
  -Wl,--enable-stdcall-fixup -Wl,--exclude-all-symbols \
  -Wl,--image-base,0x647c0000 -Wl,--no-insert-timestamp \
  -lkernel32

g++ -std=c++20 -Wall -Wextra -Werror -o "$work/guard_test" "$src/image_guard_test.cpp"
"$work/guard_test" >/dev/null
g++ -std=c++20 -Wall -Wextra -Werror \
  -o "$work/policy_test" "$repo/addon-src/ff13-exe-redirect/redirect_policy_test.cpp"
"$work/policy_test" >/dev/null

python3 "$repo/tools/pin_bundled.py" >/dev/null
python3 - "$out/dinput8.dll" <<'PY'
import hashlib, pathlib, struct, sys

path = pathlib.Path(sys.argv[1])
data = path.read_bytes()
pe = struct.unpack_from("<I", data, 0x3C)[0]
machine, = struct.unpack_from("<H", data, pe + 4)
characteristics, = struct.unpack_from("<H", data, pe + 22)
assert data[:2] == b"MZ" and data[pe:pe + 4] == b"PE\0\0", "not a PE"
assert machine == 0x014C, f"machine is {machine:#x}, the game is 32-bit"
assert characteristics & 0x2000, "not marked as a DLL"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(path.parents[3]))
from patcher.patch_ops import read_static_imports

imports = read_static_imports(data)
# THE CHECK THAT MATTERS. The game imports this file statically, so a symbol the
# target machine cannot resolve is not a broken feature, it is a game that will
# not start, with no log and nothing to read.
assert set(imports) <= {"kernel32.dll", "msvcrt.dll"}, f"unexpected imports: {imports}"

import pefile

names = {i.name.decode() for e in pefile.PE(data=data).DIRECTORY_ENTRY_IMPORT
         for i in e.imports if i.name}
# the secure CRT specifically. _wfopen_s and friends arrived in msvcrt.dll at
# different Windows versions and mingw's import library claims them all
# unconditionally, so they are the class of symbol that resolves on the machine
# you built on and not on somebody else's.
risky = sorted(n for n in names if n.endswith("_s") or n.endswith("_s_l"))
assert not risky, f"secure CRT imports, which are not on every Windows: {risky}"

pe = pefile.PE(data=data)
exports = sorted(e.name.decode() for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name)
# Exactly what the real dinput8.dll offers, undecorated. A missing one is a
# game that will not start; a decorated duplicate means the .def did not take,
# and an import by plain name would then not resolve either.
assert exports == ["DirectInput8Create", "DllCanUnloadNow", "DllGetClassObject",
                   "DllRegisterServer", "DllUnregisterServer"], exports

strings = {}
for info in getattr(pe, "FileInfo", None) or []:
    for block in info:
        for table in getattr(block, "StringTable", None) or []:
            strings.update({k.decode(): v.decode() for k, v in table.entries.items()})
# what NovaFix's popup reads first, so the player sees what this is
assert strings.get("ProductName") == "FFXIII 4GB Compatibility", strings

print(f"built {len(data):,} bytes  sha256 {hashlib.sha256(data).hexdigest()}")
print(f"imports: {', '.join(imports)}")
print(f"{len(names)} symbols, none of them secure CRT")
print(f"exports: {', '.join(exports)}")
print(f"named: {strings['ProductName']}")
PY

rm -rf "$work"
