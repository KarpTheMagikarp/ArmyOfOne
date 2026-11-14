"""
handles what happens after nexus_api.get_download_url() gives us a
real download link: fetch the bytes, extract if it's something we
know how to extract, and identify what looks like an installer inside.

deliberately doesn't prompt for anything or launch anything itself -
that's main.py's job, kept separate so this logic is testable without
needing input() or actually running a program.

honest limitation: .zip and the tar family (.tar.gz, .tar.xz, .tar.bz2)
are handled, both from the stdlib. nexus mod archives are very commonly
.rar or .7z, which python has no support for at all; extracting those
would mean bundling an external unrar tool or a third-party dependency.
anything unrecognized is left as-is with a message pointing at the
downloaded file, which is still a real win: the login-gated download is
done automatically, and manually extracting an archive you already have
is a small, universally understood step.
"""

import tarfile
import zipfile
from pathlib import Path

from patcher import zipnames
from typing import Callable

from patcher.fileops import write_atomic
from patcher.updater import default_fetch

INSTALLER_EXTENSIONS = (".exe", ".bat")


def download_and_stage(
    download_url: str,
    download_dir: Path,
    fetch: Callable[[str], bytes] = default_fetch,
    on_progress=None,
    cancel=None,
) -> Path:
    """
    downloads the file into download_dir, returns the path it landed at.

    STREAMED, and it was not. This handles the largest file the whole tool ever
    touches, tens of gigabytes of HD textures, and it was the one download that
    reached none of download.py: `raw = fetch(url)` pulled the entire thing into
    memory and write_atomic then needed a second full copy on disk. No progress
    at all, no cancel, no free space check, no resume, no length verification.
    A user with the game on D: and 20GB free on C: watched it fill C: and fail.

    fetch is still honoured when a caller passes one, because the tests inject
    a fake and a caller with its own transport should keep it. The default goes
    through the same streaming path as every other download.
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    filename = zipnames.filename_from_url(download_url)
    file_path = download_dir / filename

    if fetch is default_fetch:
        from patcher.download import stream_download

        return stream_download(download_url, file_path, label=filename,
                               on_progress=on_progress, cancel=cancel)

    raw = fetch(download_url)
    write_atomic(file_path, raw)
    return file_path


TAR_EXTENSIONS = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar")


def extract_archive(file_path: Path) -> Path | None:
    """
    extracts file_path into a sibling folder, returns that folder. returns
    None for anything we don't know how to extract (.rar, .7z), not an
    error, just "can't auto-extract this one."

    unlike file_install, this keeps the archive's internal folder structure:
    an HD texture pack is a tree the game reads, not a couple of dlls that
    have to sit next to an exe, so flattening it would destroy it.
    """
    name = file_path.name.lower()

    if name.endswith(".zip"):
        extract_dir = file_path.with_suffix("")
        with zipfile.ZipFile(file_path) as zf:
            zipnames.extract_all(zf, extract_dir)
        return extract_dir

    if any(name.endswith(ext) for ext in TAR_EXTENSIONS):
        # .tar.gz has two suffixes, so with_suffix("") would leave a stray
        # ".tar" on the folder name
        stem = file_path.name
        for ext in TAR_EXTENSIONS:
            if name.endswith(ext):
                stem = file_path.name[: -len(ext)]
                break
        # filter="data" is the stdlib's own hardening: it strips absolute
        # paths, ".." traversal, symlinks pointing outside the tree and device
        # nodes. A Python without it gets "extract this yourself", not an
        # extraction with the guard quietly dropped.
        if not hasattr(tarfile, "data_filter"):
            return None
        extract_dir = file_path.with_name(stem)
        with tarfile.open(file_path, mode="r:*") as tf:
            tf.extractall(extract_dir, filter="data")
        return extract_dir

    return None


# the old name, kept so nothing that imports it breaks mid-refactor
extract_if_zip = extract_archive


def find_installer_candidates(extract_dir: Path) -> list[Path]:
    """finds likely installers at the top level of an extracted folder, sorted for stable, predictable ordering"""
    candidates = [
        p for p in extract_dir.iterdir()
        if p.is_file() and p.suffix.lower() in INSTALLER_EXTENSIONS
    ]
    return sorted(candidates, key=lambda p: p.name.lower())
