"""
XIII-2's environment zones.

The same nesting as XIII's: a zone's blob sits loose in alba_data\\zone and its
index is a file inside white_img<v>. Two things differ. Every index is
encrypted, so ff13tool only ever sees a decrypted copy and what goes back is
sealed again. And their .bat keeps the originals under zone\\ rather than
models\\orig, so a run does too, under their names, and clears them once it has
worked (hd_install.clear_caches): the next run reads them out of our backup
again.

    zone\\filelist_<z><v>_orig.win32.bin     the zone's index, out of the backup
    zone\\white_<z><v>_img_orig.win32.bin    its blob, out of the backup
    zone\\<z>\\orig\\bg                        the extracted models, cached
    zone\\<z>\\mod                            where the injector writes
    zone\\filelist_<z><v>.win32.bin          the working pair the imports edit
    zone\\white_<z><v>_img.win32.bin

Every block is an .imgb with a .trb beside it anywhere under bg, which is how
their MODZONE finds them. Nothing is assumed about the folder depth.
"""

from pathlib import Path

from patcher.mods.hd_runner import (COMMIT2, DISCARD, ERR, HARVEST2, OK_COUNT, OK_IMPORTED,
                                    OK_PRODUCED, OPEN, OUT, SEED_PAIR, ZONE_TREE, Step)

FLAG = "-ff132"
COMMIT_SLACK = 64 * 1024 * 1024


def cached_index(zone: str, variant: str) -> str:
    return f"zone\\filelist_{zone}{variant}_orig.win32.bin"


def cached_blob(zone: str, variant: str) -> str:
    return f"zone\\white_{zone}{variant}_img_orig.win32.bin"


def work_index(zone: str, variant: str) -> str:
    return f"zone\\filelist_{zone}{variant}.win32.bin"


def work_blob(zone: str, variant: str) -> str:
    return f"zone\\white_{zone}{variant}_img.win32.bin"


def orig_tree(zone: str) -> str:
    return f"zone\\{zone}\\orig"


def mod_tree(zone: str) -> str:
    return f"zone\\{zone}\\mod"


def harvest_steps(source: Path, variant: str, zones) -> list:
    """each zone's vanilla pair, out of our backup and into zone\\"""
    zones = list(zones)
    if not zones:
        return []
    return [Step(tool=None, action=HARVEST2, args=[str(source), variant, *zones],
                 label=f"reading {len(zones)} zone archives out of the backup")]


def harvest(step: Step, mod_root: Path, on_line=None, on_share=None, cancel=None) -> int:
    """
    args are (folder holding the vanilla pair and blobs, variant, zone, ...).

    The index is kilobytes, so it is written from the backup every time and a
    cache left by their .bat can never stand in for it. A blob is copied again
    unless the cached one is the backup's size: an HD blob is always bigger, so
    a copy their .bat took from a modded game does not survive this.
    """
    from patcher.archive.filelist import load
    from patcher.fileops import write_atomic
    from patcher.mods.hd_runner import _cache_copy, at
    from patcher.mods.hd_zones import read_entry

    def say(text, stream=OUT):
        if on_line:
            on_line(text, stream)

    source, variant, zones = Path(step.args[0]), step.args[1], list(step.args[2:])
    index, image = source / f"filelist{variant}.win32.bin", source / f"white_img{variant}.win32.bin"
    for path in (index, image):
        if not path.is_file():
            say(f"{path.name} is not in {source}", ERR)
            return 1
    try:
        entries = list(load(index).entries())
    except Exception as e:
        say(f"could not read {index.name}: {e}", ERR)
        return 1

    at(mod_root, "zone").mkdir(parents=True, exist_ok=True)
    for n, zone in enumerate(zones):
        name = f"zone/filelist_{zone}{variant}.win32.bin"
        try:
            write_atomic(at(mod_root, cached_index(zone, variant)),
                         read_entry(image, entries, name), durable=True)
        except Exception as e:
            say(f"could not take {name} out of {image.name}: {e}", ERR)
            return 1
        blob = source / f"white_{zone}{variant}_img.win32.bin"
        target = at(mod_root, cached_blob(zone, variant))
        if not blob.is_file():
            say(f"{blob.name} is not in {source}", ERR)
            return 1
        if target.is_file() and target.stat().st_size == blob.stat().st_size:
            continue
        say(f"copying {blob.name} ({blob.stat().st_size / 1024 ** 2:.0f}MB)")
        share = None if on_share is None else \
            (lambda part, at_zone=n: on_share((at_zone + part) / len(zones)))
        _cache_copy(blob, target, cancel=cancel, on_share=share)
    say(f"{len(zones)} zone archives cached in zone")
    return 0


def unpack_steps(zone: str, variant: str) -> list:
    """one zone's archive apart, its bg models kept and the rest thrown away"""
    out = f"zone\\white_{zone}{variant}_img_orig"
    return [
        Step(tool=None, action=OPEN, args=[cached_index(zone, variant), work_index(zone, variant)],
             label=f"opening {zone}'s index"),
        Step(tool="pack", args=["-x", work_index(zone, variant), cached_blob(zone, variant), FLAG],
             label=f"unpacking {zone}", success=OK_PRODUCED, produces=out),
        Step(tool=None, action=ZONE_TREE, args=[out, orig_tree(zone), "bg", "models"],
             label=f"caching {zone} backgrounds"),
    ]


def prepare_steps(source: Path, variant: str, zones) -> list:
    """the half that runs before the blocks are knowable"""
    steps = harvest_steps(source, variant, zones)
    for zone in zones:
        steps += unpack_steps(zone, variant)
    return steps


def blocks(mod_root: Path, zone: str) -> list:
    """
    (folder under the zone's orig, name) for every block, the way MODZONE walks
    them: an .imgb with something in it and its .trb beside it
    """
    from patcher.mods.hd_runner import at

    root = at(mod_root, orig_tree(zone))
    if not root.is_dir():
        return []
    found = []
    for imgb in sorted(root.rglob("*")):
        if not imgb.name.lower().endswith(".imgb") or not imgb.is_file():
            continue
        stem = imgb.name[:-len(".imgb")]
        if imgb.stat().st_size == 0 or not (imgb.parent / f"{stem}.trb").is_file():
            continue
        found.append(("\\".join(imgb.parent.relative_to(root).parts) + "\\", stem))
    return found


def block_steps(mod_root: Path, zone: str, variant: str) -> list:
    """
    their UPDATEIMGB for every block: the injector, then both halves into the
    working pair, .trb first. The imports run from inside zone\\<z>\\mod, since
    the path they are handed is also the name the file goes into the archive
    under.
    """
    steps = []
    for folder, stem in blocks(mod_root, zone):
        made = Step(tool="inject2",
                    args=[stem, f"{orig_tree(zone)}\\{folder}", f"{mod_tree(zone)}\\{folder}",
                          f"{zone}\\", "/s"],
                    label=f"{zone} {folder}{stem} textures", success=OK_COUNT,
                    optional=True, payload=True)
        steps.append(made)
        previous = made.label
        for suffix in (".trb", ".imgb"):
            steps.append(Step(
                tool="pack",
                args=["-i", FLAG, f"..\\..\\filelist_{zone}{variant}.win32.bin",
                      f"..\\..\\white_{zone}{variant}_img.win32.bin", f"{folder}{stem}{suffix}"],
                label=f"{zone} {folder}{stem}{suffix} into the zone archive",
                success=OK_IMPORTED, optional=True, after=previous, cwd=mod_tree(zone)))
            previous = steps[-1].label
    if steps:
        # fresh working copies every time, and an empty mod tree, so a zone that
        # failed last time never feeds this run its leftovers
        steps[0:0] = [
            Step(tool=None, action=SEED_PAIR,
                 args=[cached_index(zone, variant), work_index(zone, variant),
                       cached_blob(zone, variant), work_blob(zone, variant),
                       orig_tree(zone), mod_tree(zone)],
                 label=f"{zone} archive ready to write into"),
            Step(tool=None, action=OPEN, args=[work_index(zone, variant)],
                 label=f"{zone} index opened for ff13tool"),
        ]
    return steps


def plan_for(mod_root: Path, game_dir: Path, variant: str, zones, undo: Path,
             backup: Path) -> list:
    """every zone's blocks, commit and cleanup, in the order a failure survives"""
    steps = []
    for zone in zones:
        made = block_steps(mod_root, zone, variant)
        if not made:
            continue
        worked = [s.label for s in made if s.payload]
        imports = [s.label for s in made if s.success == OK_IMPORTED]
        steps += made + [
            Step(tool=None, action=COMMIT2, after_any=worked, unless_failed=imports,
                 args=[work_blob(zone, variant), work_index(zone, variant), str(game_dir),
                       variant, zone, str(undo), str(backup), cached_index(zone, variant)],
                 label=f"{zone} into the game", lands=f"zone {zone}"),
            Step(tool=None, action=DISCARD,
                 args=[work_blob(zone, variant), work_index(zone, variant), mod_tree(zone)],
                 label=f"{zone} working copies released"),
        ]
    return steps


def commit_zone(step: Step, mod_root: Path, support: Path, on_line=None,
                on_share=None, cancel=None) -> int:
    """
    one finished zone into the game, blob and index as a unit. XIII's commit
    with the encryption either side of ff13tool.

    The zone's own index is sealed first, since the game stores it encrypted
    inside white_img. Then the blob is staged and swapped, the game's index is
    opened into the mod folder, ff13tool imports the zone's index into the
    game's white_img against that copy, and the copy is sealed and written over
    the game's. ImageGuard took the image's length, the span the old entry sat
    in and a copy of the index before any of that, so a failure anywhere after
    the swap puts all three back along with the blob.

    args are (working blob, working index, game dir, variant, zone, undo dir,
    backup dir, the vanilla copy of this zone's index).
    """
    from patcher.archive import crypt
    from patcher.download import free_space
    from patcher.fileops import (CopyCancelled, Stranded, discard, drop, stage_copy, swap,
                                 unswap, write_atomic)
    from patcher.mods.hd2_archive import SealError, open_index, seal, seal_index
    from patcher.mods.hd_install import mark_written, unmark_written
    from patcher.mods.hd_layout import XIII2
    from patcher.mods.hd_runner import CMD, NOT_UNDONE, Cancelled, _spawn, at, succeeded, tool_path
    from patcher.mods.hd_zones import ImageGuard, check_pair

    def say(text, stream=OUT):
        if on_line:
            on_line(text, stream)

    blob_rel, index_rel, game, variant, zone, undo, backup, original_rel = step.args
    blob, index, original = at(mod_root, blob_rel), at(mod_root, index_rel), at(mod_root, original_rel)
    game = Path(game)
    live = XIII2.zone_dir(game) / blob.name
    image = XIII2.sys_dir(game) / f"white_img{variant}.win32.bin"
    listing = XIII2.sys_dir(game) / f"filelist{variant}.win32.bin"
    working = Path(mod_root) / listing.name
    tool = tool_path(support, "pack")
    revert = XIII2.revert

    for needed in (blob, index):
        if not needed.is_file():
            say(f"{zone} was left as it was: {needed.name} was never written", ERR)
            return 1
    try:
        if crypt.looks_decrypted(index.read_bytes()):
            seal_index(index)
    except (OSError, SealError) as e:
        say(f"{zone} was left as it was: its index could not be sealed ({e})", ERR)
        return 1
    problem = check_pair(index, blob, original)
    if problem:
        say(f"{zone} was left as it was: {problem}", ERR)
        return 1

    need = blob.stat().st_size + COMMIT_SLACK
    free = free_space(live.parent)
    if free < need:
        say(f"{zone} was left as it was: going in safely needs {need / 1024 ** 3:.1f} GB "
            f"free on the game's drive and there is {free / 1024 ** 3:.1f} GB", ERR)
        return 1

    try:
        temp, _ = stage_copy(blob, live, cancel=cancel, on_share=on_share)
    except CopyCancelled as e:
        raise Cancelled(str(e)) from None
    except OSError as e:
        say(f"{zone} was left as it was: the new copy could not be written beside the "
            f"game's ({e})", ERR)
        return 1

    try:
        marked = mark_written(Path(backup))
    except OSError as e:
        discard(temp)
        say(f"{zone} was left as it was: the backup's record could not be updated ({e}), "
            "and Revert goes by it", ERR)
        return 1

    def left_as_it_was(text) -> int:
        if marked:
            unmark_written(Path(backup))
        discard(working)
        say(f"{zone} was left as it was: {text}", ERR)
        return 1

    try:
        prev = swap(temp, live)
    except Stranded as e:
        discard(temp)
        say(f"{zone} failed and could not be undone: {e}. Close whatever is holding the "
            f"game's files, then put the game back with {revert} before playing.", ERR)
        return NOT_UNDONE
    except OSError as e:
        discard(temp)
        return left_as_it_was(f"{live.name} could not be replaced ({e}). This usually "
                              "means the game is running.")

    def put_back_blob() -> str:
        try:
            unswap(live, prev)
        except OSError as e:
            where = f", the game's own copy is beside it as {prev.name}" if prev else ""
            return f"{live.name} could not be put back ({e}){where}"
        return ""

    guard = None
    try:
        guard = ImageGuard.take(image, listing, f"zone/{index.name}", Path(undo))
        open_index(listing, working)
    except Exception as e:
        if guard is not None:
            guard.release()
        left = put_back_blob()
        if left:
            say(f"{zone} failed and could not be undone: {left}. Put the game back with "
                f"{revert} before playing.", ERR)
            return NOT_UNDONE
        return left_as_it_was(f"no way to undo an import into {image.name} could be "
                              f"taken ({e})")

    said = []

    def hear(text, stream=OUT):
        if stream != ERR:
            said.append(text)
        say(text, stream)

    args = ["-i", FLAG, working.name, str(image), f"zone\\{index.name}"]
    say(f"> {tool.name} {' '.join(args)}", CMD)
    try:
        code = _spawn([str(tool), *args], Path(mod_root), on_line=hear, label=step.label)
    except OSError as e:
        code, said = None, [str(e)]

    judge = Step(tool="pack", args=[], label=step.label, success=OK_IMPORTED)
    if code is None or not succeeded(judge, code, None, said):
        problem = "ff13tool did not report the import"
    else:
        try:
            write_atomic(listing, seal(working.read_bytes(), working.name), durable=True)
            problem = guard.check(index.read_bytes())
        except (OSError, SealError) as e:
            problem = f"the game's index could not be sealed again ({e})"
    if problem:
        left = "; ".join(x for x in (guard.roll_back(edited=working), put_back_blob()) if x)
        if left:
            say(f"{zone} failed and could not be fully undone: {left}. Put the game back "
                f"with {revert} before playing.", ERR)
            discard(working)
            return NOT_UNDONE
        guard.release()
        return left_as_it_was(problem)

    drop(prev)
    guard.release()
    discard(working)
    say(f"{zone} is in the game")
    return 0
