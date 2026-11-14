"""
Things about a user's machine that will make this install go badly, found
before it does.

Everything here is about the difference between the one PC this was developed
on and the ones it will run on. None of it is exotic: a cloud synced Downloads
folder is the Windows 11 default for a lot of people, and an install that
writes 1.3GB of temporary files into it is a bad afternoon nobody will connect
back to this app.

Warnings, not refusals, with one exception. Somebody who knows their setup
should be able to carry on, and the point is that they are told rather than
that they are stopped.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# The folder names the three big clients use. Matched on a path segment rather
# than a substring: a folder called "MyOneDriveBackups" is not OneDrive, and
# the redirected ones are always a whole segment.
CLOUD = {
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "google drive": "Google Drive",
    "icloud drive": "iCloud Drive",
}

# what a Files On Demand placeholder looks like. FILE_ATTRIBUTE_RECALL_ON_OPEN
# and FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS: the file is in the listing, its
# size is right, and the bytes are not on this machine until something reads
# them, which for three thousand files is a download nobody asked for.
RECALL_ON_OPEN = 0x00040000
RECALL_ON_DATA_ACCESS = 0x00400000


@dataclass
class Note:
    level: str        # "warn" or "stop"
    text: str


def cloud_folder(path: Path) -> str | None:
    """
    the sync client whose folder this sits in, by name, or None.

    Segment matching rather than a substring, and case folded, because the
    redirected folder is "OneDrive - Contoso" on a work account and plain
    "OneDrive" otherwise.

    Split on both separators off the string rather than through Path.parts,
    because a Windows path handed to a POSIX Path is one part containing
    backslashes, and this has to give the same answer wherever it runs.
    """
    for part in str(path).replace("\\", "/").split("/"):
        lowered = part.strip().lower()
        for needle, label in CLOUD.items():
            if lowered == needle or lowered.startswith(needle + " -"):
                return label
    return None


def is_placeholder(path: Path) -> bool:
    """
    whether this file's bytes are still in the cloud rather than on the disk.

    Only ever true on Windows, and False everywhere else rather than raising,
    because st_file_attributes does not exist on other platforms.
    """
    try:
        attributes = os.stat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & (RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS))


def survey(mod_root: Path, install_dir: Path, unpacked_bytes: int = 0) -> list:
    """
    every note worth putting in front of somebody before an install starts.

    Ordered by how likely each one is to be the thing that ruins their evening.
    """
    notes = []
    mod_root, install_dir = Path(mod_root), Path(install_dir)

    where = cloud_folder(mod_root)
    if where:
        size = f"{unpacked_bytes / 1024 ** 3:.1f} GB" if unpacked_bytes else "over a gigabyte"
        notes.append(Note("warn",
            f"The mod folder is inside {where}. Unpacking writes {size} of temporary "
            f"files there, and {where} will try to upload all of it. Moving the mod "
            "folder somewhere outside it, like C:\\FF13HD, makes this much faster and "
            "leaves your cloud storage alone."))

    if cloud_folder(install_dir):
        notes.append(Note("warn",
            f"The game itself is inside {cloud_folder(install_dir)}. Syncing a game "
            "folder while it is being patched can put back a file mid-write. Pausing "
            "syncing until this finishes is worth doing."))

    stale = _placeholders(mod_root)
    if stale:
        notes.append(Note("warn",
            f"{stale} files in the mod folder are cloud placeholders rather than real "
            "files. They will download as the tools reach them, so the install will "
            "stall in places. Right click the folder and choose Always keep on this "
            "device first if you would rather it did not."))

    return notes


def _placeholders(folder: Path, cap: int = 4000) -> int:
    """
    how many files under here are not actually here.

    Capped, because this walks a mod folder that can hold tens of thousands of
    textures and the answer is the same either way: some, or none.
    """
    if not hasattr(os.stat_result, "st_file_attributes"):
        return 0
    found = seen = 0
    try:
        for path in Path(folder).rglob("*"):
            if seen >= cap:
                break
            if not path.is_file():
                continue
            seen += 1
            if is_placeholder(path):
                found += 1
    except OSError:
        pass
    return found
