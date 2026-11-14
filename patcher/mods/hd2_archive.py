"""
XIII-2's indexes, opened for ff13tool and sealed again after it.

The game encrypts every filelist, the main one and each zone's. ff13tool v1.4
only works on a decrypted copy, so their .bat wraps every call in ffxiiicrypt
-d and -e. We do the same work with patcher.archive.crypt, which matched
ffxiiicrypt byte for byte on the real filelistu in both directions when both
were run side by side under wine.

Sealing also writes the checksum, which theirs never does. ff13tool leaves a
zero there after -c or -i and only fixes it by shelling out to ffxiiicrypt,
which it looks for in the working directory, where their .bat never puts it.
A vanilla index always carries the right value, so ours does too.
"""

from pathlib import Path

from patcher.archive import crypt
from patcher.archive.filelist import FilelistError, parse
from patcher.fileops import write_atomic


class SealError(Exception):
    pass


def _entries(raw: bytes, name: str) -> list:
    try:
        return [(e.path, e.sector_offset, e.uncompressed, e.stored) for e in parse(raw).entries()]
    except (FilelistError, crypt.DecryptError, ValueError) as e:
        raise SealError(f"{name} does not read as a filelist ({e})") from e


def open_index(source: Path, target: Path | None = None) -> None:
    """a decrypted copy of source at target, or in place. Already open is fine."""
    source = Path(source)
    raw = source.read_bytes()
    try:
        plain = crypt.decrypt(raw) if crypt.is_encrypted(raw) else raw
    except (crypt.DecryptError, ValueError) as e:
        raise SealError(f"{source.name} does not decrypt ({e})") from e
    if not crypt.looks_decrypted(plain):
        raise SealError(f"{source.name} does not decrypt to a filelist")
    _entries(plain, source.name)
    write_atomic(Path(target or source), plain, durable=True)


def seal(plain: bytes, name: str = "the index") -> bytes:
    """checksum stamped, encrypted, and proven to read back as the same index"""
    if not crypt.looks_decrypted(plain):
        raise SealError(f"{name} is not a decrypted filelist, so it cannot be sealed")
    before = _entries(plain, name)
    sealed = crypt.encrypt(crypt.apply_checksum(plain))
    if crypt.looks_decrypted(sealed) or _entries(sealed, name) != before:
        raise SealError(f"{name} did not read back the same once sealed")
    return sealed


def seal_index(path: Path) -> None:
    """path sealed where it lies"""
    path = Path(path)
    write_atomic(path, seal(path.read_bytes(), path.name), durable=True)


def run_open(step, mod_root: Path, on_line=None) -> int:
    """OPEN: args are (source,) to open in place, or (source, target)"""
    from patcher.mods.hd_runner import ERR, at

    source = at(mod_root, step.args[0])
    target = at(mod_root, step.args[1]) if len(step.args) > 1 else source
    try:
        open_index(source, target)
    except (OSError, SealError) as e:
        if on_line:
            on_line(f"could not open {source.name} for ff13tool: {e}", ERR)
        return 1
    return 0


def run_seal(step, mod_root: Path, on_line=None) -> int:
    """SEAL: args are (index,)"""
    from patcher.mods.hd_runner import ERR, at

    path = at(mod_root, step.args[0])
    try:
        seal_index(path)
    except (OSError, SealError) as e:
        if on_line:
            on_line(f"could not seal {path.name}: {e}", ERR)
        return 1
    if on_line:
        on_line(f"{path.name} sealed, checksum written")
    return 0
