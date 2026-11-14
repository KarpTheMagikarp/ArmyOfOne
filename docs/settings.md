# The Settings drawer

## Rows

XIII and XIII-2:

```
Display       Graphics Source, Monitor, Window Mode, Resolution, Refresh Rate
Performance   VSync, Frame Rate, Renderer, FPS Counter
Quality       Shadow Quality, Anti-Aliasing, Anisotropic Filtering
Language      Voices
```

Lightning Returns: Render Resolution, Frame Rate, Stutter Reduction, Renderer,
FPS Counter, Shadow Quality, Anti-Aliasing, Texture Filtering, NPC Spawn
Distance, under World instead of Language.

Order follows dependency: the monitor decides which resolutions and refresh
rates exist, window mode decides whether Resolution is read, and VSync sits
above Frame Rate because it decides what the frame rate does.

Labels and options are Title Case, enforced by a test as a rule (hyphens
split, so Anti-Aliasing needs both halves; a word starting with a digit
passes, so 16x, 4K, 60 FPS are fine).

## Reading and writing

- A game is read the first time its tab is offered, so the drawer opens on its
  rows. Opening reads again underneath and only redraws on a difference,
  keeping any edit in progress. A read after Apply starts after the save.
- Only keys the user changed are written (`patcher/novafix_config.py`). Both
  mods fill in anything missing on first boot, so a partial file is complete.
  Writes are line based to keep comments and section order.
- The mods read their ini once at startup, so a change does nothing until the
  next launch. A successful save offers Start game in the drawer footer when
  the game isn't already running.
- A row left on Default isn't written. A combination set by hand that matches
  no option reads as Default rather than a guessed label.

## One row, several keys

| Row | Writes | Why |
|---|---|---|
| Frame Rate (LR) | `TargetFpsX100`, `UnlockFramerate`, `SimDeltaFix` | the rate is only read while the limiter is unlocked, and interact prompts and stamina drain are wrong above 30fps without the third |
| VSync | `VSyncMode`, `TripleBuffering` | triple buffering does nothing with VSync off |
| Anti-Aliasing (LR) | `FxaaOff`, `MsaaSamples`, `SsaaScale` | three controls for one question |
| Shadow Quality (LR) | `ShadowMapRes`, `ShadowPcss` | same |
| Stutter Reduction (LR) | `DiscardFix`, `ReadPace`, `ShaderThrottle`, `LoaderThrottle` | Standard, Aggressive or Off |
| Resolution | `Width`, `Height` | two numbers that can disagree |

## Graphics Source

On XIII and XIII-2 Square Enix's launcher owns resolution, shadows and
anti-aliasing unless NovaFix's `OverrideLauncherGraphics` is on, in which case
NovaFix ignores the launcher. The row picks between the patcher and The Game's
Launcher. Changing any Display or Quality row switches it to the patcher
automatically; an explicit choice still wins. Picking the launcher brings its
window back (see [patching.md](patching.md)).

## Left out on purpose

- Ambient occlusion on LR: 28 of Savior's Patch's keys, and a look, not a fix.
- VSync on LR: NovaFix forces `D3DPRESENT_INTERVAL_IMMEDIATE` at device
  creation and reset, so the toggle would be overridden every frame.
- Off for Anti-Aliasing on XIII and XIII-2: NovaFix clamps `MSAA` into
  `{2,4,8,16}`, so a 0 comes back as 2.
- Window Mode on LR: neither mod has a key; it's in the game's own Graphics >
  Presentation menu.
- `RedirectExecutableReads`: stock NovaFix doesn't read it.

## Savior's Patch (LR)

Fixes LR's area-load stutter by reworking asset streaming and the frame pacer.
Its rows appear once its `dinput8.dll` is beside `LRFF13.exe` and names
`SaviorsPatch.ini` (`novafix_config.owner_in`). The ini is flat `Key=integer`
with `#` comments. Seven rows out of about 140 keys, every key and range read
off the mod's source (`g_toggles` and `g_numerics` in `08_config_persist.c`).
Render Resolution maps to `CustomResW`/`CustomResH`.

## Voices

`voice_mode` in the launcher's `setup.xml` (`patcher/voices.py`), the one
launcher setting NovaFix doesn't take over. 0 is Japanese (the `c` archives),
1 English (`u`).

- Edited like the launcher flag: that element only, never in a file that
  doesn't exist. Before a first launch the row is greyed as After First
  Launch.
- Only sets present on disk in full are offered (the character pair, plus on
  XIII the white_img pair).
- It won't change while an HD install is running on that game.
- If the set it lands on is missing HD Project categories the other set has,
  the row says so on pick and after Apply.

## The HD voices box

An HD install builds the set the launcher has picked. Ticking Japanese Voices
Too (or English, from the Japanese side) in the HD panel puts the result in the
other set as well, in the same run (`patcher/mods/hd_mirror.py`):

- Finished files are copied across, not rebuilt: appended to that set's
  archives, its indexes swapped in last, its own originals backed up first.
  Revert puts both back.
- A file only goes across when the two sets' vanilla copies match byte for
  byte, and a .trb always travels with its .imgb. A pass holding any file the
  languages ship differently is built for the other set by the mod's tools
  from that set's originals instead.
- Off by default because of disk: the other set gets its own backup and a
  second copy of the art, about 10 GB with every category on XIII-2. The size
  beside the box adds that up for what's ticked.
- Everything an install builds is recorded as owed to the other set, ticked or
  not. A refused, stopped or partial copy stays owed and ticking the box again
  finishes it. Only what went in whole is paid off.
- Ticked with nothing else, it's just the copy.

None of this is FMV or audio; the HD Projects don't touch cutscenes.
