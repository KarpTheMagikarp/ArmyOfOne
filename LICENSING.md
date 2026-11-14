# Licensing

ArmyOfOne is GPL-3.0. That's required, not a preference:
`patcher/archive/crypt.py` ports the filelist cipher from Surihix's
[WhiteCryptTool](https://github.com/Surihix/WhiteCryptTool) (GPL-3.0), and a
port is a derivative work. XIII-2 and Lightning Returns encrypt their archive
indexes, so dropping the port would mean dropping archive mods on both games.

## What ships, and under what

| Component | Author | Licence | How it ships |
|---|---|---|---|
| NovaFix | Kartin8 | GPL-3.0 | bundled with permission as `d3d9.dll` |
| ReShade 6.8.0 | crosire | BSD 3-Clause | bundled unmodified, `LICENSE-ReShade.md` beside it |
| SMAA, LumaSharpen, Curves, Vibrance | CeeJay.dk (SweetFX); SMAA by Jorge Jimenez et al. | MIT; MIT-style in `SMAA.fxh` | bundled unmodified with ReShade, `LICENSE-SweetFX.txt` |
| ReShade.fxh, ReShadeUI.fxh | crosire | CC0 | bundled with ReShade |
| MinHook | Tsuda Kageyu | BSD 2-Clause | statically linked into our native add-ons; `LICENSE-MinHook.txt` sits beside each in `assets/mods/` (so it's in the release zip), and LR mouse support, Save Anywhere and the XIII-2 audio fix also install it beside themselves |
| Filelist cipher | Surihix (WhiteCryptTool) | GPL-3.0 | ported, `patcher/archive/crypt.py` |
| Saira | The Saira Project Authors (Omnibus-Type) | SIL OFL 1.1 | pinned to one width and weight and subset to the UI's characters as `assets/saira.woff2`, `saira-OFL.txt` beside it |
| Zen Kaku Gothic New | The Zen Project Authors | SIL OFL 1.1 | the Fontsource latin builds, unmodified, as the UI's fallback font, `zen-kaku-gothic-new-OFL.txt` beside them |

Our own work (the app, the native add-ons including the XIII-2 audio fix, the
Fabula-Lous Shader presets and `FabulaLous.fx`) is GPL-3.0 with the rest.
`FabulaLous.fx` reimplements Curves' S-curve and Vibrance's rule from SweetFX,
credited in its header.

## Mods the patcher doesn't ship

Everything under the Mods drawer's Worth getting (HD Fonts and GUI, Savior's
Patch, HD Models Plus, the HD Projects, the DLC restorations) is the user's
own download from the author's page. Nothing of it is rehosted unless the
author agrees to a mirror; see [MIRRORS.md](MIRRORS.md).

Full credits, including tools whose behaviour the patcher reimplements, are in
[docs/credits.md](docs/credits.md).
