# XIII's 4GB patch

## The problem

XIII's Steam exe is CEG protected and reads itself back to validate. The 4GB
patch sets `IMAGE_FILE_LARGE_ADDRESS_AWARE` in the PE header, which has to be
in the file Windows maps, which is the file the check reads. Unhidden, Steam
refuses the launch: `Application load error 3:0000065432`.

FF13Fix (rebtd7, `d3d9ex/AutoFix.cpp`) handled it from its `d3d9.dll` proxy:

1. hook `CreateFileA/W` so the check's open of `ffxiiiimg.exe` gets
   `untouched.exe`
2. rewrite the mapped headers (`FF13_HandleLargeAddressAwarePatch`: clear the
   LAA bit at `base + 0x126`, zero the checksum at `base + 0x168`), since the
   check also compares memory against the file

The kernel sized the address space at `CreateProcess`, so clearing the bit
afterwards keeps the 4GB.

Not needed for a plain 1080p install. FF13Fix's readme scopes it to triple
buffering, 4K+, DXVK or ReShade, and HD texture or model mods.

## Why not a NovaFix add-on

The first version did the hook as a NovaFix add-on
(`addon-src/ff13-exe-redirect`, now retired). It never fired. NovaFix 1.0.2
loads add-ons in `compat::addon_loader::Initialize()`, called from its
one-time `Initialize()`, reached only through `EnsureInitialized()`, whose
callers are the D3D9 proxy exports (`Direct3DCreate9` and friends) and its
`GetProcAddress` hook for D3D factory exports. `DllMain` only runs `Attach()`.
So add-ons load at the game's first Direct3D9 factory call, render init. The
CEG check runs in the entry point, and a refused process never gets to render
init: a refused launch left no `ff13-exe-redirect.log` at all.

## The shim

What runs before the entry point is a statically imported DLL's `DllMain`.
NovaFix has `d3d9.dll`, so `patch_ops.read_static_imports` (also printed by
`--doctor`) was used to pick another slot:

```
early-load targets
    dinput8.dll, winmm.dll, version.dll, d3d9.dll. 15 static imports in
    total: d3dx9_43.dll, dinput8.dll, winmm.dll, ws2_32.dll, kernel32.dll,
    user32.dll, advapi32.dll, ole32.dll, oleaut32.dll, bink2w32.dll,
    version.dll, imm32.dll, d3d9.dll, gdi32.dll, shell32.dll
```

`addon-src/ff13-dinput8-shim` builds `dinput8.dll`: five exports forwarded to
System32's, plus both halves of FF13Fix's fix.

- `image_guard.h` computes the header offsets from `e_lfanew` instead of
  hardcoding them, and `image_guard_test.cpp` checks they come out as `0x126`
  and `0x168` for XIII's header. The values written come from `untouched.exe`,
  not an assumed zero checksum.
- The hook disarms after the check's read. Writes are never redirected, or a
  4GB patcher would rewrite `untouched.exe`.
- Built with `-nostdlib++`, no exceptions or RTTI. `tools/build_shim.sh`
  asserts kernel32 and old msvcrt imports only, no secure-CRT `_s` symbols
  (mingw's import lib claims msvcrt has them all; real Windows versions vary),
  and exactly the five undecorated exports. A static import that fails to load
  is a game that won't start, with no log.
- Writes `ff13-dinput8-shim.log` beside the game, first line before any
  decision, so "didn't load" and "loaded and stayed inert" are distinguishable.

Taking the 4GB patch off always takes our shim with it, since a leftover
static import the loader can't use keeps the game from starting. Only ours: an
unrecognised `dinput8.dll` (ReShade, input mods) is refused as cover and named
by `--doctor`, never deleted.

## Covers

`engine.current_cover` says what's hiding the patch, and both the guard and
the launch check read it:

| Cover | Meaning |
|---|---|
| `dinput8_shim` | our pinned shim is beside the exe |
| `proxy_config` | the `d3d9.dll` proxy's own ini sets `RedirectExecutableReads` (only a NovaFix fork that reimplements FF13Fix's redirect writes it; stock NovaFix doesn't read it) |
| `none` | nothing; on the Steam exe the patch stays off |

The community replacement exe has no CEG and takes the patch with no cover.
`ff13_laa_guard` decides per Apply and repairs an install a bad patch already
broke, from `untouched.exe` when that copy passes `clean_copy` (same length,
flag off, same PE fingerprint), otherwise by clearing the flag in place.

## Verifying by launching

Static checks can all pass and the game can still be refused, so the launch
is the check.

1. `shell.Api._verify_launch` watches every launch on a daemon thread. Only a
   launch with the exe patched can file a refusal.
2. `launch_watch.read_signals` gives `ok`, `drm_error`, `crashed` or
   `unknown`. Signals are a process that died within twelve seconds, and error
   windows. `unknown` (a cancelled Steam prompt, a slow machine) does nothing.
   - **Crashes.** A fast death gets its exit code (from a handle opened as the
     process appears) and the Application Error event (1000), read with
     `wevtutil` for up to eight seconds. A crash in any module but the exe is
     `crashed`, named by owner (`bridge.module_owner`), never blamed on the
     patch. One in the exe stays a refusal, since a wrapper that finds itself
     patched can crash from its own code, and a crashing shim is the cover
     failing.
   - **Patch off.** A fast death with the patch off clears any refusal, since
     it can't have been the patch.
   - **Two kinds of error window.** Steam's box means the DRM refused.
     `<exe> - System Error` and friends mean the loader refused the shim.
     `is_error_title` matches by shape against the exe name, which isn't
     translated. Polled every 0.2s, since a loader-killed process only lives
     about that long.
   - A process gone for three seconds counts as gone, so a game handing
     itself back to Steam isn't a death.
3. `drm_error` goes in `launch_checks` (outside `applied_patches`, so a revert
   can't erase it), against the patch and the cover, with what was seen
   (`evidence`). The record is written before anything that can fail. A record
   with no cover predates covers and decides nothing; one against no cover
   (LR) needs evidence to count.
4. Steam's modal error box keeps the refused process alive with the exe
   mapped. `launch_watch.wait_until_replaceable` polls by opening for write,
   which is what the image lock forbids; a rename succeeds on a mapped image.
5. Once free, the exe is restored from `untouched.exe`. If the wait times out,
   the page says to press Apply and `ff13_laa_guard` repairs from the record.
6. A required row a refusal holds off reads SKIPPED (`engine.held_off`), so it
   doesn't hide Launch.

`--retry-4gb` forgets a recorded refusal, since a machine mid Steam update can
produce a bad reading. `untouched.exe` is never deleted; it's the only clean
copy.

## Licence check

None of this weakens Steam's licence check. Steam still has to be running, the
account still has to own the game, and CEG still validates Square Enix's
unmodified bytes already on the disk. Nothing is redistributed.
