"""
Answers "was it storage or was it the paths" without running an install.

    python tools/check_hd_paths.py "D:\\Mods\\FFXIII HD Project"

Reads the game's own index, works out every path ff13tool -x is going to write
into the mod folder and what the lot weighs, and checks the free space on both
drives involved rather than the one people assume. Thirty seconds, and it
settles the two things an exit code of 1 cannot.

Development tool. The same checks run inside install() now, so a normal user
never needs this; it exists so a failure already in the past can be diagnosed
without repeating it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from patcher.download import free_space                        # noqa: E402
from patcher.mods.hd_install import archive_pair, find_variant  # noqa: E402
from patcher.mods.hd_preflight import (MAX_PATH, explain, headroom,  # noqa: E402
                                       survey_extraction)
from patcher.mods.hd_recipe import detect                      # noqa: E402
from patcher.paths import undo_store_for                       # noqa: E402

GB = 1024 ** 3


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2

    mod_root = Path(argv[1]).expanduser()
    found = detect(mod_root)
    if found is None:
        print(f"{mod_root} does not look like the HD Project")
        return 1

    game = Path(argv[2]).expanduser() if len(argv) > 2 else find_game()
    if game is None:
        print("could not find the game. Pass its folder as a second argument.")
        return 1

    variant = find_variant(game)
    index, blob = archive_pair(game, variant)
    print(f"game      {game}")
    print(f"mod       {mod_root}")
    print(f"archives  {index.name}, {blob.name} ({blob.stat().st_size / GB:.1f} GB)\n")

    survey = survey_extraction(index, mod_root)
    payload = index.stat().st_size + blob.stat().st_size
    needed = survey.bytes_needed + payload * 2
    store = undo_store_for(game) / "hdproject"

    print(f"files to write     {survey.entries}")
    print(f"unpacked size      {survey.bytes_needed / GB:.1f} GB")
    print(f"longest path       {survey.longest} of {MAX_PATH}"
          f"   ({headroom(survey):+d} characters of room)")
    print(f"                   {survey.longest_path}")
    print(f"over the limit     {len(survey.too_long)}")
    print(f"illegal characters {len(survey.illegal)}\n")

    print(f"mod drive needs    {needed / GB:.1f} GB, has "
          f"{free_space(mod_root) / GB:.1f} GB")
    print(f"backup goes to     {store}")
    print(f"backup drive needs {payload / GB:.1f} GB, has "
          f"{free_space(nearest(store)) / GB:.1f} GB\n")

    words = explain(survey, mod_root)
    if words:
        print(words)
        return 1
    print("nothing here would stop the extraction. If it still fails, the "
          "reason is on stderr, which the terminal now shows in red.")
    return 0


def nearest(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def find_game():
    from patcher.bridge import UiBridge
    from patcher.paths import discover_manifests

    answer = UiBridge(manifest_paths=discover_manifests()).scan("ff13")
    return Path(answer["path"]) if answer.get("found") and answer.get("path") else None


if __name__ == "__main__":
    sys.exit(main(sys.argv))
