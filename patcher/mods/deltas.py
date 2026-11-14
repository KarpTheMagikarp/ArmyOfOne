"""
xdelta patches on their own, the way some mods ship a change to one of the
game's files with no .bat round them.

Each is matched to the file it's for by the name xdelta3 wrote in its own
header, else by its own name less the .xdelta. An index and its archive
patched together go through recipes the way the Console Content Patch's do,
record by record, so nothing the drawer or the HD Project put in those
archives is written over. A file the game reads loose off the disk is decoded
whole against the game's copy and goes in over it like any loose file. One for
the exe, half a pair, or a file the game hasn't got is left out and said.
"""

from patcher.mods import vcdiff

# xdelta3's header: target name, its compression, source name, its compression
HEADER = 4096


class DeltaError(ValueError):
    pass


def _app(head: bytes) -> bytes:
    """xdelta3's application header out of a patch's first bytes, b"" without one"""
    if head[:4] != vcdiff.MAGIC or len(head) < 5 or not head[4] & vcdiff.VCD_APPHEADER:
        return b""
    pos = 5
    if head[4] & vcdiff.VCD_DECOMPRESS:
        pos += 1
    if head[4] & vcdiff.VCD_CODETABLE:
        return b""
    try:
        size, pos = vcdiff._varint(head, pos)
    except vcdiff.PatchError:
        return b""
    return head[pos:pos + size]


def named(member: str, head: bytes) -> str:
    """the name of the game's file a patch is for, lowercased"""
    fields = _app(head).decode("utf-8", errors="replace").split("/")
    for field in (fields[2] if len(fields) > 2 else "", fields[0]):
        leaf = field.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        if leaf:
            return leaf
    from patcher.mods.game_files import DELTAS

    leaf = member.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in DELTAS:
        if leaf.endswith(suffix):
            return leaf[:-len(suffix)].rstrip(".")
    return leaf


def sort(deltas: list, read, game) -> tuple:
    """
    (a recipes Option for the archive pairs patched whole, or None,
    {member: path in the game's folder} for loose files, {member: why} for
    the rest)
    """
    from patcher.mods import game_files, recipes
    from patcher.mods.game_files import GAME_EXES, WHOLE

    option, loose, left = recipes.Option(label=""), {}, {}
    halves = {}
    files, _, programs, leaves = game.disk()
    for member in deltas:
        try:
            name = named(member, read(member)[:HEADER])
        except Exception as e:
            left[member] = f"it won't read ({e})"
            continue
        # named the Console Content Patch's way, filelistu.win32.xdelta, the
        # .bin goes without saying
        pair = next((recipes._pair_of(n) for n in (name, f"{name}.bin", f"{name}.win32.bin")
                     if recipes._pair_of(n)), None)
        if pair is not None:
            name = next(n for n in pair if n.startswith(name.split(".")[0]))
        if name in GAME_EXES:
            left[member] = f"it patches {name}, the game's own exe, which stays the one Steam ships"
        elif pair is not None:
            index, archive = pair
            held = halves.setdefault(index, {"archive": archive})
            held["index_patch" if name == index else "archive_patch"] = member
        elif WHOLE.match(name):
            left[member] = f"it patches {name}, a zone's archive, which this doesn't open"
        else:
            spots = [k for k in leaves.get(name, ()) if k.rpartition("/")[0] not in programs]
            if len(spots) > 1:
                left[member] = f"the game has {len(spots)} files called {name}"
            elif not spots:
                left[member] = f"the game hasn't got a {name}"
            elif game_files._disk_spot(spots[0].split("/"), game) != files[spots[0]]:
                # where a loose file couldn't go either: Apply's ReShade, a
                # loader's folder, the archives in sys
                left[member] = (f"it patches {files[spots[0]]}, which is Apply's or another "
                                "mod's, not one of the game's own files")
            else:
                loose[member] = files[spots[0]]
    known = {pair.index.name.lower(): pair for pair in game.pairs}
    for index, held in sorted(halves.items()):
        both = held.get("index_patch") and held.get("archive_patch")
        if not both:
            member = held.get("index_patch") or held.get("archive_patch")
            left[member] = (f"it patches one of {index} and {held['archive']}, which change "
                            "together, without the other")
            continue
        pair = known.get(index)
        if pair is None:
            left[held["index_patch"]] = left[held["archive_patch"]] = \
                f"this game hasn't got {index}"
            continue
        folder = pair.index.parent.name
        option.pairs[f"{folder}/{pair.index.name}"] = recipes.PairPlan(
            index=f"{folder}/{pair.index.name}", archive=f"{folder}/{pair.archive.name}",
            index_patch=held["index_patch"], archive_patch=held["archive_patch"])
    return (option if option.pairs else None), loose, left


def spill(install_dir):
    """
    a folder in the undo store, on the game's drive, for loose files decoded
    out of their patches, after one a crash left an hour or more ago is swept
    """
    import tempfile
    import time
    from pathlib import Path

    from patcher.fileops import clear_tree
    from patcher.paths import undo_store_for

    work = undo_store_for(Path(install_dir)) / "work"
    work.mkdir(parents=True, exist_ok=True)
    for leftover in work.glob("delta-*"):
        try:
            stale = time.time() - leftover.stat().st_mtime > 3600
        except OSError:
            continue
        if stale:
            clear_tree(leftover)
    return Path(tempfile.mkdtemp(prefix="delta-", dir=work))


def size(raw: bytes) -> int:
    """how big the file a patch makes is, before making it"""
    try:
        return vcdiff.Patch(raw).size
    except vcdiff.PatchError as e:
        raise DeltaError(str(e)) from None


def whole(raw: bytes, original, out) -> int:
    """
    a loose file patched whole into `out`, open to write and read back, a
    window at a time so a movie's patch never holds the movie, and how many
    bytes that came to. `original` is the game's copy, a path. Each window's
    checked against the adler32 xdelta3 keeps, so a patch made from another
    version of the file says so rather than writing noise over it
    """
    try:
        patch = vcdiff.Patch(raw)
    except vcdiff.PatchError as e:
        raise DeltaError(str(e)) from None
    written = 0

    def target(at: int, size: int) -> bytes:
        out.flush()
        out.seek(at)
        chunk = out.read(size)
        out.seek(written)
        return chunk

    with open(original, "rb") as handle:
        def source(at: int, size: int) -> bytes:
            handle.seek(at)
            return handle.read(size)

        try:
            for window in patch.windows:
                chunk = patch.decode(window, source, target)
                out.write(chunk)
                written += len(chunk)
        except vcdiff.PatchError as e:
            raise DeltaError(str(e)) from None
    return written
