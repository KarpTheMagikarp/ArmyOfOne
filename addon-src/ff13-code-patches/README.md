# ff13-code-patches

A NovaFix add-on that applies Nova Chrysalia's code patches (`Code\<Name>.nccp`)
so those mods work without Nova installed. Works for all three games.

## What a .nccp is

Valve KeyValues text. The root object's children with all-digit names are each
`"RVA|bytes"`, both hex:

```
"MyMod"
{
    "1"  "2B54F|9090"
    "2"  "0x1A2B30|EB05"
}
```

Nova's launcher starts the game suspended and writes each one at the image base
plus the RVA. That's all a code patch is: bytes in memory, nothing on disk.

## Why an add-on

We can't start the game ourselves. Steam refuses a direct launch
(`Application load error 3:0000065432`), so there's no suspended process to
write into. The patches go in from inside instead, when NovaFix loads its
add-ons.

That's later than Nova writes. NovaFix loads add-ons from its one-time
`Initialize()`, which only runs on the game's first Direct3D9 factory call
(`Direct3DCreate9`, or `GetProcAddress` for it), so render init, after the
entry point and the game's startup code. A patch to code that has already run
by then won't take effect. Patches to gameplay code, which is what Nova mods
almost always touch, land before that code runs.

Patching the exe on disk instead would trip XIII's CEG self-check and get
undone by any Steam file verify. In memory, neither one ever sees it.

## What it does

On load it reads every `*.nccp` beside itself, sorted by name, and writes each
patch through `VirtualProtect`, then flushes the instruction cache. A patch
that falls outside the exe's image is skipped and logged rather than written.
The patcher already turned away files like that, and mods whose patches clash,
before they got here, so the order never decides anything.

It logs to `ff13-code-patches.log` beside it.

## Building

Visual Studio 2022 with the C++ workload, 32-bit:

```
cmake -B build -A Win32
cmake --build build --config Release
```

Output: `build/Release/01_ff13_code_patches.asi`. Copy it to
`assets/mods/ff13-code-patches/`. No dependencies.

mingw works too, and is how the bundled build was made:

```
tools/build_code_patches.sh
```

## Testing the parser without Windows

`nccp.h` has no Win32 in it. The pytest suite compiles `nccp_test.cpp` and also
checks it reads every file the same way `patcher/mods/nccp.py` does.

```
g++ -std=c++20 -Wall -Wextra -Werror -o nccp_test nccp_test.cpp && ./nccp_test
```

## Licence

GPL-3.0, matching the patcher. The `.nccp` format is Nova Chrysalia's;
this is a fresh implementation of reading it.
