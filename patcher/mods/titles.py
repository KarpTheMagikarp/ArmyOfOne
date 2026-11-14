"""
What a mod is called in the drawer, out of whatever its download was called.

Nexus file names carry how the mod was packaged as much as what it is:
"(STANDALONE VERSION) HD Fonts and GUI - Base mod", "Red Rose Beachwear 2.0
(Patcher)". The drawer lists the mod, so it gets the part that names it.

Display only. Records keep the name they went in under, because updates and
Remove find a mod by it, and a title two mods share would make them one.
"""

import re

from patcher.addon_sieve import VERSION_TAIL, VERSION_TOKEN, stamped as _stamped

# how it was packaged, never what it is. Only ever dropped as a whole bracket
# or a whole " - " part, so "HD Texture Pack" keeps its Pack
FILLER = {
    "standalone", "version", "ver", "edition", "patcher", "installer", "nova", "chrysalia",
    "base", "main", "core", "file", "files", "mod", "mods", "full", "complete", "package",
    "pack", "update", "updated", "fixed", "final", "reupload", "re-upload", "latest", "new",
    "release", "optional", "x86", "32bit", "32-bit", "win32", "nexus", "nexusmods",
}
# filler that also turns up bare at the end of a name
TRAILING = {"patcher", "installer", "standalone"}

BRACKETS = re.compile(r"\s*[(\[{]([^)\]}]*)[)\]}]")
VERSIONISH = re.compile(r"v?\d+(?:\.\d+)+|v\d+", re.I)
# a game in front of a name the drawer already shows per game
GAME_TAG = re.compile(r"^(?:final\s+fantasy|ff)\s*(?:xiii|13)(?:\s*-\s*(?:2|ii|lr))?\s+"
                      r"|^lightning\s+returns:?\s+|^lrff13\s+", re.I)


def _all_filler(text: str) -> bool:
    # words in any script: "Русская версия" is a translation, not packaging
    words = re.findall(r"[\w.+-]+", text.lower())
    # "Name (2)" is how a namesake is told apart, so a bare number stays
    return bool(words) and all(w in FILLER or VERSIONISH.fullmatch(w) for w in words)


def _unversioned(text: str) -> str:
    """
    the version off the end, unless it's part of the word before it, as in
    Speed x1.5. Only a name that didn't come through mod_name has one still,
    a Nova Chrysalia modpack's own, say
    """
    token = VERSION_TOKEN.search(text)
    if token and token.start() > 0 and text[token.start() - 1].isalnum() \
            and text[token.start()].isalnum():
        return text
    return text[:token.start()] if token else text


def version(name: str) -> str:
    """
    the version a download's name carries, "1.3", for the drawer to show beside
    its title rather than in it: the one number in Nexus's tail that means
    anything to the person. "" for none
    """
    raw = re.sub(r"\s+", " ", str(name or "")).strip()
    _, said = _stamped(raw)
    if said:
        return said
    tail = VERSION_TAIL.search(raw)
    if tail:
        parts = re.split(r"[-_]", tail.group(0).strip("-_"))
        # name-id-major-minor-time, Nexus's older spelling
        if len(parts) >= 3 and len(parts[-1]) >= 9:
            return ".".join(parts[1:-1])
        return ""
    raw = BRACKETS.sub(lambda m: "" if _all_filler(m.group(1)) else m.group(0), raw).strip()
    token = VERSION_TOKEN.search(raw)
    if token and _unversioned(raw) != raw:
        return re.sub(r"^[\s._-]*v\.?", "", token.group(0), flags=re.I).strip(" ._-").replace("_", ".")
    return ""


def title(name: str) -> str:
    whole = str(name or "").strip()
    # before underscores go, since Nexus's newest names are told by them
    raw = re.sub(r"[\s_]+", " ", _stamped(whole)[0]).strip(" .-") \
        or re.sub(r"[\s_]+", " ", whole).strip()
    # Nexus's id and upload time first, which sit on the last word
    text = VERSION_TAIL.sub("", raw).strip() or raw
    text = BRACKETS.sub(lambda m: "" if _all_filler(m.group(1)) else m.group(0), text)
    head, *rest = re.split(r"\s+[-\u2013]\s+", text)
    text = " - ".join([head] + [part for part in rest if not _all_filler(part)])
    while True:
        bare = text.rsplit(" ", 1)
        if len(bare) == 2 and bare[1].lower() in TRAILING:
            text = bare[0]
            continue
        trimmed = _unversioned(VERSION_TAIL.sub("", text)).strip(" .-")
        if trimmed == text or not trimmed:
            break
        text = trimmed
    untagged = GAME_TAG.sub("", text)
    # two words of it left, and words rather than a dash and a number
    if len(re.findall(r"[^\W\d_]+", untagged)) >= 2:
        text = untagged
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    # a pack's tweak named for the pack itself: Centered Camera (Centered Camera)
    echo = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", text)
    if echo and re.sub(r"[\W_]", "", echo[1]).lower() == re.sub(r"[\W_]", "", echo[2]).lower():
        text = echo[1]
    return text or raw or "mod"
