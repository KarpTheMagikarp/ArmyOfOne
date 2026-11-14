"""
stores the user's personal Nexus API key locally so they only ever paste it
in once. Registering as the nxm:// handler lives in patcher/nxm_registry.py,
which is the one that backs up and respects whoever had it first.
"""

import json
from pathlib import Path

from patcher.fileops import write_atomic


def read_config(config_path: Path) -> dict:
    """
    the config as a dict, whatever's on disk. Parsing fine isn't enough:
    `null` or a list is valid JSON, and every reader here went straight to
    .get() on it and took Apply down with an AttributeError
    """
    try:
        data = json.loads(config_path.read_bytes())
    # ValueError rather than JSONDecodeError: json.loads on bytes raises
    # UnicodeDecodeError for a file that is not valid utf-8, and that is a
    # SIBLING of JSONDecodeError under ValueError, not a subclass
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_api_key(config_path: Path, api_key: str) -> None:
    """
    stores the key without touching anything else in the file.

    This wrote a fresh single-key dict, which took nexus_app_id with it: the id
    that makes one-click sign-in possible was deleted by the sign-in that used
    it. nexus_auth.sign_out has always merged, for exactly this reason, so the
    two halves of one file now agree about how many keys it has.

    A file we cannot parse is replaced rather than preserved. There is nothing
    to merge into, and refusing to store a key the user has just authorised in
    a browser because of a hand-edit elsewhere in the file is the worse of the
    two failures.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_config(config_path)
    existing["nexus_api_key"] = api_key
    write_atomic(config_path, json.dumps(existing, indent=2).encode("utf-8"))


def load_api_key(config_path: Path) -> str | None:
    key = read_config(config_path).get("nexus_api_key")
    return key if isinstance(key, str) and key else None
