"""
Reporting what is actually in a game folder, so a problem can be diagnosed
from facts instead of from a theory.

Written after a real install failed to launch with "Application load error
3:0000065432" and the possible causes were a stale untouched.exe, leftovers
from a superseded tool, a settings file being ignored, or none of those. Every
check below distinguishes between two of those, and none of them changes
anything.
"""

from dataclasses import dataclass, field
from pathlib import Path

from patcher.engine import clean_copy, installed_on_disk
from patcher.install_resolver import resolve_install_dir
from patcher.novafix_config import resolve_addons_dir
from patcher.manifest import load_manifest
from patcher.patch_ops import identify_xiii_build, is_laa_enabled

# files a superseded tool leaves behind. harmless on their own, but they are
# the difference between "FF13Fix is gone" and "FF13Fix is mostly gone".
FF13FIX_LEFTOVERS = (
    "FF13Fix.ini", "FF13Fix.log", "FF13Fix_README.txt", "FF13Fix_LICENSE.txt",
    "MinHook_LICENSE.txt", "d3d9.log",
)

OK = "ok"
WARN = "warn"
BAD = "bad"


@dataclass
class Finding:
    level: str
    title: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"level": self.level, "title": self.title, "detail": self.detail}


@dataclass
class Report:
    game: str
    install_dir: Path | None = None
    findings: list = field(default_factory=list)

    def add(self, level: str, title: str, detail: str = "") -> None:
        self.findings.append(Finding(level, title, detail))

    @property
    def worst(self) -> str:
        levels = {f.level for f in self.findings}
        return BAD if BAD in levels else WARN if WARN in levels else OK


def _exe_report(report: Report, manifest, install_dir: Path) -> None:
    exe = install_dir / manifest.executable_relative_path
    if not exe.is_file():
        report.add(BAD, f"{exe.name} is missing")
        return

    data = exe.read_bytes()
    patched = is_laa_enabled(data)
    report.add(OK, f"{exe.name}", f"4GB patch {'applied' if patched else 'not applied'}")

    build = identify_xiii_build(data) if manifest.game_id == "ffxiii" else {}
    if manifest.game_id == "ffxiii":
        if build.get("known"):
            report.add(OK, "executable build", build["name"])
        else:
            report.add(WARN, "executable build",
                       f"not one we recognise (image {build.get('image_size', 0):,}, "
                       f"code {build.get('text_size', 0):,})")

    backup_name = next((p.required_backup_filename for p in manifest.patches
                        if p.required_backup_filename), None)
    if not backup_name:
        return

    if manifest.game_id == "ffxiii":
        _early_load_report(report, data)

    if manifest.game_id == "ffxiii" and patched and not build.get("safe_to_edit"):
        # The stock Steam build reads itself to validate, so a 4GB patch stops
        # it launching. Hiding that means running code before the entry point,
        # and the loader only offers one way in: a statically imported DLL.
        from patcher.engine import dinput8_shim_installed, proxy_redirects_executable

        if dinput8_shim_installed(install_dir, manifest):
            report.add(OK, "4GB patch on the Steam build",
                       "our dinput8.dll is beside the executable, and the game imports "
                       "that statically, so it runs before the DRM check and serves it "
                       "untouched.exe")
        elif proxy_redirects_executable(install_dir, manifest):
            report.add(OK, "4GB patch on the Steam build",
                       "the installed proxy says in its own config that it redirects "
                       "executable reads, so the DRM sees the original bytes")
        else:
            report.add(BAD, "this executable won't launch",
                       "it has the 4GB patch and it is the stock Steam build, which checks "
                       "itself against the DRM. run the patcher again and it will undo the "
                       "patch. the 4GB patch needs the community replacement executable")

    backup = exe.with_name(backup_name)
    if not backup.is_file():
        return

    original = backup.read_bytes()
    if is_laa_enabled(original):
        report.add(WARN, f"{backup_name} is not a clean copy",
                   "it already has the 4GB patch, so it cannot be used to restore the "
                   "original. the patcher will clear the flag instead, which is exact "
                   "as long as nothing else edited the executable")
        return

    if len(original) != len(data):
        report.add(WARN, f"{backup_name} is a different size to the executable",
                   f"{len(original):,} vs {len(data):,}. it may be from a different "
                   "version of the game")
        return

    # the engine's own test, so the report can't call a copy clean that Apply
    # is about to move aside
    if clean_copy(backup, data) is None:
        report.add(WARN, f"{backup_name} is from a different build",
                   "its header doesn't match, so it is not a copy of this executable")
    else:
        report.add(OK, backup_name,
               "clean copy of the original. this is what the 4GB compatibility shim hands "
               "the DRM check, and what the patcher restores from if the patch has to "
               "come back off")


def _early_load_report(report: Report, exe_bytes: bytes) -> None:
    """
    which DLLs the loader maps before this executable's entry point runs.

    The CEG self-check lives in the entry point, so anything that wants to be
    in front of it has to be a statically imported DLL: the loader runs those
    DllMains first, and that is the entire list of free openings. FF13Fix took
    d3d9.dll and that is why FF13Fix works. A NovaFix add-on loads at render
    init, which the refused process never reaches.

    Printed rather than acted on. Deciding what a working shim would have to be
    is a design question, and it wants the real answer off a real install
    instead of another guess.
    """
    from patcher.patch_ops import PROXY_CANDIDATES, read_static_imports

    imports = read_static_imports(exe_bytes)
    if not imports:
        report.add(WARN, "early-load targets",
                   "couldn't read the import table, so there is no telling what loads "
                   "before the entry point")
        return
    usable = [name for name in PROXY_CANDIDATES if name in imports]
    report.add(OK, "early-load targets",
               (", ".join(usable) if usable else "none of the usual proxy targets")
               + f". {len(imports)} static imports in total: " + ", ".join(imports))


def _proxy_report(report: Report, manifest, install_dir: Path) -> None:
    proxy_patch = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    if proxy_patch is None:
        return
    folder = install_dir / (proxy_patch.install_relative_path or "")
    proxy = folder / "d3d9.dll"

    if not proxy.is_file():
        report.add(WARN, "NovaFix is not installed", "no d3d9.dll beside the executable")
    elif installed_on_disk(install_dir, proxy_patch):
        report.add(OK, "NovaFix", f"d3d9.dll, {proxy.stat().st_size:,} bytes")
    else:
        # present but not our build. It still loads and passes every presence
        # check, so this is the line that has to say so.
        expected = next((s.get("sha256") for s in proxy_patch.bundled_files
                         if s.get("target") == "d3d9.dll"), None)
        report.add(BAD, "NovaFix is not the build this patcher ships",
                   f"d3d9.dll is {proxy.stat().st_size:,} bytes and does not match the "
                   f"pinned {expected[:12] if expected else '?'}. run the patcher again "
                   "and it will replace it")

    # checked whether or not the proxy is there. an install with leftovers and
    # no proxy is a half-finished pivot, which is exactly when saying so helps.
    leftovers = [name for name in FF13FIX_LEFTOVERS if (folder / name).is_file()]
    if leftovers:
        report.add(WARN, "FF13Fix files are still here",
                   ", ".join(leftovers) + ". NovaFix disables its own overlapping "
                   "features when it detects FF13Fix, so these should go")

    # resolve_addons_dir, not "addons": NovaFix scans only the first of
    # addons/extra/extras/mods/dll that exists, and so does the engine.
    addons = resolve_addons_dir(folder)
    if addons.is_dir():
        # the .asi files, not the folder names. an empty ff13-exe-redirect/
        # printed the same line as a working one, which is the shape of report
        # that costs an evening.
        loadable = sorted(p.name for p in addons.rglob("*") if p.suffix.lower() in (".asi", ".dll"))
        report.add(OK, f"add-ons in {addons.name}",
                   ", ".join(loadable) if loadable else "nothing NovaFix can load")

    shim = next((p for p in manifest.patches if p.id == "install_dinput8_shim"), None)
    if shim is not None:
        from patcher.engine import install_target_dir

        dll = install_target_dir(install_dir, shim) / "dinput8.dll"
        if installed_on_disk(install_dir, shim):
            report.add(OK, "4GB compatibility shim",
                       "our dinput8.dll, which the loader runs before the entry point and "
                       "therefore before the DRM check")
        elif dll.is_file():
            # A dinput8.dll that is not ours is the worst file in this folder.
            # The game imports it statically, so whatever sits here runs first,
            # with the game's privileges, before anything can look at it.
            report.add(BAD, "dinput8.dll is not the build this patcher ships",
                       f"{dll.stat().st_size:,} bytes, which does not match the pinned "
                       f"{_pin_for(shim, 'dinput8.dll')}. the game imports this file "
                       "statically, so it runs before the entry point whatever it is. run "
                       "the patcher again and it will replace it")
        else:
            report.add(OK, "4GB compatibility shim", "not installed")

    redirect = next((p for p in manifest.patches if p.id == "install_exe_redirect"), None)
    if redirect is not None:
        from patcher.engine import install_target_dir

        home = install_target_dir(install_dir, redirect)
        asi = home / "00_ff13_exe_redirect.asi"
        if installed_on_disk(install_dir, redirect):
            report.add(OK, "exe-redirect add-on",
                       "our build, which is what lets the 4GB patch survive the DRM check")
        elif asi.is_file():
            # an older build of ours armed whenever untouched.exe existed, so it
            # hooked CreateFile on installs with nothing to hide, which Steam
            # answers with Application load error 3:0000065432.
            report.add(BAD, "the exe-redirect add-on is not the build this patcher ships",
                       f"{asi.name} is {asi.stat().st_size:,} bytes and does not match the "
                       f"pinned {_pin_for(redirect, asi.name)}. an older build hooked "
                       "CreateFileA/W whenever untouched.exe existed, which Steam's DRM "
                       "answers with Application load error 3:0000065432. run the patcher "
                       "again and it will replace it")
        else:
            report.add(OK, "exe-redirect add-on", "not installed, which is correct: NovaFix "
                       "loads add-ons at render init and the DRM check runs long before that")


def _pin_for(patch, target_name: str) -> str:
    expected = next((s.get("sha256") for s in patch.bundled_files
                     if s.get("target") == target_name), None)
    return expected[:12] if expected else "?"


def _settings_report(report: Report, manifest, install_dir: Path) -> None:
    from patcher import novafix_config

    proxy_patch = next((p for p in manifest.patches if p.id == "install_novafix"), None)
    if proxy_patch is None:
        return
    relative = proxy_patch.install_relative_path or ""
    path = novafix_config.config_path(install_dir, relative)
    if not path.is_file():
        report.add(OK, "NovaFix.ini", "not written yet, NovaFix will create it on first run")
        return

    values = novafix_config.read(path)
    graphics = values.get("Graphics", {})
    report.add(OK, "NovaFix.ini", f"{sum(len(v) for v in values.values())} settings")

    # XIII runs Square Enix's own launcher, and NovaFix only overrides what the
    # launcher passes to the game when this is on. without it, every graphics
    # setting written here is read and then ignored.
    if manifest.game_id in ("ffxiii", "ffxiii2") and graphics:
        override = graphics.get("OverrideLauncherGraphics", "").lower()
        if override not in ("on", "true", "1", "yes"):
            report.add(WARN, "launcher graphics are not overridden",
                       "the game's own launcher decides resolution, shadows and "
                       "anti-aliasing unless OverrideLauncherGraphics is On, so the "
                       "graphics settings in this file are being ignored")


def _state_report(report: Report, manifest) -> None:
    from patcher.paths import state_path_for
    from patcher.state import load_state

    path = state_path_for(manifest.game_id)
    if not path.is_file():
        report.add(WARN, "no record of what this tool installed",
                   f"expected at {path}. without it, uninstalling and replacing an "
                   "older tool cannot find what to remove")
        return
    state = load_state(path)
    report.add(OK, "recorded installs", ", ".join(sorted(state["applied_patches"])) or "none")

    # the one finding that explains a patch which keeps coming back off. without
    # it the report shows "4GB patch not applied" next to a job the user keeps
    # pressing, and nothing says why.
    checks = state.get("launch_checks")
    if isinstance(checks, dict):
        from patcher.launch_watch import DRM_ERROR

        refused = sorted((pid, entry.get("cover") or "an older mechanism")
                         for pid, entry in checks.items()
                         if isinstance(entry, dict) and entry.get("verdict") == DRM_ERROR)
        for patch_id, cover in refused:
            # naming the cover is the difference between "your install cannot
            # take this" and "this way of hiding it did not work". only the
            # second one is what was measured, and only the second one leaves
            # room for the next attempt.
            report.add(WARN, "a patch was refused at launch",
                       f"{patch_id}, covered by {cover}. the game was started with it on "
                       "and would not run, so this tool took it back off and will not try "
                       "that combination again here. if that was a bad reading, "
                       f"--retry-4gb forgets it and the next Apply checks again")


def _injection_report(report: Report, manifest, install_dir: Path) -> None:
    """
    why an archive injection reads as applied, or does not.

    This one is opaque from outside in a way the others are not. A binary patch
    is a bit you can look at and a bundled install is a file you can see, but an
    injection lives inside a multi-gigabyte archive, so when the row disagrees
    with the user there is nothing to point at.

    The archive decides, and says so first. The journal walk below only runs
    when the archive could not answer, or when it disagrees with the record,
    which is the case worth spelling out: applied once, not there now.
    """
    from patcher.archive.spanbackup import load_journal
    from patcher.engine import injection_on_disk
    from patcher.paths import state_path_for
    from patcher.state import load_state

    injections = [p for p in manifest.patches if p.type == "archive_inject"]
    if not injections:
        return

    try:
        recorded = load_state(state_path_for(manifest.game_id))["applied_patches"]
    except OSError as e:
        report.add(WARN, "could not read the patch record", str(e))
        return

    for patch in injections:
        entry = recorded.get(patch.id)
        on_disk = injection_on_disk(install_dir, patch)

        if patch.retired:
            # gone from the patcher, so only news while an earlier version's
            # install of it is in the archive or on the record
            if on_disk is True:
                report.add(OK, patch.label,
                           "an earlier version put it in and it's still in the archive. It "
                           "doesn't ship any more: " + (
                               "Remove under the Mods drawer's Installed, or a revert, takes it "
                               "off" if entry is not None else
                               "with no record of it here, verifying the game files through "
                               "Steam is what takes it out"))
                continue
            if on_disk is False and entry is not None:
                report.add(WARN, patch.label,
                           "on the record from an earlier version, but the archive holds "
                           "something else at that entry now, which is what Steam's verify "
                           "does. Remove under the Mods drawer's Installed clears the record")
                continue
            if on_disk is not None or entry is None:
                continue

        if on_disk is True:
            report.add(OK, patch.label,
                       "our payload is in the archive: the index entry hashes to "
                       "the file we ship. the row reads applied whatever the "
                       "record says")
            continue
        if on_disk is False and entry is None:
            report.add(OK, patch.label,
                       "not in the archive and never recorded, so the row asking "
                       "for it is correct")
            continue
        if on_disk is False:
            report.add(BAD, patch.label,
                       "recorded as applied, but the archive holds something else "
                       "at that entry now. verifying the game files through Steam "
                       "does this. apply it again")
            continue

        # on_disk is None: no pinned hash, or the archives are not in this
        # install, so the journal is doing the work
        if entry is None:
            report.add(OK, patch.label,
                       "no record of it being applied, so the row asking for it "
                       "is correct")
            continue

        journals = entry.get("journals") or []
        if not journals:
            report.add(WARN, patch.label,
                       "recorded as applied but with no undo journal, so whether "
                       "its bytes are still in the archive cannot be checked. "
                       "the row trusts the record")
            continue

        for raw in journals:
            journal_path = Path(raw)
            spans_path = journal_path.parent / journal_path.name.replace(".journal", ".spans")
            name = journal_path.name

            if not journal_path.is_file():
                report.add(BAD, f"{patch.label}: {name}",
                           f"the journal is gone from {journal_path.parent}. without "
                           "it the check cannot see the mod and the row asks for it again")
                continue
            if not spans_path.is_file():
                report.add(BAD, f"{patch.label}: {name}",
                           f"{spans_path.name} is missing beside the journal, so the "
                           "original bytes it compares against are gone")
                continue

            try:
                journal = load_journal(journal_path)
            except (OSError, ValueError, KeyError) as e:
                report.add(BAD, f"{patch.label}: {name}", f"unreadable journal ({e})")
                continue

            target = Path(journal.target)
            if not target.is_file():
                report.add(BAD, f"{patch.label}: {target.name}",
                           f"the archive it names is not there: {target}. a moved or "
                           "reinstalled game records this way")
                continue

            live_size = target.stat().st_size
            if live_size != journal.target_size:
                report.add(BAD, f"{patch.label}: {target.name}",
                           f"the archive is {live_size:,} bytes and was {journal.target_size:,} "
                           "when patched, so every recorded offset describes a file that no "
                           "longer exists. Steam verifying the files does this")
                continue

            ours = 0
            with target.open("rb") as live, spans_path.open("rb") as original:
                for span in journal.spans:
                    live.seek(span.offset)
                    original.seek(span.journal_offset)
                    if live.read(span.length) != original.read(span.length):
                        ours += 1
            total = len(journal.spans)
            if ours:
                report.add(OK, f"{patch.label}: {target.name}",
                           f"{ours} of {total} regions still differ from the original, "
                           "so the mod is in there")
            else:
                report.add(BAD, f"{patch.label}: {target.name}",
                           f"all {total} regions are byte for byte the original again, "
                           "so the mod is not in the archive any more even though the "
                           "record says it was applied")


def diagnose(game_id: str, manual_path: Path | None = None,
             steam_library_paths=None) -> Report:
    """inspects one game and reports. reads only, changes nothing."""
    from patcher.paths import discover_manifests

    manifests = discover_manifests()
    if game_id not in manifests:
        report = Report(game=game_id)
        report.add(BAD, f"no manifest for {game_id}")
        return report

    manifest = load_manifest(manifests[game_id])
    report = Report(game=manifest.display_name)

    resolution = resolve_install_dir(manifest, manual_path=manual_path,
                                     steam_library_paths=steam_library_paths)
    if not resolution.found:
        # the resolver's message is deliberately advice-free, so the fix goes
        # here in the form this caller can actually offer
        report.add(BAD, "couldn't find the game",
                   f"{resolution.error or ''}. Point at it with --path "
                   '"C:\\path\\to\\the\\game", or pick the folder in the app')
        return report

    report.install_dir = resolution.install_dir
    _exe_report(report, manifest, resolution.install_dir)
    _proxy_report(report, manifest, resolution.install_dir)
    _settings_report(report, manifest, resolution.install_dir)
    _injection_report(report, manifest, resolution.install_dir)
    _state_report(report, manifest)
    return report


def format_report(report: Report) -> str:
    marks = {OK: "  ok  ", WARN: " warn ", BAD: " BAD  "}
    lines = [f"{report.game}"]
    if report.install_dir:
        lines.append(f"  {report.install_dir}")
    lines.append("")
    for finding in report.findings:
        lines.append(f"{marks[finding.level]} {finding.title}")
        if finding.detail:
            for chunk in _wrap(finding.detail, 68):
                lines.append(f"        {chunk}")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
