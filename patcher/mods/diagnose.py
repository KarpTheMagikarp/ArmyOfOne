"""
Why a .bat played on a copy of the game put nothing in, said so a person can
act on it, and what the copy's PC could change to get the script past it.

A script that gives up nearly always gives up on a check: a file that isn't
where it looks, a registry key, a program that isn't installed, a variable its
search never set, a question it keeps asking. batch.py keeps every check and
every program a play ran (Run.trace), so walking back from where the script
stopped finds the one that sent it off, which says far more than the
"Exiting..." it printed on the way out. What it printed is read too, its own
words and its programs': a Python traceback, .NET naming the runtime it
wants, a missing DLL, 7-Zip finding an archive broken. A program's exit code
is read the way Windows means it, 0xC0000135 being a DLL that isn't on this
PC, and the program's own imports say which one.

When the cause is something the PC could have had, the answer carries an
`adapt` for sandbox.py to apply before playing it again: the registry key it
checks, the game mounted where the script looks for it, a file of the
download found in another folder, admin taken away from a script that
refuses it, the Windows version it checks for.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from patcher.mods.batch import Ran, Step, child, join, key, leaf, norm, parent

# programs a script expects on the PC: what each is called, what it goes by
# on a command line, where its installer puts it, what asking its version
# prints, and how someone gets it
RUNTIMES = {
    "python": ("Python", ("python", "python3", "py", "pythonw"),
               "C:\\Program Files\\Python312\\python.exe", "Python 3.12.6",
               "Install Python 3 from python.org, with \"Add python.exe to PATH\" ticked"),
    "java": ("Java", ("java", "javaw"), "C:\\Program Files\\Java\\jre-1.8\\bin\\java.exe",
             'java version "1.8.0_421"', "Install Java from java.com"),
    "node": ("Node.js", ("node",), "C:\\Program Files\\nodejs\\node.exe", "v20.17.0",
             "Install Node.js (the LTS one) from nodejs.org"),
    "dotnet": (".NET", ("dotnet",), "C:\\Program Files\\dotnet\\dotnet.exe", "8.0.401",
               "Install the .NET runtime from dotnet.microsoft.com"),
}
# and every name one goes by, npm being Node's too though it's no program of its own
NAMES = {name: runtime for runtime, (_, names, *_) in RUNTIMES.items() for name in names}
NAMES.update(npm="node", npx="node")

# the redistributables a DLL comes with, off its name
DLLS = [
    (r"^(msvcp|vcruntime|concrt|vccorlib|mfc)140(_\w+)?\.dll$",
     "the Microsoft Visual C++ 2015-2022 Redistributable"),
    (r"^(msvc[pr]|mfc)120\.dll$", "the Microsoft Visual C++ 2013 Redistributable"),
    (r"^(msvc[pr]|mfc)110\.dll$", "the Microsoft Visual C++ 2012 Redistributable"),
    (r"^(msvc[pr]|mfc)100\.dll$", "the Microsoft Visual C++ 2010 Redistributable"),
    (r"^(msvc[pr]|mfc)90\.dll$", "the Microsoft Visual C++ 2008 Redistributable"),
    (r"^(msvc[pr]|mfc)80\.dll$", "the Microsoft Visual C++ 2005 Redistributable"),
    (r"^mscoree\.dll$", "the .NET Framework"),
    (r"^(d3dx9_\d+|d3dx10_\d+|d3dx11_\d+|xinput1_[123]|xaudio2_[0-7]|x3daudio1_\d|"
     r"d3dcompiler_(3\d|4[0-3]))\.dll$", "the DirectX End-User Runtime (June 2010)"),
    (r"^openal32\.dll$", "OpenAL"),
]
# and what's always on Windows, so never the one missing
WINDOWS_DLLS = re.compile(r"^(kernel32|user32|gdi32|advapi32|shell32|ole32|oleaut32|comdlg32|"
                          r"comctl32|shlwapi|ws2_32|winmm|version|ntdll|msvcrt|crypt32|bcrypt|"
                          r"rpcrt4|setupapi|imm32|winspool\.drv|uxtheme|dwmapi|d3d9|d3d11|dxgi|"
                          r"opengl32|psapi|iphlpapi|userenv|wininet|winhttp|secur32|dbghelp|"
                          r"api-ms-win-[\w-]+|ucrtbase)(\.dll)?$", re.I)

# a program's exit code as Windows means it, read unsigned
DLL_NOT_FOUND, DLL_INIT, BAD_IMAGE = 0xC0000135, 0xC0000142, 0xC000007B
CLR_CRASH = 0xE0434352
CRASHES = {0xC0000005: "crashed (an access violation)", 0xC0000409: "crashed",
           0xC00000FD: "crashed (a stack overflow)", 0xC0000374: "crashed (heap corruption)",
           0xC000001D: "crashed (an instruction this CPU doesn't have)",
           0x80000003: "crashed", 0xC0000417: "crashed"}
DENIED = 0xC0000022
# .NET's own launcher, when the runtime the program was built for isn't there
DOTNET_HOST = range(0x80008081, 0x800080B0)

# what a line a script or one of its programs printed says went wrong
SIGNS = [
    (r"No module named '([\w.]+)'", "pymodule"),
    (r"You must install (or update )?\.NET|framework '([^']+)', version '([^']+)'|"
     r"It was not possible to find any compatible framework version|"
     r"\.NET (Framework |Desktop Runtime |Runtime )?v?(\d[\d.]*)? ?(is )?(required|not installed|"
     r"missing)|requires? (the )?\.NET( Framework| Desktop Runtime| Runtime)?( v?\d[\d.]*)?",
     "dotnet"),
    (r"\b((msvcp|vcruntime|msvcr|mfc)\d+\w*|d3dx9_\d+|xinput1_\d|d3dcompiler_\d+)\.dll\b", "dll"),
    (r"Unable to access jarfile|Could not find or load main class|"
     r"\bjava(\.exe)? (is )?(not (found|installed)|required|missing)|JAVA_HOME|"
     r"requires? Java", "java"),
    (r"\bpython(\.exe)? (was )?not found|\bpython (is )?(required|not installed|missing)|"
     r"requires? python|Python \d(\.\d+)? (or (newer|later|higher|above) )?is required", "python"),
    (r"\bnode(\.js|\.exe)? (is )?(not (found|installed)|required)|requires? node", "node"),
    (r"not enough (free )?(disk )?space|There is not enough space|disk (is )?full|"
     r"No space left", "disk"),
    (r"being used by another process|in use by another (program|process)", "inuse"),
    (r"Can ?not open (the )?file as (an )?archive|\bData Error\b|CRC Failed|Headers Error|"
     r"Unexpected end of (archive|data)|is not RAR archive|checksum error|"
     r"archive (is )?(corrupt|damaged|broken)|Unsupported compression method", "archive"),
    (r"Wrong password|Enter password|password (is )?(required|incorrect)", "password"),
    (r"The system cannot find the batch label specified - (\S+)", "label"),
    (r"(the )?(game|ff ?x{0,2}iii(-2)?|final fantasy[\w -]*|lightning returns)[\w \\/:.()'-]{0,60}?"
     r"\b(not found|couldn'?t be found|could not be found|isn'?t installed|not installed|"
     r"wasn'?t found|is missing)|could(n'?t| not) (find|locate|detect) (the |your )?(game|ff ?x|"
     r"final fantasy|steam)", "game"),
    (r"(wrong|unsupported|incompatible|different|old|outdated) (game )?version|"
     r"version (mismatch|is not supported|not supported)|update (your |the )?game|"
     r"not the (right |correct |same |supported )?version", "version"),
    (r"(please )?(extract|unzip|unpack) (all |everything|the (whole )?(archive|zip|files?|mod)|"
     r"(it|this|the mod) first)|(not|isn'?t|wasn'?t) (been )?(fully )?extracted", "unzip"),
    (r"run (this |it |the \S+ )?as (an )?admin|(administrator|admin|elevated) (rights|privileges|"
     r"permissions) (are )?(required|needed)|requires? (admin|administrator|elevation)", "admin"),
    (r"Access is denied|Permission denied|access to the path .* is denied", "denied"),
]
SIGNED = [(re.compile(pattern, re.I), kind) for pattern, kind in SIGNS]

# what a registry key is, off where it is
KEYS = [
    (r"\\python(core)?\\|\\python$", "python"),
    (r"\\javasoft\\|\\javasoft$", "java"),
    (r"net framework setup\\ndp", "framework"),
    (r"\\dotnet\\", "dotnet"),
    (r"\\vc\\runtimes|vcredist|visualstudio\\\d+\.0\\vc", "vcredist"),
    (r"\\directx", "directx"),
    # Square Enix's own install record and Steam's uninstall entry for one of
    # the three, never a mod tool's key that has the game's name in it
    (r"\\square ?enix\\(final fantasy|lightning returns|ff ?x|ff ?13)|"
     r"\\uninstall\\steam app (292120|292140|345350)$|"
     r"\\valve\\steam\\apps\\(292120|292140|345350)$", "game"),
    (r"\\node\.?js", "node"),
]
# the three by their data folders, the way Steam names them
TITLES = {"white_data": "FINAL FANTASY XIII", "alba_data": "FINAL FANTASY XIII-2",
          "weiss_data": "LIGHTNING RETURNS: FINAL FANTASY XIII"}
READ_AS = {"python": "Python's", "java": "Java's", "framework": "the .NET Framework's",
           "dotnet": ".NET's", "vcredist": "the Visual C++ Redistributable's",
           "directx": "DirectX's", "game": "the game's", "node": "Node.js's"}

# folders a script looks in for something installed, by the program they hold
PROGRAMS = [
    (r"\\python\d*\\|\\python\d*$", "python"), (r"\\java\\|\\jre|\\jdk", "java"),
    (r"\\nodejs\\|\\nodejs$", "node"), (r"\\dotnet\\|\\dotnet$", "dotnet"),
]

class _Argued:
    """
    %1, %~1, %~dp1 with a name straight after it, %1%: what was dragged onto
    the script, read off a line left to right the way cmd reads it, so %p1%
    is a variable, %%1 a FOR's and %foo%1 foo's value with a 1 on the end
    """

    TOKENS = re.compile(r"%%|%~[fdpnxsatz]*(?:\$[^:%\s]+:)?(\d)|%([\d*])|%[^%\r\n]+%", re.I)

    def search(self, text: str):
        for hit in self.TOKENS.finditer(text or ""):
            if (hit.group(1) or hit.group(2) or "0") in "123456789":
                return hit
        return None


ARGUED = _Argued()

# checks a script makes to tell whether it's running as admin, and the
# registry hive only an admin can open
ADMIN = re.compile(r"^(net1?|fltmc|openfiles|fsutil|cacls|icacls|whoami)$", re.I)
ADMIN_HIVE = re.compile(r"\b(hku|hkey_users)\\s-1-5-(19|20)\b", re.I)

# lines that say nothing about why: a goodbye, a banner's rules, a pause
FAREWELL = re.compile(r"^[\W_]*((exit(ing)?|quit(ting)?|closing|thanks?|press any key)\b.*|"
                      r"(good ?)?bye|done|finished|the end|end|abort(ing|ed)?|"
                      r"cancel+(ing|ed)?)[\W_]*$", re.I)
# and one that says what went wrong in the script's own words. "Installing,
# please wait..." doesn't
EXPLAINS = re.compile(r"\b(error|fail(ed|ure|s)?|missing|unable|cannot|invalid|wrong|requires?|"
                      r"make sure|not (found|installed|detected|supported|valid|there)|"
                      r"\w+n't|no (game|file|folder|such)|run (this |it )?as admin\w*|drag|"
                      r"please (install|run|put|place|copy|move|extract|unpack|select|download|"
                      r"make|close|check|verify|update)|you (need|must|have) to)\b|^\W*install\b|"
                      r"\b(set ?up|install|run|extract|unzip|unpack|download|close|start|launch|"
                      r"update|enable)\b[^.!?]*\bfirst\b", re.I)
# cmd's own complaints, which say a file's missing but not which: what it
# went looking for says that
STOCK = re.compile(r"^(the system cannot find the (file|path) specified|file not found - |"
                   r"could not find |\d+ file\(s\) copied|the syntax of the command is incorrect|"
                   r"access is denied\.?$|error: the system was unable to find the specified "
                   r"registry key)", re.I)
# what the play itself says for a program it doesn't run, which the script didn't
OURS = re.compile(r"^'[^']*' is not available here\.$|^\S+ can't open that here$")
# a .NET program reading a key when there's no console to read one from: the
# end of a tool that finished and waits for any key, which isn't its failing
KEYLESS = re.compile(r"Cannot read keys when either application does not have a console|"
                     r"Console input has been redirected|EOFError: EOF when reading a line|"
                     r"Input past end of file|Unhandled exception\. System\.InvalidOperationException:"
                     r" Cannot read keys", re.I)
FINISHED = re.compile(r"\b(done|finished|complete(d)?|success(ful(ly)?)?|patched|installed|"
                      r"all (files )?(have been )?(written|copied|imported))\b", re.I)
# and what says it didn't, whatever else the line says: "does not match",
# "not a valid", "Nothing was patched", "No game selected"
WENT_WRONG = re.compile(r"\b(errors?|fail(ed|ure|s)?|not|nothing|couldn'?t|can'?t|cannot|unable|"
                        r"invalid|wrong|missing|abort(ed)?|unsupported|denied|isn'?t|wasn'?t|"
                        r"doesn'?t|didn'?t|won'?t|aren'?t|no (such|match(es)?|changes?|valid)|"
                        r"no\b[^!?]{0,30}?\b(files?|selected|found|detected|given|chosen|"
                        r"specified)|0 (files? )?((was|were|has been|have been) )?(patched|copied|"
                        r"changed|written|modified|installed|replaced|updated)|0 of [1-9]\d*)\b",
                        re.I)
# never a tally that says all went well, "No errors found", "0 failed",
# "Errors: 0", what it tells you to do, "Do not move the game folder", what
# to do if, "If the game doesn't start, verify its files", nor a note that
# all's well: "Nothing else to do", "Your saves will not be touched", "Your
# original isn't deleted", "Not needed for the Steam version", "already
# patched". A note only to the end of its clause, so a failure after a comma
# or a colon is still one, "Nothing else to patch: 2 files failed", where an
# instruction runs on through why: "Do not delete Backup, or you won't..."
CLAUSE = r"[^.!?;:,]*"
FINE = re.compile(r"\b(no|0|zero|without( any)?)\s+(errors?|failures?|failed|problems?|issues?|"
                  r"warnings?)\b|\b(errors?|fail(ed|ures?)?)\s*[:=]\s*0\b|"
                  r"\bnothing (went )?wrong\b|(^|[.!:]\s*|\bplease\s+)(do not|don'?t|never)\b"
                  r"[^.!?]*|\bif\b[^,.!?]*,|\bnothing (else |more )?(to do|left to do)\b|"
                  r"\bnothing (else|more)\b" + CLAUSE + r"|"
                  r"\b(won'?t|will not|doesn'?t|does not|didn'?t|did not|never)\s+(touch|affect|"
                  r"harm|delete|overwrite|modify|change|remove)\s+(your|any of your)\b" + CLAUSE +
                  r"|\b(is|are|was|were|has been|have been|will|would|won'?t|wouldn'?t)"
                  r"(\s+not|n'?t)?\s+(be\s+)?(deleted|lost|overwritten|touched|harmed)\b|"
                  r"\b(not|isn'?t|aren'?t)\s+(required|needed|necessary)\b|\bno need\b" + CLAUSE +
                  r"|\b(don'?t|doesn'?t|do not|does not)\s+(need|require|have to)\b" + CLAUSE +
                  r"|\balready (been )?(patched|installed|applied|done|up to date)\b|"
                  r"\bnot (compressed|encrypted)\b", re.I)


def went_wrong(line: str) -> bool:
    """whether a line a program printed says it failed, a tally of no errors aside"""
    return bool(WENT_WRONG.search(FINE.sub(" ", line)))

IDLE = "played to the end on a copy of the game without changing it"
UNDID = "only puts the game's own files back"
# the reasons that are a .bat getting to its end with nothing gone wrong, only
# nothing changed, which is all a .bat around a 4GB patcher ever does
IDLING = {"idle", "said", "payload", "elsewhere", "made", "unset", "gamefile", "game", "setting",
          "content"}


@dataclass
class Why:
    said: str                       # what follows "its install.bat"
    fix: str = ""                   # what the person can do about it
    seen: list = field(default_factory=list)    # the lines that show it
    adapt: tuple | None = None      # what the PC could change to get past it
    kind: str = "unknown"
    sure: bool = False              # a cause worked out, not only what it said last
    guess: bool = False             # tried for want of anything better, never the last word
    needs: str = ""                 # the deps.py base mod it's an add-on for, when that's why

    def detail(self, tried=()) -> str:
        """the rest, for behind the drawer's (i): what to do, what was tried, what showed it"""
        parts = [self.fix] if self.fix else []
        if tried:
            parts.append("Played it again " + _listed(list(tried)) + ", and it still put "
                         "nothing in.")
        block = []
        for line in self.seen:
            # each heading starts a paragraph of its own
            if block and (line.endswith(":") or line.startswith(("It said:", "Where it stopped:"))):
                parts.append("\n".join(block))
                block = []
            block.append(line)
        if block:
            parts.append("\n".join(block))
        return "\n\n".join(p for p in parts if p)


# the net under the reading back, which the tests take away so a bug in it
# fails them rather than reading as a script that ran to its end
NET = True


def diagnose(stage, world, run, stopped: str) -> Why:
    """why a play that put nothing in went nowhere, the most telling reason first"""
    if not NET:
        return _diagnose(stage, world, run, stopped)
    try:
        return _diagnose(stage, world, run, stopped)
    except Exception:
        # a script odd enough to trip the reading back still gets its last
        # words said, rather than the drop failing over the explanation
        lines = [line.strip() for line in run.tail(6)
                 if line.strip() and not FAREWELL.match(line.strip())]
        return Why(f"{IDLE}, and the last thing it said was \"{_cut(lines[-1])}\"" if lines
                   else IDLE, kind="idle", fix=IDLE_FIX, seen=_shown(lines))


def _diagnose(stage, world, run, stopped: str) -> Why:
    if getattr(world, "refused", None) is not None:
        return world.refused
    if world.verdict() == "restores":
        return Why(UNDID, kind="restores", sure=True,
                   fix="That's its uninstaller. The mod's installer is another .bat in the "
                       "download, or the files go in on their own: drop the folder they're in.")
    gone = [rel for rel, ref in world.changes().items() if ref is None]
    if gone:
        return Why(f"deletes the game's {gone[0].rsplit('/', 1)[-1]}, which can't go in from "
                   "here, and puts nothing in", kind="deletes", sure=True,
                   fix="A mod that only takes files away can't be undone from the Installed "
                       "list, so it isn't put in. Removing that file by hand is how its "
                       "readme would have it done.")
    lines = said_lines(run, world)
    shown = _shown(lines)
    for left in world.left:
        # an archive nothing here unpacked says more than whatever it skipped first
        for pattern, kind, said, fix in UNPACKED:
            hit = re.match(pattern, left)
            if hit:
                return Why(said.format(hit.group(1)), kind=kind, sure=True, seen=shown,
                           fix=fix.format(hit.group(1)))
    # a registry setting it writes is skipped, and seldom what stopped it
    blocked = [left for left in world.left if left != SETTING]
    if blocked:
        return Why(f"needs to {blocked[0]}, and that isn't done here", kind="outside",
                   sure=True, seen=shown, fix=_outside_fix(blocked[0]))
    if stopped:
        return _stopped(stage, world, run, stopped, shown)
    if world.ended:
        return Why(world.ended, kind="input", sure=True, seen=shown,
                   fix="It wants an answer that isn't any it checks for, so there's nothing to "
                       "work it out from here. Its readme or mod page should say what to type.")
    step = decisive(run)
    found = _missing_program(stage, run)
    if found is not None:
        return found
    failed = _program_failed(stage, world)
    checked = _check_why(stage, world, run, step) if step is not None else None
    if checked is None and run.unargued and failed is None:
        # it read a %1 it was never given and went nowhere, without a check
        # that left on it: "if not "%~1"=="" goto start" falling through. Only
        # a guess, as an optional /silent reads %1 too
        checked = _dragged(stage, guess=True)
    own = _own_words(lines, world)
    # a check it gave up on after one of its programs failed is that program's doing
    if failed is not None and (checked is None or checked.kind in ("made", "unknown", "unset")):
        failed.adapt = checked.adapt if checked is not None else None
        return _with_words(failed, own)
    if checked is not None and checked.sure:
        return _with_words(checked, own)
    signed = _signs(lines)
    if signed is not None:
        signed.adapt = checked.adapt if checked is not None else None
        return signed
    opened = _opened(stage, run)
    if opened is not None:
        # a readme opened on the way doesn't make what it checked any less
        # worth giving it
        opened.adapt = checked.adapt if checked is not None else None
        return _with_words(opened, own)
    if own:
        # its words go first, with what it checked behind them as the
        # evidence, or a file of its download it looked for and went without
        behind = checked if checked is not None else _missed(stage, world, run)
        return Why(f"said \"{own}\"", kind="said", seen=shown,
                   adapt=behind.adapt if behind is not None else None,
                   fix=f"It {behind.said}. {behind.fix}" if behind is not None else
                   "That's its own reason. Its readme may say more about what it needs.")
    # after its words: a log it leaves on the desktop on its way to failing
    # isn't what it's for
    put = world.elsewhere()
    if put:
        why = _elsewhere(put)
        why.adapt = checked.adapt if checked is not None else None
        return why
    if checked is not None:
        return checked
    if failed is not None:
        return failed
    missed = _missed(stage, world, run)
    if missed is not None:
        return missed
    if SETTING in world.left:
        # nothing else stopped it, so the registry was all it was for: a .bat
        # getting to its end, which next to a 4GB patcher is that patcher's
        return Why(f"needs to {SETTING}, and that isn't done here", kind="setting", sure=True,
                   seen=shown, fix=_outside_fix(SETTING))
    if lines:
        # a banner or a menu's last line is still more than nothing
        return Why(f"{IDLE}, and the last thing it said was \"{lines[-1]}\"", kind="idle",
                   seen=shown, fix=IDLE_FIX)
    return Why(IDLE, kind="idle", fix=IDLE_FIX)


def _missed(stage, world, run) -> Why | None:
    """
    a file of its download it only copies if it's there, `if exist ... copy`,
    and isn't: all a script that otherwise ran clean had to do. One the
    download has somewhere else first, since an optional extra it never had
    says less than the payload zipped a folder deeper
    """
    first = None
    for step in run.trace:
        if step.kind == "exist" and not step.ok and stage.inside_mod(norm(step.what)) \
                and world.kind(step.what) is None:
            why = path_why(stage, world, step.what)
            if why.kind != "payload":
                continue
            why.seen = why.seen + [f"Where it looked: {_cut(step.line)}"]
            if why.adapt is not None and why.adapt not in stage.machine.done:
                return why
            first = first or why
    return first


# what sandbox.py says it left undone for a reg add
SETTING = "change a setting in the registry"
IDLE_FIX = ("It ran through without checking for anything that wasn't there and without "
            "copying anything in, so it may want a step its readme describes done first.")

# an archive nothing here could unpack, in the words sandbox.py left it in:
# what that's said as, and what to do about it
UNPACKED = [
    (r"unpack (.+), which looks damaged or cut short$", "archive",
     "unpacks {0}, which looks damaged or cut short",
     "Download the mod again: {0} in the copy here didn't finish downloading or got damaged on "
     "the way."),
    (r"unpack (.+), which is locked with a password it doesn't give$", "password",
     "unpacks {0}, which is locked with a password it doesn't give",
     "The mod's page or readme gives the password. Unzip {0} with it and drop the folder that "
     "comes out."),
    (r"unpack (.+) with a password that doesn't open it$", "password",
     "unpacks {0} with a password that doesn't open it",
     "The password in its .bat doesn't fit this copy of {0}. The mod's page may give the right "
     "one, or have a newer download."),
    (r"unpack (.+), which takes 7-Zip to open$", "archiver",
     "unpacks {0}, which takes 7-Zip to open, and this PC hasn't got it",
     "Install 7-Zip from 7-zip.org, then drop the mod again."),
    (r"unpack (.+), which needs more free space than the game's drive has$", "disk",
     "unpacks {0}, which needs more free space than the game's drive has",
     "It plays on copies on the game's drive. Free up some space there, then drop the mod "
     "again."),
    (r"unpack (.+) with PowerShell, which can't open how it's packed$", "archive",
     "unpacks {0} with PowerShell, which can't open how it's packed",
     "Unzip {0} with 7-Zip yourself and drop the folder that comes out."),
]


def _stopped(stage, world, run, stopped: str, shown: list) -> Why:
    """
    a play stopped short: cmd giving up on a line, which is a mistake in the
    script, or a loop, which is it waiting on something, the check it went
    round on saying what
    """
    if not re.search(r"loop|deeper and deeper", stopped):
        mistake = stopped.startswith("cmd gave up")
        if mistake and any(re.search(r"\s", arg) for arg in stage.args) and \
                ARGUED.search(run.line):
            # "%1" round a path with spaces, which Explorer hands over in
            # quotes of its own, is ""C:\Program Files..."" to cmd on any PC
            return Why(f"stopped: {stopped}", kind="stopped", sure=True, seen=shown + [
                       f"Where it stopped: {_cut(run.line)}"], adapt=("plain",),
                       fix="It puts quotes of its own round what's dragged onto it, so cmd gives "
                           "up on any folder with a space in its path, the way it would on your "
                           "PC. A newer version of the mod may fix it.")
        return Why(f"stopped: {stopped}", kind="stopped", sure=True, seen=shown,
                   fix="cmd itself gives up on the script there, the way it would on your PC: "
                       "the script has a mistake in it. A newer version of the mod may fix it."
                       if mistake else "That's as far as it could be played here. Its readme "
                       "may describe the rest as steps to do by hand.")
    step = decisive(run) or getattr(run, "spun", None)
    checked = _check_why(stage, world, run, step) if step is not None else None
    if checked is not None:
        return Why(f"went round in a loop, because it {checked.said}", kind="loop", sure=True,
                   seen=shown + checked.seen, adapt=checked.adapt, fix=checked.fix)
    return Why(f"stopped: {stopped}", kind="loop", sure=True, seen=shown,
               fix="It went round the same lines without getting anywhere, which is usually it "
                   "waiting on something a copy of the game here never has. Its readme may say "
                   "what.")


def _opened(stage, run) -> Why | None:
    """what it opened for the person rather than ran: a web page, a readme, a setup that isn't there"""
    steps = [s for s in run.trace if s.kind == "opened"]
    page = next((s for s in steps if re.match(r"^(https?://|www\.)", str(s.what), re.I)), None)
    if page is not None:
        return Why(f"opens a web page, {_cut(page.what, 90)}, rather than installing anything",
                   kind="page", sure=True,
                   fix="That's usually the mod's page, or where to get the part of it that does "
                       "the installing. Download what it points to and drop that in.")
    missing = next((s for s in steps if not s.ok and re.search(r"\.(exe|msi|bat|cmd)$",
                                                               str(s.what), re.I)), None)
    if missing is not None:
        name = _in_download(stage, str(missing.what))
        return Why(f"starts {name}, which isn't in the download", kind="absent", sure=True,
                   seen=[f"Where it stopped: {_cut(missing.line)}"],
                   fix=f"The download should have {name} in it. Get the mod again, or check its "
                       "page for a separate download.")
    shown = next((s for s in steps if s.ok), None)
    if shown is not None:
        return Why(f"only opens {leaf(str(shown.what))} for you to read", kind="readme",
                   sure=True, fix="The steps to install it are in there. It's a readme, not an "
                                  "installer.")
    return None


def _elsewhere(put: list) -> Why:
    folder = parent(put[0])
    saves = re.search(r"\\(my games|saved games)(\\|$)", key(folder))
    return Why(f"copies its files to {folder}" + (
               ", where the game keeps its saves and settings," if saves else "") +
               " rather than into the game", kind="outside", sure=True,
               fix="Files outside the game's folder aren't something that can go in from here "
                   "or come back out from the Installed list. Run its .bat yourself if you want "
                   "them there.")


def _in_download(stage, path: str) -> str:
    """a path as the download has it, tools\\xdelta3.exe, or as it was when it's elsewhere"""
    inner = stage.inside_mod(norm(path)) if path else None
    return inner if inner else path


def said_lines(run, world) -> list:
    """what it put on screen, less its goodbye, the questions answered here and rule lines"""
    asked = {a.prompt.strip() for a, _ in world.asked}
    return [line.strip() for line in run.tail(12) if re.search(r"[^\W_]", line)
            and not FAREWELL.match(line.strip()) and line.strip() not in asked
            and not OURS.match(line.strip()) and not KEYLESS.search(line)
            and not re.search(r"\[[A-Z0-9,]+\]\?\w?\s*$", line)
            and not line.strip().lower().startswith("press any key")]


def _shown(lines: list) -> list:
    if not lines:
        return []
    return ["What it said last:"] + [f"  {_cut(line)}" for line in lines[-3:]]


def _cut(text: str, most: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= most else text[:most - 1].rstrip() + "..."


def _listed(things: list) -> str:
    return things[0] if len(things) == 1 else ", ".join(things[:-1]) + " and " + things[-1]


def _own_words(lines: list, world) -> str:
    """the last thing it said that explains itself, unless one of its programs said it"""
    theirs = world.failed[-1][2] if world.failed else ()
    for line in reversed(lines[-3:]):
        if EXPLAINS.search(line) and not STOCK.match(line) and line not in theirs:
            return _cut(line)
    return ""


def _with_words(why: Why, own: str) -> Why:
    if own and f"\"{own}\"" not in why.said and not any(own in s for s in why.seen):
        why.seen = [f"It said: \"{own}\""] + why.seen
    return why


def _outside_fix(what: str) -> str:
    if "PowerShell" in what:
        return ("It hands part of the install to PowerShell, which isn't played here. If that "
                "part only unzips something, unzip it yourself and drop the folder that comes "
                "out. Otherwise its readme may have the same steps by hand.")
    if "MSI" in what:
        return ("It runs a Windows Installer package (.msi), which installs onto the PC by "
                "itself rather than into a copy of the game. Run the .msi yourself if you trust "
                "it.")
    if "Script Host" in what or "HTML" in what:
        return ("It hands part of the install to a script Windows runs (VBScript, JScript or "
                "HTML), which isn't played here. Its readme may have the same steps by hand.")
    if "download" in what:
        return ("It fetches part of itself from the internet while it runs, which isn't done "
                "from here. The mod page usually has that part as a download of its own: get "
                "it and drop it in too.")
    if "registry" in what:
        return ("A setting in Windows' registry isn't part of the game's files, so it's left "
                "out. If the mod needs it, its readme says what to set.")
    return "That isn't part of the game's files, so it's left out. Its readme says what it's for."


def decisive(run) -> Step | None:
    """
    the check that sent the script off for good: walking back from where it
    ended, the last that gave up on something. A miss whose branch left (a
    goto or an exit), a command whose failing the line acted on, a variable
    that never got set, a copy of nothing, or an admin check it left on
    passing, which is a script that refuses to run as admin. A search that
    found what it wanted afterwards isn't it, and nor is anything a program
    of theirs ran after: the script went on to its work from there
    """
    found, busy, later = set(), False, False
    for step in reversed(run.trace):
        if step.kind == "ran" and step.what.how == "program" and step.within is None:
            # one run on a check's own way off, a message box before its
            # exit, is that check giving up
            later = True
        if step.ok and (step.kind == "copy" or step.kind == "ran" and step.what.how == "program"):
            # work it went on to do after an earlier check
            busy = True
            continue
        if step.kind == "exist":
            if step.ok:
                found.add(leaf(step.what).lower())
                if step.sets:
                    found.add(step.sets.lower())
                continue
            if leaf(step.what).lower() in found or later:
                continue
            if step.leaves or (step.sets and not run.get(step.sets)
                               and step.sets.lower() not in found):
                return step
        elif step.kind == "copy":
            if not step.ok and leaf(step.what).lower() not in found and not later:
                return step
        elif later:
            continue
        elif step.kind in ("errorlevel", "status") and step.leaves:
            ran = step.what
            # a call's own status is whatever it checked inside, further back
            if isinstance(ran, Ran) and ran.how != "call" and (
                    ran.code != 0 or _admin_probe(ran) and not busy):
                return step
        elif step.kind == "compare" and step.leaves:
            if re.search(r"errorlevel", step.line, re.I):
                ran = _ran_before(run, step)
                if ran is not None and ran.how != "call" and (
                        ran.code != 0 or _admin_probe(ran) and not busy):
                    return Step("status", ran, ran.code == 0, True, step.line)
            elif _unset(step) or step.depth == 1 and ARGUED.search(step.line):
                return step
        elif step.kind == "defined" and step.leaves and not step.ok:
            return step
    return None


def _ran_before(run, step: Step):
    """the program whose errorlevel a compare reads, the last one before it"""
    at = next((n for n in range(len(run.trace) - 1, -1, -1) if run.trace[n] is step), None)
    if at is None:
        return None
    return next((s.what for s in reversed(run.trace[:at]) if s.kind == "ran"), None)


def _unset(step: Step) -> str:
    """the variable a compare found empty, off its line: if "%game%"=="" goto ..."""
    if not step.ok:
        return ""
    # "", [] and {} around nothing are all an empty one
    sides = [str(side).strip().strip('"').strip("[]{}()'").strip() for side in step.what]
    if any(sides):
        return ""
    hit = re.search(r"[%!](\w[\w.#$-]*)[%!]", step.line)
    return hit.group(1) if hit else ""


def _admin_probe(ran: Ran) -> bool:
    name = leaf(ran.name).lower().removesuffix(".exe")
    if name in ("find", "findstr"):
        return "mandatory label" in ran.fed.lower()
    if name == "reg":
        return bool(ADMIN_HIVE.search(ran.text))
    if name in ("net", "net1"):
        return ran.text.strip().lower().startswith("session")
    return bool(ADMIN.match(name))


def _missing_program(stage, run) -> Why | None:
    """a program cmd couldn't find anywhere: a runtime this PC hasn't got, or one the download lacks"""
    for step in reversed(run.trace):
        if step.kind != "ran" or step.what.how != "missing":
            continue
        name = step.what.name
        runtime = NAMES.get(leaf(name).lower().removesuffix(".exe"))
        if runtime is not None:
            label, *_, get = RUNTIMES[runtime]
            return Why(f"needs {label}, which isn't installed on this PC", kind="runtime",
                       sure=True, fix=f"{get}, then drop the mod again.",
                       seen=[f"Where it stopped: {_cut(step.line)}"])
        archiver = _archiver(stage, name)
        if archiver is not None:
            folder, label = archiver
            return Why(f"runs {leaf(name)}, which is {label}'s, from a PATH it isn't on",
                       kind="path", sure=True, adapt=("path", folder),
                       seen=[f"Where it stopped: {_cut(step.line)}"],
                       fix=f"Its author has {label}'s folder on the PATH. Adding it there in "
                           "Windows' environment settings lets it run the same way.")
        shown = _in_download(stage, name)
        full = join(step.what.cwd, name) if step.what.cwd and name else name
        inner = stage.inside_mod(norm(full)) if full else None
        if inner:
            # run by a path into a folder of its own that was zipped a level
            # deeper, or that only the mod it's an add-on for has
            # cmd finds `call FFXIIIwdbLoad` as FFXIIIwdbLoad.bat, so the drop's
            # searched the same way, and the copy goes where cmd will look
            bare = "." not in leaf(inner)
            for ext in ("", *((".exe", ".com", ".bat", ".cmd") if bare else ())):
                moved = stage.find_in_drop(inner + ext)
                if moved:
                    return Why(f"runs {inner + ext} beside itself, and its download has it in "
                               f"{moved[1].replace('/', chr(92))}", kind="payload", sure=True,
                               adapt=("payload", norm(full) + ext, *moved),
                               seen=[f"Where it stopped: {_cut(step.line)}"])
            other = _another_download(stage, norm(full), inner, "absent",
                                      [f"Where it stopped: {_cut(step.line)}"], ran=True)
            if other is not None:
                return other
        return Why(f"runs {shown}, which isn't anywhere in the download", kind="absent",
                   sure=True, seen=[f"Where it stopped: {_cut(step.line)}"],
                   fix=f"The download should have {shown} in it. Get the mod again, or check its "
                       "page for a separate download of the tools it uses.")
    return None


def _another_download(stage, full: str, inner: str, kind: str, seen=(),
                      ran: bool = False) -> Why | None:
    """
    a file of its download's that's another download's instead: lent from
    there when the drawer can see that one, else said as the add-on it is
    when the tool's one only a known mod ships (deps.py)
    """
    from patcher.mods import deps

    lent = stage.borrow(full, ran) if hasattr(stage, "borrow") else None
    if lent is not None:
        return Why(f"needs {inner}, which comes with your {lent[2]} download rather than its own",
                   kind=kind, sure=True, adapt=("lend", *lent), seen=list(seen),
                   fix="It's an add-on made to be unpacked over that mod's download.")
    parts = inner.split("\\")
    tool = parts[-1] if "." in parts[-1] or not ran else parts[-1] + ".exe"
    base = deps.of(getattr(getattr(stage, "game", None), "data", "") or "", tool)
    if base is None:
        return None
    where = f" out of its {parts[-2]} folder" if len(parts) > 1 else ""
    # "your HD Models Plus download", "the HD Project download": a label
    # that brings its own "the" drops it after one of ours
    bare = re.sub(r"^the\s+", "", base.label)
    gone = getattr(getattr(stage, "lenders", None), "gone", {}).get(base.id)
    if gone:
        # in the game, but its tools went with the download it came from: the
        # one thing that can't be made up for, since nothing of theirs ships here
        spot = PureWindowsPath(gone)
        there = Path(gone).exists()
        return Why(f"is an add-on for {base.label}, and {'runs' if ran else 'needs'} {tool}"
                   f"{where}. {base.label} is in, but that tool only comes in its download, "
                   + (f"and {spot.name}, the one it went in from, doesn't have it" if there else
                      f"and {spot.name} isn't in {spot.parent.name or spot.parent} any more"),
                   kind=kind, sure=True, seen=list(seen), needs=base.id,
                   fix=(f"Drop the {bare} download that has {parts[-2] if len(parts) > 1 else tool} "
                        "in it on the drawer once" if there else
                        f"Put {spot.name} back, or drop your {bare} download on the drawer "
                        "once")
                   + ". You don't have to install it again: dropping it is enough for its tools "
                     "to be kept, offline, for this and every add-on after it.")
    return Why(f"is an add-on for {base.label}, and {'runs' if ran else 'needs'} {tool}{where}, "
               "which this download doesn't have", kind=kind, sure=True, seen=list(seen),
               needs=base.id,
               fix=f"Drop your {bare} download on the drawer once and it's kept for this "
                   "and every add-on after it, offline.")


def _archiver(stage, name: str):
    """(its folder, its name) for 7-Zip's or WinRAR's program called bare, when it's off the PATH"""
    from patcher.mods.sandbox import ARCHIVERS

    bare = leaf(name).lower().removesuffix(".exe")
    if "\\" in name or "/" in name:
        return None
    for folder, programs in ARCHIVERS.items():
        if bare in {p.lower().removesuffix(".exe") for p in programs if not p.endswith(".dll")} \
                and key(folder) not in key(stage.env.get("PATH", "")).split(";"):
            return folder, leaf(folder)
    return None


def _program_failed(stage, world) -> Why | None:
    """the last of its own programs that failed, read by its exit code, what it said and its imports"""
    if not world.failed:
        return None
    entry = world.failed[-1]
    tool, code, lines = entry[:3]
    path = entry[3] if len(entry) > 3 else ""
    return program_why(tool, code, lines, _bytes_of(world, path))


def _bytes_of(world, path: str) -> bytes:
    try:
        return world.read(path) if path and world.kind(path) == "file" else b""
    except Exception:
        return b""


def program_why(tool: str, code: int, lines: list, data: bytes = b"") -> Why:
    """
    one of its programs exiting `code` having said `lines`. The exit code
    first, which Windows means one way, then what it said, then the plain
    "failed with code N"
    """
    unsigned = code & 0xFFFFFFFF
    last = [line for line in lines if not FAREWELL.match(line)]
    seen = [f"{tool} said:"] + [f"  {_cut(line)}" for line in last[-4:]] if last else []
    if unsigned in (DLL_NOT_FOUND, DLL_INIT):
        need = _redistributable(data)
        if need:
            dll, what = need
            return Why(f"runs {tool}, which needs {what} ({dll} isn't on this PC)",
                       kind="dll", sure=True, seen=seen,
                       fix=f"Install {what} from Microsoft (both the x86 and x64 ones), then "
                           "drop the mod again.")
        return Why(f"runs {tool}, which needs a DLL that isn't on this PC", kind="dll",
                   sure=True, seen=seen,
                   fix="Installing the latest Microsoft Visual C++ Redistributable (x86 and x64) "
                       "fixes this for most tools. Its readme may name what else it needs.")
    if unsigned == BAD_IMAGE:
        return Why(f"runs {tool}, which loads a DLL built for the other kind of Windows (32 "
                   "against 64-bit)", kind="dll", sure=True, seen=seen,
                   fix="Reinstalling the Microsoft Visual C++ Redistributable, both x86 and x64, "
                       "usually puts the right ones back.")
    if unsigned in DOTNET_HOST:
        return Why(f"runs {tool}, which needs a .NET runtime this PC doesn't have", kind="dotnet",
                   sure=True, seen=seen, fix=_dotnet_fix(" ".join(lines)))
    if KEYLESS.search(" ".join(lines)):
        # what it asked is the last thing it said before its error about there
        # being no one to answer, a closing "Press any key" included
        said = [line.strip() for line in lines if line.strip()]
        ends = next((n for n, line in enumerate(said) if KEYLESS.search(line)
                     or re.match(r"(traceback|unhandled exception)", line, re.I)), len(said))
        prompt = said[ends - 1] if ends > 0 else ""
        before = said[:max(ends - 1, 0)]
        # a closing "press any key" or "Done." after it had already failed: the
        # failure's what it said before that. Or the last thing it said was
        # the failure, which no question ends in a colon or a question mark
        asking = prompt.rstrip().endswith((":", "?")) or re.search(
            r"\(\s*y(es)?\s*/\s*n(o)?\s*\)|\[\s*y(es)?\s*/\s*n(o)?\s*\]|\by/n\b", prompt, re.I)
        closing = not asking and (re.search(r"press|any key|to exit|to close|to continue",
                                            prompt, re.I) or (FINISHED.search(prompt)
                                                              and not went_wrong(prompt)))
        shown = before if closing else said[:ends]
        wrong = [line for line in shown if went_wrong(line)] if not asking else []
        # "Nothing was patched. Press any key to exit." fails on its own line
        ahead = re.split(r"press|any key", prompt, 1, re.I)[0].strip(" ,.;:-") if closing else ""
        if ahead and went_wrong(ahead):
            shown, wrong = shown + [ahead], wrong + [ahead]
        if wrong:
            last = shown.index(wrong[-1])
            seen = [f"{tool} said:"] + [f"  {_cut(line)}" for line in shown[max(0, last - 3):last + 1]]
            signed = _signs(shown, tool)
            if signed is not None:
                signed.seen = seen
                return signed
            # an ERROR: line says more than the "Nothing was patched." after it
            pick = next((line for line in wrong if re.search(r"\berror\b|\bfail", line, re.I)),
                        wrong[-1])
            return Why(f"runs {tool}, which failed saying \"{_cut(pick, 120)}\", so nothing "
                       "changed", kind="tool", sure=True, seen=seen,
                       fix="That's the tool's own error. The mod's page or readme may say what "
                           "it needs.")
        return Why(f"runs {tool}, which stopped to ask something that can't be answered here"
                   + (f": \"{_cut(prompt, 100)}\"" if prompt else ""), kind="input", sure=True,
                   seen=seen, fix="It asks while it runs, which a play here can't answer for "
                                  "anything but a yes, a numbered menu or Press Enter. Its readme "
                                  "may give a way to run it without asking.")
    signed = _signs(last, tool)
    if signed is not None:
        signed.seen = seen
        return signed
    if unsigned == CLR_CRASH:
        first = next((line for line in last if "exception" in line.lower()), "")
        return Why(f"runs {tool}, which crashed with a .NET error" + (
                   f": \"{_cut(first, 120)}\"" if first else ""), kind="crash", sure=True,
                   seen=seen, fix="Get the mod's latest version. If it's a .NET Framework tool, "
                                  "the .NET Framework 4.8 runtime from Microsoft may be missing.")
    if unsigned in CRASHES:
        return Why(f"runs {tool}, which {CRASHES[unsigned]}", kind="crash", sure=True, seen=seen,
                   fix="That's the tool itself failing. The mod's latest version, or its page's "
                       "comments, are the place to look.")
    if unsigned == DENIED:
        return Why(f"runs {tool}, which Windows wouldn't let start", kind="denied", sure=True,
                   seen=seen, fix="Antivirus software blocking it is the usual reason: allow it "
                                  "there if you trust the mod, then drop it again.")
    if code == 9009:
        return Why(f"runs {tool}, which Windows couldn't start", kind="denied", sure=True,
                   seen=seen, fix="Windows Security taking the file is the usual reason. If you "
                                  "trust the mod, allow it there and drop the mod again.")
    how = f"saying \"{_cut(last[-1], 120)}\"" if last else f"with code {code}"
    return Why(f"runs {tool}, which failed {how}, so nothing changed", kind="tool", sure=True,
               seen=seen,
               fix="That's the tool's own error. The mod's page or readme may say what it needs.")


def _redistributable(data: bytes):
    """(the DLL, what it comes with) for the first import a redistributable provides"""
    if not data:
        return None
    from patcher.addon_sieve import pe_imports

    for dll in pe_imports(data):
        low = dll.lower()
        if WINDOWS_DLLS.match(low):
            continue
        for pattern, what in DLLS:
            if re.match(pattern, low):
                return dll, what
    return None


def _dotnet_fix(text: str) -> str:
    hit = re.search(r"version '(\d+\.\d+)", text) or re.search(
        r"\.NET( Desktop Runtime| Runtime)? v?(\d+(\.\d+)?)", text)
    version = hit.group(hit.lastindex if hit.re.groups > 1 else 1) if hit else ""
    if version and re.match(r"^\d+(\.\d+)?$", version) and float(version.split(".")[0]) >= 5:
        return (f"Install the .NET {version} Desktop Runtime (x64) from dotnet.microsoft.com, "
                "then drop the mod again.")
    if "framework" in text.lower():
        return ("Install the .NET Framework 4.8 runtime from Microsoft (and 3.5, if it names "
                "that), then drop the mod again.")
    return "Install the .NET runtime it names from dotnet.microsoft.com, then drop the mod again."


def _signs(lines: list, tool: str = "") -> Why | None:
    """what the last lines printed say went wrong, read off their words"""
    who = f"runs {tool}, which " if tool else ""
    for line in reversed(lines[-6:]):
        for pattern, kind in SIGNED:
            hit = pattern.search(line)
            if hit is None:
                continue
            why = _signed(kind, hit, line, who)
            if why is not None:
                why.seen = [f"It said: \"{_cut(line)}\""]
                return why
    return None


def _signed(kind: str, hit, line: str, who: str) -> Why | None:
    if kind == "pymodule":
        module = hit.group(1).split(".")[0]
        return Why(f"{who}needs the Python package {module}, which isn't installed",
                   kind="runtime", sure=True,
                   fix=f"In a command prompt, run: pip install {module}. Then drop the mod again.")
    if kind == "dotnet":
        return Why(f"{who}needs a .NET runtime this PC doesn't have", kind="dotnet", sure=True,
                   fix=_dotnet_fix(line))
    if kind == "dll":
        dll = hit.group(1) + ".dll"
        what = next((w for p, w in DLLS if re.match(p, dll.lower())), "")
        return Why(f"{who}needs {dll}" + (f", which comes with {what}" if what else ""),
                   kind="dll", sure=True,
                   fix=(f"Install {what} from Microsoft, then drop the mod again." if what else
                        "Its readme should say which runtime that comes with."))
    if kind in ("java", "python", "node"):
        label, *_, get = RUNTIMES[kind]
        return Why(f"{who}needs {label}, which isn't installed on this PC", kind="runtime",
                   sure=True, fix=f"{get}, then drop the mod again.")
    if kind == "disk":
        return Why(f"{who}ran out of disk space", kind="disk", sure=True,
                   fix="It plays on copies on the game's drive. Free up some space there, then "
                       "drop the mod again.")
    if kind == "inuse":
        return Why(f"{who}found a file it needs open in another program", kind="inuse",
                   sure=True, fix="Close the game and anything else using its files, then drop "
                                  "the mod again.")
    if kind == "archive":
        return Why(f"{who}found an archive in the download damaged or cut short", kind="archive",
                   sure=True, fix="Download the mod again: the copy here didn't finish or got "
                                  "damaged on the way.")
    if kind == "label":
        return Why(f"{who}has a mistake in it: it goes to :{hit.group(1)}, a label that isn't in "
                   "it", kind="stopped", sure=True,
                   fix="cmd gives up on it at that line on your PC too. A newer version of the "
                       "mod may fix it.")
    if kind == "password":
        return Why(f"{who}needs a password for one of its archives", kind="archive", sure=True,
                   fix="Its mod page or readme gives the password. Unzip it with that and drop "
                       "the folder that comes out.")
    if kind == "game":
        return Why(f"{who}couldn't find the game, whichever way it looked", kind="game",
                   fix="It looks for the game somewhere a Steam copy isn't. Its readme may say "
                       "where it expects it, or to run it from inside the game's folder.")
    if kind == "version":
        return Why(f"{who}says the game isn't the version it was made for", kind="version",
                   sure=True, fix="Check the mod page for the game version it supports. This is "
                                  "the copy Steam has now.")
    if kind == "unzip":
        return Why(f"{who}says its files have to be unzipped first", kind="unzip",
                   fix="Unzip the mod and drop the folder that comes out, all of it, rather "
                       "than one file from inside.")
    if kind == "admin":
        return Why(f"{who}wants to be run as administrator", kind="admin",
                   fix="Run as administrator is how it was played here, and it still said so, "
                       "so something else it checks is missing. Its readme may say what.")
    if kind == "denied":
        return Why(f"{who}was refused access to a file it needed", kind="denied",
                   fix="Antivirus software or a read-only file is the usual reason. Check the "
                       "mod's files aren't blocked (right-click, Properties, Unblock).")
    return None


def _check_why(stage, world, run, step: Step) -> Why | None:
    """what the check that sent it off was about, and what the PC could change"""
    where = [f"Where it stopped: {_cut(step.line)}"] if step.line else []
    dragged = _dragged(stage) if _on_argument(stage, step) else None
    if dragged is not None:
        why = dragged
    elif step.kind in ("exist", "copy"):
        why = path_why(stage, world, step.what, copying=step.kind == "copy")
    elif step.kind in ("errorlevel", "status"):
        why = _ran_why(stage, world, run, step)
    elif step.kind in ("compare", "defined"):
        var = step.what if step.kind == "defined" else _unset(step)
        why = _unset_why(stage, world, run, var)
    else:
        why = None
    if why is not None:
        why.seen = why.seen + where
    return why


def _on_argument(stage, step: Step) -> bool:
    """whether a check is on what was dragged onto the script: %1 itself, or a variable set from it"""
    if step.depth != 1:
        return False
    if ARGUED.search(step.line):
        return True
    names = re.findall(r"[%!](\w[\w.#$-]*)[%!]", step.line)
    if step.kind == "defined":
        names.append(str(step.what))
    text = stage.texts.get(stage.dropped[1], "")
    return any(re.search(r"\bset\s+\"?" + re.escape(name) + r"=\s*\"?%~?[fdpnxsatz]*[1-9]",
                         text, re.I) for name in names)


def _dragged(stage, guess: bool = False) -> Why | None:
    """
    a script made to have something dragged onto it, which a play gives it
    nothing of: the game's folder or its exe, the one its script reads like
    it wants first, or for one that copies from what it's given, the folder
    of its download that lands on the game's own files. None once each has
    been
    """
    for path, what in _draggable(stage):
        if any(done[:2] == ("args", path) for done in stage.machine.done):
            continue
        return Why(f"is made to have {what} dragged onto it", kind="args", sure=not guess,
                   guess=guess, adapt=("args", path, what),
                   fix=f"Dragging {what} onto it is how its readme would have it run.")
    return None


def _draggable(stage) -> list:
    """(the path, what it is in words) of what a person would drag onto it, likeliest first"""
    from patcher.mods.game_files import GAME_EXES

    stage.game_path("")
    raw = stage.texts.get(stage.dropped[1], "")
    into = _copied_from_argument(stage, raw)
    if into is not None:
        return _drop_folders_onto(stage, into)
    text = raw.lower()
    root = key(stage.root)
    # the game itself before its launcher, and the one its script names before either
    exes = sorted((k for k in stage.known if leaf(k) in GAME_EXES),
                  key=lambda k: (leaf(k) not in text, "launcher" in k, k))
    exe = stage.nodes[root + "\\" + exes[0]][0] if exes else None
    # %~dp1 and %~nx1 are what a script does with a file it was given
    how = {m.lower() for m in re.findall(r"%~([fdpnxsatz]*)[1-9]", text, re.I)}
    filed = exe is not None and (leaf(exe).lower() in text or any(
        "x" in m or m in ("d", "p", "dp", "pd") for m in how))
    picks = [p for p in ([exe, stage.root] if filed else [stage.root, exe]) if p]
    return [(p, "the game's folder" if p == stage.root else f"the game's {leaf(p)}")
            for p in picks]


def _copied_from_argument(stage, text: str):
    """
    for a script that copies FROM what's dragged onto it ("drag your texture
    folder onto this"), where in the game it copies that to, "" for the
    game's own folder. None for one that's given the game, which a copy out
    of it into its own folder, TEMP or a backup is: that's the game's files
    kept, not the mod's put in
    """
    named = {m.lower() for m in re.findall(r"\bset\s+\"?(\w+)=\s*\"?(?:%~?[fdpnxsatz]*1)\b", text,
                                           re.I)}

    def given(path: str) -> bool:
        return bool(ARGUED.search(path)) or any(f"%{n}%" in path.lower() for n in named)

    for line in text.splitlines():
        verb = re.search(r"\b(x?copy|robocopy|move)(\.exe)?\s+(.*)$", line, re.I)
        if verb is None:
            continue
        # its arguments only, up to a redirect or the next command
        rest = re.split(r'(?:\s*\d?>>?|\||&)(?=(?:[^"]*"[^"]*")*[^"]*$)', verb.group(3))[0]
        paths = [a.strip('"') for a in _args(rest) if not a.startswith("/")]
        if len(paths) < 2 or not given(paths[0]) or given(paths[-1]):
            continue
        into = _in_game(stage, text, paths[-1])
        if into is not None:
            return into
    return None


def _in_game(stage, text: str, target: str):
    """
    where in the game a copy's target is, "" for its folder, or None for
    somewhere that isn't the game: the script's own folder (unless it's run
    from inside the game's), TEMP, the user's folders, a backup
    """
    flat = _resolved(stage, text, target.replace("/", "\\"))
    if re.search(r"\\(backups?|bak|originals?|vanilla)(\\|$)", flat, re.I):
        return None
    own = re.match(r"^%~d?p0\\?(.*)$", flat, re.I)
    if own:
        rest = own.group(1).strip("\\")
        return rest.lower() if rest and stage.game_path(rest) else None
    if re.match(r"^[a-zA-Z]:", flat):
        rel = stage.game_rel(flat)
        if rel is None:
            base = stage.game_at(flat)
            if base is None:
                return None
            rel = flat[len(base):]
        return rel.replace("/", "\\").strip("\\").lower()
    # the end of where it goes, past the variable the game's in
    return re.sub(r"^.*[%!][\w~$:]+[%!]", "", flat).strip("\\").lower()


def _resolved(stage, text: str, path: str) -> str:
    """
    a path with the variables a script set in so many words, and the PC's own
    (%TEMP%, %USERPROFILE%), put in. One it worked out, the game's folder off
    the registry say, stays as it's written
    """
    for _ in range(4):
        hit = re.search(r"%(\w[\w()]*)%", path)
        if hit is None:
            break
        name = hit.group(1)
        said = re.findall(r"\bset\s+\"?" + re.escape(name) + r"=\"?([^\"\r\n&|<>]*)", text, re.I)
        value = said[-1].strip() if said else next(
            (v for k, v in stage.env.items() if k.lower() == name.lower()), None)
        if value is None or "%%" in value or f"%{name.lower()}%" in value.lower():
            break
        path = path[:hit.start()] + value + path[hit.end():]
    return path


def _drop_folders_onto(stage, into: str) -> list:
    """
    the folders of the download that, copied to `into` in the game, land on
    the most of the game's own files: Textures\\white_data for a copy into
    white_data, never Textures, which would land a white_data inside it
    """
    members = [m for m, _ in stage.dropped[0]]
    folders = {""} | {"/".join(m.split("/")[:n]) for m in members
                      for n in range(1, m.count("/") + 1)}
    scored = []
    for folder in folders:
        prefix = folder.lower() + "/" if folder else ""
        hits = sum(1 for m in members if m.lower().startswith(prefix) and
                   "\\".join(filter(None, [into, m[len(prefix):].replace("/", "\\")])).lower()
                   in stage.known)
        if hits:
            scored.append((-hits, -folder.count("/"), folder))
    return [(child(stage.mod, folder.replace("/", "\\")) if folder else stage.mod,
             f"its {folder.replace('/', chr(92))} folder" if folder else "its own folder")
            for _, _, folder in sorted(scored)]


def path_why(stage, world, path: str, copying: bool = False) -> Why:
    """a file or folder it wanted that isn't there, read by where it is"""
    full = norm(path)
    # "folder\nul" is how a script asks whether a folder's there
    if leaf(full).lower() == "nul":
        full = parent(full)
    rel = stage.game_rel(full)
    if rel is not None and not getattr(stage, "unpacked_game", False) and stage.in_archives(rel):
        # a file the archives hold, wanted loose: made for a game Nova
        # Chrysalia has unpacked, which a play fakes from the archives
        return Why("is made for a game Nova Chrysalia has unpacked", kind="unpacked", sure=True,
                   adapt=("unpacked",),
                   fix="Nova Chrysalia's unpacked mode is how its readme would have it run.")
    if rel is not None:
        shown = rel.replace("/", "\\") or "folder"
        return Why(f"looks for the game's {shown}, which this copy of the game doesn't have",
                   kind="gamefile", sure=True,
                   fix="It's made to go on top of something the game doesn't have: another mod "
                       "it needs in first, or another release of the game. Its readme should "
                       "say which.")
    climb = stage.climb_home(full)
    if climb is not None:
        return Why("is made to be unzipped into a folder of its own inside the game's",
                   kind="home", sure=True, adapt=("home", climb),
                   fix="Unzipped into the game's folder and run from there is how its readme "
                       "would have it done.")
    base = stage.game_at(full)
    if base is not None:
        return Why(f"looks for the game at {base}, which isn't where this copy of the game is",
                   kind="game", adapt=("game", base),
                   fix="That's where its author keeps the game. Its readme may say how to point "
                       "it at yours, or to run it from inside the game's folder.")
    inner = stage.inside_mod(full)
    if inner is not None:
        mine = stage.inside_mod(parent(stage.bat))
        own = inner[len(mine):].lstrip("\\") if mine and inner.lower().startswith(
            mine.lower() + "\\") else inner if not mine else None
        # its white_data\ zipped a folder deeper is the download's, which says
        # nothing about where the .bat runs from
        moved = stage.find_in_drop(inner)
        if not moved and own and stage.game_path(own):
            return Why("is made to be run from inside the game's folder", kind="home",
                       sure=True, adapt=("home",),
                       fix="Unzipped into the game's folder and run from there is how its "
                           "readme would have it done.")
        others = [] if moved else stage.drop_matches(inner)
        fix = ("The download may be incomplete or zipped differently from how its .bat "
               "expects. Get it again, or unzip it and drop the whole folder.")
        if len(others) > 1:
            shown = _listed([m.replace("/", "\\") for m in others[:3]])
            fix = (f"Its download has {leaf(inner)} in more than one place ({shown}), which look "
                   "like versions to choose between. Put the one you want where its .bat looks, "
                   "beside it, and drop the folder.")
        if not moved and not others:
            other = _another_download(stage, full, inner, "payload")
            if other is not None:
                return other
            # a file of the game's it wants copied in beside it first
            ours = stage.game_copy(inner)
            if ours is not None:
                return Why(f"wants the game's {leaf(ours)} copied in beside it first",
                           kind="payload", sure=True, adapt=("gamefile", full, ours),
                           fix="Copy it out of the game's folder into the mod's, where its "
                               ".bat looks, and drop the folder.")
        said = (f"looks for {inner} beside itself, and its download has it in "
                f"{moved[1].replace('/', chr(92))}" if moved else
                f"needs {inner} from its download, which isn't in what was dropped" if inner
                else "needs its own folder, which isn't there")
        return Why(said, kind="payload", sure=True, fix=fix,
                   adapt=("payload", full, *moved) if moved else None)
    if copying and _under_temp(full):
        return Why(f"copies {leaf(full)} out of a folder an earlier step was to fill, and it "
                   "isn't there", kind="made",
                   fix="A step before it didn't make what it should have: one of its tools "
                       "failing quietly is the usual reason.")
    program = _program_at(full)
    if program is not None:
        label, *_, get = RUNTIMES[program]
        version = runtime_version(program, full)
        if version:
            # C:\Python27 is Python 2, which this PC's Python 3 won't stand in for
            label = f"{label} {version}"
            get = f"Install {label} from {'python.org' if program == 'python' else 'its maker'}"
        return Why(f"looks for {label} at {norm(full)}, and it isn't installed on this PC",
                   kind="runtime", sure=True, adapt=("runtime-at", full, program, version),
                   fix=f"{get}, then drop the mod again.")
    if _under_temp(full):
        return Why(f"looks for {leaf(full)} in a folder an earlier step was to fill, and it "
                   "isn't there", kind="made",
                   fix="A step before it didn't make what it should have: one of its tools "
                       "failing quietly is the usual reason.")
    shown = norm(full)
    if re.search(r"\\(my games|saved games)(\\|$)", key(full)):
        return Why(f"looks for the game's saves and settings at {shown}, which a copy of the game "
                   "doesn't have", kind="saves", sure=True,
                   fix="It works on your saves or settings, which aren't part of the game's "
                       "files, so it can't go in from here. Its readme says how to do it by hand.")
    # what's there is anyone's guess, so its own words about it go first
    return Why(f"looks for {shown}, which isn't there", kind="elsewhere",
               fix="That's somewhere outside the game and the download. Its readme should say "
                   "what's meant to be there.")


def _under_temp(path: str) -> bool:
    return bool(re.search(r"\\appdata\\local\\temp(\\|$)", key(path)))


def runtime_version(program: str, path: str) -> str:
    """the version a runtime's install path names, Python27 or jre1.8, else """""
    low = key(path)
    if program == "python":
        hit = re.search(r"\\python(\d)(\d*)(\\|$)", low)
        return f"{hit.group(1)}.{hit.group(2)}" if hit and hit.group(2) else \
            hit.group(1) if hit else ""
    if program == "java":
        hit = re.search(r"\\(?:jre|jdk)-?(1\.\d+|\d+)", low)
        return hit.group(1) if hit else ""
    return ""


def _program_at(path: str):
    low = key(path)
    if not re.match(r"^[a-z]:\\(program files( \(x86\))?|programdata|users\\[^\\]+\\appdata)\\",
                    low) and not re.match(r"^[a-z]:\\(python\d*|java|jdk|jre)", low):
        return None
    return next((program for pattern, program in PROGRAMS if re.search(pattern, low)), None)


def _ran_why(stage, world, run, step: Step) -> Why | None:
    """a command whose result sent the script off"""
    ran = step.what
    if not isinstance(ran, Ran):
        return None
    name = leaf(ran.name).lower().removesuffix(".exe")
    words = ran.text.split()
    if _admin_probe(ran):
        if ran.code == 0:
            return Why("won't run as administrator, and gave up on finding it was",
                       kind="admin", sure=True, adapt=("admin", False),
                       fix="Run without administrator rights is how it wants it. Its readme may "
                           "say why it minds.")
        return Why("has to be run as administrator", kind="admin", sure=True,
                   adapt=("admin", True),
                   fix="Run it as administrator: right-click it, Run as administrator.")
    if name == "reg" and words and words[0].lower() == "query":
        return _registry_why(stage, ran, step.line)
    if name == "where":
        target = next((w for w in words if not w.startswith("/")), "")
        archiver = _archiver(stage, target.strip('"'))
        if archiver is not None:
            folder, label = archiver
            return Why(f"looks for {label} on the PATH, which it isn't on", kind="path",
                       sure=True, adapt=("path", folder),
                       fix=f"Its author has {label}'s folder on the PATH. Adding it there in "
                           "Windows' environment settings lets it find it the same way.")
        runtime = NAMES.get(leaf(target.strip('"')).lower().removesuffix(".exe"))
        if runtime is not None:
            label, *_, get = RUNTIMES[runtime]
            return Why(f"needs {label}, which isn't installed on this PC", kind="runtime",
                       sure=True, fix=f"{get}, then drop the mod again.")
        return Why(f"looks for a program called {target}, which isn't installed on this PC",
                   kind="elsewhere", sure=True,
                   fix=f"Its readme should say what {target} is and where to get it.")
    if name in ("find", "findstr"):
        # reg query ... | find "0x1": a key that wasn't there left nothing to
        # search. Only the query piped into it, never one before it
        fed = getattr(ran, "piped", None)
        if isinstance(fed, Ran) and fed.code != 0 and not _searched(ran)[1] \
                and not ran.fed.strip() and leaf(fed.name).lower().removesuffix(".exe") == "reg" \
                and (_args(fed.text) or [""])[0].lower() == "query":
            return _registry_why(stage, fed, step.line)
        return _found_why(stage, world, run, ran)
    if name in ("cd", "chdir", "pushd"):
        target = ran.text.strip()
        target = re.sub(r"^/d\s+", "", target, flags=re.I).strip('"')
        return path_why(stage, world, join(run.cwd, target)) if target else None
    if re.match(r"^(7z[ar]?|winrar|rar|unrar)$", name) and words and words[0].lower() in ("x", "e"):
        # an archive that isn't there is what it couldn't unpack, *.zip
        # included when nothing matches it
        from patcher.mods.batch import matches, wild

        archive = next((w.strip('"') for w in _args(ran.text)[1:] if not w.startswith("-")), "")
        full = join(run.cwd, archive) if archive else ""
        there = full and (world.kind(full) is not None or wild(leaf(full)) and any(
            matches(name, leaf(full)) for name, is_dir in world.listdir(parent(full))
            if not is_dir))
        if full and not there:
            return path_why(stage, world, full, copying=True)
    if name in ("robocopy", "xcopy"):
        source = next((w.strip('"') for w in _args(ran.text) if not w.startswith("/")), "")
        full = join(run.cwd, source) if source else ""
        return path_why(stage, world, full, copying=True) if full and \
            world.kind(full) is None else None
    if name in ("copy", "move", "type", "dir", "del", "erase", "ren", "rename"):
        return None
    if ran.how == "program":
        # this PC's Python running a script that failed is the script failing
        lines = next((entry[2] for entry in reversed(world.failed)
                      if entry[0].lower() == leaf(ran.name).lower()), [])
        return program_why(leaf(ran.name), ran.code, lines, _bytes_of(world, ran.name))
    runtime = NAMES.get(name)
    if runtime is not None:
        label, *_, get = RUNTIMES[runtime]
        return Why(f"needs {label}, which isn't installed on this PC", kind="runtime", sure=True,
                   fix=f"{get}, then drop the mod again.")
    return None


def _registry_why(stage, ran: Ran, line: str = "") -> Why:
    words = _args(ran.text)
    place = words[1] if len(words) > 1 else ""
    if not place.strip():
        # the key itself was in a variable that never got set
        var = re.search(r"reg(?:\.exe)?\s+query\s+\"?[%!](\w[\w.#$-]*)[%!]", line, re.I)
        return Why(f"reads the registry at a key it keeps in %{var.group(1)}%, which it never set"
                   if var else "reads the registry at a key it never worked out", kind="unset",
                   sure=True, fix="It works that key out some way this copy of the PC doesn't answer. Its "
                       "readme may say what it expects installed.")
    lowered = [w.lower() for w in words]
    at = lowered.index("/v") + 1 if "/v" in lowered else 0
    value = words[at] if 0 < at < len(words) else ""
    what = key_kind(place)
    other = game_of_key(place) if what == "game" else ""
    if other and other != stage.game.data:
        # another of the three's own key, which this game's folder never answers
        named = TITLES.get(other, "another game")
        return Why(f"checks the registry for {named}'s install record ({place}), so it's a mod "
                   f"for {named}", kind="othergame", sure=True,
                   fix=f"It's made for {named}, not this game. Drop it in {named}'s Mods drawer "
                       "instead.")
    version = key_version(what, place)
    read = f"{RUNTIMES[what][0]} {version}'s" if version else READ_AS.get(what, "")
    said = f"checks the registry for {read} install record ({place})" if read else \
        f"checks the registry for {place}" + (f", {value}" if value else "")
    fixes = {
        "python": RUNTIMES["python"][-1], "java": RUNTIMES["java"][-1],
        "node": RUNTIMES["node"][-1], "dotnet": RUNTIMES["dotnet"][-1],
        "framework": "Install the .NET Framework 4.8 runtime from Microsoft",
        "vcredist": "Install the latest Microsoft Visual C++ Redistributable (x86 and x64)",
        "directx": "Install the DirectX End-User Runtime (June 2010) from Microsoft",
    }
    if version:
        # PythonCore\2.7 is Python 2, which this PC's Python 3 won't stand in for
        fixes[what] = f"Install {RUNTIMES[what][0]} {version} from " + (
            "python.org" if what == "python" else "its maker")
    steam = re.search(r"\\uninstall\\steam app \d+$", place.lower().rstrip("\\"))
    fix = f"{fixes[what]}, then drop the mod again." if what in fixes else (
        "That's the record Steam keeps of a game it's installed, and this PC's Steam has none "
        "for that one: the mod may be for another of the three games." if what == "game" and steam
        else "It reads where the game is from the registry entry Square Enix's own installer "
        "makes, which a Steam copy doesn't have." if what == "game" else
        "Something it expects installed writes that key. Its readme should say what.")
    # only a key whose meaning is known is given: anything else's values
    # would be made up
    return Why(f"{said}, which isn't there", kind="registry", sure=True,
               adapt=("key", place, value) if what else None, fix=fix)


def key_kind(place: str) -> str:
    low = place.lower().rstrip("\\")
    return next((kind for pattern, kind in KEYS if re.search(pattern, low)), "")


def game_of_key(place: str) -> str:
    """
    the data folder of the game a Square Enix or Steam install key is for,
    white_data say, or "" when it doesn't name one of the three
    """
    from patcher.mods.game_files import STEAM_APPS
    from patcher.mods.sandbox import GAME_FOLDERS

    low = place.lower().rstrip("\\")
    app = re.search(r"\\(?:steam app |valve\\steam\\apps\\)(\d+)$", low)
    if app:
        return next((data for data, number in STEAM_APPS.items() if number == app.group(1)), "")
    named = re.search(r"\\square ?enix\\([^\\]+)", low)
    bare = re.sub(r"[^a-z0-9]", "", named.group(1)) if named else ""
    return next((data for data, names in GAME_FOLDERS.items() if bare in names), "")


def key_version(what: str, place: str) -> str:
    """the version a runtime's own key is for, PythonCore\\2.7's 2.7, else """""
    low = place.lower()
    hit = re.search(r"\\pythoncore\\(\d+(\.\d+)?)", low) if what == "python" else re.search(
        r"\\(?:java runtime environment|java development kit|jre|jdk)\\(1\.\d+|\d+)", low) \
        if what == "java" else None
    return hit.group(1) if hit else ""


def _args(text: str) -> list:
    from patcher.mods.batch import split_args

    return split_args(text)


def _searched(ran: Ran) -> tuple:
    """(what a find or findstr looks for, the files it looks in), none for what it's piped"""
    args = _args(ran.text)
    # findstr /c:"text" file, or find "text" file
    switched = next((re.sub(r"^/c:", "", a, flags=re.I).strip('"') for a in args
                     if a.lower().startswith("/c:")), None)
    plain = [a.strip('"') for a in args if not a.startswith("/")]
    wanted = switched if switched is not None else (plain[0] if plain else "")
    return wanted, plain if switched is not None else plain[1:]


def _found_why(stage, world, run, ran: Ran) -> Why | None:
    """a find or findstr that came up empty, read by what it was searching"""
    fed = ran.fed.lower()
    wanted, files = _searched(ran)
    if not wanted:
        return None
    if "microsoft windows [version" in fed:
        version = re.search(r"\d+\.\d+(\.\d+)*", wanted)
        return Why(f"checks for Windows {wanted}", kind="windows", sure=True,
                   adapt=("windows", version.group(0)) if version else None,
                   fix="Its readme may say why it minds which Windows it's on.")
    # certutil -hashfile's or 7-Zip's h, which the play hashes off the real file
    hashed = re.search(r"hash of (.+?):\s*$", ran.fed, re.M) or re.search(
        r"^[0-9A-Fa-f]{8,}\s+\d+\s+(\S.*?)\s*$", ran.fed, re.M)
    # what's fed is cut at 400 characters, so 7-Zip's is known by how it starts
    if hashed and ("-hashfile command completed" in fed or fed.lstrip().startswith("scanning\n")):
        return Why(f"checks {leaf(hashed.group(1))} against a hash of the one it was made for, "
                   "which this one doesn't match", kind="content", sure=True,
                   fix="It's made for one release of that file: another, or one a patch "
                       "already changed, doesn't match. Its readme should say which. Steam's "
                       "Verify integrity of game files puts back the original.")
    if "no tasks are running" in fed or "image name" in fed:
        return Why(f"wants {wanted} running while it works, which makes it a patch for the "
                   "game while it runs rather than something that installs", kind="running",
                   sure=True,
                   fix="Mods like that are run by hand with the game open, the way their "
                       "readme says. Nothing of it goes into the game's files.")
    if files:
        target = join(run.cwd, files[-1])
        rel = stage.game_rel(target)
        where = f"the game's {rel.replace('/', chr(92))}" if rel else leaf(target)
        return Why(f"checks {where} for \"{wanted}\", which isn't in it", kind="content",
                   sure=True,
                   fix="It's looking for a version or setting the file doesn't have, which "
                       "usually means a different release of the game, or another mod in "
                       "first. Its readme should say which.")
    return None


def _unset_why(stage, world, run, var: str) -> Why | None:
    """a variable it needed that never got set: what it searched, and where"""
    if not var:
        return None
    looked = [s for s in run.trace if s.kind == "exist" and not s.ok
              and s.sets.lower() == var.lower()]
    places = []
    for s in looked:
        spot = norm(s.what)
        if spot not in places:
            places.append(spot)
    game = next((b for b in (stage.game_at(p) for p in places) if b is not None), None)
    if places:
        shown = _listed([_cut(p, 90) for p in places[:3]] + (
            [f"{len(places) - 3} more"] if len(places) > 3 else []))
        many = f"{len(places)} place{'s' if len(places) > 1 else ''}"
        if game is not None:
            return Why(f"looks for the game in {many}, and it isn't in any of them: {shown}",
                       kind="game", adapt=("game", game), sure=True,
                       fix="Those are where its author's PC keeps the game. Its readme may say "
                           "how to point it at yours, or to run it from inside the game's folder.")
        # a search through its own folders is for part of the download, and
        # any one of the places will do to put it
        inside = next((p for p in places if stage.inside_mod(p) is not None), None)
        if inside is not None:
            why = path_why(stage, world, inside)
            if why.kind in ("payload", "home"):
                return why
        thing = leaf(places[0]) if len({leaf(p).lower() for p in places}) == 1 else ""
        program = NAMES.get(thing.lower().removesuffix(".exe")) or _program_at(places[0])
        if program is not None:
            label, *_, get = RUNTIMES[program]
            return Why(f"looks for {label} in {many}, and it isn't installed on this PC",
                       kind="runtime", sure=True, fix=f"{get}, then drop the mod again.")
        return Why(f"looks for {thing or 'what it needs'} in {many}, and it isn't in any of "
                   f"them: {shown}", kind="elsewhere",
                   fix=f"Its readme should say what {thing} is and where it goes." if thing else
                   "Its readme should say what it's looking for there.")
    asked = next((a for a, _ in reversed(world.asked) if a.var and a.var.lower() == var.lower()),
                 None)
    if asked is not None:
        prompt = _cut(asked.prompt.strip(), 90)
        return Why((f"asks \"{prompt}\"" if prompt else f"asks for %{var}%") +
                   ", and won't go on with anything a copy of the game here could type",
                   kind="input", sure=True,
                   fix="It wants something typed that isn't any answer it checks for, so there's "
                       "nothing to work it out from. Its readme or mod page should say what to "
                       "type.")
    reads = [s.what for s in run.trace if s.kind == "ran" and not s.ok
             and re.search(r"\bset\s+\"?" + re.escape(var) + r"=", s.line, re.I)]
    if reads and leaf(reads[-1].name).lower().removesuffix(".exe") == "reg":
        return _registry_why(stage, reads[-1], next(
            (s.line for s in reversed(run.trace) if s.kind == "ran" and s.what is reads[-1]), ""))
    return Why(f"never found what it keeps in %{var}%", kind="unset",
               fix="It works that out some way this copy of the PC doesn't answer. Its readme "
                   "may say what it expects to find.")
