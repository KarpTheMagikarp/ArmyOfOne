# NovaFix and ReShade

## NovaFix

[NovaFix](https://github.com/Kartin8/FF13NovaFix) by Kartin8 (GPL-3.0) is the
`d3d9.dll` proxy on all three games, bundled and pinned.

- **FF13Fix.** NovaFix scans loaded modules for FF13Fix markers and quietly
  disables its own overlapping features (frame pacing included) when it finds
  one. Applying NovaFix removes our old FF13Fix install (`replaces=`), and any
  FF13Fix-based `.dll` beside the exe, put there by hand or by a drawer mod, is
  flagged in the warning colour on its row and by Apply.
- **DXVK** isn't bundled or applied. It's a backend NovaFix can chain to: a
  user-supplied `dxvk.dll` beside the proxy with `D3D9Backend=LocalDXVK`.
- **The 4GB patch** goes on all three games, since all three are 32-bit exes.
  NovaFix's layout check compares machine, optional header magic, SizeOfImage
  and `.text` size, none of which the LAA flag changes. XIII needs a cover for
  its CEG check ([4gb-patch.md](4gb-patch.md)). XIII-2 and LR take the flag
  directly; for LR nothing on disk says whether its Steam build validates
  itself, so `lr_laa_guard` checks the image is 32-bit and leaves the rest to
  the launch check.
- **Hybrid GPUs.** These 32-bit D3D9 exes match no driver profile, so laptops
  run them on the integrated GPU. Launch sets the exe to High Performance in
  Windows' per-app graphics setting (`patcher/gpu_preference.py`), only where
  the exe has no preference yet. Revert removes it while it's still ours.
- No default run depends on another launcher or mod manager; a test enforces
  that.

### Add-on folder

NovaFix scans the first of `addons`, `extra`, `extras`, `mods`, `dll` that
exists, creating `addons` when none do, and only that one.
`novafix_config.resolve_addons_dir` resolves it the same way: writing to
`addons/` while NovaFix reads `mods/` installs an add-on that never loads, and
creating `addons/` beside an existing `mods/` hides the user's add-ons. A test
reads the probe list and its order out of the shipped binary.

The patcher's own add-on folders (read off the manifests,
`UiBridge.our_addon_folders`) are left out of the Mods drawer's list and
can't be removed from it, since they're part of the 4GB patch, Save Anywhere
and so on.

### The loaded add-ons popup

NovaFix names each dll by its version resource's ProductName or
FileDescription, else its file name, and lists only dlls beside it or in its
add-on folder. The 4GB shim carries `FFXIII 4GB Compatibility`
(`addon-src/ff13-dinput8-shim/version.rc`). HD Fonts and GUI's `version.dll`
has no version resource, so it shows as `VERSION`. Mod dlls are never renamed:
src_38 added version resources to drawer mods' proxies and broke XIII-2 and LR
launching, and `patcher/pe_name.py` restores any dll it touched to the
author's exact bytes. ReShade loads from `ReShade\` as the renderer, so it
isn't in the list.

### Defaults set once

See [patching.md](patching.md#novafix-ini-defaults-once-only): the overlay menu
moves to F8 only, and the popup goes to WhenChanged. Each only while every key
in its group is still NovaFix's default, read the way NovaFix reads them, and
only for a `d3d9.dll` that is NovaFix. A read-only `NovaFix.ini` is left alone
and the drawer says so.

### Graphics Preset

Low, Medium and High set shadows, anti-aliasing and filtering together
(NovaFix's keys on XIII and XIII-2, Savior's Patch's on LR). Auto picks one from
the largest GPU's memory in the registry and the desktop width
(`patcher/hardware.py`). Editing any row under it reads Custom. The preset
isn't an ini key; Auto is remembered in `.patcher-undo\graphics-preset.json`.
It never touches the frame rate.

### Why settings apply on next launch

NovaFix reads `NovaFix.ini` once at startup. The shipped build imports
`GetPrivateProfileStringW` and `WritePrivateProfileStringW` and no
`ReadDirectoryChangesW` or `FindFirstChangeNotification`, and its only exports
besides the D3D9 entry points are `NovaFix_IsOverlayOpen` and
`NovaFix_RestartHelper`. Its overlay applies some settings live through
internal code that isn't exposed. A test pins this, so a NovaFix that gains a
reload shows up as a failure.

`patcher/running.py` checks for the game with a Toolhelp snapshot, by the path
each process started from (an elevated process that won't say counts by exe
name). The Settings drawer still saves while the game runs and says it takes
effect next launch. It never offers to close the game. Everything that writes
game files (Apply, HD installs, the voices copy, drawer installs and removes,
`--revert`) refuses to start while it's running, since Windows won't replace
files the game has open.

## ReShade

NovaFix's readme: the 32-bit D3D9 ReShade goes in a `ReShade` folder beside the
proxy as `d3d9.dll`, with its settings and `reshade-shaders`. NovaFix loads it
when the renderer is Automatic, after `d3d9_chain.dll` and before the system's.
That folder is beside the exe on XIII and XIII-2 and in the game root on LR.

### The Fabula-Lous Shader row

ReShade 6.8.0 plus our preset, bundled, on all three games, optional.

- Generic Depth is turned off (`[ADDON] DisabledAddons=Generic Depth`,
  `engine.depth_off`): nothing in the presets reads depth, and the add-on
  watches every draw call. Written once, never while the game runs, and never
  over an existing `DisabledAddons` line or a read-only `ReShade.ini`.
- `ReShade.ini` and the preset are the user's once changed (ReShade rewrites
  them on exit). A changed one stays and ours lands beside it as `.new`, but
  only when ours has changed since it went in. Status goes by the dll and
  shaders.
- A ReShade of the user's own in that folder is left alone and the row says so.
- Unticking leaves it in. It's listed under Installed with a Remove, which
  leaves a changed `ReShade.ini` behind.
- The preset used to be called Crystarium; an old install is moved over with
  its tweaks (`engine.renamed_bundled`).
- Apply fixes one renderer case as the shader goes in: `LocalDXVK` with no
  `dxvk.dll` (NovaFix falls back to Windows' D3D9 and logs "dxvk.dll is
  missing") goes back to Automatic. DXVK that's present, System, or a
  `d3d9_chain.dll` beside NovaFix are left and the row says what's blocking
  ReShade. The renderer is read the way NovaFix's `ReadChoice` reads it, and a
  UTF-16 `NovaFix.ini` stays UTF-16.

### The grade (`FabulaLous.fx`)

One pass after SMAA (High: 16 edge steps, 8 diagonal) and LumaSharpen.

- **Base.** Curves' smoothstep S-curve at 0.15 for the shadows; above mid-grey
  a bump (`luma^6 * (1 - luma)`, peak at 6/7) brings the bright end down by
  `Highlights` instead of up, up to 9.5 levels of 255 with the shipped values.
  White and black stay put and the curve is monotonic for every slider value.
  Vibrance (SweetFX's rule) fades out over the brightest third and over the
  last quarter toward black or white, so no channel is pushed past either.
- **XIII.** Contrast 0.15, Highlights 0.05, Vibrance 0.12. Places like
  Orphan's Cradle are already near white in the game.
- **XIII-2** (`FABULA_HAZE`). XIII-2 lays a grey veil over sunlit areas. The
  frame's darkest level is measured (darkest channel per 20px patch on a 32x18
  grid, letterbox bars excluded) and settled over time (150ms down, 400ms up).
  Above 8% of white, up to 45% of it comes off, at most a quarter of the range,
  as one lift per pixel off its darkest channel so hues hold. On a big drop
  (a cut to a dark room) the veil follows the frame's own level instead of
  crushing it. Contrast 0.12, Highlights 0.02, Vibrance 0.24.
- **LR** (`FABULA_LIFT`). LR has a day/night clock, so it reads a quarter-size
  frame every frame (brightest cell, mean, veil) on the same 32x18 grid,
  settled over time. The top is stretched only by the room under white, up to
  0.05, with a tanh shoulder from 0.85. Haze 0.15, Clarity 0.25 (local
  contrast, damped on hard edges so the HUD doesn't halo), Glow 0.06 (dark
  frames only), Blacks 0, Contrast 0.14, Highlights 0.02, Vibrance 0.10.
  Sky-lit white surfaces keep the game's tint. Behind the field HUD (gauge
  bottom left, map and clock top right) the grade fades back to the game's
  picture (`Hud` 1.0), at 16:9 only. Everything reads a quarter and an eighth
  size copy plus the 32x18 grid, well under a millisecond at 4K.

`tests/test_shader_compile.py` runs every effect each preset uses through
ReShade 6.8's own preprocessor and parser with that preset's defines; an effect
that fails to parse silently doesn't run in game. CI builds the checker with
`tools/fxcheck/build.sh`. Provenance and licences are in
`assets/mods/reshade/README.txt`.

### ReShade from the Mods drawer

A dropped ReShade is recognised by its `ReShadeVersion` export, like NovaFix
does. It goes in as `ReShade\d3d9.dll`, settings as `d3d9.ini` (4.x) or
`ReShade.ini` (5+), presets, shaders and its own folders where they sat, and
any search path that pointed outside its folder or at its author's PC is
repointed. Other mods in the same download stay out. Two builds in separate
folders are versions to pick from. Without NovaFix, or with a renderer or
`d3d9_chain.dll` that keeps it from loading, the row says why.
