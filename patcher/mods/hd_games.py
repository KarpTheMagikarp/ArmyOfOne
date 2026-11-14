"""
The two HD mods the app installs, behind one face.

The shell and the bridge run the same flow for both: identify a drop, show
the categories, survey the game, install, record. What differs is which mod
a folder is, how its steps are built and how "already in the game" is told,
and each game's module already answers those. This only picks the module.
"""

from pathlib import Path


class Xiii:
    job_id = "hdproject"
    name = "the HD Project"

    def detect(self, root):
        from patcher.mods.hd_recipe import detect
        return detect(Path(root))

    def holds(self, names) -> bool:
        from patcher.mods.hd_recipe import archive_holds_project
        return archive_holds_project(names)

    def install(self, install_dir, found, categories, selected, store, **kw):
        from patcher.mods.hd_install import install
        root = Path(found["root"])
        return install(Path(install_dir), root, Path(found["data"]), root / "SupportFiles",
                       categories, selected, store=store, **kw)

    def precheck(self, install_dir, found, categories, selected, prep) -> None:
        """what install() would refuse, asked before a rebuild restores prep's backup"""
        from patcher.mods.hd_install import build_plan
        build_plan(Path(install_dir), Path(found["root"]), Path(found["data"]), categories,
                   selected, prep.variant)

    def applied(self, install_dir, found, categories, prep):
        """(Applied, whether a backup exists to compare against)"""
        from patcher.mods.hd_state import applied_categories

        vanilla = prep.backup_dir / prep.archives[0].name if prep.backup_dir is not None else None
        backed_up = vanilla is not None and vanilla.is_file()
        state = applied_categories(prep.archives[0], vanilla if backed_up else None,
                                   Path(found["data"]), categories, mod_root=Path(found["root"]))
        return state, backed_up

    def cache_doubt(self, found, variant) -> str:
        from patcher.mods import hd_vanilla
        from patcher.mods.hd_layout import XIII
        from patcher.mods.hd_preflight import cache_not_vanilla

        # with the shipped sizes the seed caches a modded copy again from a
        # clean one and the plan leaves out what has none, so only a build
        # without them needs to stop here
        if hd_vanilla.known(XIII):
            return ""
        return cache_not_vanilla(Path(found["root"]), variant)

    def applied_in(self, install_dir, found, categories, variant):
        """what's in one voice set, without prepare()'s checks on the game, or None"""
        from patcher.mods.hd_install import archive_pair
        from patcher.mods.hd_state import applied_categories
        from patcher.paths import undo_store_for

        index = archive_pair(Path(install_dir), variant)[0]
        vanilla = undo_store_for(Path(install_dir)) / "hdproject" / index.name
        if not index.is_file():
            return None
        state = applied_categories(index, vanilla if vanilla.is_file() else None,
                                   Path(found["data"]), categories, mod_root=Path(found["root"]))
        state.categories |= self.zones_in(install_dir, found, categories, variant)
        return state

    def zones_in(self, install_dir, found, categories, variant) -> set:
        """
        the zone categories in one voice set. applied_categories reads the scr
        index, and a zone's art is its own blob beside it, one per set
        """
        from patcher.mods.hd_state import zones_installed
        from patcher.paths import undo_store_for

        store = undo_store_for(Path(install_dir)) / "hdproject"
        return {c["id"] for c in categories
                if zones_installed(Path(install_dir), variant, store, Path(found["data"]), c)}

    def zone_categories(self, found, categories) -> set:
        from patcher.mods.hd_install import zone_categories
        return zone_categories(Path(found["data"]), categories)

    def touched(self, found, categories, ids):
        """
        what installing `ids` rebuilds, as hd_mirror.Fresh: each id's own folder,
        and its zones. None without the mod folder, whose lists name the ids
        """
        from patcher.mods.hd_install import zones_wanted
        from patcher.mods.hd_mirror import Fresh
        from patcher.mods.hd_state import _model_paths

        if found is None:
            return None
        data, ids = Path(found["data"]), set(ids)
        folders = {path.rsplit("/bin/", 1)[0] + "/" for c in categories if c["id"] in ids
                   for path in _model_paths(data, c)}
        return Fresh(tuple(sorted(folders)), frozenset(zones_wanted(data, categories, ids)))

    def zones_of(self, found, categories, ids) -> list:
        """the zones installing `ids` writes"""
        from patcher.mods.hd_install import zones_wanted
        return zones_wanted(Path(found["data"]), categories, set(ids))


class Xiii2:
    job_id = "hdproject2"
    name = "FFXIII-2 HD"

    def detect(self, root):
        from patcher.mods.hd2_recipe import detect
        return detect(Path(root))

    def holds(self, names) -> bool:
        from patcher.mods.hd2_recipe import archive_holds
        return archive_holds(names)

    def install(self, install_dir, found, categories, selected, store, **kw):
        from patcher.mods.hd2_install import install
        return install(Path(install_dir), found, categories, selected, store, **kw)

    def precheck(self, install_dir, found, categories, selected, prep) -> None:
        """what install() would refuse, asked before a rebuild restores prep's backup"""
        from patcher.mods.hd2_install import precheck
        precheck(Path(install_dir), found, categories, selected, prep.variant,
                 restoring=prep.backup_dir)

    def applied(self, install_dir, found, categories, prep):
        from patcher.mods.hd2_install import applied

        vanilla = prep.backup_dir / prep.archives[0].name if prep.backup_dir is not None else None
        return (applied(Path(install_dir), found, categories, prep.variant),
                vanilla is not None and vanilla.is_file())

    def cache_doubt(self, found, variant) -> str:
        from patcher.mods import hd_vanilla
        from patcher.mods.hd2_install import cache_doubt
        from patcher.mods.hd_layout import XIII2

        # see Xiii.cache_doubt
        if hd_vanilla.known(XIII2):
            return ""
        return cache_doubt(Path(found["root"]), variant)

    def applied_in(self, install_dir, found, categories, variant):
        from patcher.mods.hd2_install import applied
        return applied(Path(install_dir), found, categories, variant)

    def zones_in(self, install_dir, found, categories, variant) -> set:
        # applied() counts the zones already
        return set()

    def zone_categories(self, found, categories) -> set:
        return {c["id"] for c in categories if c.get("zones")}

    def touched(self, found, categories, ids):
        """
        what installing `ids` rebuilds, as hd_mirror.Fresh: the group folders,
        since which models the injector changes inside them is up to their .trb
        """
        from patcher.mods.hd_mirror import Fresh

        chosen = [c for c in categories if c["id"] in set(ids)]
        groups = {f"chr/{g}/".lower() for c in chosen for g in c.get("groups") or []}
        return Fresh(tuple(sorted(groups)),
                     None if any(c.get("zones") for c in chosen) else frozenset())

    def zones_of(self, found, categories, ids) -> list:
        """the zones installing `ids` writes: every one the mod ships art for"""
        chosen = [c for c in categories if c["id"] in set(ids)]
        return list(found.get("zones") or []) if any(c.get("zones") for c in chosen) else []

    def survey(self, install_dir, found, categories) -> dict:
        from patcher.mods.hd2_install import applied, sizes

        state = applied(Path(install_dir), found, categories)
        return {"sizes": sizes(Path(install_dir), found, categories),
                "applied": sorted(state.categories), "verified": state.verified,
                "suspect": {}}


GAMES = (Xiii(), Xiii2())


def for_job(job_id: str):
    return next((g for g in GAMES if g.job_id == job_id), None)


def identify(root):
    """(game, detect result) for whichever mod this folder is, or (None, None)"""
    for game in GAMES:
        found = game.detect(root)
        if found is not None:
            return game, found
    return None, None
