"""
everything that pulls files from somewhere else: github releases, nexus
downloads, the nxm:// handoff. the engine imports the install helpers from
here rather than from the individual modules, so swapping a source out is a
one line change in this file instead of a hunt through engine.py.
"""

from patcher.mods.file_install import (
    install_from_url,
    latest_release_asset,
    latest_release_asset_url,
)
from patcher.mods.hd_pack_installer import (
    download_and_stage,
    extract_archive,
    find_installer_candidates,
)

__all__ = [
    "install_from_url",
    "latest_release_asset",
    "latest_release_asset_url",
    "download_and_stage",
    "extract_archive",
    "find_installer_candidates",
]
