import json
from pathlib import Path

import pytest

from patcher.engine import PatchStatus, run_patches
from patcher.manifest import load_manifest
from patcher.mods.nexus_api import Account, needs_website_click, validate_api_key
from patcher.mods.nexus_source import NEEDS_ACCOUNT, NEEDS_FILE, NEEDS_WEBSITE, READY, NexusSource
from tests.helpers import make_zip_bytes

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "manifests"


def fake_validate(is_premium, name="tester"):
    def fetch(url, headers):
        assert headers["apikey"] == "key-123"
        return json.dumps({"user_id": 7, "name": name, "is_premium?": is_premium}).encode()
    return fetch


def test_validate_reads_the_ruby_style_premium_flag():
    account = validate_api_key("key-123", fetch=fake_validate(True))
    assert account.valid
    assert account.is_premium
    assert account.name == "tester"


def test_validate_also_accepts_the_plain_premium_key():
    def fetch(url, headers):
        return json.dumps({"user_id": 7, "is_premium": True}).encode()
    assert validate_api_key("key-123", fetch=fetch).is_premium


def test_a_rejected_key_is_reported_not_raised():
    def fetch(url, headers):
        raise OSError("401 Unauthorized")
    account = validate_api_key("bad", fetch=fetch)
    assert not account.valid
    assert account.error


def test_premium_never_needs_the_website_and_free_always_does():
    assert needs_website_click(None, is_premium=True) is False
    assert needs_website_click(None, is_premium=False) is True


def source_with(tmp_path, api_key, fetch):
    config = tmp_path / "nexus_config.json"
    if api_key:
        config.write_text(json.dumps({"nexus_api_key": api_key}))
    return NexusSource(config, fetch=fetch)


def test_premium_resolves_a_download_with_no_user_interaction(tmp_path):
    def fetch(url, headers):
        if "validate" in url:
            return json.dumps({"user_id": 1, "is_premium?": True}).encode()
        if "files.json" in url:
            return json.dumps({"files": [
                {"file_id": 10, "category_name": "MAIN", "uploaded_timestamp": 100},
                {"file_id": 11, "category_name": "MAIN", "uploaded_timestamp": 300},
            ]}).encode()
        return json.dumps([{"URI": "https://cdn.test/pack.zip"}]).encode()

    result = source_with(tmp_path, "key-123", fetch).resolve("finalfantasy13", "21")

    assert result.status == READY
    assert result.download_url == "https://cdn.test/pack.zip"


def test_latest_main_file_wins_not_the_first_listed(tmp_path):
    seen = {}

    def fetch(url, headers):
        if "validate" in url:
            return json.dumps({"user_id": 1, "is_premium?": True}).encode()
        if "files.json" in url:
            return json.dumps({"files": [
                {"file_id": 10, "category_name": "MAIN", "uploaded_timestamp": 100},
                {"file_id": 99, "category_name": "MAIN", "uploaded_timestamp": 900},
            ]}).encode()
        seen["url"] = url
        return json.dumps([{"URI": "https://cdn.test/x.zip"}]).encode()

    source_with(tmp_path, "key-123", fetch).resolve("finalfantasy13", "21")
    assert "/files/99/" in seen["url"]


def test_no_key_at_all_still_works_and_touches_no_network(tmp_path):
    """
    the whole point of the change: with zero setup, zero credentials and no
    network call, a Nexus mod is still installable.
    """
    def explode(url, headers):
        raise AssertionError("must not hit the network with no key stored")

    result = source_with(tmp_path, None, explode).resolve("finalfantasy13", "21")

    assert result.status == NEEDS_FILE
    assert result.page_url.endswith("?tab=files")


def test_a_premium_key_still_shortcuts_to_a_silent_download(tmp_path):
    """the api route is kept as an accelerator, just never as a requirement"""
    def fetch(url, headers):
        if "validate" in url:
            return json.dumps({"user_id": 1, "is_premium?": True}).encode()
        if "files.json" in url:
            return json.dumps({"files": [{"file_id": 7, "category_name": "MAIN",
                                          "uploaded_timestamp": 1}]}).encode()
        return json.dumps([{"URI": "https://cdn.test/a.zip"}]).encode()

    result = source_with(tmp_path, "key-123", fetch).resolve("finalfantasy13", "21")
    assert result.status == READY


def test_the_account_tier_is_checked_once_per_run(tmp_path):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        return json.dumps({"user_id": 1, "is_premium?": False}).encode()

    source = source_with(tmp_path, "key-123", fetch)
    for _ in range(4):
        source.resolve("finalfantasy13", "21")

    validate_calls = [u for u in calls if "validate" in u]
    assert len(validate_calls) == 1, "the api quota is 300/hour, do not spend it re-validating"


class StubSource:
    def __init__(self, status, url=None):
        self._status = status
        self._url = url

    def resolve(self, domain, mod_id, file_id=None):
        from patcher.mods.nexus_source import Resolution
        return Resolution(self._status, download_url=self._url,
                          page_url=f"https://www.nexusmods.com/{domain}/mods/{mod_id}",
                          detail="stub")


def build_manifest(tmp_path, patches):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "game_id": "ffxiii", "display_name": "FFXIII", "manifest_version": "v1",
        "ui_id": "ff13", "executable_relative_path": "game.exe", "patches": patches,
    }))
    (tmp_path / "game.exe").write_bytes(b"exe")
    return load_manifest(path)


NEXUS_PATCH = {
    "id": "audio", "label": "Audio Fix", "type": "nexus",
    "nexus_game_domain": "finalfantasy13", "nexus_mod_id": "21",
    "install_relative_path": "movie", "default_enabled": True, "ui_job_id": "audiofix",
}


def test_nexus_patch_installs_for_premium(tmp_path):
    manifest = build_manifest(tmp_path, [NEXUS_PATCH])
    payload = make_zip_bytes({"pack/audio.bin": b"audio"})

    results = run_patches(tmp_path, manifest, {"audio"},
                          fetch=lambda url: payload,
                          nexus_source=StubSource(READY, "https://cdn.test/a.zip"))

    assert results[0].status == PatchStatus.APPLIED
    assert (tmp_path / "movie" / "audio.bin").read_bytes() == b"audio"


def test_nexus_patch_reports_needs_website_with_the_page(tmp_path):
    manifest = build_manifest(tmp_path, [NEXUS_PATCH])

    results = run_patches(tmp_path, manifest, {"audio"},
                          nexus_source=StubSource(NEEDS_WEBSITE))

    assert results[0].status == PatchStatus.NEEDS_WEBSITE
    assert results[0].page_url.endswith("/finalfantasy13/mods/21")


def test_an_installer_mod_is_staged_never_launched(tmp_path):
    """multi-gb interactive installers do not get run unattended"""
    patch = dict(NEXUS_PATCH, id="hd", ui_job_id="hdproject", runs_installer=True)
    manifest = build_manifest(tmp_path, [patch])

    results = run_patches(tmp_path, manifest, {"hd"},
                          fetch=lambda url: b"installer bytes",
                          nexus_source=StubSource(READY, "https://cdn.test/hd.exe"))

    assert results[0].status == PatchStatus.NEEDS_INSTALLER
    assert "run its installer" in results[0].detail


def test_a_dependency_blocks_until_its_prerequisite_is_applied(tmp_path):
    """HD Models Plus reuses files The HD Project extracts, order is not optional"""
    first = dict(NEXUS_PATCH, id="hdproject", ui_job_id="hdproject", nexus_mod_id="17")
    second = dict(NEXUS_PATCH, id="hdmodels", ui_job_id="hdmodelsplus",
                  nexus_mod_id="11", requires=["hdproject"])
    manifest = build_manifest(tmp_path, [first, second])

    # only the dependent one selected, prerequisite not applied
    results = run_patches(tmp_path, manifest, {"hdmodels"},
                          nexus_source=StubSource(READY, "https://cdn.test/x.zip"))
    blocked = [r for r in results if r.patch_id == "hdmodels"][0]
    assert blocked.status == PatchStatus.BLOCKED
    # the prerequisite's LABEL, never its internal id. the shipped build put
    # "needs restore_console_content installed first" in the hint bar.
    assert "Audio Fix" in blocked.detail
    assert "hdproject" not in blocked.detail


def test_a_dependency_satisfied_earlier_in_the_same_run_does_not_block(tmp_path):
    first = dict(NEXUS_PATCH, id="hdproject", ui_job_id="hdproject", nexus_mod_id="17")
    second = dict(NEXUS_PATCH, id="hdmodels", ui_job_id="hdmodelsplus",
                  nexus_mod_id="11", requires=["hdproject"])
    manifest = build_manifest(tmp_path, [first, second])
    payload = make_zip_bytes({"a/one.bin": b"1"})

    results = run_patches(tmp_path, manifest, {"hdproject", "hdmodels"},
                          fetch=lambda url: payload,
                          nexus_source=StubSource(READY, "https://cdn.test/x.zip"))

    assert [r.status for r in results] == [PatchStatus.APPLIED, PatchStatus.APPLIED]


def test_a_dependency_already_on_disk_does_not_block(tmp_path):
    first = dict(NEXUS_PATCH, id="hdproject", ui_job_id="hdproject", nexus_mod_id="17")
    second = dict(NEXUS_PATCH, id="hdmodels", ui_job_id="hdmodelsplus",
                  nexus_mod_id="11", requires=["hdproject"])
    manifest = build_manifest(tmp_path, [first, second])
    payload = make_zip_bytes({"a/one.bin": b"1"})

    results = run_patches(tmp_path, manifest, {"hdmodels"},
                          already_applied_check=lambda pid: pid == "hdproject",
                          fetch=lambda url: payload,
                          nexus_source=StubSource(READY, "https://cdn.test/x.zip"))

    assert [r for r in results if r.patch_id == "hdmodels"][0].status == PatchStatus.APPLIED


def test_manual_patches_report_where_to_get_them(tmp_path):
    patch = {"id": "tears", "label": "Leviathan's Tears", "type": "manual",
             "manual_url": "https://example.test/tears", "default_enabled": False,
             "ui_job_id": "leviathanstears", "notes": "fixes the rain"}
    manifest = build_manifest(tmp_path, [patch])

    results = run_patches(tmp_path, manifest, {"tears"})

    assert results[0].status == PatchStatus.MANUAL
    assert results[0].page_url == "https://example.test/tears"


def test_shipped_manifests_have_no_dangling_dependencies():
    """a requires pointing at an id that does not exist blocks forever"""
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = load_manifest(path)
        ids = {p.id for p in manifest.patches}
        order = [p.id for p in manifest.patches]
        for patch in manifest.patches:
            for required in patch.requires:
                assert required in ids, f"{path.name}: {patch.id} requires unknown {required}"
                # manifest order is execution order, so a prerequisite listed
                # after its dependent could never be satisfied in one run
                assert order.index(required) < order.index(patch.id), (
                    f"{path.name}: {required} must be listed before {patch.id}")


def test_every_shipped_nexus_patch_has_a_mod_id():
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        for patch in load_manifest(path).patches:
            if patch.type == "nexus":
                assert patch.nexus_game_domain and patch.nexus_mod_id, patch.id


def test_a_manual_patch_chain_gives_an_order_not_a_dead_end(tmp_path):
    """
    both of XIII-2's content patches are manual, so a hard BLOCK on the second
    could never be cleared: nothing we do will ever mark the first applied.
    it has to tell the user the order instead.
    """
    first = {"id": "console", "label": "DLC Restoration", "type": "manual",
             "manual_url": "https://example.test/dlc", "default_enabled": False,
             "ui_job_id": "dlcrestore"}
    second = {"id": "tears", "label": "Leviathan's Tears", "type": "manual",
              "manual_url": "https://example.test/tears", "default_enabled": False,
              "requires": ["console"], "ui_job_id": "leviathanstears",
              "notes": "fixes the rain"}
    manifest = build_manifest(tmp_path, [first, second])

    results = run_patches(tmp_path, manifest, {"console", "tears"})

    tears = [r for r in results if r.patch_id == "tears"][0]
    assert tears.status == PatchStatus.MANUAL, "a manual chain must not dead-end on BLOCKED"
    assert "DLC Restoration" in tears.detail
    assert "console" not in tears.detail


def test_no_shipped_message_leaks_an_internal_patch_id(tmp_path):
    """whatever reaches the hint bar has to be readable by a player"""
    from patcher.manifest import load_manifest
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = load_manifest(path)
        ids = {p.id for p in manifest.patches}
        results = run_patches(tmp_path, manifest, ids, nexus_source=StubSource(NEEDS_WEBSITE))
        for r in results:
            for patch_id in ids:
                assert patch_id not in (r.detail or ""), (
                    f"{path.name}: {r.patch_id} leaks the id {patch_id!r} into '{r.detail}'")


def test_a_mirror_needs_no_nexus_account_at_all(tmp_path):
    """the whole point: a mirrored mod behaves identically for every user"""
    patch = dict(NEXUS_PATCH, mirror_url="https://mirror.test/audio.zip")
    manifest = build_manifest(tmp_path, [patch])
    payload = make_zip_bytes({"pack/audio.bin": b"audio"})

    def explode(*a, **k):
        raise AssertionError("a mirrored patch must never call Nexus")

    class NoNexus:
        resolve = explode

    results = run_patches(tmp_path, manifest, {"audio"},
                          fetch=lambda url: payload, nexus_source=NoNexus())

    assert results[0].status == PatchStatus.APPLIED
    assert "mirror" in results[0].detail
    assert (tmp_path / "movie" / "audio.bin").read_bytes() == b"audio"


def test_a_tampered_mirror_fails_instead_of_installing(tmp_path):
    patch = dict(NEXUS_PATCH, mirror_url="https://mirror.test/audio.zip",
                 mirror_sha256="0" * 64)
    manifest = build_manifest(tmp_path, [patch])
    payload = make_zip_bytes({"pack/audio.bin": b"audio"})

    results = run_patches(tmp_path, manifest, {"audio"},
                          fetch=lambda url: payload, nexus_source=None)

    assert results[0].status == PatchStatus.FAILED
    assert "checksum" in results[0].detail
    assert not (tmp_path / "movie").exists()


UI_JOBS = json.loads((MANIFEST_DIR.parent / "ui" / "jobs.json").read_text(encoding="utf-8"))


def ui_job_kinds():
    kinds = {}
    for game in UI_JOBS["games"]:
        for job in game["jobs"]:
            kinds[(game["id"], job["id"])] = job.get("kind", "required")
    return kinds


# jobs the UI presents as REQUIRED that cannot actually be completed without
# either a Nexus account or a trip outside the app. every entry here is a row
# that can show SIGN IN, WEBSITE or MANUAL in the "Patches to be applied"
# list, which reads as the patcher being broken rather than the mod being
# hosted somewhere awkward.
#
# the fix for each is a mirror_url agreed with the mod author, or demoting the
# job to Extras. this list exists so the number can only go down.
KNOWN_GATED_REQUIRED = set()


def test_no_new_required_job_depends_on_a_nexus_account():
    kinds = ui_job_kinds()
    gated = set()

    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = load_manifest(path)
        for patch in manifest.patches:
            if not patch.ui_job_id:
                continue
            key = (manifest.ui_id, patch.ui_job_id)
            if kinds.get(key) != "required":
                continue
            # a mirror makes a nexus patch behave like any other download
            needs_outside_help = (
                (patch.type == "nexus" and not patch.mirror_url) or patch.type == "manual"
            )
            if needs_outside_help:
                gated.add(key)

    new = gated - KNOWN_GATED_REQUIRED
    assert not new, (
        f"these required jobs now need a Nexus account or a manual download: {sorted(new)}. "
        "either give the patch a mirror_url the author has agreed to, or move the job to Extras."
    )

    fixed = KNOWN_GATED_REQUIRED - gated
    assert not fixed, (
        f"{sorted(fixed)} no longer need outside help, remove them from KNOWN_GATED_REQUIRED"
    )


def test_an_optional_prerequisite_never_hard_blocks_a_required_job():
    """
    a required row blocked by something sitting in Extras can never clear
    itself, because the user is not obliged to tick the thing it needs.
    """
    kinds = ui_job_kinds()
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = load_manifest(path)
        by_id = {p.id: p for p in manifest.patches}
        for patch in manifest.patches:
            if kinds.get((manifest.ui_id, patch.ui_job_id)) != "required":
                continue
            for required_id in patch.requires:
                prerequisite = by_id[required_id]
                # manual prerequisites report an ordering hint rather than a
                # hard block, which is why they are allowed here
                if prerequisite.type == "manual":
                    continue
                assert kinds.get((manifest.ui_id, prerequisite.ui_job_id)) == "required", (
                    f"{path.name}: required job {patch.ui_job_id!r} hard-blocks on "
                    f"{prerequisite.ui_job_id!r}, which the UI lists as optional"
                )


def test_leviathans_tears_is_kept_as_an_injection_to_take_off():
    """
    retired, so there's nothing to download or drop for it, but an earlier
    version's install is still in the packed archives, both voice sets, and
    this entry is what finds it there and takes it off
    """
    manifest = load_manifest(MANIFEST_DIR / "ffxiii2.json")
    patch = next(p for p in manifest.patches if p.ui_job_id == "leviathanstears")

    assert patch.type == "archive_inject" and patch.retired and patch.in_mods
    assert patch.download_url is None
    assert patch.fallback_page_url is None
    assert patch.archive_relative_dir == "alba_data/sys"
    assert patch.archive_variants == ["u", "c"], "both languages or it only half comes off"
    assert len(patch.injections) == 2
    assert all(spec.get("sha256") for spec in patch.injections), "the pins are how it's found"
    assert not patch.requires


def test_a_direct_download_installs_with_no_account(tmp_path):
    patch = {"id": "tears", "label": "Leviathan's Tears", "type": "direct",
             "download_url": "https://example.test/tears.zip",
             "fallback_page_url": "https://example.test/page",
             "install_relative_path": "alba_data", "default_enabled": True,
             "ui_job_id": "leviathanstears"}
    manifest = build_manifest(tmp_path, [patch])
    payload = make_zip_bytes({"Leviathans/rain.imgb": b"rain"})

    results = run_patches(tmp_path, manifest, {"tears"}, fetch=lambda url: payload)

    assert results[0].status == PatchStatus.APPLIED
    assert (tmp_path / "alba_data" / "rain.imgb").read_bytes() == b"rain"


def test_a_confirm_page_is_not_installed_as_a_mod(tmp_path):
    """
    community file hosts answer an unauthenticated download with HTML and a
    200. writing that into alba_data and reporting success is the worst
    possible outcome.
    """
    patch = {"id": "tears", "label": "Leviathan's Tears", "type": "direct",
             "download_url": "https://example.test/tears?do=download",
             "fallback_page_url": "https://example.test/page",
             "install_relative_path": "alba_data", "default_enabled": True,
             "ui_job_id": "leviathanstears"}
    manifest = build_manifest(tmp_path, [patch])

    results = run_patches(tmp_path, manifest, {"tears"},
                          fetch=lambda url: b"<!DOCTYPE html><html><title>Confirm</title>")

    assert results[0].status == PatchStatus.NEEDS_WEBSITE
    assert results[0].page_url == "https://example.test/page"
    assert not (tmp_path / "alba_data").exists()


def test_a_url_with_no_filename_is_sniffed_from_its_bytes(tmp_path):
    """"?do=download" carries no extension, so the archive type comes from magic"""
    patch = {"id": "tears", "label": "Tears", "type": "direct",
             "download_url": "https://example.test/files/file/2354-x/?do=download",
             "install_relative_path": ".", "default_enabled": True, "ui_job_id": "leviathanstears"}
    manifest = build_manifest(tmp_path, [patch])
    payload = make_zip_bytes({"w/rain.imgb": b"rain"})

    results = run_patches(tmp_path, manifest, {"tears"}, fetch=lambda url: payload)

    assert results[0].status == PatchStatus.APPLIED
    assert (tmp_path / "rain.imgb").read_bytes() == b"rain"


def read_ui():
    return (MANIFEST_DIR.parent / "ui" / "index.html").read_text(encoding="utf-8")


def test_apply_never_opens_a_browser_or_asks_for_a_key_on_its_own():
    """
    pressing Apply must not produce a website or a native credential prompt.
    it may open the in-app sign-in window, which explains itself and can be
    dismissed; acting on it is always a button press by the user.
    """
    ui = read_ui()
    run_body = ui[ui.index("async function run(){"):ui.index("go.addEventListener")]
    assert "window.prompt" not in run_body
    assert "nexus_open_key_page" not in run_body
    assert "nexus_sign_in(" not in run_body
    # the window is consulted, never acted on
    # the sign-in gate is gone entirely: a Nexus mod is obtained by opening
    # its page and catching the download, which needs no account from us
    assert "Auth.needed" not in run_body
    assert "Auth.open" not in run_body


def test_the_sign_in_window_explains_itself_and_can_be_dismissed():
    ui = read_ui()
    # built in JS and appended, so the authored body markup stays untouched
    assert 'el.id = "auth"' in ui
    assert 'id="authGo"' in ui and 'id="authSkip"' in ui
    assert "Not now" in ui, "a dialog the user did not ask for needs a way out"
    assert "Escape" in ui, "escape has to close it"
    assert "never sees your password" in ui
    # the free personal-key route has to be guided, not a bare ask
    for step in ("API Keys", "Personal API Key", "Generate"):
        assert step in ui, f"guided key entry is missing the {step!r} step"


def test_the_original_stylesheet_is_untouched():
    """the window ships as its own block so the hand-built UI stays byte-exact"""
    import re
    ui = read_ui()
    blocks = re.findall(r"<style[^>]*>", ui)
    # every added block carries an id; the original stays unnamed and untouched
    assert "id=" not in blocks[0], "the original block must stay unnamed and unedited"
    assert all("id=" in b for b in blocks[1:]), "added blocks must be identifiable"
    joined = " ".join(blocks[1:])
    assert 'id="auth-style"' in joined and 'id="drop-style"' in joined


def test_the_gauge_reflects_installed_state_rather_than_resetting():
    """
    a run that installed four of six used to end on an empty bar, which reads
    as nothing having happened.
    """
    ui = read_ui()
    assert "function paintGauge" in ui
    run_body = ui[ui.index("async function run(){"):ui.index("go.addEventListener")]
    assert "setGauge(list.length, 0)" not in run_body, "run must not blank the gauge on exit"


def test_rows_keep_the_reason_they_did_not_finish():
    """
    MANUAL on its own tells a player nothing about what to do, so the reason
    is behind the row's (i). Not in its help line, which says what the row is
    whatever Apply did: Fabula-Lous's turned into a line about DXVK
    """
    ui = read_ui()
    assert "li._hint = o.hint" in ui
    assert "job.el._hint = detail" not in ui
    end = ui[ui.index("async function jobEnd("):]
    end = end[:end.index("\n}\n")]
    assert "if (said) noteOn(job.el, said);" in end
    # an already applied row's chip says it, so its (i) only gets what's past that
    assert 'status === "already_applied" ? pastApplied(detail) : detail' in end
    note = ui[ui.index("function noteOn(li, detail){"):]
    note = note[:note.index("\n}\n")]
    assert "infoButton(" in note and "info._note = detail" in note


def test_no_backdrop_filter_anywhere():
    """
    backdrop-filter promotes the whole document to a compositing layer. this
    app ships a software rendering mode on purpose (--no-gpu, weakMachine),
    and under WebView2 without a GPU that combination repaints in bands and
    leaves half the window black.
    """
    import re
    # strip comments first: the stylesheet documents WHY it is absent, and the
    # explanation should not trip the check that enforces it
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", read_ui(), re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert "backdrop-filter" not in css


def test_the_sign_in_window_can_be_moved():
    ui = read_ui()
    assert 'id="authGrip"' in ui
    assert "function makeDraggable" in ui
    # clamped, or it can be dragged somewhere it cannot be dragged back from
    assert "innerWidth - w - 8" in ui and "innerHeight - h - 8" in ui


def test_outstanding_downloads_are_batched_into_one_trip():
    """
    the complaint this fixes: patching three games meant leaving for Nexus,
    coming back, pressing Apply, and leaving again, once per mod.
    """
    ui = read_ui()
    assert "function finish(api, items)" in ui
    assert "open_pages" in ui
    assert "Open all ${items.length} pages" in ui


def test_mirror_tooling_exists_and_is_wired_to_the_manifest_fields():
    root = MANIFEST_DIR.parent
    tool = (root / "tools" / "mirror.py").read_text(encoding="utf-8")
    assert "mirror_sha256" in tool and "mirror_url" in tool
    assert "--apply" in tool
    guide = (root / "MIRRORS.md").read_text(encoding="utf-8")
    # the permission ask is the actual blocker, so the template has to be there
    assert "Permission to mirror" in guide
    # mods/21 was the XIII cutscene audio fix, cut as a cosmetic upgrade
    # behind a Nexus login. every row still IN the UI must be tracked.
    # mods/43, cyanea-bt's XIII-2 audio fix, was replaced by our own add-on.
    # LR's DLC restoration moved from mods/4 to its author's newer mods/23
    for mod_id in ("mods/17", "mods/11", "mods/23"):
        assert mod_id in guide, f"{mod_id} is not tracked in MIRRORS.md"
    assert "Bundled with permission" in guide
    assert "Krisan Thyme" in guide


def test_every_required_job_completes_without_leaving_the_app():
    """
    the default run has to be self-sufficient. a required row that ends on
    SIGN IN, WEBSITE or MANUAL reads as the patcher being broken, and the
    honest place for anything needing an account or a manual step is Extras.
    """
    kinds = ui_job_kinds()
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = load_manifest(path)
        for patch in manifest.patches:
            if kinds.get((manifest.ui_id, patch.ui_job_id)) != "required":
                continue
            assert patch.type in ("binary_patch", "file_install", "direct",
                                  "archive_inject", "bundled_install"), (
                f"{path.name}: required job {patch.ui_job_id!r} is type {patch.type!r}, "
                "which cannot finish on its own"
            )
            if patch.type == "nexus":
                assert patch.mirror_url


def test_leviathans_tears_records_that_injection_is_still_pending():
    """
    the payload is injected into white_imgu/filelistu, not copied. downloading
    it is only half the job, and the manifest has to say so or a future reader
    assumes a file copy finishes it.
    """
    manifest = load_manifest(MANIFEST_DIR / "ffxiii2.json")
    patch = next(p for p in manifest.patches if p.ui_job_id == "leviathanstears")
    assert "inject" in (patch.notes or "").lower()


def test_a_direct_patch_with_no_fallback_page_never_opens_one(tmp_path):
    """
    the reported bug: Leviathan's Tears has no fallback_page_url, but the
    failure path defaulted page_url to download_url, so the PCGamingWiki page
    opened in a browser on every run.
    """
    patch = {"id": "tears", "label": "Leviathan's Tears", "type": "direct",
             "download_url": "https://community.pcgamingwiki.com/files/file/2354-x/?do=download",
             "install_relative_path": "alba_data", "default_enabled": True,
             "ui_job_id": "leviathanstears"}
    manifest = build_manifest(tmp_path, [patch])

    results = run_patches(tmp_path, manifest, {"tears"},
                          fetch=lambda url: b"<!DOCTYPE html><html><title>Confirm</title>")

    assert results[0].status == PatchStatus.NEEDS_FILE
    assert results[0].page_url is None, "nothing to open, so nothing may be opened"
    assert "pick up the download automatically" in results[0].detail


def test_every_shipped_direct_patch_either_has_a_page_or_expects_a_drop():
    """a direct patch must never fall back to opening its own download url"""
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        for patch in load_manifest(path).patches:
            if patch.type == "direct" and patch.fallback_page_url:
                assert patch.fallback_page_url != patch.download_url


def test_the_drop_target_is_always_available():
    """
    it is live the whole time, not only after a timeout. someone who already
    has the file must not have to wait out a countdown to use it.
    """
    ui = read_ui()
    assert 'el.id = "drop"' in ui
    assert 'id="askBrowse"' in ui, "a picker for files saved outside Downloads"
    assert "dragenter" in ui and "dragleave" in ui
    # depth counting, or the overlay flickers on every child element
    assert "++depth" in ui and "--depth" in ui


def test_a_drop_without_a_real_path_says_so():
    """WebView2 exposes file.path; without it there is nothing to open"""
    ui = read_ui()
    assert "Couldn't read that file's location" in ui


def test_a_queueable_job_never_requires_one_that_cannot_report_applied():
    """
    `requires` is enforced against patches recorded as APPLIED, and a patch
    carrying runs_installer never reaches that status: apply_nexus stops at
    NEEDS_INSTALLER because we stage a multi-gb interactive installer rather
    than running it, and on_applied only fires on APPLIED. So requiring one is
    a job that reports BLOCKED "needs X first" on every run, forever.

    HD Models Plus requires the HD Project and is exactly that shape. It is
    harmless today only because both sit in Extras, which the window never
    queues and the CLI's default set never includes. This is the tripwire for
    the day one of them is promoted, because nothing about that change would
    otherwise say the job can no longer finish.
    """
    kinds = ui_job_kinds()
    unsatisfiable = set()

    for path in sorted(MANIFEST_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        staged = {p["id"] for p in raw["patches"] if p.get("runs_installer")}
        manifest = load_manifest(path)
        for patch in manifest.patches:
            if kinds.get((manifest.ui_id, patch.ui_job_id)) == "extra":
                continue
            for needed in (patch.requires or ()):
                if needed in staged:
                    unsatisfiable.add((path.name, patch.id, needed))

    assert not unsatisfiable, (
        f"these jobs can be queued but require a staged installer that never "
        f"reports applied, so they are blocked forever: {sorted(unsatisfiable)}"
    )
