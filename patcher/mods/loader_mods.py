"""
Mods that load themselves: a .dll named after one Windows ships, version.dll or
winmm.dll or dinput8.dll, put beside the game's exe so the loader finds it
before the real one, with whatever folders it reads its data out of. HD Fonts
and GUI is one: version.dll, hd_textures and hd_textures_shaders, all into the
folder ffxiii2img.exe is in, per its own readme.

They go in as files on disk, not into the archives or NovaFix's add-ons, and
nothing already there is written over unless this app put it there for the
same mod: two mods can't both be version.dll, and NovaFix is d3d9.dll. The
same mod is the same name with a file in common, since a name alone is any
other Release.zip. The record goes down before anything moves, with the hash
of every file, so a Remove takes off exactly what went in and a file changed
since is left be.

Each file is written beside its target first and read back, then swapped in,
with whatever it replaces moved aside until the whole set is in. A crash leaves
the record saying what to look for, and antivirus quarantining a file as it's
written shows up as the read back failing, before anything was swapped.
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

# Windows DLLs a 32-bit game loads from beside its exe before System32, by name.
# The names Ultimate ASI Loader answers to, which is what most of these mods
# are built on, and the few others that show up as proxies
PROXY_NAMES = {
    "version.dll", "winmm.dll", "dinput8.dll", "dinput.dll", "dsound.dll", "d3d8.dll",
    "d3d9.dll", "d3d10.dll", "d3d11.dll", "d3d12.dll", "ddraw.dll", "dxgi.dll", "winhttp.dll",
    "wininet.dll", "msacm32.dll", "msvfw32.dll", "wintrust.dll", "xinput1_1.dll",
    "xinput1_2.dll", "xinput1_3.dll", "xinput1_4.dll", "xinput9_1_0.dll", "dbghelp.dll",
    "msimg32.dll", "avrt.dll", "binkw32.dll", "vorbisfile.dll", "d3dcompiler_43.dll",
    "d3dcompiler_46.dll", "d3dcompiler_47.dll", "xlive.dll",
}
# what a loader reads its settings out of, beside it
CONFIG = (".ini", ".cfg", ".json", ".toml", ".yaml", ".yml")
# folders a mod ships beside its .dll that are for people, not for it
NOT_DATA = {"screenshots", "screenshot", "images", "preview", "previews", "docs",
            "documentation", "src", "source"}

RECORDS = "loaders"
NEW, OLD = ".patcher-new", ".patcher-old"
# a file written and synced on its own, then read back, weighs about a
# megabyte of its bytes on the drawer's line: the sync is most of a small one
SYNCED = 1 << 20


class LoaderError(Exception):
    pass


# install, remove and rewrite each read a record and write it back, and the
# drawer's list rewrites dlls on its own thread while a drop is going in
_lock = threading.RLock()


def _one_at_a_time(fn):
    @functools.wraps(fn)
    def held(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return held


def _records(store) -> Path:
    return Path(store) / RECORDS


def records(store) -> list:
    """
    every loader mod on record, oldest first. A part a hand edit has made
    into something else reads as missing, so one broken record can't stop
    the rest coming off, and it can still be taken off itself by the name
    the drawer shows
    """
    folder = _records(store)
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
            continue
        for part in ("was", "kept", "leftover"):
            if not isinstance(record.get(part), dict):
                record.pop(part, None)
        dirs = record.get("dirs")
        record["dirs"] = [d for d in dirs if isinstance(d, str)] if isinstance(dirs, list) else []
        record["name"] = str(record.get("name") or "mod")
        out.append(dict(record, _path=str(path)))
    return out


def _write(record: dict) -> None:
    from patcher.fileops import write_atomic

    path = Path(record["_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {k: v for k, v in record.items() if not k.startswith("_")}
    write_atomic(path, json.dumps(body, indent=1).encode("utf-8"), durable=True)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except FileNotFoundError:
        return None


def _key(rel: str) -> str:
    return rel.replace("\\", "/").lower()


def _slug(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name).strip()
    return keep[:60] or "mod"


def _held(record: dict, *parts) -> dict:
    """rel -> every digest the record's `parts` say a file of its may hold"""
    out = {}
    for part in parts:
        for rel, digests in (record.get(part) or {}).items():
            for digest in [digests] if isinstance(digests, str) else \
                    digests if isinstance(digests, list) else ():
                if isinstance(digest, str):
                    out.setdefault(rel, set()).add(digest)
    return out


def claimed(store) -> set:
    """every path beside the exe a drawer mod holds, lowercased"""
    return {_key(rel) for record in records(store) for rel in _held(record, "files", "kept")}


def _ours(store, name: str):
    """this mod's record, by name, any case"""
    return next((r for r in records(store) if str(r.get("name")).lower() == name.lower()), None)


def _going(store, name: str, rels, others=()) -> tuple:
    """
    (the record a drop is a new version of or None, the name it goes in
    under). One by the name is this mod's when they share a file beside the
    exe. One that only shares the name is another mod, which goes in as
    "Name (2)" and is what a later drop sharing its files updates. So does a
    mod of another kind by the name (`others`, lowercased)
    """
    from patcher.addon_sieve import stamped

    keys = {_key(rel) for rel in rels}
    kin = re.compile(re.escape(name) + r"( \(\d+\))?", re.I)
    # one that went in before names lost Nexus's id, version and time
    # (stamped) is still this mod
    named = [r for r in records(store) if kin.fullmatch(str(r.get("name", "")))
             or kin.fullmatch(stamped(str(r.get("name", "")))[0].strip(" .-"))]
    for record in sorted(named, key=lambda r: r.get("at", 0), reverse=True):
        if keys & {_key(rel) for rel in _held(record, "files", "was", "kept")}:
            was = str(record["name"])
            return record, name if stamped(was)[0] != was else was
    if any(str(r.get("name", "")).lower() == name.lower() for r in named) \
            or name.lower() in others:
        taken = {str(r.get("name", "")).lower() for r in records(store)} | set(others)
        n = 2
        while f"{name} ({n})".lower() in taken:
            n += 1
        return None, f"{name} ({n})"
    return None, name


def _read(read, member: str, name: str) -> bytes:
    try:
        return read(member)
    except LoaderError:
        raise
    except Exception as e:
        # the drop's reader says a block itself, with the OSError underneath
        if is_threat_block(e) or is_threat_block(e.__cause__):
            raise LoaderError(threat_message(Path(member).name, name)) from e
        raise LoaderError(f"{Path(member).name} couldn't be read out of {name} ({e}), so "
                          "nothing went in beside the game's exe. The download may be damaged: "
                          "get it again and drop that.") from e


@_one_at_a_time
def install(exe_dir, store, name: str, files: dict, read, taken: dict | None = None,
            origin: str = "", extra: dict | None = None, adopt: bool = False,
            progress=None, others=(), owners: dict | None = None) -> dict:
    """
    `files` is drop member -> its path under the exe's folder, `read(member)`
    its bytes. `taken` is file name -> what this app installed there itself,
    NovaFix's d3d9.dll and the like, which are never a mod's to replace.
    An update replaces its own files and drops what the new version doesn't
    ship, and a config the user has edited since stays theirs, with the new
    one beside it as .new. The name it went in under is in the answer: one
    that only shares a name with a mod already in gets a number. `extra` goes
    on the record as it is, for the drawer's list. `adopt` is for a folder only
    this mod uses, never beside the exe, where two mods can ship one loader or
    share one config: a file already there with the same bytes is its own, and
    settings nothing has a record of are the user's, as edited ones are, most
    often the ones a Remove left because ReShade had saved to them.
    `progress(done, of)` hears how far it's got, out of a hundred
    """
    from patcher.mods.meter import Meter

    meter = Meter(progress)
    exe_dir, store = Path(exe_dir), Path(store)
    if not files:
        raise LoaderError(f"there's nothing in {name} to put beside the game's exe.")
    wanted = {}
    for member, rel in sorted(files.items(), key=lambda kv: (kv[1].lower(), kv[0])):
        rel = rel.replace("\\", "/").strip("/")
        if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
            raise LoaderError(f"{member} has a path that doesn't stay inside the game's folder.")
        wanted.setdefault(_key(rel), []).append((member, rel))
    old, name = _going(store, name, [rel for those in wanted.values() for _, rel in those],
                       others)
    # reading the drop and hashing it is about a third, writing, syncing and
    # reading back each file the rest, and an update's renames and deletes
    # are a part of their own where a first install only renames
    meter.split(read=1, write=2, swap=0.5 if old else 0.15)
    # what each of its files may hold and still be its own: what went in, what
    # an update cut short left, or a config the user kept
    mine = {}
    for rel, digests in _held(old or {}, "files", "was", "kept").items():
        mine.setdefault(_key(rel), set()).update(digests)
    users = set(((old or {}).get("kept") or {}).values())
    # `owners` is the same for files another kind of mod holds, loose files say
    owner = {_key(rel): who for rel, who in (owners or {}).items()}
    for record in records(store):
        if old is None or record["_path"] != old["_path"]:
            for rel in record["files"]:
                owner[_key(rel)] = record.get("name", "another mod")

    plan, kept, sums = {}, {}, {}
    for n, (key, those) in enumerate(wanted.items(), start=1):
        member, rel = those[0]
        target = exe_dir / rel
        leaf = rel.rsplit("/", 1)[-1]
        # where it is, which for one in a folder of its own isn't just its name
        shown = rel if "/" in rel else leaf
        if key in (taken or {}):
            raise LoaderError(f"{shown} beside the game's exe is {taken[key]}, which this app put "
                              f"there. {name} ships its own {leaf}, which would replace it, so it "
                              "wasn't installed.")
        if key in owner:
            raise LoaderError(f"{shown} beside the game's exe is {owner[key]}'s. Two mods can't "
                              f"both be {leaf}, so {name} wasn't installed. Remove "
                              f"{owner[key]} first if you'd rather have this one.")
        if target.exists() and not target.is_file():
            raise LoaderError(f"there's a folder called {shown} beside the game's exe where "
                              f"{name} puts a file, so it wasn't installed.")
        data = _read(read, member, name)
        digest = hashlib.sha256(data).hexdigest()
        for other, _ in those[1:]:
            # two builds of one file, an x86 and an x64 folder or two
            # options, with nothing to say which. The same bytes twice is one
            if _read(read, other, name) != data:
                raise LoaderError(f"{name} has two different {leaf} files for beside the game's "
                                  f"exe, {member} and {other}, with nothing to say which one goes "
                                  "in. Unpack it and drop the folder you want.")
        now = _digest(target) if target.is_file() else None
        ours = now is not None and (now in mine.get(key, set()) - users or (
            adopt and now == digest))
        if now is not None and not ours:
            # settings on no record are only taken as the user's in a folder
            # the mod has to itself: beside the exe they can be another mod's
            if not leaf.lower().endswith(CONFIG) or (key not in mine and not adopt):
                if key not in mine:
                    raise LoaderError(f"there's already a {shown} beside the game's exe that isn't "
                                      f"from this app. {name} would replace it, so it wasn't "
                                      "installed. If it's an older copy of the same mod, take it "
                                      "out first.")
                raise LoaderError(f"{shown} beside the game's exe has changed since {name} put it "
                                  "there, so it wasn't replaced. Take it out or put it back, then "
                                  "drop the mod again.")
            # edited since, or on no record at all, and settings are the user's
            kept[rel] = now
            rel, key = rel + ".new", key + ".new"
            target = exe_dir / rel
            # the .new an update before this left can have been edited too,
            # the settings merged into it, and that's the user's as well
            there = _digest(target) if target.is_file() else None
            if there is not None and there not in mine.get(key, ()):
                raise LoaderError(f"{target.name} beside the game's exe has changed since {name} "
                                  "put it there, so it wasn't replaced. Take it out or put it "
                                  "back, then drop the mod again.")
        plan[key], sums[key] = (rel, target, data), digest
        meter.at("read", n, len(wanted))

    keep = {_key(rel) for rel in kept}
    # what the old version had beside the exe that this one doesn't ship,
    # still as it left it, and what the one before that had when an update
    # of it was cut short
    # of it cut short. Never a config the user edited, which a cut short
    # update had already kept as theirs
    dropping = {}
    for rel in _held(old or {}, "files", "was"):
        key = _key(rel)
        if key not in plan and key not in keep \
                and _digest(exe_dir / rel) in mine.get(key, set()) - users:
            dropping.setdefault(key, (rel, exe_dir / rel))

    made = set()
    for _, target, _ in plan.values():
        for parent in target.relative_to(exe_dir).parents[:-1]:
            if not (exe_dir / parent).exists():
                made.add(parent.as_posix())
    record = {**(extra or {}), "name": name, "origin": origin, "at": time.time(), "pending": True,
              "files": {rel: sums[key] for key, (rel, _, _) in plan.items()},
              "was": {rel: sorted(digests)
                      for rel, digests in _held(old or {}, "files", "was").items()},
              "kept": dict(kept), "leftover": dict((old or {}).get("leftover") or {}),
              "dirs": sorted(set((old or {}).get("dirs") or []) | made),
              "_path": (old or {}).get("_path")
              or str(_records(store) / f"{int(time.time() * 1000)}-{_slug(name)}.json")}
    try:
        _write(record)
    except OSError as e:
        raise LoaderError(f"{name} wasn't installed: {write_failure(e, store)}") from e

    staged, swapped, moved = [], [], []
    total, written = sum(len(data) + SYNCED for _, _, data in plan.values()), 0
    # every file into place, and every old copy aside and then deleted
    ops = len(plan) + 2 * (len(dropping) + sum(t.exists() for _, t, _ in plan.values()))
    try:
        for key, (rel, target, data) in plan.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            side = target.with_name(target.name + NEW)
            # on the list before it's opened, so a disk that fills part way
            # through it is a file the undo takes back off
            staged.append(side)
            with side.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # antivirus takes a flagged file as it's closed, so the read back
            # is where that shows up, before anything has been swapped
            if _digest(side) != sums[key]:
                raise LoaderError(threat_message(target.name, name))
            written += len(data) + SYNCED
            meter.at("write", written, total)
        for rel, target, _ in plan.values():
            side = target.with_name(target.name + NEW)
            if target.exists():
                aside = target.with_name(target.name + OLD)
                os.replace(target, aside)
                moved.append((aside, target))
            os.replace(side, target)
            swapped.append(target)
            meter.at("swap", len(swapped) + len(moved), ops)
        for rel, target in dropping.values():
            aside = target.with_name(target.name + OLD)
            os.replace(target, aside)
            moved.append((aside, target))
            meter.at("swap", len(swapped) + len(moved), ops)
    except BaseException as e:
        left = _undo(staged, swapped, moved, made, exe_dir)
        try:
            if old is None:
                Path(record["_path"]).unlink(missing_ok=True)
            else:
                _write(old)
        except OSError:
            pass
        if isinstance(e, LoaderError) or not isinstance(e, Exception):
            raise
        leaf = Path(str(getattr(e, "filename", "") or name)).name
        if is_threat_block(e) or (isinstance(e, FileNotFoundError) and leaf.endswith(NEW)):
            # one that read back and then went before it could be swapped in
            # is antivirus taking it a beat later
            said = threat_message(leaf.removesuffix(NEW), name)
        else:
            said = write_failure(e, exe_dir)
        raise LoaderError(f"{name} wasn't installed: {said}"
                          + (f" Not everything could be put back: {left}." if left else "")) from e

    # the old copies this moved aside, and what an update of it cut short left
    # either side of its swaps, while each is still one of its own versions
    known = {}
    for rel, digests in _held(old or {}, "files", "was").items():
        known.setdefault(_key(rel), set()).update(digests)
    for rel, digest in record["files"].items():
        known.setdefault(_key(rel), set()).add(digest)
    left = {}
    for n, (aside, target) in enumerate(moved, start=1):
        # its own by being moved there just now, so it goes unread
        try:
            aside.unlink()
        except OSError:
            rel = target.relative_to(exe_dir).as_posix()
            left[rel + OLD] = sorted(known.get(_key(rel), ()))
        meter.at("swap", len(swapped) + len(moved) + n, ops)
    sides = {}
    for rel in set(_held(old or {}, "files", "was")) | set(record["files"]):
        for suffix in (NEW, OLD):
            if rel + suffix not in left:
                sides.setdefault(_key(rel + suffix), (rel + suffix, set(known[_key(rel)])))
    for side, digests in _held(record, "leftover").items():
        sides.setdefault(_key(side), (side, set()))[1].update(digests)
    record["leftover"] = {**left, **_tidy(exe_dir, sides.values())}

    # the old version's folders that hold none of this one's files now
    _drop_empty(exe_dir, [d for d in (old or {}).get("dirs") or []
                          if not any(_key(rel).startswith(_key(d) + "/")
                                     for rel in record["files"])])
    record["dirs"] = [d for d in record["dirs"] if (exe_dir / d).is_dir()]
    record["kept"] = {**{rel: digest for rel, digest in ((old or {}).get("kept") or {}).items()
                         if _key(rel) not in plan}, **kept}
    for part in ("pending", "was") + (() if record["leftover"] else ("leftover",)):
        record.pop(part, None)
    try:
        _write(record)
    except OSError:
        pass
    folders = sorted({rel.split("/", 1)[0] for rel in record["files"] if "/" in rel},
                     key=str.lower)
    meter.end()
    return {"ok": True, "name": name, "files": len(record["files"]), "folders": folders,
            "dlls": sorted(rel for rel in record["files"]
                           if "/" not in rel and rel.lower().endswith(".dll")),
            "updated": old is not None, "kept": sorted(kept)}


def rewrite(exe_dir, store, change) -> list:
    """
    every mod's .dll or .asi beside the exe that's still a version of what went
    in, swapped for what `change(bytes, its digests on record)` makes of it,
    None for nothing: the dlls src_38 renamed go back to their authors' bytes
    the next time the list is drawn. The names of the mods it changed. Every
    version stays the mod's on its record, so a swap that fails, the game
    holding the file, leaves it as it was until the next time. It never waits
    on a drop or a Remove going on, and the list after that one gets to it
    """
    if not _lock.acquire(blocking=False):
        return []
    try:
        return _rewrite(Path(exe_dir), store, change)
    finally:
        _lock.release()


def _rewrite(exe_dir: Path, store, change) -> list:
    out = []
    for record in records(store):
        if record.get("part") or record.get("pending"):
            continue
        held = _held(record, "files", "was")
        changed = False
        for rel in list(record["files"]):
            if "/" in rel or not rel.lower().endswith((".dll", ".asi")):
                continue
            target = exe_dir / rel
            try:
                data = target.read_bytes()
            except OSError:
                continue
            if hashlib.sha256(data).hexdigest() not in held.get(rel, ()):
                continue
            made = change(data, set(held[rel]))
            if not made or made == data:
                continue
            digest = hashlib.sha256(made).hexdigest()
            record.setdefault("was", {})[rel] = sorted(held[rel] | {digest})
            record["files"][rel] = digest
            side = target.with_name(target.name + NEW)
            try:
                _write(record)
                with side.open("wb") as handle:
                    handle.write(made)
                    handle.flush()
                    os.fsync(handle.fileno())
                if _digest(side) != digest:
                    raise OSError(f"{side.name} didn't read back")
                os.replace(side, target)
            except OSError:
                try:
                    side.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            changed = True
        if changed:
            out.append(record["name"])
    return out


def _tidy(exe_dir: Path, sides) -> dict:
    """
    each (side file, the digests it may hold) gone while it holds one of
    them. What won't delete yet, or can't even be read while antivirus or the
    game holds it, comes back as side file -> the digests it may hold, so a
    later install or Remove still knows what to look for. Never raises: the
    install is in by now
    """
    left = {}
    for side, digests in sides:
        path = exe_dir / side
        try:
            digest = _digest(path)
        except OSError:
            left[side] = sorted(digests)
            continue
        if digest is None or digest not in digests:
            continue
        try:
            path.unlink()
        except OSError:
            left[side] = [digest]
    return left


def _undo(staged, swapped, moved, made, exe_dir) -> str:
    """every swap backed out, newest first. What couldn't be, as a sentence or "" """
    left = []
    for target in reversed(swapped):
        try:
            target.unlink()
        except OSError:
            pass
    for aside, target in reversed(moved):
        try:
            os.replace(aside, target)
        except OSError as e:
            left.append(f"{target.name} ({e.strerror or e})")
    for side in staged:
        try:
            side.unlink(missing_ok=True)
        except OSError:
            pass
    _drop_empty(exe_dir, made)
    return ", ".join(left)


def _drop_empty(exe_dir: Path, dirs) -> None:
    for rel in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
        try:
            (exe_dir / rel).rmdir()
        except OSError:
            pass


@_one_at_a_time
def remove(exe_dir, store, name: str) -> dict:
    """
    every file it put there, while it's still what went in, and the folders it
    made once they're empty. One changed since, a config the user kept among
    them, is left where it is and said
    """
    exe_dir = Path(exe_dir)
    record = _ours(store, name)
    if record is None:
        return {"ok": False, "error": f"{name} isn't installed"}
    # one file, whatever case each version of the record spelled it in
    known = {}
    for rel, digests in _held(record, "files", "was").items():
        known.setdefault(_key(rel), (rel, set()))[1].update(digests)
    changed = sorted(record.get("kept") or {})
    for rel, digests in sorted(known.values(), key=lambda kv: kv[0].count("/"), reverse=True):
        target = exe_dir / rel
        # a crash part way through an update leaves its copies beside the files
        for path in (target, target.with_name(target.name + NEW),
                     target.with_name(target.name + OLD)):
            now = _digest(path) if path.is_file() else None
            if now is None:
                continue
            if now in digests:
                try:
                    path.unlink()
                except OSError as e:
                    raise LoaderError(f"{name} couldn't come off: {write_failure(e, path)}") from e
            elif path == target:
                changed.append(rel)
    for side, digests in _held(record, "leftover").items():
        path = exe_dir / side
        try:
            if _digest(path) in digests:
                path.unlink()
        except OSError as e:
            raise LoaderError(f"{name} couldn't come off: {write_failure(e, path)}") from e
    _drop_empty(exe_dir, record.get("dirs") or [])
    Path(record["_path"]).unlink(missing_ok=True)
    return {"ok": True, "removed": name, "left": changed}


def remove_parts(root, store, name: str) -> list:
    """
    the files another of the drawer's mods put in on disk as a part of it, LR's
    DLC files beside a patcher's records, off with that mod. `root` is the
    game's folder, which is where a part's paths start
    """
    gone = []
    for record in records(store):
        if record.get("part") == name:
            remove(root, store, record["name"])
            gone.append(record["name"])
    return gone


def strays(store, owners) -> list:
    """
    the parts whose mod isn't on record any more: what a Remove that couldn't
    take them left, which nothing else would ever list or take off
    """
    return [record for record in records(store)
            if record.get("part") and record["part"] not in owners]


_FAMILY = {}
_FAMILY_LOCK = threading.Lock()


def _has(data: bytes, marker: str) -> bool:
    # as an exe keeps text either way: plain, or UTF-16 for a wide string
    return marker.encode() in data or marker.encode("utf-16-le") in data


def ff13fix_built(path) -> bool:
    """
    whether a .dll reads to NovaFix as FF13Fix or one built on it, by
    NovaFix's own rule (compat/modules/foreign_fix.cpp): its ini named and
    one more marker beside it, or its log, OneTweakNG and the game together.
    Loaded from beside the exe, NovaFix turns its own fixes off for it, frame
    pacing and all; from its add-ons, it won't load it. Read once a version
    """
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return False
    spot = (str(path).lower(), stat.st_size, stat.st_mtime_ns)
    with _FAMILY_LOCK:
        if spot in _FAMILY:
            return _FAMILY[spot]
    try:
        data = path.read_bytes() if stat.st_size < 64 << 20 else b""
    except OSError:
        return False
    found = ff13fix_bytes(data)
    with _FAMILY_LOCK:
        _FAMILY[spot] = found
    return found


def ff13fix_bytes(data: bytes) -> bool:
    """ff13fix_built's rule on a .dll's bytes"""
    ini, log, engine = (_has(data, m) for m in ("FF13Fix.ini", "FF13Fix.log", "OneTweakNG"))
    game = _has(data, "Final Fantasy XIII") or _has(data, "ffxiiiimg.exe")
    return bool((ini and (log or engine or game)) or (engine and log and game))


def novafix_off(exe_dir, files) -> str:
    """the line for a mod beside the exe that switches NovaFix off, "" for one that doesn't"""
    if exe_dir is None:
        return ""
    hit = next((rel for rel in sorted(files) if rel.lower().endswith((".dll", ".asi"))
                and ff13fix_built(Path(exe_dir) / rel)), None)
    if hit is None:
        return ""
    return (f"NovaFix turns its own fixes off while {hit.rsplit('/', 1)[-1]} is in, since it's "
            "built on FF13Fix, so the game loses NovaFix's frame pacing and the rest. Remove it "
            "if the game runs worse")


def listed(store, exe_dir=None) -> list:
    """
    the drawer's rows, a folder as one entry. A .dll that's gone since is
    said, which is usually antivirus taking it after it went in
    """
    out = []
    for record in records(store):
        if record.get("part"):
            # files another of the drawer's mods put beside the game, which
            # are listed, and come off, with that one
            continue
        top = sorted({rel.split("/", 1)[0] + ("/" if "/" in rel else "")
                      for rel in record["files"]}, key=str.lower)
        if exe_dir is not None:
            top = [name if name.endswith("/") or (Path(exe_dir) / name).is_file()
                   else f"{name} is missing" for name in top]
        # a .dll in a folder of its own too, which is ReShade's whole install
        missing = [name for name in top if name.endswith(" is missing")] + [
            f"{rel.rsplit('/', 1)[-1]} is missing" for rel in sorted(record["files"])
            if exe_dir is not None and "/" in rel and rel.lower().endswith(".dll")
            and not (Path(exe_dir) / rel).is_file()]
        # the name NovaFix's add-on popup gives a .dll with no name of its own
        # is its file's, VERSION for version.dll, so that's the one to say
        dlls = [name for name in top if name.lower().endswith(".dll")]
        about = missing[0] if missing else \
            f"Loads as {' and '.join(dlls)}" if dlls else "Beside the game"
        if record.get("reshade"):
            about = missing[0] if missing else reshade_about(record["reshade"])
        elif record.get("nccp"):
            # on record from the game's folder, so its top folder says nothing
            top = sorted(rel.rsplit("/", 1)[-1] for rel in record["files"])
            about = "Code patches, applied by NovaFix"
        # Installed shows a mod by its name alone, and a line under it only
        # when something's wrong with it
        warn = missing[0] if missing else novafix_off(exe_dir, record["files"])
        out.append({"name": record.get("name", "mod"), "kind": "loader", "files": top,
                    "about": about, "warn": warn, "managed": True})
    return out


def reshade_about(info) -> str:
    """its preset and the key that opens its menu, which it's no use without"""
    info = info if isinstance(info, dict) else {}
    preset = str(info.get("preset") or "").strip()
    key = str(info.get("menu") or "").strip()
    said = f"{preset} preset" if preset else "Through NovaFix"
    return f"{said}, {key} opens its menu" if key else said
