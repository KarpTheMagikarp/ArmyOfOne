# Building

## The exe

```
pip install -r requirements.txt pyinstaller pytest
python tools/build_ui_manifest.py     # generates assets/manifest.json
pytest -q
pyinstaller --noconfirm --clean ArmyOfOne.spec
```

Output is `dist/ArmyOfOne/`. Windows only, since PyInstaller can't
cross-compile. Pushing a `v*` tag runs the same steps in
`.github/workflows/release.yml` and attaches the zip to a release.

The spec builds onedir, not onefile: onefile re-extracts the payload (mostly
video) to `%TEMP%` on every launch and Defender rescans it each time.

`tools/check_bundle.py <dist>/_internal` fails if a manifest names a bundled
file the bundle doesn't contain. CI runs it after the build. With no argument
it checks the checkout.

## From source

```
python shell.py            # the window
python shell.py --debug    # with devtools (right click > inspect)
python shell.py --no-gpu   # force software rendering
python main.py ffxiii --dry-run
python main.py --doctor    # read-only report of every install it finds
```

`assets/manifest.json` is generated and gitignored. If a toggle looks stale
after updating the source, run `python tools/build_ui_manifest.py`.

## Pinned files

NovaFix ships in the repo with Kartin8's permission (GPL-3.0). Every bundled
file is pinned by sha256 in the manifests; `tools/pin_bundled.py` rewrites the
pins after a replacement and `--check` verifies them (the test suite runs it).
User-supplied files are never pinned, only checked for shape, since a pin
would reject every build but one.

## Native add-ons

Cross-compiled from Linux with mingw-w64:

```
sudo apt-get install -y g++-mingw-w64-i686
tools/build_shim.sh                # dinput8 shim (XIII's 4GB cover)
tools/build_code_patches.sh        # applies Nova Chrysalia .nccp patches
tools/build_lr_mouse.sh            # LR mouse support
tools/build_xiii_save_anywhere.sh  # XIII save anywhere
tools/build_xiii2_audio.sh         # XIII-2 audio fix
tools/build_addon.sh               # retired exe-redirect add-on
```

Each fetches MinHook at a pinned tag, runs the host-side policy tests and
repins the manifest. mingw stamps a link timestamp into the PE header, so the
hash changes on every build.

`build_shim.sh` is the strict one. The game imports `dinput8.dll` statically,
so a shim that fails to load is a game that doesn't start. The build asserts
i386, the DLL characteristic, imports limited to `kernel32` and old `msvcrt`,
no secure-CRT `_s` symbols, and exactly the five undecorated exports the real
`dinput8.dll` has. It links `-nostdlib++`, so a stray STL reference fails the
build instead of needing `libstdc++-6.dll` in a game folder.

`tools/fxcheck/build.sh` builds ReShade 6.8.0's own preprocessor and parser for
`tests/test_shader_compile.py` (CI's `shaders` job).

## Video title cards

`tools/make_banner.py` rebuilds XIII-2's area-name banner (measured off a 60fps
capture) as a ProRes 4444 clip with alpha:

```
pip install pillow numpy fonttools brotli   # plus ffmpeg on PATH
python tools/make_banner.py "Mouse Support" --tag -LR- --sub "Camera, strikes and menus"
```

## Layout

```
shell.py                  desktop window (pywebview + WebView2), serves ui/ over localhost
main.py                   CLI, and the nxm:// handler Windows invokes
ui/index.html             the whole UI
ui/jobs.json              the UI's job list (source for assets/manifest.json)
patcher/bridge.py         UI <-> engine boundary, plain JSON
patcher/engine.py         applies patches, owns file IO and backups
patcher/patch_ops.py      pure byte transforms
patcher/state.py          what's applied, checked against disk
patcher/steam_locator.py  libraryfolders.vdf and appmanifest .acf parsing
patcher/launch.py         Steam or direct launch
patcher/launcher_skip.py  Square Enix launcher's "do not show again"
patcher/display.py        16:9 window math
patcher/archive/          filelist / white_img read and write
patcher/mods/             the Mods drawer, HD Project, .bat sandbox
addon-src/                native NovaFix add-ons and the dinput8 shim
manifests/*.json          what each game needs
tools/                    build scripts, probes, asset tools
```

The patching core is standard library only. `pywebview` and `websocket-client`
are the only third-party runtime deps, both used by the shell. Network and
filesystem access are injected (`fetch`, `steam_library_paths`, `state_path`),
so the suite runs with no Steam install and no network.

## UI probes

`tools/*_probe.js` drive the real page under jsdom or Playwright's Chromium;
each file's header says what it checks and how to run it. `tools/shot.js`
renders the window to png.
