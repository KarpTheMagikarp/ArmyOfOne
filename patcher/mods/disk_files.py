"""
A mod's files that the game reads loose off the disk rather than out of its
archives: its movies, LR's DLC folder, a zone's blob beside its index. The
archives have game_files and the exe's folder has loader_mods; this is the
rest of the game's own tree.

Each goes in over the game's file with the original moved into the undo
store, on the same drive so it's a rename rather than a copy of a 2GB movie,
and the record says the digest of what went in. Remove puts the original back
while the file is still what went in, and one changed since, a Steam verify
say, is left be with the original kept and said. A file the game doesn't have
goes in too when its folder is one of the game's, and Remove takes it away.

Two mods can't both be one file, the rule loader_mods has: the second is
turned away naming the first, since taking the first off from under it would
put the game's file back under the second's record.

Every step lands on the record before the file moves, so a crash part way
leaves a record still marked pending that settle() finishes undoing.
"""

import functools
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from patcher.access import is_threat_block, threat_message, write_failure

RECORDS = "disk"
KEPT = "disk-originals"
NEW = ".patcher-new"


class DiskError(Exception):
    pass


# a list drawn on its own thread settles what a crash cut short, and an install
# going on beside it is a pending record too
_lock = threading.RLock()


def _one_at_a_time(fn):
    @functools.wraps(fn)
    def held(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return held


def _records(store) -> Path:
    return Path(store) / RECORDS


def _key(rel: str) -> str:
    return rel.replace("\\", "/").lower().strip("/")


def _digest(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()
    except FileNotFoundError:
        return None


def _stamp(path: Path):
    try:
        seen = Path(path).stat()
    except OSError:
        return None
    return [seen.st_size, seen.st_mtime_ns]


def records(store) -> list:
    """every mod with files on disk, oldest first"""
    folder = _records(store)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
            continue
        for part in ("kept", "stamps"):
            if not isinstance(record.get(part), dict):
                record[part] = {}
        if not isinstance(record.get("added"), list):
            record["added"] = []
        record["name"] = str(record.get("name") or "mod")
        out.append(dict(record, _path=str(path)))
    return out


def _write(record: dict) -> None:
    from patcher.fileops import write_atomic

    path = Path(record["_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in record.items() if not k.startswith("_")}
    write_atomic(path, json.dumps(body, indent=1).encode("utf-8"), durable=True)


def claimed(store) -> dict:
    """every file on disk a mod here holds, lowercased, -> that mod's name"""
    return {_key(rel): record["name"] for record in records(store)
            for rel in set(record["files"]) | set(record["kept"]) | set(record["added"])}


def _slug(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    return keep[:60] or "mod"


def _ours(store, name: str):
    return next((r for r in records(store) if r["name"].lower() == name.lower()), None)


def _going(store, name: str, keys: set, others=()) -> tuple:
    """
    (the record a drop of loose files alone is a new version of or None, the
    name it goes in under), loader_mods' way: one by the name is this mod's
    when they share a file, and one that only shares the name, or a mod of
    another kind by it (`others`, lowercased), is another mod, so this one
    is "Name (2)"
    """
    kin = re.compile(re.escape(name) + r"( \(\d+\))?", re.I)
    named = [r for r in records(store) if kin.fullmatch(r["name"])]
    for record in sorted(named, key=lambda r: r.get("at", 0), reverse=True):
        if keys & {_key(rel) for rel in set(record["files"]) | set(record["kept"])
                   | set(record["added"])}:
            return record, record["name"]
    taken = {r["name"].lower() for r in records(store)} | set(others)
    if name.lower() not in taken:
        return None, name
    n = 2
    while f"{name} ({n})".lower() in taken:
        n += 1
    return None, f"{name} ({n})"


def _copy(member: str, side: Path, read, opened) -> str:
    """
    a member into its side copy, synced, and the digest of what went: through
    `opened` a megabyte at a time when there is one, since a zone's archive
    is a gigabyte or two and read() would hold all of it at once
    """
    digest = hashlib.sha256()
    with side.open("wb") as out:
        if opened is None:
            data = read(member)
            digest.update(data)
            out.write(data)
        else:
            with opened(member) as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
                    out.write(block)
        out.flush()
        os.fsync(out.fileno())
    return digest.hexdigest()


@_one_at_a_time
def install(install_dir, store, name: str, files: dict, read, sizes: dict | None = None,
            origin: str = "", progress=None, taken: dict | None = None, opened=None,
            check=None, others=None) -> dict:
    """
    `files` is drop member -> its path under the game's folder, `read(member)`
    its bytes, or `opened(member)` a file of them to stream, and `sizes` how
    big each is, for `progress(done, of)` in bytes. `taken` is lowercased
    path -> whose, for files another kind of mod holds, a loader's data
    folder say. `check(rel, copy)` is why a file copied beside its place
    mustn't go in, or "", asked before anything of the game's moves. With
    `others`, the names other kinds of mod have, it's these files alone and
    picks its own name; without, the name is the rest of the mod's. A new
    version of a mod already in comes off whole first, so what's under it is
    the game's own and its record keeps the right originals
    """
    install_dir, store = Path(install_dir), Path(store)
    settle(install_dir, store)
    if not files:
        raise DiskError(f"there's nothing in {name} to put in the game's folder.")
    wanted = {}
    for member, rel in sorted(files.items(), key=lambda kv: kv[1].lower()):
        rel = rel.replace("\\", "/").strip("/")
        if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
            raise DiskError(f"{member} has a path that doesn't stay inside the game's folder.")
        if _key(rel) in wanted:
            if sizes and sizes.get(member) != sizes.get(wanted[_key(rel)][0]) \
                    or read(member) != read(wanted[_key(rel)][0]):
                raise DiskError(f"{name} has two different files for {rel}, with nothing to say "
                                "which goes in. Unpack it and drop the folder you want.")
            continue
        wanted[_key(rel)] = (member, rel)
    old, name = _going(store, name, set(wanted), others) if others is not None \
        else (_ours(store, name), name)
    holders = {k: who for k, who in {**(taken or {}), **claimed(store)}.items()
               if old is None or who.lower() != name.lower()}
    for key, (_, rel) in wanted.items():
        if key in holders:
            raise DiskError(f"{rel} is {holders[key]}'s. Two mods can't both be that file, so "
                            f"{name} wasn't installed. Remove {holders[key]} first if you'd "
                            "rather have this one.")
        target = install_dir / rel
        if target.exists() and not target.is_file():
            raise DiskError(f"there's a folder called {rel} in the game where {name} puts a "
                            "file, so it wasn't installed.")
    if old is not None:
        left = remove(install_dir, store, old["name"]).get("left")
        if left:
            raise DiskError(f"{name}'s older version couldn't all come off ({', '.join(left)} "
                            "changed since it went in), so the new one wasn't installed.")

    stamp = f"{int(time.time() * 1000)}-{_slug(name)}"
    kept_dir = store / KEPT / stamp
    # in with the mod's archive files or loader, it's that mod's row and
    # comes off with it; alone, it's a row of its own
    record = {"name": name, "origin": origin, "at": time.time(), "pending": True,
              "with": others is None, "files": {}, "kept": {}, "added": [], "stamps": {},
              "keep_dir": f"{KEPT}/{stamp}", "_path": str(_records(store) / f"{stamp}.json")}
    sizes = sizes or {}
    total = sum(int(sizes.get(m) or 0) for m, _ in wanted.values()) or len(wanted)
    done = 0
    try:
        _write(record)
        for member, rel in wanted.values():
            target = install_dir / rel
            side = target.with_name(target.name + NEW)
            # on the record before anything of it is on the disk, its digest
            # once its copy is
            record["files"][rel] = None
            if target.is_file():
                record["kept"][rel] = _digest(target)
            else:
                record["added"].append(rel)
            _write(record)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = _copy(member, side, read, opened)
            # antivirus takes a file as it's closed, so the read back is where
            # that shows up, before anything of the game's has moved
            if _digest(side) != digest:
                raise DiskError(threat_message(target.name, name))
            problem = check(rel, side) if check is not None else ""
            if problem:
                raise DiskError(f"{name} wasn't installed: {problem}.")
            record["files"][rel] = digest
            _write(record)
            if rel in record["kept"]:
                keep = kept_dir / rel
                keep.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, keep)
            os.replace(side, target)
            record["stamps"][rel] = _stamp(target)
            done += int(sizes.get(member) or 0) if sizes else 1
            if progress is not None:
                progress(done, total)
        record.pop("pending")
        _write(record)
    except BaseException as e:
        settle(install_dir, store)
        if isinstance(e, DiskError) or not isinstance(e, Exception):
            raise
        if is_threat_block(e):
            raise DiskError(threat_message(Path(str(getattr(e, "filename", "") or name)).name,
                                           name)) from e
        raise DiskError(f"{name} wasn't installed: {write_failure(e, install_dir)}") from e
    return {"ok": True, "name": name, "files": len(record["files"]),
            "replaced": sorted(record["kept"]), "added": sorted(record["added"])}


def settle(install_dir, store, wait: bool = True) -> list:
    """
    finishes undoing any install a crash or an error cut short, by its record:
    the game's originals back where they were, a file it added gone, its side
    copies swept. The names of the ones it settled. Without `wait` it does
    nothing while an install or a Remove is going on
    """
    if not _lock.acquire(blocking=wait):
        return []
    try:
        return _settle(Path(install_dir), Path(store))
    finally:
        _lock.release()


def _settle(install_dir: Path, store: Path) -> list:
    out = []
    for record in records(store):
        if not record.get("pending"):
            continue
        kept_dir = store / str(record.get("keep_dir") or "")
        for rel in set(record["files"]) | set(record["kept"]) | set(record["added"]):
            target = install_dir / rel
            target.with_name(target.name + NEW).unlink(missing_ok=True)
            keep = kept_dir / rel
            try:
                if rel in record["kept"] and keep.is_file():
                    os.replace(keep, target)
                elif rel in record["added"] and target.is_file() \
                        and _digest(target) == record["files"].get(rel):
                    target.unlink()
            except OSError:
                # tried again next time, the record still saying what's where
                break
        else:
            Path(record["_path"]).unlink(missing_ok=True)
            _prune(kept_dir)
            out.append(record["name"])
    return out


@_one_at_a_time
def remove(install_dir, store, name: str) -> dict:
    """
    every file it put there back to the game's, or gone when the game had
    none, while it's still what went in. One changed since stays as it is,
    with the game's original kept, and is said
    """
    install_dir, store = Path(install_dir), Path(store)
    settle(install_dir, store)
    record = _ours(store, name)
    if record is None:
        return {"ok": False, "error": f"{name} isn't installed"}
    kept_dir = store / str(record.get("keep_dir") or "")
    left = []
    for rel, digest in sorted(record["files"].items()):
        target = install_dir / rel
        now = _digest(target) if target.is_file() else None
        if now is not None and now != digest:
            left.append(rel)
            continue
        keep = kept_dir / rel
        try:
            if rel in record["kept"] and keep.is_file():
                os.replace(keep, target)
            elif now is not None:
                target.unlink()
        except OSError as e:
            raise DiskError(f"{name} couldn't come off: {write_failure(e, target)}") from e
        record["files"].pop(rel)
        record["kept"].pop(rel, None)
        if rel in record["added"]:
            record["added"].remove(rel)
        _write(record)
    if left:
        return {"ok": True, "removed": name, "left": left}
    Path(record["_path"]).unlink(missing_ok=True)
    _prune(kept_dir)
    return {"ok": True, "removed": name, "left": []}


def _prune(folder: Path) -> None:
    """its folders once they're empty, never a file"""
    try:
        for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        folder.rmdir()
    except OSError:
        pass


def listed(store, install_dir) -> list:
    """
    the drawer's rows, with a line only when a file of one isn't as it went
    in, told by size and time rather than hashing every movie on every draw
    """
    out = []
    for record in records(store):
        # going in now, or one a crash cut short that settle() hasn't got to
        if record.get("pending"):
            continue
        changed = [rel for rel in sorted(record["files"])
                   if _stamp(Path(install_dir) / rel) != record["stamps"].get(rel)]
        warn = f"{changed[0]} has changed since it went in" if changed else ""
        count = len(record["files"])
        out.append({"name": record["name"], "kind": "disk", "files": sorted(record["files"]),
                    "about": f"{count} file{'' if count == 1 else 's'} in the game's folder",
                    "warn": warn, "managed": True, "with": bool(record.get("with"))})
    return out
