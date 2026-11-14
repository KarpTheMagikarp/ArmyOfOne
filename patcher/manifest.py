"""
loads a game manifest json and turns it into something the engine can
actually work with. keeping this separate from the engine so we can
validate a manifest file without needing to touch a real game install.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Patch:
    id: str
    label: str
    type: str  # binary_patch, file_install, nexus, direct, archive_inject, manual, asset_pack
    default_enabled: bool
    requires_backup: bool = False
    required_backup_filename: str | None = None  # e.g. "untouched.exe" for steam DRM, overrides the generic .backup naming
    operation: str | None = None          # only for binary_patch
    target_relative_path: str | None = None  # only for binary_patch, falls back to the game's exe if unset
    source_url: str | None = None         # only for asset_pack
    github_repo: str | None = None        # only for file_install, "owner/repo"
    install_relative_path: str | None = None  # for asset_pack and file_install
    source_subdir: str | None = None      # for file_install, which folder inside the archive to take
    nexus_game_domain: str | None = None  # for nexus, e.g. "finalfantasy13"
    nexus_mod_id: str | None = None       # for nexus, the numeric mod id
    nexus_file_id: str | None = None      # for nexus, pins a file; latest main file if unset
    manual_url: str | None = None         # for manual, where the user gets it themselves
    mirror_url: str | None = None         # a direct download that bypasses Nexus entirely
    download_url: str | None = None       # for direct, a public file needing no account
    fallback_page_url: str | None = None  # where to send the user if the direct fetch fails
    archive_password: str | None = None   # some community hosts ship password protected zips
    mirror_sha256: str | None = None      # integrity check for a mirrored file
    runs_installer: bool = False          # mod ships an interactive installer rather than drop-in files
    requires: list[str] = field(default_factory=list)  # patch ids that must be applied first
    replaces: list[str] = field(default_factory=list)  # patch ids to uninstall before applying
    build_guard: str | None = None     # decides, per executable, whether this patch is safe
    moved_from: list[str] = field(default_factory=list)  # paths earlier versions installed to
    expects: list[str] = field(default_factory=list)   # filenames a manually dropped archive should contain
    archive_relative_dir: str | None = None            # for archive_inject, where filelist/white_img live
    archive_variants: list[str] = field(default_factory=lambda: ["u", "c"])
    injections: list[dict] = field(default_factory=list)  # [{game_path, bundled|source}] for archive_inject
    credit: str | None = None          # attribution for a bundled third-party mod
    # [{source, target, sha256?, config?, foreign?}]. config is a settings
    # file that's the user's once it changes; foreign: refuse leaves the patch
    # out when a file of somebody else's is already where that one goes
    bundled_files: list[dict] = field(default_factory=list)
    estimated_size_gb: float | None = None
    notes: str | None = None          # developer documentation, NEVER shown to a player
    player_note: str | None = None    # user facing, shown for manual patches
    ui_job_id: str | None = None  # which job in ui/jobs.json this patch backs, if any
    # for a mod whose installer asks what to install. the mod's own menu is a
    # tree because cmd can only ask one question at a time; these are what it
    # is really asking, which is a set of independent on/off choices.
    categories: list[dict] = field(default_factory=list)
    # an extra listed among the Mods drawer's Installed while it's in, with a
    # Remove that is this patch's revert
    in_mods: bool = False
    # no longer something the patcher puts in. The entry stays so what an
    # earlier version applied still reads as in and still comes off
    retired: bool = False
    # what the Mods drawer puts in from this row's page by reading it rather
    # than running it, a .bat or a patcher exe by name: the row is in once
    # each has gone in, and its Open page watches for that many downloads
    recipes: list[str] = field(default_factory=list)
    # what a download from the page is called, as globs, for a page that isn't
    # Nexus, whose downloads carry the mod's id, and the size of the ones
    # whose size is known, which is what a percentage is out of
    download_names: list[str] = field(default_factory=list)
    download_sizes: dict = field(default_factory=dict)


@dataclass
class GameManifest:
    game_id: str
    display_name: str
    manifest_version: str
    executable_relative_path: str
    patches: list[Patch] = field(default_factory=list)
    steam_app_id: str | None = None  # lets a new game ship as json only
    ui_id: str | None = None         # matching game id in ui/jobs.json


VALID_TYPES = {"binary_patch", "asset_pack", "file_install", "nexus", "direct",
               "archive_inject", "bundled_install", "manual"}



def load_manifest(path: str | Path) -> GameManifest:
    raw = json.loads(Path(path).read_bytes())

    patches = []
    for p in raw["patches"]:
        if p["type"] not in VALID_TYPES:
            raise ValueError(f"unknown patch type {p['type']!r} on patch {p['id']!r}")
        if p["type"] == "binary_patch" and not p.get("operation"):
            raise ValueError(f"binary_patch {p['id']!r} is missing its operation field")
        if p["type"] == "asset_pack" and not p.get("source_url"):
            raise ValueError(f"asset_pack {p['id']!r} is missing its source_url field")
        if p["type"] == "file_install" and not p.get("github_repo"):
            raise ValueError(f"file_install {p['id']!r} is missing its github_repo field")
        if p["type"] == "file_install" and not p.get("install_relative_path"):
            raise ValueError(f"file_install {p['id']!r} is missing its install_relative_path field")
        if p["type"] == "nexus" and not (p.get("nexus_game_domain") and p.get("nexus_mod_id")):
            raise ValueError(f"nexus {p['id']!r} needs nexus_game_domain and nexus_mod_id")
        if p["type"] == "direct" and not p.get("download_url"):
            raise ValueError(f"direct {p['id']!r} is missing its download_url field")
        if p["type"] == "archive_inject":
            if not p.get("archive_relative_dir"):
                raise ValueError(f"archive_inject {p['id']!r} is missing archive_relative_dir")
            if not p.get("injections"):
                raise ValueError(f"archive_inject {p['id']!r} has no injections")
            for entry in p["injections"]:
                if not entry.get("game_path"):
                    raise ValueError(
                        f"archive_inject {p['id']!r}: every injection needs a game_path")
                # a retired one only has to be found and taken back off, which
                # its game paths and pins do
                if not entry.get("bundled") and not entry.get("source") and not p.get("retired"):
                    raise ValueError(
                        f"archive_inject {p['id']!r}: every injection needs either a bundled "
                        "asset or a source pattern to find in a supplied archive")
        if p["type"] == "bundled_install":
            if not p.get("install_relative_path"):
                raise ValueError(f"bundled_install {p['id']!r} is missing install_relative_path")
            if not p.get("bundled_files"):
                raise ValueError(f"bundled_install {p['id']!r} has no bundled_files")
            for entry in p["bundled_files"]:
                if not entry.get("source") or not entry.get("target"):
                    raise ValueError(
                        f"bundled_install {p['id']!r}: every file needs a source and a target")
                # an old name gets renamed onto the new one, so it's held to
                # the same rule as where the file goes
                for named in [entry["target"], *(entry.get("was") or [])]:
                    parts = str(named).replace("\\", "/").split("/")
                    if str(named).startswith(("/", "\\")) or ".." in parts or ":" in str(named):
                        raise ValueError(f"bundled_install {p['id']!r}: {named!r} has to "
                                         "stay inside the folder it installs into")
                if entry.get("was") and not entry.get("config"):
                    # a pinned file under an old name is just a stale copy,
                    # which moved_from already covers
                    raise ValueError(f"bundled_install {p['id']!r}: only a settings file "
                                     "carries an old name across")
                if entry.get("foreign") not in (None, "refuse"):
                    raise ValueError(f"bundled_install {p['id']!r}: foreign can only be refuse")
        # what Remove does is revert the one patch, by its record: files off by
        # their names, or an injection's archives back from its journals
        if p.get("in_mods") and p["type"] not in ("bundled_install", "nexus", "direct",
                                                  "file_install", "archive_inject"):
            raise ValueError(f"{p['id']!r}: in_mods is for a patch that puts files in")
        if p.get("retired") and p.get("default_enabled"):
            raise ValueError(f"{p['id']!r} is retired, so nothing can be applying it by default")
        if p["type"] == "manual" and not p.get("manual_url"):
            raise ValueError(f"manual {p['id']!r} is missing its manual_url field")

        patches.append(Patch(
            id=p["id"],
            label=p["label"],
            type=p["type"],
            default_enabled=p["default_enabled"],
            requires_backup=p.get("requires_backup", False),
            required_backup_filename=p.get("required_backup_filename"),
            operation=p.get("operation"),
            target_relative_path=p.get("target_relative_path"),
            source_url=p.get("source_url"),
            github_repo=p.get("github_repo"),
            install_relative_path=p.get("install_relative_path"),
            source_subdir=p.get("source_subdir"),
            nexus_game_domain=p.get("nexus_game_domain"),
            nexus_mod_id=str(p["nexus_mod_id"]) if p.get("nexus_mod_id") is not None else None,
            nexus_file_id=str(p["nexus_file_id"]) if p.get("nexus_file_id") is not None else None,
            manual_url=p.get("manual_url"),
            mirror_url=p.get("mirror_url"),
            download_url=p.get("download_url"),
            fallback_page_url=p.get("fallback_page_url"),
            archive_password=p.get("archive_password"),
            mirror_sha256=p.get("mirror_sha256"),
            runs_installer=p.get("runs_installer", False),
            categories=list(p.get("categories", [])),
            requires=list(p.get("requires", [])),
            replaces=list(p.get("replaces", [])),
            build_guard=p.get("build_guard"),
            moved_from=list(p.get("moved_from", [])),
            expects=list(p.get("expects", [])),
            archive_relative_dir=p.get("archive_relative_dir"),
            archive_variants=list(p.get("archive_variants", ["u", "c"])),
            injections=list(p.get("injections", [])),
            credit=p.get("credit"),
            bundled_files=list(p.get("bundled_files", [])),
            estimated_size_gb=p.get("estimated_size_gb"),
            notes=p.get("notes"),
            player_note=p.get("player_note"),
            ui_job_id=p.get("ui_job_id"),
            in_mods=bool(p.get("in_mods")),
            retired=bool(p.get("retired")),
            recipes=[str(r) for r in p.get("recipes", [])],
            download_names=[str(n) for n in p.get("download_names", [])],
            download_sizes={str(k): int(v) for k, v in (p.get("download_sizes") or {}).items()},
        ))

    return GameManifest(
        game_id=raw["game_id"],
        display_name=raw["display_name"],
        manifest_version=raw["manifest_version"],
        executable_relative_path=raw["executable_relative_path"],
        patches=patches,
        steam_app_id=raw.get("steam_app_id"),
        ui_id=raw.get("ui_id"),
    )
