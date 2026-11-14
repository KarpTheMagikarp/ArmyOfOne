"""
Turns a mod archive you have permission to rehost into the manifest entry that
makes it a one-click install.

Why this exists: every patch sourced from GitHub or a plain URL installs with
no account, no tier and no clicks. FF13Fix, DXVK and the 4GB patch already
work that way, which is why those rows always say DONE. The only rows that
ever ask the user for anything are the ones still pointed at Nexus, and the
fix is not a better sign-in flow, it is not needing Nexus for them.

Usage:
    python tools/mirror.py path/to/mod.zip --game ffxiii --patch install_cutscene_audio
    python tools/mirror.py mod.zip --game ffxiii --patch install_cutscene_audio --apply

Prints the mirror_url / mirror_sha256 pair to add, and with --apply writes it
into the manifest for you. Upload the file to a GitHub release first and pass
--url, or pass nothing and it prints the release URL it expects.

Get permission before you mirror anything. It is a redistribution of someone
else's work, and a project that rehosts without asking gets its mods pulled
and its name attached to a takedown. There is a ready-to-send request in
MIRRORS.md.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "manifests"

# the release the patcher's own updater already points at, so mirrors ride
# along with a build rather than needing separate hosting
DEFAULT_RELEASE = "https://github.com/REPLACE_WITH_OWNER/REPLACE_WITH_REPO/releases/download/mirrors"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        # chunked because a texture pack can be several gb and this should not
        # need that much memory to hash
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(game: str) -> tuple[Path, dict]:
    path = MANIFEST_DIR / f"{game}.json"
    if not path.exists():
        sys.exit(f"no manifest at {path}. games: "
                 + ", ".join(sorted(p.stem for p in MANIFEST_DIR.glob('*.json'))))
    return path, json.loads(path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="build a mirror entry for a mod archive")
    parser.add_argument("archive", type=Path, help="the mod file you are mirroring")
    parser.add_argument("--game", required=True, help="manifest stem, e.g. ffxiii")
    parser.add_argument("--patch", required=True, help="patch id to attach the mirror to")
    parser.add_argument("--url", help="where the file will be downloadable from")
    parser.add_argument("--apply", action="store_true", help="write it into the manifest")
    args = parser.parse_args()

    if not args.archive.is_file():
        sys.exit(f"no such file: {args.archive}")

    path, manifest = load(args.game)
    patch = next((p for p in manifest["patches"] if p["id"] == args.patch), None)
    if patch is None:
        sys.exit(f"{path.name} has no patch {args.patch!r}. ids: "
                 + ", ".join(p["id"] for p in manifest["patches"]))

    checksum = sha256_of(args.archive)
    url = args.url or f"{DEFAULT_RELEASE}/{args.archive.name}"
    size_mb = args.archive.stat().st_size / (1024 * 1024)

    print(f"{args.archive.name}  ({size_mb:.1f} MB)")
    print(f"  sha256: {checksum}")
    print()
    print(f'  "mirror_url": "{url}",')
    print(f'  "mirror_sha256": "{checksum}"')
    print()

    if "REPLACE_WITH_" in url:
        print("note: that url still has the release placeholder in it. pass --url with the")
        print("      real download link, or fill in DEFAULT_RELEASE at the top of this file.")
        print()

    if not args.apply:
        print("add those two keys to the patch, or re-run with --apply to do it here.")
        return 0

    patch["mirror_url"] = url
    patch["mirror_sha256"] = checksum
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote mirror into {path.name} for {args.patch}.")
    print("that patch now installs with no Nexus account, for every user, in one click.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
