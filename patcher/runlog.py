"""
A tool run's transcript on disk, since the terminal pane drops old lines and
an extraction prints tens of thousands. Written through, not buffered, so a
dying process doesn't take the explanation with it.

Each step keeps its first and last lines with a count of what went between
(a zones run is otherwise ~28,000 `Extracted <path>` lines), so ff13tool's
totals still land. stderr is never dropped, and the output just before it is
flushed first, since that's the context a complaint needs.
"""

import time
from collections import deque
from pathlib import Path

MARKS = {"out": "  ", "err": "! ", "cmd": "$ "}
KEEP = 10          # runs, oldest deleted past this
# steps named in the closing summary. A real run is fifteen hundred steps and
# one slow drive or a scanner on one archive is what a "why so slow" log needs
SLOWEST = 5

# per step, on the out stream only. Twelve at the end because ff13tool puts its
# counts there ("Extracted 3076 files in total", "2928 uncompressed, 148
# copied") and those are the lines that say whether it finished.
HEAD_LINES = 8
TAIL_LINES = 12


class RunLog:
    """
    one file per run. Failures are silent by design: a log that cannot be
    written is not a reason to refuse an install.
    """

    def __init__(self, path: Path, header: dict | None = None):
        # named to the second, and a voice switch's copy can hand over to the
        # tools inside one, so the second run gets a name of its own rather
        # than writing over the first one's transcript
        self.path = Path(path)
        n = 1
        while self.path.exists():
            n += 1
            self.path = Path(path).with_name(f"{Path(path).stem}-{n}{Path(path).suffix}")
        self._file = None
        self._seen = 0                      # out lines in the current step
        self._held = deque(maxlen=TAIL_LINES)
        self._dropped = 0
        self._began = time.monotonic()
        self._current = None                # (heading, started) of the open step
        self._took = []                     # (seconds, heading) of the closed ones
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _prune(self.path.parent)
            self._file = self.path.open("w", encoding="utf-8", errors="replace")
            self._file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            for key, value in (header or {}).items():
                self._file.write(f"{key}: {value}\n")
            self._file.flush()
        except OSError:
            self._file = None

    def step(self, label: str, index: int, total: int) -> None:
        """
        a heading, and the boundary the line budget is counted against.

        Its own call rather than a write() with a marker on it, because a step
        header is not something a tool said and reading it as one is what made
        the old format unscannable: `$` meant both "here is a step" and "here
        is the command it ran".
        """
        self._flush_held()
        now = time.monotonic()
        self._timed(now)
        heading = f"[{index + 1}/{total}] {label}"
        self._current = (heading, now)
        if self._file is None:
            return
        try:
            self._file.write(f"\n{heading}\n")
            self._file.flush()
        except (OSError, ValueError):
            self._file = None

    def write(self, text: str, stream: str = "out") -> None:
        if self._file is None:
            return
        if stream == "out":
            # the ordinary case, and the only one with a budget
            if self._seen < HEAD_LINES:
                self._seen += 1
                self._put(MARKS["out"] + text)
            else:
                if len(self._held) == self._held.maxlen:
                    self._dropped += 1
                self._held.append(MARKS["out"] + text)
            return
        # a complaint reads against what came before it, so the held lines go
        # out first and this one is never a candidate for dropping
        self._flush_held()
        self._put(MARKS.get(stream, "  ") + text)

    def close(self, footer: str = "") -> None:
        self._flush_held()
        now = time.monotonic()
        if self._current is not None:
            # nothing marks the last step ending, so its clock also ran through
            # whatever the run did after it: the copy back, the zone commit
            heading, started = self._current
            self._current = (f"{heading}, and everything after it", started)
        self._timed(now)
        if self._file is None:
            return
        try:
            if self._took:
                # ahead of the verdict, which stays the last line
                spent = now - self._began
                spent = f"{spent:.1f}s" if spent < 60 else f"{int(spent // 60)}m {int(spent % 60):02d}s"
                self._file.write(f"\nslowest of {len(self._took)} steps, {spent} in all:\n")
                for seconds, heading in sorted(self._took, reverse=True)[:SLOWEST]:
                    self._file.write(f"  {seconds:8.1f}s  {heading}\n")
            if footer:
                self._file.write("\n" + footer + "\n")
            self._file.close()
        except (OSError, ValueError):
            pass
        self._file = None

    def _timed(self, now: float) -> None:
        if self._current is not None:
            heading, started = self._current
            self._took.append((now - started, heading))
            self._current = None

    def _put(self, text: str) -> None:
        try:
            self._file.write(text + "\n")
            self._file.flush()
        except (OSError, ValueError):
            self._file = None

    def _flush_held(self) -> None:
        if self._file is not None and (self._held or self._dropped):
            if self._dropped:
                self._put(f"  ... {self._dropped} more lines ...")
            for text in self._held:
                self._put(text)
        self._held.clear()
        self._dropped = 0
        self._seen = 0

    @property
    def usable(self) -> bool:
        return self._file is not None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def _prune(folder: Path, keep: int = KEEP) -> None:
    """
    oldest first, so a machine that installs the mod thirty times does not
    accumulate thirty transcripts of an archive extraction
    """
    try:
        logs = sorted(folder.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for stale in logs[:-keep] if len(logs) > keep else []:
        try:
            stale.unlink()
        except OSError:
            pass
