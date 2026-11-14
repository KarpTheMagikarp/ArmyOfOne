"""
The game's exe on the dedicated GPU in Windows' graphics settings, never over a
choice already there, and back off with Revert only while it's still ours
"""

import sys
import types

import pytest

from patcher import gpu_preference


@pytest.fixture
def registry(monkeypatch):
    """a winreg holding HKCU's UserGpuPreferences, as name -> value"""
    values, made = {}, []

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(root, path, *rest):
        if path not in made:
            raise FileNotFoundError(path)
        return Key(path)

    def create_key(root, path):
        if path not in made:
            made.append(path)
        return Key(path)

    def query(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_QUERY_VALUE=1, KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=open_key, CreateKey=create_key, QueryValueEx=query,
        SetValueEx=lambda key, name, _, kind, value: values.__setitem__(name, value),
        DeleteValue=lambda key, name: values.pop(name))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return values, made


EXE = r"E:\SteamLibrary\steamapps\common\FINAL FANTASY XIII\white_data\prog\win\bin\ffxiiiimg.exe"


def test_the_game_goes_on_the_dedicated_gpu_and_comes_back_off(registry, tmp_path):
    values, _ = registry
    assert gpu_preference.prefer(EXE, tmp_path)
    assert values == {EXE: "GpuPreference=2;"}
    # set once: the next launch has nothing to do
    assert not gpu_preference.prefer(EXE, tmp_path)
    assert gpu_preference.release(tmp_path) == [EXE]
    assert values == {}


def test_a_preference_already_there_is_theirs(registry, tmp_path):
    values, made = registry
    made.append(gpu_preference.KEY)
    values[EXE] = "GpuPreference=1;"
    assert not gpu_preference.prefer(EXE, tmp_path)
    assert gpu_preference.release(tmp_path) == []
    assert values == {EXE: "GpuPreference=1;"}


def test_one_changed_since_stays_through_a_revert(registry, tmp_path):
    values, _ = registry
    gpu_preference.prefer(EXE, tmp_path)
    # power saving picked in Windows' settings afterwards
    values[EXE] = "GpuPreference=1;"
    assert gpu_preference.release(tmp_path) == []
    assert values == {EXE: "GpuPreference=1;"}
    assert not (tmp_path / gpu_preference.MARK).exists()


def test_off_windows_it_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert not gpu_preference.prefer(EXE, tmp_path)
    assert gpu_preference.release(tmp_path) == []
