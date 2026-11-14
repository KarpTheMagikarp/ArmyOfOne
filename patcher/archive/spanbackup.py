"""
Undo for edits inside enormous files, without copying the enormous file.

ensure_backup() copies the whole target before touching it. That is correct
for a 3MB exe and unusable for white_imgu.win32.bin, which runs 8 to 20GB: the
copy alone can exceed the free space on the drive, and a user patching three
games would spend 60GB to protect a few hundred kilobytes of edits.

An in-place archive edit only ever rewrites the byte span an entry occupies.
So the undo is that span's original bytes, plus the original index entry. For
Leviathan's Tears that is roughly 90KB against a 12GB container.

The journal is written and fsynced BEFORE the target is modified, because a
backup that lands after the write protects nothing. If the process dies
mid-edit, the journal on disk is what makes the archive recoverable, so its
durability matters more than its speed.

Layout, in a run folder of the game's undo store (.patcher-undo beside the install):

    <archive>.<timestamp>.journal      json: entries, offsets, lengths, and a
                                       sha256 of each span's original bytes
    <archive>.<timestamp>.spans        the original bytes, concatenated
"""

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHUNK = 1024 * 1024


class RestoreError(Exception):
    pass


class JournalUnreadable(RestoreError, ValueError):
    """
    a journal a power cut or a full disk left half written. Both families,
    because callers reading one catch ValueError and callers restoring from
    one catch RestoreError, and a TypeError out of a cut file was neither
    """


@dataclass
class Span:
    """one contiguous region of the target that is about to be overwritten"""
    offset: int
    length: int
    journal_offset: int = 0     # where the original bytes sit in the .spans file
    label: str = ""
    original: str = ""          # sha256 of those bytes, empty on older journals


@dataclass
class SpanJournal:
    target: str
    target_size: int
    created: float
    spans: list[Span] = field(default_factory=list)
    # free-form room for the caller to record what it needs to put back that
    # is not raw bytes, e.g. the original filelist entry text
    metadata: dict = field(default_factory=dict)

    def total_bytes(self) -> int:
        return sum(s.length for s in self.spans)


def _paths(store: Path, target: Path, stamp: str) -> tuple[Path, Path]:
    base = f"{target.name}.{stamp}"
    return store / f"{base}.journal", store / f"{base}.spans"


class SpanBackup:
    """
    records the original contents of regions before they are overwritten.

    usage is deliberately two-phase: record everything, commit, then write.
    committing is what fsyncs the journal, and the caller must not touch the
    target until that has happened.
    """

    def __init__(self, target: Path, store: Path, metadata: dict | None = None,
                 clock=time.time):
        self.target = Path(target)
        self.store = Path(store)
        self._stamp = f"{int(clock() * 1000):x}"
        self._journal = SpanJournal(
            target=str(self.target),
            target_size=self.target.stat().st_size if self.target.exists() else 0,
            created=clock(),
            metadata=dict(metadata or {}),
        )
        self._committed = False

    @property
    def journal_path(self) -> Path:
        return _paths(self.store, self.target, self._stamp)[0]

    @property
    def spans_path(self) -> Path:
        return _paths(self.store, self.target, self._stamp)[1]

    def record(self, offset: int, length: int, label: str = "") -> Span:
        """
        copies out the bytes currently at [offset, offset+length) so they can
        be put back later. streamed, because a span can legitimately be large
        even when the whole-file copy is not an option.
        """
        if self._committed:
            raise RestoreError("journal already committed, record before committing")
        if offset < 0 or length < 0:
            raise ValueError("offset and length must not be negative")
        end = offset + length
        if end > self._journal.target_size:
            raise ValueError(
                f"span {offset}+{length} runs past the end of {self.target.name} "
                f"({self._journal.target_size} bytes)"
            )

        self.store.mkdir(parents=True, exist_ok=True)
        span = Span(offset=offset, length=length, label=label)
        digest = hashlib.sha256()

        with self.spans_path.open("ab") as sink:
            span.journal_offset = sink.tell()
            if length:
                with self.target.open("rb") as source:
                    source.seek(offset)
                    remaining = length
                    while remaining:
                        block = source.read(min(CHUNK, remaining))
                        if not block:
                            raise RestoreError(
                                f"{self.target.name} ended early while reading span at {offset}")
                        sink.write(block)
                        digest.update(block)
                        remaining -= len(block)
        span.original = digest.hexdigest()
        self._journal.spans.append(span)
        return span

    def commit(self) -> Path:
        """
        makes the journal durable. nothing may modify the target before this
        returns: a backup written after the edit is not a backup.
        """
        self.store.mkdir(parents=True, exist_ok=True)
        payload = asdict(self._journal)
        tmp = self.journal_path.with_suffix(".journal.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # fsync both the spans and the journal. the ordering guarantee this
        # whole design rests on is worthless if either is still in a write
        # cache when the power goes.
        for path in (self.spans_path, tmp):
            if path.exists():
                with path.open("rb+") as handle:
                    os.fsync(handle.fileno())
        os.replace(tmp, self.journal_path)
        self._committed = True
        return self.journal_path

    @property
    def cost(self) -> int:
        return self._journal.total_bytes()


def load_journal(journal_path: Path) -> SpanJournal:
    try:
        return _journal(json.loads(Path(journal_path).read_bytes()))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise JournalUnreadable(f"{Path(journal_path).name} can't be read ({type(e).__name__})") from e


def _journal(data: dict) -> SpanJournal:
    return SpanJournal(
        target=data["target"],
        target_size=data["target_size"],
        created=data["created"],
        # fields a later build adds are left for it, so a journal still loads
        # after going back a version
        spans=[Span(**{k: v for k, v in s.items() if k in Span.__dataclass_fields__})
               for s in data["spans"]],
        metadata=data.get("metadata", {}),
    )


MISSING = "missing"      # the undo itself is not all there, or not what was taken
RESHAPED = "reshaped"    # the file it was taken against has changed size since


def _spans_file(journal_path: Path) -> Path:
    return journal_path.with_name(journal_path.name.replace(".journal", ".spans"))


def _sha256(handle, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    handle.seek(offset)
    while length > 0:
        block = handle.read(min(CHUNK, length))
        if not block:
            break
        digest.update(block)
        length -= len(block)
    return digest.hexdigest()


def _damaged(journal: SpanJournal, spans: Path) -> bool:
    """whether the saved bytes are gone, cut short, or not the ones that were taken"""
    try:
        if spans.stat().st_size < max((s.journal_offset + s.length for s in journal.spans),
                                      default=0):
            return True
        with spans.open("rb") as saved:
            return any(s.original and _sha256(saved, s.journal_offset, s.length) != s.original
                       for s in journal.spans)
    except OSError:
        return True


def check(journal_path: Path, target: Path | None = None) -> str:
    """
    "" when restore() would go ahead, otherwise MISSING or RESHAPED, without
    writing anything.

    A revert touches two files per archive and has to know both halves can go
    back before the first one does, so this is the half of restore() that
    only looks. Saved bytes that aren't the ones taken are MISSING, since
    writing them back undoes nothing.

    What's in the spans now decides nothing: they go back whole, and the
    index decides whether that leaves the pair agreeing with itself and
    whether the entries are still ours. So a revert cut short partway through
    a span can always be run again.
    """
    journal_path = Path(journal_path)
    try:
        journal = load_journal(journal_path)
    except (OSError, ValueError, KeyError, TypeError):
        return MISSING
    destination = Path(target) if target else Path(journal.target)
    if not destination.is_file() or _damaged(journal, _spans_file(journal_path)):
        return MISSING
    return "" if destination.stat().st_size == journal.target_size else RESHAPED


def matches(journal_path: Path, target: Path | None = None) -> bool:
    """whether the target still holds every recorded span, so restore() would change nothing"""
    journal_path = Path(journal_path)
    journal = load_journal(journal_path)
    spans = _spans_file(journal_path)
    destination = Path(target) if target else Path(journal.target)
    with spans.open("rb") as saved, destination.open("rb") as live:
        for span in journal.spans:
            saved.seek(span.journal_offset)
            live.seek(span.offset)
            remaining = span.length
            while remaining:
                size = min(CHUNK, remaining)
                if saved.read(size) != live.read(size):
                    return False
                remaining -= size
    return True


def restore(journal_path: Path, target: Path | None = None) -> int:
    """
    puts every recorded span back. returns how many bytes were written.

    applied in reverse order so overlapping spans recorded in sequence unwind
    correctly: the earliest recording holds the truly original bytes, so it
    has to be the last one written.
    """
    journal_path = Path(journal_path)
    journal = load_journal(journal_path)
    destination = Path(target) if target else Path(journal.target)
    spans_file = _spans_file(journal_path)

    if not destination.exists():
        raise RestoreError(f"{destination} is gone, nothing to restore into")
    if not spans_file.exists():
        raise RestoreError(f"missing span data at {spans_file}")
    # all of it checked before any goes in, or a cut .spans file gets written
    # up to the cut and no second run can finish it
    if _damaged(journal, spans_file):
        raise RestoreError(f"the saved bytes for {destination.name} aren't the ones that were "
                           "taken, so writing them back would undo nothing")

    current = destination.stat().st_size
    if current != journal.target_size:
        # a resized target means this journal describes a different file. a
        # blind restore would write the old bytes at offsets that now mean
        # something else, turning a recoverable situation into a ruined one.
        raise RestoreError(
            f"{destination.name} is {current} bytes but the journal was taken at "
            f"{journal.target_size}. refusing to restore into a file that changed shape"
        )

    written = 0
    with spans_file.open("rb") as source, destination.open("rb+") as sink:
        for span in reversed(journal.spans):
            source.seek(span.journal_offset)
            sink.seek(span.offset)
            remaining = span.length
            while remaining:
                block = source.read(min(CHUNK, remaining))
                if not block:
                    raise RestoreError(f"span data ended early for offset {span.offset}")
                sink.write(block)
                remaining -= len(block)
                written += len(block)
        sink.flush()
        os.fsync(sink.fileno())
    return written


def discard(journal_path: Path) -> None:
    """drops a journal once its edit is known good"""
    journal_path = Path(journal_path)
    journal_path.unlink(missing_ok=True)
    _spans_file(journal_path).unlink(missing_ok=True)


def journals_for(store: Path, target: Path) -> list[Path]:
    """every journal recorded against a target, newest last"""
    store = Path(store)
    if not store.is_dir():
        return []
    return sorted(store.glob(f"{Path(target).name}.*.journal"))
