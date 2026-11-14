"""
Reads and writes the mods' config files without pretending to own them.

NovaFix.ini has sections, ; comments and named values (VSyncMode=GameDefault).
SaviorsPatch.ini has no sections, # comments and Key=integer only. So a Setting
says which file it's in, display grouping is separate from ini sections, and a
sectionless file writes its keys at the top level.

We only ever write keys the user changed. Both mods fill in anything missing on
first boot, so a partial file is a complete instruction, and a full one from
guessed defaults would pin settings nobody chose. The writer is line based
because configparser drops comments and reorders sections.
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "NovaFix.ini"

# which file each Setting lives in, and how that file is shaped. "flat" means
# no [Section] headers: Savior's Patch writes one key per line and nothing else,
# so writing a header into it would produce a key its parser never finds.
CONFIG_FILES = {
    "novafix": {"name": "NovaFix.ini", "comment": ";", "flat": False,
                "owner": "NovaFix"},
    # "dll" is what reads it. Savior's Patch is the user's own download now,
    # so its rows wait for it (owner_in)
    "saviors": {"name": "SaviorsPatch.ini", "comment": "#", "flat": True,
                "owner": "Savior's Patch", "dll": "dinput8.dll"},
}


def owner_in(install_dir: Path, proxy_relative_dir: str, which: str) -> bool:
    """
    whether the mod that reads this file is beside it. NovaFix's rows always
    show, since they're the drawer. One that names a dll counts once that dll
    names the file: LayeredFS is a dinput8.dll as well, and a setting written
    for a mod that isn't there is one we'd report saved that nothing reads
    """
    spec = CONFIG_FILES[which]
    if not spec.get("dll"):
        return True
    try:
        return spec["name"].encode() in (Path(install_dir) / proxy_relative_dir
                                         / spec["dll"]).read_bytes()
    except OSError:
        return False

# the keys we expose, with the values NovaFix accepts. taken from their
# default_config.cpp rather than invented, and deliberately a small subset:
# anything we cannot describe accurately is better left to NovaFix's own menu,
# which F8 still opens (see MENU_KEYS).
@dataclass(frozen=True)
class Setting:
    section: str                 # the heading it appears under in the drawer
    key: str
    label: str
    kind: str                    # "choice", "toggle" or "number"
    choices: tuple = ()
    # a number's range, and on a choice the free numbers NovaFix also takes
    # beside the named ones. 0 to 0 on a choice means names only
    minimum: int = 0
    maximum: int = 0
    note: str = ""                # one line for the help bar, said to a player
    file: str = "novafix"        # which CONFIG_FILES entry it is written to
    labels: tuple = ()           # display text per choice, positional
    # where it goes inside that file, when that is not the display heading.
    # a flat file ignores this entirely.
    ini_section: str | None = None
    # within a section, low first. the diagnostics NovaFix contributes sink
    # below the settings a player came for, which matters on LR where two mods
    # both land rows under Performance and the mod's rows are the useful ones.
    order: int = 0
    # one row, several keys: ((choice, ((key, value), ...)), ...). The row's own
    # `key` is then a name for the group rather than something in the file, and
    # nothing writes it. Tuples rather than dicts so the dataclass stays
    # hashable.
    expand: tuple = ()
    # ((choice, line), ...): said in the drawer the moment that choice is
    # picked. What a choice costs belongs there, not in a note everyone reads
    warn: tuple = ()
    # a preset row's: ((tier, ((row key, its choice), ...)), ...), and what an
    # unset row reads as, ((row key, choice), ...)
    tiers: tuple = ()
    defaults: tuple = ()

    @property
    def target_section(self) -> str:
        if CONFIG_FILES[self.file]["flat"]:
            return ""
        return self.section if self.ini_section is None else self.ini_section

    @property
    def expansions(self) -> dict:
        return {choice: dict(pairs) for choice, pairs in self.expand}

    @property
    def real_keys(self) -> set:
        """the keys this row actually writes, which is not always its own"""
        if not self.expand:
            return {self.key}
        return {k for _, pairs in self.expand for k, _ in pairs}

    def selected(self, present: dict) -> str | None:
        """
        which choice the file currently represents, for a row that writes
        several keys. An exact match on every key or None, which shows as
        Default, since naming the nearest option would let Save rewrite a
        combination the user set by hand.
        """
        for choice, pairs in self.expand:
            if all(present.get(key) == value for key, value in pairs):
                return choice
        return None

    def as_dict(self) -> dict:
        named = SHADOW_LABELS if self.key == "ShadowResolution" else {}
        labels = (list(self.labels) if self.labels
                  else [named.get(c, c) for c in self.choices])
        out = {"section": self.section, "key": self.key, "label": self.label,
               "kind": self.kind, "choices": list(self.choices),
               "choiceLabels": labels,
               "min": self.minimum, "max": self.maximum, "note": self.note,
               "file": self.file, "warn": dict(self.warn)}
        if self.kind == "preset":
            # what each tier sets the rows under it to, and what those read as
            # unset, so the drawer can say which tier the rows add up to
            out["presets"] = {tier: dict(rows) for tier, rows in self.tiers}
            out["defaults"] = dict(self.defaults)
        return out


ON_OFF = ("On", "Off")

# the overlay spells shadow quality out; matching it means someone who has seen
# both surfaces does not have to work out that 4096 and "Very high" are the
# same setting.
# These games render at fixed 16:9 internally and their UI was laid out for it,
# so an arbitrary size is a way to get a stretched HUD rather than a feature.
# The list is the standard ladder plus the two ultrawides people actually own.
RESOLUTIONS = {
    "1280x720": (1280, 720),
    "1600x900": (1600, 900),
    "1920x1080": (1920, 1080),
    "2560x1440": (2560, 1440),
    "3440x1440": (3440, 1440),
    "3840x2160": (3840, 2160),
}

SHADOW_LABELS = {"512": "Low (512)", "1024": "Medium (1024)", "2048": "High (2048)",
                 "4096": "Very High (4096)", "8192": "Extreme (8192)"}

def _combo(section, key, label, options, note="", file="saviors", order=0,
           ini_section=None):
    """
    one row that writes several keys.

    options is ((display, ((key, value), ...)), ...). The display text doubles
    as the stored choice, because nothing else needs to know it and a second
    identifier here would only be a thing to keep in sync.
    """
    return Setting(section, key, label, "choice",
                   choices=tuple(text for text, _ in options),
                   labels=tuple(text for text, _ in options),
                   expand=tuple((text, pairs) for text, pairs in options),
                   note=note, file=file, order=order, ini_section=ini_section)


def _res(width, height):
    return (("CustomResW", str(width)), ("CustomResH", str(height)))


def _preset(file, tiers, defaults=(), note=""):
    """
    one row over the Quality rows under it: Low, Medium and High set them
    together, Auto picks one for this PC (hardware.auto_tier), and it reads
    Custom once a row's changed by hand. Writes nothing itself. `tiers` is
    ((tier, ((row key, its choice), ...)), ...) and `defaults` is what an
    unset row reads as (NovaFix's own). Never touches the frame rate.
    """
    return Setting("Quality", "GraphicsPreset", "Graphics Preset", "preset",
                   choices=("Auto", "Low", "Medium", "High"), file=file, order=-1,
                   tiers=tuple(tiers), defaults=tuple(defaults), note=note or (
                       "Sets the rows below together. Auto picks for your PC; change any "
                       "row and it's Custom."))


SETTINGS = (
    # a real choice (some people want Square Enix's launcher to keep
    # resolution, shadows and AA), but not one you should need to find first:
    # apply_changes sets it whenever a Quality or Display row changes, and an
    # explicit answer here still wins.
    _combo("Display", "GraphicsSource", "Graphics Source", (
        ("This Patcher", (("OverrideLauncherGraphics", "On"),)),
        ("The Game's Launcher", (("OverrideLauncherGraphics", "Off"),)),
    ), file="novafix", ini_section="Graphics", order=-1,
        note="Whether this patcher or the game's launcher sets resolution, shadows and "
             "anti-aliasing."),
    # the order every launcher and in-game menu uses, and it is causal rather
    # than aesthetic: the monitor decides what resolutions and refresh rates
    # are real, and the window mode decides whether Resolution is even read.
    Setting("Display", "Monitor", "Monitor", "choice",
            ("Automatic", "1", "2", "3"), ini_section="Graphics",
            note="Which screen the game opens on."),
    _combo("Display", "DisplayMode", "Window Mode", (
        ("Windowed", (("DisplayMode", "Windowed"),)),
        ("Borderless", (("DisplayMode", "Borderless"),)),
        ("Borderless Fullscreen", (("DisplayMode", "BorderlessFullscreen"),)),
    ), file="novafix", ini_section="Graphics"),
    _combo("Display", "Resolution", "Resolution", tuple(
        (name, (("Width", str(w)), ("Height", str(h))))
        for name, (w, h) in RESOLUTIONS.items()
    ), file="novafix", ini_section="Graphics",
        note="Not used in Borderless Fullscreen, which always runs at your desktop "
             "resolution."),
    Setting("Display", "RefreshRate", "Refresh Rate", "choice",
            ("Automatic", "60", "120", "144", "240"), 24, 1000, ini_section="Performance",
            note="Automatic uses your monitor's current refresh rate."),

    # VSync above the frame rate, since it decides what the frame rate does.
    # Triple buffering is part of the VSync question, it does nothing with
    # VSync off.
    _combo("Performance", "VSync", "VSync", (
        ("Off", (("VSyncMode", "Off"), ("TripleBuffering", "Off"))),
        ("On", (("VSyncMode", "On"), ("TripleBuffering", "On"))),
        ("On, Double Buffered", (("VSyncMode", "On"), ("TripleBuffering", "Off"))),
    ), file="novafix", ini_section="Performance",
        note="Stops screen tearing. Double Buffered feels quicker but stutters more if "
             "FPS dips."),
    Setting("Performance", "FrameRateLimit", "Frame Rate", "choice",
            ("Unlimited", "30", "60", "120", "144", "240"), 15, 1000,
            ini_section="Performance",
            note="Caps the frame rate. With VSync on, your refresh rate caps it as well."),
    Setting("Performance", "D3D9Backend", "Renderer", "choice",
            ("Automatic", "System", "LocalDXVK"), order=8,
            labels=("Automatic", "Windows D3D9", "DXVK"), ini_section="Performance",
            note="Automatic suits most PCs and is the only one that runs the Fabula-Lous "
                 "Shader.",
            # NovaFix only looks for ReShade\d3d9.dll when it picks the renderer
            # itself, and LocalDXVK with no dxvk.dll beside it has nothing to load
            warn=(("System", "Windows D3D9 leaves the Fabula-Lous Shader off."),
                  ("LocalDXVK", "DXVK needs a 32-bit dxvk.dll you've added beside NovaFix's "
                                "d3d9.dll, and leaves the Fabula-Lous Shader off."))),
    Setting("Performance", "ShowFpsCounter", "FPS Counter", "toggle", ON_OFF,
            order=9, ini_section="Performance",
            note="Shows your frame rate on screen while you play."),
    # RedirectExecutableReads isn't a row: stock NovaFix doesn't read it (it's
    # not in the binary's strings), only a fork does. proxy_redirects_executable
    # still reads it so that fork is detected. OverrideLauncherGraphics isn't a
    # row either, since its only right answer is "on whenever anything else is
    # set", so apply_changes does it.
    _preset("novafix", (
        ("Low", (("ShadowResolution", "1024"), ("MSAA", "2"), ("AnisotropicFiltering", "4"))),
        ("Medium", (("ShadowResolution", "2048"), ("MSAA", "2"),
                    ("AnisotropicFiltering", "8"))),
        ("High", (("ShadowResolution", "4096"), ("MSAA", "4"),
                  ("AnisotropicFiltering", "16"))),
        # NovaFix's own defaults (graphics_settings.h), which are its High
    ), defaults=(("ShadowResolution", "4096"), ("MSAA", "4"), ("AnisotropicFiltering", "16"))),
    Setting("Quality", "ShadowResolution", "Shadow Quality", "choice",
            ("512", "1024", "2048", "4096", "8192"), ini_section="Graphics",
            note="Sharper shadows the higher you go. Extreme costs the most."),
    # no Off. NovaFix clamps MSAA into {2,4,8,16} (MsaaFromLauncherIndex), so a
    # zero here comes back as 2 and the row would be lying about it.
    Setting("Quality", "MSAA", "Anti-Aliasing", "choice",
            ("2", "4", "8", "16"), labels=("2x", "4x", "8x", "16x"),
            ini_section="Graphics",
            note="Smooths edges. With the Fabula-Lous Shader on, 2x looks close to 4x and runs "
                 "faster."),
    Setting("Quality", "AnisotropicFiltering", "Anisotropic Filtering", "choice",
            ("0", "2", "4", "8", "16"),
            labels=("Off", "2x", "4x", "8x", "16x"), ini_section="Graphics",
            note="Keeps textures sharp at a distance and at an angle. Costs almost nothing."),
)

# Lightning Returns only. Every key is read off Savior's Patch's own source
# (g_toggles and g_numerics in 08_config_persist.c), real ranges included.
#
# Seven rows out of ~140 keys:
#   nothing that overlaps. Frame rate, the unlock and the sim-delta fix are one
#   row, since 60fps without the other two does nothing.
#   nothing that can't work. ForceImmediatePresent is a VSync toggle NovaFix
#   overrides every frame for LR (presentation_setup.cpp).
#   nothing that's a look rather than a fix, so no ambient occlusion (28 keys).
SAVIORS_SETTINGS = (
    _combo("Display", "Resolution", "Render Resolution", (
        ("720p", _res(1280, 720)),
        ("1080p", _res(1920, 1080)),
        ("1440p", _res(2560, 1440)),
        ("4K", _res(3840, 2160)),
    ), note="The resolution the game draws at. Above your screen's, it's sharper but "
            "heavier."),

    _combo("Performance", "FrameRate", "Frame Rate", (
        ("30 FPS", (("UnlockFramerate", "1"), ("TargetFpsX100", "3000"),
                    ("SimDeltaFix", "1"))),
        ("60 FPS", (("UnlockFramerate", "1"), ("TargetFpsX100", "6000"),
                    ("SimDeltaFix", "1"))),
        ("120 FPS", (("UnlockFramerate", "1"), ("TargetFpsX100", "12000"),
                     ("SimDeltaFix", "1"))),
        ("144 FPS", (("UnlockFramerate", "1"), ("TargetFpsX100", "14400"),
                     ("SimDeltaFix", "1"))),
    ), note="Unlocks the frame rate and fixes the prompts and stamina drain that break "
            "above 30 FPS."),

    _combo("Performance", "Stutter", "Stutter Reduction", (
        ("Standard", (("DiscardFix", "1"), ("ReadPace", "1"),
                      ("ShaderThrottle", "1"), ("LoaderThrottle", "0"))),
        ("Aggressive", (("DiscardFix", "1"), ("ReadPace", "1"),
                        ("ShaderThrottle", "1"), ("LoaderThrottle", "1"))),
        ("Off", (("DiscardFix", "0"), ("ReadPace", "0"),
                 ("ShaderThrottle", "0"), ("LoaderThrottle", "0"))),
    ), note="Cuts the stutter while new areas load in. Try Aggressive if towns still "
            "stutter."),

    _preset("saviors", (
        ("Low", (("Shadows", "Standard"), ("AntiAliasing", "FXAA"),
                 ("TextureFiltering", "8x"))),
        ("Medium", (("Shadows", "High"), ("AntiAliasing", "MSAA 2x"),
                    ("TextureFiltering", "16x"))),
        ("High", (("Shadows", "Very High, Softened"), ("AntiAliasing", "MSAA 4x"),
                  ("TextureFiltering", "16x"))),
    )),
    _combo("Quality", "Shadows", "Shadow Quality", (
        ("Standard", (("ShadowMapRes", "1024"), ("ShadowPcss", "0"))),
        ("High", (("ShadowMapRes", "2048"), ("ShadowPcss", "0"))),
        ("Very High", (("ShadowMapRes", "4096"), ("ShadowPcss", "0"))),
        ("Very High, Softened", (("ShadowMapRes", "4096"), ("ShadowPcss", "1"))),
        ("Ultra", (("ShadowMapRes", "8192"), ("ShadowPcss", "0"))),
        ("Ultra, Softened", (("ShadowMapRes", "8192"), ("ShadowPcss", "1"))),
    ), note="Softened shadows stay crisp near what casts them and blur with distance, "
            "like real ones."),

    _combo("Quality", "AntiAliasing", "Anti-Aliasing", (
        ("Off", (("FxaaOff", "1"), ("MsaaSamples", "0"), ("SsaaScale", "100"))),
        ("FXAA", (("FxaaOff", "0"), ("MsaaSamples", "0"), ("SsaaScale", "100"))),
        ("MSAA 2x", (("FxaaOff", "1"), ("MsaaSamples", "2"), ("SsaaScale", "100"))),
        ("MSAA 4x", (("FxaaOff", "1"), ("MsaaSamples", "4"), ("SsaaScale", "100"))),
        ("MSAA 8x", (("FxaaOff", "1"), ("MsaaSamples", "8"), ("SsaaScale", "100"))),
        ("Supersampled 1.5x", (("FxaaOff", "1"), ("MsaaSamples", "0"),
                               ("SsaaScale", "150"))),
        ("Supersampled 2x", (("FxaaOff", "1"), ("MsaaSamples", "0"),
                             ("SsaaScale", "200"))),
    ), note="FXAA is cheap but soft, MSAA is sharper, and Supersampled looks best but "
            "costs the most."),

    _combo("Quality", "TextureFiltering", "Texture Filtering", (
        ("8x", (("AnisoLevel", "8"), ("MipBiasMode", "1"))),
        ("16x", (("AnisoLevel", "16"), ("MipBiasMode", "1"))),
        ("16x, Sharpened", (("AnisoLevel", "16"), ("MipBiasMode", "2"))),
    ), note="Sharper textures at a distance. Sharpened cleans up far-off ground but can "
            "shimmer a bit."),

    _combo("World", "NpcSpawn", "NPC Spawn Distance", (
        ("Default", (("NpcSpawnFix", "0"),)),
        ("Extended", (("NpcSpawnFix", "1"),)),
    ), note="Extended shows townsfolk about 2.5x farther out, but can bring back some "
            "stutter."),
)


# settings NovaFix only exposes for some titles, per default_config.cpp
# Keys, not headings. NovaFix decides what it reads per title and that has
# nothing to do with which heading we file a row under, so listing them by
# section meant renaming a heading silently emptied a game's panel.
TITLE_SCOPED = {
    # LR has no [Graphics] section at all, and bootstrap.cpp turns the
    # presentation controls off for it, so frame rate, VSync, refresh rate and
    # triple buffering are not merely unset there, they are unreadable.
    "ffxiii3": {"ShowFpsCounter", "SmartControllerHotplug", "SchedulingStability",
                "HighFpsCorrectness", "StartupSound", "MenuSounds", "D3D9Backend"},
}


# the order headings appear in the drawer. two mods contribute rows and both
# have something to say about Performance, so without a defined order the panel
# rendered "Performance" twice with Display wedged between them: the renderer
# starts a new heading whenever the section changes, which is a running
# comparison rather than a grouping. sorting here also means a player never
# learns which mod owns which row, which is the whole point.
SECTION_ORDER = ("Display", "Performance", "Graphics", "Quality", "World")


def settings_for(game_id: str) -> list:
    """which settings apply to this title, in the order they are shown"""
    scope = TITLE_SCOPED.get(game_id)
    base = (list(SETTINGS) if scope is None
            else [s for s in SETTINGS if s.key in scope])
    rows = base + list(EXTRA_SETTINGS.get(game_id, ()))
    # stable, so authored order survives inside a section at equal weight
    return sorted(rows, key=lambda s: (
        SECTION_ORDER.index(s.section) if s.section in SECTION_ORDER
        else len(SECTION_ORDER), s.order))


# settings that come from a mod other than NovaFix. LR is the only game with
# one, because it is the only game whose fix for the thing NovaFix does not do
# carries its own config file.
EXTRA_SETTINGS = {"ffxiii3": SAVIORS_SETTINGS}


def config_path(install_dir: Path, proxy_relative_dir: str,
                which: str = "novafix") -> Path:
    """
    a mod's ini, which sits next to that mod's dll rather than next to the exe.

    Both of LR's land in the same folder, because NovaFix's d3d9.dll and
    Savior's dinput8.dll are both beside LRFF13.exe. On XIII and XIII-2 the
    proxy folder is deeper than the game root and only NovaFix is there.
    """
    return Path(install_dir) / proxy_relative_dir / CONFIG_FILES[which]["name"]


# a game switch takes its first look at NovaFix.ini on a thread of its own while
# the Settings drawer can be saving, and each reads the file, changes it and
# writes it back: without this, the second writer puts back what the first changed
_WRITING = threading.RLock()


def _header(line: str) -> str | None:
    """
    a section header's name, lowercased, or None. "[Graphics] ; display"
    counts, as it does for GetPrivateProfileString
    """
    return line[1:line.index("]")].strip().lower() if line.startswith("[") and "]" in line \
        else None


# Notepad's UTF-16, which GetPrivateProfileString reads by its BOM and writes back
# in. Read as UTF-8 it held no sections at all, and keys added to it in UTF-8
# were never read
WIDE = b"\xff\xfe"


def _decoded(raw: bytes) -> str:
    """a NovaFix.ini's text, the way Windows reads it: UTF-16 by its BOM, else bytes"""
    if raw.startswith(WIDE):
        return raw[len(WIDE):].decode("utf-16-le", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def read(path: Path) -> dict:
    """
    every key present, as {section: {key: value}}.

    a missing file is an empty dict rather than an error: NovaFix only writes
    it on first run, so "not there yet" is the normal state for a freshly
    patched game.
    """
    path = Path(path)
    if not path.is_file():
        return {}

    values: dict = {}
    section = ""
    for raw in _decoded(path.read_bytes()).splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            values.setdefault(section, {})
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            values.setdefault(section, {})[key.strip()] = value.strip()
    return values


def write(path: Path, changes: dict, comment: str = ";",
          owner: str = "NovaFix") -> Path:
    """
    applies only the given {section: {key: value}} changes.

    everything else in the file is preserved byte for byte, including comments,
    blank lines and ordering. a key already present is replaced in place; a new
    one is appended to the end of its section; a new section is appended to the
    file.
    """
    with _WRITING:
        return _write(Path(path), changes, comment, owner)


def _write(path: Path, changes: dict, comment: str, owner: str) -> Path:
    import stat

    from patcher.fileops import write_atomic

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        return _write_fresh(path, changes, comment, owner)
    # somebody's lock on their settings, and write_atomic would lift it
    if not path.stat().st_mode & stat.S_IWRITE:
        raise OSError(f"{path.name} is read-only, so it was left as it is. Untick Read-only "
                      "in its Properties to change it from here.")

    # read as bytes: read_text() performs universal-newline translation, so a
    # CRLF file arrives with every \r already stripped and the check below
    # would always say LF. NovaFix writes CRLF, and rewriting as LF makes every
    # line look changed to anything diffing the file.
    raw = path.read_bytes()
    # surrogateescape, not replace: replace turns every non-UTF-8 byte into
    # EF BF BD, which wrecked a cp1251 NovaFix.ini's comments on the first save.
    # Notepad's BOM is kept, out of the first header's way. A UTF-16 one is
    # written back as UTF-16, the only way Windows reads it.
    codec, errors = ("utf-16-le", "surrogatepass") if raw.startswith(WIDE) \
        else ("utf-8", "surrogateescape")
    bom = WIDE if codec != "utf-8" else b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    text = raw[len(bom):].decode(codec, errors=errors)
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    # matched the way GetPrivateProfileString matches, which is how NovaFix
    # reads it back: names in any case, and only the first section of a name
    # counts. Matched any other way, a key lands where it's never read
    pending = {name.lower(): (name, {key.lower(): (key, value) for key, value in keys.items()})
               for name, keys in changes.items() if keys}
    out: list = []
    section, seen = "", set()
    section_end: dict = {}

    for line in lines:
        stripped = line.strip()
        name = _header(stripped)
        if name is not None:
            section = None if name in seen else name
            seen.add(name)
        elif section is not None and "=" in stripped and not stripped.startswith((";", "#")):
            keys = pending.get(section, ("", {}))[1]
            key = stripped.split("=", 1)[0].strip().lower()
            if key in keys:
                # replace in place so the comment above it still describes it
                out.append("{}={}".format(*keys.pop(key)))
                section_end[section] = len(out)
                continue
        out.append(line)
        # after its last key rather than its last line, so a key added isn't
        # parted from the rest by a blank, or put under the next one's comment
        if section and (name is not None or "=" in stripped) \
                and not stripped.startswith((";", "#")):
            section_end[section] = len(out)

    # keys that belong to a section already in the file, appended at its end.
    # The last section first, so one insert never moves where the next goes
    for name in sorted((n for n in pending if n in section_end and pending[n][1]),
                       key=section_end.get, reverse=True):
        insert_at = section_end[name]
        out[insert_at:insert_at] = ["{}={}".format(*pair) for pair in pending.pop(name)[1].values()]

    # sections the file does not have yet. the empty name is the top level of a
    # flat file, which gets its keys appended and no header: writing "[]" into
    # SaviorsPatch.ini would give its parser a line it never looks for.
    for name, keys in pending.values():
        if not keys:
            continue
        if out and out[-1].strip():
            out.append("")
        if name:
            out.append(f"[{name}]")
        out.extend("{}={}".format(*pair) for pair in keys.values())

    # the same codec on the way out, or the surrogates raise on encode, and
    # beside and renamed, so NovaFix starting mid-save never reads half of it
    write_atomic(path, bom + (newline.join(out) + newline).encode(codec, errors))
    return path


def _write_fresh(path: Path, changes: dict, comment: str = ";",
                 owner: str = "NovaFix") -> Path:
    """
    a new file holding only what the user changed, in the owning mod's comment
    character (; for NovaFix, # for Savior's Patch, the wrong one is a parse
    error).
    """
    lines = [
        f"{comment} Written by ArmyOfOne.",
        f"{comment} Only the settings changed in the patcher are listed here.",
        f"{comment} {owner} fills in everything else with its own defaults on launch.",
    ]
    for section, keys in changes.items():
        if not keys:
            continue
        lines.append("")
        if section:
            lines.append(f"[{section}]")
        lines.extend(f"{key}={value}" for key, value in keys.items())
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def current(install_dir: Path, proxy_relative_dir: str, game_id: str) -> dict:
    """
    what the settings UI needs: the schema plus whatever is currently set.

    a value we have no entry for comes back as None, which the UI shows as
    "NovaFix default" rather than inventing a value to display.
    """
    settings = settings_for(game_id)
    owned = {which: owner_in(install_dir, proxy_relative_dir, which)
             for which in {s.file for s in settings}}
    settings = [s for s in settings if owned[s.file]]
    wanted = {s.file for s in settings}
    paths = {which: config_path(install_dir, proxy_relative_dir, which)
             for which in wanted}
    # NovaFix's the way NovaFix reads it, so a "[graphics]" one wrote under
    # shows what it took; Savior's flat file as it always was
    present = {which: profile(path) if not CONFIG_FILES[which]["flat"] else
               {s.lower(): {k.lower(): v for k, v in keys.items()}
                for s, keys in read(path).items()}
               for which, path in paths.items()}

    def value_of(setting):
        if setting.kind == "preset":
            # Auto only while the rows are still what it picked; otherwise the
            # drawer reads the tier off the rows, or Custom
            return "Auto" if auto_picked(install_dir, game_id) else None
        found = present[setting.file].get(setting.target_section.lower(), {})
        section = {key: found[key.lower()] for key in setting.real_keys | {setting.key}
                   if key.lower() in found}
        if setting.expand:
            return setting.selected(section)
        return section.get(setting.key)

    novafix = paths.get("novafix")
    return {
        # the single path stays for callers that predate the second file, and
        # it is still the one a user is most likely to want to open by hand
        "path": str(novafix) if novafix else "",
        "exists": bool(novafix and novafix.is_file()),
        "files": [{"id": which, "name": CONFIG_FILES[which]["name"],
                   "path": str(path), "exists": path.is_file()}
                  for which, path in sorted(paths.items())],
        "settings": [_with_auto(dict(s.as_dict(), value=value_of(s))) for s in settings],
    }


PRESET_MARK = "graphics-preset.json"


def _with_auto(row: dict) -> dict:
    """a preset row with Auto's rows filled in for this PC, and which tier that is"""
    if row["kind"] != "preset":
        return row
    from patcher.hardware import auto_tier

    picked = auto_tier()
    row["presets"] = dict(row["presets"], Auto=row["presets"][picked])
    row["autoTier"] = picked
    return row


def auto_picked(install_dir: Path, game_id: str) -> bool:
    """whether Auto was the preset last saved for this game"""
    from patcher.paths import undo_store_for

    try:
        said = json.loads((undo_store_for(install_dir) / PRESET_MARK).read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(said, dict) and said.get(game_id) == "Auto"


def _remember_preset(install_dir: Path, game_id: str, preset: str) -> None:
    """Auto kept in the undo store, since no ini has a key for it, and anything else forgets it"""
    from patcher.fileops import write_atomic
    from patcher.paths import undo_store_for

    mark = undo_store_for(install_dir) / PRESET_MARK
    try:
        said = json.loads(mark.read_text("utf-8"))
        said = said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        said = {}
    if preset == "Auto":
        said[game_id] = "Auto"
    else:
        said.pop(game_id, None)
    mark.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(mark, json.dumps(said).encode())


def apply_changes(install_dir: Path, proxy_relative_dir: str, game_id: str,
                  changes: dict) -> dict:
    """
    validates against the schema and writes.

    an unknown key or an out-of-range value is refused rather than written.
    this file is read by someone else's parser, and a bad value there is their
    crash to debug and our fault.
    """
    allowed = {(s.section, s.key): s for s in settings_for(game_id)}
    cleaned: dict = {}
    rejected = []
    owned = {}
    preset = None

    for section, keys in (changes or {}).items():
        for key, value in (keys or {}).items():
            setting = allowed.get((section, key))
            if setting is None:
                rejected.append(f"{key} isn't a setting the patcher changes")
                continue
            if setting.file not in owned:
                owned[setting.file] = owner_in(install_dir, proxy_relative_dir, setting.file)
            if not owned[setting.file]:
                # a drawer opened before it came off
                owner = CONFIG_FILES[setting.file]["owner"]
                rejected.append(f"{setting.label} is a {owner} setting, and {owner} isn't in")
                continue
            text = str(value).strip()
            if setting.kind == "preset":
                # nothing of its own to write: the rows it set came along with it
                if text not in setting.choices:
                    rejected.append(f"{text} isn't an option for {setting.label}")
                else:
                    preset = text
                continue
            # ascii as well: "²" is a digit to isdigit() and a ValueError to int()
            ranged = text.isascii() and text.isdigit() \
                and setting.minimum <= int(text) <= setting.maximum
            if setting.kind == "number":
                if not ranged:
                    rejected.append(f"{setting.label} has to be a number from "
                                    f"{setting.minimum} to {setting.maximum}")
                    continue
            elif setting.choices and text not in setting.choices:
                # a free number only where NovaFix takes a range as well as the
                # names, and never on a row that expands, which has nothing to
                # write for one
                if setting.expand or not (setting.maximum and ranged):
                    takes = " / ".join(setting.as_dict()["choiceLabels"])
                    if setting.maximum and not setting.expand:
                        takes += f", or a number from {setting.minimum} to {setting.maximum}"
                    rejected.append(f"{text} isn't an option for {setting.label}. It takes {takes}")
                    continue
            # grouped by the file it belongs to AND the section inside that
            # file, which is not always the heading it appeared under: the
            # drawer groups Savior's settings under Display, Performance and
            # Quality, while the file itself has no sections at all.
            target = cleaned.setdefault(setting.file, {}) \
                            .setdefault(setting.target_section, {})
            # a row that stands for several keys writes those, never its own
            # name: "AntiAliasing" is a question we ask, not a key that exists.
            target.update(setting.expansions[text] if setting.expand else {key: text})

    novafix = cleaned.get("novafix", {})

    if rejected:
        return {"ok": False, "error": "; ".join(rejected)}
    # Auto remembered, or a tier picked or a Quality row changed by hand
    # forgetting it, so the drawer never reads Auto over rows it didn't pick
    quality = {k for k in (changes or {}).get("Quality", {}) if k != "GraphicsPreset"}
    if preset is not None or quality:
        try:
            _remember_preset(install_dir, game_id, preset or "")
        except OSError:
            pass

    # writing a graphics setting without this produces a file NovaFix reads and
    # then ignores, because the game's own launcher is still deciding. the user
    # changed a graphics value, so turning it on is what they meant.
    graphics = novafix.get("Graphics")
    if graphics and any(key != "OverrideLauncherGraphics" for key in graphics):
        graphics.setdefault("OverrideLauncherGraphics", "On")

    if not any(any(k for k in sections.values()) for sections in cleaned.values()):
        return {"ok": True, "written": False,
                "path": str(config_path(install_dir, proxy_relative_dir))}

    written, changed = [], {}
    for which, sections in cleaned.items():
        if not any(sections.values()):
            continue
        path = write(config_path(install_dir, proxy_relative_dir, which), sections,
                     CONFIG_FILES[which]["comment"], CONFIG_FILES[which]["owner"])
        written.append(str(path))
        for section, keys in sections.items():
            if keys:
                changed.setdefault(section or CONFIG_FILES[which]["name"], []).extend(keys)

    return {"ok": True, "written": True, "path": written[0],
            "paths": written, "changed": changed}


# NovaFix's own menu. Everything in it a player needs is in this app's Settings
# drawer now, and ` and F10 open it in the middle of a game. It won't let every
# key go: Normalize() in input_settings.cpp puts F10 back when both keyboard
# keys are Disabled, so one stays, on F8, which nothing else in these games or
# Steam's overlay answers to. Not Home, which is ReShade's menu
MENU_KEYS = {"OverlayHotkey": "F8", "AlternateOverlayHotkey": "Disabled",
             "OverlayGamepadHotkey": "Disabled"}
# what NovaFix writes on its first launch and reads when they're missing, the
# first of each the one it writes. StickClicks and ViewMenu are older builds'
# names it reads as Both
MENU_DEFAULTS = {"OverlayHotkey": ("F10",), "AlternateOverlayHotkey": ("Grave",),
                 "OverlayGamepadHotkey": ("Both", "StickClicks", "ViewMenu")}
# the add-ons NovaFix lists as the game starts: on the first launch that finds
# them and whenever they change, not every time. 1.0.2 made every launch its
# default; ReadChoice takes an older build's 2 as EveryLaunch too
POPUP_KEYS = {"LoadedModulesNotificationMode": "WhenChanged"}
POPUP_DEFAULTS = {"LoadedModulesNotificationMode": ("EveryLaunch", "2")}

# what Apply sets in NovaFix.ini, a group at a time: once, and only while
# every key in the group is still NovaFix's default, since anything else is
# somebody's choice. Each group's marker sits in our undo store beside
# launcher_skip's, holding what the keys were, so keys set back by hand stay
# set and a group added later still gets its look. Revert puts them back and
# drops the markers; a NovaFix the user had before keeps its markers.
QUIETED = "novafix-menu-quiet"
POPPED = "novafix-popup-on-change"
ONCE = {QUIETED: ("Input", MENU_KEYS, MENU_DEFAULTS),
        POPPED: ("Performance", POPUP_KEYS, POPUP_DEFAULTS)}
# the popup group used to set EveryLaunch, under its own marker. A game
# patched back then gets the new group's look, keeping the old marker's note
# of what the key was before either touched it, so Revert still lands there
LEGACY = {"novafix-popup-every": (POPPED, ("Performance",
                                           {"LoadedModulesNotificationMode": "EveryLaunch"},
                                           {"LoadedModulesNotificationMode": ("WhenChanged", "1")}))}


def _read_mark(marker: Path) -> dict | None:
    try:
        said = json.loads(marker.read_bytes())
    except FileNotFoundError:
        return None
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}


def _migrate(store: Path) -> dict:
    """
    the was each new group should keep from an old marker it replaces. An old
    one that wrote is folded into the new look; one that didn't, or names the
    user's own NovaFix, already said the key is theirs and just takes the new name
    """
    carried = {}
    for old, (new, _) in LEGACY.items():
        said = _read_mark(Path(store) / old)
        if said is None or (Path(store) / new).exists():
            continue
        if said.get("wrote") and not said.get("dll"):
            carried[new] = said.get("was") if isinstance(said.get("was"), dict) else {}
            (Path(store) / old).unlink(missing_ok=True)
        else:
            (Path(store) / old).replace(Path(store) / new)
    return carried


def looked_at(store: Path, which: str) -> str | None:
    """
    what a group's marker says about the NovaFix beside the game: "" for any,
    a sha256 for that dll only, or None with no marker
    """
    try:
        said = json.loads((Path(store) / which).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return ""
    return str(said.get("dll") or "") if isinstance(said, dict) else ""


def profile(path: Path) -> dict:
    """
    the file the way NovaFix reads it, through GetPrivateProfileString: names
    in any case (lowercased here), the first section of a name and the first
    of a key, one pair of quotes off a value
    """
    path = Path(path)
    if not path.is_file():
        return {}
    values: dict = {"": {}}
    section = ""
    for raw in _decoded(path.read_bytes()).splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        name = _header(line)
        if name is not None:
            section = None if name in values else name
            if section is not None:
                values[section] = {}
        elif section is not None and "=" in line:
            key, _, value = line.partition("=")
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[section].setdefault(key.strip().lower(), value)
    return values


RENDERERS = ("Automatic", "System", "LocalDXVK")


def choice(value, names) -> str | None:
    """
    a named setting the way NovaFix's ReadChoice takes a value profile() read:
    a name in any case, else a whole number up to 2^32 - 1 with blanks either
    side, clamped to the last name, else None for whatever it defaults to.
    ReadText gives up at 63 characters, quotes keep the blanks inside them out
    of a name, and "²" passes isdigit() where int() refuses it
    """
    text = str(value or "")
    if len(text) >= 63:
        return None
    for name in names:
        if text.lower() == name.lower():
            return name
    digits = text.strip()
    if digits.isascii() and digits.isdigit() and int(digits) <= 0xFFFFFFFF:
        return names[min(int(digits), len(names) - 1)]
    return None


def renderer(found: dict) -> str:
    """the renderer NovaFix picks off a file profile() read"""
    return choice(found.get("performance", {}).get("d3d9backend"), RENDERERS) or RENDERERS[0]


def off_dxvk(install_dir: Path, proxy_relative_dir: str) -> bool:
    """
    LocalDXVK with no dxvk.dll beside NovaFix put on Automatic, read and written
    under the drawer's lock. NovaFix runs Windows' D3D9 there anyway, so nothing
    that worked goes. Not with a d3d9_chain.dll there: LocalDXVK never loads
    one and Automatic loads it first. Whether it wrote
    """
    path = config_path(install_dir, proxy_relative_dir)
    if any((path.parent / name).is_file() for name in ("dxvk.dll", "d3d9_chain.dll")):
        return False
    with _WRITING:
        if renderer(profile(path)) != "LocalDXVK":
            return False
        write(path, {"Performance": {"D3D9Backend": "Automatic"}})
        return True


def first_look(install_dir: Path, proxy_relative_dir: str, store: Path,
               current_dll=None) -> bool:
    """
    the groups in ONCE that haven't been looked at, set where they're still
    NovaFix's defaults. `current_dll()` is the sha256 of the d3d9.dll beside
    the game, asked only when a marker names one. Whether it wrote
    """
    with _WRITING:
        carried = _migrate(store)
        said = {which: looked_at(store, which) for which in ONCE}
        named = {dll for dll in said.values() if dll}
        if named:
            # the user's own NovaFix, which Revert left: every key of it is
            # theirs while it's there, and a different one put on since starts over
            if current_dll is None or current_dll() in named:
                return False
            for which in ONCE:
                if said[which]:
                    (Path(store) / which).unlink(missing_ok=True)
                    said[which] = None
        path = config_path(install_dir, proxy_relative_dir)
        made = not path.is_file()
        found = profile(path)
        changes, marks = {}, {}
        for which, (section, ours, defaults) in ONCE.items():
            if said[which] is not None:
                continue
            keys = found.get(section.lower(), {})
            was = {key: keys.get(key.lower()) for key in ours}
            wrote = all(value is None or value.lower() in {d.lower() for d in defaults[key]}
                        for key, value in was.items())
            if wrote:
                changes.setdefault(section, {}).update(ours)
                was = {key: carried[which].get(key, value) for key, value in was.items()} \
                    if which in carried else was
            marks[which] = {"wrote": wrote, "was": was, "made": made and wrote}
        if changes:
            write(path, changes)
        for which, mark in marks.items():
            _mark(Path(store) / which, mark)
        return bool(changes)


def put_back(install_dir: Path, proxy_relative_dir: str, store: Path,
             stays: str | None = None) -> bool:
    """
    Revert's half: keys first_look set go back to what they were if they still
    read as it left them, or the file goes if it made it and nothing else has
    been written since. `stays` is the sha256 of a NovaFix still beside the
    game afterwards (the user's), whose keys are theirs from here; its markers
    stay so the next game switch doesn't set them again. Returns whether the
    file changed.
    """
    with _WRITING:
        groups = {**ONCE, **{old: group for old, (_, group) in LEGACY.items()}}
        marks = {}
        for which in groups:
            # an OSError reading one isn't a marker that says nothing: it
            # stays, and Revert says it couldn't, for the next one to finish
            said = _read_mark(Path(store) / which)
            if said is not None:
                marks[which] = said
        if not marks:
            return False
        path = config_path(install_dir, proxy_relative_dir)
        found = profile(path)
        back = {}
        for which, said in marks.items():
            if not said.get("wrote"):
                continue
            section, ours, defaults = groups[which]
            was = said.get("was") if isinstance(said.get("was"), dict) else {}
            keys = found.get(section.lower(), {})
            for key, value in ours.items():
                if (keys.get(key.lower()) or "").lower() == value.lower():
                    # missing reads as NovaFix's default, so the default says the same
                    back.setdefault(section, {})[key] = was.get(key) or defaults[key][0]
        putting = {section.lower(): {key.lower() for key in keys} for section, keys in back.items()}
        alone = not found.get("") and all(set(keys) == putting.get(name, set())
                                          for name, keys in found.items() if name)
        if back and alone and any(said.get("made") for said in marks.values()):
            path.unlink(missing_ok=True)
        elif back:
            write(path, back)
        for which in marks:
            if stays:
                section, ours, _ = groups[which]
                keys, mine = found.get(section.lower(), {}), back.get(section, {})
                _mark(Path(store) / which, {
                    "wrote": False, "dll": stays,
                    "was": {key: mine.get(key) or keys.get(key.lower()) for key in ours}})
            else:
                (Path(store) / which).unlink(missing_ok=True)
        return bool(back)


def _mark(marker: Path, said: dict) -> None:
    from patcher.fileops import write_atomic

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(marker, json.dumps(said).encode())
    except OSError:
        # keys it wrote read as somebody's choice from here on anyway, so all
        # this costs is Revert putting them back
        pass


# NovaFix's add-on folder probe list in its order, read out of the shipped
# binary and pinned by a test. It takes the first that exists and only scans
# that one, so making addons/ beside a user's mods/ silently stops theirs
# loading.
ADDON_DIR_NAMES = ("addons", "extra", "extras", "mods", "dll")


def resolve_addons_dir(proxy_root: Path) -> Path:
    """
    the folder NovaFix will actually load add-ons from, without creating one.

    Callers that need it on disk do their own mkdir. Keeping the decision
    separate from the creation is what lets the engine install into the folder
    a user already has rather than manufacturing a competing one.
    """
    for name in ADDON_DIR_NAMES:
        candidate = proxy_root / name
        if candidate.is_dir():
            return candidate
    return proxy_root / ADDON_DIR_NAMES[0]
