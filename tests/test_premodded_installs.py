"""
the game folders people actually have before they find this. FF13Fix,
ReShade dropped in by hand, Special K, DXVK and ASI loaders all live under
the same names the default patches use, and a player's settings beside them.
Apply sets theirs aside rather than over it, and Revert hands back a folder
byte for byte the one it found, every game, every setup.
"""
import hashlib
from pathlib import Path

import pytest

from patcher.revert import revert_all
from tests.helpers import make_export_pe, make_fake_pe

REPO = Path(__file__).resolve().parent.parent

GAMES = {
    "ff13": ("ffxiii", "292120", "FINAL FANTASY XIII", "white_data/prog/win/bin/ffxiiiimg.exe"),
    "ff13-2": ("ffxiii2", "292140", "FINAL FANTASY XIII-2", "alba_data/prog/win/bin/ffxiii2img.exe"),
    "lr": ("ffxiii3", "345350", "LIGHTNING RETURNS FINAL FANTASY XIII", "LRFF13.exe"),
}


def someone_elses(tag: bytes) -> bytes:
    return make_export_pe(("Direct3DCreate9",), machine=0x14C) + bytes(64) + tag


SETUPS = {
    "vanilla": {},
    "ff13fix": {"d3d9.dll": someone_elses(b"FF13Fix"), "FF13Fix.ini": b"[Options]\r\nEnableFix=1\r\n"},
    "reshade by hand": {"d3d9.dll": someone_elses(b"ReShade"), "ReShade.ini": b"[GENERAL]\r\n",
                        "reshade-shaders/Shaders/Mine.fx": b"// mine\n"},
    "special k": {"d3d9.dll": someone_elses(b"SpecialK"), "d3d9.ini": b"[SpecialK]\r\n"},
    "dxvk": {"d3d9.dll": someone_elses(b"dxvk"), "dxvk.conf": b"d3d9.maxFrameRate = 0\n"},
    "asi loader as dinput8": {"dinput8.dll": someone_elses(b"ASILoader"), "scripts/thing.asi": b"MZasi"},
    "asi loader as winmm": {"winmm.dll": someone_elses(b"ASILoader")},
    "read-only dll": {"d3d9.dll": someone_elses(b"ReadOnly")},
}


def exe() -> bytes:
    # a PE32 optional header's magic, which the 4GB patch's 32-bit check reads
    pe = bytearray(make_fake_pe(characteristics=0x0102, machine=0x14C))
    pe[0x98:0x98] = b"\x0b\x01" + bytes(222)
    pe[0x94:0x96] = (224).to_bytes(2, "little")
    return bytes(pe)


def make_game(library: Path, game: str, files: dict, readonly=False) -> Path:
    _, app, folder, exe_relative = GAMES[game]
    root = library / "steamapps" / "common" / folder
    binary = root / exe_relative
    binary.parent.mkdir(parents=True)
    binary.write_bytes(exe())
    (library / "steamapps" / f"appmanifest_{app}.acf").write_text(
        f'"AppState"\n{{\n\t"appid"\t\t"{app}"\n\t"installdir"\t\t"{folder}"\n}}\n', encoding="utf-8")
    for relative, data in files.items():
        path = binary.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if readonly:
            path.chmod(0o444)
    return root


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def bridge_for(tmp_path, monkeypatch):
    from patcher.bridge import UiBridge

    monkeypatch.setattr("patcher.bridge.state_path_for", lambda gid: tmp_path / "state" / f"{gid}.json")
    monkeypatch.setattr("patcher.paths.persistent_data_root", lambda: tmp_path / "data")
    library = tmp_path / "Steam Library"
    paths = {p.stem: p for p in sorted((REPO / "manifests").glob("*.json"))}
    return library, lambda: UiBridge(manifest_paths=paths, steam_library_paths=[library])


def defaults(bridge, game) -> list:
    return [p.ui_job_id for p in bridge.manifest_for(game).patches if p.default_enabled and p.ui_job_id]


@pytest.mark.parametrize("game", list(GAMES))
@pytest.mark.parametrize("setup", list(SETUPS))
def test_apply_keeps_theirs_and_revert_gives_the_folder_back(bridge_for, tmp_path, game, setup):
    library, make_bridge = bridge_for
    root = make_game(library, game, SETUPS[setup], readonly=setup == "read-only dll")
    before = snapshot(root)
    bridge = make_bridge()

    went = bridge.apply(game, defaults(bridge, game))

    assert went["ok"], went
    after = snapshot(root)
    for name, digest in before.items():
        if name.endswith(".exe"):
            continue
        # theirs is either untouched or set aside whole under .backup
        assert after.get(name) == digest or after.get(name + ".backup") == digest, name

    revert_all(root, bridge.manifest_for(game), tmp_path / "state" / f"{GAMES[game][0]}.json")

    back = snapshot(root)
    assert {name: back.get(name) for name in before} == before
    # what's left is only ever ours: the undo store, and XIII's untouched.exe,
    # which is the one clean copy of the exe a player has and stays on purpose
    extra = {name for name in back if name not in before}
    assert all(name.startswith(".patcher-undo/") or name.endswith("untouched.exe") for name in extra), extra


def test_a_player_s_ff13fix_settings_come_back_with_their_ff13fix(bridge_for, tmp_path):
    """
    the purge took FF13Fix.ini off as litter, so Revert handed back their
    FF13Fix dll without the settings they'd tuned for it
    """
    library, make_bridge = bridge_for
    root = make_game(library, "ff13", SETUPS["ff13fix"])
    ini = root / "white_data" / "prog" / "win" / "bin" / "FF13Fix.ini"
    bridge = make_bridge()

    went = bridge.apply("ff13", ["ff13fix"])

    assert went["ok"], went
    assert not ini.exists()
    assert ini.with_name("FF13Fix.ini.backup").read_bytes() == SETUPS["ff13fix"]["FF13Fix.ini"]

    revert_all(root, bridge.manifest_for("ff13"), tmp_path / "state" / "ffxiii.json")

    assert ini.read_bytes() == SETUPS["ff13fix"]["FF13Fix.ini"]
    assert not ini.with_name("FF13Fix.ini.backup").exists()


@pytest.mark.parametrize("game", list(GAMES))
def test_an_old_backup_of_theirs_is_never_written_over(bridge_for, game):
    library, make_bridge = bridge_for
    files = {"d3d9.dll": someone_elses(b"Mine"), "d3d9.dll.backup": someone_elses(b"OlderMine")}
    root = make_game(library, game, files)
    before = snapshot(root)
    bridge = make_bridge()

    went = bridge.apply(game, defaults(bridge, game))

    novafix = next(r for r in went["results"] if r["status"] == "failed")
    assert "press Apply again" in novafix["detail"]
    after = snapshot(root)
    assert all(after[name] == before[name] for name in before if not name.endswith(".exe"))
