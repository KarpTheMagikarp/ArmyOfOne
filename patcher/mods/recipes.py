"""
A mod's .bat, read rather than run.

Some mods are a .bat around their author's tools: the Console Content Patch
moves XIII-2's archives aside, runs xdelta over them to put the 1.0 release's
costumes back, then injects two files with Cocoon. Running that means running
somebody's exe over the archives with no record of what was under, and xdelta
writes the whole 1.5GB archive out again in the Steam layout, taking whatever
the HD Project or the drawer appended with it. So the .bat is read instead:
every copy, move and delete is followed on paper, and what ends up in the
game's archives comes out as the two steps the drawer does safely:

    xdelta over an index and its archive   the records the patch changes,
                                           decoded out of it (vcdiff.py)
    Cocoon inject2 <index> <archive> <dir> <path>
                                           one file into one archive

A menu (`echo 1. ...`, `set /p`, `if '%choice%'=='1' goto ...`) is a choice
per option. Options putting different files in the same place are versions
of the mod; ones that don't are steps, done in the menu's order, which is how
the Console Content Patch's "revert to 1.0 first" reads. An option that does
anything else to the game's files, downloads, or runs another tool is left
out with the reason, and a .bat with nothing left is the drop it always was.
"""

import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath

XDELTA = ("xdelta", "xdelta3", "xdelta.exe", "xdelta3.exe")
COCOON = ("cocoon", "cocoon.exe")
# lines that only talk to the console or wait on it
QUIET = {"echo", "echo.", "cls", "pause", "title", "color", "setlocal", "endlocal", "chcp",
         "mode", "timeout", "set"}

MENU = re.compile(r"^echo\s+(\d+)\s*[.):-]\s*(.+?)\s*$", re.I)
CHOICE = re.compile(r"^if\s+(?:/i\s+)?[\"']?%(\w+)%[\"']?\s*==\s*[\"']?([^\"'\s]+)[\"']?"
                    r"\s+goto\s+:?(\S+)\s*$", re.I)
PAIRS = re.compile(r"^(filelist|white_img)([a-z0-9]*)\.win32\.(bin|org)$")


@dataclass
class PairPlan:
    """what one of the game's index and archive pairs ends up as"""
    index: str                  # relative to the data folder: sys/filelistu.win32.bin
    archive: str
    index_patch: str = ""       # the drop's xdelta for each, when the pair is patched
    archive_patch: str = ""
    index_whole: str = ""       # or the drop's own copy of each, a mod that ships them whole
    archive_whole: str = ""
    injects: list = field(default_factory=list)   # (game path, drop member), in order


@dataclass
class Option:
    label: str
    pairs: dict = field(default_factory=dict)     # index -> PairPlan
    problem: str = ""                             # why it can't be done here


@dataclass
class Recipe:
    bat: str                    # the .bat's member in the drop
    options: list               # its menu's, or one for a .bat without a menu

    def usable(self) -> list:
        return [o for o in self.options if not o.problem and o.pairs]


def _norm(path: str) -> str:
    """a .bat's path as a key: forward slashes, lower case, ./ and .. worked out"""
    parts = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == ".." and parts and parts[-1] != "..":
            parts.pop()
            continue
        parts.append(part.lower())
    return "/".join(parts)


def _join(cwd: str, path: str) -> str:
    path = path.strip().strip('"')
    if re.match(r"^[a-z]:[\\/]", path, re.I) or path.startswith(("\\", "/")):
        # somewhere else on the PC, nothing the drop or the game has
        return "?" + _norm(path)
    return _norm(f"{cwd}/{path}" if cwd else path)


def _tokens(line: str) -> list:
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split, lexer.escape, lexer.commenters = True, "", ""
    try:
        return list(lexer)
    except ValueError:
        return line.split()


def _lines(text: str) -> tuple:
    """(statements as (words, line), label -> the statement after it)"""
    out, labels = [], {}
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().lstrip("@").strip()
        lowered = line.lower()
        if not line or line.startswith("::") or lowered == "rem" \
                or lowered.startswith(("rem ", "rem\t")):
            continue
        if line.startswith(":"):
            name = line[1:].split()
            labels[name[0].lower() if name else ""] = len(out)
            continue
        out.append((_tokens(line), line))
    return out, labels


def read(text: str, bat: str, has, dirs=()) -> Recipe:
    """
    what a .bat does to the game, per menu option. `has(key)` gives the drop's
    member for a path relative to the .bat's own folder, or None, and `dirs`
    is the folders the drop has there: what its tools make in them on the way
    (Cocoon's _chunks folder) is theirs. Any other path is the game's,
    relative to the data folder the .bat runs in
    """
    lines, labels = _lines(text)
    numbered, picked = {}, {}
    prompt = next((n for n, (words, _) in enumerate(lines)
                   if words and words[0].lower() == "set" and len(words) > 1
                   and words[1].lower() == "/p"), -1)
    for words, line in lines:
        menu, choice = MENU.match(line), CHOICE.match(line)
        if menu:
            numbered.setdefault(menu.group(1), menu.group(2))
        if choice:
            picked.setdefault(choice.group(2), choice.group(3).lower())
    options = []
    for number, label in numbered.items():
        target = picked.get(number)
        if prompt < 0 or target not in labels:
            continue
        option = Option(label=re.sub(r"\s+", " ", label.strip('"')).rstrip("."))
        _walk(lines, labels, labels[target], prompt, option, has, dirs)
        options.append(option)
    if not options:
        option = Option(label="")
        _walk(lines, labels, 0, -1, option, has, dirs)
        options.append(option)
    return Recipe(bat=bat, options=options)


def _walk(lines: list, labels: dict, at: int, prompt: int, option: Option, has,
          dirs=()) -> None:
    """one option followed on paper, to where it goes back to the menu or ends"""
    files, cwd, steps, initial = {}, "", 0, set()
    dirs = {d.lower() for d in dirs}

    def value(key: str):
        if key in files:
            return files[key]
        member = has(key)
        if member:
            return ("drop", member)
        if key.startswith(("?", "..")):
            return ("elsewhere", key)
        if key.split("/", 1)[0] in dirs:
            return ("scratch", key)
        initial.add(key)
        return ("game", key)

    def fail(why: str) -> None:
        option.problem = option.problem or why

    while at < len(lines) and steps < 10_000 and not option.problem:
        steps += 1
        words, line = lines[at]
        at += 1
        verb = words[0].lower()
        if verb in QUIET or verb.startswith("echo"):
            continue
        if verb == "goto":
            target = words[1].lstrip(":").lower() if len(words) > 1 else ""
            where = labels.get(target)
            if where is None or where <= prompt:
                break
            at = where
            continue
        if verb == "exit":
            break
        if verb == "if":
            if not CHOICE.match(line):
                fail(f"its .bat decides something this can't follow ({line})")
            continue
        if any("%" in w for w in words[1:]):
            fail(f"its .bat runs {line!r} with a variable in it, which this can't follow")
            break
        if verb in ("cd", "chdir", "pushd"):
            path = [w for w in words[1:] if w.lower() != "/d"]
            cwd = _join(cwd, path[0]) if path else cwd
            continue
        if verb == "popd":
            cwd = ""
            continue
        if verb in ("copy", "move", "ren", "rename"):
            paths = [w for w in words[1:] if not w.startswith("/")]
            if len(paths) != 2 or any(c in "".join(paths) for c in "*?+"):
                fail(f"its .bat runs {line!r}, which this can't follow")
                break
            source = _join(cwd, paths[0])
            if verb in ("ren", "rename"):
                target = _join(source.rpartition("/")[0], paths[1])
            else:
                target = _join(cwd, paths[1])
            # what it lands on counts as the game's, if it was
            value(target)
            files[target] = value(source)
            if verb != "copy":
                files[source] = ("gone",)
            continue
        if verb in ("del", "erase", "rd", "rmdir"):
            for w in words[1:]:
                if w.startswith("/"):
                    continue
                gone = _join(cwd, w)
                # one of the game's, if it's nothing the drop has or the .bat made
                value(gone)
                for key in [k for k in list(files) + [gone]
                            if k == gone or k.startswith(gone + "/")]:
                    files[key] = ("gone",)
            continue
        if verb in ("md", "mkdir"):
            continue
        tool = PurePosixPath(_norm(words[0])).name
        if tool in XDELTA:
            decoded = _xdelta(words[1:])
            if decoded is None:
                fail(f"its .bat runs xdelta in a way this doesn't read ({line})")
                break
            source, patch, out = (_join(cwd, w) for w in decoded)
            files[out] = ("xdelta", value(source), value(patch))
            continue
        if tool in COCOON:
            if len(words) != 6 or words[1].lower() != "inject2":
                fail(f"its .bat runs Cocoon in a way this doesn't read ({line})")
                break
            index, archive = _join(cwd, words[2]), _join(cwd, words[3])
            inner = _norm(words[5])
            put = value(_join(_join(cwd, words[4]), inner))
            files[index] = ("inject", value(index), inner, put)
            files[archive] = ("inject", value(archive), inner, put)
            continue
        fail(f"its .bat runs {words[0]}, which this doesn't do ({line})")
        break
    if not option.problem:
        _settle(files, initial, option)


def _xdelta(args: list):
    """(source, patch, output) for an xdelta decode, None for anything else"""
    source, rest, decode, i = None, [], False, 0
    while i < len(args):
        word = args[i]
        i += 1
        if word.startswith("-") and len(word) > 1:
            flags = word[1:]
            for n, flag in enumerate(flags):
                if flag == "e":
                    return None
                if flag == "d":
                    decode = True
                elif flag == "s":
                    tail = flags[n + 1:]
                    if tail:
                        source = tail
                    elif i < len(args):
                        source, i = args[i], i + 1
                    break
            continue
        rest.append(word)
    if not decode or source is None or len(rest) != 2:
        return None
    return source, rest[0], rest[1]


def _games(value) -> bool:
    """whether a value is one of the game's files, or made from one"""
    if not value:
        return False
    return value[0] == "game" or any(isinstance(v, tuple) and _games(v) for v in value[1:])


def _unwrap(value) -> tuple:
    """(injections in order, what's under them, the xdelta patch over that or None)"""
    injects = []
    while value and value[0] == "inject":
        injects.append((value[2], value[3]))
        value = value[1]
    injects.reverse()
    if value and value[0] == "xdelta":
        return injects, value[1], value[2]
    return injects, value, None


def _pair_of(name: str):
    """(index name, archive name) for either half of an archive pair, or None"""
    hit = PAIRS.match(name)
    return (f"filelist{hit.group(2)}.win32.bin", f"white_img{hit.group(2)}.win32.bin") \
        if hit else None


def _settle(files: dict, initial: set, option: Option) -> None:
    """what one option leaves the game's files as, as pair plans, or why not"""
    for key, now in sorted(files.items()):
        if key not in initial:
            if now[0] != "gone" and _games(now):
                option.problem = (f"its .bat leaves the game's files somewhere else ({key}), "
                                  "which this doesn't follow")
                return
            continue
        if now == ("game", key):
            continue
        if now[0] == "gone":
            option.problem = f"its .bat deletes the game's {key}"
            return
        injects, base, patch = _unwrap(now)
        names = _pair_of(PurePosixPath(key).name)
        if base != ("game", key) or names is None:
            option.problem = f"its .bat puts something else in place of the game's {key}"
            return
        folder = PurePosixPath(key).parent
        index, archive = (str(folder / n) if str(folder) != "." else n for n in names)
        plan = option.pairs.setdefault(index, PairPlan(index=index, archive=archive))
        if patch is not None:
            if patch[0] != "drop":
                option.problem = f"its .bat patches {key} with something it made on the way"
                return
            if key == index:
                plan.index_patch = patch[1]
            else:
                plan.archive_patch = patch[1]
        if any(put[0] != "drop" for _, put in injects):
            option.problem = f"its .bat injects something into {key} that isn't in the mod"
            return
        said = [(path, put[1]) for path, put in injects]
        if key == index:
            plan.injects = said
        elif plan.injects != said:
            option.problem = f"its .bat injects different files into {index} and {archive}"
            return
    for plan in option.pairs.values():
        if bool(plan.index_patch) != bool(plan.archive_patch):
            option.problem = (f"its .bat patches only one of {plan.index} and {plan.archive}, "
                              "which have to change together")
            return


def steps(recipe: Recipe) -> tuple:
    """
    (the options to do, in the menu's order, the labels to choose between).
    Options putting different files in the same place are versions of the mod
    and come back to be picked between; the rest are steps, all of them done
    """
    usable = recipe.usable()
    claimed = {}
    for option in usable:
        for pair in option.pairs.values():
            for path, _ in pair.injects:
                claimed.setdefault((pair.index, path), set()).add(option.label)
    if any(len(labels) > 1 for labels in claimed.values()):
        return [], [o.label for o in usable]
    return usable, []


def wholes(members: list, game) -> tuple:
    """
    (an Option putting in the index and archive pairs a drop ships whole, or
    None, {member: why} for half a pair or one this game hasn't got). A zone's
    blob or anything else not a pair is the caller's
    """
    option, left, halves = Option(label=""), {}, {}
    for member in members:
        leaf = member.replace("\\", "/").rsplit("/", 1)[-1].lower()
        pair = _pair_of(leaf)
        if pair is not None:
            held = halves.setdefault(pair[0], {"archive": pair[1]})
            held["index" if leaf == pair[0] else "archive_member"] = member
    known = {pair.index.name.lower(): pair for pair in game.pairs}
    for index, held in sorted(halves.items()):
        if not held.get("index"):
            left[held["archive_member"]] = (f"the {index} that says what's in it isn't with it, "
                                            "so there's no telling what it changes")
            continue
        if not held.get("archive_member"):
            left[held["index"]] = f"the {held['archive']} it lists the files of isn't with it"
            continue
        pair = known.get(index)
        if pair is None:
            left[held["index"]] = left[held["archive_member"]] = f"this game hasn't got {index}"
            continue
        folder = pair.index.parent.name
        option.pairs[f"{folder}/{pair.index.name}"] = PairPlan(
            index=f"{folder}/{pair.index.name}", archive=f"{folder}/{pair.archive.name}",
            index_whole=held["index"], archive_whole=held["archive_member"])
    return (option if option.pairs else None), left


class BuildError(Exception):
    pass


WORK = "work"


def sweep(install_dir) -> None:
    """what an earlier build left, a crash or a power cut mid build"""
    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    work = undo_store_for(install_dir) / WORK
    for leftover in work.glob("recipe-*") if work.is_dir() else ():
        clear_tree(leftover)


def build(install_dir, source, chosen: list, progress=None):
    """
    (a folder laid out the way the drawer reads a drop, voice set by voice
    set, and the look() of it) for the options picked, in order. The caller
    installs it as one mod and clears the folder either way
    """
    import tempfile
    from pathlib import Path

    from patcher.mods import game_files
    from patcher.paths import undo_store_for

    install_dir = Path(install_dir)
    data = game_files.data_dir(install_dir)
    game = game_files.read_game(install_dir)
    if data is None or not game.pairs:
        raise BuildError("couldn't find this game's archives")
    by_index, merged = {pair.index: pair for pair in game.pairs}, {}
    for option in chosen:
        for name, plan in option.pairs.items():
            into = merged.setdefault(name, PairPlan(index=plan.index, archive=plan.archive))
            if plan.index_patch or plan.index_whole:
                if into.index_patch or into.index_whole or into.injects:
                    raise BuildError(f"its .bat patches {plan.index} after changing it, which "
                                     "this can't follow")
                into.index_patch, into.archive_patch = plan.index_patch, plan.archive_patch
                into.index_whole, into.archive_whole = plan.index_whole, plan.archive_whole
            into.injects += plan.injects
    present = {name: plan for name, plan in merged.items()
               if (data / plan.index).resolve() in {p.resolve() for p in by_index}}
    if not present:
        raise BuildError("none of the archives its .bat changes are in this game")
    sweep(install_dir)
    (undo_store_for(install_dir) / WORK).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="recipe-", dir=undo_store_for(install_dir) / WORK))
    found = game_files.Look()
    try:
        for n, (name, plan) in enumerate(sorted(present.items())):
            pair = next(p for p in game.pairs if p.index.resolve() == (data / name).resolve())
            laid, renames = set(), {}

            def lay(path: str, plain: bytes) -> None:
                # onto the disk as it comes, since a whole archive's changes
                # can be gigabytes
                member = f"{pair.voice}/{path}"
                target = work / member
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(plain)
                if path not in laid:
                    found.members_at.setdefault(path, []).append(member)
                laid.add(path)
                found.picks[(path, pair.voice)] = member
                found.sizes[member] = len(plain)
                found.matched.add(path)
                found.voices[member] = pair.voice

            if plan.index_patch:
                for path, plain, rename in restore(install_dir, pair,
                                                   source.read(plan.index_patch),
                                                   source.read(plan.archive_patch)):
                    lay(path, plain)
                    if rename is not None:
                        renames[path] = rename
            elif plan.index_whole:
                tick = (lambda done, of, at=n: progress(at * of + done, len(present) * of)) \
                    if progress is not None else None
                with source.opened(plan.archive_whole) as archive:
                    for path, plain, rename in whole(install_dir, pair,
                                                     source.read(plan.index_whole), archive, tick):
                        lay(path, plain)
                        if rename is not None:
                            renames[path] = rename
            for path, member in plan.injects:
                lay(path, source.read(member))
                renames.pop(path, None)
            if renames:
                found.renames[pair.name(install_dir)] = renames
            # a path the .bat put back here can be listed by another index of
            # the voice set too, which it never touched
            if laid:
                found.only.add(pair.name(install_dir))
            if progress is not None:
                progress(n + 1, len(present))
        if not found.picks and all(plan.index_whole for plan in present.values()):
            raise BuildError("its archives hold nothing Steam's don't, so there's nothing in "
                             "them to put in")
    except BaseException:
        from patcher.fileops import clear_tree
        clear_tree(work)
        raise
    return work, found


def restore(install_dir, pair, index_patch: bytes, archive_patch: bytes) -> list:
    """
    (path, plain bytes, (code, the name its record has now) or None) for every
    record an xdelta over this pair really changes. The patch was made against
    Steam's files, so that's what its copies read: the HD backup's archive when
    there is one, since an HD install repacks the live archive, else the live
    archive, whose own region only the drawer's appends have grown past. Each
    window it decodes is checked against the patch's own adler32, so a game
    that isn't what the patch expects says so instead of putting in noise, and
    that's also how the index it was made from is told from one the drawer has
    rewritten since
    """
    import zlib
    from bisect import bisect_right

    from patcher.archive.filelist import by_code, parse
    from patcher.mods import game_files
    from patcher.mods.vcdiff import Patch, PatchError
    from patcher.paths import undo_store_for

    spares = undo_store_for(install_dir) / "hdproject"
    spare_index, spare_archive = spares / pair.index.name, spares / pair.archive.name
    backed = spare_index.is_file() and spare_archive.is_file()
    base_archive = spare_archive if backed else pair.archive
    try:
        index_patch, archive_patch = Patch(index_patch), Patch(archive_patch)
    except (PatchError, ValueError) as e:
        raise BuildError(f"its patch for {pair.index.name} doesn't fit this game's ({e})") from None
    # the index as Steam had it: under the drawer's mods, as the first of them
    # found it, then the HD backup's, then the game's own
    sources = game_files.befores(pair.index) + ([spare_index.read_bytes()] if backed else []) \
        + [pair.index.read_bytes()]
    raw, problem = None, None
    for candidate in sources:
        try:
            wanted = parse(index_patch.read(0, index_patch.size,
                                            lambda at, n, c=candidate: c[at:at + n]))
        except (PatchError, ValueError) as e:
            problem = problem or e
            continue
        raw = candidate
        break
    if raw is None:
        raise BuildError(f"its patch for {pair.index.name} doesn't fit this game's "
                         f"({problem})") from None
    vanilla = by_code(parse(raw))
    if not backed:
        # what the drawer's mods hold is theirs; what's under them is the game's
        under = game_files.bottoms(pair.index)
        vanilla = {code: [game_files._entry_at(e.path, under[game_files._key(e.path)])
                          if game_files._key(e.path) in under else e for e in records]
                   for code, records in vanilla.items()}
    ops = [op for window in archive_patch.windows for op in archive_patch.ops(window)]
    starts = [op[0] for op in ops]

    def untouched(target, original) -> bool:
        """whether the patch copies this record's bytes straight from where they were"""
        if (target.uncompressed, target.stored) != (original.uncompressed, original.stored):
            return False
        lo, hi = target.byte_offset, target.byte_offset + target.stored
        i = max(0, bisect_right(starts, lo) - 1)
        while i < len(ops) and ops[i][0] < hi:
            at, size, kind, where = ops[i]
            if at + size > lo and (kind != "source"
                                   or where - at != original.byte_offset - lo):
                return False
            i += 1
        return True

    live, listed = game_files.coded(pair.index), game_files.entries(pair.index)
    changed, skipped = [], 0
    for code, records in by_code(wanted).items():
        before = vanilla.get(code, [])
        if len(records) != 1 or len(before) != 1:
            # a code on several records can't be renamed or told apart, so one
            # the patch changes is something this can't put back
            if len(records) != len(before) or not all(
                    game_files._key(t.path) == game_files._key(o.path) and untouched(t, o)
                    for t, o in zip(records, before)):
                skipped += 1
            continue
        target, original = records[0], before[0]
        if game_files._key(target.path) == game_files._key(original.path) \
                and untouched(target, original):
            continue
        changed.append((code, target, original))
    if skipped:
        raise BuildError(f"its patch changes {skipped} records of {pair.index.name} that share a "
                         "code with others, which can't be told apart")
    handle, cache = base_archive.open("rb"), {}

    def source(at: int, n: int) -> bytes:
        handle.seek(at)
        return handle.read(n)

    out = []
    try:
        for code, target, original in changed:
            stored = archive_patch.read(target.byte_offset, target.stored, source, cache=cache)
            if target.is_stub:
                plain = b""
            elif target.is_compressed:
                plain = zlib.decompress(stored)
            else:
                plain = stored
            if len(plain) != target.uncompressed:
                raise BuildError(f"{target.path} came out of its patch the wrong size")
            path, now = game_files._key(target.path), live.get(code)
            if path == game_files._key(original.path) and not original.is_stub \
                    and plain == _held(handle, original):
                # copied out of another place holding the same bytes, the copy
                # Steam's XIII-2 keeps of c201's and c204's, and no change: over
                # a costume from the drawer it'd put the game's back
                continue
            rename = None
            if now is None or game_files._key(now.path) != path:
                if now is None:
                    raise BuildError(f"{pair.index.name} has nothing under {target.path}'s "
                                     "code to put it back on")
                if path in listed:
                    raise BuildError(f"{target.path} is already in {pair.index.name} under "
                                     "another code")
                rename = (code, now.path)
            out.append((path, plain, rename))
    except PatchError as e:
        where = "the HD backup's copy of" if backed else "this game's"
        raise BuildError(f"{where} {pair.archive.name} isn't the Steam file its patch was made "
                         f"from ({e})") from None
    except zlib.error as e:
        raise BuildError(f"a file came out of its patch that won't unpack ({e})") from None
    finally:
        handle.close()
    return out


def whole(install_dir, pair, index_raw: bytes, archive, progress=None):
    """
    restore()'s answers, one at a time, for a mod that ships an index and its
    archive whole,
    the kind whose readme says to drag them into white_data\\sys: every
    record of theirs held against Steam's under the same code, and only the
    ones whose bytes really differ, so whatever the drawer and the HD Project
    have in the game's own pair stays. `archive` is the mod's, a file to seek
    through, since one is gigabytes
    """
    import zlib

    from patcher.archive.filelist import by_code, parse
    from patcher.mods import game_files
    from patcher.paths import undo_store_for

    spares = undo_store_for(install_dir) / "hdproject"
    spare_index, spare_archive = spares / pair.index.name, spares / pair.archive.name
    backed = spare_index.is_file() and spare_archive.is_file()
    try:
        theirs = by_code(parse(index_raw))
    except Exception:
        raise BuildError(f"its {pair.index.name} doesn't read as one of the game's indexes, so "
                         "it's damaged or not what its name says") from None
    # Steam's index: the HD backup's, an HD install having repacked the live
    # one, else as it was before the drawer's first mod, else the live one
    # with what's under the drawer's mods for the paths they hold
    befores = [] if backed else game_files.befores(pair.index)
    steam = by_code(parse(spare_index.read_bytes() if backed else
                          befores[0] if befores else pair.index.read_bytes()))
    if not backed:
        under = game_files.bottoms(pair.index)
        steam = {code: [game_files._entry_at(e.path, under[game_files._key(e.path)])
                        if game_files._key(e.path) in under else e for e in records]
                 for code, records in steam.items()}
    added = [records[0].path for code, records in theirs.items() if code not in steam]
    if added:
        raise BuildError(f"its {pair.index.name} lists {len(added)} file"
                         f"{'' if len(added) == 1 else 's'} this game's doesn't, {added[0]} "
                         "first, so it was made for another version of the game")
    live, listed = game_files.coded(pair.index), game_files.entries(pair.index)

    def stored(handle, entry) -> bytes:
        handle.seek(entry.byte_offset)
        raw = handle.read(entry.stored)
        if len(raw) != entry.stored:
            raise BuildError(f"its {pair.archive.name} ends before {entry.path} does, so it's "
                             "cut short")
        return raw

    def plain(raw: bytes, entry) -> bytes:
        return b"" if entry.is_stub else zlib.decompress(raw) if entry.is_compressed else raw

    codes = sorted(theirs)
    with (spare_archive if backed else pair.archive).open("rb") as ours:
        try:
            for n, code in enumerate(codes):
                if progress is not None and n % 64 == 0:
                    progress(n, len(codes))
                records, before = theirs[code], steam[code]
                if len(records) != 1 or len(before) != 1:
                    # a code on several records, Steam's copy of c201 under
                    # c204's say, is only fine left as Steam has it
                    if len(records) != len(before) or any(
                            game_files._key(t.path) != game_files._key(o.path)
                            or stored(archive, t) != stored(ours, o)
                            for t, o in zip(records, before)):
                        raise BuildError(f"it changes {records[0].path}, which shares its code "
                                         "with other records, so they can't be told apart")
                    continue
                target, original = records[0], before[0]
                mine = stored(archive, target)
                same = game_files._key(target.path) == game_files._key(original.path)
                if same and mine == stored(ours, original):
                    continue
                made = plain(mine, target)
                if len(made) != target.uncompressed:
                    raise BuildError(f"{target.path} in its {pair.archive.name} unpacks to the "
                                     "wrong size")
                if same and made == plain(stored(ours, original), original):
                    # packed again by their tool, the same file underneath
                    continue
                path, now = game_files._key(target.path), live.get(code)
                rename = None
                if now is None or game_files._key(now.path) != path:
                    if now is None:
                        raise BuildError(f"{pair.index.name} has nothing under {target.path}'s "
                                         "code to put it on")
                    if path in listed:
                        raise BuildError(f"{target.path} is already in {pair.index.name} under "
                                         "another code")
                    rename = (code, now.path)
                yield path, made, rename
        except zlib.error as e:
            raise BuildError(f"a file in its {pair.archive.name} won't unpack ({e})") from None


def _held(handle, entry) -> bytes | None:
    """what a record holds in Steam's archive, None when that won't read"""
    import zlib

    from patcher.mods.hd_zones import _record

    try:
        return _record(handle, entry)
    except (OSError, ValueError, zlib.error):
        return None


def find(source, members) -> "Recipe | None":
    """
    the first .bat in a drop that does something this reads to the game's
    archives, with paths read from its own folder, or None
    """
    names = sorted(m for m, _ in members if m.lower().endswith((".bat", ".cmd")))
    lowered = {m.lower(): m for m, _ in members}
    for bat in names:
        folder = bat.rpartition("/")[0].lower()
        inside = [m[len(folder) + 1:] if folder else m for m in lowered
                  if not folder or m.startswith(folder + "/")]
        dirs = {m.split("/", 1)[0] for m in inside if "/" in m}
        try:
            text = source.read(bat).decode("utf-8", errors="replace")
        except Exception:
            continue
        recipe = read(text, bat, lambda key, f=folder: lowered.get(f"{f}/{key}" if f else key),
                      dirs)
        if recipe.usable():
            return recipe
    return None
