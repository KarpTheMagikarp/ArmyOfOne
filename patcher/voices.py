"""
The voice language, the one setting in Square Enix's launcher NovaFix doesn't
take over. That window stays hidden once the patcher owns the graphics, which
left the voices with nowhere to change but a hand edit of setup.xml, so the
Settings drawer carries them as one more row.

Not a cosmetic one, either. voice_mode picks which copy of the archives the
game loads, u for English and c for Japanese, and an HD install rebuilds the
copy the launcher has picked. Into the other one too only when its box in the
HD panel is ticked, since that's a second backup and a second copy of the art.
So a switch is the launcher's one write, and the row says so when the set it
lands on is short of the HD Project, rather than leaving that to an evening of
2009 faces.
"""

from patcher import launcher_skip
from patcher.launch import shows_launcher
from patcher.mods.hd_install import voice_sets

SECTION, KEY = "Language", "Voices"
# voice_mode as the launcher writes it, and the archives each one loads
LANGUAGES = {"English": (1, "u"), "Japanese": (0, "c")}

NOTE = "The language the characters speak. The game's launcher used to ask, now it's set here."
# what the row shows in place of choices when there's nothing to pick, and why
NOT_YET = ("After First Launch",
           "The game asks for this the first time it starts. After that, you can change it here.")
NOT_THERE = ("Launcher Only",
             "Your game's settings file has no voice option, so set this in the game's launcher.")


def row(install_dir, app_id, gaps: dict | None = None) -> dict | None:
    """
    the drawer's row, shaped like novafix_config's, or None for a game with no
    launcher to keep a voice setting. `unavailable` stands in for the choices
    when there's nothing to pick, and `warn` is what each switch leaves behind.
    `gaps` is UiBridge.voice_gap's answer per language.
    """
    if not shows_launcher(app_id):
        return None
    sets = voice_sets(install_dir, whole=True)
    names = [name for name, (_, voice) in LANGUAGES.items() if voice in sets]
    if not names:
        # not an install whose archives this can read, and every other part
        # of the app is already saying so
        return None
    mode = launcher_skip.voice_mode(install_dir)
    current = next((name for name, (value, _) in LANGUAGES.items() if value == mode), None)
    shown = {"section": SECTION, "key": KEY, "label": KEY, "kind": "choice",
             "choices": names, "choiceLabels": names, "min": 0, "max": 0, "note": NOTE,
             "file": "launcher", "value": current, "unavailable": "", "warn": {}}

    if current and current not in names:
        # the launcher names a set that isn't there, so the game has nothing
        # to load, and the one that is there is the way out
        return dict(shown, note=f"Set to {current}, but only {names[0]} is installed, so "
                                f"there's nothing to load. Pick {names[0]}.")
    blocked = _blocked(install_dir, names)
    if blocked:
        return dict(shown, unavailable=blocked[0], note=blocked[1])
    for name in names:
        said = _short((gaps or {}).get(name), name)
        if name != current and said:
            shown["warn"][name] = said
    return shown


def check(install_dir, app_id, language) -> str:
    """why `language` can't be set on this game, or "" """
    return _refusal(row(install_dir, app_id), language)


def save(install_dir, app_id, language, gap: dict | None = None) -> dict:
    """
    {"ok", "changed", "notice"}, or {"ok": False, "error"}. A language whose
    archives aren't on disk is refused: the game would go looking for them.
    `gap` is what voice_gap said about it, read before the write.
    """
    shown = row(install_dir, app_id, {language: gap} if gap else None)
    refusal = _refusal(shown, language)
    if refusal:
        return {"ok": False, "error": refusal}
    # worded for after the switch, when the HD panel's box is the other language's
    notice = _short(gap, language, switched=True) if shown["warn"].get(language) else ""
    result = launcher_skip.set_voice(install_dir, LANGUAGES[language][0])
    if not result.changed and result.why != "already set":
        return {"ok": False, "error": f"Couldn't set the voices: {result.why}."}
    return {"ok": True, "changed": result.changed, "notice": notice if result.changed else ""}


def _refusal(shown, language) -> str:
    if shown is None:
        return "This game has no voice setting to change."
    if language not in LANGUAGES:
        return f"{language} isn't a voice language these games have."
    if language not in shown["choices"]:
        return f"The {language} voices aren't installed, so the game would have nothing to play."
    return shown["note"] if shown["unavailable"] else ""


def _blocked(install_dir, names) -> tuple | None:
    if len(names) == 1:
        return f"{names[0]} Only", f"Only the {names[0]} voices are installed."
    path = launcher_skip.setup_path(install_dir)
    if not path.is_file():
        return NOT_YET
    try:
        found = launcher_skip.VOICE.search(path.read_bytes())
    except OSError:
        found = None
    return None if found else NOT_THERE


def _short(gap: dict | None, going: str, switched: bool = False) -> str:
    """
    what switching to `going` leaves of the HD Project behind, from voice_gap's
    answer. `switched` is the same once it has, when a plain install goes into
    `going` and the box beside it copies the other way
    """
    if not gap:
        return ""
    leaving = next((name for name, (_, voice) in LANGUAGES.items() if voice == gap.get("from")),
                   "other")
    mod = gap.get("mod") or "the HD Project"
    fix = f"To have it there too, drop the mod on the window and install it with {going} " \
          "Voices Too ticked."
    if switched:
        fix = (f"To have it here too, drop the mod on the window and install it, which now goes "
               f"into these voices. Switching back to {leaving} first and ticking {going} Voices "
               "Too on its own copies it across instead, which is quicker.")
    if gap.get("none"):
        return (f"{mod[0].upper()}{mod[1:]} is only in the {leaving} voice files, and {going} "
                f"loads its own, so the game looks standard with these voices. {fix}")
    missing, older = gap.get("missing") or [], gap.get("older") or []
    said = " and ".join(([f"without {joined(missing)}"] if missing else [])
                        + ([f"with an older build of {joined(older)}"] if older else []))
    return (f"The {going} voice files are behind the {leaving} ones on {mod}"
            + (f", {said}" if said else "") + f". {fix}")


def joined(names) -> str:
    """"A", "A and B", "A, B and C" """
    names = list(names)
    return names[0] if len(names) == 1 else \
        f"{', '.join(names[:-1])} and {names[-1]}" if names else ""
