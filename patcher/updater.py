"""
checks whether a newer version of a game's manifest has been published
as a github release, and describes what changed. This is the backend
for the "update available" dialog (patch notes, Update Now / Cancel),
not the dialog itself.

the network call is injected as a plain function so this is fully
testable without hitting the real network, same pattern as
steam_library_paths being passable into app.py instead of always doing
live steam detection.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

VersionTuple = tuple[int, ...]


def parse_version(version_str: str) -> VersionTuple:
    """turns 'v1.2.0' or '1.2.0' into (1, 2, 0) so versions can be compared numerically"""
    cleaned = version_str.lstrip("vV")
    parts = re.split(r"[.\-]", cleaned)
    numeric_parts = []
    for part in parts:
        if part.isdigit():
            numeric_parts.append(int(part))
        else:
            break  # stop at the first non-numeric bit, e.g. a "-beta" suffix
    return tuple(numeric_parts) if numeric_parts else (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass
class UpdateInfo:
    available: bool
    latest_version: str | None = None
    release_notes: str | None = None
    download_url: str | None = None


def default_fetch(url: str) -> bytes:
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ArmyOfOne",
    })
    with urlopen(request, timeout=10) as response:
        return response.read()


def check_for_update(
    current_version: str,
    releases_api_url: str,
    manifest_asset_name: str,
    fetch: Callable[[str], bytes] = default_fetch,
) -> UpdateInfo:
    """
    hits github's "latest release" endpoint and compares its tag against
    current_version. never available on a fresh install where the
    bundled manifest already matches the latest release; that falls
    out naturally from the version comparison, no separate "is this a
    first run" check needed. also fails quiet (available=False) rather
    than raising on network errors or a repo with no releases yet, an
    update check going down shouldn't be able to break a normal patch
    run.
    """
    try:
        release = json.loads(fetch(releases_api_url))
    except Exception:
        return UpdateInfo(available=False)
    # a captive portal or a filtering proxy can answer with JSON that isn't a release
    if not isinstance(release, dict) or not isinstance(release.get("assets", []), list):
        return UpdateInfo(available=False)

    latest_version = release.get("tag_name") if isinstance(release.get("tag_name"), str) else None
    if not latest_version or not is_newer(latest_version, current_version):
        return UpdateInfo(available=False, latest_version=latest_version)

    download_url = None
    for asset in release.get("assets", []):
        if isinstance(asset, dict) and asset.get("name") == manifest_asset_name:
            download_url = asset.get("browser_download_url")
            break

    return UpdateInfo(
        available=True,
        latest_version=latest_version,
        release_notes=release.get("body", ""),
        download_url=download_url,
    )


def download_and_install_manifest(
    download_url: str,
    destination_path: Path,
    fetch: Callable[[str], bytes] = default_fetch,
) -> None:
    """
    downloads the updated manifest and overwrites destination_path with
    it. doesn't validate the content itself; the caller should try
    load_manifest() on the result before treating the update as done,
    so a corrupt download doesn't silently replace a working manifest.
    """
    destination_path.write_bytes(fetch(download_url))
