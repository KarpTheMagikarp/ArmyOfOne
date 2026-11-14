"""
the CLI, and what Windows runs for nxm:// links. The logic lives in
patcher/app.py; this prints what happened.

    python main.py ffxiii
    python main.py ffxiii --path "D:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII"
    python main.py ffxiii --dry-run
    python main.py ffxiii --revert
    python main.py --doctor
    python main.py                            every installed game, defaults
    python main.py --setup-hd-pack ffxiii     key, nxm handler, mod page
    python main.py --handle-nxm "nxm://..."   Windows runs this, not you
"""

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from patcher.access import (make_console_unicode_safe, release_console,
                            silence_device_errors, write_failure)
from patcher.app import run_full_automated_patch
from patcher.engine import PatchStatus
from patcher.mods.hd_pack_installer import (
    download_and_stage,
    extract_archive,
    find_installer_candidates,
)
from patcher.logging_setup import setup_logging
from patcher.paths import migrate_legacy_data
from patcher.manifest import load_manifest
from patcher.paths import (
    discover_manifests,
    log_path,
    nexus_config_path,
    persistent_data_root,
    state_path_for,
)
from patcher.mods.nexus_api import get_download_url, mod_page_url, parse_nxm_url
from patcher.mods.nexus_auth import sign_in as nexus_sign_in
from patcher.mods.nxm_handler import load_api_key, save_api_key
from patcher.updater import check_for_update, download_and_install_manifest


def ask(prompt: str) -> str | None:
    """
    input() that survives a windowed build. PyInstaller with console=False
    detaches stdin, so a bare input() raises RuntimeError("lost sys.stdin")
    and takes the whole run down. None means "no console to ask with" and
    every caller treats that as the user declining, which is the safe
    default for all three prompts here (update, api key, run installer).
    """
    if not sys.stdin or not sys.stdin.isatty():
        return None
    try:
        return input(prompt)
    except (RuntimeError, EOFError, OSError):
        return None


def notify(message: str) -> None:
    """
    say something the user will actually see. with console=False, print() goes
    to a handle nobody is reading, and the nxm:// handoff is launched by
    windows with no terminal attached at all, so a plain print there is the
    same as saying nothing.
    """
    print(message)
    if sys.stdin and sys.stdin.isatty():
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "ArmyOfOne", 0x40)
    except Exception:
        pass


def invocation_hint() -> str:
    """how the user would type this command, frozen exe or dev checkout."""
    if getattr(sys, "frozen", False):
        return f"{Path(sys.executable).name} "
    return "python main.py "


def pause_before_close() -> None:
    """double-click leaves no terminal behind, so hold the window open."""
    if ask("Press Enter to close this window...") is None:
        time.sleep(6)


def hd_pack_staging_dir(game_domain: str) -> Path:
    """
    where to put a multi-gigabyte download: beside the game it's for, since
    %LOCALAPPDATA% is on C: and the pack is tens of gigabytes headed somewhere
    else. Falls back to the old spot when the game can't be found.
    """
    from patcher.app import detect_steam_library_paths
    from patcher.install_resolver import resolve_install_dir

    try:
        libraries = detect_steam_library_paths()
        for path in MANIFEST_PATHS.values():
            manifest = load_manifest(path)
            domains = {p.nexus_game_domain for p in manifest.patches if p.nexus_game_domain}
            if game_domain not in domains:
                continue
            resolution = resolve_install_dir(manifest, steam_library_paths=libraries)
            if resolution.found:
                return resolution.install_dir / ".patcher-downloads"
    except Exception:
        # detection is best effort here. the download is the point.
        pass
    return persistent_data_root() / "downloads"


MANIFEST_PATHS = discover_manifests()

# release tags are compared against each manifest's manifest_version, so tag
# releases to match (v1.0.0). A repo with no releases yet just 404s quietly
GITHUB_RELEASES_API_URL = "https://api.github.com/repos/KarpTheMagikarp/ArmyOfOne/releases/latest"

STATUS_LABELS = {
    PatchStatus.APPLIED: "applied",
    PatchStatus.ALREADY_APPLIED: "already applied",
    PatchStatus.WOULD_APPLY: "would apply",
    PatchStatus.SKIPPED: "skipped",
    PatchStatus.FAILED: "couldn't apply",
}


def maybe_offer_update(manifest_path: Path, manifest_version: str, asset_name: str):
    """
    prints the release notes when the latest release is newer than this
    manifest, and nothing otherwise. --skip-update-check turns it off.
    """
    info = check_for_update(manifest_version, GITHUB_RELEASES_API_URL, asset_name)
    if not info.available:
        return

    print("=" * 60)
    print(f"  New update available: {info.latest_version}")
    print("=" * 60)
    print(info.release_notes or "(no release notes provided)")
    print("-" * 60)

    choice = (ask("Update now? [y/N]: ") or "").strip().lower()
    if choice == "y" and info.download_url:
        try:
            download_and_install_manifest(info.download_url, manifest_path)
            print("updated. re-run to apply patches with the new manifest.")
        except OSError as e:
            print(write_failure(e, manifest_path))
        except Exception as e:
            print(f"update failed: {e}")
    else:
        print("skipped for now.")


def try_sso_login(api_key_path: Path) -> bool:
    """
    one-click Nexus login. returns whether a key ended up stored.

    the whole sequence lives in patcher/mods/nexus_auth.py so the CLI and the
    UI run identical code; this just prints what it is doing.
    """
    outcome = nexus_sign_in(
        api_key_path,
        open_browser=lambda url: webbrowser.open(url),
        on_progress=print,
    )
    if not outcome.ok:
        print(f"({outcome.detail})")
    return outcome.ok


def prompt_for_manual_api_key() -> str | None:
    print("this needs your personal (free) Nexus API key.")
    print("generate one at https://www.nexusmods.com/users/myaccount?tab=api")
    print("under 'Personal API Key' - it's free, no Premium required.")
    key = (ask("paste your Nexus API key here: ") or "").strip()
    return key or None


def setup_hd_pack(game_id: str):
    """
    one-time setup for the HD texture pack download: gets a Nexus API key if
    none is stored (SSO when available, manual otherwise), registers the
    nxm:// handler, opens the mod page and says what to click. Doesn't wait;
    Windows starts --handle-nxm as its own process when they click through.
    """
    manifest = load_manifest(MANIFEST_PATHS[game_id])
    hd_patch = next((p for p in manifest.patches if p.id == "hd_texture_pack"), None)
    # a nexus patch has no source_url, only its domain and id, so checking
    # source_url here turned every run into "no link found"
    if hd_patch is None or not hd_patch.nexus_mod_id:
        print(f"no HD texture pack link found for {manifest.display_name}")
        return
    page = mod_page_url(hd_patch.nexus_game_domain, hd_patch.nexus_mod_id)

    api_key_path = nexus_config_path()
    if load_api_key(api_key_path) is None:
        # one-click first, manual entry as the fallback that needs nobody's
        # permission. try_sso_login() stores the key itself on success.
        if not try_sso_login(api_key_path):
            manual = prompt_for_manual_api_key()
            if not manual:
                print("no key obtained, stopping.")
                return
            save_api_key(api_key_path, manual)
        print("saved - you won't need to do this again.")

    # through nxm_registry, the one the app itself uses on startup: it backs up
    # whoever had nxm links before and will not take them from Vortex or Mod
    # Organizer without being asked. The old route here did both silently.
    from patcher.nxm_registry import current, register
    state = current()
    force = False
    if state.registered and not state.ours:
        answer = (ask(f"another app handles Nexus downloads right now:\n  {state.owner}\n"
                      "hand them to this program instead? it can be given back. [y/N] ") or "")
        if answer.strip().lower() not in ("y", "yes"):
            print("left alone, so Nexus downloads keep going to that app.")
            return
        force = True
    result = register(force=force)
    if not result.ours:
        print(f"couldn't register the download handler: {result.error}")
        return
    print("registered this program to receive Nexus downloads.")

    print(f"opening {page}")
    print("log in if needed, then click the 'Mod Manager Download' button on that page.")
    print("this program will pick up the download automatically once you do.")
    webbrowser.open(page)


def handle_nxm_callback(nxm_url: str):
    """
    what actually runs when windows launches this program because the
    user clicked "Mod Manager Download" on nexus. resolves the real
    download link, downloads it, extracts it if it's something we know
    how to extract (.zip), and offers to launch whatever looks like an
    installer inside, always with explicit confirmation, never silently.
    """
    log = setup_logging(log_path())
    log.info("nxm callback received: %s", nxm_url)

    api_key_path = nexus_config_path()
    api_key = load_api_key(api_key_path)
    if api_key is None:
        notify(f"no Nexus API key saved yet - run '{invocation_hint()}--setup-hd-pack <game>' first")
        return

    try:
        nxm = parse_nxm_url(nxm_url)
    except ValueError as e:
        notify(f"couldn't understand that download link: {e}")
        log.warning("bad nxm url: %s", e)
        return

    print(f"resolving download for mod {nxm.mod_id}, file {nxm.file_id}...")
    download_url = get_download_url(api_key, nxm)
    if download_url is None:
        notify("couldn't get a download link from Nexus - it may have expired, try clicking the button again")
        log.warning("nexus download link resolution failed for mod %s file %s", nxm.mod_id, nxm.file_id)
        return

    # beside the game, not in %LOCALAPPDATA%: the pack is tens of GB and the
    # game's often on another drive.
    download_dir = hd_pack_staging_dir(nxm.game_domain)
    print("downloading...")

    def say(progress):
        if progress.total:
            print(f"  {progress.done * 100 // progress.total}%  {progress.label}", end="\r")

    try:
        file_path = download_and_stage(download_url, download_dir, on_progress=say)
    except Exception as e:
        log.exception("hd pack download failed")
        notify(f"download failed: {e}")
        return

    print(f"downloaded to {file_path}")
    log.info("downloaded %s", file_path)

    extract_dir = extract_archive(file_path)
    if extract_dir is None:
        notify(f"couldn't auto-extract a {file_path.suffix or 'this'} file - extract it yourself and run its installer.\n\nfile is at: {file_path}")
        return

    print(f"extracted to {extract_dir}")
    # the HD Project's own installer is the batch file this app exists to
    # replace, and the app's install takes a backup first. Offering to launch
    # it here was the one route that skipped all of that.
    from patcher.mods.hd_games import identify
    game, _ = identify(extract_dir)
    if game is not None:
        notify(f"{game.name[0].upper()}{game.name[1:]} is downloaded and unpacked. Open the "
               "patcher, go to Mods, press Choose a folder and pick:\n\n"
               f"{extract_dir}\n\nIt installs from there, with a backup of your game taken first.")
        return
    candidates = find_installer_candidates(extract_dir)
    if not candidates:
        notify(f"no installer found automatically - check {extract_dir} and run its setup manually")
        return

    print("found what looks like an installer:")
    for i, candidate in enumerate(candidates, 1):
        print(f"  {i}. {candidate.name}")

    choice = (ask("run one of these now? enter the number, or press enter to skip: ") or "").strip()
    if not choice:
        notify(f"downloaded and extracted. run the installer yourself from:\n\n{extract_dir}")
        return
    try:
        selected = candidates[int(choice) - 1]
    except (ValueError, IndexError):
        print("didn't understand that, skipping")
        return

    print(f"launching {selected.name}...")
    log.info("launching hd pack installer: %s", selected)
    subprocess.Popen([str(selected)], cwd=extract_dir)


def run_and_report_game(
    manifest_path: Path,
    manual_path: Path | None,
    dry_run: bool,
    skip_update_check: bool,
    log,
) -> bool:
    """
    runs the automated patch for one game and prints/logs what
    happened. returns whether the install was found at all (not
    whether every patch succeeded), shared between the normal
    single-game CLI path and the double-click path that tries every
    known game in one go.
    """
    manifest = load_manifest(manifest_path)

    if not skip_update_check:
        maybe_offer_update(manifest_path, manifest.manifest_version, manifest_path.name)

    state_path = state_path_for(manifest.game_id)
    outcome = run_full_automated_patch(
        manifest, state_path=state_path, manual_path=manual_path, dry_run=dry_run
    )

    if not outcome.resolution.found:
        log.warning("install not found: %s", outcome.resolution.error)
        print(f"{manifest.display_name}: not found ({outcome.resolution.error})")
        return False

    log.info("install found at %s (via %s)", outcome.resolution.install_dir, outcome.resolution.source)
    if dry_run:
        print(f"[dry run] found {manifest.display_name} at {outcome.resolution.install_dir} (via {outcome.resolution.source})")
        print("[dry run] nothing will actually be changed")
    else:
        print(f"found {manifest.display_name} at {outcome.resolution.install_dir} (via {outcome.resolution.source})")
    print()

    retired = {p.id for p in manifest.patches if p.retired}
    for result in outcome.results:
        # gone from the patcher, so not news on every run
        if result.patch_id in retired and result.status == PatchStatus.SKIPPED:
            continue
        label = STATUS_LABELS.get(result.status, result.status)
        line = f"  {result.label}: {label}"
        # "not selected" is the only skip reason worth hiding, it is just noise
        # on every non-default patch. an asset_pack skip explaining that the
        # installer isn't built yet is the opposite: that is the whole answer.
        if result.detail and result.detail != "not selected":
            line += f" - {result.detail}"
        print(line)
        log.info("patch %s -> %s (%s)", result.patch_id, result.status, result.detail)

    if any(r.patch_id == "hd_texture_pack" for r in outcome.results):
        print(f"tip: run '{invocation_hint()}--setup-hd-pack {manifest.game_id}' to set up the HD texture pack download")

    return True


def revert_game(game_id: str, manual_path: Path | None = None) -> None:
    """
    puts a game back the way it was, using only what we recorded installing.

    deliberately never guesses from the manifest: a file we did not install is
    not ours to delete, even if a patch would have written something by that
    name. someone who put a mod there by hand keeps it.
    """
    from patcher.install_resolver import resolve_install_dir
    from patcher.revert import revert_all
    from patcher.app import detect_steam_library_paths

    manifest = load_manifest(MANIFEST_PATHS[game_id])
    resolution = resolve_install_dir(manifest, manual_path=manual_path,
                                     steam_library_paths=detect_steam_library_paths())
    if not resolution.found:
        notify(resolution.error)
        return

    from patcher.running import running_from

    held = running_from(resolution.install_dir,
                        resolution.install_dir / manifest.executable_relative_path)
    if held:
        notify(f"{manifest.display_name} is running ({held}). Close it first: its files can't "
               "be changed while it has them open, so nothing was reverted.")
        return

    print(f"reverting {manifest.display_name} at {resolution.install_dir}\n")
    release_console("the result comes up in a message box when it is done, and closing "
                    "this window will not stop it")
    results = revert_all(resolution.install_dir, manifest, state_path_for(manifest.game_id))
    if not results:
        notify("nothing to undo, this game has no patches applied")
        return

    # one notify at the end, because the exe run from a shortcut or the Run
    # box has no terminal and the outcome is the one thing that has to land
    lines = [f"  {r.label}: {'ok' if r.ok else 'FAILED'} - {r.detail}" for r in results]
    if any(not r.ok for r in results):
        lines.append("\nsome of it could not be undone. verifying the game files through "
                     "Steam will replace anything still wrong.")
    else:
        # what it applied, which is all it knows about. A mod installed by hand
        # since is still there, and "back to how it was" said otherwise
        lines.append("\ndone, everything this app applied has been taken back.")
    notify("\n".join(lines))


def run_double_click_flow():
    """
    no arguments, like double clicking the exe: tries every known game and
    patches whichever are installed.
    """
    log = setup_logging(log_path())
    log.info("double-click launch, trying every known game")

    try:
        print("looking for installed FFXIII trilogy games...")
        print()

        found_any = False
        for manifest_path in MANIFEST_PATHS.values():
            found = run_and_report_game(manifest_path, manual_path=None, dry_run=False, skip_update_check=False, log=log)
            found_any = found_any or found
            print()

        if not found_any:
            print("couldn't automatically find any of the games.")
            print("if steam isn't installed in its usual place, open a terminal here and run, e.g.:")
            print(f'  {invocation_hint()}ffxiii --path "C:\\path\\to\\your\\game"')

    except Exception:
        log.exception("unhandled error during double-click run")
        print(f"something went wrong - see {log_path()} for details")

    pause_before_close()


def main():
    # before anything reads state, so a build that kept its records beside the
    # exe hands them over instead of the app starting amnesiac
    make_console_unicode_safe()
    silence_device_errors()
    migrate_legacy_data()

    # a bare double-click in Explorer launches this with zero arguments -
    # rather than requiring a game name it doesn't have, just try to
    # find and patch whatever's actually installed. see
    # run_double_click_flow()'s docstring for why.
    if len(sys.argv) == 1:
        run_double_click_flow()
        return

    # --handle-nxm carries a raw url as its argument, which is its own
    # invocation shape entirely (no game, no other flags), handled
    # before argparse rather than folded into the normal parser.
    if "--handle-nxm" in sys.argv:
        idx = sys.argv.index("--handle-nxm")
        if idx + 1 >= len(sys.argv):
            print("--handle-nxm needs a url argument")
            sys.exit(1)
        handle_nxm_callback(sys.argv[idx + 1])
        return

    parser = argparse.ArgumentParser(description="ArmyOfOne, the FFXIII trilogy patcher (CLI)")
    parser.add_argument("game", nargs="?", choices=sorted(MANIFEST_PATHS.keys()), default=None)
    parser.add_argument("--path", type=Path, default=None, help="manually specify the install folder")
    parser.add_argument("--skip-update-check", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen without changing anything")
    parser.add_argument("--doctor", action="store_true",
                        help="report the state of an install without changing anything")
    parser.add_argument("--revert", action="store_true",
                        help="undo everything this tool applied to a game")
    parser.add_argument("--retry-4gb", action="store_true",
                        help="forget a recorded launch refusal so the 4GB patch is "
                             "tried again on the next Apply")
    parser.add_argument("--setup-hd-pack", metavar="GAME", choices=sorted(MANIFEST_PATHS.keys()), default=None,
                         help="one-time setup for semi-automated HD texture pack downloads")
    args = parser.parse_args()

    # logging before the dispatch, so --doctor and --revert failures land in
    # the log too. The windowed build has no console to print a traceback to.
    log = setup_logging(log_path())
    log.info("run started: game=%s dry_run=%s manual_path=%s doctor=%s revert=%s",
             args.game, args.dry_run, args.path, args.doctor, args.revert)

    if args.revert and not args.game:
        print("say which game to revert, e.g. python main.py ffxiii2 --revert")
        sys.exit(1)
    if args.retry_4gb and not args.game:
        print("say which game, e.g. python main.py ffxiii --retry-4gb")
        sys.exit(1)
    if not (args.doctor or args.setup_hd_pack) and args.game is None:
        parser.error("a game is required unless using --setup-hd-pack")

    try:
        if args.retry_4gb:
            # The launch check is allowed to be wrong, so there has to be a way
            # to tell it so. A machine that was mid Steam update, or a launch
            # somebody closed, can leave a refusal recorded against a
            # combination that would work, and without this the only route back
            # is hand editing a json.
            from patcher.engine import LAA_GUARDS
            from patcher.manifest import load_manifest
            from patcher.paths import state_path_for
            from patcher.state import clear_launch_record

            manifest = load_manifest(MANIFEST_PATHS[args.game])
            cleared = [p.id for p in manifest.patches if p.build_guard in LAA_GUARDS]
            for patch_id in cleared:
                clear_launch_record(state_path_for(manifest.game_id), patch_id)
            print(f"forgot the launch record for {', '.join(cleared) or 'nothing'}. "
                  "press Apply and it will be tried again, and checked again.")
            return

        if args.doctor:
            from patcher.doctor import diagnose, format_report
            from patcher.app import detect_steam_library_paths

            games = [args.game] if args.game else sorted(MANIFEST_PATHS)
            reports = [diagnose(game, args.path, detect_steam_library_paths()) for game in games]
            # one --path is one game's folder, so the other two "couldn't find
            # the game" there by definition and only bury the one that matters
            if args.path and any(r.install_dir for r in reports):
                reports = [r for r in reports if r.install_dir]
            for report in reports:
                print(format_report(report))
                print()
            return

        if args.revert:
            revert_game(args.game, args.path)
            return

        if args.setup_hd_pack:
            setup_hd_pack(args.setup_hd_pack)
            return

        found = run_and_report_game(
            MANIFEST_PATHS[args.game], args.path, args.dry_run, args.skip_update_check, log
        )
        if not found:
            sys.exit(1)
    # notify rather than print: a revert has already let go of its terminal by
    # the time anything in it can fail, and promised a message box instead
    except OSError as e:
        log.exception("unhandled file error during run")
        notify(write_failure(e, args.path or "the game folder"))
        sys.exit(1)
    except Exception:
        log.exception("unhandled error during run")
        notify(f"something went wrong - see {log_path()} for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
