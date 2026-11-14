# -*- mode: python ; coding: utf-8 -*-
#
# onedir, not onefile. onefile re-extracts the whole payload to %TEMP% on every
# launch, and the payload here is mostly video, so that is a multi-second stall
# and a fresh Defender scan every single time the app opens. onedir pays that
# once, at install.
#
# build:  pyinstaller --noconfirm --clean ArmyOfOne.spec
# output: dist/ArmyOfOne/
#
# assets/manifest.json is GENERATED. run tools/build_ui_manifest.py first or
# the bundle ships whatever stale copy was lying around.

from PyInstaller.utils.hooks import collect_all

# the read-only payload, mirroring what patcher/paths.py:bundled_data_root()
# expects to find. these land in _internal/ next to the exe, which is what
# sys._MEIPASS points at for a onedir build.
datas = [
    ("ui", "ui"),
    ("assets", "assets"),
    ("manifests", "manifests"),
]
binaries = []

hiddenimports = [
    # pywebview picks its backend by string at runtime, so nothing imports
    # this statically and the analyser never sees it. without it the app
    # builds fine and then fails to open a window.
    "webview.platforms.edgechromium",
    # shell.py imports main lazily, inside the nxm:// branch, specifically so
    # a download handoff does not drag pywebview in. that also means the
    # analyser cannot see it.
    "main",
    # imported inside a try block in main.py's try_sso_login(), invisible to
    # static analysis for the same reason.
    "websocket",
]

# pywebview on windows hosts WebView2 through a WinForms control via pythonnet.
# that whole chain is loaded through clr at runtime, so it has to be collected
# wholesale rather than traced.
for package in ("webview", "clr_loader", "pythonnet"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        # pythonnet is windows-only and clr_loader moves around between
        # releases. a missing one here is not fatal, a missing one at runtime
        # is, and the CI bundle check catches that.
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["shell.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # the patching core is stdlib only, so almost nothing heavy should be
    # reachable. these are the usual stowaways that arrive through a
    # transitive import and add tens of MB for nothing.
    excludes=[
        "tkinter", "unittest", "pytest", "pydoc",
        "numpy", "PIL", "matplotlib", "scipy", "pandas",
        "flask", "jinja2", "werkzeug",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ArmyOfOne",
    # longPathAware, asInvoker and an explicit UTF-8 active code page. the
    # first two are what let this work on a deeply nested library without
    # asking every user for elevation they mostly do not need; the third makes
    # the ANSI codepage UTF-8 on Win10 1903+, which is a second line of defence
    # behind the explicit encodings in the code.
    manifest="app.manifest",
    icon="app.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX on an unsigned binary is a reliable way to get
                        # flagged by antivirus. not worth the few MB.
    console=False,      # this is a windowed app. main.py routes every prompt
                        # through ask(), which returns None with no stdin
                        # instead of raising "lost sys.stdin".
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ArmyOfOne",
)
