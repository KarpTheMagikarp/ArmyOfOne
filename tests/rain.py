"""
Leviathan's Tears on bytes of our own, for the injection tests.

It doesn't ship any more and Apply never puts it in, but its entry stays for
what an earlier version applied, and what it exercised is every injection's:
two weather files into both voice sets' archives, one payload for both.
manifest() is XIII-2's with that entry back on Apply, writing PAYLOAD
"""

import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path

from patcher.manifest import load_manifest

REPO = Path(__file__).resolve().parent.parent
LT_ID = "install_leviathans_tears"
WEATHER = ["vfx/field/weather/weather07/veffs.jp.win32.imgb",
           "vfx/field/weather/weather08/veffs.jp.win32.imgb"]
# the real one's length, and enough noise to store in the same 16 sectors it
# did, so the archives fit and grow around it the way they did around that
PAYLOAD = random.Random("rain").randbytes(31_725) + bytes(90_512 - 31_725)
SHA = hashlib.sha256(PAYLOAD).hexdigest()
# conftest's session fixture points this at pytest's own temp folder
HOME: Path | None = None


def payload() -> Path:
    """PAYLOAD on disk, written once a session"""
    path = HOME / "veffs.jp.win32.imgb"
    if not path.is_file():
        path.write_bytes(PAYLOAD)
    return path


def appliable(manifest):
    """the manifest with its retired entry back on Apply, on PAYLOAD"""
    return replace(manifest, patches=[
        replace(p, retired=False, injections=[
            dict(spec, bundled=str(payload()), sha256=SHA) for spec in p.injections])
        if p.id == LT_ID else p for p in manifest.patches])


def manifest():
    return appliable(load_manifest(REPO / "manifests" / "ffxiii2.json"))


def manifest_file(folder: Path) -> Path:
    """the same as a file, for what loads its own: the window's bridge"""
    doc = json.loads((REPO / "manifests" / "ffxiii2.json").read_text(encoding="utf-8"))
    for patch in doc["patches"]:
        if patch["id"] == LT_ID:
            patch.pop("retired")
            for spec in patch["injections"]:
                spec.update(bundled=str(payload()), sha256=SHA)
    path = folder / "ffxiii2.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path
