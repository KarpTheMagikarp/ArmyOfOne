"""
installs small file bundles (like FF13Fix's d3d9.dll) from a GitHub repo's
latest release. Small drops only, a few hundred KB; the multi-GB packs go
elsewhere. Network calls are injected so tests stay offline.
"""

import io
import json
import os
import tarfile
import zipfile
from pathlib import PurePosixPath, Path
from typing import Callable

from patcher import zipnames
from patcher.updater import default_fetch


def latest_release_asset(github_repo: str,
                         fetch: Callable[[str], bytes] = default_fetch) -> tuple[str | None, str | None]:
    """
    (download url, why not) for a GitHub repo's latest release, skipping the
    auto-generated "Source code" assets. The reason exists because GitHub
    allows 60 unauthenticated requests an hour per IP, so on a campus NAT the
    real answer is often "try later", not "this mod has no release".
    """
    api_url = f"https://api.github.com/repos/{github_repo}/releases/latest"
    try:
        release = json.loads(fetch(api_url))
    except Exception as e:
        return None, _github_reason(e, github_repo)

    # a captive portal or a filtering proxy can answer with JSON that isn't a release
    assets = release.get("assets") if isinstance(release, dict) else None
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "")
        if name.lower().startswith("source code"):
            continue
        url = asset.get("browser_download_url")
        if url:
            return url, None
    return None, f"{github_repo}'s latest release has no downloadable file attached"


def _github_reason(error: Exception, repo: str) -> str:
    status = getattr(error, "code", None)
    headers = getattr(error, "headers", None)
    remaining = headers.get("X-RateLimit-Remaining") if headers else None

    if status == 403 and remaining == "0":
        return ("GitHub is rate limiting this network (60 requests an hour per address). "
                "Wait about an hour and run it again, or install this one by hand")
    if status == 404:
        return f"{repo} has no published releases"
    if status in (401, 403):
        return f"GitHub refused the request for {repo} ({status})"
    return f"couldn't reach GitHub for {repo}: {error}"


def latest_release_asset_url(github_repo: str,
                             fetch: Callable[[str], bytes] = default_fetch) -> str | None:
    """url only, for callers that have nowhere to put a reason"""
    return latest_release_asset(github_repo, fetch=fetch)[0]


# tarfile sniffs the compression itself with mode "r:*", so every one of these
# is the same code path. DXVK publishes .tar.gz, which is the whole reason
# this exists: it was the only thing standing between the LR fix and working.
TAR_EXTENSIONS = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar")

# formats that genuinely need a third-party dependency or a bundled binary.
# listed explicitly rather than allow-listing .dll/.ini, because a release
# shipping a bare file with an odd extension is fine and should still install.
UNSUPPORTED_ARCHIVES = (".rar", ".7z")


class NotAFileError(Exception):
    """the host served a web page instead of the file we asked for"""


def looks_like_web_page(raw: bytes) -> bool:
    """
    community file hosts answer an unauthenticated download with a login
    wall, a confirmation interstitial or a cloudflare challenge, all of which
    are HTML with a 200 status. writing that into a game folder as
    "leviathans_tears.zip" and calling it installed is worse than failing.
    """
    head = raw[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<!-- ")) or b"<title" in head


def _archive_entries(raw: bytes, filename: str, password: bytes | None = None) -> list[tuple[str, bytes]] | None:
    """
    (member path, contents) for every regular file in the archive, or None if
    this isn't an archive shape we handle. one function for zip and tar so
    everything downstream (subdir scoping, collision checks, flattening) works
    identically regardless of how the mod author packaged their release.
    """
    lowered = filename.lower()

    if lowered.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            if password:
                zf.setpassword(password)
            return [(name, zf.read(name)) for name in zf.namelist() if not name.endswith("/")]

    if any(lowered.endswith(ext) for ext in TAR_EXTENSIONS):
        entries = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
            for member in tf.getmembers():
                # regular files only. a tar can carry symlinks, hardlinks,
                # devices and absolute paths, and a symlink pointing out of the
                # tree is the classic way to get a write outside the target
                # folder. we only ever want the bytes of real files anyway.
                if not member.isfile():
                    continue
                handle = tf.extractfile(member)
                if handle is None:
                    continue
                entries.append((member.name, handle.read()))
        return entries

    return None


def _members_to_install(names: list[str], source_subdir: str | None) -> list[str]:
    """
    picks which zip members get installed, and refuses to guess. Flattening
    is right for one wrapper folder (FF13Fix_164/d3d9.dll goes beside the
    exe) and wrong for per-architecture builds (DXVK's x32/ and x64/
    d3d9.dll), where last one wins and a 64-bit dll lands in a 32-bit game.
    source_subdir picks the folder; a collision without it raises.
    """
    files = [n for n in names if not n.endswith("/") and Path(n).name]

    if source_subdir:
        # matches a path COMPONENT, not a prefix. DXVK's tarball wraps
        # everything in a version folder, so the real path is
        # dxvk-2.3/x32/d3d9.dll and a startswith("x32/") test finds nothing.
        wanted = source_subdir.strip("/").lower()
        scoped = [n for n in files
                  if wanted in [part.lower() for part in PurePosixPath(n.replace("\\", "/")).parts[:-1]]]
        if not scoped:
            raise ValueError(f"archive has no '{source_subdir}' folder to install from")
        files = scoped

    seen: dict[str, str] = {}
    for member in files:
        name = Path(member).name
        if name in seen:
            raise ValueError(
                f"archive contains more than one '{name}' ({seen[name]} and {member}). "
                "set source_subdir on this patch to say which one to install"
            )
        seen[name] = member
    return files


def _sniff_extension(raw: bytes, filename: str) -> str:
    """
    some download urls carry no filename at all ("...?do=download"), so the
    extension has to come from the bytes. zip and gzip both have unambiguous
    magic numbers.
    """
    if "." in filename:
        return filename
    if raw[:2] == b"PK":
        return filename + ".zip"
    if raw[:2] == b"\x1f\x8b":
        return filename + ".tar.gz"
    return filename


def install_from_url(
    download_url: str,
    install_dir: Path,
    fetch: Callable[[str], bytes] = default_fetch,
    source_subdir: str | None = None,
    expected_sha256: str | None = None,
    archive_password: str | None = None,
    opener=None,
    on_progress=None,
    cancel=None,
    staging_dir: Path | None = None,
) -> list[str]:
    """
    downloads whatever's at download_url and installs it into install_dir.
    Streams the whole way, so a 300KB dll and a 6GB pack cost the same memory.
    Archives extract flat, since the dll has to sit beside the exe; anything
    else installs as a single file under its own name.
    """
    from patcher.download import (
        Cancelled,
        opener_for,
        sha256_of_file,
        stream_download,
        stream_extract,
    )

    filename = zipnames.filename_from_url(download_url)

    # deliberately does NOT create install_dir yet. a download that turns out
    # to be a login page, or fails its checksum, must leave nothing behind at
    # all, and an empty folder where a mod should be is its own kind of lie.
    staging = staging_dir or _staging_for(install_dir)
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / filename

    try:
        stream_download(download_url, archive,
                        opener=opener or opener_for(fetch),
                        on_progress=on_progress, cancel=cancel, label=filename)

        with archive.open("rb") as handle:
            head = handle.read(512)

        if looks_like_web_page(head):
            raise NotAFileError(
                "the host returned a web page instead of the file, which usually means "
                "the download needs a browser"
            )

        # only meaningful for a mirror. a Nexus CDN link is short lived and
        # signed by them; a file we rehost is ours to vouch for, and "the
        # mirror got replaced" should fail loudly rather than install quietly.
        if expected_sha256:
            actual = sha256_of_file(archive, cancel=cancel)
            if actual.lower() != expected_sha256.lower():
                raise ValueError(
                    f"{filename} does not match its expected checksum "
                    f"(got {actual[:16]}..., expected {expected_sha256[:16]}...)"
                )

        resolved = _sniff_extension(head, filename)

        if any(resolved.lower().endswith(ext) for ext in UNSUPPORTED_ARCHIVES):
            raise ValueError(
                f"{filename} needs an extractor python doesn't ship with (.rar and .7z). "
                "download it manually and copy the contents into the game folder"
            )

        if _is_archive(resolved):
            if archive.name != resolved:
                archive = archive.rename(staging / resolved)
            _guard_collisions(archive, source_subdir)
            install_dir.mkdir(parents=True, exist_ok=True)
            return stream_extract(archive, install_dir, flatten=True,
                                  source_subdir=source_subdir,
                                  on_progress=on_progress, cancel=cancel)

        install_dir.mkdir(parents=True, exist_ok=True)
        target = install_dir / filename
        # replace, not rename: a leftover from a previous attempt would make
        # this fail on windows
        os.replace(archive, target)
        archive = None
        return [filename]
    except Cancelled:
        # the .part stays. resume is the whole reason it has that name, and a
        # cancelled 6GB download should not cost the user 6GB again.
        raise
    finally:
        if archive is not None and archive.exists():
            archive.unlink(missing_ok=True)
        _prune(staging)


def _staging_for(install_dir: Path) -> Path:
    """
    a scratch folder on the same volume as the eventual install, so the
    finishing move is a rename rather than a multi-gigabyte copy across
    devices. walks up to a directory that actually exists, because the target
    may not have been created yet.
    """
    anchor = install_dir
    while not anchor.exists() and anchor.parent != anchor:
        anchor = anchor.parent
    return anchor / ".patcher-tmp"


def _is_archive(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith(".zip") or any(lowered.endswith(e) for e in TAR_EXTENSIONS)


def _guard_collisions(archive: Path, source_subdir: str | None) -> None:
    """
    a flat extraction of an archive holding the same filename twice would
    silently install whichever came last. DXVK ships x32/d3d9.dll and
    x64/d3d9.dll, and picking wrong is a game that will not launch with no
    useful error. reads only the index, so it costs nothing on a huge file.
    """
    import tarfile as _tarfile
    import zipfile as _zipfile

    if archive.name.lower().endswith(".zip"):
        with _zipfile.ZipFile(archive) as zf:
            names = [zipnames.repair(i) for i in zf.infolist() if not i.is_dir()]
    else:
        with _tarfile.open(archive, mode="r:*") as tf:
            names = [m.name for m in tf.getmembers() if m.isfile()]
    _members_to_install(names, source_subdir)


# how long a half-finished download is worth keeping for a resume. anything
# older is from an attempt nobody is coming back to, and it is sitting inside
# the Steam install where "Verify integrity of game files" will find it.
PART_TTL_SECONDS = 24 * 60 * 60


def _prune(staging: Path) -> None:
    """
    clears the staging folder when nothing in it is still useful.

    A cancelled download leaves a .part deliberately, so the empty check alone
    never fired and .patcher-tmp stayed in the game folder indefinitely. A
    .part is only worth that if someone might still resume it.
    """
    import time

    try:
        if not staging.is_dir():
            return
        cutoff = time.time() - PART_TTL_SECONDS
        for leftover in staging.glob("*.part"):
            try:
                if leftover.stat().st_mtime < cutoff:
                    leftover.unlink()
            except OSError:
                continue
        if not any(staging.iterdir()):
            staging.rmdir()
    except OSError:
        pass


