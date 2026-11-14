# ArmyOfOne

**One-click fixes and mod manager for the Final Fantasy XIII trilogy (XIII,
XIII-2 and Lightning Returns) on Steam.**

[![Downloads](https://img.shields.io/github/downloads/KarpTheMagikarp/ArmyOfOne/total)](../../releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

<!-- demo video or GIF goes here, above the screenshot -->

The Final Fantasy XIII trilogy on Steam needs a pile of community fixes before
it runs the way it should, and setting them up usually means an evening of
guides, file copying and batch scripts. ArmyOfOne does all of it in one click,
from a menu built to look and feel like XIII's own.

![menu](assets/art_xiii.png)

## What you get

- **The essential fixes, already picked.** Smoother frame pacing, proper
  graphics options and the memory fix the games need, applied to Final Fantasy
  XIII, XIII-2 and Lightning Returns.
- **HD texture packs, installed for you.** Drop the FF XIII HD Project or
  FF XIII-2 HD on the window and pick what you want. No command prompts, and a
  backup is taken first.
- **Mouse support for Lightning Returns**, which shipped without any.
- **Save Anywhere for XIII** (optional). Save from the pause menu while you're
  exploring, and load back exactly where you stood.
- **Fixed audio for XIII-2** and an optional visual preset I made for all three
  games.
- **Other mods in one place.** Recommended mods are listed with a button that
  opens the page and installs the download when it lands. Anything else can be
  dropped on the window.
- **Everything comes back off.** Remove a mod, or undo everything, and the game
  goes back exactly how it was.

## Get started

1. Download `ArmyOfOne-windows.zip` from [Releases](../../releases/latest) and
   extract it.
2. Run `ArmyOfOne.exe`.
3. Pick a game and press Apply.

Windows 10 or 11, 64-bit. Windows will show a SmartScreen warning the first
time because the app isn't code signed: click **More info**, then **Run
anyway**. Antivirus and other first-run notes are in
[docs/windows.md](docs/windows.md).

## Something broke?

Open an issue and attach the log for whatever broke:

- The patcher: `%LOCALAPPDATA%\FFXIIITrilogyPatcher\patcher.log`
- NovaFix: `NovaFix.log` next to the game's exe (XIII: `white_data\prog\win\bin`, XIII-2: `alba_data\prog\win\bin`, LR: the game's main folder)
- Save Anywhere: `xiii-save-anywhere.log` in `white_data\prog\win\bin\addons\xiii-save-anywhere`

What you clicked right before it broke helps a lot. To undo everything the
patcher did to a game, run `ArmyOfOne.exe ffxiii --revert` (or `ffxiii2`,
`ffxiii3`) from the app's folder.

## Under the hood

- Python app with a web UI (pywebview), packaged with PyInstaller
- Native C++ add-ons that hook the games directly: Lightning Returns' mouse
  support, Save Anywhere, XIII-2's audio fix, and a DLL shim that lets XIII's
  memory fix get past its DRM check
- Reads and writes the games' packed archive format natively, with every
  change journalled so it can be undone byte for byte
- Plays mods' own `.bat` installers through its own interpreter, against a
  copy of the game, so a broken script can't damage the install
- About 4,000 automated tests, run on every push

Building from source and how each part works: [docs/building.md](docs/building.md)
and the rest of [docs/](docs).

## Credits

Special thanks to **Kartin8** for [NovaFix](https://github.com/Kartin8/FF13NovaFix),
the core fix this patcher is built around, and for letting it ship with
ArmyOfOne.

Also included:

- **ReShade** by crosire, and the SMAA, LumaSharpen, Curves and Vibrance shaders
  from CeeJay.dk's [SweetFX](https://github.com/CeeJayDK/SweetFX)
- The archive cipher is a port of Surihix's
  [WhiteCryptTool](https://github.com/Surihix/WhiteCryptTool), and the archive
  layout comes from his [WhiteBinTools](https://github.com/Surihix/WhiteBinTools)

Licences and the tools whose behaviour the patcher reimplements are in
[docs/credits.md](docs/credits.md). Licensed GPL-3.0. Not affiliated with
Square Enix.

If ArmyOfOne saved you some time and you'd like to support it, I have a
[Ko-fi](https://ko-fi.com/karpthemagikarp).
