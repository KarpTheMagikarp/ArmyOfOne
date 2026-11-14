"""
Runs The FF XIII HD Project's own tools without its batch files.

Every reported failure of the mod lives in the .bat scripts (drive scanning,
`!` and `^` breaking delayed expansion, no space check before 20GB of writes,
an uninstall that can leave an infinite Now Loading), none in the tools. So
this replaces the sequencing and keeps the tools.

    ff13tool.exe          -x -ff13   unpack      -i  import      -c  repack
    FFXIII_ModelsHD.exe   Data\\<id>.txt                          per recipe
    FFXIII_HDProject.exe  <id>.win32 models\\orig\\... models\\mod\\... <grp>\\

Exit codes don't mean what you'd think:

    FFXIII_ModelsHD    0 is success
    FFXIII_HDProject   1..1000 is success (a count), 0 means it did nothing
    ff13tool           meaningless, returned 1 on a clean 3076-file extract

So ff13tool steps are judged by what they produced (Step.produces, checked in
_produced()), which is what the next step needs anyway.

The tools are run by fixed name from the SupportFiles/ detect() found, checked
to be inside the mod folder, never through a shell and never from a name in a
data file. That can't make an untrusted mod safe, but a doctored Data/ file
can't turn this into running something else.
"""

import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = {
    "pack": "ff13tool.exe",
    "models": "FFXIII_ModelsHD.exe",
    "inject": "FFXIII_HDProject.exe",
    # XIII-2 HD ships its own builds under other names. ff13tool is shared.
    "models2": "FF XIII HD Models.exe",
    "inject2": "FFXIII2_HDProject.exe",
    # ours, beside theirs: see hd2_models_fix
    "models2fixed": "FF XIII HD Models.fixed.exe",
    # HD Models Plus's own build, 4.1.7, run from the Mods drawer. Same name
    # as XIII-2's, a different program: x64, and its name swaps are only read
    # while parsing, so v4.0.0's empty-table spin isn't in it
    "modelsplus": "FF XIII HD Models.exe",
}

# how each tool says it worked. see the module docstring.
OK_ZERO = "zero"
OK_COUNT = "count"
OK_PRODUCED = "produced"     # the exit code means nothing; look at the result
OK_IMPORTED = "imported"     # nor does it here, and the result is not ours to look at

# what ff13tool -i prints when an import worked. Their LOADMODELFILE counts
# these four, which is the only test there is: the exit code is 1 either way.
# Matched on the first token, like their `for /f`, since the tool prints
# "Added filelist_z002u.win32.bin (copied)" and never a bare "Added".
IMPORTED_WORDS = ("Added", "Updated", "Imported", "Wrote")

# what a local step returns when it failed AND could not prove it put the game
# back, so the run can say "may be half in" instead of "nothing changed"
NOT_UNDONE = 2

# what a tool step returns when it outran its limit and was ended. No exit code
# a process can hand back is negative here, so it never reads as the tool's own
HUNG = -2

# the way back, for every message about a game that could not be put back
# automatically. The window has no Revert of its own, so it says how to run one
REVERT = "Revert (ArmyOfOne.exe ffxiii --revert, from the patcher's folder)"


class RunnerError(Exception):
    pass


class Cancelled(RunnerError):
    pass


class Unsure(str):
    """in Result.landed: something a failed step may have left half in the game"""


@dataclass
class Step:
    tool: str | None          # a key of TOOLS, never a path. None for a local step.
    args: list
    label: str                # what to show a person
    success: str = OK_ZERO
    optional: bool = False    # a step whose failure should not stop the run
    produces: str = ""        # relative to the mod folder, for OK_PRODUCED
    # this step is the point of the install rather than scaffolding around it.
    # Unpacking and repacking are not; the per id model and texture work is.
    # run() refuses to call a run successful when none of these did anything.
    payload: bool = False
    # a tree that has to have changed since it was produced for this step to be
    # worth running. See _changed() and the repack step.
    needs_change: str = ""
    # (source, destination) pairs relative to the mod folder, copied once the
    # step has succeeded. This is GENERATESINGLE's loadcopy=1 branch: the tools
    # write into models\\mod and the copy is what puts the result where the
    # repack will find it. Leaving it out is why 854 successful injections once
    # produced an archive identical to the one that went in.
    copies: list = field(default_factory=list)
    # for a local step (tool is None): what it does. See run_local().
    action: str = ""
    # relative to the mod folder, when this step has to run from somewhere
    # else. Only the zone pipeline needs it, and it needs it because ff13tool
    # -i takes the archive path AND the file to import as two paths resolved
    # against the same current directory, so the mod's own installer cd's into
    # models\\mod\\zone<v>\\<zone> before every import. Left empty, a step runs
    # from the mod folder like everything else.
    cwd: str = ""
    # the label of a step that has to have worked for this one to be worth
    # running. Their GENERATEZONE wraps the import in `IF !success! EQU 1`,
    # and skipping that is how a live run reached an import for a block whose
    # injector had just failed, in a folder the injector therefore never made.
    after: str = ""
    # the same idea one level up: run if ANY of these worked. Their `updated`
    # flag, which decides whether a zone is written back to the game at all.
    after_any: list = field(default_factory=list)
    # and the veto: skipped if any of these ran and failed. A zone is only
    # worth writing to the game if every import that went into it landed.
    unless_failed: list = field(default_factory=list)
    # what this step puts in the game when it works, as a person would say it.
    # Result.landed collects them, so a run that stops partway can say exactly
    # what it changed instead of guessing.
    lands: str = ""
    # SEED only: a folder holding the vanilla scr pair. A model the tree
    # already carries modded is cached out of that instead.
    source: str = ""
    # seconds a tool gets before it counts as frozen and is ended, 0 for no
    # limit. A frozen optional step never fails, it just never returns, so
    # optional alone can't stop it taking the whole run down
    limit: int = 0


@dataclass
class Result:
    steps_run: int = 0
    failed: Step | None = None
    code: int | None = None
    lines: list = field(default_factory=list)      # tail of the failed step's stdout
    errors: list = field(default_factory=list)     # tail of its stderr, which is the reason
    applied: int = 0                               # payload steps that did something
    skipped: list = field(default_factory=list)    # (label, code) for the ones that failed
    # the steps behind those, and those an install left as they are, for what
    # each would have written: an id's files, or for a zone's, none
    missed: list = field(default_factory=list)
    landed: list = field(default_factory=list)     # what reached the game, in order
    gaps: list = field(default_factory=list)       # what was meant to and did not
    # ids the mod ships nothing for. FFXIII_HDProject returns a count, and a
    # count of zero with nothing on stderr means it looked and there was no HD
    # texture, which is the normal state of 102 of the 891 ids in their own
    # lists. Calling that a skip made a complete install read as half broken.
    nothing: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed is None

    @property
    def payload_steps(self) -> int:
        """
        the ones that could have installed something. An id the mod ships no
        art for is not a step that went wrong, so it is not counted against
        the run when deciding whether enough of it worked.
        """
        return self.applied + len(self.skipped)

    @property
    def reason(self) -> str:
        """
        the most useful one line summary available, which is the last thing the
        tool said on stderr. "stopped with code 1" on its own sent somebody off
        chasing disk space for an evening.
        """
        for text in reversed(self.errors):
            if text.strip():
                return text.strip()
        return ""


def tool_path(support: Path, tool: str) -> Path:
    """
    the executable for a step, resolved and confined to the mod folder.

    the name comes from TOOLS rather than from anything on disk, and the result
    has to still be inside the folder after resolving, so a symlink planted in
    SupportFiles cannot redirect this somewhere else.
    """
    if tool not in TOOLS:
        raise RunnerError(f"no such tool: {tool}")
    support = Path(support).resolve(strict=False)
    found = (support / TOOLS[tool]).resolve(strict=False)
    if found.parent != support:
        raise RunnerError(f"{TOOLS[tool]} does not sit directly in {support.name}")
    if not found.is_file():
        raise RunnerError(f"{TOOLS[tool]} is missing from {support.name}")
    return found


def succeeded(step: Step, code: int, made: bool | None = None, lines=()) -> bool:
    """
    `made` is the artefact verdict from _produced(), or None when there was
    nothing to look at. Only OK_PRODUCED steps use it. `lines` is the step's
    stdout, which only OK_IMPORTED reads.
    """
    if step.success == OK_IMPORTED:
        said = {text.split(maxsplit=1)[0] for text in lines if text.split()}
        return all(word in said for word in IMPORTED_WORDS) and not tool_errors(lines)
    if step.success == OK_COUNT:
        # a count, not a status. 0 means it processed nothing.
        return 0 < code <= 1000
    if step.success == OK_PRODUCED:
        # falls back to the ordinary rule rather than passing blind, for a
        # step declared this way with no produces to check
        return code == 0 if made is None else made
    return code == 0


# XIII-2's index lists 274 paths more than once, and then this reads
# "14381 files (470 duplicates) listed", which the folder can only hold 13911 of
LISTED = re.compile(r"(\d+) files(?: \((\d+) duplicates?\))? listed in \d+ file chunks")
EXTRACTED = re.compile(r"Extracted (\d+) files in total")


def tool_errors(lines) -> list:
    """
    what ff13tool said went wrong. It says it on stdout and then exits with
    whatever it likes, so these are the only failure it reliably reports.
    """
    return [text.strip() for text in lines if text.lstrip().startswith("Error")]


def at(mod_root: Path, relative) -> Path:
    """
    a plan path joined onto the mod folder. Plans spell paths with backslashes
    so they're the same object on any OS, and on POSIX a backslash is just a
    character, so they're split here instead of joined raw.
    """
    return Path(mod_root) / Path(str(relative).replace("\\", "/"))


def _stamp(target: Path):
    """what a file looks like right now, for a before and after comparison"""
    try:
        info = target.stat()
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def _tree_stamp(folder: Path):
    """
    a cheap signature of an unpacked tree: how many files, how much, and when
    the most recent one was written.

    Three thousand stats, so tens of milliseconds against a run measured in
    tens of minutes. Any of the three moving means something in there was
    rewritten, which is the only question being asked.
    """
    count = total = 0
    newest = 0
    try:
        for path in Path(folder).rglob("*"):
            try:
                info = path.stat()
            except OSError:
                continue
            if not path.is_file():
                continue
            count += 1
            total += info.st_size
            newest = max(newest, info.st_mtime_ns)
    except OSError:
        return None
    return (count, total, newest)


def _produced(mod_root: Path, step: Step, before, lines) -> bool | None:
    """
    did the step make the thing it exists to make. None if nothing was named.

    A directory is judged on contents, since extraction is idempotent and a
    retry rewrites the same files. A file is judged on change of (mtime, size),
    since the repack target was copied in minutes earlier and a whole run can
    land inside one timestamp tick. A byte-identical rebuild could read as no
    change, which refuses a copy back rather than copying something wrong.
    """
    if not step.produces:
        return None

    target = at(mod_root, step.produces)
    if not target.exists() or tool_errors(lines):
        return False

    if target.is_dir():
        made = sum(1 for path in target.rglob("*") if path.is_file())
        if made == 0:
            return False
        # when it announced both numbers and they disagree it stopped early,
        # whatever it returned. Only checked when both are present, because
        # this is the -x wording and the other modes print something else.
        listed, repeats, extracted = _counts(lines)
        if listed is not None and extracted is not None and extracted != listed:
            return False
        # a path listed twice is extracted twice over the same file
        return listed is None or made >= listed - repeats

    return _stamp(target) != before


def _counts(lines):
    """(listed, how many of those repeat a path, extracted), None for what it never said"""
    listed = extracted = None
    repeats = 0
    for text in lines:
        found = LISTED.search(text)
        if found:
            listed, repeats = int(found.group(1)), int(found.group(2) or 0)
        found = EXTRACTED.search(text)
        if found:
            extracted = int(found.group(1))
    return listed, repeats, extracted


OUT = "out"
ERR = "err"
CMD = "cmd"


def run_step(step: Step, mod_root: Path, support: Path, on_line=None,
             cancel=None) -> int:
    """
    runs one tool and streams its output a line at a time.

    cwd is the mod folder since every path the tools get is relative to it.
    on_line gets (text, stream) with stream OUT or ERR, kept apart so the
    complaint in a wall of filenames can be found. One reader thread per pipe,
    or the unread one fills at 4KB and the tool deadlocks.
    """
    executable = tool_path(support, step.tool)
    command = [str(executable), *[str(a) for a in step.args]]

    # a step's own working directory, confined to the mod folder for the same
    # reason tool_path is: the plan is built from data that came out of an
    # archive somebody downloaded.
    here = Path(mod_root)
    if step.cwd:
        here = (here / Path(step.cwd.replace("\\", "/"))).resolve(strict=False)
        root = Path(mod_root).resolve(strict=False)
        if root != here and root not in here.parents:
            raise RunnerError(f"{step.label} wants to run outside the mod folder")
        if not here.is_dir():
            raise RunnerError(f"{step.label} wants to run in {step.cwd}, which is not there")

    # echoed before it runs so a failed install's log has the exact command.
    # The directory only when it isn't the mod folder (zone imports).
    if on_line:
        where = f"   (in {step.cwd})" if step.cwd else ""
        on_line(f"> {executable.name} {' '.join(str(a) for a in step.args)}{where}", CMD)

    return _spawn(command, here, on_line=on_line, cancel=cancel, label=step.label,
                  limit=step.limit)


def _end(process) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _spawn(command: list, cwd: Path, on_line=None, cancel=None, label: str = "",
           limit: int = 0) -> int:
    """
    the process half of run_step. cancel=None means it always runs to the
    end, which is what a tool writing into the game's own archive needs:
    killing it mid-write is the one thing worse than letting it finish.
    limit is the same call made on a clock, so only give it to tools whose
    output is still in the mod folder when they're ended.
    """
    creation = 0
    if sys.platform == "win32":
        # no console window. these are console programs and every one of them
        # would flash up a black box otherwise, which is most of what this
        # feature exists to stop.
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        bufsize=1, text=True, errors="replace", creationflags=creation,
    )

    def pump(pipe, stream):
        try:
            for line in pipe:
                if on_line:
                    on_line(line.rstrip("\r\n"), stream)
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=pump, args=(pipe, stream), daemon=True)
               for pipe, stream in ((process.stdout, OUT), (process.stderr, ERR))]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    try:
        while True:
            try:
                process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    _end(process)
                    raise Cancelled(f"cancelled during {label}")
                if limit and time.monotonic() - started > limit:
                    _end(process)
                    if on_line:
                        took = f"{limit // 60} minutes" if limit >= 120 else f"{limit} seconds"
                        on_line(f"{label} was still running after {took}, so it was ended "
                                "and left out", ERR)
                    return HUNG
    finally:
        # joined before the pipes are closed, or a reader mid-iteration gets a
        # ValueError on a closed file and the last few lines are lost, which is
        # exactly the output that matters when something has gone wrong
        for reader in readers:
            reader.join(timeout=5)
        for pipe in (process.stdout, process.stderr):
            if pipe:
                pipe.close()

    return process.returncode


GROUPS = {"W": "weapon", "N": "npc", "P": "pc", "M": "mon", "S": "summon", "F": "fa"}

# An id becomes a path segment and a filename in the arguments below, so it has
# to be an id and not a path. All 911 in the shipped lists are plain
# alphanumeric and the longest is four characters; the ceiling here is loose on
# purpose, because the point is to exclude separators, dots and nulls rather
# than to guess what a future id might look like.
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# and these, which pass that pattern and are still not filenames. Windows
# resolves a reserved device name with an extension on it, so "con.win32" is
# the console, not a file, and a tool told to write it does something nobody
# wants to debug. None of them are real ids.
RESERVED = {"con", "prn", "aux", "nul",
            *(f"com{n}" for n in range(1, 10)),
            *(f"lpt{n}" for n in range(1, 10))}


def usable_id(member: str) -> bool:
    return bool(SAFE_ID.match(member)) and member.lower() not in RESERVED


def plan(data_dir: Path, members: list, variant: str) -> list:
    """
    the command sequence for a set of ids, straight off PROCESSLINE.

    Its one letter type -> folder table is all that maps an id to a place on
    disk, so a type not in it is skipped, not guessed. Zones aren't here, they
    expand per block with their own filelist pair. loadcopy is 1 throughout so
    the archive is repacked once at the end, not once per category.
    """
    data_dir = Path(data_dir)
    steps = []

    for kind, member in members:
        if kind is None:
            continue                      # a zone marker, handled elsewhere
        group = GROUPS.get(kind.upper())
        if group is None:
            continue
        # a corrupted or doctored list would otherwise put "../.." straight
        # into a path argument. Skipping rather than raising keeps one bad line
        # from failing an install of nine hundred good ones, and a category
        # that ends up with no steps at all is refused by build_plan anyway.
        if not usable_id(member):
            continue

        # both tools write into models\\mod and neither puts the result where
        # the repack looks. GENERATESINGLE's loadcopy=1 branch does that, for
        # both of them, which is why this sits outside the if.
        mod_bin = "\\".join(["models", "mod", "chr", group, member, "bin", ""])
        tree_bin = "\\".join([f"white_scr{variant}", "chr", group, member, "bin", ""])
        copies = [(mod_bin + member + suffix, tree_bin + member + suffix)
                  for suffix in (".win32.trb", ".win32.imgb")]

        recipe = data_dir / f"{member}.txt"
        if recipe.is_file():
            # spelled with a backslash rather than built with Path, so the plan
            # is the same object whichever OS assembled it. Path would hand
            # Windows "Data\\n910.txt" and the test host "Data/n910.txt", which
            # makes a plan comparison pass locally and mean nothing.
            steps.append(Step(tool="models", args=["Data\\" + f"{member}.txt"],
                              label=f"{member} models", success=OK_ZERO,
                              optional=True, payload=True, copies=copies))
        else:
            # no recipe, so the injector is driven by paths instead. success is
            # a COUNT here, see the module docstring.
            # trailing separators kept, because the batch file passes them and
            # the tool joins a filename straight onto the end
            base = "\\".join(["models", "orig", "chr", group, member, "bin", ""])
            steps.append(Step(tool="inject",
                              args=[f"{member}.win32", base, mod_bin, group + "\\"],
                              label=f"{member} textures", success=OK_COUNT,
                              optional=True, payload=True, copies=copies))
    return steps


SEED = "seed"
# the two halves of the zone pipeline that are file work rather than a tool.
# HARVEST pulls a zone's index out of the game's image archive and its blob off
# disk; ZONE_TREE takes what ff13tool just extracted and leaves it where the
# per block steps expect to find it. Both are hd_zones' business and live here
# only because run_local is where a local step is dispatched.
ZONE_TREE = "zone_tree"
HARVEST = "harvest"
SEED_PAIR = "seed_pair"     # the cached zone pair copied to where it is edited
COMMIT = "commit"           # a finished zone into the game, blob and index as one
DISCARD = "discard"         # a finished zone's working copies, off the drive
# XIII-2 only. Its indexes are encrypted, so ff13tool gets a decrypted copy
# (OPEN) and the result is sealed again (SEAL) before anything reads it.
OPEN = "open"
SEAL = "seal"
HARVEST2 = "harvest2"       # XIII-2's zone originals, into their .bat's zone\ folder
COMMIT2 = "commit2"         # a finished XIII-2 zone into the game
GROUP_DIRS = ("pc", "npc", "weapon", "summon", "mon", "fa")
MODEL_FILES = (".win32.trb", ".win32.imgb")


# which voice set a cache folder's originals came out of. Theirs is one cache for
# both sets, which holds while the two sets' files are the same; the copy into
# the other set leaves the tools only the files that aren't, and those need
# their own set's originals
VOICES_MARK = "patcher-voices.txt"
VOICE_IN_NAME = re.compile(r"white_(?:img|scr|z\d+)([uc])", re.IGNORECASE)


def _cached_voices(folder: Path) -> str | None:
    try:
        return (Path(folder) / VOICES_MARK).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _mark_voices(folder: Path, variant: str) -> None:
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / VOICES_MARK).write_text(variant, encoding="utf-8")
    except OSError:
        # costs one comparison pass on the next run into this set, nothing more
        pass


# files cached at once by the seed. Waiting on the disk rather than on a core,
# so not tied to how many there are
SEED_WORKERS = 4


class _Unread(Exception):
    """a backup copy the seed needed and couldn't read, said in the terminal as the reason"""


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return Path(path).stat().st_size == len(data) and Path(path).read_bytes() == data
    except OSError:
        return False


def _cache_copy(source: Path, target: Path, cancel=None, on_share=None) -> None:
    """
    into a cache that trusts whatever is already there, so it has to be all or
    nothing: a copy killed halfway would otherwise be an "original" forever.
    Flushed too, for the same reason. A rename can reach the disk before the
    data does, and after a power cut that is a file of zeros under a trusted
    name.
    """
    from patcher.fileops import CopyCancelled, copy_atomic

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        copy_atomic(source, target, cancel=cancel, on_share=on_share)
    except CopyCancelled as e:
        raise Cancelled(str(e)) from None


def _modded(mod_root: Path, step: Step) -> tuple:
    """
    (vanilla archive, {path: entry}) for every model file the tree has modded,
    to be read out of the backup instead. (None, {}) when there's nothing.

    A new mod version unpacks with no models\\orig, and filling it from a game
    that already has HD art feeds the tools HD art. Decided by size against
    the backup's index, only where the game's copy is bigger (HD art is bigger
    art). A model's .trb and .imgb decide together, since a texture header
    rewritten at the same length is still half an HD model.
    """
    if not step.source:
        return None, {}
    from patcher.archive.filelist import load

    variant = step.args[0][-1]
    # XIII seeds from the scr pair, XIII-2 from white_img, which has no _scr
    # in its index's name either
    index_name, archive_name = ((f"filelist{variant}.win32.bin", f"white_img{variant}.win32.bin")
                                if step.args[0].startswith("white_img") else
                                (f"filelist_scr{variant}.win32.bin",
                                 f"white_scr{variant}.win32.bin"))
    live = mod_root / index_name
    index = Path(step.source) / index_name
    archive = Path(step.source) / archive_name
    if not (live.is_file() and index.is_file() and archive.is_file()):
        return None, {}
    try:
        sizes = {e.path.replace("\\", "/").lower(): e.uncompressed for e in load(live).entries()}
        entries = [(e.path.replace("\\", "/").lower(), e) for e in load(index).entries()]
    except Exception:
        return None, {}
    grown = {path.rsplit(".win32.", 1)[0] for path, entry in entries
             if path.endswith(MODEL_FILES) and sizes.get(path, 0) > entry.uncompressed}
    modded = {path: entry for path, entry in entries
              if path.endswith(MODEL_FILES) and path.rsplit(".win32.", 1)[0] in grown}
    return (archive, modded) if modded else (None, {})


def _backup_listing(mod_root: Path, step: Step) -> tuple:
    """
    (the backup's archive, {path: entry}) for every model file in this voice
    set's vanilla copy, or (None, {}) when there's no backup of it to read
    """
    if not step.source:
        return None, {}
    from patcher.archive.filelist import load

    variant = step.args[0][-1]
    index_name, archive_name = ((f"filelist{variant}.win32.bin", f"white_img{variant}.win32.bin")
                                if step.args[0].startswith("white_img") else
                                (f"filelist_scr{variant}.win32.bin",
                                 f"white_scr{variant}.win32.bin"))
    index, archive = Path(step.source) / index_name, Path(step.source) / archive_name
    if not (index.is_file() and archive.is_file()):
        return None, {}
    try:
        found = {}
        for entry in load(index).entries():
            key = entry.path.replace("\\", "/").lower()
            if key.endswith(MODEL_FILES):
                found.setdefault(key, entry)
    except Exception:
        return None, {}
    return archive, found


def run_local(step: Step, mod_root: Path, on_line=None, on_share=None,
              support: Path | None = None, cancel=None) -> int:
    """
    a step that's file work rather than a process. Returns 0 or 1 like a tool.
    on_share is a plain 0..1 of one step, named apart from the engine's
    on_progress, which takes a Progress object.

    Only SEED so far, the half of Setup.bat that isn't the three tools:

        robocopy white_scru\\chr\\npc models\\orig\\chr\\npc *.trb *.imgb /S /XN /XO /XC
        ... once per group ...
        robocopy models\\orig models\\mod /e /xf *

    /XN /XO /XC means only copy what isn't there, so an existing models\\orig
    cache of originals is never refreshed from a modded tree. /e /xf * is the
    empty folder skeleton FFXIII_HDProject writes into but won't create.
    """
    if step.action in (OPEN, SEAL, HARVEST2, COMMIT2):
        from patcher.mods import hd2_archive, hd2_zones
        if step.action == OPEN:
            return hd2_archive.run_open(step, Path(mod_root), on_line=on_line)
        if step.action == SEAL:
            return hd2_archive.run_seal(step, Path(mod_root), on_line=on_line)
        if step.action == HARVEST2:
            return hd2_zones.harvest(step, Path(mod_root), on_line=on_line, on_share=on_share,
                                     cancel=cancel)
        return hd2_zones.commit_zone(step, Path(mod_root), support, on_line=on_line,
                                     on_share=on_share, cancel=cancel)
    if step.action == SEED_PAIR:
        return _seed_pair(step, Path(mod_root), on_line=on_line, cancel=cancel)
    if step.action == COMMIT:
        from patcher.mods.hd_zones import commit_zone
        return commit_zone(step, Path(mod_root), support, on_line=on_line,
                           on_share=on_share, cancel=cancel)
    if step.action == DISCARD:
        return _discard(step, Path(mod_root), on_line=on_line)
    if step.action == ZONE_TREE:
        return _zone_tree(step, Path(mod_root), on_line=on_line, on_share=on_share,
                          cancel=cancel)
    if step.action == HARVEST:
        return _harvest(step, Path(mod_root), on_line=on_line, cancel=cancel,
                        on_share=on_share)
    if step.action != SEED:
        raise RunnerError(f"no such local action: {step.action}")

    mod_root = Path(mod_root)
    tree = at(mod_root, step.args[0])
    orig = mod_root / "models" / "orig" / "chr"
    if not tree.is_dir():
        if on_line:
            on_line(f"{tree.name} is not there, so there is nothing to cache", ERR)
        return 1

    # only the groups this install actually needs. All six is 1.2GB and their
    # own script caches per group for the same reason, so a weapons only run
    # copies 74MB instead of waiting through the other five.
    wanted = [g for g in (step.args[1:] or GROUP_DIRS) if g in GROUP_DIRS]

    # listed up front rather than walked lazily, so there is a denominator.
    # Two thousand files and a gigabyte under one step number is the longest
    # thing in the run that is not a tool, and a bar that cannot move during it
    # is the same as no bar.
    work = []
    for group in wanted:
        source = tree / "chr" / group
        if not source.is_dir():
            continue
        work += [(group, source, path) for path in sorted(source.rglob("*"))
                 if path.is_file() and path.name.endswith(MODEL_FILES)]

    from patcher.fileops import write_atomic
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_layout import XIII, XIII2
    from patcher.mods.hd_zones import read_entry

    archive, modded = _modded(mod_root, step)
    variant = (VOICE_IN_NAME.match(tree.name) or [None, None])[1]
    variant = variant.lower() if variant else None
    # a group cached out of the other voice set, or by a build that didn't
    # say which, is held up file by file against this set's own vanilla copy
    # in the backup, and what differs is cached again from there. The backup
    # rather than the tree, because a file HD rewrote at the same length looks
    # vanilla in the tree, and caching that would build HD over HD
    kept, vanilla = _backup_listing(mod_root, step) if variant else (None, {})
    other = {g for g in wanted if kept is not None and _cached_voices(orig / g) != variant}
    # bigger than Steam ships it is HD or some other mod, and never an original,
    # whichever way it got here: their .bat refills this cache out of the live
    # game, and a backup taken over a hand install is as modded as the game.
    # hd_vanilla.stuck() takes the same view, so the plan has already left out
    # whatever ends up withheld here
    steam = hd_vanilla.table(XIII2 if step.args[0].startswith("white_img") else XIII)

    def one(group, source, path) -> str:
        """caches one model file, and says how: cached, swapped, unmodded, healed or kept"""
        if cancel is not None and cancel.is_set():
            raise Cancelled(f"stopped while caching {path.name}")
        target = orig / group / path.relative_to(source)
        key = path.relative_to(tree).as_posix().lower()
        entry = modded.get(key)
        clean = steam.get(key)
        dirty = clean is not None and target.is_file() and target.stat().st_size > clean
        if target.exists() and not dirty:
            own = vanilla.get(key) if group in other else None
            if own is not None and clean is not None and own.uncompressed > clean:
                own = None
            try:
                original = None if own is None else \
                    b"" if own.is_stub else read_entry(kept, [own], own.path)
            except Exception as e:
                raise _Unread(f"{own.path}'s vanilla copy could not be read out of the backup "
                              f"to check the cached one against ({e})") from e
            if original is None or _same_bytes(target, original):
                return "kept"
            write_atomic(target, original, durable=True)
            return "swapped"
        if clean is not None and (entry.uncompressed if entry is not None
                                  else path.stat().st_size) > clean:
            return "withheld"
        if entry is not None:
            try:
                # a placeholder has no bytes to read, and none is what vanilla has
                original = b"" if entry.is_stub else read_entry(archive, [entry], entry.path)
            except Exception as e:
                raise _Unread(f"{entry.path} is modded in the game and its vanilla copy "
                              f"could not be read out of the backup ({e})") from e
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(target, original, durable=True)
            return "healed" if dirty else "unmodded"
        _cache_copy(path, target, cancel=cancel)
        return "healed" if dirty else "cached"

    # a few files at once: each is a create, a write, an fsync and a rename,
    # and on Windows a scan on close, so most of the time per file is waiting
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tally, problem = Counter(), None
    with ThreadPoolExecutor(max_workers=SEED_WORKERS) as pool:
        going = {pool.submit(one, *item): item[0] for item in work}
        for done, future in enumerate(as_completed(going), start=1):
            try:
                tally[future.result()] += 1
            except BaseException as e:
                if problem is None:
                    problem = e
                    # nothing new starts once one has gone wrong
                    for later in going:
                        later.cancel()
                continue
            if done % 200 == 1:
                if on_share and work:
                    on_share(done / len(work))
                if on_line:
                    on_line(f"{done} of {len(work)} cached, currently in {going[future]}")
    if isinstance(problem, _Unread):
        if on_line:
            on_line(str(problem), ERR)
        return 1
    if problem is not None:
        raise problem
    if on_share:
        on_share(1.0)
    swapped, unmodded, healed, withheld = (tally["swapped"], tally["unmodded"],
                                           tally["healed"], tally["withheld"])
    cached = tally["cached"] + swapped + unmodded + healed
    skipped = tally["kept"]

    made = _mirror_dirs(mod_root / "models" / "orig", mod_root / "models" / "mod")
    if variant:
        for group in wanted:
            if (orig / group).is_dir():
                _mark_voices(orig / group, variant.lower())

    if on_line:
        kept = f", {skipped} already cached" if skipped else ""
        on_line(f"cached {cached} original model files{kept}, {made} folders "
                "ready for the tools to write into")
        if swapped:
            on_line(f"{swapped} of them were the other voice set's, which has its own "
                    "copy of those, so they were cached again from this one")
        if unmodded:
            on_line(f"{unmodded} of them are already modded in the game, so those came "
                    "out of the backup instead")
        if healed:
            on_line(f"{healed} of them were in the cache bigger than a vanilla copy, so it was "
                    "filled from a game that already had HD in it, and they were cached again "
                    "clean")
        if withheld:
            on_line(f"{withheld} model files are already modded in the game with no vanilla "
                    "copy here, so they weren't cached as originals")
    # nothing cached and nothing already there means the groups this install
    # needs are not in the archive at all, which no later step can recover from
    return 0 if (cached or skipped or withheld) else 1


def _place(mod_root: Path, pairs) -> str:
    """
    copies a step's output where the repack will find it. Returns the first
    source that was not there, or "".

    Paths are spelled with backslashes in the plan so it is the same object
    whichever OS assembled it, which is why they go through Path here rather
    than being joined as text.
    """
    mod_root = Path(mod_root)
    for source, destination in pairs:
        from_path = at(mod_root, source)
        if not from_path.is_file():
            return str(source)
        to_path = at(mod_root, destination)
        to_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(from_path, to_path)
    return ""


def _mirror_dirs(source: Path, target: Path) -> int:
    """the directory shape of one tree recreated in another, files excluded"""
    made = 0
    if not source.is_dir():
        return 0
    for path in source.rglob("*"):
        if not path.is_dir():
            continue
        wanted = target / path.relative_to(source)
        if not wanted.is_dir():
            wanted.mkdir(parents=True, exist_ok=True)
            made += 1
    return made


def _harvest(step: Step, mod_root: Path, on_line=None, cancel=None, on_share=None) -> int:
    """
    a zone's archive pair, out of the game and into models\\orig\\zone<v>\\.

    Setup.bat extracts all of white_img<v> (about 2GB) to get about 15KB of
    index. This reads the one entry it needs. The blob is a plain copy since
    it's loose in white_data\\zone. args are (sys dir, zone dir, variant, zone,
    ...); install() points both at our backup, which is vanilla by
    construction.
    """
    from patcher.archive.filelist import load
    from patcher.fileops import write_atomic
    from patcher.mods.hd_zones import ZONE_CACHE, read_entry

    sys_dir, zone_dir, variant = Path(step.args[0]), Path(step.args[1]), step.args[2]
    wanted = list(step.args[3:])
    cache = at(mod_root, ZONE_CACHE.format(variant=variant))
    cache.mkdir(parents=True, exist_ok=True)

    index, image = sys_dir / f"filelist{variant}.win32.bin", sys_dir / f"white_img{variant}.win32.bin"
    for path in (index, image):
        if not path.is_file():
            if on_line:
                on_line(f"{path.name} is not in {sys_dir}", ERR)
            return 1

    try:
        entries = list(load(index).entries())
    except Exception as e:
        if on_line:
            on_line(f"could not read {index.name}: {e}", ERR)
        return 1

    for at_zone, zone in enumerate(wanted):
        name = f"filelist_{zone}{variant}.win32.bin"
        # kilobytes, so out of the source every time: their .bat fills this
        # cache from whatever the game holds, HD zones included
        try:
            write_atomic(cache / name, read_entry(image, entries, f"zone/{name}"))
        except Exception as e:
            if on_line:
                on_line(f"could not take {name} out of {image.name}: {e}", ERR)
            return 1
        blob = zone_dir / f"white_{zone}{variant}_img.win32.bin"
        if not blob.is_file():
            if on_line:
                on_line(f"{blob.name} is not in {zone_dir}", ERR)
            return 1
        cached = cache / blob.name
        # an HD blob is always bigger, so one cached off a modded game doesn't
        # survive this, the way XIII-2's harvest already worked
        if not cached.is_file() or cached.stat().st_size != blob.stat().st_size:
            if on_line:
                on_line(f"copying {blob.name} ({blob.stat().st_size / 1024**2:.0f}MB)")
            # four and a half gigabytes under one step number, so the bar moves
            # through each blob rather than sitting still for the whole harvest
            share = None if on_share is None else \
                (lambda part, n=at_zone: on_share((n + part) / len(wanted)))
            _cache_copy(blob, cache / blob.name, cancel=cancel, on_share=share)

    if on_line:
        on_line(f"{len(wanted)} zone archives cached in {cache.name}")
    return 0


def _seed_pair(step: Step, mod_root: Path, on_line=None, cancel=None) -> int:
    """
    the cached zone pair copied to where the imports will edit it.

    Copied, not edited in place, since the cache is the only original a retry
    has. Also builds the mod side folders (the zone half of `robocopy
    models\\orig models\\mod /e /xf *`), which the seed step can't since the
    zone isn't unpacked yet, and the injector won't create them.

    The mod side is emptied first, their per block `del /Q
    models\\mod\\!basedir!\\bin\\*.*`, or a second run imports the first run's
    leftovers. args are (source, destination) pairs, flat, then the orig and
    mod trees to mirror.
    """
    mirror = step.args[-2:]
    pairs = list(zip(step.args[0:-2:2], step.args[1:-2:2]))
    for source, target in pairs:
        from_path = at(mod_root, source)
        to_path = at(mod_root, target)
        if not from_path.is_file():
            if on_line:
                on_line(f"{source} is not cached, so there is nothing to write into", ERR)
            return 1
        _cache_copy(from_path, to_path, cancel=cancel)

    stale = at(mod_root, mirror[1])
    if stale.is_dir():
        shutil.rmtree(stale, ignore_errors=True)
    made = _mirror_dirs(at(mod_root, mirror[0]), stale)
    if on_line:
        on_line(f"{len(pairs)} files ready to write into, {made} folders for the tools")
    return 0


def _discard(step: Step, mod_root: Path, on_line=None) -> int:
    """
    working copies whose result is already in the game.

    Never fails a run. Anything still holding one of these open is a reason to
    leave it on the drive and carry on, not a reason to stop forty minutes of
    an install that has otherwise finished.
    """
    freed = 0
    for target in step.args:
        path = at(mod_root, target)
        try:
            if path.is_dir():
                freed += sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                freed += path.stat().st_size
                path.unlink()
        except OSError as e:
            if on_line:
                on_line(f"{target} could not be released: {e}", ERR)
    if on_line and freed:
        on_line(f"{freed / 1024 ** 2:.0f}MB released")
    return 0


def _zone_tree(step: Step, mod_root: Path, on_line=None, on_share=None, cancel=None) -> int:
    """
    what EXTRACTZONE does after ff13tool: keep the bg subtree, drop the rest.
    robocopy /XN /XO /XC again, except where the cache came from the other
    voice set, since this extraction is vanilla and safe to check against.
    args are (extracted dir, destination, source subfolder), and a fourth
    "models" keeps only .trb and .imgb, which is XIII-2's variant.
    """
    mod_root = Path(mod_root)
    source = at(mod_root, step.args[0]) / step.args[2]
    target = at(mod_root, step.args[1]) / step.args[2]
    if not source.is_dir():
        if on_line:
            on_line(f"{step.args[2]} is not in what was extracted, so this zone "
                    "has no backgrounds in it", ERR)
        return 1

    models_only = len(step.args) > 3 and step.args[3] == "models"
    work = [p for p in sorted(source.rglob("*"))
            if p.is_file() and (not models_only or p.name.lower().endswith(MODEL_FILES))]
    # XIII-2 keeps one zone cache for both voice sets. What's extracted here
    # came out of this set's vanilla blob in the backup, so a cache last
    # filled from the other set is checked against it and put right
    voice = VOICE_IN_NAME.search(Path(str(step.args[0]).replace("\\", "/")).name)
    voice = voice.group(1).lower() if voice else None
    root = at(mod_root, step.args[1])
    stale = voice is not None and _cached_voices(root) != voice
    kept = 0
    for done, path in enumerate(work):
        landing = target / path.relative_to(source)
        if not landing.exists() or (stale and not _same_bytes(landing, path.read_bytes())):
            _cache_copy(path, landing, cancel=cancel)
            kept += 1
        if on_share and work and done % 200 == 0:
            on_share(done / len(work))
    if on_share:
        on_share(1.0)
    if voice is not None:
        _mark_voices(root, voice)

    # the extraction is the big one: a zone blob is 60 to 500MB and the tree it
    # unpacks to is larger again, so leaving twenty of them behind is how the
    # mod folder ends up needing more room than the game
    shutil.rmtree(at(mod_root, step.args[0]), ignore_errors=True)
    if on_line:
        on_line(f"{kept} background files cached, {len(work) - kept} already there")
    return 0 if work else 1


def seed_steps(variant: str, groups=(), source: Path | None = None) -> list:
    """
    caching the originals, between the unpack and the first tool.

    groups narrows it to what the plan needs. Empty means all six, which is
    what a plan covering every category amounts to anyway. source is the
    backup, see _modded().
    """
    ordered = [g for g in GROUP_DIRS if g in set(groups)] or list(GROUP_DIRS)
    return [Step(tool=None, action=SEED, args=[f"white_scr{variant}", *ordered],
                 label="caching the original models", source=str(source or ""))]


def unpack_steps(variant: str) -> list:
    """pulling the archive apart before anything is changed in it"""
    return [Step(tool="pack",
                 args=["-x", "-ff13", f"filelist_scr{variant}.win32.bin",
                       f"white_scr{variant}.win32.bin"],
                 label=f"unpacking white_scr{variant}",
                 # it returns 1 after extracting all 3076 files. see the module
                 # docstring; the tree it leaves behind is the only honest test
                 success=OK_PRODUCED, produces=f"white_scr{variant}")]


def repack_steps(variant: str) -> list:
    """
    putting it back together, once, after every id has been written into the
    unpacked tree
    """
    return [Step(tool="pack",
                 args=["-c", f"filelist_scr{variant}.win32.bin", f"white_scr{variant}"],
                 label=f"repacking white_scr{variant}",
                 # same tool, same non-status. The archive is already sitting
                 # there from the copy in, so existing proves nothing and the
                 # check is that this step rewrote it.
                 success=OK_PRODUCED, produces=f"white_scr{variant}.win32.bin",
                 # and the tree it is about to repack has to differ from what
                 # came out of the archive, or this is an expensive way to
                 # produce a copy of what the game already has. That check does
                 # not care how the tools work or whether we drove them
                 # correctly, which is why it is the one worth having.
                 needs_change=f"white_scr{variant}")]


def _nothing_applied(result: Result) -> list:
    """
    the complaint to show when the payload steps didn't take, or [] when
    enough did. None working, or most failing, is the setup and not bad luck,
    and half a texture pack is a broken game. A handful of bad ids is what
    optional is for, so those get reported, not refused.
    """
    total = result.payload_steps
    # the zone pass shares these counters with the character pass, whose
    # archives may already be in the game by now
    copied = ("nothing more has gone into the game" if result.landed
              else "nothing has been copied back to the game")
    # every single id reporting "nothing to install" is not a quiet success.
    # It is what a tool that has lost its inputs would look like if it happened
    # to return zero rather than a negative, and a plan of 891 ids where the
    # mod ships art for none of them is not a thing.
    if total == 0 and result.nothing:
        return [f"All {len(result.nothing)} ids came back with nothing to install, so "
                f"there is nothing to put in the game and {copied}.",
                "The first one said:"]
    if total == 0 or result.applied > total // 2:
        return []

    if result.applied == 0:
        headline = (f"None of the {total} model and texture steps did anything, so the "
                    f"archive holds exactly what came out of it and {copied}.")
    else:
        headline = (f"Only {result.applied} of {total} model and texture steps did "
                    "anything. That is a broken setup rather than a few bad models, "
                    f"so {copied}.")
    return [headline, "The first one said:"]


# the tools whose every write is their own id's models\\mod folder, the mod's
# art and data read and never written, nothing in the working folder, traced
# under wine before being let run side by side. XIII's builds haven't been, so
# they still go one at a time
SIDE_BY_SIDE = frozenset({"inject2", "models2", "models2fixed"})


def _workers() -> int:
    """
    how many tools run at once. Each is a single threaded 32 bit process on one
    id, so it's cores, less one for the window and the copies
    """
    import os
    return max(1, min(4, (os.cpu_count() or 1) - 1))


def _ids(step: Step) -> set:
    """the models a step puts in the tree, off where its copies land"""
    from patcher.mods.hd_vanilla import parse_path

    found = set()
    for _, landing in step.copies or ():
        path = landing.replace("\\", "/").split("/", 1)[-1].lower()
        if parse_path(path) is not None:
            found.add(path.rsplit(".win32.", 1)[0])
    return found


def _batch(steps: list, first: int) -> list:
    """
    the steps from `first` on that can run at once: tools in SIDE_BY_SIDE, none
    waiting on another, and no model twice, so a model's textures are in before
    its mesh edits start
    """
    batch, seen = [], set()
    for step in steps[first:]:
        mine = _ids(step)
        if (step.tool not in SIDE_BY_SIDE or not step.optional or not step.payload or not mine
                or mine & seen or step.produces or step.needs_change or step.after
                or step.after_any or step.unless_failed or step.lands or step.cwd):
            break
        batch.append(step)
        seen |= mine
    return batch


def _alongside(step: Step, mod_root: Path, support: Path, cancel, halt) -> tuple:
    """
    one step of a batch, from clearing its last output to its copies landing:
    (code, worked, empty, said, complained, heard), or None when the batch was
    stopped before it started. Kept rather than said, since only run()'s
    thread says anything
    """
    if halt.is_set():
        return None
    # asked here too: a worker that just saw its own tool ended by a Stop picks
    # up the next step before run()'s thread hears about it
    if cancel is not None and cancel.is_set():
        raise Cancelled(f"cancelled before {step.label}")
    said, complained, heard = [], [], []

    def line(text, stream=OUT):
        heard.append((text, stream))
        (complained if stream == ERR else said).append(text)

    for source, _ in step.copies:
        try:
            at(mod_root, source).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        code = run_step(step, mod_root, support, on_line=line, cancel=cancel)
    except Cancelled:
        raise
    except RunnerError as e:
        line(str(e), ERR)
        code = -1
    worked = succeeded(step, code, None, said)
    empty = (not worked and step.success == OK_COUNT
             and code == 0 and not any(t.strip() for t in complained))
    if worked:
        missing = _place(mod_root, step.copies)
        if missing:
            worked = False
            name = missing.replace("\\", "/").rsplit("/", 1)[-1]
            complained.append(f"{step.label} reported success and left no {name} to install")
    return code, worked, empty, said, complained, heard


def run(steps: list, mod_root: Path, support: Path, on_line=None, on_step=None,
        cancel=None, on_share=None, start: int = 0, total: int | None = None,
        into: Result | None = None) -> Result:
    """
    runs a plan in order, stopping at the first step that matters.

    An optional step that fails is reported and stepped over, like the mod's
    own installer does with a missing recipe. But the run is judged on the
    payload steps: none or most of them failing stops it before the repack,
    so before install() copies anything into the game. (857 of 859 skipped
    once "succeeded" and wrote a recompressed vanilla archive over a game.)

    Runs of per id steps whose tools only touch their own id (_batch) go
    _workers() at a time, each settled as it finishes, with the same verdict
    once the batch is in.
    """
    # start/total/into exist for the zone pass, which cannot be planned until
    # the first pass has unpacked the archives its steps live in. Two calls,
    # one set of counters, so the bar and the tallies read as one run.
    result = into if into is not None else Result()
    counted = total if total is not None else start + len(steps)
    mod_root, support = Path(mod_root), Path(support)
    # the point past which no further payload step will run, so the verdict can
    # be reached before the repack rather than after it. A repack of a
    # multi-gigabyte archive that is about to be thrown away is forty minutes
    # of somebody's evening.
    last_payload = max((i for i, s in enumerate(steps) if s.payload), default=-1)
    # signatures of the trees a step produced, taken the moment it produced
    # them, so a later step can ask whether anything has touched one since
    trees = {}
    # labels that succeeded, for Step.after, and ones that ran and did not,
    # for Step.unless_failed
    worked_labels, failed_labels = set(), set()

    def settle(step, index, code, made, worked, empty, said, complained, judge=True) -> bool:
        """a step's outcome into the tallies, and True when the run stops at it"""
        result.steps_run += 1
        if worked:
            worked_labels.add(step.label)
            if step.lands:
                result.landed.append(step.lands)
        elif not empty:
            failed_labels.add(step.label)
            if step.lands:
                result.gaps.append(step.lands)
                if step.tool is None and code == NOT_UNDONE:
                    result.landed.append(Unsure(step.lands))
        if worked and step.produces and at(mod_root, step.produces).is_dir():
            trees[step.produces] = _tree_stamp(at(mod_root, step.produces))

        if step.payload:
            if worked:
                result.applied += 1
            elif empty:
                result.nothing.append(step.label)
            else:
                result.skipped.append((step.label, code))
                result.missed.append(step)
                # the first one carries why, and the first one is the only one
                # anybody reads. 854 identical complaints is one complaint.
                if not result.errors:
                    result.errors = complained[-6:] or said[-3:]

        # tallied above first, so the final step counts towards its own verdict,
        # and taken whichever way that step went, so a plan whose last model
        # happens to work does not skip the check
        if judge and index == last_payload and judged(step, code):
            return True

        if not worked:
            if step.optional:
                if on_line:
                    on_line(f"[none] the mod ships no art for {step.label}" if empty
                            else f"[skipped] {step.label} returned {code}", OUT)
                return False
            result.failed, result.code = step, code
            # an OK_PRODUCED step that failed did so because the artefact is
            # missing or short, and the code is meaningless, so say the useful
            # thing rather than repeating a number that means nothing
            if made is False:
                result.errors = complained[-40:] or tool_errors(said)[-6:] or [
                    f"{step.label} did not produce {step.produces}"]
                result.lines = said[-12:]
                return True
            # stderr first and kept longer. It is where the reason lives, and
            # it is almost always short, whereas stdout at this point is a
            # thousand filenames of which the last one is the only clue.
            result.errors = complained[-40:]
            result.lines = said[-12:]
            return True
        return False

    def judged(step, code) -> bool:
        verdict = _nothing_applied(result)
        if verdict:
            result.failed, result.code = step, code
            result.errors = verdict + result.errors
        return bool(verdict)

    def together(batch, first) -> bool:
        """
        a batch _workers() at a time, each said and settled as it finishes, and
        the verdict once all of it is in. True when the run stops at it
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        halt, codes, done, problem = threading.Event(), {}, 0, None
        with ThreadPoolExecutor(max_workers=_workers()) as pool:
            running = {pool.submit(_alongside, step, mod_root, support, cancel, halt): n
                       for n, step in enumerate(batch)}
            for future in as_completed(running):
                n = running[future]
                try:
                    outcome = future.result()
                except BaseException as e:
                    # a Stop, or a copy that failed: nothing new starts, and
                    # what's running finishes or is ended by the same Stop
                    halt.set()
                    problem = problem or e
                    continue
                if outcome is None or problem is not None:
                    continue
                code, worked, empty, said, complained, heard = outcome
                codes[n] = code
                if on_step:
                    on_step(batch[n], start + first + done, counted)
                done += 1
                if on_line:
                    for text, stream in heard:
                        on_line(text, stream)
                settle(batch[n], first + n, code, None, worked, empty, said, complained,
                       judge=False)
        if problem is not None:
            raise problem
        last = last_payload - first
        return 0 <= last < len(batch) and judged(batch[last], codes.get(last))

    past = 0
    for index, step in enumerate(steps):
        # a batch already ran these
        if index < past:
            continue
        if cancel is not None and cancel.is_set():
            raise Cancelled(f"cancelled before {step.label}")

        batch = _batch(steps, index) if _workers() > 1 else []
        if len(batch) > 1:
            past = index + len(batch)
            if together(batch, index):
                return result
            continue

        # asked before the step runs, because the point is not to run it. The
        # payload counters above answer "did the tools report doing anything";
        # this answers "is there anything different to repack", which is the
        # same question one level further down and does not depend on having
        # driven the tools correctly.
        if step.needs_change and step.needs_change in trees:
            now = _tree_stamp(at(mod_root, step.needs_change))
            if now == trees[step.needs_change]:
                result.failed, result.code = step, None
                result.errors = [
                    f"Nothing in {step.needs_change} changed, so repacking it would "
                    "produce a copy of what the game already has. Nothing has been "
                    "copied back."]
                return result

        # their GENERATEZONE only imports a block whose tools reported doing
        # something. Skipped before the step counter, because a step that was
        # never going to run is not a step anybody needs to see go by.
        if step.after and step.after not in worked_labels:
            continue
        if step.after_any and worked_labels.isdisjoint(step.after_any):
            continue
        # a zone where one import went in and its partner did not is HD
        # headers over vanilla pixels, which is the coloured static. Leaving
        # the whole zone out is a gap; putting it in is a broken game.
        broke = [label for label in step.unless_failed if label in failed_labels]
        if broke:
            result.skipped.append((step.label, None))
            result.missed.append(step)
            if step.lands:
                result.gaps.append(step.lands)
            if on_line:
                on_line(f"[left out] {step.label}, because {broke[0]} failed", ERR)
            continue

        if on_step:
            on_step(step, start + index, counted)

        # per step, not per run: when something fails these are the only lines
        # anybody wants, and carrying every step's output to the end of a
        # fourteen hundred step plan costs megabytes to throw away.
        said, complained = [], []

        def line(text, stream=OUT, _said=said, _complained=complained):
            (_complained if stream == ERR else _said).append(text)
            if on_line:
                on_line(text, stream)

        before = _stamp(at(mod_root, step.produces)) if step.produces else None
        # last run's output for this id would otherwise be copied as this
        # run's whenever the tool reports success without rewriting both halves
        if step.tool is not None:
            for source, _ in step.copies:
                try:
                    at(mod_root, source).unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            if step.tool is None:
                code = run_local(step, mod_root, on_line=line, on_share=on_share,
                                 support=support, cancel=cancel)
            else:
                code = run_step(step, mod_root, support, on_line=line, cancel=cancel)
        except Cancelled:
            raise
        except RunnerError as e:
            # a step that could not even be started. Optional means one bad id
            # out of nine hundred does not end the run, and that has to hold
            # here too: a live run died at step 69 of 2536 because a missing
            # working directory raised out of an optional step.
            if not step.optional:
                raise
            line(str(e), ERR)
            code = -1
        made = _produced(mod_root, step, before, said)

        worked = succeeded(step, code, made, said)
        # a count of zero with nothing on stderr is the tool saying it found no
        # HD art for this id, which is true of 102 of the 891 in their lists.
        # Their own batch treats it the same way: no copy, no complaint.
        empty = (not worked and step.success == OK_COUNT
                 and code == 0 and not any(t.strip() for t in complained))

        if worked and step.copies:
            # the loadcopy=1 half of GENERATESINGLE. A tool that reported
            # success and produced nothing to copy did not succeed, whatever it
            # returned, so a missing source fails the step here.
            missing = _place(mod_root, step.copies)
            if missing:
                worked = False
                # split by hand: the plan spells paths with backslashes so it is
                # the same object on either OS, and Path.name would hand back
                # the whole string on anything but Windows
                name = missing.replace("\\", "/").rsplit("/", 1)[-1]
                complained.append(f"{step.label} reported success and left no "
                                  f"{name} to install")
        if settle(step, index, code, made, worked, empty, said, complained):
            return result

    return result
