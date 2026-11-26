# ArmyOfOne

**One-click fixes and mod manager for the Final Fantasy XIII trilogy (XIII,
XIII-2 and Lightning Returns) on Steam.**

[![Downloads](https://img.shields.io/github/downloads/KarpTheMagikarp/ArmyOfOne/total)](../../releases)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

<!-- demo GIF goes here, linking to the full video -->

The Final Fantasy XIII trilogy on Steam unfortunately needs a pile of community
fixes before it runs the way it should, and setting them up usually means an
evening of guides, file copying and batch scripts. I wanted to fix this so it's
easier to get the games up and running, for newcomers and for anyone who just
wants to come back without the hassle. Enjoy!

## What you get

- The essential fixes, already picked for you, so you get smoother frame
  pacing, proper graphics options and the memory fix the games need, for XIII,
  XIII-2 and Lightning Returns.
- HD texture packs installed for you, just drop the FF XIII HD Project or
  FF XIII-2 HD onto the window and pick what you want. No command prompts, and
  it backs everything up first.
- Mouse support for Lightning Returns, since it shipped without any.
- Save Anywhere for XIII if you want it, so you can save from the pause menu
  while you're exploring and load right back where you were standing.
- Fixed audio for XIII-2, plus an optional visual preset I made for all three
  games.
- Other mods in one place. The ones I recommend have a button that opens their
  page and installs the download once it finishes, and you can drop anything
  else right onto the window.
- And everything comes back off, so if you remove a mod or undo everything, the
  game goes right back to how it was.

## Get started

1. Download `ArmyOfOne-windows.zip` from [Releases](../../releases/latest) and
   extract it.
2. Run `ArmyOfOne.exe`.
3. Pick a game and hit Apply, that's it!

You'll need Windows 10 or 11, 64-bit. The first time you open it, Windows will
probably show a SmartScreen warning since the app isn't code signed, just click
**More info** and then **Run anyway**. If your antivirus gets nervous or you
run into anything else on the first run, I put notes for that in
[docs/windows.md](docs/windows.md).

## Something broke?

Sorry about that! Open an issue and attach the log for whatever broke, and I'll
take a look when I can:

- The patcher: `%LOCALAPPDATA%\FFXIIITrilogyPatcher\patcher.log`
- NovaFix: `NovaFix.log` next to the game's exe (XIII: `white_data\prog\win\bin`, XIII-2: `alba_data\prog\win\bin`, LR: the game's main folder)
- Save Anywhere: `xiii-save-anywhere.log` in `white_data\prog\win\bin\addons\xiii-save-anywhere`

It helps a ton if you tell me what you clicked right before it broke too. And
if you just want everything undone, run `ArmyOfOne.exe ffxiii --revert` (or
`ffxiii2`, `ffxiii3`) from the app's folder and it'll put that game back how it
was.

## Under the hood

For anyone curious about how it all works:

- It's a Python app with a web UI (pywebview), packaged with PyInstaller
- Native C++ add-ons hook into the games directly for Lightning Returns' mouse
  support, Save Anywhere, XIII-2's audio fix, and a DLL shim that lets XIII's
  memory fix get past its DRM check
- It reads and writes the games' packed archive format itself, and every change
  gets journalled so it can be undone byte for byte
- Mods that come with their own `.bat` installers get run through the patcher's
  own interpreter against a copy of the game, so a broken script can't mess up
  your install
- About 4,000 automated tests, run on every push

If you want to build it yourself or dig into how each part works, start with
[docs/building.md](docs/building.md) and the rest of [docs/](docs).

## Credits

A huge thank you to Kartin8 for [NovaFix](https://github.com/Kartin8/FF13NovaFix),
the core fix this whole patcher is built around, and for letting me ship it
with ArmyOfOne.

Also included:

- ReShade by crosire, plus the SMAA, LumaSharpen, Curves and Vibrance shaders
  from CeeJay.dk's [SweetFX](https://github.com/CeeJayDK/SweetFX)
- The archive cipher is a port of Surihix's
  [WhiteCryptTool](https://github.com/Surihix/WhiteCryptTool), and the archive
  layout comes from his [WhiteBinTools](https://github.com/Surihix/WhiteBinTools),
  so a big thanks to him too

The licences, and the tools whose behaviour the patcher reimplements, are all in
[docs/credits.md](docs/credits.md). ArmyOfOne isn't affiliated with Square Enix.

ArmyOfOne, Copyright (C) 2026 Cody Le ([KarpTheMagikarp](https://github.com/KarpTheMagikarp)).
Licensed under [GPL-3.0](LICENSE).

If ArmyOfOne saved you some time and you'd like to support me, I have a
[Ko-fi](https://ko-fi.com/karpthemagikarp). Thanks for checking it out!
