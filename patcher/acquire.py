"""
Getting one file from "we need this" to "we have it". Three ways in, tried as
a sequence:

    1. the embedded browser catches it mid-air        (nothing to find)
    2. it lands in Downloads and we notice            (no dragging)
    3. the user drops it on the window or picks it    (always works)

Kept out of the UI so the timing can be tested with a fake clock. A catcher
stuck behind a bot check looks like one still waiting, so after GRACE_SECONDS
the prompt switches to the way out on its own. The drop target is live the
whole time, so someone who already has the file never waits on a countdown.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

# how long the automatic paths get before the prompt changes. long enough to
# cover a slow page and a five second Nexus countdown, short enough that
# someone who is stuck does not sit there wondering.
GRACE_SECONDS = 18.0

# after this we stop watching entirely. the drop target stays live.
GIVE_UP_SECONDS = 900.0

# a download that's there and not growing: a dropped connection or a pause,
# which the browser keeps the partial through. Longer than the grace, since a
# virus scan holds a finished one at full size before letting it go
STALLED_SECONDS = 60.0

# how long one sits still before the line says the browser may be asking about
# it. Chrome and Edge keep a download they call dangerous, a zip with an exe
# in it say, as "Unconfirmed 123456.crdownload" at full size until Keep is
# pressed, and a count that just stops reads as the app being stuck
HELD_SECONDS = 6.0


class Stage(str, Enum):
    OPENING = "opening"           # sending them to the mod page
    WATCHING = "watching"         # catcher and folder watcher both live
    DOWNLOADING = "downloading"   # a browser is writing something into Downloads
    HELD = "held"                 # it's stopped growing, most often a browser asking about it
    FALLBACK = "fallback"         # automatic paths have not produced anything
    ARRIVED = "arrived"           # we have a file
    CANCELLED = "cancelled"
    EXPIRED = "expired"           # stopped watching; a drop or Choose file still works


PROMPTS = {
    # plain language, and each one says what the user should DO. "waiting for
    # download" tells someone nothing about whether it is their turn to act.
    Stage.OPENING: "Opening the mod page...",
    Stage.WATCHING: "Log in if you need to, then press Download. We'll grab it automatically.",
    Stage.DOWNLOADING: "Downloading...",
    Stage.HELD: "Your browser hasn't let go of it. If it asks whether to keep the file, choose Keep.",
    Stage.FALLBACK: "Didn't catch it? Drag the downloaded file onto this window, or click to browse.",
    Stage.ARRIVED: "Got it.",
    Stage.CANCELLED: "Stopped.",
    Stage.EXPIRED: "Stopped watching Downloads. Drag the downloaded file here, or click to browse.",
}


@dataclass
class AcquisitionEvent:
    stage: Stage
    prompt: str
    job_id: str
    source: str | None = None      # "catcher", "downloads", "drop", "picker"
    path: str | None = None
    seconds_waited: float = 0.0
    landed: int | None = None      # bytes of a download still being written
    total: int | None = None       # what it comes to, when that's known
    file: str = ""                 # what it's called, once the browser has said
    left: int = 0                  # files still to come after this one arrived

    def as_dict(self) -> dict:
        return {"type": "acquire", "stage": self.stage.value, "prompt": self.prompt,
                "jobId": self.job_id, "source": self.source, "path": self.path,
                "waited": round(self.seconds_waited, 1), "bytes": self.landed,
                "total": self.total, "file": self.file, "left": self.left}


@dataclass
class Acquisition:
    """
    one file, one job, however it arrives.

    every external thing is injected: the clock so timing is testable, the
    catcher and watcher so neither a browser nor a real folder is needed, and
    the emit callback so this never imports the UI.
    """

    job_id: str
    page_url: str
    emit: Callable[[AcquisitionEvent], None] | None = None
    clock: Callable[[], float] = time.monotonic
    grace: float = GRACE_SECONDS
    give_up: float = GIVE_UP_SECONDS
    stalled: float = STALLED_SECONDS
    held: float = HELD_SECONDS
    # how many downloads the job is, LR's DLC restoration being two files off
    # one page. Each that lands is handed over and the watch goes on for the rest
    want: int = 1
    # file name -> bytes, for a download whose size is known before it's done
    sizes: dict = field(default_factory=dict)

    _stage: Stage = Stage.OPENING
    # None, not 0.0: a monotonic clock can legitimately read zero, and a
    # falsy sentinel made "not started" and "started at t=0" the same thing
    _started: float | None = None
    _result: Path | None = None
    _source: str | None = None
    _done: bool = False
    _dropped: list = field(default_factory=list)
    # when a download in progress last grew, and how big it was then
    _moved: float | None = None
    _landed: int | None = None
    # when the one being written went, which is usually its rename
    _gone: float | None = None
    _file: str = ""
    _landed_files: int = 0
    # the run's thread hears the download while a drop or Stop comes in on
    # another, and a step said after "Got it" would take the strip back
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def stage(self) -> Stage:
        return self._stage

    @property
    def result(self) -> Path | None:
        return self._result

    def _announce(self, stage: Stage, source: str | None = None, path: Path | None = None,
                  landed: int | None = None, left: int = 0):
        self._stage = stage
        if self.emit:
            self.emit(AcquisitionEvent(
                stage=stage, prompt=PROMPTS[stage], job_id=self.job_id,
                source=source, path=str(path) if path else None,
                seconds_waited=(self.clock() - self._started) if self._started is not None else 0.0,
                landed=landed, total=self._total() if landed else None,
                file=self._file if stage in (Stage.DOWNLOADING, Stage.HELD) else "", left=left,
            ))

    def _total(self) -> int | None:
        """the size of the one being written, when it's a file whose size is known"""
        if not self._file:
            return None
        wanted = self._file.lower()
        return next((size for name, size in self.sizes.items() if name.lower() == wanted), None)

    def growing(self, size: int | None, name: str = "") -> None:
        """
        a download in Downloads at `size` bytes, or None for none, `name`
        what it'll be called once the browser says. The first sighting and
        every change are reported, so "press Download" becomes a count going
        up. One that sits still is first said to be with the browser, then
        hands back to the way out, as does one with no file turning up; one
        that picks up again takes it back.
        """
        with self._lock:
            if self._done:
                return
            now = self.clock()
            if size:
                self._gone = None
                # "" too: a browser never takes a name back, so it's another download
                self._file = name
                total = self._total()
                if size != self._landed:
                    self._moved, self._landed = now, size
                    self._announce(Stage.DOWNLOADING, landed=size)
                elif self._stage is Stage.DOWNLOADING and now - self._moved >= self.held \
                        and (not name or (total is not None and size >= total)):
                    # one still unnamed, Chrome's "Unconfirmed", or all there:
                    # one that's only stopped coming in is a connection's
                    self._announce(Stage.HELD, landed=size)
                elif self._stage in (Stage.DOWNLOADING, Stage.HELD) \
                        and now - self._moved >= self.stalled:
                    self._announce(Stage.FALLBACK)
                return
            if self._stage not in (Stage.DOWNLOADING, Stage.HELD):
                return
            # gone, and most often renamed to the file the watcher is about to
            # hand over, so it gets the grace before it's called missed
            self._gone = now if self._gone is None else self._gone
            if now - self._gone >= self.grace:
                # one that comes back after is another go, even at this size
                self._landed = None
                self._announce(Stage.FALLBACK)

    def start(self) -> None:
        self._started = self.clock()
        self._announce(Stage.OPENING)
        self._announce(Stage.WATCHING)

    def offer(self, path: Path, source: str = "drop") -> bool:
        """
        hands a file in from anywhere: the catcher, the folder watcher, a drop
        or the picker. Accepted at any stage, grace period or not.
        """
        path = Path(path)
        with self._lock:
            if self._done or not path.exists():
                return False
            self._done = True
            self._result = path
            self._source = source
            self._announce(Stage.ARRIVED, source=source, path=path)
            return True

    def _landed_one(self, path: Path) -> bool:
        """
        one of the job's downloads in Downloads, said as arrived. True once
        it's the last of them, when the watch is done; before that it goes on
        for the rest, the give up counted from here
        """
        with self._lock:
            if self._done or not Path(path).exists():
                return False
            self._landed_files += 1
            left = max(0, self.want - self._landed_files)
            if not left:
                self._done = True
                self._result, self._source = Path(path), "downloads"
            self._announce(Stage.ARRIVED, source="downloads", path=Path(path), left=left)
            if left:
                self._started, self._moved, self._landed, self._file = \
                    self.clock(), None, None, ""
                self._stage = Stage.WATCHING
            return not left

    def cancel(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            self._announce(Stage.CANCELLED)

    def tick(self) -> Stage:
        """
        advances the prompt based on elapsed time. called on a timer by the
        caller; does no waiting of its own so it stays testable.
        """
        with self._lock:
            if self._done or self._started is None:
                return self._stage
            waited = self.clock() - self._started
            if self._stage is Stage.WATCHING and waited >= self.grace:
                # a blocked catcher looks identical to a working one, so the
                # only honest thing is to stop implying it is still going to
                # happen
                self._announce(Stage.FALLBACK)
            return self._stage

    def expired(self) -> bool:
        """whether the automatic watchers should stop; the drop target does not"""
        if self._done or self._started is None:
            return self._done
        # from when a download last grew, if one has: six gigabytes on a slow
        # line is still going long after a page with nothing on it would be
        since = max(self._started, self._moved if self._moved is not None else self._started)
        return (self.clock() - since) >= self.give_up

    def run(self, poll: Callable[[], Path | None] | None = None,
            interval: float = 0.5, sleep=time.sleep, cancel=None,
            partial: Callable | None = None) -> Path | None:
        """
        blocking convenience for a worker thread.

        poll is whatever automatic source there is (folder watcher, catcher
        queue) and partial how far an unfinished download has got, in bytes
        or as the watcher's Writing (which has the name too). The file it
        returns is one poll found, the caller's to install. A drop or pick on
        another thread ends it too, but that file is its own caller's, or it
        installs twice. Past the give up it says so and ends. A job wanting
        several files hands each over through its "arrived" as it lands and
        returns the last.
        """
        # stopped before it began, while the sizes were being asked for
        if self._done:
            return None
        self.start()
        while not self._done:
            if cancel is not None and cancel.is_set():
                self.cancel()
                return None
            if self.expired():
                with self._lock:
                    if not self._done:
                        self._done = True
                        self._announce(Stage.EXPIRED)
                return None
            if poll is not None:
                found = poll()
                # the folder watcher hands back a Sighting, not a path, and
                # offer() does Path(found). That TypeError was swallowed by
                # the caller's blanket handler, so the downloads watch had
                # never once worked: every acquisition fell through to
                # "Couldn't watch for the download. Use Choose file."
                found = getattr(found, "path", found)
                if found is not None and self._landed_one(found):
                    return self._result
                if partial is not None:
                    writing = partial()
                    self.growing(getattr(writing, "size", writing), getattr(writing, "name", ""))
            self.tick()
            sleep(interval)
        return None
