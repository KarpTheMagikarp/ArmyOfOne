import shutil
import zlib
from pathlib import Path

import pytest

from patcher.archive import crypt, filelist
from patcher.archive.entry import SECTOR
from patcher.archive.inject import Replacement
from patcher.archive.transaction import (
    TransactionError,
    apply_injection,
    discover,
    prepare,
)

SAMPLE = Path(__file__).resolve().parent / "data" / "filelistu_sample.bin"
WEATHER07 = "vfx/field/weather/weather07/veffs.jp.win32.imgb"
WEATHER08 = "vfx/field/weather/weather08/veffs.jp.win32.imgb"
ARCHIVE_DIR = "alba_data/sys"


def build_install(tmp_path, variants=("u", "c")):
    """
    a real install shape: the actual XIII-2 index, plus a white_img big enough
    to hold the entries it describes.
    """
    if not SAMPLE.is_file():
        pytest.skip("no sample filelist")
    raw = SAMPLE.read_bytes()
    index = filelist.parse(raw)

    root = tmp_path / "game"
    sys_dir = root / ARCHIVE_DIR
    sys_dir.mkdir(parents=True)

    highest = max(index.entries(), key=lambda e: e.sector_offset)
    size = highest.byte_offset + highest.sector_span * SECTOR

    for variant in variants:
        shutil.copyfile(SAMPLE, sys_dir / f"filelist{variant}.win32.bin")
        white = sys_dir / f"white_img{variant}.win32.bin"
        with white.open("wb") as handle:
            handle.truncate(size)
        # put real, inflatable payloads where the two weather entries live, so
        # verification has something genuine to read back
        with white.open("rb+") as handle:
            for path in (WEATHER07, WEATHER08):
                _, entry = index.locate(path)
                handle.seek(entry.byte_offset)
                handle.write(zlib.compress((b"original rain" * 7000)[:entry.uncompressed], 9))
    return root


def digest(path: Path) -> str:
    """
    hashes in chunks. white_img is 1.5GB, so read_bytes() on it is an OOM
    kill rather than a test failure.
    """
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def payload(size=90512, entropy=30000, seed=1234):
    """
    a stand-in for the real veffs.jp.win32.imgb: 90512 bytes that deflate to
    roughly 30KB.

    the compression ratio has to be realistic, not just the size. a payload of
    repeating text compresses to a few hundred bytes, which makes the stored
    size one hex digit shorter, which changes the record length, which
    replace_entry() correctly refuses. an unrealistic fixture would have
    "failed" on a guard that is working exactly as designed.
    """
    import random as _r
    rng = _r.Random(seed)
    block = bytes(rng.randrange(256) for _ in range(entropy))
    return (block * (size // len(block) + 1))[:size]


def test_both_language_archives_are_discovered(tmp_path):
    root = build_install(tmp_path)
    pairs = discover(root, ARCHIVE_DIR)
    assert [p.variant for p in pairs] == ["u", "c"]


def test_an_english_only_install_is_not_refused(tmp_path):
    """not every release ships both; that is not an error"""
    root = build_install(tmp_path, variants=("u",))
    assert [p.variant for p in discover(root, ARCHIVE_DIR)] == ["u"]


def test_the_full_injection_patches_both_archives(tmp_path):
    root = build_install(tmp_path)
    reps = [Replacement(WEATHER07, payload()), Replacement(WEATHER08, payload())]

    result = apply_injection(root, ARCHIVE_DIR, reps, store=tmp_path / "undo")

    assert result["ok"] is True
    assert result["archives"] == ["u", "c"]
    for variant in ("u", "c"):
        sys_dir = root / ARCHIVE_DIR
        index = filelist.parse((sys_dir / f"filelist{variant}.win32.bin").read_bytes())
        with (sys_dir / f"white_img{variant}.win32.bin").open("rb") as handle:
            for path in (WEATHER07, WEATHER08):
                _, entry = index.locate(path)
                handle.seek(entry.byte_offset)
                assert zlib.decompress(handle.read(entry.stored)) == payload()


def test_the_rewritten_index_still_verifies_against_itself(tmp_path):
    """the next run's pre-flight must pass on the state we just created"""
    root = build_install(tmp_path)
    apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                    store=tmp_path / "undo")

    for variant in ("u", "c"):
        raw = (root / ARCHIVE_DIR / f"filelist{variant}.win32.bin").read_bytes()
        filelist.verify_roundtrip(raw)


def test_a_failure_on_the_second_archive_rolls_back_the_first(tmp_path):
    """
    the whole point. patching English and failing Japanese would leave a game
    where the rain is fixed in one language and not the other.
    """
    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    before = {v: digest(sys_dir / f"white_img{v}.win32.bin") for v in ("u", "c")}
    index_before = {v: (sys_dir / f"filelist{v}.win32.bin").read_bytes() for v in ("u", "c")}

    # make the Japanese white_img unwritable partway through by deleting it
    # after phase 1 has validated everything
    import patcher.archive.transaction as tx
    real_write = tx._write_payloads
    calls = []

    def explode(undo, writes, store, **kwargs):
        calls.append(undo.pair.variant)
        if undo.pair.variant == "c":
            raise OSError("disk went away")
        return real_write(undo, writes, store, **kwargs)

    tx._write_payloads = explode
    try:
        with pytest.raises(OSError):
            apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                            store=tmp_path / "undo")
    finally:
        tx._write_payloads = real_write

    assert calls == ["u", "c"]
    for variant in ("u", "c"):
        assert digest(sys_dir / f"white_img{variant}.win32.bin") == before[variant], \
            f"{variant} archive was not rolled back"
        assert (sys_dir / f"filelist{variant}.win32.bin").read_bytes() == index_before[variant], \
            f"{variant} index was not rolled back"


def test_a_failed_verification_rolls_everything_back(tmp_path):
    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    before = digest(sys_dir / "white_imgu.win32.bin")

    import patcher.archive.transaction as tx
    real_verify = tx._verify
    tx._verify = lambda pair, writes: (_ for _ in ()).throw(TransactionError("bad readback"))
    try:
        with pytest.raises(TransactionError, match="bad readback"):
            apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                            store=tmp_path / "undo")
    finally:
        tx._verify = real_verify

    assert digest(sys_dir / "white_imgu.win32.bin") == before


def test_a_payload_too_large_is_refused_before_anything_is_written(tmp_path):
    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    before = digest(sys_dir / "white_imgu.win32.bin")
    # genuinely incompressible and far over the 32KB span. a repeating pattern
    # would deflate to almost nothing and fit, which is not the case under test.
    huge = payload(size=200_000, entropy=200_000, seed=99)

    with pytest.raises(TransactionError, match="repack"):
        apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, huge)],
                        store=tmp_path / "undo")

    assert digest(sys_dir / "white_imgu.win32.bin") == before
    # the lock that keeps the HD install out is all it may leave
    assert not (tmp_path / "undo").exists() or \
        [p.name for p in (tmp_path / "undo").iterdir()] == ["archives.lock"]


def test_an_index_we_cannot_reproduce_aborts_the_whole_thing(tmp_path):
    """
    if the pre-flight fails on ANY archive, none of them are touched. an index
    we do not fully understand is one we have no business rewriting.
    """
    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    damaged = bytearray((sys_dir / "filelistc.win32.bin").read_bytes())
    damaged[crypt.HEADER_SIZE + 900] ^= 0xFF
    (sys_dir / "filelistc.win32.bin").write_bytes(bytes(damaged))
    before = digest(sys_dir / "white_imgu.win32.bin")

    with pytest.raises(TransactionError):
        apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                        store=tmp_path / "undo")

    assert digest(sys_dir / "white_imgu.win32.bin") == before


def test_an_unknown_path_aborts_before_writing(tmp_path):
    root = build_install(tmp_path)
    before = digest(root / ARCHIVE_DIR / "white_imgu.win32.bin")

    with pytest.raises(TransactionError, match="no entry"):
        apply_injection(root, ARCHIVE_DIR, [Replacement("not/real.imgb", payload())],
                        store=tmp_path / "undo")

    assert digest(root / ARCHIVE_DIR / "white_imgu.win32.bin") == before


def test_the_undo_cost_is_tiny_next_to_the_archives(tmp_path):
    root = build_install(tmp_path)
    apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                    store=tmp_path / "undo")

    undo_bytes = sum(p.stat().st_size for p in (tmp_path / "undo").iterdir())
    archive_bytes = sum((root / ARCHIVE_DIR / f"white_img{v}.win32.bin").stat().st_size
                        for v in ("u", "c"))
    assert undo_bytes < archive_bytes / 100


def test_prepare_writes_nothing_at_all(tmp_path):
    root = build_install(tmp_path)
    snapshot = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}

    prepare(discover(root, ARCHIVE_DIR), [Replacement(WEATHER07, payload())])

    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == snapshot



def test_two_injections_sharing_a_store_keep_separate_index_backups(tmp_path):
    """
    The one that could destroy a save-game folder's worth of work.

    _write_index names its backup after the archive, not after the run, so two
    injections into the same store had the second one's copy land on the first
    one's filename. The second copy is an index that is ALREADY injected, so a
    later revert restored the modded index over the vanilla data, verified
    nothing, and reported success.

    It needed two injections into one store to happen, which is exactly what
    unifying the store made possible: before, Apply wrote to the game folder
    and a dropped file wrote under the state folder, so the two never met.
    Fixing that divergence without this would have created the collision.
    """
    root = build_install(tmp_path, variants=("u",))
    store = tmp_path / "undo"
    vanilla = digest(root / ARCHIVE_DIR / "filelistu.win32.bin")

    first = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload(seed=1))],
                            store=store, variants=("u",))
    second = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER08, payload(seed=2))],
                             store=store, variants=("u",))

    first_backup = Path(first["index_backups"][0]["backup"])
    second_backup = Path(second["index_backups"][0]["backup"])

    assert first_backup != second_backup, "the second run overwrote the first run's backup"
    assert first_backup.is_file() and second_backup.is_file()
    # and the first one is still the untouched index, which is the whole point
    assert digest(first_backup) == vanilla
    assert digest(second_backup) != vanilla


def test_a_journal_and_its_index_backup_live_in_the_same_run_folder(tmp_path):
    """
    revert reads them as a pair. splitting them across folders would work right
    up until somebody cleaned one of the two out.
    """
    root = build_install(tmp_path, variants=("u",))
    store = tmp_path / "undo"

    result = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                             store=store, variants=("u",))

    journal = Path(result["undo"][0])
    backup = Path(result["index_backups"][0]["backup"])

    assert journal.parent == backup.parent
    assert journal.parent.parent == store, "the run folder sits directly in the store"


def test_dropping_the_undo_leaves_no_empty_run_folder_behind(tmp_path):
    """one directory per injection accumulating forever is its own small mess"""
    root = build_install(tmp_path, variants=("u",))
    store = tmp_path / "undo"

    apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                    store=store, variants=("u",), keep_undo=False)

    # the lock stays: deleting it on the way out lets two runs hold two of them
    assert sorted(p.name for p in store.iterdir()) == ["archives.lock"]


def test_an_injection_waits_its_turn_behind_the_hd_install(tmp_path):
    """
    XIII-2's HD install rewrites this same white_img and its index, and an
    injection that lands in the middle is written over by an index read before it
    """
    from patcher.mods.hd_install import exclusive

    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    store = tmp_path / "undo"
    before = {p.name: digest(p) for p in sys_dir.iterdir()}

    with exclusive(store):
        with pytest.raises(TransactionError, match="Nothing has been changed"):
            apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())], store=store)

    assert {p.name: digest(p) for p in sys_dir.iterdir()} == before
    assert apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                           store=store)["ok"]


def test_a_pair_that_already_holds_the_payload_is_left_alone(tmp_path):
    """
    written again, its journal would keep the payload as its own original,
    and nothing could ever take it off that pair
    """
    from patcher.archive.transaction import carry_undo

    root = build_install(tmp_path)
    sys_dir = root / ARCHIVE_DIR
    first = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                            store=tmp_path / "undo", variants=("u",))
    earlier = carry_undo(first, None)
    before = {p.name: digest(p) for p in sys_dir.iterdir()}

    again = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                            store=tmp_path / "undo")

    assert [k["variant"] for k in again["kept"]] == ["u"] and again["archives"] == ["c"]
    assert digest(sys_dir / "white_imgu.win32.bin") == before["white_imgu.win32.bin"]
    assert digest(sys_dir / "filelistu.win32.bin") == before["filelistu.win32.bin"]
    recorded = carry_undo(again, earlier)
    assert set(earlier["journals"]) < set(recorded["journals"])
    assert len(recorded["journals"]) == 2 and recorded["archives"] == ["c", "u"]
    assert {Path(b["target"]).name for b in recorded["index_backups"]} == {
        "filelistu.win32.bin", "filelistc.win32.bin"}


def test_a_run_with_nothing_left_to_write_leaves_no_run_folder(tmp_path):
    root = build_install(tmp_path, variants=("u",))
    store = tmp_path / "undo"
    apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())], store=store,
                    variants=("u",))
    runs = sorted(p.name for p in store.iterdir() if p.is_dir())

    again = apply_injection(root, ARCHIVE_DIR, [Replacement(WEATHER07, payload())],
                            store=store, variants=("u",))

    assert again["undo"] == [] and again["archives"] == []
    assert sorted(p.name for p in store.iterdir() if p.is_dir()) == runs
