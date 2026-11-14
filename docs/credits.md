# Credits and licences

ArmyOfOne is GPL-3.0 ([LICENSE](../LICENSE)), because
`patcher/archive/crypt.py` is a port of GPL-3.0 code (below).

## Special thanks

**Kartin8**, for [NovaFix](https://github.com/Kartin8/FF13NovaFix) (GPL-3.0)
and permission to bundle it. It ships as `d3d9.dll`, pinned by sha256 in every
manifest.

## Bundled

- **ReShade** 6.8.0 by crosire, BSD 3-Clause: the official 32-bit build as
  `ReShade\d3d9.dll`, licence beside it.
- **SMAA, LumaSharpen, Curves and Vibrance** from CeeJay.dk's
  [SweetFX](https://github.com/CeeJayDK/SweetFX), MIT, unmodified. SMAA itself
  is Jorge Jimenez et al., MIT-style licence in `SMAA.fxh`.
- `ReShade.fxh` and `ReShadeUI.fxh` from crosire's reshade-shaders, CC0.
- **MinHook** (BSD 2-Clause), statically linked into the native add-ons.
- **Saira** by Omnibus-Type and the Saira Project Authors, SIL OFL 1.1: the
  menu font, pinned to width 118 and weight 480 and subset by
  `tools/make_font.py`, licence in `assets/saira-OFL.txt`.
- **Zen Kaku Gothic New** by the Zen Project Authors, SIL OFL 1.1: the fallback
  font, unmodified, licence in `assets/zen-kaku-gothic-new-OFL.txt`.

The Fabula-Lous Shader presets, `FabulaLous.fx` and the `ReShade.ini` beside
them are this project's. The grade borrows Curves' S-curve and Vibrance's rule,
credited in its header.

## Ported or reimplemented

Code or behaviour taken from other people's tools, so the patcher can do the
same job without running them:

- **Surihix**: the filelist cipher in `patcher/archive/crypt.py` is a port of
  [WhiteCryptTool](https://github.com/Surihix/WhiteCryptTool) (GPL-3.0), and
  the archive layout comes from [WhiteBinTools](https://github.com/Surihix/WhiteBinTools).
- **rebtd7**: the 4GB shim (`addon-src/ff13-dinput8-shim`) uses FF13Fix's
  technique from `d3d9ex/AutoFix.cpp`.
- **FF XIII HD Models.exe**, the model builder shipped with
  [HD Models Plus](https://www.nexusmods.com/finalfantasy13/mods/11), is
  reimplemented in `patcher/mods/hdmodels` and checked byte for byte against it.
- **FFXIIIwdbMod.exe**, shipped with Gameplay Tweaks, is reimplemented in
  `patcher/mods/wdbmod.py` ([wdbmod.md](wdbmod.md)).
- **FluffyQuack's ff13tool** and **Nova Chrysalia's** `.nccp` code patch
  format are followed by the Mods drawer's sandbox and `patcher/mods/nccp.py`.

## Pointed the way

- **cyanea-bt**: the XIII-2 audio fix (`addon-src/xiii2-audio`) is our own,
  worked out from the game's code, but the list of what was broken comes from
  the readme of their [FFXIII-2 AudioFix](https://www.nexusmods.com/finalfantasyxiii2/mods/43).

Reasoning in [LICENSING.md](../LICENSING.md), mirror status in
[MIRRORS.md](../MIRRORS.md). Not affiliated with Square Enix.
