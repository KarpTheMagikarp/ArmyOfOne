"""
Rewrites the sha256 of every committed bundled asset in every manifest.

Run after replacing a file under assets/mods/. Doing this by hand means three
manifests to edit for one dll, and a missed one is a checksum failure on
whichever game you forgot.

    python tools/pin_bundled.py            # update every pin
    python tools/pin_bundled.py --check    # fail if any pin is stale, for CI
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report stale pins without writing")
    args = parser.parse_args()

    stale, pinned, skipped = [], 0, 0

    for manifest_path in sorted((REPO / "manifests").glob("*.json")):
        doc = json.loads(manifest_path.read_bytes())
        changed = False

        for patch in doc.get("patches", []):
            for spec in patch.get("bundled_files") or []:
                source = spec.get("source")
                if not source:
                    continue
                asset = REPO / source
                if not asset.is_file():
                    # an uncommitted slot. check_bundle.py is what complains
                    # about those; pinning a file that is not there would just
                    # be a second voice saying the same thing.
                    skipped += 1
                    continue

                digest = hashlib.sha256(asset.read_bytes()).hexdigest()
                if spec.get("sha256", "").lower() == digest:
                    pinned += 1
                    continue

                stale.append(f"{manifest_path.stem}/{patch.get('id', '?')} -> {source}")
                spec["sha256"] = digest
                changed = True

        if changed and not args.check:
            manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    print(f"{pinned} already correct, {len(stale)} {'stale' if args.check else 'updated'}, "
          f"{skipped} not committed yet")
    for entry in stale:
        print(f"  {entry}")

    if args.check and stale:
        print("\nrun tools/pin_bundled.py to fix", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
