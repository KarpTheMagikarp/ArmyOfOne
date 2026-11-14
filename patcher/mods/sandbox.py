"""
A mod's own installer, played on a copy of the game.

Lots of mods are a .bat around a few tools. Instead of sending people off to
run it, it's played here (batch.py is the language) against a fake PC: the
game at Steam's default path as read-only references to the real files, the
drop in C:\\Users\\Player\\Downloads, empty folders where installers look,
7-Zip and WinRAR, this PC's own Python, Java, Node and .NET, one drive, a
registry with only Steam, the game and the archivers, and admin (Machine).

Known tools (ff13tool, ffxiiicrypt, xdelta, Cocoon, 7-Zip, WinRAR, MJB's path
picker, the 4GB patchers) are simulated on references, so the multi-gigabyte
archives never get copied. Anything else runs for real in a staging folder,
with paths on its command line swapped to the staging ones; paths written into
files can't be swapped, so the game folder is compared before and after and a
play that changed it puts nothing in.

Questions get answered like a person would. Version menus come back to the
drawer, an English or Japanese choice is played once per voice set, and the
result goes in through the drawer like a drop, so Remove works.

When a play puts nothing in, diagnose.py reads why, and if it's something a PC
could have had (the game where it looked, a registry key, admin off, 7-Zip on
PATH, ...) this PC gets it and the script's played again, up to ADAPTS times.
What no PC change fixes is reported as the cause with the lines that show it,
never as the "Exiting..." the script printed on its way out.
"""

import errno
import hashlib
import io as _io
import lzma
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from patcher.mods import batch, diagnose
from patcher.mods.batch import child, join, key, leaf, norm, parent
from patcher.mods.game_files import INFLATES, spanned

WORK = "work"
STEAM = "C:\\Program Files (x86)\\Steam"
LIBRARY = STEAM + "\\steamapps\\common"
HOME = "C:\\Users\\Player"
DOWNLOADS = HOME + "\\Downloads"
TEMP = HOME + "\\AppData\\Local\\Temp"
WINDOWS = "C:\\Windows"
ENV = {
    "ALLUSERSPROFILE": "C:\\ProgramData", "APPDATA": HOME + "\\AppData\\Roaming",
    "CommonProgramFiles": "C:\\Program Files\\Common Files",
    "CommonProgramFiles(x86)": "C:\\Program Files (x86)\\Common Files",
    "COMPUTERNAME": "PC", "ComSpec": WINDOWS + "\\system32\\cmd.exe", "HOMEDRIVE": "C:",
    "HOMEPATH": "\\Users\\Player", "LOCALAPPDATA": HOME + "\\AppData\\Local",
    "NUMBER_OF_PROCESSORS": "4", "OS": "Windows_NT",
    "PATH": WINDOWS + "\\system32;" + WINDOWS + ";" + WINDOWS + "\\System32\\Wbem;"
            + WINDOWS + "\\System32\\WindowsPowerShell\\v1.0\\",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC",
    "PROCESSOR_ARCHITECTURE": "AMD64", "ProgramData": "C:\\ProgramData",
    "ProgramFiles": "C:\\Program Files", "ProgramFiles(x86)": "C:\\Program Files (x86)",
    "ProgramW6432": "C:\\Program Files", "PUBLIC": "C:\\Users\\Public", "SystemDrive": "C:",
    "SystemRoot": WINDOWS, "TEMP": TEMP, "TMP": TEMP, "USERDOMAIN": "PC",
    "USERNAME": "Player", "USERPROFILE": HOME, "windir": WINDOWS,
}
# the folders a fresh PC has that installers go looking in
FOLDERS = (WINDOWS + "\\System32", WINDOWS + "\\SysWOW64", "C:\\ProgramData",
           "C:\\Program Files", HOME + "\\Desktop", HOME + "\\Documents", DOWNLOADS,
           HOME + "\\AppData\\Roaming", TEMP, "C:\\Users\\Public")
# the archivers a mod's .bat most often checks for before it unpacks anything,
# where their installers put them. Run, they're played like any 7z.exe
ARCHIVERS = {"C:\\Program Files\\7-Zip": ("7z.exe", "7z.dll"),
             "C:\\Program Files\\WinRAR": ("WinRAR.exe", "Rar.exe", "UnRAR.exe")}
# and what they're asked that isn't an unpack: testing or listing an archive,
# or packing a backup for an uninstall. Fine as far as the script can tell and
# nothing a play keeps, where run, the stand-ins would only fail
CHECKS = {"t", "l", "v", "a", "u"}
# what each game's own folder goes by, its letters and digits alone: Steam's
# name and the short ones people rename it to. A mod library called "FF13
# Mods" isn't one, nor is XIII-2's folder to XIII
GAME_FOLDERS = {
    "white_data": {"finalfantasyxiii", "finalfantasy13", "ffxiii", "ff13"},
    "alba_data": {"finalfantasyxiii2", "finalfantasy132", "ffxiii2", "ff132"},
    "weiss_data": {"lightningreturnsfinalfantasyxiii", "lightningreturnsfinalfantasy13",
                   "lightningreturns", "lightningreturnsffxiii", "lightningreturnsff13", "lrffxiii",
                   "lrff13"},
}
# and words that make a folder named for the game something else: FF13 Mods,
# FFXIII Backup, the Final Fantasy XIII Mod Library
NOT_THE_GAME = re.compile(r"mod|backup|bak|orig|vanilla|librar|save|tool|stock|unmodded", re.I)
# what a file under this size is kept as: bytes, rather than a file on the stage
INLINE = 1 << 20
# a file past this only goes over to a program of theirs when it's named
BIG = 256 << 20
# seconds one program of theirs gets, and every one of them together
TOOL_LIMIT = 900
TOOLS_LIMIT = 3600
# plays of the script while looking for the answers that install something,
# counted so a slow PC finds the same ones, and the seconds one play gets
# before it's taken for a loop
TRIES = 24
GLANCE = 30
# and the seconds cmd itself gets going round a play for real, its programs' time aside
LOOPING = 180
# times the PC's changed for a script that gave up for the want of something,
# each followed by the plays it takes to find answers again
ADAPTS = 4
# the questions a version can take, a menu inside a menu at most, and the
# plays the versions get. Counted, not timed: the list the drawer shows has
# to be the one the install picks from
DEPTH = 4
VERSIONS = 24
TREE = 40
# what Nova Chrysalia looks for in a pack's External folder, in its order: it
# keeps the last one there, so Setup.exe beats Install.bat
NOVA_RUNS = ("install.bat", "install.exe", "setup.bat", "setup.exe")
# the pack's folders Nova installs from, after its installer's had them
NOVA_TREES = ("Data", "EN-Data", "JP-Data", "Code")
# what starts an External installer that's an exe, which a play needs a .bat for
NOVA_RUN = "ArmyOfOne-External.bat"
# what an installer leaves beside the game that's nobody's to install
JUNK = re.compile(r"(^|[\\/])(log\.txt|selectedpath\.txt|[^\\/]*_dir\.txt|thumbs\.db|desktop\.ini|"
                  r"[^\\/]*(read ?me|change ?log|licen[cs]e|credits)[^\\/]*)$"
                  r"|\.(log|tmp|bak|old|orig|md|url|pdf|html?|nfo)$", re.I)
# a folder a mod keeps the game's originals in, for its uninstall
STASH = re.compile(r"(^|/)(originals?|backups?|vanilla|uninstall)/", re.I)
# and the words one's named with however its author liked: Original Opening,
# BackupFiles, Stock. Vanilla\ is as often the version for an unmodded game,
# and Default Ending and Stockings are versions
KEEPS = {"orig", "original", "originals", "backup", "backups", "bak", "stock", "uninstall",
         "unmodded", "unmodified", "untouched"}
# and a Restore\ folder too, when what it puts back has the bytes there already
KEEPS_SAME = KEEPS | {"restore"}
# what a play that went fine and put nothing in is said as, and one that only
# put the game's own files back, originals and all
IDLE, UNDID = diagnose.IDLE, diagnose.UNDID
# a name for taking a mod back off, which is never played unless it says it
# installs too. Any other is looked ahead at and judged by what it puts in:
# one that only puts originals back isn't the installer
UNINSTALL = re.compile(r"un-?install|un-?patch", re.I)
PUTS_IN = re.compile(r"install|setup|apply|enable", re.I)
# a word for undoing something, which a mod's named for too (Remove
# Censorship, Disable Intro Video), so it only says which .bat comes first
UNDOING = re.compile(r"remove|restore|revert|undo|delete|clean|cleanup|reset|rollback|disable|"
                     r"deactivate", re.I)
# what a .bat that does anything has in it, which Launch Game.bat or Visit
# Nexus Page.bat doesn't: starting the game, Steam, a readme or a page isn't
# installing, but running a program of its own is, .exe or not
DOES = re.compile(r"\b(copy|xcopy|robocopy|move|ren|rename|del|erase|mkdir|md|rd|rmdir|call|"
                  r"powershell|expand|tar|ff13tool|ffxiiicrypt|xdelta3?|7za?|cocoon|"
                  r"winrar|unrar|rar)\b|"
                  r"\.(exe|bat|cmd)\b|%~dp0(?!\.\.)[^\"\s]|\d?>>?\s*(?!nul\b)[^\s&|]")
VIEWERS = ("steam.exe", "notepad.exe", "explorer.exe", "write.exe", "wordpad.exe",
           "iexplore.exe", "msedge.exe", "chrome.exe", "firefox.exe")
# and a readme, a picture or a folder of its own it opens for them is no
# program, nor is the game's folder above its own (%~dp0..). Quoted, a
# readme's name can have spaces: "%~dp0Install Instructions.txt"
SEEN = r"\.(txt|md|pdf|html?|url|nfo|jpe?g|png|gif|bmp|webp|ini|log|docx?|rtf|odt|mp4|webm)"
SHOWN = re.compile(r"\"%~dp0[^\"\r\n]*" + SEEN + r"\"|%~dp0[^\"\s]*" + SEEN + r"\b|"
                   r"\bexplorer\s+\"?%~dp0[^\"\r\n]*")
# and what it only says: How to Install.bat naming the .bat that does it.
# An echo into a file or a program, or with a command after it, is doing
# something
SAID = re.compile(r"(?im)^[ \t]*@?(echo\b[^>|&\r\n]*|rem\b[^\r\n]*|::[^\r\n]*)\r?$")
# a .bat for a step of the work, or for another program the mod likes beside
# it: Install ReShade.bat isn't the mod's installer
HELPER = re.compile(r"\b(extract|unpack|repack|decrypt|encrypt|test|debug|log|reshade|dxvk|"
                    r"special ?k)\b", re.I)
# Enable Intro.bat is no likelier the installer than Disable Intro.bat
INSTALLING = re.compile(r"install|setup|apply|patch", re.I)
# a menu line: 1. Lightning's outfit, [2] Snow, A) all of them
MENU = re.compile(r"^\s*[\[(]?\s*([0-9]{1,2}|[A-Za-z])\s*[\]).:-]\s+(\S.*?)\s*$")
LEAVING = re.compile(r"\b(exit|quit|cancel|back|un-?install|remov\w*|restor\w*|revert\w*|undo|"
                     r"vanilla|read ?me|help|credits|about|website|nexus|donat\w*|close|"
                     r"delete\w*|clean\w*|check\w*|verify|log)\b", re.I)
# of those, the ones that get out of a menu rather than undo what it did
YES_NO = re.compile(r"\s*(yes|no)\b(?!\s+\w)", re.I)
# a version named as the game as it ships
OWN = re.compile(r"\s*(the\s+)?(default|original|vanilla|stock|unmodded)\b", re.I)
# a question about how it installs rather than what, whatever its keys say
STEPS = re.compile(r"\b(automatic\w*|manual\w*|find|locate|search\w*|select\w*|"
                   r"director\w*|folder|path|continue|quit|cancel|exit|un-?install\w*|"
                   r"restor\w*|backup|overwrite|keep|skip|retry|abort|english|japanese)\b",
                   re.I)
EXITING = re.compile(r"\b(exit|quit|close|cancel|back|done|finish\w*|end|leave)\b", re.I)
# what a yes or no about the voices says: Japanese voices? (y/n)
SPOKEN = re.compile(r"voice|audio|language|dub|speech", re.I)
GOING_ON = re.compile(r"\b(yes|continue|proceed|install\w*|apply|auto\w*|ok|accept|agree|start|"
                      r"go|use|overwrite|replace|update)\b", re.I)
STOPPING = re.compile(r"\b(no|quit|exit|abort|cancel|stop|skip|later|never)\b", re.I)
# for a bare yes or no: what a yes puts in, what leaves part of it out, a
# part asked about on top (Restore the cut content too?), going back to the
# game as it was (Keep the original intro? Go back to vanilla?), and a
# question about putting the original in (Replace the HD opening with the
# original one?), which a yes to anything put in still is
PUTTING = re.compile(r"\b(install\w*|apply\w*|enable\w*|add|overwrite|replace|update|patch\w*|"
                     r"copy)\b", re.I)
NARROWING = re.compile(r"\b(only|without|except|minimal|lite|just (the|a|an))\b", re.I)
ADDING = re.compile(r"\b(too|as well|also|additionally|extra|optional)\b", re.I)
AS_WAS = r"\b(original|vanilla|backups?|backed[- ]?up|default|unmodded|stock|untouched)"
GOING_BACK = re.compile(
    r"un-?install|un-?patch|\b(restor\w*|revert\w*|reset|roll ?back|go(ing)? back|return|"
    r"switch back|start over|put)\b[^.?!]{0,40}" + AS_WAS + r"|\b(use|keep|prefer|stay with|"
    r"stick with)\b[^.?!]{0,30}\b(original|vanilla|unmodded|stock|untouched)|\b(remove|delete|"
    r"undo|revert|disable)\b[^.?!]{0,20}\b(the |this )?(mod|patch|changes|it)\b", re.I)
WITH_ORIGINAL = re.compile(r"\bwith (the |your |a )?" + AS_WAS[2:], re.I)


class SandboxError(Exception):
    pass


class _Done(Exception):
    """
    back at a menu it's already been through: the install's over. Or the
    same question again and again, which says why nothing went in
    """


class Opaque(SandboxError):
    """a file that's only a description here, an archive part way through being rebuilt"""


@dataclass(frozen=True)
class Theirs:
    """a file of the game's, as it is on the disk now"""
    rel: str


@dataclass(frozen=True)
class Dropped:
    member: str


@dataclass(frozen=True)
class Nested:
    """
    an archive inside the drop, which Source lists the files of. 7-Zip is
    played on that list; anything else gets the archive itself
    """
    member: str


@dataclass(frozen=True)
class Inline:
    data: bytes


@dataclass(frozen=True)
class Kept:
    """bytes a program of theirs made, in the stage's store under their digest"""
    digest: str
    size: int


@dataclass(frozen=True)
class Record:
    """one file inside one of the game's index and archive pairs"""
    index: Theirs
    archive: Theirs
    path: str


@dataclass(frozen=True)
class Listing:
    """an index as a tool's left it: the game's, with records swapped"""
    base: Theirs
    changes: tuple = ()
    plain: object = None

    def changed(self) -> dict:
        return dict(self.changes)


@dataclass(frozen=True)
class Blob:
    """an archive as a tool's left it: the game's grown, or one ff13tool -c built"""
    base: object
    extra: frozenset = frozenset()
    contents: tuple = ()


@dataclass(frozen=True)
class Patched:
    base: object
    patch: object


@dataclass(frozen=True)
class Joined:
    """
    copy /b part1+part2 whole.bin: the parts end to end, read through only
    when something wants the bytes
    """
    parts: tuple


@dataclass(frozen=True)
class Broken:
    """what ff13tool -c leaves of an archive when a file it wanted wasn't there"""
    why: str


@dataclass
class Played:
    bat: str = ""
    work: Path | None = None
    versions: list = field(default_factory=list)
    problem: str = ""
    # what to do about it and what showed it, for behind the drawer's (i), and
    # diagnose's word for what kind of reason it is
    detail: str = ""
    kind: str = ""
    # what the copy's PC was changed to get the script past, in words
    tried: list = field(default_factory=list)
    notes: list = field(default_factory=list)       # sentences on what didn't go in
    skipped: list = field(default_factory=list)     # what it tried outside the game's files
    asked: list = field(default_factory=list)
    ran: list = field(default_factory=list)
    languages: list = field(default_factory=list)
    # all it puts in is in the game already: dropped again, or put in by hand
    already: bool = False
    # that, because the pick was the game's own setting rather than the mod:
    # "already" when the game has it, "restores" when it'd take the mod back off
    own: str = ""
    # of its versions, the one the game has in now
    current: list = field(default_factory=list)
    # World.wdb's, for the record
    wdb: list = field(default_factory=list)
    # the base mod (deps.py) it's an add-on for, when not having that is why
    needs: str = ""

    @property
    def idle(self) -> bool:
        """whether it got to the end with nothing gone wrong and nothing changed"""
        return self.kind in diagnose.IDLING


def find(members, read=None, data: str = "") -> list:
    """
    the drop's installer .bats, best first, as (member, score). One another
    .bat calls is that one's helper and left off, and so is one named for
    taking a mod back off. `read` reads one, to tell which game each is for
    in a pack for all three, `data` being this game's data folder, and which
    does nothing but start the game or a web page
    """
    from patcher.mods.game_files import DATA_DIRS, GAME_EXES

    bats = [m for m, _ in members if m.lower().endswith((".bat", ".cmd"))]
    # what starting only shows the person something: the game, Steam, a page
    shows = (*GAME_EXES, *VIEWERS)
    texts = {}
    for bat in bats if read is not None else ():
        try:
            texts[bat] = batch.decode(read(bat)).lower()
        except Exception:
            texts[bat] = ""
    called = set()
    for bat, text in texts.items():
        for other in bats:
            stem = leaf(other).lower().rsplit(".", 1)[0]
            if other != bat and re.search(r"\bcall\b[^\n]*" + re.escape(stem), text):
                called.add(other)
    scored = []
    for bat in bats:
        stem = leaf(bat).rsplit(".", 1)[0]
        if bat in called or undoes(stem):
            continue
        words = re.findall(r"[a-z]+", stem.lower())
        score = (2 if INSTALLING.search(stem) else 0) - (3 if HELPER.search(stem) else 0) \
            - (1 if any(UNDOING.fullmatch(w) for w in words) else 0)
        text = texts.get(bat, "")
        others = [d for d in DATA_DIRS if d != data and d in text]
        if data and data in text:
            score += 1
        elif others:
            # a pack's .bat for another of the three games
            score -= 10
        if bat in texts and not DOES.search(SHOWN.sub("", SAID.sub("", _without(text, shows)))):
            score -= 5
        scored.append((-score, bat.count("/"), bat.lower(), bat))
    return [(bat, -score) for score, _, _, bat in sorted(scored)]


def tweaks(source, members, data: str = "") -> list:
    """
    the labels of a drop's installer .bats when each is a tweak of its own,
    Mod_RunSpeed.bat beside Mod_Adamantite.bat, which go in together, rather
    than one Install.bat per folder, which are versions of one thing. [] for
    any other drop
    """
    if nova_external(source, members) is not None:
        return []
    choices = rivals(find(members, source.read, data))
    if len(choices) < 2 or len({label(b) for b in choices}) < len(choices):
        return []
    return list(labels(choices))


def tweak_name(text: str) -> str:
    """what a tweak's .bat is called, as a name: Mod_RunSpeed is RunSpeed"""
    text = re.sub(r"^[\s\-_.]+", "", text)
    text = re.sub(r"^mod[\s_\-]+", "", text, flags=re.I).replace("_", " ").strip()
    return text or "Tweak"


def _name_words(name: str) -> list:
    # OriginalOpening and Backup_Files both, word by word
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", name)]


def _keeps(member: str, home: str = "", words=KEEPS) -> bool:
    """
    whether a drop's file sits in a folder named for originals kept for an
    uninstall, under the folder its .bat is in (Restore Cut Content, say,
    which is only the mod's name)
    """
    if home and member.lower().startswith(home.lower() + "/"):
        member = member[len(home) + 1:]
    return any(words & set(_name_words(folder)) for folder in member.split("/")[:-1])


def _without(text: str, names) -> str:
    for name in names:
        text = text.replace(name, "")
    return text


def undoes(stem: str) -> bool:
    """whether a .bat's name says it takes a mod back off: not Uninstall Old and Install"""
    return bool(UNINSTALL.search(stem)) and not PUTS_IN.search(UNINSTALL.sub(" ", stem))


def rivals(found: list) -> list:
    """
    the ones as likely as the first to be the installer, beside it: one per
    version. Enable Intro.bat beside Disable Intro.bat is one too, since only
    the drop's name says which way round its mod is, and so is the same .bat
    in the folder beside its own: English Voices\\Install.bat and Japanese
    Voices\\Install.bat
    """
    if not found:
        return []
    top, score = found[0]
    folder = _folder(top)
    out = [bat for bat, s in found if s == score and _folder(bat) == folder]
    if folder:
        names = {leaf(bat).lower() for bat in out}
        out += [bat for bat, s in found if bat not in out and s == score
                and leaf(bat).lower() in names and _folder(_folder(bat)) == _folder(folder)]
    flip = {"enable": "disable", "disable": "enable", "on": "off", "off": "on"}
    turned = re.sub(r"\b(enable|disable|on|off)\b", lambda m: flip[m.group(1).lower()],
                    leaf(top).lower())
    out += [bat for bat, _ in found if bat not in out and leaf(bat).lower() == turned
            and _folder(bat) == folder]
    return out


def _folder(member: str) -> str:
    return member.rsplit("/", 1)[0] if "/" in member else ""


def label(bat: str) -> str:
    return leaf(bat).rsplit(".", 1)[0]


def labels(bats: list) -> dict:
    """
    what each version .bat is called: its name, its folder's when each
    folder has the one (Install.bat in English Voices and in Japanese
    Voices), or both when a name's in more than one folder among others
    """
    stems = [label(b) for b in bats]
    folders = [leaf(_folder(b)) for b in bats]
    if len(set(stems)) == 1 and len(set(folders)) == len(bats):
        names = folders
    else:
        names = [f"{stem} ({where})" if stems.count(stem) > 1 and where else stem
                 for stem, where in zip(stems, folders)]
    out = {}
    for name, bat in zip(names, bats):
        out[name if name not in out else bat] = bat
    return out


def sweep(install_dir) -> None:
    """stages an earlier play left, a crash or a power cut halfway"""
    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    work = undo_store_for(install_dir) / WORK
    for leftover in work.glob("bat-*") if work.is_dir() else ():
        clear_tree(leftover)


@dataclass
class Machine:
    """
    the PC a play runs on, as far as a script can ask it. Each change made to
    it is one a script gave up for the want of, made before it's played again
    """
    admin: bool = True
    windows: str = "10.0.19045.5011"
    # registry keys it's been given: key -> value name, lowercased -> (name, type, data)
    keys: dict = field(default_factory=dict)
    # each change as made, so none is made twice, and in words for the drawer
    done: list = field(default_factory=list)
    tried: list = field(default_factory=list)

    def values(self, place: str):
        """a key's values when it's been given that key, else None"""
        return self.keys.get(_hive(place))


def _hive(place: str) -> str:
    """a registry key the one way, however a script spelled its hive"""
    low = place.strip().strip('"').rstrip("\\").lower()
    for long, short in (("hkey_local_machine", "hklm"), ("hkey_current_user", "hkcu"),
                        ("hkey_classes_root", "hkcr"), ("hkey_users", "hku")):
        if low.startswith(long):
            low = short + low[len(long):]
    return low


# this PC's own Python, Java, Node, .NET and 7-Zip, kept once found. One
# that isn't is looked for again a little later: the reason says to install
# it and drop the mod again, which has to work without restarting the app
_HOSTS = {}
_MISSED = {}
MISSED_FOR = 15
# whether this PC is one, so its own paths mean what a script's do
ON_WINDOWS = sys.platform == "win32"


def _missing(what: str) -> bool:
    """whether `what` was looked for and not found a moment ago"""
    return time.monotonic() - _MISSED.get(what, -MISSED_FOR - 1) <= MISSED_FOR


def _path_now() -> str:
    """
    the PATH as Windows has it now, not only the one this app was started
    with: Python installed since, Add to PATH ticked, is on the registry's
    """
    paths = [os.environ.get("PATH", "")]
    if ON_WINDOWS:
        import winreg

        for hive, where in ((winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                            (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(hive, where) as opened:
                    paths.append(os.path.expandvars(winreg.QueryValueEx(opened, "Path")[0]))
            except OSError:
                continue
    return os.pathsep.join(p for p in paths if p)


def host_runtime(runtime: str):
    """
    the real program for one of diagnose.RUNTIMES on this PC, or None. The
    python.exe Windows ships that only opens the Microsoft Store isn't one
    """
    if _HOSTS.get(runtime) or _missing(runtime):
        return _HOSTS.get(runtime)
    path = _path_now()
    for name in diagnose.RUNTIMES[runtime][1]:
        found = shutil.which(name, path=path)
        if found and _answers(found):
            _HOSTS[runtime] = found
            return found
    _MISSED[runtime] = time.monotonic()
    return None


def host_archiver():
    """
    this PC's own 7-Zip, for an archive neither the drawer nor a play can
    open: a .7z on a Windows whose tar can't, a zip packed with Deflate64 or
    AES. None when it hasn't got one
    """
    if _HOSTS.get("7-zip") or _missing("7-zip"):
        return _HOSTS.get("7-zip")
    spots = [os.path.join(os.environ[v], "7-Zip", "7z.exe")
             for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
    path = _path_now()
    found = next((spot for spot in spots if os.path.isfile(spot)), None) \
        or shutil.which("7z", path=path) or shutil.which("7za", path=path)
    if found:
        _HOSTS["7-zip"] = found
    else:
        _MISSED["7-zip"] = time.monotonic()
    return found


# and what each says its version is, asked once it says one
_VERSIONS = {}


def host_version(runtime: str) -> str:
    """the version this PC's own runtime says it is, "3.12.6" say, or """""
    if _VERSIONS.get(runtime):
        return _VERSIONS[runtime]
    real, said = host_runtime(runtime), ""
    if real is not None:
        try:
            done = subprocess.run([real, "-version" if runtime == "java" else "--version"],
                                  capture_output=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            hit = re.search(rb"(\d+(\.\d+)+)", done.stdout + done.stderr)
            said = hit.group(1).decode() if hit else ""
        except (OSError, subprocess.SubprocessError):
            said = ""
    _VERSIONS[runtime] = said
    return said


def host_runtime_of(runtime: str, version: str = "", at: str = ""):
    """
    this PC's own runtime of the version a script wants: the program at the
    very path it checks when this PC has one there (C:\\Python27\\python.exe),
    the usual one when its major version is the one asked for, or another
    install of that version where its installer puts it. None for none
    """
    if at and ON_WINDOWS and os.path.isfile(at):
        return at
    real = host_runtime(runtime)
    if real is not None and (not version or _same_major(host_version(runtime), version)):
        return real
    if runtime != "python" or not version or not ON_WINDOWS:
        return None
    named = f"Python{''.join(version.split('.')[:2])}"
    spots = [f"C:\\{named}\\python.exe",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", named,
                          "python.exe"),
             *(os.path.join(os.environ[v], named, "python.exe")
               for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v))]
    return next((spot for spot in spots if os.path.isfile(spot)), None)


def _runtime_home(runtime: str, version: str) -> str:
    """where a version of a runtime its own registry key names is put: C:\\Python27\\python.exe"""
    if runtime == "python" and version:
        return f"C:\\Python{''.join(version.split('.')[:2])}\\python.exe"
    return diagnose.RUNTIMES[runtime][2]


def _same_major(have: str, want: str) -> bool:
    """whether a runtime's version is the one a path asks for: 3.12 for Python3, never 2.7"""
    def major(v):
        # Java's 1.8 is its 8
        parts = v.split(".")
        return parts[1] if parts[0] == "1" and len(parts) > 1 else parts[0]
    return bool(have) and major(have) == major(want)


def _answers(path: str) -> bool:
    if "windowsapps" not in path.lower():
        return True
    try:
        done = subprocess.run([path, "--version"], capture_output=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and bool(re.search(rb"\d", done.stdout + done.stderr))


# what an add-on can lean on another download for by name alone: something it
# runs. A bare data file is too likely to be some other mod's that's similarly named
PROGRAMS = (".exe", ".dll", ".bat", ".cmd", ".com", ".py", ".jar", ".ps1", ".vbs")
# past this a folder's someone's whole download, not the tools beside a .bat
LEND_FILES, LEND_BYTES = 4000, 2 << 30


# what a download might be kept as that only an unpack reads, Windows' own tar
# or this PC's 7-Zip doing it
SEALED = (".7z", ".rar")


def _sealed(archive: Path, *more: str) -> list:
    """
    the argvs that list (no `more`) or unpack the named members of a .7z or
    .rar: Windows' own tar, bsdtar, which reads both, then 7-Zip
    """
    tar = shutil.which("bsdtar") or shutil.which("tar")
    seven = host_archiver()
    if not more:
        return ([[tar, "-tf", str(archive)]] if tar else []) + \
            ([[seven, "l", "-ba", "-slt", str(archive)]] if seven else [])
    into, *names = more
    return ([[tar, "-xf", str(archive), "-C", into, *names]] if tar else []) + \
        ([[seven, "x", str(archive), f"-o{into}", "-y", *names]] if seven else [])


def _sealed_names(archive: Path) -> list:
    """what a .7z or .rar lists, off its headers alone: nothing's unpacked to know"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for argv in _sealed(archive):
        try:
            done = subprocess.run(argv, capture_output=True, timeout=60,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode != 0:
            continue
        lines = done.stdout.decode("utf-8", "replace").splitlines()
        if "-slt" in argv:
            lines = [line[len("Path = "):] for line in lines if line.startswith("Path = ")]
        names = [n.strip().replace("\\", "/").rstrip("/") for n in lines if n.strip()]
        if names:
            return names
    return []


def _unseal(archive: Path, top: str, into: Path) -> bool:
    """
    one folder of a .7z or .rar unpacked under `into`, and only that: both
    tars and 7-Zip take a folder's name for everything in it. Nothing that
    lands outside `into` is kept, the way ingest.try_system_extract has it
    """
    from patcher.ingest import _escapes

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for argv in _sealed(archive, str(into), top):
        shutil.rmtree(into, ignore_errors=True)
        into.mkdir(parents=True, exist_ok=True)
        try:
            done = subprocess.run(argv, capture_output=True, timeout=600,
                                  stdin=subprocess.DEVNULL, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0 and _escapes(into) is None and \
                _on_disk(into, top.split("/")) is not None:
            return True
    shutil.rmtree(into, ignore_errors=True)
    return False


def _on_disk(base: Path, parts) -> Path | None:
    """`parts` under `base` the way Windows finds them, whatever their case"""
    at = base
    for part in parts:
        hit = at / part
        # Windows matches case itself, so a miss there is a miss
        if not hit.exists() and os.name != "nt":
            try:
                hit = next((p for p in at.iterdir() if p.name.casefold() == part.casefold()),
                           None)
            except OSError:
                return None
            if hit is None:
                return None
        elif not hit.exists():
            return None
        at = hit
    return at


class Lenders:
    """
    other downloads an add-on's .bat may expect to have been unpacked into.
    A Vanille outfit for HD Models Plus ships its own Data\\ and runs
    SupportFiles\\FF XIII HD Models.exe, which only HD Models Plus's download
    has. `homes` are download roots, the kept ones (deps.py) and the folders
    and zips the drawer put mods in from; `near` are folders whose folders are
    each one, the drop's own Downloads say. `data` is the game's data folder,
    so another game's known download is never lent. Only ever looked at for a
    path the drop hasn't got
    """

    def __init__(self, homes=(), near=(), data: str = "", gone=None):
        self.homes = [Path(p) for p in homes]
        self.near = [Path(p) for p in near]
        self.data = data
        # base mod id -> the download it went in from, for one in the game
        # whose tools weren't kept and can't be from there
        self.gone = dict(gone or {})
        self._zips = {}

    def _roots(self):
        seen = set()
        for root in self.homes:
            if str(root).lower() not in seen:
                seen.add(str(root).lower())
                yield root
        for folder in self.near:
            try:
                kids = sorted(p for p in folder.iterdir() if p.is_dir())[:200]
            except OSError:
                continue
            for kid in kids:
                if str(kid).lower() not in seen:
                    seen.add(str(kid).lower())
                    yield kid

    def _listing(self, archive: Path) -> dict:
        """a zip's or a .7z's or .rar's members, lower case -> as it spells them"""
        if archive not in self._zips:
            names = []
            if archive.suffix.lower() in SEALED:
                names = _sealed_names(archive)
            else:
                try:
                    with zipfile.ZipFile(archive) as z:
                        names = [i.filename for i in z.infolist()]
                except (OSError, zipfile.BadZipFile, ValueError):
                    pass
            self._zips[archive] = {n.replace("\\", "/").rstrip("/").lower(): n for n in names}
        return self._zips[archive]

    def _foreign(self, root: Path) -> bool:
        from patcher.mods import deps

        if root.is_dir():
            def has(mark):
                bits = mark.split("/")
                if _on_disk(root, bits) is not None:
                    return True
                try:
                    return any(_on_disk(kid, bits) is not None
                               for kid in list(root.iterdir())[:50] if kid.is_dir())
                except OSError:
                    return False
        else:
            names = self._listing(root)

            def has(mark):
                return any(low == mark or low.endswith("/" + mark) for low in names)
        return deps.foreign(has, self.data)

    def _within(self, where, rest: list) -> bool:
        """whether a lent folder has `rest` under it, a bare program by its PATHEXT names too"""
        tries = [rest] + ([rest[:-1] + [rest[-1] + ext] for ext in (".exe", ".com", ".bat", ".cmd")]
                          if "." not in rest[-1] else [])
        if isinstance(where, Path):
            return any(_on_disk(where, t) is not None for t in tries)
        archive, top = where
        names = self._listing(archive)
        return any(f"{top}/{'/'.join(t).lower()}" in names for t in tries)

    def find(self, parts: list, ran: bool = False, mentions=None):
        """
        (how many of `parts` matched from the end, where their first one is,
        whose download it is) for the longest tail of `parts` another download
        has. Where is a Path, or (zip, member) for one in a zip. None for none.
        `ran` is cmd running it, so a bare ff13tool is ff13tool.exe the way
        PATHEXT has it. `mentions(folder)` is everything the .bat names under
        a folder by that name, and a download has to have all of it to lend
        the folder: one with the right exe and not the .bat beside it is
        another mod's that happens to share a name
        """
        from patcher.mods import deps

        last = parts[-1]
        spelled = [last] + ([last + ext for ext in (".exe", ".com", ".bat", ".cmd")]
                            if ran and "." not in last else [])
        for n in range(len(parts)):
            if n == len(parts) - 1 and not (ran or last.lower().endswith(PROGRAMS)):
                return None
            for leafname in spelled:
                tail = parts[n:-1] + [leafname]
                joined = "/".join(tail).lower()
                wanted = (mentions(tail[0]) if mentions and len(tail) > 1 else [])
                for root in self._roots():
                    where = None
                    if root.is_dir():
                        hit = _on_disk(root, tail)
                        if hit is not None:
                            for _ in tail[1:]:
                                hit = hit.parent
                            where, whose = hit, root.name
                    elif root.is_file() and self._listing(root):
                        names = self._listing(root)
                        spot = next((low for low in names
                                     if low == joined or low.endswith("/" + joined)), None)
                        if spot is not None:
                            where = (root, spot[:len(spot) - len(joined)] + tail[0].lower())
                            whose = root.stem
                    if where is None or self._foreign(root):
                        continue
                    if all(self._within(where, rest) for rest in wanted):
                        return len(tail), where, deps.label_of(root) or whose
        return None


class Stage:
    """
    one install attempt: the game and the drop mounted, the store, and what
    every play starts from
    """

    def __init__(self, install_dir, game, source, members, bat: str, name: str, folder,
                 in_game: bool = False, went_over=None, lenders: Lenders | None = None,
                 nova=None):
        self.install_dir = Path(install_dir)
        self.lenders = lenders
        self.game = game
        self.source = source
        # the drawer's record of a mod by the drop's name: whether all of it's
        # in the game still, and what each of its files went in over
        self.in_game = in_game
        self.went_over = {k.lower(): v for k, v in (went_over or {}).items()}
        self.digests = {}
        self.sizes = dict(members)
        self.folder = Path(folder)
        self.store = self.folder / "s"
        self.store.mkdir(parents=True, exist_ok=True)
        self.nodes, self.kids = {}, {}
        self.indexes, self.decoded = {}, {}
        self.compared = {}
        self.nested = {}
        # a zip's member as inflated into the store, by the zip and the
        # member, so the plays after the first don't inflate it again
        self.inflated = {}
        # key -> (path, the drop's file) for every play to start with: what a
        # .bat made to run in the game's folder came unpacked over it with
        self.overlay = {}
        self.mod = child(DOWNLOADS, _safe(name) or "Mod")
        self.root = child(LIBRARY, self.install_dir.name)
        # the PC as scripts find it, the other places the game's been mounted
        # for a script that looks for it there, and this PC's own runtimes,
        # by where the play has them -> the real program
        self.machine = Machine()
        self.aliases = []
        self.hosts = {}
        # what's dragged onto the .bat, for one made to have the game dropped on it
        self.args = []
        self.env = dict(ENV)
        self.known = None
        self.pairs = {}
        for pair in game.pairs:
            index = pair.index.relative_to(self.install_dir).as_posix()
            archive = pair.archive.relative_to(self.install_dir).as_posix()
            self.pairs[index.lower()] = (Theirs(index), Theirs(archive), pair)
        for folder_path in ("C:\\", STEAM, LIBRARY, *FOLDERS):
            self.mkdirs(folder_path)
        self.put(WDBLOAD, self.keep(batch.encode(WDBLOAD_TEXT.replace("\n", "\r\n"))))
        self._mount_steam()
        self._mount_archivers()
        self._mount_runtimes()
        self._mount_game()
        self.texts = {}
        self.home = self.mod
        # the archives' records mounted loose, Nova's unpacked game, once a
        # play wants it (unpack_game)
        self.unpacked_game = False
        # its empty ones too, which members() leaves out with the rest
        self.folders = [f for f in getattr(source, "folders", list)()
                        if not any(f == n or f.startswith(n + "/")
                                   for n in getattr(source, "nested", []) or [])]
        self._mount_drop(members, bat)
        # a Nova pack's own folders, when it's its External installer playing
        self.trees = []
        if nova is not None:
            self._mount_nova(*nova)
        # the drop's originals beside the files they're the originals of:
        # originals\op.win32.wmp next to the op.win32.wmp that goes in
        self.bat_folder = bat.rsplit("/", 1)[0] if "/" in bat else ""
        names = {}
        for member in self.sizes:
            names.setdefault(leaf(member).lower(), []).append(member)
        self.kept = {m for same in names.values()
                     if not all(_keeps(o, self.bat_folder) for o in same)
                     for m in same if _keeps(m, self.bat_folder)}
        self.mounts = sorted([(key(self.root), self.root, self.folder / "g"),
                              (key(self.mod), self.mod, self.folder / "m"),
                              (key(TEMP), TEMP, self.folder / "t")],
                             key=lambda m: -len(m[0]))

    def mkdirs(self, path: str) -> None:
        path = norm(path)
        chain = []
        while key(path) not in self.nodes:
            chain.append(path)
            if parent(path) == path:
                break
            path = parent(path)
        for each in reversed(chain):
            self.nodes[key(each)] = (each, None)
            self.kids.setdefault(key(each), {})
            if parent(each) != each:
                self.kids.setdefault(key(parent(each)), {})[leaf(each).lower()] = leaf(each)

    def put(self, path: str, ref) -> None:
        path = norm(path)
        self.mkdirs(parent(path))
        self.nodes[key(path)] = (path, ref)
        self.kids[key(parent(path))][leaf(path).lower()] = leaf(path)

    def _mount_steam(self) -> None:
        """what Steam keeps that installers read to find a game: its library list and the
        game's manifest, both saying it's in the one library there is"""
        from patcher.mods.game_files import STEAM_APPS

        app = STEAM_APPS.get(self.game.data, "")
        steam = STEAM.replace("\\", "\\\\")
        self.put(STEAM + "\\steam.exe", Inline(b"MZ"))
        self.put(STEAM + "\\steamapps\\libraryfolders.vdf", Inline(
            f'"libraryfolders"\r\n{{\r\n\t"0"\r\n\t{{\r\n\t\t"path"\t\t"{steam}"\r\n'
            f'\t\t"apps"\r\n\t\t{{\r\n\t\t\t"{app}"\t\t"0"\r\n\t\t}}\r\n\t}}\r\n}}\r\n'
            .encode()))
        if app:
            self.put(STEAM + f"\\steamapps\\appmanifest_{app}.acf", Inline(
                f'"AppState"\r\n{{\r\n\t"appid"\t\t"{app}"\r\n'
                f'\t"installdir"\t\t"{self.install_dir.name}"\r\n}}\r\n'.encode()))

    def _mount_archivers(self) -> None:
        """7-Zip and WinRAR, installed: a .bat that finds neither says to go get one and exits"""
        for folder, names in ARCHIVERS.items():
            for name in names:
                self.put(child(folder, name), Inline(b"MZ"))

    def _mount_runtimes(self) -> None:
        """Python, Java and the like where their installers put them, when this PC has them for real"""
        added = []
        for runtime, (_, names, where, *_) in diagnose.RUNTIMES.items():
            real = host_runtime(runtime)
            if real is None:
                continue
            for name in names:
                self.put(child(parent(where), name + ".exe"), Inline(b"MZ"))
                self.hosts[key(child(parent(where), name + ".exe"))] = real
            added.append(parent(where))
        if added:
            self.env["PATH"] = ";".join([self.env["PATH"], *added])

    def _mount_game(self) -> None:
        base = self.install_dir
        for folder, dirs, files in os.walk(base):
            rel = Path(folder).relative_to(base).as_posix()
            rel = "" if rel == "." else rel
            if not rel:
                dirs[:] = [d for d in dirs if d.lower() != ".patcher-undo"]
            here = child(self.root, rel.replace("/", "\\")) if rel else self.root
            self.mkdirs(here)
            for name in files:
                self.put(child(here, name), Theirs(f"{rel}/{name}" if rel else name))

    def _mount_drop(self, members, bat: str) -> None:
        nested = list(getattr(self.source, "nested", []) or [])
        for member, _ in members:
            outer = next((n for n in nested if member.startswith(n + "/")), None)
            if outer is not None:
                self.nested.setdefault(outer, []).append(member)
                continue
            self.put(child(self.mod, member.replace("/", "\\")), Dropped(member))
            if member.lower().endswith((".bat", ".cmd")):
                try:
                    self.texts[member] = batch.decode(self.source.read(member))
                except Exception:
                    pass
        for outer in nested:
            self.put(child(self.mod, outer.replace("/", "\\")), Nested(outer))
            self.sizes[outer] = self.source.size(outer)
        for folder in self.folders:
            self.mkdirs(child(self.mod, folder.replace("/", "\\")))
        self.bat = child(self.mod, bat.replace("/", "\\"))
        self.dropped = (list(members), bat)
        climb = _home(self.texts.get(bat, ""), self)
        if climb is not None:
            self.unpack_home(*self.home_for(climb))

    def _mount_nova(self, pack: str, run: str) -> None:
        """
        what Nova Chrysalia hands an External installer: the game unpacked,
        which is the only way Nova installs anything, whitepath.txt in its
        folder saying where, and an empty Data folder for it to fill. Every
        record the archives hold is a file at its place under the data
        folder, the English set's over the Japanese where both have one, so
        an installer that edits one in place reads what the game has now
        """
        white = self.unpack_game()
        base = child(self.mod, pack.replace("/", "\\")) if pack else self.mod
        # Nova's put the pack's Data in before it runs the installer
        inside = (pack + "/data/").lower() if pack else "data/"
        for member in self.sizes:
            if member.lower().startswith(inside) and not member.endswith("/"):
                self.put(child(white, member[len(inside):].replace("/", "\\")), Dropped(member))
        # File.WriteAllText: the path with its \\ on the end, no newline
        self.put(child(base, "External\\whitepath.txt"), Inline((white + "\\").encode()))
        self.mkdirs(child(base, "Data"))
        self.trees = [child(base, tree) for tree in NOVA_TREES]
        if not run.lower().endswith(".bat"):
            self.bat = child(base, "External\\" + NOVA_RUN)
            self.put(self.bat, Inline(f'@"%~dp0{leaf(run)}"\r\n'.encode()))

    def unpack_game(self) -> str:
        """
        the game as Nova Chrysalia unpacks it, without unpacking anything:
        every record the archives hold a file at its place under the data
        folder, read out of the archive only if the script reads it. The
        English set's over the Japanese where both have one. The data folder
        """
        white = norm(child(self.root, self.game.data))
        self.mkdirs(white)
        records = {}
        for index, archive, pair in sorted(self.pairs.values(), key=lambda p: p[2].voice == "u"):
            for path, entry in self.entries(index).items():
                if not entry.is_stub:
                    records[path.lower()] = (path, Record(index, archive, path))
        # tens of thousands of them, so not through put(): a record's path is
        # already one norm() would leave alone
        low = key(white)
        for path, ref in records.values():
            spelled = white + "\\" + path.replace("/", "\\")
            k = low + "\\" + path.lower().replace("/", "\\")
            if k in self.nodes:
                # a file the game has loose there is the one it reads
                continue
            folder, _, name = spelled.rpartition("\\")
            if k.rpartition("\\")[0] not in self.nodes:
                self.mkdirs(folder)
            self.nodes[k] = (spelled, ref)
            self.kids.setdefault(k.rpartition("\\")[0], {})[name.lower()] = name
        self.known = None
        self.unpacked_game = True
        return white

    def in_archives(self, rel: str) -> bool:
        """whether a game path under the data folder is a record, or a folder of them"""
        data = (self.game.data or "").lower()
        rel = rel.lower().replace("\\", "/").strip("/")
        if not data or not rel.startswith(data + "/"):
            return False
        inner = rel[len(data) + 1:]
        return any(path.lower() == inner or path.lower().startswith(inner + "/")
                   for index, _, _ in self.pairs.values() for path in self.entries(index))

    def home_for(self, climb: int) -> tuple:
        """
        (where in the game, which of the drop's folders goes there) for a .bat
        whose paths climb `climb` folders up to the game's: 0 is its own folder
        at the top of the game, 1 is one folder of its own inside it
        (..\\white_data\\sys). The folders the zip has above the .bat are those,
        and past them a folder named for the mod
        """
        above = self.dropped[1].split("/")[:-1]
        if climb <= len(above):
            kept = above[:len(above) - climb]
            return self.root, "/".join(kept) + "/" if kept else ""
        home = self.root
        for n in range(climb - len(above)):
            home = child(home, leaf(self.mod) if n == 0 else f"{leaf(self.mod)} {n + 1}")
        return home, ""

    def game_copy(self, inner: str):
        """
        the game's own program a path in the drop's folder names, for a .bat
        whose readme says to copy one in beside it (a 4GB patcher's
        ffxiiiimg.exe): by the longest end of the path the game has, a lone
        name only when the game has the one file by it. Only an exe or dll,
        since a movie or archive by the game's name is the mod's own, missing.
        Its place in the game, or None
        """
        self.game_path("")
        bits = inner.replace("/", "\\").strip("\\").lower().split("\\")
        if not bits[-1].endswith((".exe", ".dll")):
            return None
        for n in range(len(bits) - 1):
            tail = "\\".join(bits[n:])
            if tail in self.known:
                return tail
        named = [k for k in self.known if k == bits[-1] or k.endswith("\\" + bits[-1])]
        if len(named) != 1:
            return None
        node = self.nodes.get(key(child(self.root, named[0])))
        return named[0] if node is not None and node[1] is not None else None

    def climb_home(self, path: str):
        """
        how many folders up from the .bat's own a path it wanted climbs to
        reach a file or folder of the game's, the way one made to be unzipped
        into a folder inside the game's looks: None for any other path
        """
        here, there = norm(parent(self.bat)).split("\\"), norm(path).split("\\")
        same = 0
        while same < min(len(here), len(there)) and here[same].lower() == there[same].lower():
            same += 1
        up, down = len(here) - same, there[same:]
        if up < 1 or not down or self.game_rel(path) is not None:
            return None
        self.game_path("")
        if "\\".join(down).lower() in self.known:
            return up
        # or a file in one of the game's data folders this copy hasn't got,
        # the other voice set's filelist_scrc beside the filelist_scru it has
        data = (self.game.data or "").lower()
        inside = len(down) > 2 and down[0].lower() == data
        return up if inside and "\\".join(down[:-1]).lower() in self.known else None

    def unpack_home(self, home: str, folder: str = None) -> None:
        """
        a .bat made to be run from inside the game's folder: its folder goes in
        there, the way its readme says to unpack it. What lands in one of the
        game's own folders, or over one of its files, is the mod, and each play
        starts with it there. The rest, the .bat and its tools beside the game,
        is only the installer's. `folder` is the drop's folder that lands at
        `home`, the .bat's own unless home_for says otherwise
        """
        members, bat = self.dropped
        nested = list(getattr(self.source, "nested", []) or [])
        if folder is None:
            folder = bat.rsplit("/", 1)[0] + "/" if "/" in bat else ""
        ours = {k for k, (spelled, ref) in self.nodes.items()
                if self.game_rel(spelled) and (ref is None or isinstance(ref, Theirs))}
        for member, _ in members:
            if member.startswith(folder) and not any(member.startswith(n + "/")
                                                     for n in nested):
                landing = child(home, member[len(folder):].replace("/", "\\"))
                above = landing
                while key(above) not in ours and key(above) != key(home):
                    above = parent(above)
                if key(above) in ours:
                    self.overlay[key(landing)] = (landing, Dropped(member))
                else:
                    self.put(landing, Dropped(member))
        for empty in self.folders:
            if (empty + "/").startswith(folder) and len(empty) + 1 > len(folder):
                self.mkdirs(child(home, empty[len(folder):].replace("/", "\\")))
        self.bat = child(home, bat[len(folder):].replace("/", "\\"))
        self.home = home

    def game_path(self, inner: str) -> bool:
        """
        whether a path from the .bat's own folder names one of the game's
        files or folders that the drop has nothing of its own under:
        FFXiiiLauncher.exe, white_data\\prog, the way a .bat made to be run
        from inside the game's folder looks for them
        """
        if self.known is None:
            low = key(self.root)
            self.known = {k[len(low) + 1:] for k in self.nodes if k.startswith(low + "\\")}
        bits = inner.replace("/", "\\").strip("\\").lower().split("\\")
        if not bits or bits[0] not in self.known:
            return False
        folder = self.dropped[1].rsplit("/", 1)[0].lower() + "/" if "/" in self.dropped[1] else ""
        mine = folder + bits[0]
        return not any(m.lower() == mine or m.lower().startswith(mine + "/")
                       for m, _ in self.dropped[0])

    def real(self, path: str) -> Path:
        """where a path of the play's is on the stage, for a program of theirs"""
        path = norm(path)
        k = key(path)
        for root_key, root, real_root in self.mounts:
            if k == root_key or k.startswith(root_key + "\\"):
                rest = path[len(root):].strip("\\")
                return real_root.joinpath(*_plain(rest.split("\\"))) if rest else real_root
        parts = path.split("\\")
        return self.folder.joinpath("x", parts[0].rstrip(":"), *_plain(parts[1:]))

    def fresh(self) -> None:
        """
        the copies programs of theirs were given, cleared: what one play's
        programs left isn't the next one's
        """
        from patcher.fileops import clear_tree

        for real in [*(real for _, _, real in self.mounts), self.folder / "x"]:
            clear_tree(real)

    def reaches(self, raw: str) -> bool:
        """whether a path from the root, on the drive the stage is on, is in the real game"""
        drive = PureWindowsPath(str(self.folder)).drive
        if not drive:
            return False
        real = PureWindowsPath(ntpath.normpath(drive + raw.replace("/", "\\")))
        return real.is_relative_to(PureWindowsPath(str(self.install_dir)))

    def game_rel(self, path: str):
        """a play path's place in the game's folder, wherever it's mounted, or None outside it"""
        k = key(path)
        for root in (self.root, *self.aliases):
            low = key(root)
            if k == low:
                return ""
            if k.startswith(low + "\\"):
                return norm(path)[len(root) + 1:].replace("\\", "/")
        return None

    def roots(self) -> list:
        return [self.root, *self.aliases]

    def game_at(self, path: str):
        """
        where the game would be for a path a script looks for it at, outside
        where it's mounted: the path less the end of it that's a file or
        folder of the game's, white_data\\prog\\win\\bin\\ffxiiiimg.exe say, or a
        folder named the way the game's own is. None for anything else
        """
        full = norm(path)
        # a folder named for the game in My Games or AppData holds its saves
        # and settings, never the game
        if self.game_rel(full) is not None or self.inside_mod(full) is not None \
                or re.search(r"\\(appdata|my games|saved games)(\\|$)", key(full)):
            return None
        self.game_path("")
        parts = full.split("\\")
        for n in range(1, len(parts)):
            tail = "\\".join(parts[n:]).lower()
            if tail not in self.known:
                continue
            base = "\\".join(parts[:n])
            if len(base) > 3 and self._holds_game(base, tail):
                return base
        return full if self.game_folder(leaf(full)) and len(full) > 3 else None

    def _holds_game(self, base: str, tail: str) -> bool:
        """
        whether `base` is the game for a script that looks there for `tail`,
        a file or folder of the game's: a folder named the way the game's own
        is, anywhere, or one of its exes. In the user's own folders that's
        all, since a mod library or a backup laid out like the game isn't it.
        Elsewhere, a folder named for the game with a word more, FINAL
        FANTASY XIII (Steam), or one holding a file of the game's a few
        folders down
        """
        from patcher.mods.game_files import GAME_EXES

        if self.game_folder(leaf(base)) or leaf(tail) in GAME_EXES:
            return True
        if re.match(r"^[a-z]:\\users\\", key(base)) or NOT_THE_GAME.search(leaf(base)):
            return False
        if "\\" not in tail and tail != self.game.data:
            # readme.txt anywhere isn't the game
            return False
        node = self.nodes.get(key(self.root + "\\" + tail))
        return self.game_named(leaf(base)) or "\\" in tail and node is not None \
            and node[1] is not None

    def game_folder(self, name: str) -> bool:
        """whether a folder's name is the one this game's own goes by"""
        bare = re.sub(r"[^a-z0-9]", "", name.lower())
        mine = re.sub(r"[^a-z0-9]", "", self.install_dir.name.lower())
        return bool(bare) and (bare == mine or bare in GAME_FOLDERS.get(self.game.data, ()))

    def game_named(self, name: str) -> bool:
        """
        whether a folder's name is the game's with more after it: Final
        Fantasy XIII Complete, FFXIII PC. Never XIII-2's for XIII, whose
        name goes on with a digit
        """
        bare = re.sub(r"[^a-z0-9]", "", name.lower())
        mine = re.sub(r"[^a-z0-9]", "", self.install_dir.name.lower())
        return any(bare.startswith(own) and not bare[len(own):][:1].isdigit()
                   for own in {mine, *GAME_FOLDERS.get(self.game.data, ())} if own)

    def inside_mod(self, path: str):
        """a path's place in the drop's folder, "" for the folder, or None outside it"""
        k, mod = key(path), key(self.mod)
        if k == mod:
            return ""
        if k.startswith(mod + "\\"):
            return norm(path)[len(self.mod) + 1:]
        return None

    def find_in_drop(self, inner: str):
        """
        ("file" or "dir", the member) for a file or folder its .bat wants in its
        own folder that the drop has somewhere else, by the longest end of its
        path that one, and only one, member ends with: data\\x.win32.wmp where
        the zip had Outfit\\data\\x.win32.wmp. None when nothing or several match
        """
        found = self.drop_matches(inner, with_kind=True)
        if len(found) == 1:
            return found[0]
        if len(found) > 1 and all(kind == "file" for kind, _ in found) and \
                len({self.sizes[m] for _, m in found}) == 1 and \
                len({self.digest(Dropped(m)) for _, m in found}) == 1:
            # the same file twice over is still the one file
            return found[0]
        return None

    def drop_matches(self, inner: str, with_kind: bool = False) -> list:
        """the drop's members with the longest end of `inner`'s path any of them has"""
        want = inner.replace("\\", "/").strip("/").lower().split("/")
        files = {m.lower(): m for m in self.sizes}
        folders = {}
        for m in self.sizes:
            bits = m.split("/")
            for n in range(1, len(bits)):
                folders.setdefault("/".join(bits[:n]).lower(), "/".join(bits[:n]))
        for n in range(len(want)):
            tail = "/".join(want[n:])
            hits = [(kind, spelled) for kind, pool in (("file", files), ("dir", folders))
                    for low, spelled in pool.items()
                    if (low == tail or low.endswith("/" + tail)) and low != "/".join(want)]
            if hits:
                return hits if with_kind else [spelled for _, spelled in hits]
        return []

    def alias(self, base: str) -> None:
        """the game mounted at `base` as well, every play from now on finding it there too"""
        base = norm(base)
        low = key(self.root)
        for k, (spelled, ref) in list(self.nodes.items()):
            if k == low or k.startswith(low + "\\"):
                there = base + spelled[len(self.root):]
                if ref is None:
                    self.mkdirs(there)
                else:
                    self.put(there, ref)
        self.aliases.append(base)
        self.mounts = sorted(self.mounts + [(key(base), base,
                                             self.folder / f"a{len(self.aliases)}")],
                             key=lambda m: -len(m[0]))

    def relocate(self, path: str, kind: str, member: str) -> None:
        """a file or folder of the drop where its .bat looks for it, as well as where it is"""
        if kind == "file":
            self.put(path, Dropped(member))
            return
        self.mkdirs(path)
        for m in self.sizes:
            if m.startswith(member + "/"):
                self.put(child(path, m[len(member) + 1:].replace("/", "\\")), Dropped(m))

    def borrow(self, path: str, ran: bool = False):
        """
        (where in its folder it goes, where it's coming from, whose it is) for a
        file of its download's another download of theirs has instead, or None
        """
        inner = self.inside_mod(path)
        kit = self._models_kit(inner)
        if kit is not None:
            # the whole kit at once, so it's one change to the PC rather than
            # one a file, each played again
            return parent(norm(path)), kit, MODELS_KIT_NAME
        if not inner or self.lenders is None:
            return None
        found = self.lenders.find(inner.split("\\"), ran, self.mentions)
        if found is None:
            return None
        matched, origin, whose = found
        spot = norm(path)
        # the folder that matched goes in whole, a SupportFiles\ and the dlls
        # beside its exe with it, at the place in ours it was looked for
        for _ in range(matched - 1):
            spot = parent(spot)
        return spot, origin, whose

    def _models_kit(self, inner):
        """{"": bytes} for one of HD Models Plus's support files, when that's what's missing"""
        bits = inner.lower().split("\\") if inner else []
        if self.game.data != "white_data" or len(bits) < 2 or bits[-2] != "supportfiles":
            return None
        name = bits[-1] if "." in bits[-1] else bits[-1] + ".exe"
        if name not in MODELS_KIT:
            return None
        return {spelled: data if data is not None else models_setup(self.root)
                for spelled, data in MODELS_KIT_FILES.items()}

    def mentions(self, folder: str) -> list:
        """
        the paths every .bat in the drop names under a folder called `folder`,
        the part after it, split. %vars% and wildcards aren't a path to check
        """
        out = []
        ours = {m.replace("\\", "/").lower() for m in self.sizes}
        # digits can sit in front of it: %~dp0SupportFiles\x.exe
        pattern = re.compile(r"(?<![A-Za-z_.-])" + re.escape(folder) + r"\\", re.I)
        for text in self.texts.values():
            for line in text.splitlines():
                for hit in pattern.finditer(line):
                    quoted = line.count('"', 0, hit.start()) % 2 == 1
                    rest = line[hit.end():]
                    rest = rest.split('"', 1)[0] if quoted else re.split(r'[\s"&|<>]', rest)[0]
                    rest = rest.strip().rstrip(".,;")
                    if not rest or re.search(r"[%*?!]", rest):
                        continue
                    bits = [b for b in rest.split("\\") if b]
                    tail = "/".join([folder, *bits]).lower()
                    # what the add-on brings itself is no lender's to have
                    if bits and bits not in out and not any(
                            m == tail or m.endswith("/" + tail) for m in ours):
                        out.append(bits)
        return out

    def lend(self, spot: str, origin) -> bool:
        """another download's file or folder at `spot`, over nothing the drop has itself"""
        if isinstance(origin, dict):
            for rest, data in origin.items():
                target = child(spot, rest) if rest else spot
                if key(target) not in self.nodes:
                    self.put(target, self.keep(data))
            return True
        pending, size = [], 0
        if isinstance(origin, tuple) and not zipfile.is_zipfile(origin[0]):
            # a .7z or .rar the drawer put a mod in from: the one folder
            # unpacked into this attempt's own, gone with it once it's done
            archive, top = origin
            names = self.lenders._listing(archive)
            # spelled the way it's stored, off a file in it when the folder
            # itself isn't listed, since tar matches names case and all
            spelled = names.get(top) or next((n[:len(top)] for low, n in names.items()
                                              if low.startswith(top + "/")), top)
            into = Path(tempfile.mkdtemp(prefix="lent-", dir=self.folder))
            if not _unseal(archive, spelled, into):
                return False
            return self.lend(spot, _on_disk(into, spelled.split("/")))
        if isinstance(origin, tuple):
            archive, top = origin
            with zipfile.ZipFile(archive) as z:
                for info in z.infolist():
                    low = info.filename.replace("\\", "/").lower()
                    if info.is_dir() or not (low == top or low.startswith(top + "/")):
                        continue
                    rest = info.filename.replace("\\", "/")[len(top) + 1:]
                    pending.append((child(spot, rest.replace("/", "\\")) if rest else spot, info))
                    size += info.file_size
                if len(pending) > LEND_FILES or size > LEND_BYTES:
                    return False
                for target, info in pending:
                    if key(target) not in self.nodes:
                        with z.open(info) as handle:
                            self.put(target, self.keep_from(handle.read))
            return bool(pending)
        files = [origin] if origin.is_file() else []
        for real in () if files else origin.rglob("*"):
            if real.is_file() and not real.is_symlink():
                files.append(real)
                size += real.stat().st_size
                if len(files) > LEND_FILES or size > LEND_BYTES:
                    return False
        for real in files:
            rest = real.relative_to(origin).as_posix() if real != origin else ""
            target = child(spot, rest.replace("/", "\\")) if rest else spot
            if key(target) not in self.nodes:
                self.put(target, self.keep_file(real))
        return bool(files)

    def entries(self, index: Theirs) -> dict:
        from patcher.mods import game_files

        found = self.indexes.get(index)
        if found is None:
            found = game_files.entries(self.install_dir / index.rel)
            self.indexes[index] = found
        return found

    def read(self, ref) -> bytes:
        if isinstance(ref, Inline):
            return ref.data
        if isinstance(ref, Theirs):
            return (self.install_dir / ref.rel).read_bytes()
        if isinstance(ref, (Dropped, Nested)):
            return self.source.read(ref.member)
        if isinstance(ref, Kept):
            return (self.store / ref.digest).read_bytes()
        if isinstance(ref, Record):
            from patcher.mods.hd_zones import read_record

            entry = self.entries(ref.index).get(ref.path)
            if entry is None or entry.is_stub:
                return b""
            return read_record(self.install_dir / ref.archive.rel, entry)
        if isinstance(ref, Patched):
            return self.read(self._decoded(ref))
        if isinstance(ref, Joined):
            return b"".join(self.read(part) for part in ref.parts)
        if isinstance(ref, Broken):
            raise Opaque(ref.why)
        raise Opaque("the game's archives part way through a rebuild")

    def size(self, ref) -> int:
        if isinstance(ref, Inline):
            return len(ref.data)
        if isinstance(ref, Theirs):
            return (self.install_dir / ref.rel).stat().st_size
        if isinstance(ref, (Dropped, Nested)):
            return self.sizes.get(ref.member, 0)
        if isinstance(ref, Kept):
            return ref.size
        if isinstance(ref, Record):
            entry = self.entries(ref.index).get(ref.path)
            return 0 if entry is None else entry.uncompressed
        if isinstance(ref, Patched):
            # the patch says how big what it makes is, without making it
            from patcher.mods import deltas
            try:
                return deltas.size(self.read(ref.patch))
            except deltas.DeltaError:
                return 0
        if isinstance(ref, Joined):
            return sum(self.size(part) for part in ref.parts)
        if isinstance(ref, (Listing, Blob)):
            # roughly what it'd be: its own file's size, which is what an
            # installer's size checks look for
            return self.size(ref.base) if ref.base is not None else 0
        return 0

    def same(self, ra, rb) -> bool:
        """
        whether two references hold the same bytes, a movie or an archive
        compared a megabyte at a time rather than held whole, and each pair
        only once: a mod dropped again has its files compared with the
        game's in every look-ahead and again laying it out
        """
        if ra == rb:
            return True
        try:
            if (ra, rb) not in self.compared:
                self.compared[(ra, rb)] = self._same(ra, rb)
            return self.compared[(ra, rb)]
        except TypeError:
            # an index's listing carries what it was read from, which can't be a key
            return self._same(ra, rb)

    def _same(self, ra, rb) -> bool:
        try:
            if self.size(ra) != self.size(rb):
                return False
            left, right = b"", b""
            theirs = self.chunks(rb)
            for chunk in self.chunks(ra):
                left += chunk
                while len(right) < len(left):
                    more = next(theirs, b"")
                    if not more:
                        return False
                    right += more
                if left[:len(right)] != right[:len(left)]:
                    return False
                cut = min(len(left), len(right))
                left, right = left[cut:], right[cut:]
            return not left and not right and next(theirs, b"") == b""
        except Opaque:
            return False

    def keep(self, data: bytes):
        if len(data) < INLINE:
            return Inline(data)
        digest = hashlib.sha256(data).hexdigest()
        target = self.store / digest
        if not target.exists():
            temp = target.with_suffix(".part")
            temp.write_bytes(data)
            temp.replace(target)
        return Kept(digest, len(data))

    def keep_from(self, read):
        """
        bytes off `read` into the store as they come, never held whole: a
        zip's member that inflates to gigabytes is a file here, not memory
        """
        head = b""
        while len(head) < INLINE:
            chunk = read(INLINE - len(head))
            if not chunk:
                return Inline(head)
            head += chunk
        summed, size = hashlib.sha256(head), len(head)
        handle, name = tempfile.mkstemp(suffix=".part", dir=self.store)
        part = Path(name)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(head)
                for chunk in iter(lambda: read(1 << 20), b""):
                    summed.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
            target = self.store / summed.hexdigest()
            if target.exists():
                part.unlink()
            else:
                part.replace(target)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        return Kept(summed.hexdigest(), size)

    def opened(self, ref):
        """a reference as a file to seek through, the caller's to close"""
        if isinstance(ref, Theirs):
            return (self.install_dir / ref.rel).open("rb")
        if isinstance(ref, Kept):
            return (self.store / ref.digest).open("rb")
        if isinstance(ref, (Dropped, Nested)) and self.sizes.get(ref.member, 0) >= INLINE:
            return self.source.opened(ref.member)
        if isinstance(ref, Joined) and self.size(ref) >= INLINE:
            # the parts end to end in a file that goes when it's closed
            joined = tempfile.TemporaryFile(dir=self.folder)
            for chunk in self.chunks(ref):
                joined.write(chunk)
            joined.seek(0)
            return joined
        return _io.BytesIO(self.read(ref))

    def head(self, ref, n: int = 8) -> bytes:
        """a reference's first bytes, never the rest of it read or copied anywhere"""
        if isinstance(ref, (Dropped, Nested)):
            return self.source.head(ref.member, n)
        if isinstance(ref, Joined):
            out = b""
            for part in ref.parts:
                out += self.head(part, n - len(out))
                if len(out) >= n:
                    break
            return out
        if isinstance(ref, (Theirs, Kept)):
            with self.opened(ref) as handle:
                return handle.read(n)
        return self.read(ref)[:n]

    def keep_file(self, path: Path):
        """a file a program of theirs left, into the store as it is"""
        size = path.stat().st_size
        if size < INLINE:
            return Inline(path.read_bytes())
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        target = self.store / digest.hexdigest()
        if not target.exists():
            shutil.copyfile(path, target.with_suffix(".part"))
            target.with_suffix(".part").replace(target)
        return Kept(digest.hexdigest(), size)

    def chunks(self, ref):
        """a reference's bytes a megabyte at a time"""
        if isinstance(ref, Joined):
            for part in ref.parts:
                yield from self.chunks(part)
            return
        if isinstance(ref, (Theirs, Kept)) or (isinstance(ref, (Dropped, Nested))
                                               and self.sizes.get(ref.member, 0) >= INLINE):
            opened = (self.install_dir / ref.rel).open("rb") if isinstance(ref, Theirs) else \
                (self.store / ref.digest).open("rb") if isinstance(ref, Kept) else \
                self.source.opened(ref.member)
            with opened as handle:
                yield from iter(lambda: handle.read(1 << 20), b"")
            return
        yield self.read(ref)

    def lay(self, ref, target: Path) -> None:
        """a reference's bytes into a real file, streamed when it's big"""
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(ref, Theirs):
            shutil.copyfile(self.install_dir / ref.rel, target)
        elif isinstance(ref, Kept):
            shutil.copyfile(self.store / ref.digest, target)
        elif isinstance(ref, (Dropped, Nested)) and self.sizes.get(ref.member, 0) >= INLINE:
            with self.source.opened(ref.member) as inside, target.open("wb") as out:
                shutil.copyfileobj(inside, out, 1 << 20)
        elif isinstance(ref, Joined):
            try:
                with target.open("wb") as out:
                    for chunk in self.chunks(ref):
                        out.write(chunk)
            except BaseException:
                # a part that can't be read leaves no half of it behind
                target.unlink(missing_ok=True)
                raise
        else:
            target.write_bytes(self.read(ref))

    def _decoded(self, ref: Patched):
        """an xdelta's result, decoded once onto the stage"""
        from patcher.mods import deltas

        done = self.decoded.get(ref)
        if done is not None:
            return done
        if isinstance(ref.base, (Listing, Blob)):
            raise Opaque("an xdelta over an archive part way through a rebuild")
        source = self.install_dir / ref.base.rel if isinstance(ref.base, Theirs) else \
            self.store / ref.base.digest if isinstance(ref.base, Kept) else None
        if source is None:
            source = self.folder / f"xdelta-source-{len(self.decoded)}"
            self.lay(ref.base, source)
        out_path = self.folder / f"xdelta-{len(self.decoded)}"
        try:
            with out_path.open("w+b") as out:
                deltas.whole(self.read(ref.patch), source, out)
        except deltas.DeltaError as e:
            raise SandboxError(f"its xdelta patch doesn't fit the file it's for ({e})") from None
        done = self.keep_file(out_path)
        out_path.unlink()
        self.decoded[ref] = done
        return done

    def listing(self, ref):
        """what an index reference lists, as a Listing over the game's, or None"""
        if isinstance(ref, Listing):
            return ref
        if isinstance(ref, Theirs) and ref.rel.lower() in self.pairs:
            return Listing(self.pairs[ref.rel.lower()][0])
        return None

    def blob(self, ref):
        if isinstance(ref, Blob):
            return ref
        if isinstance(ref, Theirs) and any(a == ref for _, a, _ in self.pairs.values()):
            return Blob(ref)
        return None

    def archive_of(self, index: Theirs) -> Theirs:
        return self.pairs[index.rel.lower()][1]

    def put_as(self, ref, before, spot: str, over=None) -> str:
        """
        what `ref` written where `before` was (None for a new file) is:
        "original" for what the drawer's record says the mod went in over
        (`over`, its digest and size) put back or a file it added taken away,
        a backup of the game's file put back, or a file out of the drop's
        folder of originals that's beside the file it's the original of or
        has the bytes there already; "same" for a file of the mod's that's
        there already, which is the mod in already; "deletes" for one of the
        game's files gone, which can't go in; "" for a copy it made of one of
        the game's files or an unpack left where it was made; and "mod" for
        anything else, a swap included
        """
        if over is not None:
            # dropped again, the uninstall a .bat offers is told by what it
            # does rather than what it's called: a menu's, a typed one's, or
            # one it runs without asking on a game that has the mod
            digest, size = over
            if ref is None and before is not None and not digest:
                return "original"
            if ref is not None and digest and self.digest(ref, size) == digest:
                return "original"
        if ref is None:
            return "" if before is None else "deletes"
        if isinstance(ref, Theirs):
            if before is None or before == ref:
                return ""
            return "original" if _backup_of(ref.rel, spot) else "mod"
        if isinstance(ref, Record):
            if before is None:
                return "" if _record_of(spot, {ref.path.lower()}) else "mod"
            return "" if before == ref else "mod"
        dropped = isinstance(ref, (Dropped, Nested))
        if dropped and ref.member in self.kept:
            return "original"
        if before is not None and self.same(ref, before):
            # on a game the mod's in, the bytes there are its, whatever its
            # folder's called: Restore Cut Content, Original Soundtrack
            return "original" if dropped and not self.in_game and _keeps(
                ref.member, self.bat_folder, KEEPS_SAME) else "same"
        return "mod"

    def digest(self, ref, size=None) -> str:
        """a reference's sha256, read once, or "" when it isn't `size` bytes and so can't match"""
        try:
            if size is not None and self.size(ref) != size:
                return ""
            if ref not in self.digests:
                summed = hashlib.sha256()
                for chunk in self.chunks(ref):
                    summed.update(chunk)
                self.digests[ref] = summed.hexdigest()
            return self.digests[ref]
        except (Opaque, TypeError):
            return ""


# pieces on the end of a file before the small ones are gathered into one,
# so a log written to thousands of times is a short list, not a long one
# copied each time
APPENDS = 64


def _gathered(stage: "Stage", parts: list) -> list:
    """a file's pieces with each run of small ones kept as one"""
    out, run = [], []
    for part in parts:
        if isinstance(part, Inline):
            run.append(part.data)
            continue
        if run:
            out.append(stage.keep(b"".join(run)))
            run = []
        out.append(part)
    if run:
        out.append(stage.keep(b"".join(run)))
    return out


def _plain(parts: list) -> list:
    """
    a play path's parts as names Windows takes as names: C: in the middle of
    one would start a stage path over on that drive, and the rest of
    <>:"|?* has no place in a name there
    """
    return [re.sub(r'[<>:"|?*]', "_", p) for p in parts if p]


def _safe(name: str) -> str:
    """
    the drop's name as a folder to play it from. Without spaces, brackets or
    &: a .bat with an unquoted %~dp0 breaks on those, and only on the PC of
    whoever unzipped it somewhere with one in
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "", name).strip(".")[:40]


def _home(text: str, stage: Stage):
    """
    for a .bat made to be run from inside the game's folder, how many folders
    up from its own the game is (see Stage.home_for): one that reaches
    white_data\\ and the rest by relative paths alone, with nothing in it to
    find the game by. None for one that runs from its own folder, the usual
    """
    data = stage.game.data
    if not text or not data:
        return None
    lowered = text.lower()
    relative = re.search(r'(?<![%!\w\\:.])((?:\.\.\\)*)' + data + r'\\', lowered)
    finding = re.search(r'[%!][\w~$:]*[%!]\\?' + data + r'|set\s+/p|steamapps|reg\s+query|'
                        r'\bchoice\b|_dir\.txt|getpath', lowered)
    # ..\white_data\ is the same .bat unzipped a folder down, into one of its own
    return relative.group(1).count("..") if relative and not finding else None


class World:
    """
    one play of the script: the tree as it's been left so far, and the
    questions it's asked. `dry` plays the programs of theirs without running
    them, which is how the questions and the menus are found before anything
    slow happens
    """

    def __init__(self, stage: Stage, answers, dry: bool, cancel=None, on_line=None):
        self.stage = stage
        self.nodes = dict(stage.nodes)
        self.kids = {k: dict(v) for k, v in stage.kids.items()}
        # bumped on every change to the tree, and every path it's ever
        # changed, for batch.py to tell a wait loop from one that's getting
        # somewhere: a log written to again is the one path
        self.changed, self.touched = 0, set()
        for path, ref in stage.overlay.values():
            self.set(path, ref)
        self.answers = answers
        self.dry = dry
        self.cancel, self.on_line = cancel, on_line
        self.ran, self.left, self.asked = [], [], []
        # the registry as the play's reg adds and deletes left it: never this
        # PC's, but a script that reads back what it set finds it, and one
        # that checks a key it deleted is gone finds that. `unset` is values
        # deleted out of a key that's still there
        self.written, self.unset = {}, {}
        # (program, exit code, the last few things it said) for each that failed
        self.failed = []
        self.made = {}
        self.spent = 0.0
        # programs of theirs a look-ahead didn't run, whose work it can't see
        self.unseen = 0
        # folders ff13tool unpacked an archive into, which are the game's
        # archive and stay a description unless a program's told to look there
        self.unpacked = set()
        self.until = time.monotonic() + GLANCE
        self.began = time.monotonic()
        # why the answers ran out, when they did
        self.ended = ""
        # the .txts FFXIIIwdbLoad put in, in order: edits to a table's fields
        # rather than the table, so a Remove under another can replay the rest
        self.wdb = []
        # a reason one of our own stand-ins gave up with, said as it is
        self.refused = None

    @property
    def fresh(self) -> int:
        return len(self.touched)

    @property
    def admin(self) -> bool:
        return self.stage.machine.admin

    @property
    def windows(self) -> str:
        return self.stage.machine.windows

    def in_game(self, path: str) -> bool:
        return self.stage.game_rel(path) is not None

    def kind(self, path: str):
        node = self.nodes.get(key(path))
        if node is None:
            return None
        return "dir" if node[1] is None else "file"

    def listdir(self, folder: str) -> list:
        names = self.kids.get(key(folder), {})
        base = norm(folder)
        return [(name, self.nodes[key(child(base, name))][1] is None) for name in names.values()
                if key(child(base, name)) in self.nodes]

    def ref(self, path: str):
        node = self.nodes.get(key(path))
        return None if node is None else node[1]

    def read(self, path: str) -> bytes:
        return self.stage.read(self.ref(path))

    def size(self, path: str) -> int:
        return self.stage.size(self.ref(path))

    def same(self, a: str, b: str) -> bool:
        return self.stage.same(self.ref(a), self.ref(b))

    def spell(self, path: str) -> str:
        node = self.nodes.get(key(path))
        return node[0] if node is not None else norm(path)

    def set(self, path: str, ref) -> None:
        self.changed += 1
        path = norm(path)
        k = key(path)
        self.touched.add(k)
        if key(parent(path)) not in self.nodes:
            # an unpack or an extract makes the folders a file lands in
            self.mkdirs(parent(path))
        old = self.nodes.get(k)
        spelled = old[0] if old is not None else path
        self.nodes[k] = (spelled, ref)
        self.kids.setdefault(key(parent(path)), {})[leaf(spelled).lower()] = leaf(spelled)

    def write(self, path: str, data: bytes, append: bool = False) -> None:
        if append and self.kind(path) == "file":
            # a piece on the end, never the whole file kept again: a log a
            # loop adds a line to each pass would fill the disk with copies
            old = self.ref(path)
            parts = list(old.parts) if isinstance(old, Joined) else [old]
            parts.append(self.stage.keep(data))
            if len(parts) > APPENDS:
                parts = _gathered(self.stage, parts)
            self.set(path, Joined(tuple(parts)))
            return
        self.set(path, self.stage.keep(data))

    def copy(self, source: str, target: str) -> None:
        self.set(target, self.ref(source))

    def join(self, sources: list, target: str) -> None:
        # flat, so copy /b whole + piece whole in a loop is a list, not a tower
        parts = []
        for source in sources:
            ref = self.ref(source)
            parts += ref.parts if isinstance(ref, Joined) else [ref]
        self.set(target, Joined(tuple(parts)))

    def move(self, source: str, target: str) -> None:
        source, target = norm(source), norm(target)
        if self.kind(source) == "file":
            ref = self.ref(source)
            self.remove(source)
            self.set(target, ref)
            return
        moving = [k for k in self.nodes if k == key(source) or k.startswith(key(source) + "\\")]
        for k in sorted(moving, key=len):
            spelled, ref = self.nodes[k]
            landing = target + spelled[len(source):]
            if ref is None:
                self.mkdir(landing)
            else:
                self.set(landing, ref)
        for k in sorted(moving, key=len, reverse=True):
            self.nodes.pop(k, None)
            self.kids.pop(k, None)
        self.kids.get(key(parent(source)), {}).pop(leaf(source).lower(), None)

    def remove(self, path: str) -> None:
        self.changed += 1
        k = key(path)
        self.touched.add(k)
        if k in self.nodes and self.nodes[k][1] is not None:
            del self.nodes[k]
            self.kids.get(key(parent(path)), {}).pop(leaf(path).lower(), None)

    def mkdirs(self, path: str) -> None:
        chain = []
        while key(path) not in self.nodes and parent(path) != path:
            chain.append(path)
            path = parent(path)
        for each in reversed(chain):
            self.mkdir(each)

    def mkdir(self, path: str) -> None:
        self.changed += 1
        path = norm(path)
        self.touched.add(key(path))
        if key(path) not in self.nodes:
            self.nodes[key(path)] = (path, None)
            self.kids.setdefault(key(path), {})
            self.kids.setdefault(key(parent(path)), {})[leaf(path).lower()] = leaf(path)

    def rmdir(self, path: str) -> None:
        self.changed += 1
        k = key(path)
        self.touched.add(k)
        if k in self.nodes and self.nodes[k][1] is None:
            del self.nodes[k]
            self.kids.pop(k, None)
            self.kids.get(key(parent(path)), {}).pop(leaf(path).lower(), None)

    def script(self, path: str) -> batch.Script:
        return batch.Script.read(self.spell(path), self.read(path))

    def answer(self, ask: batch.Ask) -> str:
        said = self.answers(ask, self)
        self.asked.append((ask, said))
        return said

    def check(self, steps: int) -> None:
        if self.cancel is not None and self.cancel.is_set():
            from patcher.mods.hd_runner import Cancelled
            raise Cancelled("stopped while its installer played")
        if self.dry and time.monotonic() > self.until:
            raise batch.Endless(f"it ran over {GLANCE} seconds without finishing, so it's "
                                "caught in a loop")
        # played for real, its programs take what they take, and cmd's own
        # going round gets a clock of its own
        if not self.dry and time.monotonic() - self.began - self.spent > LOOPING:
            raise batch.Endless(f"it went round its own lines for over {LOOPING // 60} minutes "
                                "without finishing, so it's caught in a loop")

    def waited(self, began: float) -> None:
        """
        time spent on its work rather than going round its lines, a big zip
        inflated say, which neither clock counts against it
        """
        took = time.monotonic() - began
        self.spent += took
        self.until += took

    # the programs it starts

    def program(self, path: str, text: str, cwd: str, io) -> int:
        name = leaf(path).lower()
        argv = batch.split_args(text)
        self.ran.append((leaf(path), text))
        real = self.stage.hosts.get(key(path))
        if real is not None:
            return self.hosted(path, real, argv, text, cwd, io)
        played = _PLAYED.get(name) or next((how for pattern, how in _PLAYED_LIKE
                                            if re.search(pattern, name)), None)
        if played is not None:
            return played(self, argv, cwd, io, path, text)
        from patcher.mods.game_files import GAME_EXES, LARGE_ADDRESS
        if name in GAME_EXES or name == "steam.exe":
            # starting the game or Steam, which is for later
            return 0
        if LARGE_ADDRESS.search(name):
            return _laa(self, argv, cwd, io, path, text)
        if self.dry:
            return self.unrun()
        return self.staged(path, text, cwd, io)

    def unrun(self, code: int = 0) -> int:
        self.unseen += 1
        return code

    def hosted(self, path: str, real: str, argv: list, text: str, cwd: str, io) -> int:
        """
        one of this PC's own runtimes, Python say, running a script of theirs
        the way a program of theirs runs: on the stage, over copies. Asked
        only its version, it says one without being started
        """
        runtime = diagnose.NAMES.get(leaf(path).lower().removesuffix(".exe"), "")
        if argv and all(a.lower() in ("--version", "-v", "-version", "/v") for a in argv):
            io.out.write(diagnose.RUNTIMES[runtime][3] + "\n" if runtime else "\n")
            return 0
        if self.dry:
            return self.unrun()
        if leaf(path).lower() == "py.exe":
            # the py launcher's -3 or -3.12, which python.exe itself doesn't take
            text = re.sub(r"^\s*-[23](\.\d+)?(-(32|64))?(\s+|$)", "", text)
        return self.staged(path, text, cwd, io, host=real)

    def builtin(self, name: str):
        """ours, for a program of theirs cmd would find by that name wherever it's looked for"""
        return WDBLOAD if re.sub(r"\.(bat|cmd)$", "", leaf(name).lower()) == "ffxiiiwdbload" \
            else None

    def tool(self, name: str, text: str, cwd: str, io):
        how = _TOOLS.get(name)
        if how is None:
            return None
        return how(self, batch.split_args(text), text, cwd, io)

    # running one for real

    def staged(self, path: str, text: str, cwd: str, io, host: str | None = None,
               beside=()) -> int:
        """
        a program of theirs, run in the stage over a copy of what it can see:
        its own folder, the one it runs in, and whatever its arguments name.
        What it changes there comes back in. Never the game's own files, which
        it only ever gets copies of, and only of the ones named. `host` is the
        real program for one of this PC's runtimes, which runs where it is
        """
        from patcher.download import free_space

        if self.spent > TOOLS_LIMIT:
            raise SandboxError("its tools have run over an hour between them without "
                               "finishing")
        # its copies laid and read back count as its time, not cmd's going round
        began = time.monotonic()
        roots = [cwd] if host else [cwd, parent(path)]
        named, making = ([] if host else [path]) + list(beside), []
        for full in self._mentioned(text, cwd):
            if self.kind(full) == "dir":
                roots.append(full)
            elif self.kind(full) == "file":
                named.append(full)
            else:
                # where it's told to write something that isn't there yet,
                # however many new folders down: read back, without what's
                # beside it going over
                above = parent(full)
                while self.kind(above) is None and parent(above) != above:
                    above = parent(above)
                if self.kind(above) == "dir":
                    making.append(full)
        given = [r for r in roots if self.kind(r) == "dir"]
        roots = _outermost(given)
        laying = []
        for root in roots:
            if self.stage.game_rel(root) is not None:
                # the game's folder goes over by name, past the few small files
                # sitting in the folder it runs in: its own exe and dlls, when
                # its .bat was made to be run from in there
                laying += [child(root, name) for name, is_dir in self.listdir(root)
                           if not is_dir and not self._big(self.ref(child(root, name)))
                           and not isinstance(self.ref(child(root, name)),
                                              (Listing, Blob, Broken))]
                continue
            # nor does the game's folder under a folder above it, C:\ say
            for each in self._files_under(root, skip=self.unpacked
                                          | {key(r) for r in self.stage.roots()}):
                # an archive of the game's, copied or part way through a
                # rebuild, is gigabytes or only a description here, and goes
                # over when it's named or not at all
                ref = self.ref(each)
                if isinstance(ref, (Listing, Blob, Broken)) or self._big(ref):
                    continue
                laying.append(each)
        # a whole archive's unpack stays off the stage but for the folder of
        # it the tool's handed or runs in, white_scru\chr\pc\c004 say, inside
        # the game's folder or not
        def in_unpack(path: str) -> bool:
            return any(key(path) == k or key(path).startswith(k + "\\") for k in self.unpacked)

        # and the ones named like one it's handed in there: a model tool given
        # c004.win32.trb reads the c004.win32.imgb next to it
        for g in given:
            if in_unpack(g):
                laying += self._lay_from(self._files_under(g, skip=self.unpacked), laying)
        for f in named[0 if host else 1:]:
            if in_unpack(f):
                stem = leaf(f).split(".", 1)[0].lower()
                laying += self._lay_from([child(parent(f), name) for name, is_dir
                                          in self.listdir(parent(f)) if not is_dir
                                          and name.split(".", 1)[0].lower() == stem], laying)
        # and its own TEMP, which is the play's, with what the script left
        # there, wherever it's run from
        watched = list(roots)
        if not any(_under(TEMP, r) or key(TEMP) == key(r) for r in roots):
            watched.append(TEMP)
            laying += [p for p in self._files_under(TEMP, skip=self.unpacked)
                       if not any(_under(p, r) for r in roots)
                       and not isinstance(self.ref(p), (Listing, Blob, Broken))
                       and not self._big(self.ref(p))]
        # but past a big file's worth of copies it may never look at, only
        # what it's handed, runs in or has already goes over
        inside = [g for g in given if _under(g, TEMP) or key(g) == key(TEMP)]
        handed = {key(p) for p in named}
        spare = [p for p in laying if _under(p, TEMP) and key(p) not in handed
                 and not any(_under(p, g) for g in inside)
                 and str(self.stage.real(p)) not in self.made]
        if sum(self._cost(p) for p in spare) > BIG:
            laying = [p for p in laying if p not in spare]
        laying += [p for p in named if all(key(p) != key(q) for q in laying)]
        need = sum(self._cost(p) for p in laying)
        if need and free_space(self.stage.folder) < need + (256 << 20):
            raise SandboxError(f"its {leaf(path)} needs {need / 1024 ** 3:.1f} GB of copies to "
                               "work on, more than the game's drive has free")
        for p in laying:
            self._lay(p)
        for root in watched:
            self._prune(root)
            self.stage.real(root).mkdir(parents=True, exist_ok=True)
        for p in making:
            self.stage.real(parent(p)).mkdir(parents=True, exist_ok=True)
        # there even when a root above it holds it, C:\ or the user's folder,
        # and so in a mount of its own
        temp = self.stage.real(TEMP)
        temp.mkdir(parents=True, exist_ok=True)
        exe = Path(host) if host else self.stage.real(path)
        if sys.platform != "win32" and not host:
            exe.chmod(0o755)
        if self.on_line is not None:
            self.on_line(f"> {leaf(path)} {text}")
        command, env = self._outward(text, cwd), dict(os.environ, TEMP=str(temp), TMP=str(temp))
        code, out, err = _spawn(exe, command, self.stage.real(cwd), io.stdin, self.cancel, env=env)
        if code and io.stdin is None:
            typed = _typed(out or "", err or "", self._left_something(watched))
            if typed == "":
                # it finished and then waited for a key there's no keyboard to
                # press here: on the PC a key would have ended it cleanly
                code = 0
            elif typed is not None:
                # it stopped to ask something a person would answer one way:
                # asked again from where it started, answered this time
                self._reset(watched, laying)
                code, out, err = _spawn(exe, command, self.stage.real(cwd), typed, self.cancel,
                                        env=env)
                if code and _typed(out or "", err or "", self._left_something(watched)) == "":
                    code = 0
        # the stage's paths back as the play's, before the script or anyone
        # reading back what it printed sees them
        if out:
            io.out.write(self._inward(out))
        if err:
            io.err.write(self._inward(err))
        if code is None:
            raise SandboxError(f"its {leaf(path)} was still going after "
                               f"{TOOL_LIMIT // 60} minutes, so it was stopped")
        if code:
            said = [self._inward(line.strip()) for line in f"{out or ''}\n{err or ''}".splitlines()
                    if line.strip()]
            self.failed.append((leaf(path), code, said[-12:], path))
        for root in watched:
            self._ingest(root)
        for p in named + making:
            if any(_under(p, r) for r in watched):
                continue
            if self.stage.real(p).is_dir():
                self.mkdirs(p)
                self._ingest(p)
            else:
                self._ingest_file(p)
        self.spent += time.monotonic() - began
        return code

    def _left_something(self, watched: list) -> bool:
        """
        whether a program left a file with anything in it on the stage: one
        that's new, or one laid there for it that it changed. What was laid
        has a fresh time of its own, so it's told by the stamp it was laid with
        """
        for root in watched:
            for real_root in self._reals(root):
                for folder, _, files in os.walk(real_root):
                    for name in files:
                        real = Path(folder) / name
                        stamp = _stamp(real)
                        known = self.made.get(str(real))
                        if stamp is not None and stamp[0] and (known is None or known[1] != stamp):
                            return True
        return False

    def _reset(self, watched: list, laying: list) -> None:
        """the stage as it was laid before a program of theirs ran, for running it again"""
        for root in watched:
            for real_root in self._reals(root):
                for real in list(real_root.rglob("*")) if real_root.is_dir() else ():
                    if real.is_file() and str(real) not in self.made:
                        real.unlink()
        for p in laying:
            self._lay(p)

    def _lay_from(self, found: list, laying: list) -> list:
        """of `found`, what isn't going over already, big or part way through a rebuild"""
        have = {key(p) for p in laying}
        return [p for p in found if key(p) not in have and not self._big(self.ref(p))
                and not isinstance(self.ref(p), (Listing, Blob, Broken))]

    def _big(self, ref) -> bool:
        try:
            return self.stage.size(ref) > BIG
        except Exception:
            return True

    def _files_under(self, root: str, skip=()) -> list:
        """every file in a folder and the ones in it, past any of `skip` it's not inside"""
        out, inside, stack = [], key(root), [norm(root)]
        while stack:
            folder = stack.pop()
            for name in self.kids.get(key(folder), {}).values():
                path = child(folder, name)
                node = self.nodes.get(key(path))
                if node is None:
                    continue
                if node[1] is not None:
                    out.append(node[0])
                elif key(path) not in skip or inside.startswith(key(path)):
                    stack.append(path)
        return out

    def _cost(self, path: str) -> int:
        ref = self.ref(path)
        real = self.stage.real(path)
        if self.made.get(str(real), (None,))[0] == ref:
            return 0
        try:
            return self.stage.size(ref)
        except Exception:
            return 0

    def _lay(self, path: str) -> None:
        ref = self.ref(path)
        real = self.stage.real(path)
        known = self.made.get(str(real))
        if known is not None and known[0] == ref and _stamp(real) == known[1]:
            return
        try:
            self.stage.lay(ref, real)
        except Opaque as e:
            raise SandboxError(f"a program of theirs wants {leaf(path)}, and {e}") from None
        self.made[str(real)] = (ref, _stamp(real))

    def _reals(self, root: str) -> list:
        """
        the stage folders a folder of the play's is spread over: its own, and
        each mount inside it, the drop's under C:\\ say, which sits apart
        """
        return [self.stage.real(root)] + [real for _, mount, real in self.stage.mounts
                                          if _under(mount, root)]

    def _prune(self, root: str) -> None:
        """files on the stage the play has since deleted"""
        for real_root in self._reals(root):
            for real in list(real_root.rglob("*")) if real_root.is_dir() else ():
                if real.is_file() and str(real) in self.made:
                    path = self._back(real)
                    if path is None or self.kind(path) != "file":
                        real.unlink()
                        self.made.pop(str(real), None)

    def _back(self, real: Path):
        """the play's path for a file on the stage"""
        for _, root, real_root in self.stage.mounts:
            try:
                rest = real.relative_to(real_root)
            except ValueError:
                continue
            return norm(root + "\\" + "\\".join(rest.parts)) if rest.parts else root
        try:
            rest = real.relative_to(self.stage.folder / "x")
        except ValueError:
            return None
        return norm(rest.parts[0] + ":\\" + "\\".join(rest.parts[1:]))

    def _ingest(self, root: str) -> None:
        reals, seen = self._reals(root), set()
        for real in sorted(r for real_root in reals if real_root.is_dir()
                           for r in real_root.rglob("*")):
            path = self._back(real)
            if path is None:
                continue
            if real.is_dir():
                self.mkdir(path)
                continue
            seen.add(str(real))
            self._ingest_file(path, real)
        for real in [r for r in self.made
                     if r not in seen and any(Path(r).is_relative_to(d) for d in reals)]:
            # gone from the stage, so the program deleted it
            path = self._back(Path(real))
            if path is not None:
                self.remove(path)
            self.made.pop(real, None)

    def _ingest_file(self, path: str, real: Path | None = None) -> None:
        real = real or self.stage.real(path)
        if not real.is_file():
            if str(real) in self.made:
                self.remove(path)
                self.made.pop(str(real), None)
            return
        stamp = _stamp(real)
        known = self.made.get(str(real))
        if known is not None and known[1] == stamp:
            return
        ref = self.stage.keep_file(real)
        self.set(path, ref)
        self.made[str(real)] = (ref, stamp)

    def _paths(self, text: str, cwd: str) -> list:
        """
        (start, end, quoted before, quoted after, the play's path) for every
        path in a command line. One from the root counts when it starts in a
        folder the play has, \\Program Files (x86)\\..., or would be in the real
        game from the drive the stage is on, since \\nfullscreen=0 is no path
        """
        out = []
        for start, end, before, after, kind in _spans(text):
            raw = text[start:end].replace('"', "")
            full = join(cwd, raw)
            first = full[3:].split("\\")[0]
            if kind == "root" and not (first and self.kind(child(full[:2], first))
                                       or self.stage.reaches(raw)):
                continue
            out.append((start, end, before, after, full))
        return out

    def _mentioned(self, text: str, cwd: str) -> list:
        """
        the play's paths a command line names: the ones _paths finds, and a
        bare word that's a file or a folder where it runs
        """
        out = [full for *_, full in self._paths(text, cwd)]
        for word in batch.split_args(text):
            if word[:1] in "-/\\" or _DRIVE.search(word) or _CLIMB.search(word):
                continue
            full = join(cwd, word)
            if re.search(r"[\\/]", word) or self.kind(full):
                out.append(full)
        return out

    def _inward(self, text: str) -> str:
        """a line a program of theirs printed, with the stage's paths back as the play's"""
        for _, root, real in self.stage.mounts:
            for spelled in {str(real), str(real).replace("\\", "/")}:
                text = re.sub(re.escape(spelled) + r"([\\/][^\s'\"]*)?",
                              lambda hit, root=root: root + (hit.group(1) or "").replace("/", "\\"),
                              text)
        drives = self.stage.folder / "x"
        for spelled in {str(drives), str(drives).replace("\\", "/")}:
            text = re.sub(re.escape(spelled) + r"[\\/]([A-Za-z])(?=[\\/]|\b)",
                          lambda hit: hit.group(1).upper() + ":", text)
        return text

    def _outward(self, text: str, cwd: str):
        """
        its command line with every path of the play's swapped for where that
        is on the stage, in place, so it reads the way the script wrote it. A
        path left bare gets quotes when its place on the stage has spaces, as
        the stage in the game's folder usually does. Words, for a stand-in on
        Linux
        """
        line, at = [], 0
        for start, end, before, after, full in self._paths(text, cwd):
            real = str(self.stage.real(full))
            if text[start:end].rstrip('"').endswith(("\\", "/")):
                real += os.sep
            # quoted the way it was before and after it, and when it needs to be
            opening = '"' if not before and (after or re.search(r"\s", real)) else ""
            closing = '"' if (before or opening) and not after else ""
            if closing and real.endswith("\\"):
                # a backslash right before a quote would escape it
                real += "\\"
            line.append(text[at:start] + opening + real + closing)
            at = end
        line = "".join(line) + text[at:]
        return line if os.sep == "\\" else batch.split_args(line)

    # what the game's files were left as

    def changes(self) -> dict:
        """game path -> what it is now, for every file of the game's this play changed,
        None for one it deleted"""
        out, stage = {}, self.stage
        for k, node in self.nodes.items():
            # what the stage had, untouched, first: a Nova install's stage has
            # every record of the game on it
            before = stage.nodes.get(k)
            if before is node or node[1] is None or before is not None and before[1] == node[1]:
                continue
            rel = stage.game_rel(node[0])
            if not rel:
                continue
            out[rel] = node[1]
        # wherever the game's mounted, and a file the script wrote over at one
        # place it found the game isn't one it deleted at another
        for k, (spelled, ref) in stage.nodes.items():
            if ref is not None and k not in self.nodes:
                rel = stage.game_rel(spelled)
                if rel and rel not in out:
                    out[rel] = None
        return out

    def nova_changes(self) -> bool:
        """whether it put something in a Nova pack's Data folders, which Nova installs after it"""
        trees = [key(tree) + "\\" for tree in self.stage.trees]
        return any(k.startswith(tuple(trees)) and self.nodes.get(k, (None, None))[1] is not None
                   and self.nodes.get(k) != self.stage.nodes.get(k)
                   for k in self.touched) if trees else False

    def elsewhere(self) -> list:
        """files it put outside the game, the drop and TEMP: AppData, Documents, a folder of its own"""
        skip = [key(r) for r in (*self.stage.roots(), self.stage.mod, TEMP)]
        # a log it keeps on the desktop isn't what it's for
        return sorted(spelled for k, (spelled, ref) in self.nodes.items()
                      if ref is not None and self.stage.nodes.get(k) != (spelled, ref)
                      and not any(k == s or k.startswith(s + "\\") for s in skip)
                      and not JUNK.search(spelled))

    def verdict(self) -> str:
        """
        what this play came to: "installs" when it put something of the mod's
        in, or skipped a program of theirs whose work it can't see;
        "already" when all of the mod's it wrote is there already, which is
        the mod dropped again; "restores" when all it did was put originals
        back, its uninstaller; "nothing" otherwise, deleting the game's files
        included, since that can't go in
        """
        kinds = self.kinds()
        if self.unseen > 0 or "mod" in kinds or self.nova_changes():
            return "installs"
        if "same" in kinds:
            return "already"
        return "restores" if "original" in kinds else "nothing"

    def kinds(self) -> set:
        """what kinds of thing it wrote, as Stage.put_as says them, records one by one"""
        return {kind for _, kind in self.puts().values()} - {""}

    def puts(self) -> dict:
        """
        what it wrote where, as (what, its kind): a game path, or for a record
        an index changed index|record
        """
        from patcher.mods.game_files import DATA_DIRS

        stage, out = self.stage, {}
        for rel, ref in self.changes().items():
            top = rel.split("/", 1)[0].lower()
            # an archive grown for records an index doesn't point at yet, a
            # log, a readme, another game's folder
            if isinstance(ref, Blob) or JUNK.search(rel) or (
                    top in DATA_DIRS and top != stage.game.data):
                continue
            if isinstance(ref, Listing):
                if ref.base.rel.lower() in stage.pairs:
                    archive = stage.archive_of(ref.base)
                    for record, inner in ref.changes:
                        spot = f"{ref.base.rel}|{record}".lower()
                        out[spot] = (inner, stage.put_as(inner, Record(ref.base, archive, record),
                                                         record, stage.went_over.get(spot)))
                continue
            was = stage.nodes.get(key(child(stage.root, rel.replace("/", "\\"))))
            out[rel.lower()] = (ref, stage.put_as(ref, was[1] if was is not None else None, rel,
                                                  stage.went_over.get(rel.lower())))
        return out


def _quiet_program(name: str) -> bool:
    low = name.lower()
    from patcher.mods.game_files import GAME_EXES
    return low in GAME_EXES or "getpath" in low or low == "steam.exe" or bool(
        re.search(r"4\s*gb|laa", low))


# a drive path, and a path with a .. in it
_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_CLIMB = re.compile(r"(^|[\\/])\.\.([\\/]|$)")
# a switch a path can be glued to: -o, /log:, --dir=, -Dgame.dir=
_SWITCH = re.compile(r"[-/]{1,2}[A-Za-z][\w-]*(\.[\w-]+)*[:=]?")


def _words(text: str) -> list:
    """(start, end) of each word of a command line as written, its quotes included"""
    out, start, quoted = [], None, False
    for n, c in enumerate(text):
        if c == '"':
            quoted = not quoted
        if c in " \t" and not quoted:
            if start is not None:
                out.append((start, n))
                start = None
        elif start is None:
            start = n
    if start is not None:
        out.append((start, len(text)))
    return out


def _spans(text: str) -> list:
    """
    (start, end, quoted before, quoted after, kind) of every path in a
    command line as written, kind drive, root or climb. A drive path counts
    wherever one starts (in=C:\\x, @C:\\list, a;C:\\b, "-oC:\\out"); one from
    the root (\\x or /x) or with a .. in it where a path can start: the
    word's start, past a switch, or after a key= or key:, an @, a ; or a ,
    that isn't part of a drive path's name. A path runs to the end of its
    word, a ; or the next path, and on past a closing quote only when more
    of its name follows: "%~dp0"..\\x but not "C:\\a","C:\\b". A URL's /../ is
    the site's
    """
    out = []
    for first, last in _words(text):
        word = text[first:last]
        starts = {}
        for hit in _DRIVE.finditer(word):
            at, lead = hit.start(), word[:hit.start()].replace('"', "")
            # glued to a one-letter switch or past a case change, -oC:\x and
            # -outdirD:\x, but /D:\x and /OUT:\x are switches with a key
            if lead in ("/", "-", "--") and word[at - 1:at] != '"':
                continue
            if not at or not word[at - 1].isalnum() or _SWITCH.fullmatch(lead) and (
                    len(lead.lstrip("-/")) == 1 or word[at - 1].islower() and word[at].isupper()):
                starts[at] = "drive"
        bare = len(word) - len(word.lstrip('"'))
        switch = None if starts else _SWITCH.match(word, bare)
        if word[bare:bare + 1] == "/" and _rooted(word[bare:]):
            # "/Program Files (x86)/..." starts out looking like a switch
            switch = None
        slots = {switch.end()} if switch else {0}
        drives = sorted(starts)
        reach = [_reach(word, at, drives[n + 1] - 1 if n + 1 < len(drives) else len(word))[0]
                 for n, at in enumerate(drives)]
        for hit in re.finditer(r"[=@;,:]", word):
            # a = or a , inside a drive path is part of its name
            if hit.group() == ";" or not any(at < hit.start() < end
                                             for at, end in zip(drives, reach)):
                slots.add(hit.end())
        for at in sorted(slots) if "://" not in word else ():
            while at < len(word) and word[at] == '"':
                at += 1
            raw = re.split(r"[=@;,:]", word[at:])[0]
            if at in starts or not raw:
                continue
            if raw[0] == "\\" and raw[1:2] not in ("", "\\", '"') or raw[0] == "/" and \
                    raw[1:2] not in ("", "/") and re.search(r"[\\/]", raw[1:]):
                # one slash on its own is a switch, /log, /p:x
                starts[at] = "root"
            elif _CLIMB.search(raw.replace('"', "")):
                starts[at] = "climb"
        ordered = sorted(starts)
        for n, start in enumerate(ordered):
            end, after = _reach(word, start, ordered[n + 1] - 1 if n + 1 < len(ordered)
                                else len(word))
            before = word[:start].count('"') % 2 == 1
            out.append((first + start, first + end, before, after, starts[start]))
    return out


def _rooted(word: str) -> bool:
    """whether a word starting with / is a path from the root, /Users/x, rather than /o\\x"""
    parts = re.split(r"[\\/]", re.split(r"[=@;,:]", word.replace('"', ""))[0][1:])
    return len(parts) > 1 and len(parts[0]) > 1


def _reach(word: str, start: int, stop: int) -> tuple:
    """
    (where a path in a word ends, whether that's inside quotes). A ; or a :
    past its drive's own ends it, since neither is in a name: a.txt:1
    """
    quoted = word[:start].count('"') % 2 == 1
    end = start
    while end < stop and word[end] != ";" and (word[end] != ":" or end == start + 1):
        if word[end] == '"':
            if quoted and word[end + 1:end + 2] in ("", ";", ",", "+", "=", "@", ":"):
                break
            quoted = not quoted
        end += 1
    return end, quoted


def _under(path: str, root: str) -> bool:
    return key(path).startswith(key(root).rstrip("\\") + "\\")


def _outermost(roots: list) -> list:
    out = []
    for root in sorted({key(r): r for r in roots}.values(), key=lambda r: len(key(r))):
        if not any(_under(root, kept) or key(root) == key(kept) for kept in out):
            out.append(root)
    return out


def _stamp(path: Path):
    try:
        info = path.stat()
        return info.st_size, info.st_mtime_ns
    except OSError:
        return None


# a question, which a line that also says something's done still is
ASKS = re.compile(r"\?\s*$|\(\s*y(es)?\s*/\s*n(o)?\s*\)|\[\s*y(es)?\s*/\s*n(o)?\s*\]|\by/n\b",
                  re.I)


def _typed(out: str, err: str, wrote: bool = False):
    """
    what a program of theirs that stopped for want of input would have been
    typed: "" for one that finished and then waited for a key, the answers to
    a yes or no, a numbered menu or Press Enter, else None
    """
    if not diagnose.KEYLESS.search(f"{out}\n{err}"):
        return None
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    prompt = lines[-1] if lines else ""
    # "Nothing was patched." says patched too, and so can the closing line
    # itself, up to its "press any key"
    if any(diagnose.FINISHED.search(line) for line in lines[-3:]) and \
            not any(diagnose.went_wrong(line) for line in
                    lines[-4:-1] + [re.split(r"press|any key", prompt, 1, re.I)[0]]) and \
            re.search(r"press|any key|exit|close", prompt, re.I):
        return ""
    # a bare "Done." and a ReadKey that never said what it was waiting for:
    # finished only when nothing it said all along failed and it left a file
    # with something in it. An empty one is the failure it didn't say
    if wrote and diagnose.FINISHED.search(prompt) and not ASKS.search(prompt) and \
            not any(diagnose.went_wrong(line) for line in lines):
        return ""
    if re.search(r"\(\s*y(es)?\s*/\s*n(o)?\s*\)|\[\s*y(es)?\s*/\s*n(o)?\s*\]|\by/n\b",
                 prompt, re.I):
        return "y\n" * 16
    if re.search(r"^\s*[\[(]?1[\]).:]\s", "\n".join(lines[-8:]), re.M):
        return "1\n" * 16
    if re.search(r"press (enter|return|any key)|to continue", prompt, re.I):
        return "\n" * 16
    return None


def _spawn(exe: Path, command, cwd: Path, stdin, cancel, env=None):
    """(exit code or None for one that ran out of time, its output, its errors)"""
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    if isinstance(command, list):
        args = [str(exe), *command]
    else:
        args = f'"{exe}" {command}'
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=creation, env=env,
                                   stdin=subprocess.PIPE if stdin is not None
                                   else subprocess.DEVNULL)
    except OSError as e:
        # not a program Windows will start, a 16-bit one or one Windows
        # Security took: cmd's errorlevel for a program it can't run
        # windows hands back its "%1 is not a valid Win32 application" with the
        # %1 never filled in, so the player would read a literal %1
        return 9009, "", f"{exe.name} couldn't be started: {str(e).replace('%1', str(exe))}\n"
    result = {}

    def talk():
        try:
            result["out"] = process.communicate(
                input=batch.encode(stdin) if stdin is not None else None)
        except (OSError, ValueError):
            result["out"] = (b"", b"")

    talker = threading.Thread(target=talk, daemon=True)
    talker.start()
    deadline = time.monotonic() + TOOL_LIMIT
    while talker.is_alive():
        talker.join(0.25)
        if cancel is not None and cancel.is_set() or time.monotonic() > deadline:
            process.kill()
            talker.join(5)
            if cancel is not None and cancel.is_set():
                from patcher.mods.hd_runner import Cancelled
                raise Cancelled("stopped while its installer played")
            return None, "", ""
    out, err = result.get("out", (b"", b""))
    return process.returncode, batch.decode(out or b""), batch.decode(err or b"")


# the programs played here, by what they're called

def _game_banner(flags: set) -> str:
    if "-ff132" in flags:
        return "Game set to FF13-2.\n"
    if "-ff133" in flags:
        return "Game set to FF13-3.\n"
    if "-ff13" in flags:
        return "Game set to FF13.\n"
    return "Game automatically set to FF13.\n"


def _ff13tool(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """
    FluffyQuack's ff13tool, the way v1.4 behaves: -x unpacks beside the
    archive, -i takes one file whose path is the record's, or the record's
    with one folder in front, -c builds an archive back out of an unpacked
    folder and -all imports a folder's worth. It always exits 1, and the
    scripts count its Added/Updated/Imported/Wrote lines instead, so those
    are said word for word. What it touches here is the index's and the
    archive's description, never their bytes
    """
    flags = {a.lower() for a in argv if a.startswith("-")}
    plain = [a for a in argv if not a.startswith("-")]
    said = [_game_banner(flags)]
    stage = world.stage

    def done(code: int = 1) -> int:
        text = "".join(said)
        io.out.write(text)
        world.write(child(cwd, "log.txt"), batch.encode(text.replace("\n", "\r\n")))
        return code

    if len(plain) < 2:
        said.append("Usage: ff13tool [action] [filelist] [archive or directory]\n")
        return done()
    index_path, other = join(cwd, plain[0]), join(cwd, plain[1])
    listing = stage.listing(world.ref(index_path))
    if listing is None:
        if world.kind(index_path) != "file":
            said.append(f"Error: Failed to open {plain[0]} for reading.\n")
            return done()
        if world.dry:
            return world.unrun(done())
        # an index that isn't one of the game's: theirs to unpack for real
        return world.staged(path, text, cwd, io)
    current = _contents(stage, listing, world.ref(other) if "-c" not in flags else None)
    count = len(current)
    said.append(f"File list processed. {count} files listed in "
                f"{max(1, (count + 255) // 256)} file chunks.\n")
    if "-x" in flags:
        blob = stage.blob(world.ref(other))
        if blob is None:
            said.append(f"Error: Failed to open {plain[1]} for reading.\n")
            return done()
        current = _contents(stage, listing, blob)
        folder = other[:-len(".win32.bin")] if other.lower().endswith(".win32.bin") else \
            other.rsplit(".", 1)[0]
        shown = plain[1][:-len(".win32.bin")] if plain[1].lower().endswith(".win32.bin") else \
            plain[1].rsplit(".", 1)[0]
        world.unpacked.add(key(folder))
        for record, ref in current.items():
            world.set(child(folder, record.replace("/", "\\")), ref)
            said.append(f"Extracted {shown}/{record} (uncompressed)\n")
        said.append(f"Extracted {count} files in total.\n{count} files uncompressed. "
                    "0 files copied.\n")
        return done()
    if "-c" in flags:
        folder = other
        changes, contents = dict(listing.changes), {}
        for record in _contents(stage, listing, None):
            file = child(folder, record.replace("/", "\\"))
            if world.kind(file) != "file":
                said.append(f"Error: Failed to open {plain[1]}/{record} for reading.\n")
                world.set(folder + ".win32.bin", Broken(
                    f"its ff13tool stopped rebuilding {leaf(folder)}.win32.bin part way, "
                    f"{record} being missing"))
                return done()
            ref = world.ref(file)
            contents[record] = ref
            if ref != Record(listing.base, stage.archive_of(listing.base), record):
                changes[record] = ref
            else:
                changes.pop(record, None)
            said.append(f"Added {plain[1]}/{record} (compressed)\n")
        world.set(folder + ".win32.bin", Blob(None, frozenset(), tuple(sorted(contents.items()))))
        world.set(index_path, Listing(listing.base, tuple(sorted(changes.items())), listing.plain))
        said.append(f"Added {count} files in total.\n{count} files compressed. 0 files copied.\n"
                    f"Wrote {plain[1]}.win32.bin\nWrote {plain[0]}\n")
        return done()
    if "-i" in flags:
        blob = stage.blob(world.ref(other))
        if blob is None:
            said.append(f"Error: Failed to open {plain[1]} for reading.\n")
            return done()
        if "-all" in flags:
            folder = join(cwd, plain[2]) if len(plain) > 2 else cwd
            files = [(p, p[len(folder) + 1:].replace("\\", "/"))
                     for p in world._files_under(folder)]
        else:
            if len(plain) < 3:
                return done()
            given = plain[2].replace("\\", "/")
            files = [(join(cwd, plain[2]), given)]
        changes, extra, landed = dict(listing.changes), set(blob.extra), 0
        for file, given in files:
            record = _record_for(current, given, whole_name="-all" in flags)
            shown = given if "-all" in flags else given.split("/", 1)[-1]
            if record is None or world.kind(file) != "file":
                said.append(f"Ignored {given}\n")
                continue
            ref = world.ref(file)
            changes[record] = ref
            extra.add(ref)
            landed += 1
            said.append(f"Added {shown} (compressed)\n")
        if not landed:
            return done()
        world.set(other, Blob(blob.base, frozenset(extra), blob.contents))
        world.set(index_path, Listing(listing.base, tuple(sorted(changes.items())), listing.plain))
        if "-all" in flags:
            said.append(f"Imported {landed} files to {plain[1]}\nWrote {plain[0]}\n")
        else:
            said.append(f"Updated {plain[1]}\nImported one file to {plain[1]}\nWrote {plain[0]}\n")
        return done()
    said.append("Usage: ff13tool [action] [filelist] [archive or directory]\n")
    return done()


def _record_for(current: dict, given: str, whole_name: bool = False):
    """the record ff13tool -i takes a path for: the record's own, or it with one folder in front"""
    lowered = given.lower().lstrip("./")
    if lowered in current:
        return lowered
    if not whole_name and "/" in lowered:
        rest = lowered.split("/", 1)[1]
        if rest in current:
            return rest
    return None


def _contents(stage: Stage, listing: Listing, blob) -> dict:
    """record path -> what's in it, for an index over its archive"""
    changed = listing.changed()
    archive = stage.archive_of(listing.base)
    out = {}
    built = dict(blob.contents) if isinstance(blob, Blob) and blob.base is None else None
    for record in stage.entries(listing.base):
        if record in changed:
            out[record] = changed[record]
        elif built is not None and record in built:
            out[record] = built[record]
        else:
            out[record] = Record(listing.base, archive, record)
    return out


def _crypt(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """ffxiiicrypt: a filelist's encryption is how it's stored, which the drawer redoes anyway"""
    if len(argv) >= 3 and argv[-1].lower() == "filelist" and argv[0].lower() in ("-d", "-e"):
        target = join(cwd, argv[1])
        listing = world.stage.listing(world.ref(target))
        if listing is not None:
            world.set(target, Listing(listing.base, listing.changes, argv[0].lower() == "-d"))
            io.out.write(f"Finished {'de' if argv[0].lower() == '-d' else 'en'}crypting file.\n\n")
            return 0
    if world.dry:
        return world.unrun()
    return world.staged(path, text, cwd, io)


def _xdelta(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    from patcher.mods.recipes import _xdelta as decoding

    found = decoding(argv)
    if found is None:
        if world.dry:
            return world.unrun()
        return world.staged(path, text, cwd, io)
    source, patch, out = (join(cwd, p) for p in found)
    if world.kind(source) != "file" or world.kind(patch) != "file":
        io.err.write("xdelta3: file open failed\n")
        return 1
    world.set(out, Patched(world.ref(source), world.ref(patch)))
    return 0


def _cocoon(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    if len(argv) != 5 or argv[0].lower() != "inject2":
        if world.dry:
            return world.unrun()
        return world.staged(path, text, cwd, io)
    _, index, archive, folder, inner = argv
    record = inner.replace("\\", "/").lower()
    io_line = []
    file = join(join(cwd, folder), inner)
    listing = world.stage.listing(world.ref(join(cwd, index)))
    blob = world.stage.blob(world.ref(join(cwd, archive)))
    if listing is None or blob is None or world.kind(file) != "file" or \
            record not in world.stage.entries(listing.base):
        io.err.write("Cocoon: couldn't inject\n")
        return 1
    ref = world.ref(file)
    changes = dict(listing.changes)
    changes[record] = ref
    world.set(join(cwd, archive), Blob(blob.base, blob.extra | {ref}, blob.contents))
    world.set(join(cwd, index), Listing(listing.base, tuple(sorted(changes.items())), listing.plain))
    io.out.write("".join(io_line) + f"Injected {inner}\n")
    return 0


def _seven(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """7-Zip's x and e, out of an archive in the drop the drawer has already opened"""
    if not argv or argv[0].lower() in CHECKS:
        io.out.write("Everything is Ok\n")
        return 0
    if argv[0].lower() == "h":
        if key(parent(path)) not in {key(folder) for folder in ARCHIVERS}:
            # the drop's own 7-Zip, which runs for real like any program of its own
            return _theirs(world, path, text, cwd, io)
        done = _seven_hash(world, argv, cwd, io)
        if done is not None:
            return done
        real = host_archiver()
        if real is not None:
            return world.unrun() if world.dry else world.staged(SEVEN_ZIP, text, cwd, io,
                                                                host=real)
        io.err.write("ERROR: Unsupported hash method or switch here\n")
        return 7
    if argv[0].lower() not in ("x", "e"):
        return _theirs(world, path, text, cwd, io)
    target, archive, wanted, password = cwd, None, [], None
    for word in argv[1:]:
        if word.lower().startswith("-o"):
            target = join(cwd, _unescaped(word[2:]))
        elif word.lower().startswith("-p"):
            password = word[2:]
        elif word.startswith("-"):
            continue
        elif archive is None:
            archive = join(cwd, word)
        else:
            wanted.append(word.replace("\\", "/").lower())
    return _unpack(world, argv[0].lower() == "e", archive, target, wanted, "Everything is Ok",
                   cwd, io, path, text, password)


# what 7-Zip's -scrc and certutil's -hashfile call each hash, as hashlib has them
HASHES = {"crc32": "crc32", "md5": "md5", "sha1": "sha1", "sha256": "sha256", "sha384": "sha384",
          "sha512": "sha512"}


def _digest(world: World, path: str, method: str):
    """(a file of the play's hashed, in hex, its size), read through once, or None"""
    try:
        handle = world.stage.opened(world.ref(path))
    except Exception:
        return None
    summed, crc, size = None if method == "crc32" else hashlib.new(method), 0, 0
    with handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            size += len(chunk)
            if summed is None:
                crc = zlib.crc32(chunk, crc)
            else:
                summed.update(chunk)
    return (f"{crc:08x}" if summed is None else summed.hexdigest()), size


def _seven_hash(world: World, argv: list, cwd: str, io):
    """
    7-Zip's h, the way a version check has it hash one of the game's files
    and look for the hash it knows in what's printed, None past what's
    modeled here: another method, a folder, a wildcard, a switch it hasn't
    """
    asked = next((a[5:] for a in argv[1:] if a.lower().startswith("-scrc")), "") or "CRC32"
    method = HASHES.get(asked.lower())
    switches = [a.lower() for a in argv[1:] if a.startswith("-")]
    if method is None or any(not (a.startswith(("-scrc", "-bs", "-bb")) or a in ("-ba", "-y"))
                             for a in switches):
        return None
    files = [join(cwd, a) for a in argv[1:] if not a.startswith("-")]
    if not files or any(batch.wild(leaf(f)) or world.kind(f) == "dir" for f in files):
        return None
    missing = next((f for f in files if world.kind(f) is None), None)
    if missing is not None:
        io.err.write(f"ERROR: {missing}\nThe system cannot find the file specified.\n")
        return 2
    rows = []
    for full in files:
        got = _digest(world, full, method)
        if got is None:
            return None
        # 7-Zip writes a CRC in capitals and the rest in small letters
        rows.append((got[0].upper() if method == "crc32" else got[0], got[1], leaf(full)))
    lines = [f"{h} {size:>13}  {name}" for h, size, name in rows]
    if "-ba" not in switches:
        label, wide = asked.upper(), max(len(r[0]) for r in rows)
        rule = f"{'-' * wide} -------------  ------------"
        lines = ["", "Scanning", f"{len(rows)} file{'s' * (len(rows) != 1)}", "",
                 f"{label:<{wide}} {'Size':>13}  Name", rule, *lines, rule, ""]
        if len(rows) == 1:
            lines.append(f"{label} for data:              {rows[0][0]}")
        lines += ["", "Everything is Ok"]
    io.out.write("\n".join(lines) + "\n")
    return 0


def _certutil(world: World, argv: list, text: str, cwd: str, io) -> int:
    """
    certutil -hashfile, a version check's hash of one of the game's files.
    Its download and everything else it does past that stay undone
    """
    verb = argv[0].lower().lstrip("-/") if argv else ""
    if verb == "urlcache":
        return _refused("download something")(world, argv, text, cwd, io)
    if verb != "hashfile" or len(argv) < 2:
        return _refused("run certutil")(world, argv, text, cwd, io)
    full, asked = join(cwd, argv[1]), (argv[2] if len(argv) > 2 else "SHA1").upper()
    method = HASHES.get(asked.lower())
    got = _digest(world, full, method) if method and world.kind(full) == "file" else None
    if got is None:
        io.err.write("CertUtil: -hashfile command FAILED: 0x80070002 (WIN32: 2 "
                     "ERROR_FILE_NOT_FOUND)\nCertUtil: The system cannot find the file "
                     "specified.\n")
        # its HRESULT, which is what cmd's errorlevel gets
        return 0x80070002 - (1 << 32)
    io.out.write(f"{asked} hash of {world.spell(full)}:\n{got[0]}\n"
                 "CertUtil: -hashfile command completed successfully.\n")
    return 0


def _rar(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """
    WinRAR's, Rar's and UnRAR's x and e, the same unpack said their way: no
    -o for where, which is the last word when it ends in a slash or names a
    folder, and -o+ is about overwriting
    """
    if not argv or argv[0].lower() in CHECKS:
        io.out.write("All OK\n")
        return 0
    if argv[0].lower() not in ("x", "e"):
        return _theirs(world, path, text, cwd, io)
    # -p- is not to ask for one
    password = next((w[2:] for w in argv[1:] if w.lower().startswith("-p") and w != "-p-"), None)
    words = [_unescaped(w) for w in argv[1:] if not w.startswith("-")]
    target = cwd
    if len(words) > 1 and (words[-1].endswith(("\\", "/"))
                           or world.kind(join(cwd, words[-1])) == "dir"):
        target = join(cwd, words.pop())
    return _unpack(world, argv[0].lower() == "e", join(cwd, words[0]) if words else None,
                   target, [w.replace("\\", "/").lower() for w in words[1:]], "All OK",
                   cwd, io, path, text, password)


def _unescaped(word: str) -> str:
    """
    "%TEMP%\\out\\" split the way Windows splits a command line ends in a
    quote, its backslash read as escaping it. A path can't hold one, so it's
    the folder it was written as
    """
    return word[:-1] + "\\" if word.endswith('"') else word


def _theirs(world: World, path: str, text: str, cwd: str, io, archive=None) -> int:
    """
    a program of theirs past what's played here, run for real unless it's a
    look-ahead. Never one of the stand-in archivers, which are two bytes: the
    drop's own 7-Zip or UnRAR goes in its place, or what it couldn't do is said
    """
    if key(parent(path)) in {key(folder) for folder in ARCHIVERS}:
        own = _own_archiver(world, archive, path)
        if own is None:
            # only an unpack's archive is one to call broken: a hash of a game file isn't
            what = f"unpack {leaf(archive)}, which looks damaged or cut short" \
                if archive is not None and _damaged(world, archive) else \
                "unpack an archive the drawer couldn't open"
            if what not in world.left:
                world.left.append(what)
            io.err.write(f"{leaf(path)} can't open that here\n")
            return 2
        path = own
    if world.dry:
        return world.unrun()
    return world.staged(path, text, cwd, io)


def _head(world: World, path) -> bytes:
    """the first bytes of a file of the play's, which say what kind of archive it is"""
    try:
        return world.stage.head(world.ref(path)) if path and world.kind(path) == "file" else b""
    except Exception:
        return b""


# the parts of an archive split in pieces, pack.z01 or pack.7z.001, past the last
PARTS = re.compile(r"\.(z\d\d|\d{3})$", re.I)


def _stem(name: str) -> str:
    """what every part of a split archive is called before its number: pack for pack.z01"""
    return PARTS.sub("", name).lower().removesuffix(".zip")


def _damaged(world: World, path: str) -> bool:
    """
    whether an archive of the play's is broken: one that starts like a zip and
    won't open as one, or an empty file or a page of text under its name, a
    download that never finished or a web page saved as it. Anything else may
    be one this PC's 7-Zip reads, a .7z or .rar the drawer couldn't open only
    wanted an archiver it hasn't got, and a zip it couldn't may be locked
    """
    if PARTS.search(leaf(path)):
        # one part of several, which only opens with the rest
        return False
    try:
        head = world.stage.head(world.ref(path), 264) if world.kind(path) == "file" else None
    except Exception:
        # one that can't be read here isn't known to be anything
        return False
    if head is None:
        return False
    if head[:4] != b"PK\x03\x04":
        return not head.strip(b"\0 \t\r\n") or _text(head)
    handle = None
    try:
        handle = world.stage.opened(world.ref(path))
        zipfile.ZipFile(handle).close()
        return False
    except zipfile.BadZipFile:
        return True
    except (OSError, ValueError, EOFError, Opaque):
        return False
    finally:
        if handle is not None:
            handle.close()


def _text(head: bytes) -> bool:
    """whether a file's first bytes are words, an HTML page or a JSON error, not an archive"""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        # UTF-16, a page saved by Notepad say: words have a zero for every other byte
        body = head[2:len(head) // 2 * 2]
        wide, narrow = (body[1::2], body[0::2]) if head[0] == 0xFF else (body[0::2], body[1::2])
        return not wide.strip(b"\0") and _text(narrow)
    try:
        said = head.decode("utf-8")
    except UnicodeDecodeError as e:
        # a character cut in two where the read stopped is still text
        if e.start < len(head) - 3:
            return False
        said = head[:e.start].decode("utf-8")
    return all(c.isprintable() or c in "\t\r\n\x0c" for c in said.lstrip("\ufeff"))


class _Owned(zipfile.ZipFile):
    """a zip over a handle it closes with itself, which ZipFile never does for one it's given"""

    def close(self) -> None:
        handle = self.fp
        super().close()
        if handle is not None:
            handle.close()


def _zip_of(world: World, path: str):
    """
    a zip of the play's opened, one the drawer never did: a copy the script
    made under a new name, a zip inside a zip. None for anything else
    """
    if _head(world, path)[:2] != b"PK":
        return None
    handle = None
    try:
        handle = world.stage.opened(world.ref(path))
        zipped = _Owned(handle)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, Opaque):
        if handle is not None:
            handle.close()
        return None
    if spanned(zipped.fp):
        # the last of several parts, whose members are in the others
        zipped.close()
        return None
    return zipped


class _Unreadable(Exception):
    """a member that won't inflate: a bad CRC, a stream cut short"""


def _unzipped(world: World, zipped, target: str, wanted: list, flat: bool,
              password=None, source=None) -> str:
    """
    a zip's files where an unpack of it puts them, never above where it was
    told, each streamed into the store. "" once they're in, else what stopped
    it: "password" for one locked with none given, "wrong" for one the
    password given doesn't open, "method" for one packed the way only 7-Zip
    reads, "space" for more than the drive has free, "damaged" for one that
    won't inflate
    """
    from patcher.download import free_space

    chosen = []
    for info in zipped.infolist():
        inner = info.filename.replace("\\", "/")
        if info.is_dir() or inner.startswith("/") or ".." in inner.split("/") \
                or re.match(r"^[a-zA-Z]:", inner):
            continue
        if wanted and not any(batch.matches(inner.lower(), w) or inner.lower() == w
                              for w in wanted):
            continue
        chosen.append((info, leaf(inner.replace("/", "\\")) if flat else inner.replace("/", "\\")))
    if any(info.compress_type not in INFLATES for info, _ in chosen):
        return "method"
    locked = any(info.flag_bits & 1 for info, _ in chosen)
    if locked and not password:
        return "password"

    def inflated(info):
        try:
            return None if source is None else \
                world.stage.inflated.get((source, info.filename, info.CRC, info.file_size))
        except TypeError:
            return None

    need = sum(info.file_size for info, _ in chosen
               if info.file_size >= INLINE and inflated(info) is None)
    if need and free_space(world.stage.folder) < need + (256 << 20):
        return "space"
    for info, rel in chosen:
        ref = inflated(info)
        if ref is None:
            try:
                with zipped.open(info, pwd=password.encode("utf-8") if locked else None) as inside:
                    ref = world.stage.keep_from(_reading(inside, world.cancel))
            except (zipfile.BadZipFile, zlib.error, EOFError):
                # a local header that doesn't match its listing
                return "damaged"
            except RuntimeError:
                # zipfile's word for a password that doesn't fit
                return "wrong"
            except NotImplementedError:
                return "method"
            except _Unreadable:
                return "damaged"
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    return "space"
                raise
            try:
                if source is not None:
                    world.stage.inflated[(source, info.filename, info.CRC, info.file_size)] = ref
            except TypeError:
                pass
        world.set(child(target, rel), ref)
    return ""


def _reading(handle, cancel=None):
    """
    a member's read, with what a broken one raises said as broken, and Stop
    heard between reads
    """
    def read(n: int = -1) -> bytes:
        if cancel is not None and cancel.is_set():
            from patcher.mods.hd_runner import Cancelled
            raise Cancelled("stopped while its installer played")
        try:
            return handle.read(n)
        except (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError, ValueError) as e:
            raise _Unreadable(str(e)) from e
        except OSError as e:
            # bz2's word for a stream that won't decompress
            if e.errno is None:
                raise _Unreadable(str(e)) from e
            raise
    return read


def _own_archiver(world: World, archive=None, ran: str = ""):
    """
    a 7-Zip or UnRAR the drop carries, wherever the play put the drop's files,
    that opens `archive`: 7zr only reads .7z, 7za everything but .rar, and
    UnRAR and Rar only .rar. The one called what the script ran first
    """
    head = _head(world, archive) if archive else b""
    rar = head.startswith(b"Rar!")
    opens = {"7z.exe": True, "7za.exe": not rar, "7zr.exe": head.startswith(b"7z\xbc\xaf"),
             "unrar.exe": rar, "rar.exe": rar}
    found = [spelled for spelled, ref in world.nodes.values() if isinstance(ref, Dropped)
             and opens.get(leaf(spelled).lower(), False)]
    named = [spelled for spelled in found if leaf(spelled).lower() == leaf(ran).lower()]
    return min(named or found, key=len) if found else None


# where 7-Zip's installer puts it, the program a .bat runs it as here
SEVEN_ZIP = "C:\\Program Files\\7-Zip\\7z.exe"


def _unpack(world: World, flat: bool, archive, target: str, wanted: list, ok: str, cwd: str,
            io, path: str, text: str, password=None) -> int:
    if archive is not None and batch.wild(leaf(archive)):
        # every archive the name matches, the way 7-Zip takes *.zip
        folder = parent(archive)
        found = [child(folder, name) for name, is_dir in world.listdir(folder)
                 if not is_dir and batch.matches(name, leaf(archive))]
        if not found:
            io.err.write(f"ERROR: {archive}\nThe system cannot find the file specified.\n")
            return 2
        if _own_archiver(world, found[0], path) is not None:
            return _theirs(world, path, text, cwd, io, found[0])
        return max(_unpack(world, flat, each, target, wanted, ok, cwd, io, path, text, password)
                   for each in found)
    ref = world.ref(archive) if archive else None
    if isinstance(ref, Nested):
        members = world.stage.nested.get(ref.member, [])
        prefix = ref.member + "/"
        for member in members:
            inner = member[len(prefix):]
            if wanted and not any(batch.matches(inner.lower(), w) or inner.lower() == w
                                  for w in wanted):
                continue
            rel = leaf(inner.replace("/", "\\")) if flat else inner.replace("/", "\\")
            world.set(child(target, rel), Dropped(member))
        io.out.write(ok + "\n")
        return 0
    if archive is None or _own_archiver(world, archive, path) is not None:
        # the drop's own archiver reads whatever it was packed with
        return _theirs(world, path, text, cwd, io, archive)
    if world.kind(archive) != "file":
        io.err.write(f"ERROR: {archive}\nThe system cannot find the file specified.\n")
        return 2
    trouble = "open"
    zipped = _zip_of(world, archive)
    if zipped is not None:
        began = time.monotonic()
        with zipped:
            trouble = _unzipped(world, zipped, target, wanted, flat, password, world.ref(archive))
        world.waited(began)
        if not trouble:
            io.out.write(ok + "\n")
            return 0
    if trouble == "open" and _damaged(world, archive):
        trouble = "damaged"
    real = host_archiver() if trouble in ("open", "method") else None
    if real is not None:
        # this PC's own 7-Zip reads what the play can't, told what the script told its own
        if world.dry:
            return world.unrun()
        line = " ".join(["e" if flat else "x", f'"{archive}"', f'-o"{target}"', "-y", "-aoa",
                         *([f'"-p{password}"'] if password else []), *(f'"{w}"' for w in wanted)])
        # and the other parts of one split in pieces, beside it the way 7-Zip looks for them
        stem = _stem(leaf(archive))
        parts = [child(parent(archive), name) for name, is_dir in world.listdir(parent(archive))
                 if not is_dir and PARTS.search(name) and key(name) != key(leaf(archive))
                 and _stem(name) == stem]
        return world.staged(SEVEN_ZIP, line, cwd, io, host=real, beside=parts)
    what = {"password": f"unpack {leaf(archive)}, which is locked with a password it doesn't give",
            "wrong": f"unpack {leaf(archive)} with a password that doesn't open it",
            "space": f"unpack {leaf(archive)}, which needs more free space than the game's drive "
                     "has",
            "damaged": f"unpack {leaf(archive)}, which looks damaged or cut short"}.get(
        trouble, f"unpack {leaf(archive)}, which takes 7-Zip to open")
    if what not in world.left:
        world.left.append(what)
    io.err.write(f"{leaf(path)} can't open that here\n")
    return 2


def _getpath(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """MJB's FFXIIIGetPath: a file picker for the launcher, the folder it's in written down"""
    if not argv:
        return 1
    wanted = argv[0].lower()
    found = None
    for k, (spelled, ref) in world.nodes.items():
        if ref is not None and leaf(spelled).lower() == wanted and \
                world.stage.game_rel(spelled) is not None:
            if found is None or len(spelled) < len(found):
                found = spelled
    if found is None:
        return 1
    world.write(child(cwd, "SelectedPath.txt"), batch.encode(parent(found).rstrip("\\") + "\\"))
    return 0


def _laa(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """
    a 4GB patcher of theirs. Apply's 4GB patch is the one that goes on, with
    the clean copy Steam's DRM wants beside it, so theirs never touches the
    exe here and reads as having found it done: MJB's says 1 for that
    """
    if argv and argv[-1] in ("0", "1"):
        io.out.write("Large address aware flag is set\n")
        return 1
    return 0


# ours, in place of any FFXIIIwdbLoad.bat a mod ships or leaves out. Theirs
# finds the game, unpacks white_imgu whole for its .wdbs, runs FFXIIIwdbMod.exe
# and ff13tool -i's what that made. All that's left of it is the pick a Mod_
# .bat sets up between .txts, kept a CHOICE so the drawer offers it as versions
WDBLOAD = "C:\\Program Files\\ArmyOfOne\\FFXIIIwdbLoad.bat"
WDBLOAD_TEXT = """@echo off
setlocal enabledelayedexpansion
set "wdbload=%~1"
if not defined select goto load
ffxiiiwdbload.native --menu %selectchoice% %selecttext%
CHOICE /C %selectchoice% /N /M %selecttext%
set "wdbload=!filename%ERRORLEVEL%!"
:load
ffxiiiwdbload.native "!wdbload!"
"""
WDB_FIX = ("The .txt was made against a different version of the game's tables, or has a "
           "typo in it. A newer version of the mod may fix it.")


def _wdb_txt(world: World, cwd: str, name: str):
    """the .txt a FFXIIIwdbLoad call names: where it runs, else where the download has it"""
    if not name:
        return None
    here = join(cwd, name)
    if world.kind(here) == "file":
        return here
    stage = world.stage
    hits = [m for m in stage.sizes if leaf(m.replace("/", "\\")).lower() == leaf(name).lower()]
    near = [m for m in hits if _folder(m) == stage.bat_folder] or hits
    return child(stage.mod, near[0].replace("/", "\\")) if len(near) == 1 else None


def select_labels(keys: str, text: str) -> dict:
    """
    what each key of a Mod_ .bat's selecttext picks, the way it reads on
    screen: "Select (A) 98% or (B) 88% Chain increment, or (O)riginal rate"
    is 98%, 88% Chain increment and Original rate. A key glued to the word
    is part of it, so (3)0% is 30%
    """
    marks = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"\((\w)\)", text)
             if m.group(1).upper() in keys.upper()]
    out = {}
    for n, (start, end, key) in enumerate(marks):
        stop = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        said = text[end:stop]
        glued = said[:1].isalnum()
        said = (key + said) if glued else said
        # up to where the next option's sentence takes over
        said = re.split(r",?\s+(?:or|and)\b", said, maxsplit=1, flags=re.I)[0]
        said = re.sub(r"[\s,;:?.]+$", "", said).strip()
        # a word in front of a later one's key is part of it: "or bare (F)eet"
        before = re.split(r",|\b(?:or|and)\b", text[marks[n - 1][1]:start], flags=re.I)[-1] \
            if n else ""
        if before.strip() and glued:
            said = f"{before.strip()} {said[:1].lower()}{said[1:]}"
        out[key.upper()] = said[:1].upper() + said[1:] if said else key.upper()
    return out


def _wdb_refused(world: World, said: str, fix: str = WDB_FIX) -> int:
    world.refused = diagnose.Why(said, kind="wdb", sure=True, fix=fix)
    raise SandboxError(said)


def _wdbload(world: World, argv: list, text: str, cwd: str, io) -> int:
    """
    FFXIIIwdbLoad's work in one go: each table its .txt names read out of
    the game as it is now, FFXIIIwdbMod's edits made to it (wdbmod.py), and
    the result put in both voice sets' white_img the way its ff13tool -i
    does. As it is now, not theirs' copy from a first run, so it lands on
    whatever went in before it, from them or anyone
    """
    from patcher.mods import wdbmod

    if argv[:1] == ["--menu"]:
        # our FFXIIIwdbLoad's pick, put up as a menu so the drawer offers it
        keys = argv[1] if len(argv) > 1 else ""
        for key, said in select_labels(keys, " ".join(argv[2:])).items():
            io.out.write(f"{key}. {said}\n")
        return 0
    stage = world.stage
    name = argv[0] if argv else ""
    txt = _wdb_txt(world, cwd, name)
    if txt is None:
        return _wdb_refused(world, f"loads {name or 'a .txt'} into the game's tables, and "
                                   "that isn't in its download",
                            "The download may be incomplete. Get it again and drop that.")
    sets = []
    for voice in "uc":
        rel = f"white_data/sys/filelist{voice}.win32.bin"
        if rel not in stage.pairs:
            continue
        index = child(stage.root, rel.replace("/", "\\"))
        archive = child(stage.root, stage.pairs[rel][1].rel.replace("/", "\\"))
        listing, blob = stage.listing(world.ref(index)), stage.blob(world.ref(archive))
        if listing is not None and blob is not None:
            sets.append((index, archive, listing, blob, _contents(stage, listing, blob)))
    if not sets:
        return _wdb_refused(world, "is a FINAL FANTASY XIII mod, and edits tables only XIII "
                                   "has", "Switch to FINAL FANTASY XIII and drop it again.")
    # FFXIIIwdbSetup reads them out of the English set when there is one
    first = sets[0][4]

    def load(path: str) -> bytes:
        record = _record_for(first, path.replace("\\", "/"))
        if record is None:
            raise wdbmod.WdbModError(f"the game hasn't got {path}")
        return stage.read(first[record])

    try:
        done = wdbmod.run(world.read(txt), load)
    except wdbmod.WdbModError as e:
        return _wdb_refused(world, f"loads {leaf(txt)} into the game's tables, and {e}")
    for index, archive, listing, blob, current in sets:
        changes, extra = dict(listing.changes), set(blob.extra)
        for path, data in done:
            record = _record_for(current, path.replace("\\", "/"))
            if record is not None:
                changes[record] = stage.keep(data)
                extra.add(changes[record])
                io.out.write(f"Added {record} (compressed)\n")
        world.set(archive, Blob(blob.base, frozenset(extra), blob.contents))
        world.set(index, Listing(listing.base, tuple(sorted(changes.items())), listing.plain))
    world.wdb.append(world.read(txt))
    io.out.write(f"End MOD {leaf(txt)}\n")
    return 0


def _wdbmod(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """
    FFXIIIwdbMod.exe run by a .bat of a mod's own rather than through
    FFXIIIwdbLoad: the same edits, into wdb_files\\mod\\ beside it the way the
    exe makes them, out of wdb_files\\orig\\ the first time
    """
    from patcher.mods import wdbmod

    if not argv:
        io.out.write("error: Invalid command line arguments.\n"
                     "usage: FFXIIIwdbMod.exe ModFileName\n")
        return 7
    txt = join(cwd, argv[0])
    if world.kind(txt) != "file":
        io.out.write(f"Unexpected continuous loop reading {argv[0]}.  Exiting\n")
        return 10

    def load(table: str) -> bytes:
        for base in ("wdb_files\\mod", "wdb_files\\orig"):
            there = join(cwd, child(base, table))
            if world.kind(there) == "file":
                return world.read(there)
        raise wdbmod.WdbModError(f"Unable to locate wdb_files\\orig\\{table}")

    try:
        done = wdbmod.run(world.read(txt), load)
    except wdbmod.WdbModError as e:
        io.out.write(f"{e}\n")
        return 1
    for table, data in done:
        world.write(join(cwd, child("wdb_files\\mod", table)), data)
    io.out.write(f"End MOD {argv[0]}\n")
    return 0


# HD Models Plus's support files an add-on's .bat runs out of SupportFiles\\,
# and what stands in for them when its download hasn't got them: the model
# builder runs natively (_hd_models), and setup only has to say where the
# game is, since the originals the builder reads come out of the game as it
# asks for them. Ours over another download's: theirs unpacks every model in
# both voice sets and turns back at a game whose models aren't the originals
# as its download spells them
MODELS_KIT_FILES = {"FF XIII HD Models.exe": b"MZ", "ff13tool.exe": b"MZ", "FFXIIIGetPath.exe": b"MZ",
                    "FFXIIItrbSetup.bat": None,
                    "FFXIIIHDM4Cleanup.bat": b"@echo off\r\nexit /b 0\r\n"}
MODELS_KIT = {name.lower(): data for name, data in MODELS_KIT_FILES.items()}
MODELS_KIT_NAME = "HD Models Plus"
MODELS_FIX = ("It may be made for a different version of HD Models Plus or of the game. Check its "
              "page for the version it needs, then drop it again.")


def models_setup(root: str) -> bytes:
    """our FFXIIItrbSetup.bat: the game's folder in FFXIII_Dir.txt and the two working folders"""
    return batch.encode("\r\n".join([
        "@echo off",
        f"echo {root.rstrip(chr(92))}\\> FFXIII_Dir.txt",
        "if not exist models\\orig md models\\orig",
        "if not exist models\\mod md models\\mod",
        "exit /b 0", ""]))


class _NoOriginal(Exception):
    pass


class _ModelFiles:
    """
    what FF XIII HD Models.exe opens, relative to the folder it runs in: the
    play's files, and for a model under models\\orig\\ the play hasn't got,
    the game's original out of its archives (models_plus.originals)
    """

    def __init__(self, world: World, cwd: str):
        self.world, self.cwd = world, cwd

    def read(self, path: str):
        full = join(self.cwd, path)
        if self.world.kind(full) == "file":
            return self.world.read(full)
        low = path.replace("/", "\\").lower()
        if low.startswith("models\\orig\\"):
            return _game_original(self.world.stage, low[len("models\\orig\\"):])
        return None


def _game_original(stage, rel: str):
    from patcher.mods import models_plus

    hit = re.match(r"^(chr\\\w+\\\w+\\bin\\\w+\.win32)\.(trb|imgb)$", rel)
    if not hit:
        return None
    cache = stage.__dict__.setdefault("originals", {})
    model = hit.group(1).replace("\\", "/")
    if model not in cache:
        try:
            cache[model] = models_plus.originals(stage.install_dir, [model])
        except models_plus.ModelsError as e:
            cache[model] = e
        except Exception:
            cache[model] = {}
    got = cache[model]
    if isinstance(got, Exception):
        raise _NoOriginal(str(got))
    return got.get(rel.replace("\\", "/"))


def _hd_models(world: World, argv: list, cwd: str, io, path: str, text: str = "") -> int:
    """
    FF XIII HD Models.exe, natively (hdmodels/build.py): the same command
    line, recipe and exit codes, its models written into models\\mod\\ for
    the .bat's ff13tool -i, with its folders made when the setup it expected
    didn't make them. Its ::A questions are the play's, answered like any
    other. A look-ahead only asks them
    """
    from patcher.mods.hdmodels import build

    if world.stage.game.data != "white_data":
        # XIII-2's HD Project has a builder by the same name that isn't this one
        return world.unrun() if world.dry else world.staged(path, text, cwd, io)
    files = _ModelFiles(world, cwd)
    recipe = next((a for a in argv if not a.startswith("-")), None)
    asked = Counter()

    def ask(question: str) -> bool:
        asked[question] += 1
        where = (join(cwd, recipe or ""), len(asked) * 1000 + asked[question])
        said = world.answer(batch.Ask("choice", question, options="YN", where=where,
                                      lines=(question,), new=(question,)))
        return (said or "N").upper().startswith("Y")

    said = []
    try:
        if world.dry:
            raw = files.read(recipe) if recipe else None
            if raw is not None:
                preset = argv[argv.index("-o") + 1] if "-o" in argv[:-1] else "0"
                build.questions(raw, ask, int(preset) if preset.isdigit() else 0)
            return world.unrun()
        code, outs = build.command_line(argv, files, ask=ask, log=said.append, cancel=world.cancel)
    except build.Stopped:
        from patcher.mods.hd_runner import Cancelled
        raise Cancelled("stopped while its installer played") from None
    except build.ModelError as e:
        world.refused = diagnose.Why(f"builds a character model, and {e}", kind="models",
                                     sure=True, fix=MODELS_FIX)
        raise SandboxError(f"builds a character model, and {e}") from None
    except _NoOriginal as e:
        world.refused = diagnose.Why(str(e), kind="models", sure=True)
        raise SandboxError(str(e)) from None
    io.out.write("".join(said))
    for rel, data in outs.items():
        target = join(cwd, rel)
        world.mkdirs(parent(target))
        world.write(target, data)
    return code


_PLAYED = {"ff13tool.exe": _ff13tool, "ffxiiicrypt.exe": _crypt, "cocoon.exe": _cocoon,
           "7z.exe": _seven, "7za.exe": _seven, "7zr.exe": _seven,
           "winrar.exe": _rar, "rar.exe": _rar, "unrar.exe": _rar,
           "ffxiiigetpath.exe": _getpath, "ffxiiiwdbmod.exe": _wdbmod,
           "ff xiii hd models.exe": _hd_models}
_PLAYED_LIKE = [(r"^xdelta.*\.exe$", _xdelta), (r"^ff13tool.*\.exe$", _ff13tool),
                (r"getpath.*\.exe$", _getpath)]


def known_tool(name: str) -> bool:
    """
    whether a program's a tool a .bat runs rather than an installer of its
    own: ff13tool, 7-Zip, WhiteBinTools, a 4GB patcher
    """
    from patcher.mods.game_files import LARGE_ADDRESS

    name = name.lower()
    return name in _PLAYED or any(re.search(pattern, name) for pattern, _ in _PLAYED_LIKE) \
        or bool(LARGE_ADDRESS.search(name)) or name.startswith("whitebin")


# the system's own programs, the ones installers lean on

def _reg(world: World, argv: list, text: str, cwd: str, io) -> int:
    if not argv:
        return 1
    verb = argv[0].lower()
    if verb in ("add", "delete") and len(argv) > 1 and argv[1].strip():
        _wrote(world, verb, argv)
    if verb != "query":
        # writes to the registry, a language setting or a file association:
        # nothing the game's files hold, and nothing done to this PC
        if diagnose.SETTING not in world.left:
            world.left.append(diagnose.SETTING)
        return 0
    if len(argv) < 2 or not argv[1].strip():
        # the key was in a variable that never got set
        io.err.write("ERROR: Invalid syntax.\nType \"REG QUERY /?\" for usage.\n")
        return 1
    spelled, place = argv[1], _hive(argv[1])
    # /V as often as /v
    lowered = [a.lower() for a in argv]
    at = lowered.index("/v") + 1 if "/v" in lowered else 0
    name = lowered[at] if 0 < at < len(argv) else ""
    if re.search(r"^hku\\s-1-5-(19|20)(\\.*)?$", place):
        # LocalService's hive, which only an admin can open: how a script
        # tells it's been run as one
        if not world.admin:
            io.err.write("ERROR: Access is denied.\n")
            return 1
        io.out.write(f"\n{spelled}\\Environment\n{spelled}\\Software\n\n")
        return 0
    # what this PC has before anything a play gave it, so a value given later
    # never hides one it answered all along, and fills in one it didn't
    built, given = _registry_has(world.stage, place), world.stage.machine.values(spelled)
    wrote = world.written.get(place)
    if wrote is GONE or any(world.written.get(above) is GONE for above in _parents(place)):
        values = None
    else:
        gone = world.unset.get(place, ())
        values = None if built is None and given is None and wrote is None else \
            {**({} if gone is ALL else {k: v for k, v in {**(given or {}), **(built or {})}.items()
                                         if k not in gone}), **(wrote or {})}
    shown = [v for low, v in (values or {}).items() if not name or low == name]
    if values is None or name and not shown:
        io.err.write("ERROR: The system was unable to find the specified registry key or value.\n")
        return 1
    io.out.write(f"\n{spelled}\n" + "".join(
        f"    {n or '(Default)'}    {t}    {hex(d) if isinstance(d, int) else d}\n"
        for n, t, d in shown) + "\n")
    return 0


# a key a play deleted, built-in or given, until it adds it back, and every
# value of a key it cleared with /va or deleted and made again
GONE, ALL = object(), object()


def _parents(place: str) -> list:
    parts = place.split("\\")
    return ["\\".join(parts[:n]) for n in range(1, len(parts))]


def _wrote(world: World, verb: str, argv: list) -> None:
    """a reg add or delete, on the play's own registry"""
    place, low = _hive(argv[1]), [a.lower() for a in argv]

    def after(flag):
        at = low.index(flag) + 1 if flag in low else 0
        return argv[at] if 0 < at < len(argv) else None

    name = "" if "/ve" in low else after("/v")
    if verb == "delete":
        if "/va" in low:
            # every value of the key, which stays, and the ones under it with
            # it. One that isn't there isn't made by clearing it
            there = isinstance(world.written.get(place), dict) or \
                _registry_has(world.stage, place) is not None or \
                world.stage.machine.values(argv[1]) is not None
            if there and not any(world.written.get(each) is GONE
                                 for each in [*_parents(place), place]):
                world.written[place] = {}
                world.unset[place] = ALL
        elif name is None:
            # the key and every one under it
            for under in [k for k in world.written if k.startswith(place + "\\")]:
                del world.written[under]
            world.written[place] = GONE
            world.unset.pop(place, None)
        else:
            held = world.written.get(place)
            if isinstance(held, dict):
                held.pop(name.lower(), None)
            if world.unset.get(place) is not ALL:
                world.unset.setdefault(place, set()).add(name.lower())
        return
    # adding under a key that was deleted puts it, and the ones above it, back,
    # with only what's added since: what it had before went with it
    for each in [*_parents(place), place]:
        if world.written.get(each) is GONE:
            world.written[each], world.unset[each] = {}, ALL
    held = world.written.setdefault(place, {})
    if name is None:
        return
    if world.unset.get(place) is not ALL:
        world.unset.get(place, set()).discard(name.lower())
    kind, data = (after("/t") or "REG_SZ").upper(), after("/d") or ""
    if kind in ("REG_DWORD", "REG_QWORD"):
        try:
            data = int(data, 16) if data.lower().startswith("0x") else int(data)
        except ValueError:
            data = 0
    held[name.lower()] = (name, kind, data)


def _registry_has(stage: Stage, place: str):
    """
    value name, lowercased -> (name, type, data) for the keys this PC has
    from the start: Steam's, the game's under Steam, 7-Zip's and WinRAR's.
    None for any other
    """
    from patcher.mods.game_files import STEAM_APPS

    app = STEAM_APPS.get(stage.game.data, "")
    place = re.sub(r"^(hklm|hkcu)\\software\\(wow6432node\\)?", lambda _: "software\\", place)
    if place == "software\\valve\\steam":
        return {"installpath": ("InstallPath", "REG_SZ", STEAM),
                "steampath": ("SteamPath", "REG_SZ", STEAM),
                "steamexe": ("SteamExe", "REG_SZ", STEAM + "\\steam.exe"),
                "language": ("Language", "REG_SZ", "english"),
                "runningappid": ("RunningAppID", "REG_DWORD", 0)}
    if app and place == f"software\\valve\\steam\\apps\\{app}":
        return {"installed": ("Installed", "REG_DWORD", 1), "running": ("Running", "REG_DWORD", 0),
                "updating": ("Updating", "REG_DWORD", 0),
                "name": ("Name", "REG_SZ", stage.install_dir.name)}
    if app and place == f"software\\microsoft\\windows\\currentversion\\uninstall\\steam app {app}":
        return {"installlocation": ("InstallLocation", "REG_SZ", stage.root),
                "displayname": ("DisplayName", "REG_SZ", stage.install_dir.name),
                "uninstallstring": ("UninstallString", "REG_SZ",
                                    f'"{STEAM}\\steam.exe" steam://uninstall/{app}'),
                "displayicon": ("DisplayIcon", "REG_SZ", STEAM + "\\steam\\games\\"
                                f"{app}.ico"),
                "publisher": ("Publisher", "REG_SZ", "Square Enix"),
                "urlinfoabout": ("URLInfoAbout", "REG_SZ",
                                 f"https://store.steampowered.com/app/{app}/"),
                "nomodify": ("NoModify", "REG_DWORD", 1), "norepair": ("NoRepair", "REG_DWORD", 1)}
    if place == "software\\7-zip":
        return {"path": ("Path", "REG_SZ", "C:\\Program Files\\7-Zip\\"),
                "path64": ("Path64", "REG_SZ", "C:\\Program Files\\7-Zip\\")}
    if place == "software\\winrar":
        return {"exe64": ("exe64", "REG_SZ", "C:\\Program Files\\WinRAR\\WinRAR.exe"),
                "exe32": ("exe32", "REG_SZ", "C:\\Program Files\\WinRAR\\WinRAR.exe"),
                "path": ("Path", "REG_SZ", "C:\\Program Files\\WinRAR")}
    return None


def _refused(what: str):
    """a program that reaches past the game's files: said, not run"""
    def run(world: World, argv: list, text: str, cwd: str, io) -> int:
        if what not in world.left:
            world.left.append(what)
        io.err.write(f"'{argv[0] if argv else what}' is not available here.\n")
        return 1
    return run


def _powershell(world: World, argv: list, text: str, cwd: str, io) -> int:
    if re.search(r"BrowseForFolder|FolderBrowserDialog|OpenFolderDialog", text, re.I):
        # the folder picker installers use to ask where the game is, answered
        # the way a person would answer it
        io.out.write(world.stage.root + "\n")
        return 0
    unzip = _expanding(text, world.stage.env)
    if unzip is not None:
        archive, target = (join(cwd, path) for path in unzip)
        ref = world.ref(archive)
        if isinstance(ref, Nested):
            for member in world.stage.nested.get(ref.member, []):
                world.set(child(target, member[len(ref.member) + 1:].replace("/", "\\")),
                          Dropped(member))
            return 0
        zipped = _zip_of(world, archive)
        if zipped is not None:
            began = time.monotonic()
            with zipped:
                trouble = _unzipped(world, zipped, target, [], False, source=world.ref(archive))
            world.waited(began)
            if not trouble:
                return 0
            # Expand-Archive takes no password and inflates no more than zipfile does
            what = f"unpack {leaf(archive)}, which looks damaged or cut short" \
                if trouble == "damaged" else \
                f"unpack {leaf(archive)}, which needs more free space than the game's drive has" \
                if trouble == "space" else \
                f"unpack {leaf(archive)} with PowerShell, which can't open how it's packed"
        elif world.kind(archive) == "file" and _damaged(world, archive):
            what = f"unpack {leaf(archive)}, which looks damaged or cut short"
        else:
            what = None
        if what is not None:
            if what not in world.left:
                world.left.append(what)
            io.err.write("Expand-Archive : The archive file is not valid.\n")
            return 1
    return _refused("run a PowerShell script")(world, argv, text, cwd, io)


def _expanding(text: str, env: dict):
    """
    (the archive, where to) for PowerShell asked to unzip one: Expand-Archive
    with its parameters by name or by place, -Force and the like left out,
    or .NET's ZipFile.ExtractToDirectory. None for anything else
    """
    text = re.sub(r"\$env:(\w+)", lambda hit: next(
        (v for k, v in env.items() if k.lower() == hit.group(1).lower()), hit.group(0)), text)
    net = re.search(r"ExtractToDirectory\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
                    text, re.I)
    if net:
        return net.group(1), net.group(2)
    hit = re.search(r"Expand-Archive\b([^;|]*)", text, re.I)
    if hit is None:
        return None
    # \" inside a -Command "..." is a quote of its own
    body = hit.group(1).replace('\\"', "'")
    words = [a or b or c for a, b, c in re.findall(r"'([^']*)'|\"([^\"]*)\"|([^\s'\"]+)", body)]
    named, places, n = {}, [], 0
    while n < len(words):
        flag = words[n][1:].lower() if words[n].startswith("-") else None
        if flag in ("path", "literalpath", "lp", "pspath") and n + 1 < len(words):
            named["from"], n = words[n + 1], n + 2
        elif flag is not None and flag.startswith("de") and flag != "debug" and n + 1 < len(words):
            named["to"], n = words[n + 1], n + 2
        else:
            if flag is None and words[n].strip("{}()&"):
                places.append(words[n])
            n += 1
    found = named.get("from") or (places.pop(0) if places else None)
    to = named.get("to") or (places.pop(0) if places else None)
    return (found, to) if found and to else None


def _net(world: World, argv: list, text: str, cwd: str, io) -> int:
    # net session is how a script asks whether it's running as admin
    if argv and argv[0].lower() == "session":
        if world.admin:
            return 0
        io.err.write("System error 5 has occurred.\n\nAccess is denied.\n")
    return 2


def _wmic(world: World, argv: list, text: str, cwd: str, io) -> int:
    if "logicaldisk" in text.lower():
        io.out.write("Name  \r\nC:    \r\n")
        return 0
    return 0


def _quietly(world: World, argv: list, text: str, cwd: str, io) -> int:
    return 0


_TOOLS = {
    "reg": _reg, "net": _net, "net1": _net, "wmic": _wmic, "powershell": _powershell,
    "pwsh": _powershell, "explorer": _quietly, "notepad": _quietly, "write": _quietly,
    "msg": _quietly, "mshta": _refused("show an HTML window"),
    "cscript": _refused("run a script through Windows Script Host"),
    "wscript": _refused("run a script through Windows Script Host"),
    "curl": _refused("download something"), "wget": _refused("download something"),
    "bitsadmin": _refused("download something"), "certutil": _certutil,
    "msiexec": _refused("run an MSI installer"), "regsvr32": _refused("register a dll"),
    "rundll32": _refused("run a dll's function"), "schtasks": _refused("set up a scheduled task"),
    "sc": _refused("set up a Windows service"), "setx": _quietly, "mklink": _quietly,
    # what our FFXIIIwdbLoad hands its .txt to, by a name nothing of theirs can have
    "ffxiiiwdbload.native": _wdbload,
}


# the questions it asks

def menu_of(ask: batch.Ask, keys=None, lines=None) -> dict:
    """key -> what it says, off the lines the script put up before asking"""
    found = {}
    for line in ask.lines if lines is None else lines:
        hit = MENU.match(line)
        if hit:
            found[hit.group(1).upper()] = hit.group(2).strip()
    if keys is not None:
        found = {k: v for k, v in found.items() if k in keys}
    return found


def compared(texts: dict, var: str) -> list:
    """the values a script holds a variable up against, which is what its menu's keys are"""
    if not var:
        return []
    out = []
    name = re.escape(var)
    pattern = re.compile(r'if\s+(?:/i\s+)?(?:not\s+)?["\[\']?[%!]' + name + r'[%!]["\]\']?\s*'
                         r'(?:==|\s+equ\s+)\s*["\[\']?([^"\'\]\s)]+)', re.I)
    for text in texts.values():
        for hit in pattern.finditer(text):
            value = hit.group(1)
            if value not in out:
                out.append(value)
    return out


def path_probes(texts: dict, var: str) -> list:
    """what a script looks for under a folder it's been told, to tell which folder it means"""
    if not var:
        return []
    name = re.escape(var)
    out = []
    for text in texts.values():
        for hit in re.finditer(r'exist\s+"?[%!]' + name + r'[%!]\\?([^"\r\n%!]+?)"?(?:\s|$)',
                               text, re.I):
            out.append(hit.group(1).strip().strip("\\"))
    return [p for p in out if p]


def used_as_path(texts: dict, var: str) -> bool:
    if not var:
        return False
    name = re.escape(var)
    for text in texts.values():
        if re.search(r'[%!]' + name + r'[%!](\\|[\w ()-]*\\|[\w-]+\.(exe|bin|txt))', text, re.I) or \
                re.search(r'(exist|cd\s+(/d\s+)?|pushd)\s+"?[%!]' + name + r'[%!]', text, re.I):
            return True
    return False


class Answers:
    """
    how a person installing answers: the game's folder for a path, carry on
    for a yes or no, the automatic search, the version picked for a menu.
    `plan` is answers already decided, by where they're asked; anything else
    is answered here and its alternatives noted, so a play that installs
    nothing can be tried again another way
    """

    def __init__(self, stage: Stage, plan=None, language=None):
        self.stage = stage
        self.plan = dict(plan or {})
        self.language = language
        self.seen = Counter()
        self.points = []
        self.menus = []
        self.languages = []
        # questions it takes anything typed for, a name say
        self.typed = set()

    @staticmethod
    def where(ask: batch.Ask) -> tuple:
        return (ask.kind, ask.where[0].lower() if ask.where else "",
                ask.where[1] if len(ask.where) > 1 else 0, ask.options, ask.frame)

    def __call__(self, ask: batch.Ask, world: World) -> str:
        spot = self.where(ask)
        self.seen[spot] += 1
        again = self.seen[spot] > 1
        kind, choices = self.read(ask)
        if kind == "language":
            self.languages.append(spot)
            return self._language(ask)
        if kind == "menu":
            if again:
                # back at its menu once what was picked is in: out the way it
                # offers, never through an uninstall, and not round again
                leave = next((k for k, v in choices.items() if EXITING.search(v)), None)
                if leave is None or self.seen[spot] > 2:
                    raise _Done()
                return leave
            installable = [k for k, v in choices.items() if not LEAVING.search(v)]
            self.menus.append((spot, ask, {k: choices[k] for k in installable}))
            if spot in self.plan:
                return self.plan[spot]
            self.points.append((spot, installable))
            return installable[0] if installable else next(iter(choices))
        if self.seen[spot] > 3:
            # asked the same thing over and over: whatever this answers isn't
            # taking, so the play's over
            what = f'"{ask.prompt.strip()}"' if ask.prompt.strip() else \
                f"for {ask.var}" if ask.var else "the same question"
            raise _Done(f"kept asking {what} whatever it was told")
        if spot in self.plan:
            return self.plan[spot]
        if kind == "path":
            folders = self._folders(ask)
            return folders[min(self.seen[spot] - 1, len(folders) - 1)]
        if kind == "binary":
            ordered = _scale(ask) or _carried_on(ask, list(ask.options))
            self.points.append((spot, ordered))
            return ordered[0]
        values = compared(self.stage.texts, ask.var)
        if values:
            # the one someone installing types first, and, last, none of them:
            # the way on past every one it turns away
            ordered = _carried_on(ask, _either(values))
            self.points.append((spot, ordered + [""]))
            return ordered[0]
        # Enter, and when that gets nowhere, something typed: a script that
        # wants your name only minds there being one
        self.points.append((spot, ["", "1"]))
        self.typed.add(spot)
        return ""

    def read(self, ask: batch.Ask) -> tuple:
        """(what kind of question, the menu it offers)"""
        if ask.kind == "choice":
            letters = ask.options
            if _voices(ask, list(letters)):
                return "language", {}
            menu = menu_of(ask, set(letters))
            if len(menu) >= 2 and len(menu) >= len(letters) - 1:
                return "menu", menu
            # or every key named in the question itself: "Install (N)ude
            # Lightning, or (D)efault Lightning?" picks what goes in. One about
            # how to install (find the game, carry on, take it back off) isn't
            said = select_labels(letters, ask.prompt)
            if len(letters) >= 2 and len(said) == len(letters) and set(letters.upper()) != {"Y", "N"} \
                    and not any(STEPS.search(v) or YES_NO.match(v) for v in said.values()):
                return "menu", said
            return "binary", {}
        if used_as_path(self.stage.texts, ask.var):
            return "path", {}
        values = compared(self.stage.texts, ask.var)
        if values and _voices(ask, _either(values)):
            return "language", {}
        menu = menu_of(ask, {v.upper() for v in values} if values else None)
        if len(menu) >= 2:
            return "menu", {k: menu[k] for k in menu}
        return "text", {}

    def _language(self, ask: batch.Ask) -> str:
        """what picks the voice set this play's for, English unless it's c"""
        typed = ask.kind != "choice"
        answers = _either(compared(self.stage.texts, ask.var)) if typed else list(ask.options)
        said = _voice_labels(ask, answers)
        english = next((a for a in answers if _spoken(said[a], "english")), None)
        japanese = next((a for a in answers if a != english and _spoken(said[a], "japanese")),
                        None)
        if english is None:
            english = "E" if "E" in answers else next(
                (a for a in answers if a != japanese), "english" if typed else answers[0])
        if japanese is None:
            japanese = "J" if "J" in answers else next(
                (a for a in answers if a != english), "japanese" if typed else english)
        return japanese if self.language == "c" else english

    def _folders(self, ask: batch.Ask) -> list:
        """the game's folders a path question could mean, the likeliest first"""
        root = self.stage.root
        data = self.stage.game.data
        candidates = [root] + ([child(root, data), child(root, f"{data}\\sys"),
                                child(root, f"{data}\\prog\\win\\bin")] if data else [])
        probes = path_probes(self.stage.texts, ask.var)

        def score(folder):
            return sum(1 for p in probes if key(join(folder, p)) in self.stage.nodes)

        ranked = sorted(candidates, key=lambda f: -score(f))
        return [f.rstrip("\\") + "\\" for f in ranked]


def _meaning(answer: str, text: str) -> str:
    """
    what a question says an answer does: (I)nstall, Y to restore the original
    files, [N] Skip, Install the textures (Y) or skip them (N). (Y)es and (N)o
    say nothing more than the answer does
    """
    a = re.escape(answer)
    # up to the next answer or the end of what it says
    end = r"(?=\s*(?:\bor\b|\band\b|[^a-z' -]|$))"
    glued = re.search(r"[\[(]" + a + r"[\])]([a-z]+(?:[ '-]?[a-z]+)*?)" + end, text, re.I)
    if glued:
        said = answer + glued.group(1)
    else:
        after = re.search(r"(?:[\[(]" + a + r"[\])]\s*(?:to|=|:|-|for)?|\b" + a +
                          r"\b\s*(?:to|=|:|-|for))\s*([a-z][a-z' -]*?)" + end, text, re.I)
        said = after.group(1) if after else ""
        # Voices: 1 (English) or 2 (Japanese)
        named = re.search(r"\b" + a + r"\s*\(\s*([a-z][a-z' -]*?)\s*\)", text, re.I)
        if (not said or _bare(said) or re.match(r"(or|and)\b", said, re.I)) and named \
                and not _bare(named.group(1)):
            said = named.group(1)
        elif not said or _bare(said) or re.match(r"(or|and)\b", said, re.I):
            # the words before it, past the last "or"
            before = re.search(r"([a-z][a-z' -]{2,48}?)\s*[\[(]" + a + r"[\])]", text, re.I)
            said = re.sub(r"^.*\b(or|and)\s+", "", before.group(1), flags=re.I).strip() \
                if before else ""
    return "" if _bare(said) else said


def _bare(said: str) -> bool:
    return bool(re.fullmatch(r"y|yes|n|no|ok", said.split(" ", 1)[0], re.I)) if said else True


# what a question can say about its yes, for finding the one that says it
SAYS = (PUTTING, GOING_BACK, STOPPING, NARROWING, ADDING, WITH_ORIGINAL)


def _asked(text: str) -> str:
    """
    a question without the (y/n), [Y/N] or [Yes/No] after it, and of several
    sentences the question it ends on when that says what it's about: This
    removes the old version first. Install?
    """
    text = re.sub(r"[\[(]\s*\w{1,3}(\s*[/|,]\s*\w{1,3})*\s*[\])]|\b(yes|no)\b", " ", text,
                  flags=re.I)
    last = re.findall(r"[^.!?]*\?", text)
    return last[-1] if last and any(r.search(last[-1]) for r in SAYS) else text


def _scale(ask: batch.Ask) -> list:
    """
    a slider: CHOICE /C 123456789 under "Smaller   Default   Larger", answered
    with the default in the middle, then outward from it. A person installing
    leaves a body or a size where its author set it
    """
    letters = list(ask.options)
    if len(letters) < 3 or not all(c.isdigit() for c in letters) or \
            [int(c) for c in letters] != list(range(int(letters[0]), int(letters[0]) + len(letters))):
        return []
    shown = " ".join(ask.lines[-3:] + (ask.prompt,))
    if not re.search(r"\bdefault\b|\bstandard\b|\bnormal\b", shown, re.I):
        return []
    middle = len(letters) // 2
    return sorted(letters, key=lambda c: (abs(letters.index(c) - middle), letters.index(c)))


def _carried_on(ask: batch.Ask, answers: list) -> list:
    """
    a question's answers in the order someone installing gives them: the one
    it says installs or carries on, then one it says skips, last one it says
    takes the mod back off. A bare y/n is a no first when the question's
    about going back to the game as it was or leaving part of it out:
    Install the new opening? and Restore the cut content too? are a yes,
    Restore the original instead of installing?, Keep the original intro?
    and The opening only, without the textures? are a no. Only an order: a
    play that puts nothing in is played again the other ways, so a question
    read wrong costs a play and never the mod
    """
    lines = [line for line in ask.new[-3:] if line.strip()]
    said = {a: _meaning(a, ask.prompt) for a in answers}
    if not any(said.values()) and lines:
        said = {a: _meaning(a, ". ".join(lines)) for a in answers}
    # a word typed in says what it does itself: install, uninstall, skip
    said = {a: m or (a if len(a) > 1 and not _bare(a) else "") for a, m in said.items()}

    def rank(answer: str) -> int:
        meant = said[answer]
        if GOING_BACK.search(meant):
            return 3
        if STOPPING.search(meant) or NARROWING.search(meant):
            return 2
        return 0 if GOING_ON.search(meant) else 1

    if any(said.values()):
        # a yes over a no it's said as much of
        return sorted(answers, key=lambda a: (rank(a), a.lower() not in ("y", "yes")))
    yes = [a for a in answers if a.lower() in ("y", "yes")]
    no = [a for a in answers if a.lower() in ("n", "no")]
    if not (yes and no):
        return answers
    asked = _asked(ask.prompt)
    if not any(r.search(asked) for r in SAYS):
        asked = _above(lines)
    put = PUTTING.search(asked)

    def before_put(hit) -> bool:
        return hit is not None and (put is None or hit.start() < put.start())

    undoes = bool(WITH_ORIGINAL.search(asked))
    if not undoes and (not ADDING.search(asked) or re.search(r"un-?install", asked, re.I)):
        # Restore the cut content too? is a part on top; Restore the originals? isn't
        undoes = before_put(GOING_BACK.search(asked))
    first = no if undoes or NARROWING.search(asked) or before_put(STOPPING.search(asked)) \
        else yes
    return first + [a for a in answers if a not in first]


def _above(lines: list) -> str:
    """
    for a question that only asks Continue? or (y/n), the nearest question
    above it that says something. Only what was put up since the last
    question is there, and a line that isn't a question says nothing about
    the yes: To uninstall it, run Uninstall.bat isn't a no
    """
    for line in reversed(lines):
        if "?" in line or re.search(r"[\[(]\s*y\s*/\s*n\s*[\])]", line, re.I):
            asked = _asked(line)
            if any(r.search(asked) for r in SAYS):
                return asked
    return ""


def _voices(ask: batch.Ask, answers: list) -> bool:
    """
    whether a question asks which voices the game has, by what its answers
    are called: one English and one Japanese, and any other a way out.
    (E)nglish or (J)apanese, (1) English or (2) Japanese, the two echoed above
    a Choice: with a 3. Exit, EN or JP typed at a voices prompt. A yes or no
    never is: Japanese voices? (y/n) is as often a part of the mod, and so is
    a menu of an English theme song and a Japanese one
    """
    said = _voice_labels(ask, answers)
    english = [a for a in answers if _spoken(said[a], "english")]
    japanese = [a for a in answers if a not in english and _spoken(said[a], "japanese")]
    rest = [a for a in answers if a not in english + japanese]
    if any(not (EXITING.search(said[a]) or STOPPING.search(said[a])) for a in rest) \
            or len(english) > 1 or len(japanese) > 1:
        return False
    if ask.kind == "choice":
        return len(english) == len(japanese) == 1
    # typed: what it checks for, at a prompt that's about the voices
    told = ". ".join([*ask.new[-4:], ask.prompt])
    return bool(english or japanese) and bool(SPOKEN.search(told))


def _voice_labels(ask: batch.Ask, answers: list) -> dict:
    """what each answer to a voices question says it is: 1. English, U = English"""
    told = ". ".join([*ask.new[-4:], ask.prompt])
    menu = menu_of(ask, {a.upper() for a in answers}, ask.new)
    return {a: menu.get(a.upper()) or _meaning(a, told) or a for a in answers}


# how a voices prompt shortens each: (E/J), EN or JP
CODES = {"english": r"e|en|eng|us", "japanese": r"j|jp|ja|jap|jpn"}


def _spoken(label: str, language: str) -> bool:
    """whether an answer's label is that language and nothing else: English, Japanese voices"""
    return bool(re.fullmatch(r"\W*(" + language + r"(\s+(voices?|voice-?over|audio|language|"
                             r"dub(bed|bing)?|speech|version|pack|track|set|only))*\s*"
                             r"(\([^)]*\))?|" + CODES[language] + r")\W*", label, re.I))


def _either(values: list) -> list:
    """
    a yes or no the script only checks one way, with the other answer, which
    is whatever else is typed: if /i "%r%"=="n" goto skip
    """
    low = [v.lower() for v in values]
    if not low or any(v not in ("y", "n", "yes", "no") for v in low):
        return values
    other = {"y": "n", "n": "y", "yes": "no", "no": "yes"}
    more = [other[v] for v in low if other[v] not in low]
    return values + [m.upper() if values[0].isupper() else m for m in dict.fromkeys(more)]


def _play_once(stage: Stage, answers: Answers, dry: bool, cancel=None, on_line=None):
    """(the world it left, the run, why it stopped short or "")"""
    world = World(stage, answers, dry, cancel, on_line)
    run = batch.Run(world, dict(stage.env), parent(stage.bat), check=world.check)
    why = ""
    try:
        run.run(world.script(stage.bat), _argv(stage))
    except _Done as e:
        world.ended = str(e)
    except (batch.BatchError, SandboxError) as e:
        why = _why(e)
    return world, run, why


def _argv(stage: Stage) -> list:
    """
    the .bat and what's dragged onto it, quoted the way Explorer hands them
    over: a path with a space in it
    """
    return [f'"{stage.bat}"', *(f'"{arg}"' if re.search(r"\s", arg) else arg
                                for arg in stage.args)]


def _why(e: Exception) -> str:
    """what stopped a play, to follow "stopped" in a sentence"""
    if isinstance(e, batch.Syntax):
        return f"cmd gave up on one of its lines, saying \"{str(e).rstrip('.')}\""
    return str(e).rstrip(".")


def _settle(stage: Stage, cancel=None) -> tuple:
    """
    (the answers that get it installing, the Answers that heard its
    questions, the world and run of that play, why it stopped short) in plays
    that run none of its programs. The default answers first, then each
    question's other answers in turn, until one puts the mod in or finds it
    in already: dropped again, the answer that installs it changes nothing,
    and is still the one to give. One cmd stopped after its work is kept in
    case nothing finishes cleanly; the answers are None when nothing got that
    far, unless the drawer has the mod in the game whole: then a .bat that
    goes to the end without putting anything new in is finding it there,
    whatever its other answers only take back off, and the default answers
    come back
    """
    tried, queue, seen = 0, [{}], {frozenset()}
    first = stopped = None
    while queue and tried < TRIES:
        # the fewest answers changed first, each way of answering played once
        plan = queue.pop(0)
        answers = Answers(stage, plan)
        world, run, why = _play_once(stage, answers, dry=True, cancel=cancel)
        tried += 1
        first = first or (plan, answers, world, run, why)
        verdict = world.verdict()
        # the default answers finding the mod in already are the ones, even
        # when cmd gives up on a line after: another answer that changes
        # something is another version, or the uninstall
        if verdict == "installs" and not why:
            return _fuller(stage, (plan, answers, world, run, why), cancel, TRIES - tried)
        if verdict == "already" and (not why or tried == 1):
            return plan, answers, world, run, why
        if verdict in ("installs", "already"):
            # cmd giving up on a line after the work's done
            stopped = stopped or (plan, answers, world, run, why)
        for spot, options in answers.points:
            if spot not in plan:
                for option in options[1:]:
                    other = {**plan, spot: option}
                    if frozenset(other.items()) not in seen:
                        seen.add(frozenset(other.items()))
                        queue.append(other)
    if stopped is not None:
        return stopped
    plan, answers, world, run, why = first
    # a stand-in of ours giving up isn't the mod finding itself in
    if stage.in_game and not why and not world.ended and world.refused is None:
        return plan, answers, world, run, why
    return None, answers, world, run, why


def _fuller(stage: Stage, found: tuple, cancel, left: int) -> tuple:
    """
    of the answers that install, the ones that put the most of the mod in:
    each yes or no it answered the first way tried the other, and kept when
    that puts in all the first did and more. Skip the summon animations too?
    and Also skip the 4K movies? read alike, and only what each puts in tells
    them apart. A settings file of the game's written over is never more:
    Keep your current settings? stays a yes
    """
    plan, answers, world = found[:3]
    if world.unseen:
        return found
    have = _put_in(world)
    # a menu's are its versions, and typing none of a script's values is
    # only the way past it
    menus = {spot for spot, _, _ in answers.menus}
    for spot, options in list(answers.points):
        if spot in plan or spot in menus or spot in answers.typed:
            continue
        for option in [o for o in options[1:] if o]:
            if left <= 0:
                return found
            left -= 1
            other = {**found[0], spot: option}
            asked = Answers(stage, other)
            tried, run, why = _play_once(stage, asked, dry=True, cancel=cancel)
            if why or tried.unseen or tried.verdict() != "installs":
                continue
            got = _put_in(tried)
            if _more(stage, got, have):
                found, have = (other, asked, tried, run, why), got
                break
    return found


def _put_in(world: World) -> dict:
    """what a play would put in where, the mod's bytes there already included"""
    return {spot: ref for spot, (ref, kind) in world.puts().items() if kind in ("mod", "same")}


# what's only settings, someone's own when the game has it already
SETTINGS = re.compile(r"\.(ini|cfg|conf|json|xml|ya?ml|toml|txt)$", re.I)


def _more(stage: Stage, got: dict, have: dict) -> bool:
    """whether `got` puts in everything `have` does, the same, and more than settings over theirs"""
    if any(spot not in got or not stage.same(got[spot], ref) for spot, ref in have.items()):
        return False
    return any(spot not in have and not (SETTINGS.search(spot) and "|" not in spot and isinstance(
        stage.nodes.get(key(child(stage.root, spot.replace("/", "\\"))), (None, None))[1], Theirs))
        for spot in got)


def _version_tree(stage: Stage, plan: dict, cancel=None, depth: int = 0, left=None,
                  came=None) -> list:
    """
    (label, plan) for every version its menus offer, a menu in a menu
    included. Past TREE plays the rest of a menu is taken at its word rather
    than played through. `came` gets each one played through's verdict, by
    _planned(plan)
    """
    came = {} if came is None else came
    left = [TREE] if left is None else left
    left[0] -= 1
    answers = Answers(stage, plan)
    world, _, why = _play_once(stage, answers, dry=True, cancel=cancel)
    open_menus = [(spot, ask, offered) for spot, ask, offered in answers.menus
                  if spot not in plan and len(offered) >= 1]
    if not open_menus:
        # what a menu offers is picked by name, originals and all
        came[_planned(plan)] = world.verdict()
        return [("", plan)] if world.verdict() != "nothing" and not why else []
    spot, ask, offered = open_menus[0]
    if len(offered) == 1 or depth >= DEPTH:
        first = next(iter(offered))
        return _version_tree(stage, {**plan, spot: first}, cancel, depth + 1, left, came) \
            if depth < DEPTH else [("", {**plan, spot: first})]
    out = []
    for choice, said in offered.items():
        deeper = [("", {**plan, spot: choice})] if left[0] <= 0 else \
            _version_tree(stage, {**plan, spot: choice}, cancel, depth + 1, left, came)
        for rest, picked in deeper:
            out.append(((_tidy(said) + (", " + rest if rest else "")), picked))
            if len(out) >= VERSIONS:
                return out
    return out


def _planned(plan: dict) -> frozenset:
    return frozenset(plan.items())


def _step(labelled: list, want: str) -> list:
    """
    the next question's answers only, out of every version past `want` (a
    label_key): "Nude Lighting" and "Default Lightning" before boots or bare
    feet, then those with it in front. Four questions are four short lists
    rather than seventeen long ones
    """
    from patcher.mods.game_files import label_key

    depth = len(want.split(", ")) if want else 0
    deepest = max(len(text.split(", ")) for text, _ in labelled)
    while True:
        out, seen = [], set()
        for text, plan in labelled:
            head = ", ".join(text.split(", ")[:depth + 1])
            if label_key(head) not in seen:
                seen.add(label_key(head))
                out.append((head, plan))
        # a question with one answer left isn't asked
        if len(out) > 1 or depth + 1 >= deepest:
            return out
        depth += 1


def _verdict_past_home(stage: Stage, world) -> str:
    """
    World.verdict, leaving out what it wrote in its own folder: unzipped
    inside the game's, that's its installer's scratch (BareLight copies into
    its own chr\\ before ff13tool puts it in), not the mod
    """
    home = (stage.game_rel(parent(stage.bat)) or "").lower().replace("\\", "/")
    if world.unseen > 0 or world.nova_changes():
        return "installs"
    kinds = {kind for path, (_, kind) in world.puts().items()
             if kind and not (home and path.lower().startswith(home + "/"))}
    if "mod" in kinds:
        return "installs"
    return "already" if "same" in kinds else "restores" if "original" in kinds else "nothing"


def _own_verdicts(label, plan: dict, in_game: bool) -> set:
    """
    the verdicts that make a version the game's own rather than the mod.
    Our FFXIIIwdbLoad's pick between a tweak's .txts says which is its
    "(O)riginal", so there putting originals back counts too. One called
    Default or Original something only counts when it writes what the game
    has, since a folder named Original reads as originals whatever's in it
    """
    if any(isinstance(spot, tuple) and WDBLOAD.lower() in spot for spot in plan):
        return {"restores"} if in_game else {"restores", "already"}
    if not in_game and any(OWN.match(part) for part in str(label or "").split(", ")):
        return {"already"}
    return set()


def _tidy(text: str) -> str:
    text = re.sub(r"^\s*(install(ation)?|add|apply|use)\s+", "", text.strip(), flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    return text[:1].upper() + text[1:] if text else text


def play(install_dir, source, members, name: str, version=None, cancel=None, on_line=None,
         progress=None, in_game: bool = False, went_over=None, lenders=None) -> Played | None:
    """
    a drop's installer played on a copy of the game: what it changed laid
    out in a work folder the way the drawer reads a drop, its versions when
    it has some and none is picked, or why it put nothing in. None for a
    drop with no installer to play. `in_game` is the drawer having a mod by
    its name in the game whole, and `went_over` what its record says each of
    that one's files went in over: game path, or index|record, -> (digest,
    size or None), "" for a file it added. `lenders` is the other downloads
    an add-on's .bat may reach into, a Lenders
    """
    from patcher.mods import game_files

    from patcher.mods.game_files import data_dir

    install_dir = Path(install_dir)
    pack = nova_external(source, members)
    if pack is not None:
        # what Nova runs is all a Nova pack has to play, an exe included
        if not pack[1]:
            return None
        if progress is not None:
            progress(0, 100)
        return _play_bat(install_dir, game_files.read_game(install_dir), source, members,
                         pack[1], name, version, cancel, on_line, progress, [],
                         (in_game, went_over), lenders, nova=pack)
    if not any(m.lower().endswith((".bat", ".cmd")) for m, _ in members):
        return None
    game = game_files.read_game(install_dir)
    here = data_dir(install_dir)
    found = find(members, source.read, here.name if here is not None else "")
    if not found:
        return None
    if progress is not None:
        progress(0, 100)
    choices = rivals(found)
    bat = found[0][0]
    if len(choices) > 1:
        named = labels(choices)
        head, _, rest = (version or "").partition(" / ")
        mine = game_files.picked(head, named) if version is not None else None
        if mine is None:
            return Played(bat=bat, versions=list(named))
        bat, version = named[mine], rest or None
    # the likeliest alone: one further down that writes something is as
    # likely the Restore.bat beside an installer that won't run twice
    return _play_bat(install_dir, game, source, members, bat, name, version, cancel, on_line,
                     progress, choices, (in_game, went_over), lenders)


def nova_external(source, members):
    """
    (the folder a Nova Chrysalia pack's in, the installer in its External
    folder Nova would run, or "" for none) for a pack, None for any other drop
    """
    from patcher.mods import game_files

    nova = game_files.nova_config(source)
    if not nova:
        return None
    base = f"{nova['root']}/external/".lower() if nova["root"] else "external/"
    there = {m.lower(): m for m, _ in members}
    run = ""
    for name in NOVA_RUNS if nova["external"] else ():
        run = there.get(base + name, run)
    return nova["root"], run


def _play_bat(install_dir, game, source, members, bat: str, name: str, version, cancel,
              on_line, progress, choices: list, record=(False, None), lenders=None,
              nova=None) -> Played:
    """one of a drop's .bats played, the way play() says. `nova` is nova_external()'s"""
    from patcher.mods import game_files
    from patcher.paths import undo_store_for

    sweep(install_dir)
    (undo_store_for(install_dir) / WORK).mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="bat-", dir=undo_store_for(install_dir) / WORK))
    played = Played(bat=bat)
    try:
        stage = Stage(install_dir, game, source, members, bat, name, folder, *record,
                      lenders=lenders, nova=nova)
        first = None
        # a version picked off a menu only a PC changed for it gets to is
        # picked there, once the plays get that far again
        matched = version is None
        while True:
            plan, answers, world, run, why = _settle(stage, cancel)
            while plan is None and len(stage.machine.done) < ADAPTS:
                # what it gave up on, and when the PC could have had it, the PC
                # with it and the script played again
                reason = diagnose.diagnose(stage, world, run, why)
                first = first or reason
                if not _adapt(stage, reason.adapt):
                    break
                plan, answers, world, run, why = _settle(stage, cancel)
            played.tried = list(stage.machine.tried)
            played.asked = [(a.prompt or a.var, said) for a, said in world.asked]
            played.ran = [name for name, _ in world.ran]
            if plan is None:
                _said(played, _telling(first, diagnose.diagnose(stage, world, run, why)), stage)
                _clear(folder)
                return played
            if world.verdict() in ("nothing", "restores"):
                # dropped again and its .bat found the mod in: one that won't run
                # twice, or only offers to take it back off
                played.problem = "found it in the game already, so there's nothing to put in"
                played.already = True
                _clear(folder)
                return played
            # a menu answered on the way to something that installs is still a
            # choice of versions, whichever way it was answered
            menus = {spot for spot, _, _ in answers.menus}
            came = {}
            every = _version_tree(stage, {k: v for k, v in plan.items() if k not in menus},
                                  cancel, came=came)
            # a pick that comes to what the game has now is the game's own,
            # the "(O)riginal" a tweak offers beside itself or a "(D)efault
            # Lightning", unless it's this mod as it went in last time. One
            # that only puts originals back is what Remove does. Neither's
            # worth asking about. Any other menu's are versions whatever's in,
            # since a mod that's in by hand would read as the game's own
            for text, p in every:
                # one that says it's the game's own and ran a program of theirs
                # the look-ahead couldn't see into is played for real to know
                if _own_verdicts(text, p, stage.in_game) and came.get(_planned(p)) == "installs":
                    stage.fresh()
                    world, _, why = _play_once(stage, Answers(stage, p), dry=False, cancel=cancel)
                    came[_planned(p)] = _verdict_past_home(stage, world) if not why else "installs"
            versions = [(text, p) for text, p in every if came.get(_planned(p)) not in
                        _own_verdicts(text, p, stage.in_game)] or every
            played.current = [text for text, p in versions
                              if text and stage.in_game and came.get(_planned(p)) == "already"]
            labelled = [(text, p) for text, p in versions if text]
            offer = None
            if version is not None:
                text = game_files.picked(version, dict(every))
                mine = dict(every).get(text)
                said = came.get(_planned(mine)) if mine is not None else None
                if mine is not None and said in _own_verdicts(text, mine, stage.in_game):
                    # picked off a list from before this one, or by hand
                    played.already, played.own = True, said
                    _clear(folder)
                    return played
            if version is None:
                if len(labelled) > 1:
                    offer = _step(labelled, "")
                elif versions:
                    plan = versions[0][1]
            else:
                # tidied both sides, since a label read off a menu twice can
                # differ by a \r or a bullet and still be the one picked
                want = game_files.label_key(version)
                exact = dict(labelled).get(game_files.picked(version, dict(labelled)))
                # a menu under the one picked from, only reached on a PC changed
                # for it, comes back to be picked from with the pick in front,
                # and a pick off that plays its first menu's part till it's there
                deeper = [(text, p) for text, p in labelled
                          if game_files.label_key(text).startswith(f"{want}, ")]
                part = next((p for text, p in labelled
                             if want.startswith(f"{game_files.label_key(text)}, ")), None)
                if exact is not None or len(deeper) == 1:
                    plan, matched = exact if exact is not None else deeper[0][1], True
                elif deeper:
                    offer = _step(deeper, want)
                elif part is not None:
                    plan = part
                elif labelled:
                    raise SandboxError(f"{version} isn't one of its versions. Drop it again to "
                                       "pick one.")
                elif versions:
                    plan = versions[0][1]
            if offer is not None:
                played.versions = [text for text, _ in offer]
                played.current = [t for t in played.versions
                                  if any(c == t or c.startswith(t + ", ") for c in played.current)]
                if len(choices) > 1:
                    named = next(k for k, b in labels(choices).items() if b == bat)
                    played.versions = [f"{named} / {t}" for t in played.versions]
                    played.current = [f"{named} / {t}" for t in played.current]
                _clear(folder)
                return played
            languages = _languages(game) if answers.languages else [None]
            # a program of theirs can still get to the real game, by a path the
            # script wrote into a file for it or by looking itself, so any of the
            # game's files changed under a play is said
            before = _footprint(install_dir)
            played.languages = [v for v in languages if v]
            outcomes, world, run, why = _real(stage, plan, languages, max(run.steps, 1), played,
                                              cancel, on_line, progress)
            touched = _changed_under(install_dir, before)
            if touched:
                raise SandboxError(
                    f"one of its programs went and found the game by itself and changed "
                    f"{touched[0]} there directly, so none of it went in from here. If the game "
                    "acts up, have Steam verify its files")
            if outcomes and not matched:
                raise SandboxError(f"{version} isn't one of its versions. Drop it again to "
                                   "pick one.")
            if outcomes or played.problem:
                break
            # the look-ahead couldn't see a program of theirs, which played for
            # real put nothing in: what that came to says why, and the PC can
            # still be changed for it
            reason = diagnose.diagnose(stage, world, run, why)
            first = first or reason
            if len(stage.machine.done) >= ADAPTS or not _adapt(stage, reason.adapt):
                played.tried = list(stage.machine.tried)
                _said(played, _telling(first, reason), stage)
                _clear(folder)
                return played
        if not outcomes:
            _clear(folder)
            return played
        played.work = folder / "out"
        played.wdb = list(dict.fromkeys(txt for world in outcomes for txt in world.wdb))
        notes = _lay_out(stage, outcomes, played.work)
        if stage.trees:
            notes += _lay_nova(stage, outcomes[-1], played.work)
        played.notes += notes
        if not any(p.is_file() for p in played.work.rglob("*")):
            played.problem = ("changed the game in a way that can't go in: "
                              + notes[0][0].lower() + notes[0][1:].rstrip(".")) if notes else \
                ("only changed the game to what it already has, so there's nothing to put in")
            played.already = not notes
            _clear(folder)
            played.work = None
        return played
    except BaseException:
        _clear(folder)
        raise


def _real(stage: Stage, plan: dict, languages: list, steps: int, played: Played, cancel,
          on_line, progress) -> tuple:
    """
    (the plays that changed the game, and the last one's world, run and why
    it stopped short) of the script played for real, once for each voice set
    it asks about
    """
    outcomes, world, run, why = [], None, None, ""
    for n, language in enumerate(languages):
        answers = Answers(stage, plan, language)

        def ticking(count, n=n):
            if progress is not None:
                progress(min(99, int(100 * (n + min(1.0, count / steps)) / len(languages))), 100)

        stage.fresh()
        world = World(stage, answers, dry=False, cancel=cancel, on_line=on_line)
        run = batch.Run(world, dict(stage.env), parent(stage.bat),
                        check=lambda count, w=world, t=ticking: (w.check(count), t(count)))
        why = ""
        try:
            run.run(world.script(stage.bat), _argv(stage))
        except _Done as e:
            world.ended = str(e)
        except batch.Endless as e:
            # a guard of the play's stopped it, not cmd: what it did up to
            # there is half an install, and goes nowhere
            return [], world, run, _why(e)
        except batch.BatchError as e:
            # cmd stopping it after it's done its work leaves that work
            # done, the way it would on the PC
            why = _why(e)
        except SandboxError as e:
            # one of its tools couldn't be run here, and what came before
            # it is half an install. A stand-in of ours that gave up says why
            # in its own words
            if world.refused is not None:
                played.problem, played.detail = world.refused.said, world.refused.detail()
            else:
                played.problem = f"couldn't finish here: {e}"
            return [], world, run, why
        if not world.changes() and not world.nova_changes():
            continue
        if why:
            played.notes.append(f"Its {leaf(stage.bat)} stopped before the end: {why}.")
        outcomes.append(world)
        played.ran += [name for name, _ in world.ran if name not in played.ran]
        played.skipped += [thing for thing in world.left if thing not in played.skipped]
    return outcomes, world, run, why


def _footprint(install_dir: Path) -> dict:
    """every file of the game's, by its size and time, the undo store left out"""
    out = {}
    for folder, dirs, files in os.walk(install_dir):
        if Path(folder) == Path(install_dir):
            dirs[:] = [d for d in dirs if d.lower() != ".patcher-undo"]
        for name in files:
            path = Path(folder) / name
            try:
                info = path.stat()
            except OSError:
                continue
            out[path.relative_to(install_dir).as_posix()] = (info.st_size, info.st_mtime_ns)
    return out


def _changed_under(install_dir: Path, before: dict) -> list:
    after = _footprint(install_dir)
    return sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))


def _clear(folder: Path) -> None:
    from patcher.fileops import clear_tree
    clear_tree(folder)


def _languages(game) -> list:
    """the voice sets the game has, English first, for a .bat that does one at a time"""
    voices = sorted({pair.voice for pair in game.pairs}, key=lambda v: v != "u")
    return voices or [None]


def _said(played: Played, reason, stage: Stage) -> None:
    played.problem, played.kind = reason.said, reason.kind
    played.needs = reason.needs
    played.detail = reason.detail(stage.machine.tried)


def _telling(first, last):
    """
    of what stopped the play as it first was and the last, on a PC changed
    for it, the one that says more: the last got furthest, unless all it did
    there was run to its end. Even then a guess, or how it's made to be run,
    played that way and still idle, isn't it: what was tried says that much
    """
    if first is None or last.kind not in ("idle", "unset") or first.guess \
            or first.kind in ("home", "args"):
        return last
    return first


def _adapt(stage: Stage, adapt) -> bool:
    """
    the PC changed the way a play that gave up wanted it, with the change in
    words for the drawer. False for nothing to change, or one made already
    """
    if not adapt or adapt in stage.machine.done:
        return False
    kind = adapt[0]
    if kind == "game":
        base = adapt[1]
        if not base or any(key(base) == key(r) for r in stage.roots()):
            return False
        stage.alias(base)
        said = f"with the game at {base} too"
    elif kind == "payload":
        _, path, how, member = adapt
        stage.relocate(path, how, member)
        said = f"with its {member.replace('/', chr(92))} where it looked for it"
    elif kind == "lend":
        _, spot, origin, whose = adapt
        if not stage.lend(spot, origin):
            return False
        said = (f"with {MODELS_KIT_NAME}'s tools built in" if isinstance(origin, dict)
                else f"with {leaf(spot)} out of your {whose} download, the mod it goes on top of")
    elif kind == "key":
        _, place, name = adapt
        what = diagnose.key_kind(place)
        if what in diagnose.RUNTIMES:
            # a runtime's key points at this PC's own, so only when it has the
            # one asked for: PythonCore\2.7 never gets Python 3
            want = diagnose.key_version(what, place)
            real = host_runtime_of(what, want)
            if real is None:
                return False
            home = _runtime_home(what, want)
            stage.put(home, Inline(b"MZ"))
            stage.hosts[key(home)] = real
        stage.machine.keys.setdefault(_hive(place), {})[name.lower()] = (
            name, *_registry_value(stage, place, name))
        said = f"with the registry key it checks for, {place}"
    elif kind == "gamefile":
        _, spot, rel = adapt
        node = stage.nodes.get(key(child(stage.root, rel)))
        if node is None or node[1] is None or key(spot) in stage.nodes:
            return False
        stage.put(spot, node[1])
        said = f"with the game's {leaf(rel)} where it looked for it"
    elif kind == "unpacked":
        if getattr(stage, "unpacked_game", False):
            return False
        stage.unpack_game()
        said = "with the game unpacked the way Nova Chrysalia does it"
    elif kind == "home":
        climb = adapt[1] if len(adapt) > 1 else 0
        home, folder = stage.home_for(climb)
        if key(child(home, stage.dropped[1][len(folder):].replace("/", "\\"))) == key(stage.bat):
            return False
        stage.unpack_home(home, folder)
        said = ("from inside the game's folder" if not climb else
                "unzipped into a folder of its own inside the game's")
    elif kind == "path":
        stage.env["PATH"] = stage.env["PATH"].rstrip(";") + ";" + adapt[1]
        said = f"with {leaf(adapt[1])} on the PATH"
    elif kind == "args":
        stage.args = [adapt[1]]
        said = f"with {adapt[2]} dragged onto it"
    elif kind == "plain":
        # the game dragged from a path with no spaces, for a script that
        # quotes %1 itself and so breaks on Explorer's quotes
        plain = "C:\\Games\\" + (re.sub(r"[^A-Za-z0-9]", "", stage.install_dir.name) or "Game")
        moved = [plain + arg[len(stage.root):] if stage.game_rel(arg) is not None and
                 key(arg).startswith(key(stage.root)) else arg for arg in stage.args]
        if moved == stage.args or any(re.search(r"\s", arg) for arg in moved):
            return False
        if not any(key(plain) == key(r) for r in stage.roots()):
            stage.alias(plain)
        stage.args = moved
        what = next((d[2] for d in reversed(stage.machine.done) if d[0] == "args"),
                    "the game's folder")
        stage.machine.tried = [t for t in stage.machine.tried
                               if t != f"with {what} dragged onto it"]
        said = f"with {what} dragged onto it from a folder with no spaces in its path"
    elif kind == "admin":
        stage.machine.admin = adapt[1]
        said = "as administrator" if adapt[1] else "as a user without admin rights"
    elif kind == "windows":
        stage.machine.windows = _windows(adapt[1])
        said = f"on Windows {adapt[1]}"
    elif kind == "runtime-at":
        _, path, runtime, version = adapt
        real = host_runtime_of(runtime, version, path)
        if real is None:
            return False
        stage.put(path, Inline(b"MZ"))
        stage.hosts[key(path)] = real
        said = f"with this PC's {diagnose.RUNTIMES[runtime][0]} where it looked for it"
    else:
        return False
    stage.machine.done.append(adapt)
    stage.machine.tried.append(said)
    return True


def _registry_value(stage: Stage, place: str, name: str) -> tuple:
    """(type, data) a key a script checks for would hold on a PC that had it"""
    what, low = diagnose.key_kind(place), name.lower()
    if what == "framework":
        return ("REG_DWORD", 533320) if low == "release" else \
            ("REG_DWORD", 1) if low in ("install", "installed") else ("REG_SZ", "4.8.09032")
    if what == "vcredist":
        return ("REG_DWORD", 1) if low in ("install", "installed") else \
            ("REG_DWORD", 14) if low == "major" else ("REG_SZ", "v14.40.33810.00")
    if "version" in low:
        return "REG_SZ", {"python": "3.12", "java": "1.8", "directx": "4.09.00.0904",
                          "dotnet": "8.0.8"}.get(what, "1.0")
    if low in ("install", "installed"):
        return "REG_DWORD", 1
    pathlike = not low or re.search(r"install|path|dir|location|folder|home|exe", low)
    if what in diagnose.RUNTIMES and pathlike:
        where = _runtime_home(what, diagnose.key_version(what, place))
        if "home" in low:
            return "REG_SZ", parent(parent(where))
        return "REG_SZ", where if "exe" in low else parent(where) + "\\"
    if what == "game" and pathlike:
        return "REG_SZ", stage.root
    if what == "directx" and pathlike:
        return "REG_SZ", WINDOWS + "\\System32"
    return "REG_SZ", "1"


def _windows(asked: str) -> str:
    """the whole version ver says for a Windows a script checks for by its first numbers"""
    builds = {"6.1": "6.1.7601", "6.2": "6.2.9200", "6.3": "6.3.9600", "10.0": "10.0.19045.5011"}
    return builds.get(asked, asked)


def _lay_out(stage: Stage, worlds: list, out: Path) -> list:
    """
    what the plays left the game's files as, laid out the way the drawer
    reads a drop: a record that changed under its archive's unpack folder, a
    loose file under its place in the game. Back come the reasons for what
    can't go in
    """
    from patcher.mods import game_files

    notes, merged, clashed = [], {}, set()
    for world in worlds:
        for rel, ref in world.changes().items():
            low = rel.lower()
            if low in merged and merged[low][1] != ref:
                # each language's play made it differently, the one file both
                # sets share: nothing says which is right
                notes.append(f"{rel.rsplit('/', 1)[-1]} came out different for each language, "
                             "so it stayed as the game has it.")
                clashed.add(low)
            merged[low] = (merged.get(low, (rel,))[0], ref)
    for low in clashed:
        merged.pop(low)
    out.mkdir(parents=True, exist_ok=True)
    halves = set()
    for index, archive, pair in stage.pairs.values():
        index_ref = merged.get(index.rel.lower(), (None, index))[1]
        archive_ref = merged.get(archive.rel.lower(), (None, archive))[1]
        halves.update({index.rel.lower(), archive.rel.lower()})
        if index_ref == index:
            # the index still says what it said, so whatever went on the end of
            # the archive is bytes nothing points at
            continue
        if index_ref is None or archive_ref is None:
            notes.append(f"It deleted {leaf(index.rel)} or {leaf(archive.rel)}, which stayed.")
        elif isinstance(index_ref, Listing):
            if index_ref.base != index:
                notes.append(f"It put another index in place of {leaf(index.rel)}, which "
                             "stayed.")
            else:
                notes += _lay_records(stage, index_ref, archive_ref, pair, out)
        elif any(isinstance(r, (Listing, Blob, Broken, Patched, Nested))
                 for r in (index_ref, archive_ref)):
            notes.append(f"It rewrote {leaf(index.rel)} and {leaf(archive.rel)} in a way that "
                         "can't be taken apart, so they stayed.")
        else:
            # an index and archive of its own over the game's, which the drawer
            # takes apart record by record like any dragged in by hand
            for rel, ref in ((index.rel, index_ref), (archive.rel, archive_ref)):
                stage.lay(ref, out.joinpath(*rel.split("/")))
    # a record a Nova installer found unpacked is as much the game's as a file of it
    original = {k: ref for k, (_, ref) in stage.nodes.items()
                if isinstance(ref, (Theirs, Record))}
    # what the drop's files were worked into: an archive, a join or a patch,
    # one of the game's files copied over, and the records an index took
    used = {inner for _, ref in merged.values() for inner in _inside(ref)}
    over = {ref for rel, ref in merged.values()
            if key(child(stage.root, rel.replace("/", "\\"))) in original}
    records = {record.lower() for _, ref in merged.values() if isinstance(ref, Listing)
               for record, _ in ref.changes}
    # and the versions it passed over: a file of its own beside one it took,
    # the same path but for one folder, Hair\long\x.trb to Hair\short\x.trb
    taken = {twin for path, ref in stage.overlay.values() if ref in used or ref in over
             for twin in _twins(stage.game_rel(path))}
    for low, (rel, ref) in sorted(merged.items()):
        if low in halves:
            continue
        name = rel.rsplit("/", 1)[-1]
        spot = key(child(stage.root, rel.replace("/", "\\")))
        was = original.get(spot)
        if ref is None:
            if was is not None:
                notes.append(f"It deleted the game's {name}, which stayed.")
            continue
        if was is None and (isinstance(ref, Theirs) and (
                STASH.search(rel) or not rel.lower().endswith((".dll", ".asi")))
                or isinstance(ref, Record) and _record_of(rel, {ref.path.lower()})
                or JUNK.search(rel)):
            # a copy of one of the game's own files is its backup, and of a
            # record where the record sits its unpack: d3d9_chain.dll beside
            # a proxy and a record copied to another's place are the mod's. A
            # log or a readme is nobody's
            continue
        if was is None and (ref in used or _record_of(rel, records)):
            # what it was worked into is what goes in, not what it worked
            # from: a payload, a copy staged for an inject
            continue
        if was is None and spot in stage.overlay and stage.overlay[spot][1] == ref and (
                ref in over or STASH.search(rel) or set(_twins(rel)) & taken):
            # what it came unpacked over the game with and put to use, the
            # originals it keeps for its uninstall, and the versions it
            # didn't pick. The rest, a loader's settings say, is the mod's
            continue
        if was is not None and stage.same(ref, was):
            # written over with what it already was
            continue
        if isinstance(ref, (Listing, Blob, Broken)):
            notes.append(f"It left {name} part way through a rebuild, so that stayed.")
            continue
        if game_files.ZONE_BLOB.match(name.lower()):
            notes.append(f"It changed {name}, a zone's archive, which only the HD Project's "
                         "install puts in.")
            continue
        try:
            stage.lay(ref, out.joinpath(*rel.split("/")))
        except Opaque as e:
            notes.append(f"{name} stayed as it was: {e}.")
    return notes


def _lay_nova(stage: Stage, world: World, out: Path) -> list:
    """
    a Nova pack's own folders as its installer left them, laid out with what
    it did to the game. Nova installs Data after the installer runs, so a file
    both put somewhere is Data's, and EN-Data and JP-Data before, so there it's
    the installer's. Code goes along for its code patches. Back come the
    reasons for what can't go in
    """
    trees = {key(tree) + "\\": tree for tree in stage.trees}
    notes = []
    for k, (spelled, ref) in world.nodes.items():
        tree = next((t for low, t in trees.items() if k.startswith(low)), None)
        if ref is None or tree is None:
            continue
        name, rel = leaf(tree).lower(), _plain(spelled[len(tree) + 1:].split("\\"))
        # Data\\ is whitepath\\, so its place in the game is the one
        loose = out.joinpath(stage.game.data, *rel)
        try:
            if name == "data":
                stage.lay(ref, loose)
            elif name == "code" or not loose.is_file():
                stage.lay(ref, out.joinpath(leaf(tree), *rel))
        except Opaque as e:
            notes.append(f"{leaf(spelled)} stayed as it was: {e}.")
    return notes


def _backup_of(rel: str, spot: str) -> bool:
    """whether a game file's a backup of the one at `spot`: op.win32.wmp.bak, backup/op.win32.wmp"""
    rel, spot = rel.lower(), spot.lower()
    if rel.startswith(spot):
        return True
    return rel.rsplit("/", 1)[-1] == spot.rsplit("/", 1)[-1] and _keeps(rel)


def _record_of(rel: str, records: set) -> bool:
    """whether a file's place in the game ends in the path of a record an index took"""
    parts = rel.lower().split("/")
    return any("/".join(parts[n:]) in records for n in range(1, len(parts)))


def _twins(rel: str) -> list:
    """a file's path with each of its folders in turn as a wildcard"""
    parts = rel.lower().split("/")
    return [(n, *parts[:n], *parts[n + 1:]) for n in range(len(parts) - 1)]


def _inside(ref) -> list:
    """the references a rebuilt archive, a join or a patch was made from"""
    if isinstance(ref, Listing):
        return [inner for _, inner in ref.changes]
    if isinstance(ref, Blob):
        return [*ref.extra, *(inner for _, inner in ref.contents)]
    if isinstance(ref, Joined):
        return list(ref.parts)
    if isinstance(ref, Patched):
        return [ref.base, ref.patch]
    return []


def _lay_records(stage: Stage, listing: Listing, archive_ref, pair, out: Path) -> list:
    """
    the records an index's changes point at, under a folder named the way
    ff13tool unpacks that archive, so the drawer reads which voice set it is.
    Only those the archive beside it really holds: an index swapped without
    its archive is a game that can't load them
    """
    index, archive = listing.base, stage.archive_of(listing.base)
    blob = stage.blob(archive_ref)
    if blob is None or archive_ref == archive:
        return [f"It changed what {leaf(index.rel)} lists without changing "
                f"{leaf(archive.rel)} to match, so neither went in."]
    kind = re.match(r"img2?|scr", pair.kind).group(0)
    if stage.game.data == "weiss_data":
        # LR's Japanese release has no letter of its own, and c is how a
        # folder says Japanese voices
        letter = {"j": "c"}.get(pair.kind[len(kind):], pair.kind[len(kind):])
    else:
        letter = pair.voice
    folder = out / f"white_{kind}{letter}"
    built = dict(blob.contents) if blob.base is None else None
    notes = []
    for record, ref in listing.changes:
        held = built.get(record) == ref if built is not None else \
            blob.base == archive and ref in blob.extra
        if not held:
            notes.append(f"{record.rsplit('/', 1)[-1]} isn't in the archive its index points "
                         "into, so it stayed.")
            continue
        try:
            data = stage.read(ref)
        except Opaque as e:
            notes.append(f"{record.rsplit('/', 1)[-1]} stayed as it was: {e}.")
            continue
        try:
            if data == stage.read(Record(index, archive, record)):
                continue
        except Exception:
            pass
        target = folder.joinpath(*record.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return notes
