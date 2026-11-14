"""
A Nova Chrysalia pack's External installer, played the way Nova runs it: in
its own folder, whitepath.txt beside it, the game unpacked under that path,
and Data installed after it's done.
"""

from tests.test_code_patches import folder_of, vdf
from tests.test_code_patches import world  # noqa: F401  the fixture
from tests.test_game_files import read
from tests.test_mod_drops import C108, zipped

CONFIG = (b"[ModPackConfig]\r\nName = Swap\r\nGameEntry = 2\r\n"
          b"[NovaChysaliaConfig]\r\nExtPatch = true\r\n")
SPOT = C108.replace("/", "\\")


def pack(tmp, files: dict, config: bytes = CONFIG):
    return zipped(tmp, "swap.ncmp", {"Swap/modconfig.ini": config,
                                     **{f"Swap/{k}": v for k, v in files.items()}})


def test_it_finds_the_game_unpacked_where_whitepath_says_and_writes_there(world):
    bridge, game, tmp = world
    bat = (f'@echo off\r\nset /p WP=<whitepath.txt\r\n'
           f'if not exist "%WP%{SPOT}" exit /b 1\r\n'
           f'copy /y new.imgb "%WP%{SPOT}"\r\n').encode()
    drop = pack(tmp, {"External/Install.bat": bat, "External/new.imgb": b"n" * 3000})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"] and done["modName"] == "Swap", done
    assert done["message"].startswith("Played its installer, Install.bat, on a copy"), done
    assert read(game, C108, "u") == b"n" * 3000 and read(game, C108, "c") == b"n" * 3000


def test_what_it_puts_in_data_goes_in_after_it(world):
    bridge, game, tmp = world
    bat = f'@echo off\r\nmkdir "..\\Data\\chr\\pc\\c108\\bin"\r\ncopy /y d.bin "..\\Data\\{SPOT}"\r\n'
    drop = pack(tmp, {"External/Install.bat": bat.encode(), "External/d.bin": b"d" * 3000})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert read(game, C108, "u") == b"d" * 3000


def test_data_has_the_last_word_over_the_installer(world):
    bridge, game, tmp = world
    bat = f'@echo off\r\nset /p WP=<whitepath.txt\r\ncopy /y x.bin "%WP%{SPOT}"\r\n'
    drop = pack(tmp, {"External/Install.bat": bat.encode(), "External/x.bin": b"x" * 3000,
                      f"Data/{C108}": b"D" * 3000})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert read(game, C108, "u") == b"D" * 3000 and read(game, C108, "c") == b"D" * 3000


def test_setup_beats_install_the_way_nova_picks(world):
    bridge, game, tmp = world
    write = '@echo off\r\nset /p WP=<whitepath.txt\r\ncopy /y {} "%WP%' + SPOT + '"\r\n'
    drop = pack(tmp, {"External/Install.bat": write.format("i.bin").encode(),
                      "External/setup.bat": write.format("s.bin").encode(),
                      "External/i.bin": b"i" * 3000, "External/s.bin": b"s" * 3000})
    assert bridge.install_mod("ff13-2", str(drop))["ok"]
    assert read(game, C108, "u") == b"s" * 3000


def test_one_its_config_turns_off_isnt_run(world):
    bridge, game, tmp = world
    bat = f'@echo off\r\nset /p WP=<whitepath.txt\r\ncopy /y x.bin "%WP%{SPOT}"\r\n'
    drop = pack(tmp, {"External/Install.bat": bat.encode(), "External/x.bin": b"x" * 3000,
                      f"Data/{C108}": b"D" * 3000},
                config=CONFIG.replace(b"ExtPatch = true", b"ExtPatch = false"))
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert read(game, C108, "u") == b"D" * 3000
    assert "Played" not in done["message"] and "Install.bat" in done["message"], done


def test_its_code_patches_still_go_in_after_it_plays(world):
    bridge, game, tmp = world
    bat = f'@echo off\r\nset /p WP=<whitepath.txt\r\ncopy /y x.bin "%WP%{SPOT}"\r\n'
    drop = pack(tmp, {"External/Install.bat": bat.encode(), "External/x.bin": b"x" * 3000,
                      "Code/Swap.nccp": vdf(("1", "1000|90"))})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert read(game, C108, "u") == b"x" * 3000
    assert (folder_of(game) / "Swap.nccp").is_file()
    assert bridge.remove_file_mod("ff13-2", "Swap")["ok"]
    assert not (folder_of(game) / "Swap.nccp").exists()


def test_an_exe_installer_is_started_from_its_own_folder_too(world):
    """
    nothing here can run it, but it's the one Nova would have, and what it
    couldn't do is said rather than its Data going in as if it had run
    """
    bridge, game, tmp = world
    drop = pack(tmp, {"External/Setup.exe": b"MZ" + bytes(200), f"Data/{C108}": b"D" * 3000})
    done = bridge.install_mod("ff13-2", str(drop))
    assert "Setup.exe, its installer" in (done.get("message") or done.get("error") or ""), done


def test_it_sees_the_packs_data_in_the_game_already_the_way_nova_left_it(world):
    """Nova puts Data in before it runs the installer, and again after"""
    bridge, game, tmp = world
    bat = (f'@echo off\r\nset /p WP=<whitepath.txt\r\n'
           f'copy /y "%WP%{SPOT}" "..\\Data\\seen.bin"\r\n').encode()
    drop = pack(tmp, {"External/Install.bat": bat, f"Data/{C108}": b"D" * 3000})
    done = bridge.install_mod("ff13-2", str(drop))
    assert done["ok"], done
    assert (game / "alba_data" / "seen.bin").read_bytes() == b"D" * 3000
