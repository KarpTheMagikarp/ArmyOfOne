"""
builds assets/manifest.json, the file index.html fetches at boot.

ui/jobs.json is what the player reads. manifests/*.json is what the patcher can
actually do. this stamps one onto the other so the UI can tell a wired job from
a placeholder without either side importing the other.

    python tools/build_ui_manifest.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wired_job_ids(manifest_dir: Path) -> dict[str, set[str]]:
    """ui game id -> the job ids that have a real manifest entry behind them"""
    linked = {}
    for path in sorted(manifest_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        ui_id = raw.get("ui_id")
        if not ui_id:
            continue
        linked[ui_id] = {p["ui_job_id"] for p in raw["patches"] if p.get("ui_job_id")}
    return linked


def job_categories(manifest_dir: Path) -> dict[str, dict]:
    """
    ui game id -> {job id: categories}, for a job whose installer asks what to
    install.

    same reason as `wired`: the UI needs to render the choices and the backend
    decides what they are, so it gets stamped across rather than typed twice.
    A second copy in jobs.json is the drift this whole script exists to stop.
    """
    found = {}
    for path in sorted(manifest_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        ui_id = raw.get("ui_id")
        if not ui_id:
            continue
        found[ui_id] = {p["ui_job_id"]: p["categories"] for p in raw["patches"]
                        if p.get("ui_job_id") and p.get("categories")}
    return found


def caught_job_ids(manifest_dir: Path) -> dict[str, set[str]]:
    """
    ui game id -> the jobs the Mods drawer can take straight from a download:
    a Nexus or direct mod with no installer menu of its own, or one whose .bat
    or patcher it reads (recipes) wherever its page is. Worth getting's Open
    page watches Downloads for those and puts what lands in like a drop, where
    anything else there just opens its page.
    """
    found = {}
    for path in sorted(manifest_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        ui_id = raw.get("ui_id")
        if not ui_id:
            continue
        found[ui_id] = {p["ui_job_id"] for p in raw["patches"]
                        if p.get("ui_job_id") and not p.get("categories")
                        and (p["type"] in ("nexus", "direct") or p.get("recipes"))}
    return found


def stamp(paths) -> int:
    """
    the newest mtime across everything this is built from, as whole seconds.

    Not a wall clock: two builds of the same sources should produce the same
    number, or every rebuild would look newer than an identical one and the
    comparison in loadAssets would be noise. Seconds because JSON is going to
    carry it and nanoseconds buy nothing here.
    """
    newest = 0
    for path in paths:
        try:
            newest = max(newest, int(path.stat().st_mtime))
        except OSError:
            continue
    return newest


def build() -> Path:
    jobs = json.loads((ROOT / "ui" / "jobs.json").read_text(encoding="utf-8"))
    linked = wired_job_ids(ROOT / "manifests")
    options = job_categories(ROOT / "manifests")
    caught = caught_job_ids(ROOT / "manifests")

    # assets/manifest.json is generated and gitignored, so a source drop leaves
    # whatever was there before. The page fetched that and won with it, which
    # is how a category unlocked in the source still read NOT YET on somebody
    # else's machine. Both copies carry this now and the page takes the newer.
    jobs["built"] = stamp([ROOT / "ui" / "jobs.json",
                           *sorted((ROOT / "manifests").glob("*.json"))])

    for game in jobs["games"]:
        ids = linked.get(game["id"], set())
        choices = options.get(game["id"], {})
        for job in game["jobs"]:
            if job["id"] in choices:
                job["categories"] = choices[job["id"]]
            # wired means a manifest entry exists for it. asset_pack entries are
            # wired but the engine still reports them as not built yet, that's a
            # separate thing and the UI shouldn't guess about it.
            job["wired"] = job["id"] in ids
            if job.get("kind") == "extra" and job["id"] in caught.get(game["id"], ()):
                job["catches"] = True

    out_path = ROOT / "assets" / "manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(jobs, indent=2) + "\n", encoding="utf-8")
    sync_fallback(jobs)
    return out_path


def sync_fallback(jobs: dict) -> bool:
    """
    rewrites the copy of this baked into index.html.

    index.html carries FALLBACK_MANIFEST for when the fetch fails, and it was
    hand-maintained, so it drifted: it was missing the 4GB compatibility job
    entirely, still listed two audio fixes that had been deleted, and disagreed
    about whether XIII-2's audio fix was required. That means the job list
    changes shape depending on whether a file loaded, which is the worst kind
    of intermittent to chase.

    Only the JSON literal is replaced. Everything else in the file, including
    the stylesheet and the authored markup, is untouched.
    """
    path = ROOT / "ui" / "index.html"
    text = path.read_text(encoding="utf-8")

    anchor = "const FALLBACK_MANIFEST = "
    start = text.index(anchor) + len(anchor)
    depth, i = 0, start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1

    rendered = json.dumps(jobs, indent=2)
    if text[start:i + 1] == rendered:
        return False
    path.write_text(text[:start] + rendered + text[i + 1:], encoding="utf-8")
    return True


if __name__ == "__main__":
    written = build()
    print(f"wrote {written} and synced the fallback in ui/index.html")
