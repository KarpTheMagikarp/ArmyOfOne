"""
Events into the page from one thread, in as few round trips as it takes.

pywebview's evaluate_js blocks until the page has run the script, so every
event cost whoever sent it a trip to the UI thread and back. The HD install's
pipe reader sends one per line a tool prints, and a step printing fourteen
thousand of them waited on fourteen thousand round trips with the tool stuck
behind a full pipe: against stub tools, a 1.7s unpack took 75s with the window
open. Senders only queue now. One thread does the round trips, and whatever
piled up during the last one goes over as a single batch, so a fast stream
costs a handful and a quiet one still lands straight away.
"""

import json
import queue
import threading

# events per round trip. The terminal keeps 2000 lines, so past that a batch is
# mostly lines the page would trim the moment they landed
MOST = 2000


def coalesce(events: list) -> list:
    """runs of terminal lines as one "lines" event, and a run of progress as its last"""
    out = []
    for event in events:
        last = out[-1] if out else {}
        stage = event.get("stage") if event.get("type") == "hd" else None
        tail = last.get("stage") if last.get("type") == "hd" else None
        if stage == "line":
            pair = [event.get("text", ""), event.get("stream", "out")]
            if tail == "lines":
                last["lines"].append(pair)
            else:
                out.append({"type": "hd", "stage": "lines", "lines": [pair]})
        elif stage == "progress" and tail == "progress":
            out[-1] = event
        else:
            out.append(event)
    return out


def encode(events: list) -> str | None:
    """the JSON the page gets, one event or a batch, minus any json can't take"""
    def payload(chosen):
        return chosen[0] if len(chosen) == 1 else {"type": "batch", "events": chosen}

    try:
        return json.dumps(payload(events))
    except (TypeError, ValueError):
        # one bad event (a Path, an exception) loses itself, not the rest of
        # the run's terminal
        good = []
        for event in events:
            try:
                json.dumps(event)
            except (TypeError, ValueError):
                continue
            good.append(event)
        return json.dumps(payload(good)) if good else None


class Pump:
    def __init__(self, send):
        # send takes the JSON text and blocks until the page has it
        self._send = send
        self._queue = queue.SimpleQueue()
        self._waiting = 0
        self._settled = threading.Condition()
        self._thread = None

    def put(self, event: dict) -> None:
        with self._settled:
            self._waiting += 1
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="page-events",
                                                daemon=True)
                self._thread.start()
        self._queue.put(event)

    def drain(self, timeout: float) -> bool:
        """whether everything put so far has been handed over, within timeout"""
        with self._settled:
            return self._settled.wait_for(lambda: self._waiting == 0, timeout)

    def _run(self):
        while True:
            events = [self._queue.get()]
            while len(events) < MOST:
                try:
                    events.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            try:
                text = encode(coalesce(events))
                if text is not None:
                    self._send(text)
            except Exception:
                # the window closed mid run, or the page hasn't put its
                # receiver up yet. Neither is worth losing the thread over
                pass
            finally:
                with self._settled:
                    self._waiting -= len(events)
                    self._settled.notify_all()
