"""
Shared fixtures.

The NovaFix proxy is a compiled binary we do not commit, so the suite needs a
stand-in to exercise the install path. It lives under tests/ and is never part
of a build: a fake d3d9.dll shipped to a user would be a non-functional proxy
installed into their game, which is worse than a clear "missing bundled asset".
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
STUB_BUNDLE = TESTS / "data" / "novafix-stub"
REAL_MANIFESTS = TESTS.parent / "manifests"


@pytest.fixture(autouse=True, scope="session")
def rain_home(tmp_path_factory):
    """where tests.rain writes its stand-in for the payload that stopped shipping"""
    from tests import rain

    rain.HOME = tmp_path_factory.mktemp("rain")


@pytest.fixture(autouse=True)
def diagnosis_bugs_fail_the_test(monkeypatch):
    """a bug in reading back why a .bat gave up would otherwise read as an idle script"""
    from patcher.mods import diagnose

    monkeypatch.setattr(diagnose, "NET", False)


@pytest.fixture(autouse=True)
def runtimes_only_when_handed_over(monkeypatch):
    """
    the virtual PC lends a .bat this PC's own Python, Java, 7-Zip and the
    like. A test gets none unless it hands one over, so CI's runners, which
    have all of them, play the same as a box with none
    """
    monkeypatch.setattr("patcher.mods.sandbox.host_runtime", lambda runtime: None)
    monkeypatch.setattr("patcher.mods.sandbox.host_archiver", lambda: None)


@pytest.fixture(autouse=True)
def kept_tools_stay_in_the_test(monkeypatch, tmp_path_factory):
    """a base mod's tools kept for its add-ons (deps.py), never into the real user's data folder"""
    keep = tmp_path_factory.mktemp("deps")
    monkeypatch.setattr("patcher.mods.deps.keep_root", lambda: keep)


@pytest.fixture(autouse=True)
def fixture_games_have_no_vanilla_table(monkeypatch):
    """
    the shipped tables describe a real Steam copy, and a fixture game's models
    and zone blobs are a few kilobytes of filler that would read as modded or
    not depending on the id. A test about the tables brings its own.
    """
    monkeypatch.setattr("patcher.mods.hd_vanilla.table", lambda layout: {})
    monkeypatch.setattr("patcher.mods.hd_vanilla.zone_size", lambda layout, name: None)


@dataclass
class StubBundle:
    """a bundle root plus manifests that match it"""
    root: Path
    manifests: Path

    def manifest(self, name: str) -> Path:
        return self.manifests / f"{name}.json"


@pytest.fixture
def stub_bundle(monkeypatch, tmp_path_factory):
    """
    bundled_data_root() pointed at a tree containing a stub d3d9.dll, with
    manifests whose proxy checksum has been dropped.

    the stub cannot satisfy the real proxy's recorded sha256, because it is not
    the real proxy. rather than weaken that check (it exists to catch exactly
    this case) the copy simply omits it, and a separate test verifies the real
    asset against the real hash.
    """
    manifests = tmp_path_factory.mktemp("manifests")
    for source in sorted(REAL_MANIFESTS.glob("*.json")):
        data = json.loads(source.read_text(encoding="utf-8"))
        for patch in data["patches"]:
            if patch["id"] == "install_novafix":
                patch["bundled_files"] = [
                    {k: v for k, v in f.items() if k != "sha256"}
                    for f in patch["bundled_files"]
                ]
        (manifests / source.name).write_text(json.dumps(data, indent=2) + "\n")

    monkeypatch.setattr("patcher.paths.bundled_data_root", lambda: STUB_BUNDLE)
    return StubBundle(root=STUB_BUNDLE, manifests=manifests)


class Log(list):
    """
    collects on_line output, which is (text, stream) since the streams were
    split apart.

    Behaves as the plain list of texts most assertions want, while `.tagged`
    and `.on()` keep the stream for the ones that care. Command echoes stay out
    of the list itself: they are this app talking, not the tool, and a test
    asserting on a tool's exact output should not have to know they exist.
    """

    def __init__(self):
        super().__init__()
        self.tagged = []

    def __bool__(self):
        # a list subclass is falsy while empty, and the code under test guards
        # every emit with `if on_line`. Without this the first line is dropped,
        # which keeps it empty, which drops all of them, and the tests pass
        # anything phrased as "x not in seen".
        return True

    def __call__(self, text, stream="out"):
        self.tagged.append((text, stream))
        if stream != "cmd":
            self.append(text)

    def on(self, stream):
        return [text for text, tag in self.tagged if tag == stream]
