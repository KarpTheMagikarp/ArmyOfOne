import json
from pathlib import Path

import pytest

from patcher.bridge import UNSUPPORTED, UiBridge
from patcher.engine import PatchStatus
from tests.helpers import make_fake_pe, make_zip_bytes


def write_manifest(tmp_path, patches, game_id="ffxiii", ui_id="ff13"):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(exist_ok=True)
    path = manifest_dir / f"{game_id}.json"
    path.write_text(json.dumps({
        "game_id": game_id,
        "display_name": "Final Fantasy XIII",
        "manifest_version": "v1.0.0",
        "steam_app_id": "292120",
        "ui_id": ui_id,
        "executable_relative_path": "bin/game.exe",
        "patches": patches,
    }))
    return {game_id: path}


LAA_PATCH = {
    "id": "flip_laa_flag", "label": "4GB Patch", "type": "binary_patch",
    "operation": "flip_laa_flag", "default_enabled": True, "ui_job_id": "laa",
}
DXVK_PATCH = {
    "id": "install_dxvk", "label": "DXVK", "type": "file_install",
    "github_repo": "doitsujin/dxvk", "install_relative_path": "bin",
    "default_enabled": False, "ui_job_id": "lrfix",
}


def fake_install(tmp_path, app_id="292120"):
    """a library steam would actually recognise: acf manifest plus the folder it names"""
    library = tmp_path / "steam"
    install = library / "steamapps" / "common" / "FF13"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "game.exe").write_bytes(make_fake_pe())
    (library / "steamapps" / f"appmanifest_{app_id}.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"installdir"\t\t"FF13"\n}\n' % app_id
    )
    return install


def bridge_for(tmp_path, patches, install, fetch=None, monkeypatch=None):
    paths = write_manifest(tmp_path, patches)
    # point persistent state at tmp so tests never touch a real .state folder
    import patcher.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "state_path_for",
                        lambda gid: tmp_path / ".state" / f"{gid}.json")
    return UiBridge(manifest_paths=paths, fetch=fetch,
                    steam_library_paths=[install.parent.parent.parent])


def test_scan_reports_real_path_and_supported_jobs(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)

    result = b.scan("ff13")

    assert result["found"] is True
    assert result["path"] == str(install)
    assert result["source"] == "steam"
    assert result["supported"] == ["laa"]
    assert result["applied"] == []
    # everything crossing the bridge has to survive a json round trip
    json.dumps(result)


def test_scan_says_which_required_rows_a_launch_refusal_keeps_off(tmp_path, monkeypatch):
    """the Launch row doesn't wait on a patch Apply won't put on, which hid LR's for good"""
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    monkeypatch.setattr("patcher.engine.held_off",
                        lambda install_dir, manifest: {"flip_laa_flag": "kept off"})
    assert b.scan("ff13")["held"] == {"laa": "kept off"}
    monkeypatch.setattr("patcher.engine.held_off", lambda install_dir, manifest: {})
    assert b.scan("ff13")["held"] == {}


def test_scan_reports_not_found_without_raising(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    b._steam_library_paths = [tmp_path / "nowhere"]

    result = b.scan("ff13")

    assert result["ok"] is True
    assert result["found"] is False
    assert result["path"] is None
    assert "couldn't automatically find" in result["error"]


def test_apply_emits_run_then_done_and_patches_the_file(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    events = []

    outcome = b.apply("ff13", ["laa"], on_event=events.append)

    assert outcome["ok"] is True
    assert [(e.get("jobId"), e.get("state")) for e in events if e["type"] == "job"] == [
        ("laa", "run"), ("laa", "done"),
    ]
    done = [e for e in events if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.APPLIED
    assert done["ok"] is True
    assert events[-1] == {"type": "finished", "ok": True, "applied": 1, "failed": 0, "error": None}

    from patcher.patch_ops import is_laa_enabled
    assert is_laa_enabled((install / "bin" / "game.exe").read_bytes())


def test_second_apply_reports_already_applied(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    b.apply("ff13", ["laa"])

    events = []
    b.apply("ff13", ["laa"], on_event=events.append)

    done = [e for e in events if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.ALREADY_APPLIED
    assert done["ok"] is True


def test_unsupported_job_is_reported_not_silently_succeeded(tmp_path, monkeypatch):
    """the whole point: a UI job with no patch behind it must not read as done"""
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    events = []

    outcome = b.apply("ff13", ["audiofix"], on_event=events.append)

    done = [e for e in events if e.get("state") == "done"][0]
    assert done["jobId"] == "audiofix"
    assert done["status"] == UNSUPPORTED
    assert done["ok"] is False
    assert outcome["ok"] is False


def test_job_not_selected_is_never_run(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    events = []

    b.apply("ff13", [], on_event=events.append)

    assert [e for e in events if e["type"] == "job"] == []
    from patcher.patch_ops import is_laa_enabled
    assert not is_laa_enabled((install / "bin" / "game.exe").read_bytes())


def test_default_disabled_patch_still_runs_when_the_user_asks(tmp_path, monkeypatch):
    """LR's DXVK patch is default_enabled=false, the UI must still be able to run it"""
    install = fake_install(tmp_path)
    release = json.dumps({"assets": [
        {"name": "dxvk.zip", "browser_download_url": "https://example.test/dxvk.zip"}
    ]}).encode()
    payload = make_zip_bytes({"x32/d3d9.dll": b"32bit"})

    def fetch(url):
        return release if url.endswith("releases/latest") else payload

    b = bridge_for(tmp_path, [DXVK_PATCH], install, fetch=fetch, monkeypatch=monkeypatch)
    events = []
    outcome = b.apply("ff13", ["lrfix"], on_event=events.append)

    done = [e for e in events if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.APPLIED, done
    assert outcome["ok"] is True


def test_apply_reports_failure_instead_of_raising(tmp_path, monkeypatch):
    """a patch whose own target is missing fails alone, the install stays valid"""
    install = fake_install(tmp_path)
    missing_target = dict(LAA_PATCH, target_relative_path="bin/not_here.exe")
    b = bridge_for(tmp_path, [missing_target], install, monkeypatch=monkeypatch)

    events = []
    outcome = b.apply("ff13", ["laa"], on_event=events.append)

    done = [e for e in events if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.FAILED
    assert done["ok"] is False
    assert outcome["ok"] is False
    assert events[-1]["failed"] == 1


def test_dry_run_touches_nothing(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    before = (install / "bin" / "game.exe").read_bytes()
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)

    events = []
    b.apply("ff13", ["laa"], on_event=events.append, dry_run=True)

    done = [e for e in events if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.WOULD_APPLY
    assert done["ok"] is True
    assert (install / "bin" / "game.exe").read_bytes() == before


def test_unknown_game_does_not_raise(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)

    assert b.scan("nope")["ok"] is False
    assert b.apply("nope", ["laa"])["ok"] is False


def test_install_missing_entirely_finishes_cleanly(tmp_path, monkeypatch):
    """no game on disk is a finished-with-error, not an exception into the webview"""
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    b._steam_library_paths = [tmp_path / "nowhere"]

    events = []
    outcome = b.apply("ff13", ["laa"], on_event=events.append)

    assert outcome["ok"] is False
    assert events[-1]["type"] == "finished"
    assert events[-1]["ok"] is False
    assert events[-1]["error"]


UI_HTML = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def _events(bridge, game, jobs):
    collected = []
    bridge.apply(game, jobs, on_event=collected.append)
    return collected


def _chip_map():
    """pulls CHIP_FOR out of index.html so the two sides cannot drift apart"""
    import re
    block = re.search(r"const CHIP_FOR = \{(.*?)\};", UI_HTML.read_text(encoding="utf-8"), re.S).group(1)
    # [^"]+ rather than \w+, which quietly dropped every word with a space in
    # it. "SIGN IN" and "DROP IT" were both mapped and both invisible here, so
    # this read as two statuses the UI had no word for.
    return dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block))


def test_ui_renders_every_status_the_engine_can_emit():
    """
    a status with no chip word falls through to `ok ? DONE : SKIPPED`, and the
    user is told something that is not true. this listed six of them and let
    the other seven through, which is how `repaired` came to render as SKIPPED:
    the guard had just undone a 4GB patch after a launch Steam refused, and the
    row reported it as work the tool declined to do.

    Read off the class rather than spelled out, so a new PatchStatus fails here
    the day it lands instead of the day somebody sees the wrong word.
    """
    chips = _chip_map()
    engine_statuses = {value for name, value in vars(PatchStatus).items()
                       if not name.startswith("_") and isinstance(value, str)}
    engine_statuses.add(UNSUPPORTED)
    assert engine_statuses <= set(chips), f"UI has no chip for {sorted(engine_statuses - set(chips))}"


def test_every_event_field_the_ui_reads_is_present(tmp_path, monkeypatch):
    """index.html reads ev.type/jobId/state/ok/status/detail and finished.ok/error"""
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    events = []
    b.apply("ff13", ["laa", "audiofix"], on_event=events.append)

    for e in events:
        json.dumps(e)
        if e["type"] == "job":
            assert set(e) >= {"type", "jobId", "state"}
            if e["state"] == "done":
                assert set(e) >= {"ok", "status", "detail"}
        else:
            assert e["type"] == "finished"
            assert set(e) >= {"ok", "error"}


def test_ui_manifest_job_ids_resolve_against_the_real_manifests():
    """
    the real shipped data, not a fixture. every ui_job_id a manifest claims
    must exist in ui/jobs.json, or the bridge maps a patch onto a row that is
    not on screen and it silently never runs. But for a retired one, whose
    row is gone on purpose and which never runs anyway
    """
    root = UI_HTML.parent.parent
    jobs = json.loads((root / "ui" / "jobs.json").read_text(encoding="utf-8"))
    by_game = {g["id"]: {j["id"] for j in g["jobs"]} for g in jobs["games"]}

    from patcher.manifest import load_manifest
    for path in sorted((root / "manifests").glob("*.json")):
        manifest = load_manifest(path)
        assert manifest.ui_id in by_game, f"{path.name} points at unknown ui_id {manifest.ui_id}"
        for patch in manifest.patches:
            if patch.retired:
                assert patch.ui_job_id not in by_game[manifest.ui_id], patch.id
            elif patch.ui_job_id:
                assert patch.ui_job_id in by_game[manifest.ui_id], (
                    f"{path.name}: patch {patch.id} claims ui_job_id "
                    f"{patch.ui_job_id!r}, not in ui/jobs.json for {manifest.ui_id}"
                )


def test_deleting_installed_files_makes_the_patch_apply_again(tmp_path, monkeypatch):
    """
    the reported bug: delete d3d9.dll and FF13Fix.ini from the game folder and
    the next run still said already_applied, because file_install status was
    read from the json record and never from disk.
    """
    install = fake_install(tmp_path)
    release = json.dumps({"assets": [
        {"name": "ff13fix.zip", "browser_download_url": "https://example.test/ff13fix.zip"}
    ]}).encode()
    payload = make_zip_bytes({"FF13Fix_164/d3d9.dll": b"dll", "FF13Fix_164/FF13Fix.ini": b"ini"})

    def fetch(url):
        return release if url.endswith("releases/latest") else payload

    patch = {
        "id": "install_ff13fix", "label": "FFXIII Fix", "type": "file_install",
        "github_repo": "rebtd7/FF13Fix", "install_relative_path": "bin",
        "default_enabled": True, "ui_job_id": "ff13fix",
    }
    b = bridge_for(tmp_path, [patch], install, fetch=fetch, monkeypatch=monkeypatch)

    first = [e for e in _events(b, "ff13", ["ff13fix"]) if e.get("state") == "done"][0]
    assert first["status"] == PatchStatus.APPLIED
    assert (install / "bin" / "d3d9.dll").exists()

    # unchanged folder: correctly skipped
    second = [e for e in _events(b, "ff13", ["ff13fix"]) if e.get("state") == "done"][0]
    assert second["status"] == PatchStatus.ALREADY_APPLIED

    # now the user cleans the folder out by hand
    (install / "bin" / "d3d9.dll").unlink()
    (install / "bin" / "FF13Fix.ini").unlink()

    third = [e for e in _events(b, "ff13", ["ff13fix"]) if e.get("state") == "done"][0]
    assert third["status"] == PatchStatus.APPLIED, "deleted files must reinstall, not report already_applied"
    assert (install / "bin" / "d3d9.dll").exists()


def test_one_missing_file_is_enough_to_reinstall(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    release = json.dumps({"assets": [
        {"name": "f.zip", "browser_download_url": "https://example.test/f.zip"}]}).encode()
    payload = make_zip_bytes({"w/d3d9.dll": b"dll", "w/FF13Fix.ini": b"ini"})
    patch = {
        "id": "install_ff13fix", "label": "FFXIII Fix", "type": "file_install",
        "github_repo": "rebtd7/FF13Fix", "install_relative_path": "bin",
        "default_enabled": True, "ui_job_id": "ff13fix",
    }
    b = bridge_for(tmp_path, [patch], install,
                   fetch=lambda u: release if u.endswith("releases/latest") else payload,
                   monkeypatch=monkeypatch)
    _events(b, "ff13", ["ff13fix"])

    # only the ini goes missing, the dll is still there
    (install / "bin" / "FF13Fix.ini").unlink()

    again = [e for e in _events(b, "ff13", ["ff13fix"]) if e.get("state") == "done"][0]
    assert again["status"] == PatchStatus.APPLIED
    assert (install / "bin" / "FF13Fix.ini").exists()


def test_already_applied_no_longer_claims_disk_verification(tmp_path, monkeypatch):
    """the engine cannot know how the caller decided, so it must not say so"""
    install = fake_install(tmp_path)
    b = bridge_for(tmp_path, [LAA_PATCH], install, monkeypatch=monkeypatch)
    b.apply("ff13", ["laa"])
    done = [e for e in _events(b, "ff13", ["laa"]) if e.get("state") == "done"][0]
    assert done["status"] == PatchStatus.ALREADY_APPLIED
    assert "verified on disk" not in done["detail"]


def test_the_ui_and_the_manifests_agree_on_what_runs_by_default():
    """
    Two files decide this and only one of them is on screen. ui/jobs.json
    drives the ticks in the window; manifest default_enabled drives a headless
    run. XIII-2's audio fix had them disagreeing, so the app left it unticked
    while `python main.py ffxiii2` applied it, and "the defaults" meant two
    different installs depending on how you started the tool.

    required runs unconditionally, extra is never in a run at all, and optional
    is the only kind where a default is a real choice.
    """
    root = UI_HTML.parent.parent
    jobs = json.loads((root / "ui" / "jobs.json").read_text(encoding="utf-8"))
    rows = {(g["id"], j["id"]): j for g in jobs["games"] for j in g["jobs"]}

    from patcher.manifest import load_manifest
    for path in sorted((root / "manifests").glob("*.json")):
        manifest = load_manifest(path)
        for patch in manifest.patches:
            row = rows.get((manifest.ui_id, patch.ui_job_id)) if patch.ui_job_id else None
            if row is None:
                continue
            kind = row.get("kind", "required")
            wanted = bool(row.get("default")) if kind == "optional" else kind == "required"
            assert patch.default_enabled == wanted, (
                f"{path.name}: {patch.id} is default_enabled={patch.default_enabled} "
                f"but ui/jobs.json has it as kind={kind!r} default={row.get('default')!r}"
            )


def _stylesheet() -> str:
    """
    every <style> block, joined.

    index.html has five of them, one per drawer, and slicing to the first
    </style> quietly stopped at line 681 with the tool tabs and the Start game
    button still ahead of it. A check that cannot see the rule it is about
    passes for the wrong reason.
    """
    import re

    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>",
                                UI_HTML.read_text(encoding="utf-8"), re.S))


def test_structural_greys_come_from_one_ramp():
    """
    Every line, edge and empty track beside a patch row takes its colour from
    .row .lit, through --edge and --edge-dim.

    They had drifted into four treatments of the same thing: the tool tabs on
    #b7bbc0, the PATCH QUEUE rule on #3f4142, the Apply and Start game rules on
    rgba(255,255,255,.22), and the empty ATB segment opening at #6c6f74. Three
    of those sat within a few luma of a stop on that ramp without being on it,
    which is the shape of a colour someone matched by eye once and then never
    again.

    Checking the rules rather than the values, because the point is where the
    colour comes from. Change the ramp and everything downstream should move
    with it.
    """
    import re

    css = _stylesheet()

    for token in ("--edge:", "--edge-dim:"):
        assert token in css, f"{token} is gone, so nothing has a source any more"

    # selector -> the declaration that must be derived
    wired = {
        ".tool-tab": "border-bottom:2px solid var(--edge)",
        "#go": "border-top:1px solid var(--edge)",
        "#launch": "border-top:1px solid var(--edge)",
        ".qrow-label": "border-top:1px solid var(--edge-dim)",
    }
    for selector, declaration in wired.items():
        block = re.search(re.escape(selector) + r"\{(.*?)\}", css, re.S)
        assert block, f"{selector} is gone from the stylesheet"
        assert declaration in block.group(1).replace("\n", "").replace("  ", ""), (
            f"{selector} no longer takes its edge from the ramp: {declaration}")


def test_gold_stays_state_and_never_becomes_a_surface():
    """
    The rule the palette runs on: gold means something is happening (progress
    fill, focus ring, the checkbox tick, a drawer's confirm), grey means a
    surface. A gold structural edge reads as state to anyone scanning the
    window, which is exactly what the ATB gauge underneath it is for.
    """
    import re

    css = _stylesheet()

    # the panels' own furniture counts too. a drawer used to open with a gold
    # rail, three gold headings and a gold confirm button before the user had
    # touched anything, which is a lot of "something is happening" for a list
    # sitting still.
    chrome = (".tool-tab", "#go", "#launch", ".qrow-label",
              "#nv", "#md", "#nv .grp", "#nv footer button.go",
              "#aboutPanel,#creditsPanel", "#mdZone")
    for selector in chrome:
        block = re.search(re.escape(selector) + r"\{(.*?)\}", css, re.S)
        assert block, f"{selector} is gone from the stylesheet"
        assert "--gold" not in block.group(1), (
            f"{selector} went gold. gold is state in this palette, and this is "
            "a surface")

    # and the other half of the rule: the things that DO mean something is
    # happening must not quietly go grey either, or nothing on screen reads as
    # live. these are the ones worth naming.
    for selector, why in ((".box::after", "a ticked checkbox"),
                          ("#nv select:focus,#nv input[type=number]:focus", "focus"),
                          ("#mdZone.hot", "a file held over the drop zone")):
        block = re.search(re.escape(selector) + r"\{(.*?)\}", css, re.S)
        assert block, f"{selector} is gone from the stylesheet"
        assert "--gold" in block.group(1), (
            f"{selector} lost its gold, and {why} is exactly what gold is for")
