# Patching

## What each patch is

| Game | Patch | Mechanism |
|---|---|---|
| all three | NovaFix | bundled `d3d9.dll` proxy: port fixes, frame rate, VSync, graphics options. Replaces FF13Fix, which it's incompatible with |
| all three | Fabula-Lous Shader (optional) | bundled ReShade 6.8 as `ReShade\d3d9.dll` beside NovaFix, plus our preset (SMAA, LumaSharpen, `FabulaLous.fx`). Home opens its menu |
| all three | HD Fonts and GUI | Worth getting; a `version.dll` loader mod beside the exe |
| XIII, XIII-2 | 4GB Patch | sets the PE large-address-aware flag in place |
| XIII | 4GB Compatibility | `dinput8.dll` shim, see [4gb-patch.md](4gb-patch.md) |
| XIII | Save Anywhere (optional) | NovaFix add-on, see below |
| XIII | HD Project | dropped on the Mods drawer, see [hd-install.md](hd-install.md) |
| XIII | HD Models Plus | Worth getting; its model tool is ported to Python and checked byte for byte against the original. Goes in over the HD Project's characters |
| XIII-2 | Audio Fix | NovaFix add-on, see below |
| XIII-2 | FF XIII-2 HD | dropped on the Mods drawer, same pipeline as XIII's |
| XIII-2 | Console Content Patch | Worth getting; its `.bat` is read, not run, and the 1.0 costumes decoded from its xdelta patches |
| LR | Mouse Support | NovaFix add-on, see below |
| LR | Savior's Patch | Worth getting; `dinput8.dll` beside `LRFF13.exe`, settings in the drawer |
| LR | DLC Restoration | Worth getting; Surihix's two patchers' `PatchData.bin` read and applied in both voice sets ([file-mods.md](file-mods.md)) |

## Is it applied?

Read from disk, never from the log, strongest first:

| Kind | Checked by |
|---|---|
| binary patch | the bit in the PE header, reading only the first page |
| bundled file | sha256 against the manifest pin |
| downloaded file | sha256 recorded at install (`state.applied_recorder`), since a download can't be pinned |
| archive injection | the entry the index points at, hashed against the payload's pin; else the span journal's regions read back |

An archive whose size changed at all was replaced wholesale, so the answer is
no. A record alone never says applied: it survives a Steam verify, a deleted
file and another mod writing the same name.

## Manifest rules

- `requires`: a hard block. A test checks no manifest lists a prerequisite
  after its dependent.
- `source_subdir`: which folder to take when an archive ships the same name
  twice (DXVK's `x32/` and `x64/d3d9.dll`). Without it, a collision refuses.
- `requires_backup`: XIII's binary patches keep `untouched.exe`, which the
  shim serves to the DRM.
- Release archives can be `.zip` or any tar flavour.
- `manual`: things Apply can't do (LR's audio dropout is a Windows sound
  setting). The app links them.
- `kind: "extra"` in `ui/jobs.json`: mods that end in someone else's installer
  or a login. They're listed in the Mods drawer with a link instead of being
  tickable, and a test keeps them out of Apply.

## The job list

`ui/jobs.json` is the source. `tools/build_ui_manifest.py` stamps `wired` on
each job (whether a manifest entry backs it) and regenerates the
`FALLBACK_MANIFEST` copy inside `index.html`; a test checks they agree. A job
with no manifest entry reports SKIPPED.

## Launching

Through Steam for anything with an appid, no second condition
(`launch.plan`). Running `ffxiiiimg.exe` directly gets `Application load
error 3:0000065432`, with or without `SteamAppId`/`SteamGameId` set, and so
does the CEG-free community exe: it's still a Steam app. A test pins
`launch.plan`'s signature and checks its body never mentions `SteamAppId`,
`ceg` or `safe_to_edit`. Direct launch is only for builds with no appid.

## Square Enix's launcher

XIII and XIII-2 start behind `FFXiiiLauncher.exe`. Its "Do not show this window
again" box writes `<confirm_mode>true</confirm_mode>` into `setup.xml` in the
game root, and `patcher/launcher_skip.py` writes that same value.

- `follow()` decides, and Apply, Launch, opening the game in the patcher and a
  Graphics Source pick all ask it, so Steam's Play skips the launcher too.
- Hidden once our NovaFix is on and overriding the launcher's graphics (read
  the way NovaFix reads its ini; a newer NovaFix counts). Picking The Game's
  Launcher as Graphics Source shows it again.
- Hidden once. If the user brings it back by hand, that's respected. Revert
  only shows it, and only if we hid it (`.patcher-undo\launcher-hidden`).
- Never touches LR, which has no launcher.
- `setup.xml` is edited as text, never created: it stores resolution and
  shadows as undocumented enum indices. A never-started game shows the
  launcher once, then the next `follow()` catches it.
- Best effort: a failed edit is logged and the game still starts.

## NovaFix ini defaults (once only)

Apply sets a few `NovaFix.ini` groups once, and only while every key in a
group is still NovaFix's default (`novafix_config.ONCE`): NovaFix's own menu
moves to F8 only, off Grave, F10 and the gamepad combo (the Settings drawer
replaces it; NovaFix puts F10 back if both keyboard keys are off), and the
loaded add-ons popup goes to WhenChanged (1.0.2 defaults to every launch).
Each group's marker in the undo store holds the previous values; Revert puts
them back.

## Save Anywhere (XIII)

A NovaFix add-on, `addon-src/xiii-save-anywhere`. On the field pause screen, Y
(Triangle, E) presses Resume and fires the game's own `TRG_SAVE_REQ` once the
zone state machine is back in `FIELD_IDLE`, so the normal station save screen
runs. It adds no game logic and never edits a save.

- Where the player stood goes in `save-locations\ff13-NN.where` in the
  add-on's folder (a hash of the save, the save point id, the world matrix and
  a short trail of recent footing). Loading that save, the start marker lookup
  answers with the saved spot instead of the station's. A station save over
  the slot deletes the `.where`.
- Only offered with no scene since the player was last at a station, the
  story number and UI/menu locks unchanged, the game's own footing flag set
  (not on a lift, platform or in the air), and nothing carrying the player.
- On load, a straight drop means the floor wasn't rebuilt there, so the player
  is moved back along the trail, then to the station.
- Every hooked site is byte checked against the Steam `ffxiiiimg.exe`; any
  mismatch and it stays off. `patcher/save_anywhere.py` lists the sites, and
  code mods overlapping them are refused while it's installed.
- `SaveButton`, `Prompt` and `Debug` in `xiii-save-anywhere.ini`. The log
  rotates past 1 MB.

The source header has the hook addresses and the reasoning.

## Mouse Support (LR)

A NovaFix add-on, `addon-src/lr-mouse`. LR's keyboard path never reads a
controller, so mouse input goes into the game's own input code: clicks through
the pad's one-frame held mask (left is RB, the strike; right is cancel, the
jump), camera on pad 0's right stick after the update (lifted past each
camera's deadzone, divided by frame time), and menus through the GUI manager's
top window (wheel is up/down, left decide, right cancel). Raw DirectInput, no
Windows acceleration. Sensitivity, invert and `Menus=0` are in
`addons\lr-mouse\lr-mouse.ini`, read live. The source header has the addresses.

## Audio Fix (XIII-2)

A NovaFix add-on, `addon-src/xiii2-audio`. Three bugs in the Steam
`ffxiii2img.exe`'s sound driver, each fixed where it happens:

- Muffle: every voice goes through the game's own IIR filter XAPO, whose
  `SetFrequency` takes XAudio2 units and maps them through asin(F/2)/pi.
  XAudio2's 1.0 means no filter; on this biquad it's a low pass at a sixth of
  the sample rate (-4 dB at 8 kHz, -20 dB at 16 kHz at 48 kHz), and it's
  where voices sit by default. The low pass coefficient call returns a
  pass-through biquad at that top setting, faded in from about 0.91 so a
  filter sweeping open doesn't jump. Lower settings are the game's own.
- Clipping: on stereo the master bus ends in `StereoMixdownAPO`, whose matrix
  sums to nearly 3x full scale per side. Its `Process` is replaced with an ITU
  style downmix (centre and surrounds at -3 dB, the game's own LFE share) and
  a peak limiter that ducks instead of clipping.
- 7.1 buzz: the same APO sits on every device, switched off unless it's
  stereo, and switched off it copies 6 channels into 8 and never writes the
  last two. The replacement writes every channel and shares the surrounds with
  7.1's side pair.

Cutscenes play through Bink's waveOut output, outside the XAudio2 graph, so
they're still stereo. `ClearVoices`, `StereoLimiter` and `Surround` in
`addons\xiii2-audio\xiii2-audio.ini` switch each fix off, read at launch. Every
hooked site is byte checked against the Steam exe. `mix.h` holds the maths and
`mix_test.cpp` runs on every build. The source header has the addresses.

## Archive injection

Mods that live inside `filelist*.win32.bin` / `white_img*.win32.bin` are
injected natively. Every language archive is one transaction: validate, write
with an undo journal, read back, roll everything back on any failure. The undo
is about 680 KB against 3 GB of archives, since only changed spans are kept.
See [archive-format.md](archive-format.md).
