"""
Refuses to let a release ship that cannot do what its manifests promise.

Every bundled_install patch names a file under assets/. Two of those files are
compiled binaries that are deliberately not in git, so a checkout builds green,
passes every test, produces an exe, and then reports

    NovaFix: couldn't apply - missing bundled asset assets/mods/novafix/d3d9.dll

on all three games the first time anybody presses Apply. The engine handles it
correctly as a per-patch failure, which is exactly why nothing upstream ever
noticed.

Run against the PyInstaller output in CI, or against a checkout with no
argument to see what is still missing locally.

    python tools/check_bundle.py dist/ArmyOfOne/_internal
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def bundled_sources(manifest_path: Path):
    """(patch id, default_enabled, relative asset path) for every bundled file"""
    data = json.loads(manifest_path.read_bytes())
    for patch in data.get("patches", []):
        for spec in patch.get("bundled_files") or []:
            source = spec.get("source")
            if source:
                yield patch.get("id", "?"), bool(patch.get("default_enabled")), source


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO
    manifest_dir = root / "manifests"
    if not manifest_dir.is_dir():
        print(f"no manifests/ under {root}", file=sys.stderr)
        return 2

    missing, checked = [], 0
    for manifest_path in sorted(manifest_dir.glob("*.json")):
        for patch_id, on_by_default, source in bundled_sources(manifest_path):
            checked += 1
            asset = root / source
            if asset.is_file() and asset.stat().st_size > 0:
                continue
            missing.append((manifest_path.stem, patch_id, on_by_default, source))

    print(f"checked {checked} bundled assets across {len(list(manifest_dir.glob('*.json')))} manifests")
    for game, patch_id, on_by_default, source in missing:
        mark = "ON BY DEFAULT" if on_by_default else "opt-in"
        print(f"  MISSING [{mark}] {game}/{patch_id} -> {source}", file=sys.stderr)

    # nothing fails without these, it just stops knowing what's in a game with
    # no backup, so the badges go back to trusting the record
    tables = [manifest_dir / "vanilla" / f"{game}.txt" for game in ("ffxiii", "ffxiii2")]
    gone = [t for t in tables if not t.is_file() or t.stat().st_size == 0]
    for table in gone:
        print(f"  MISSING the vanilla model sizes {table.relative_to(root)}", file=sys.stderr)

    if not missing and not gone:
        print("every bundled asset is present")
        return 0
    if gone:
        print("\nrefusing: the HD checks need the vanilla model sizes", file=sys.stderr)
        return 1
    # only a default-enabled patch is a release blocker. an opt-in one that
    # ships without its file fails for the people who asked for it, which is
    # bad but not "the headline feature is broken for everyone".
    if any(on_by_default for _, _, on_by_default, _ in missing):
        print("\nrefusing: a default-enabled patch has no file to install",
              file=sys.stderr)
        return 1
    print("\nonly opt-in patches are missing files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
