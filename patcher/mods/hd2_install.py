"""
FF XIII-2 HD, installed from here instead of by its .bat.

Their sequence, which is ours too:

    white_img<v> copied in, its index decrypted, ff13tool -x    the whole archive
    models\\orig\\chr\\<group> seeded                              the originals
    FFXIII2_HDProject on every .imgb in five groups               into the tree
    FF XIII HD Models on each id PCModels/NPCModels names         over the top
    ff13tool -c, index sealed, both into alba_data\\sys
    each zone: unpacked, injected, both halves of every block
               imported, then blob and index into the game         see hd2_zones

Theirs has no backup and no uninstall at all, installs everything or nothing,
checks no free space, and writes a zero checksum into the rebuilt index. The
backup, the checks, the swap into the game and Revert are the same code XIII's
install runs; only the steps are this game's.
"""

from pathlib import Path

from patcher.fileops import discard
from patcher.mods import hd2_zones
from patcher.mods.hd2_recipe import (GROUPS, NAME, RecipeError, child, model_ids,
                                     recipe_paths)
from patcher.mods.hd_install import (SWAP_SLACK, InstallError, _copy_back, _manifest,
                                     back_up, clear_caches, clear_leftovers, dropped_in,
                                     exclusive, in_use, left_as_they_are, left_out, mark_cache,
                                     measure, nothing_clean, outside, prepare, suspect_backup,
                                     tool_notes, unbuildable, zones_unbuildable)
from patcher.mods.hd_layout import XIII2
from patcher.mods.hd_runner import (DISCARD, MODEL_FILES, OK_COUNT, OK_PRODUCED, OK_ZERO,
                                    SEAL, SEED, TOOLS, Result, Step, run, usable_id)

FLAG = "-ff132"
REVERT = XIII2.revert

# FF XIII HD Models gets through a recipe in seconds, and their .bat users have
# watched it sit on one for hours. hd2_models_fix takes out the cause we know
# of; this is for any we don't. Ending it only costs that id its mesh edits,
# since its textures are already in the tree
MODEL_LIMIT = 5 * 60


def resolve(found: dict, categories: list, selected) -> tuple:
    """
    (groups, model lists, zones) the ticked categories come to.

    Groups in their MODTYPE order and the PC list before the NPC one, as their
    .bat runs them. The order matters where they overlap: a model the recipes
    rebuild has already had its textures done, and the model tool redoes those
    as it writes, so it has to go last.
    """
    chosen = [c for c in categories if c["id"] in selected]
    if not chosen:
        raise InstallError("nothing selected that this can install")
    blocked = [c["label"] for c in chosen if c.get("supported") is False]
    if blocked:
        raise InstallError(f"{', '.join(blocked)} cannot be installed from here yet, so "
                           "nothing has been changed. Untick it and try again.")

    have = set(found.get("groups") or [])
    empty = [c["label"] for c in chosen
             if not ((set(c.get("groups") or []) & have) or c.get("models")
                     or (c.get("zones") and found.get("zones")))]
    if empty:
        raise InstallError(f"{', '.join(empty)} would install nothing from this copy of "
                           f"{NAME}, so nothing has been changed. Its textures folder may be "
                           "incomplete; unpacking the download again usually fixes it.")

    groups = [g for g in GROUPS if any(g in (c.get("groups") or []) for c in chosen)]
    lists = sorted(((c["groups"][0], c["models"]) for c in chosen
                    if c.get("models") and c.get("groups")),
                   key=lambda pair: pair[1] != "PCModels.txt")
    zones = list(found.get("zones") or []) if any(c.get("zones") for c in chosen) else []
    return groups, lists, zones


def texture_steps(entries: list, found: dict, groups, variant: str, leave=()) -> list:
    """
    their MODTYPE, planned off the index rather than a directory walk, which
    lists the same files: every .imgb under chr\\<group> that holds anything
    and has its .trb, one injector run each
    """
    textures = Path(found["textures"]) if found.get("textures") else None
    tree = f"white_img{variant}"
    paths = {e.path.replace("\\", "/").lower(): e for e in entries}
    steps = []
    for group in groups:
        folder = child(textures, group) if textures is not None else None
        if folder is None:
            continue
        for path in sorted(p for p in paths if p.startswith(f"chr/{group}/")):
            parts = path.split("/")
            if len(parts) != 5 or not path.endswith(".win32.imgb") or paths[path].is_stub:
                continue
            member = parts[2]
            if (not usable_id(member) or parts[4] != f"{member}.win32.imgb"
                    or path[:-len("imgb")] + "trb" not in paths or member in leave):
                continue
            rel = "\\".join(["chr", group, member, "bin", ""])
            stem = f"{member}.win32"
            steps.append(Step(
                tool="inject2", args=["/s", stem, "models\\orig\\" + rel, "models\\mod\\" + rel,
                                      folder.name + "\\"],
                label=f"{member} textures", success=OK_COUNT, optional=True, payload=True,
                copies=[("models\\mod\\" + rel + stem + s, f"{tree}\\{rel}{stem}{s}")
                        for s in (".trb", ".imgb")]))
    return steps


def model_steps(found: dict, lists, variant: str, models: str = "models2", leave=()) -> list:
    """
    their MODEL, once per id the lists name that has a recipe beside it.
    models is the tool key, theirs or the fixed copy of it
    """
    data = Path(found["data"])
    tree = f"white_img{variant}"
    steps = []
    for group, listing in lists:
        for member in model_ids(child(data, listing)):
            recipe = child(data, f"{member}.txt")
            if recipe is None or member in leave:
                continue
            try:
                halves = recipe_paths(recipe, group, member)
            except (RecipeError, OSError):
                continue
            steps.append(Step(
                tool=models, args=["-s", f"{data.name}\\{recipe.name}"],
                label=f"{member} models", success=OK_ZERO, optional=True, payload=True,
                copies=[("models\\mod\\" + p, f"{tree}\\{p}") for p in halves],
                limit=MODEL_LIMIT))
    return steps


def chr_steps(entries: list, found: dict, groups, lists, variant: str,
              source: Path | None = None, models: str = "models2", leave=()) -> list:
    """
    the whole character pass, or [] when nothing ticked touches white_img.
    `leave` is ids not to build, which hd_install.unbuildable() found nothing
    clean to build from
    """
    leave = {i.lower() for i in leave}
    work = (texture_steps(entries, found, groups, variant, leave=leave)
            + model_steps(found, lists, variant, models=models, leave=leave))
    if not work:
        return []
    tree = f"white_img{variant}"
    seeded = [g for g in GROUPS if g in set(groups) | {group for group, _ in lists}]
    return [
        Step(tool="pack", args=["-x", f"filelist{variant}.win32.bin", f"{tree}.win32.bin", FLAG],
             label=f"unpacking {tree}", success=OK_PRODUCED, produces=tree),
        # their .bat deletes the copy as soon as it is unpacked. -c writes a new
        # one, and 1.6GB is 1.6GB
        Step(tool=None, action=DISCARD, args=[f"{tree}.win32.bin"],
             label=f"the copy of {tree} released"),
        Step(tool=None, action=SEED, args=[tree, *seeded], label="caching the original models",
             source=str(source or "")),
        *work,
        Step(tool="pack", args=["-c", FLAG, f"filelist{variant}.win32.bin", tree],
             label=f"repacking {tree}", success=OK_PRODUCED, produces=f"{tree}.win32.bin",
             needs_change=tree),
        Step(tool=None, action=SEAL, args=[f"filelist{variant}.win32.bin"],
             label=f"sealing filelist{variant}"),
    ]


def needs_4gb(install_dir: Path) -> str:
    """why the install should not start without the 4GB Patch, or "" """
    from patcher.patch_ops import is_laa_enabled

    exe = Path(install_dir) / XIII2.data / "prog" / "win" / "bin" / "ffxiii2img.exe"
    try:
        with exe.open("rb") as handle:
            head = handle.read(64 * 1024)
        if is_laa_enabled(head):
            return ""
    except (OSError, ValueError, IndexError):
        # nothing to read means nothing to say. The launch will have plenty
        return ""
    # the window puts it on before getting here, so this is that failing: the
    # lines above it say why, and the game holding the exe is the usual reason
    return ("the 4GB Patch isn't on ffxiii2img.exe, and XIII-2 runs out of memory with HD "
            "textures without it, so nothing has been changed. If the game is running, close "
            "it and install again.")


def record_built(backup_dir: Path, built: dict) -> None:
    """
    the archive paths this run wrote, per category, in the backup's record.

    XIII's lists name every id a category owns. XIII-2's texture folders do
    not, and which models the injector changes depends on what their .trb
    asks for, so the only reliable list of what a category touched is what it
    touched. Revert takes the record with it.
    """
    import json

    from patcher.fileops import write_atomic
    from patcher.mods.hd_install import MANIFEST

    data = _manifest(backup_dir)
    record = dict(data.get("categories") or {})
    record.update({k: sorted(v) for k, v in built.items()})
    data["categories"] = record
    write_atomic(Path(backup_dir) / MANIFEST, json.dumps(data, indent=2).encode("utf-8"),
                 durable=True)


def _built(categories: list, selected, before: Path, after: Path) -> dict:
    """what changed between two indexes, filed under the ticked categories that own it"""
    from patcher.mods.hd_state import changed_paths

    changed = changed_paths(after, before)
    out = {}
    for category in categories:
        if category["id"] not in selected or not category.get("groups"):
            continue
        prefixes = tuple(f"chr/{g}/" for g in category["groups"])
        out[category["id"]] = {p for p in changed if p.startswith(prefixes)}
    return out


def precheck(install_dir: Path, found: dict, categories: list, selected,
             variant: str, restoring: Path | None = None) -> tuple:
    """
    the refusals that do not wait on the archives, as resolve() does plus the
    4GB flag and the zone blobs. Returns resolve()'s answer.

    A rebuild asks these before it puts the backup back, so a refusal is never
    the second half of a click that has already taken the user's install off.
    `restoring` is that backup, and a blob it holds is one the restore brings back.
    """
    groups, lists, zones = resolve(found, categories, set(selected))
    too_small = needs_4gb(install_dir)
    if too_small:
        raise InstallError(too_small)
    names = {z: f"white_{z}{variant}_img.win32.bin" for z in zones}
    missing = [z for z in zones if not (XIII2.zone_dir(install_dir) / names[z]).is_file()
               and not (restoring is not None and (Path(restoring) / names[z]).is_file())]
    if missing:
        raise InstallError(f"white_{missing[0]}{variant}_img.win32.bin is not in the game, so its "
                           "zone cannot be installed. Verifying the game files through Steam "
                           "puts it back. Nothing has been changed.")
    return groups, lists, zones


def install(install_dir: Path, found: dict, categories: list, selected, store: Path,
            on_line=None, on_step=None, cancel=None, skip_space_check=False,
            on_share=None, landed: list | None = None, variant: str | None = None) -> Result:
    """
    the whole thing: backup first, then the characters all the way into the
    game, then each zone whole or not at all. `landed` is the caller's list and
    says what reached the game even when this raises.
    """
    with exclusive(store):
        return _install(install_dir, found, categories, selected, store, on_line=on_line,
                        on_step=on_step, cancel=cancel, skip_space_check=skip_space_check,
                        on_share=on_share, landed=landed, variant=variant)


def _install(install_dir: Path, found: dict, categories: list, selected, store: Path,
             on_line=None, on_step=None, cancel=None, skip_space_check=False,
             on_share=None, landed: list | None = None, variant: str | None = None) -> Result:
    from patcher.archive.filelist import load
    from patcher.fileops import copy_atomic
    from patcher.mods.hd2_archive import SealError, open_index

    install_dir, mod_root = Path(install_dir), Path(found["root"])
    support = Path(found["support"])
    selected = set(selected)

    def say(text, stream="out"):
        if on_line:
            on_line(text, stream)

    prep = prepare(install_dir, mod_root, store, variant)
    result = Result()
    if landed is not None:
        result.landed = landed

    groups, lists, zones = precheck(install_dir, found, categories, selected, prep.variant)

    try:
        entries = list(load(prep.archives[0]).entries())
    except Exception as e:
        raise InstallError(f"{prep.archives[0].name} could not be read ({e}). Verifying the "
                           "game files through Steam puts it right. Nothing has been changed.")

    models = "models2"
    if lists:
        from patcher.mods.hd2_models_fix import fixed_copy
        if fixed_copy(support, TOOLS["models2"], TOOLS["models2fixed"]) is not None:
            models = "models2fixed"
    steps = chr_steps(entries, found, groups, lists, prep.variant, source=prep.backup_dir,
                      models=models)
    if not steps and not zones:
        raise InstallError(f"nothing ticked has anything in this copy of {NAME} to install, "
                           "so nothing has been changed.")
    dropped = dropped_in(install_dir, steps)
    leave = unbuildable(steps, prep, mod_root) | set(zones_unbuildable(prep, zones)) | dropped
    if leave:
        planned = steps
        steps = chr_steps(entries, found, groups, lists, prep.variant, source=prep.backup_dir,
                          models=models, leave=leave)
        result.missed += left_out(planned, steps, dropped)
        result.gaps += [f"zone {z}" for z in zones if z in leave]
        zones = [z for z in zones if z not in leave]
        if not steps and not zones:
            raise nothing_clean(leave, install_dir)
        say(left_as_they_are(leave, install_dir), "note")
    seeded = next((s.args[1:] for s in steps if s.action == SEED), ())
    measure(prep, mod_root, groups=seeded, on_line=on_line, unpacks=bool(steps))
    if steps:
        prep.swap_bytes = sum(p.stat().st_size for p in prep.archives) + SWAP_SLACK

    blobs = [XIII2.zone_dir(install_dir) / f"white_{z}{prep.variant}_img.win32.bin" for z in zones]
    if zones:
        # white_img is already in the backup as the character pair, so only the
        # blobs are extra
        prep.extra = blobs
        prep.backup_bytes += sum(b.stat().st_size for b in blobs)
        held = in_use(blobs)
        if held:
            raise InstallError(
                f"{', '.join(name for name, _ in held)} cannot be opened for writing, which "
                "usually means the game is running or Steam is verifying it. Close both and "
                "start this again. Nothing has been changed.")
        # a cached copy and a working copy of every blob, and the working copy
        # grows by the art going into it. -i stores what it imports uncompressed,
        # so the art's size on disk is a fair bound on that growth
        art = _zone_art(found, zones)
        prep.needed_bytes += sum(b.stat().st_size for b in blobs) * 2 + sum(art.values())
        prep.swap_bytes = max(prep.swap_bytes,
                              max(b.stat().st_size + art.get(z, 0) for b, z in zip(blobs, zones))
                              + SWAP_SLACK)
        too_long = zone_paths(prep, mod_root, zones)
        if too_long:
            raise InstallError(too_long)

    if not prep.enough_room and not skip_space_check:
        raise InstallError(
            f"there is not enough room: {prep.shortfall()}. Free some up, or move the mod "
            "folder to a drive with more space, and start it again. Nothing has been changed.")

    voices = {"u": "English", "c": "Japanese"}.get(prep.variant, prep.variant)
    say(f"[prep] {len(steps)} steps for the characters, {len(zones)} areas, into the "
        f"{voices} voice set")
    # which builds of the tools this run trusts, and that the models go
    # through our fixed copy of theirs: the run log's business, not the pane's
    for note in tool_notes(support):
        say(f"[prep] {note}", "log")
    if models == "models2fixed":
        say(f"[prep] models run through {TOOLS['models2fixed']}, theirs with the empty swap "
            "table bug fixed. Their own exe is left as it is", "log")
    known = outside(prep, categories,
                    lambda: applied(install_dir, found, categories, prep.variant))
    doubt = known or suspect_backup(mod_root, prep)
    if doubt:
        say(f"[backup] {doubt}", "note" if known else "err")

    clear_leftovers(mod_root, prep.variant, on_line=on_line, layout=XIII2)
    back_up(prep, on_line=on_line, cancel=cancel)
    mark_cache(mod_root)

    total = len(steps)
    if steps:
        index, archive = prep.archives
        say(f"[copy] {archive.name} into the mod folder, with its index decrypted")
        try:
            open_index(index, mod_root / index.name)
        except SealError as e:
            raise InstallError(f"{index.name} could not be decrypted ({e}). Nothing has been "
                               "changed.") from e
        try:
            copy_atomic(archive, mod_root / archive.name, durable=False, cancel=cancel)
        except Exception as e:
            from patcher.fileops import CopyCancelled
            from patcher.mods.hd_runner import Cancelled
            if isinstance(e, CopyCancelled):
                raise Cancelled(str(e)) from None
            raise

        run(steps, mod_root, support, on_line=on_line, on_step=on_step, cancel=cancel,
            on_share=on_share, total=total, into=result)
        if not result.ok:
            return result
        _copy_back(prep, mod_root, result, on_line=on_line, on_share=on_share, cancel=cancel)
        try:
            record_built(prep.backup_dir, _built(categories, selected,
                                                 prep.backup_dir / prep.archives[0].name,
                                                 prep.archives[0]))
        except Exception:
            # the record decorates the panel. The archives are already in
            pass

    if zones:
        groundwork = hd2_zones.prepare_steps(prep.backup_dir, prep.variant, zones)
        run(groundwork, mod_root, support, on_line=on_line, on_step=on_step, cancel=cancel,
            on_share=on_share, start=total, total=total + len(groundwork), into=result)
        if not result.ok:
            return result
        total += len(groundwork)
        blocks = hd2_zones.plan_for(mod_root, install_dir, prep.variant, zones,
                                    undo=Path(store) / "hdproject-commit", backup=prep.backup_dir)
        if not blocks:
            from patcher.mods.hd_install import so_far
            raise InstallError("the zone archives unpacked and there were no backgrounds in "
                               "any of them, so there is nothing to install. "
                               + so_far(result.landed, REVERT))
        say(f"[zones] {len(blocks)} steps across {len(zones)} zones")
        run(blocks, mod_root, support, on_line=on_line, on_step=on_step, cancel=cancel,
            on_share=on_share, start=total, total=total + len(blocks), into=result)
        if not result.ok:
            return result

    clear_leftovers(mod_root, prep.variant, on_line=on_line, layout=XIII2)
    clear_caches(mod_root, on_line=on_line, layout=XIII2)
    # ours, beside their exe, and made again in a moment by the next run
    discard(support / TOOLS["models2fixed"])
    return result


def _zone_art(found: dict, zones) -> dict:
    """bytes of HD art the mod ships per zone"""
    textures = Path(found["textures"]) if found.get("textures") else None
    out = {}
    for zone in zones:
        folder = child(textures, zone) if textures is not None else None
        out[zone] = sum(p.stat().st_size for p in folder.iterdir() if p.is_file()) \
            if folder is not None else 0
    return out


def zone_paths(prep, mod_root: Path, zones) -> str:
    """
    MAX_PATH for the zone extractions, which land two folders deeper than the
    main archive's: zone\\white_<z><v>_img_orig\\<entry>
    """
    from patcher.archive.filelist import load, parse
    from patcher.mods.hd_preflight import MAX_PATH, BAD_CHARS, Survey, explain
    from patcher.mods.hd_zones import read_entry

    try:
        index, image = prep.archives
        entries = list(load(index).entries())
        base = len(str(Path(mod_root))) + 1
        found = Survey()
        for zone in zones:
            raw = read_entry(image, entries, f"zone/filelist_{zone}{prep.variant}.win32.bin")
            prefix = len(f"zone\\white_{zone}{prep.variant}_img_orig\\")
            for entry in parse(raw).entries():
                found.entries += 1
                full = base + prefix + len(entry.path)
                if full > found.longest:
                    found.longest, found.longest_path = full, entry.path
                if full > MAX_PATH:
                    found.too_long.append((entry.path, full))
                if BAD_CHARS.search(entry.path):
                    found.illegal.append(entry.path)
    except Exception:
        return ""
    return explain(found, mod_root)


def cache_doubt(mod_root: Path, variant: str) -> str:
    """
    why models\\orig cannot be trusted for a rebuild, or "". The three sizes
    their Setup checks, against the cache the tools read the originals from.
    """
    from patcher.mods.hd_preflight import VANILLA2_CHR

    cache = Path(mod_root) / "models" / "orig"
    for path, size in sorted(VANILLA2_CHR.items()):
        cached = cache / path
        if cached.is_file() and cached.stat().st_size != size:
            return (f"{cached.name} in the originals cache is {cached.stat().st_size:,} bytes "
                    f"and a vanilla copy is {size:,}, so that cache was filled from a game "
                    "that had already been modded. Rebuilding from it would write HD art over "
                    "HD art. Delete models\\orig in the mod folder and run this again, or put "
                    f"the game back with {REVERT} and install from vanilla.")
    return ""


def zones_installed(install_dir: Path, variant: str, backup_dir: Path, zones) -> bool:
    """most of the zone blobs aren't what Steam ships, or the backup's copies without that"""
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_state import blob_moved

    zone_dir, changed, seen = XIII2.zone_dir(install_dir), 0, 0
    for zone in zones:
        name = f"white_{zone}{variant}_img.win32.bin"
        moved = blob_moved(zone_dir / name, Path(backup_dir) / name,
                           hd_vanilla.zone_size(XIII2, name))
        if moved is None:
            continue
        seen += 1
        changed += moved
    return changed * 2 > seen if seen else False


def applied(install_dir: Path, found: dict, categories: list, variant: str | None = None):
    """
    which categories are in the game, in the voice set the launcher is on unless
    `variant` names another. A character category counts when most of what the
    last run wrote for it still differs from vanilla; the zones count the way
    XIII's do, blob against Steam's size or the backup.

    A category no run of ours wrote has no list of what it touches, since the
    injector goes by what each .trb asks for. Its groups count then, at a
    quarter of their models grown past vanilla: the real Weapons pass moved
    half its group, and no costume mod comes near a quarter of one.
    """
    from patcher.mods import hd_vanilla
    from patcher.mods.hd_install import archive_pair, find_variant
    from patcher.mods.hd_state import Applied, changed_paths, models_moved
    from patcher.paths import undo_store_for

    variant = variant or find_variant(install_dir)
    index = archive_pair(install_dir, variant)[0]
    backup = undo_store_for(install_dir) / "hdproject"
    vanilla = backup / index.name
    changed = models_moved(index, vanilla)
    if changed is None:
        return Applied()
    if vanilla.is_file():
        # what the model tool writes beside a model, which the shipped sizes don't cover
        try:
            changed |= {p for p in changed_paths(index, vanilla)
                        if hd_vanilla.parse_path(p) is None}
        except Exception:
            pass

    wrote = _manifest(backup).get("categories") or {}
    models = None
    state = Applied(changed=len(changed), verified=True)
    for category in categories:
        if category.get("zones"):
            if zones_installed(install_dir, variant, backup, found.get("zones") or []):
                state.categories.add(category["id"])
            continue
        mine = {p.lower() for p in wrote.get(category["id"]) or []}
        if mine:
            if len(mine & changed) * 2 > len(mine):
                state.categories.add(category["id"])
            continue
        if models is None:
            models = hd_vanilla.index_sizes(index)
        prefixes = tuple(f"chr/{g}/" for g in category.get("groups") or [])
        group = {p for p in models if prefixes and p.startswith(prefixes)}
        if group and len(group & changed) * 4 >= len(group):
            state.categories.add(category["id"])
    return state


def sizes(install_dir: Path, found: dict, categories: list) -> dict:
    """
    what each category costs, by id: the character models it rewrites, or
    the zone blobs. Absent rather than zero when there is nothing to count.
    """
    from patcher.archive.filelist import load
    from patcher.mods.hd_install import archive_pair, find_variant

    variant = find_variant(install_dir)
    # normalised once, since every category below filters the same fourteen thousand
    models = []
    for e in load(archive_pair(install_dir, variant)[0]).entries():
        path = e.path.replace("\\", "/").lower()
        if path.startswith("chr/") and path.endswith(MODEL_FILES):
            models.append((path, e.uncompressed))
    out = {}
    for category in categories:
        total = 0
        for group in category.get("groups") or []:
            prefix = f"chr/{group}/"
            total += sum(size for path, size in models if path.startswith(prefix))
        if category.get("zones"):
            for zone in found.get("zones") or []:
                blob = XIII2.zone_dir(install_dir) / f"white_{zone}{variant}_img.win32.bin"
                if blob.is_file():
                    total += blob.stat().st_size
        if total:
            out[category["id"]] = total
    return out
