# Windows notes

## Requirements

- 64-bit Windows 10 1803+ or Windows 11. Windows on ARM runs it under x64
  emulation, untested. Windows 7 can't run Python 3.12; 8.1 is untested.
- The Edge WebView2 runtime. Windows 11 ships it; on Windows 10 without it the
  app says so and links the Evergreen installer. If WebView2 is missing or too
  old, pywebview silently falls back to IE's engine, which can't run the page,
  so the app checks before the window shows.
- .NET Framework 4.7.2, built into Windows 10 since 1803. pythonnet hosts the
  WinForms window on it. Nothing else to install; the C runtime is bundled.
- Steam Deck and Linux aren't supported: the games run under Proton, but this
  is a Windows desktop app on WebView2, which Proton doesn't ship.

## Security prompts

- **SmartScreen ("Windows protected your PC", Unknown Publisher).** The exe
  isn't code signed. More info, then Run anyway.
- **Smart App Control.** On a fresh Windows 11 install with it on, unsigned
  apps Windows hasn't seen are blocked outright with no Run anyway. Only code
  signing fixes that.
- **No elevation.** `app.manifest` requests `asInvoker`. Most Steam libraries
  are writable without admin, and prompting everyone trains people to accept
  UAC from an unsigned binary. The app probes the install folder before writing
  and says what to do if it can't. A library under Program Files needs Run as
  administrator, which also disables drag and drop (Windows won't let a normal
  Explorer window drop into an elevated one); the file pickers still work.
- **Antivirus.** An unsigned exe writing into a Steam folder fits heuristic
  patterns. UPX is off in the spec because compressed unsigned binaries get
  flagged far more often.
- **Proxy DLLs.** XIII gets `d3d9.dll` (NovaFix) and `dinput8.dll` (the 4GB
  shim), both unsigned and loaded before the game starts, which is also what
  malware looks like. Scanners have deleted FF13Fix's `d3d9.dll` for years. If
  the game stops starting after a scan, check both are there; `--doctor` names
  them and says whether each is our build. Nova Chrysalia code patches add
  `01_ff13_code_patches.asi` to NovaFix's add-ons.
- **Mods from the drawer.** HD Fonts and GUI is a `version.dll` that hooks the
  game, and Defender flags it. A file Windows Security blocks comes back with
  its own error, and the drawer says it was blocked rather than broken.
  Allowing it under Windows Security > Protection history is the user's call;
  the drawer never does it.

## Install problems

- **Run from inside the zip.** Explorer extracts only the exe and PyInstaller
  stops with `Failed to load Python DLL ... _internal\python312.dll` before any
  of our code runs. Extract the whole zip first.
- **Blocked as downloaded.** Explorer copies the zip's Zone.Identifier onto
  every extracted file and .NET won't load a marked DLL. The app strips the
  mark from its own DLLs at startup (`access.unblock`); where it can't, it says
  to tick Unblock in the zip's Properties and extract again.
- **Long paths.** The manifest sets `longPathAware` (Windows 10 1607+ where the
  group policy allows). Where it doesn't, a write that fails on length says so
  instead of reporting an existing folder as missing.

## Display

The window opens at the size the display scale asks for, never bigger than the
screen, down to 960x540. The UI zooms to the window, so smaller is the same
picture smaller. See [ui.md](ui.md).

## The XIII-2 HD model tool

`FF XIII HD Models.exe` v4.0.0 can spin for hours on some models, so the app
writes `FF XIII HD Models.fixed.exe` beside it, one byte different, and runs
that. The mod's own exe is never changed. See [hd-install.md](hd-install.md).

## Where settings live

```
Windows   %LOCALAPPDATA%\FFXIIITrilogyPatcher
macOS     ~/Library/Application Support/FFXIIITrilogyPatcher
Linux     ~/.local/share/FFXIIITrilogyPatcher
```

Records, the patch log and the Nexus key go there, so a new build or a new
extract location doesn't forget what was done. Records from older builds are
brought across on first run. `portable.txt` next to the exe keeps everything
beside it instead.
