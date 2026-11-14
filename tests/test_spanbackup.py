import json

import pytest

from patcher.archive.spanbackup import (
    RestoreError,
    SpanBackup,
    discard,
    journals_for,
    load_journal,
    restore,
)


def make_target(tmp_path, size=200_000, fill=b"A"):
    target = tmp_path / "white_imgu.win32.bin"
    target.write_bytes(fill * size)
    return target


def test_backup_cost_is_the_span_not_the_file(tmp_path):
    """
    the entire reason this exists: a 20GB archive must not need a 20GB copy to
    be undoable.
    """
    target = make_target(tmp_path, 5_000_000)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(1_000_000, 90_000, label="veffs.jp.win32.imgb")
    backup.commit()

    assert backup.cost == 90_000
    on_disk = sum(p.stat().st_size for p in (tmp_path / "spans").iterdir())
    assert on_disk < 100_000, "the journal must stay tiny next to a 5MB target"
    assert on_disk < target.stat().st_size / 50


def test_a_recorded_span_restores_exactly(tmp_path):
    target = make_target(tmp_path)
    original = target.read_bytes()

    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(1000, 500)
    journal = backup.commit()

    with target.open("rb+") as handle:
        handle.seek(1000)
        handle.write(b"Z" * 500)
    assert target.read_bytes() != original

    written = restore(journal)

    assert written == 500
    assert target.read_bytes() == original


def test_several_spans_all_come_back(tmp_path):
    target = make_target(tmp_path)
    original = target.read_bytes()

    backup = SpanBackup(target, tmp_path / "spans")
    for offset in (0, 5000, 150_000):
        backup.record(offset, 1000)
    journal = backup.commit()

    with target.open("rb+") as handle:
        for offset in (0, 5000, 150_000):
            handle.seek(offset)
            handle.write(b"X" * 1000)

    restore(journal)
    assert target.read_bytes() == original


def test_overlapping_spans_unwind_in_the_right_order(tmp_path):
    """
    the earliest recording holds the truly original bytes, so it has to be
    written last. applying them forwards would leave the second recording's
    already-modified content in place.
    """
    target = make_target(tmp_path, 10_000)
    original = target.read_bytes()

    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(100, 200)                 # original A's
    with target.open("rb+") as handle:      # something else edits it
        handle.seek(100)
        handle.write(b"B" * 200)
    backup.record(150, 200)                 # now holds B's, then A's
    journal = backup.commit()

    with target.open("rb+") as handle:
        handle.seek(100)
        handle.write(b"C" * 250)

    restore(journal)
    assert target.read_bytes() == original


def test_restoring_into_a_resized_file_is_refused(tmp_path):
    """
    a different-sized target means the journal describes a different file.
    writing old bytes at offsets that now mean something else turns a
    recoverable situation into a ruined one.
    """
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(0, 100)
    journal = backup.commit()

    with target.open("ab") as handle:
        handle.write(b"extra")

    with pytest.raises(RestoreError, match="changed shape"):
        restore(journal)


def test_a_span_past_the_end_is_rejected_at_record_time(tmp_path):
    target = make_target(tmp_path, 1000)
    backup = SpanBackup(target, tmp_path / "spans")
    with pytest.raises(ValueError, match="past the end"):
        backup.record(900, 200)


def test_recording_after_commit_is_refused(tmp_path):
    """a backup written after the edit protects nothing"""
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(0, 10)
    backup.commit()
    with pytest.raises(RestoreError, match="already committed"):
        backup.record(20, 10)


def test_metadata_carries_the_original_index_entry(tmp_path):
    """
    an archive edit changes bytes AND the filelist entry describing them.
    restoring one without the other leaves the container inconsistent.
    """
    target = make_target(tmp_path)
    entry = "142217850|160|24617:92e100:92e100:vfx/field/weather/weather07/veffs.jp.win32.imgb"
    backup = SpanBackup(target, tmp_path / "spans", metadata={"entry": entry, "game": "ff13-2"})
    backup.record(0, 10)
    journal = backup.commit()

    loaded = load_journal(journal)
    assert loaded.metadata["entry"] == entry
    assert loaded.metadata["game"] == "ff13-2"


def test_the_journal_exists_on_disk_before_any_edit(tmp_path):
    """if the process dies mid-write, the journal is the only way back"""
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(0, 100)
    journal = backup.commit()

    assert journal.exists()
    assert json.loads(journal.read_text(encoding="utf-8"))["spans"][0]["length"] == 100
    assert backup.spans_path.exists()


def test_zero_length_span_is_harmless(tmp_path):
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(50, 0)
    journal = backup.commit()
    assert restore(journal) == 0


def test_discard_removes_both_files(tmp_path):
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(0, 10)
    journal = backup.commit()
    assert journals_for(tmp_path / "spans", target) == [journal]

    discard(journal)

    assert not journal.exists()
    assert not backup.spans_path.exists()
    assert journals_for(tmp_path / "spans", target) == []


def test_restore_without_span_data_fails_loudly(tmp_path):
    target = make_target(tmp_path)
    backup = SpanBackup(target, tmp_path / "spans")
    backup.record(0, 10)
    journal = backup.commit()
    backup.spans_path.unlink()

    with pytest.raises(RestoreError, match="missing span data"):
        restore(journal)
