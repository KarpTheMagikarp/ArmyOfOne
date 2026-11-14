"""
Events into the page from one thread, batched by whatever piled up.

pywebview's evaluate_js blocks until the page has run the script, so every
event used to cost its sender a round trip to the UI thread. The HD install's
pipe reader is a sender, and a tool printing fourteen thousand lines in one step
sat behind fourteen thousand of them with its pipe full: against stub tools the
unpack step went from 1.7s to 75s with the window open.
"""

import json
import threading
import time
from pathlib import Path

from patcher.pump import Pump, coalesce


def line(text, stream="out"):
    return {"type": "hd", "stage": "line", "text": text, "stream": stream}


def flat(sent):
    """every event the page got, out of the JSON it was handed and any batches"""
    out = []
    for text in sent:
        event = json.loads(text)
        out += event["events"] if event.get("type") == "batch" else [event]
    return out


def test_runs_of_lines_go_over_as_one_event():
    events = [line("a"), line("b", "err"), {"type": "hd", "stage": "step", "index": 1},
              line("c"), {"type": "dropped", "paths": []}]

    assert coalesce(events) == [
        {"type": "hd", "stage": "lines", "lines": [["a", "out"], ["b", "err"]]},
        {"type": "hd", "stage": "step", "index": 1},
        {"type": "hd", "stage": "lines", "lines": [["c", "out"]]},
        {"type": "dropped", "paths": []},
    ]


def test_only_the_latest_progress_in_a_run_of_them_matters():
    events = [{"type": "hd", "stage": "progress", "share": s} for s in (0.1, 0.2, 0.3)]
    events.append(line("x"))
    events.append({"type": "hd", "stage": "progress", "share": 0.4})

    assert coalesce(events) == [{"type": "hd", "stage": "progress", "share": 0.3},
                                {"type": "hd", "stage": "lines", "lines": [["x", "out"]]},
                                {"type": "hd", "stage": "progress", "share": 0.4}]


def test_coalescing_leaves_the_callers_events_alone():
    """the shell's own copies end up in tests and logs, and a merge into them reads as a duplicate"""
    first = line("a")
    coalesce([first, line("b")])

    assert first == line("a")


def slow_page(delay):
    sent, arrived = [], threading.Event()

    def send(text):
        time.sleep(delay)
        sent.append(text)
        arrived.set()
    return sent, arrived, send


def test_a_sender_never_waits_on_the_page():
    sent, _, send = slow_page(0.2)
    pump = Pump(send)

    started = time.perf_counter()
    for i in range(500):
        pump.put(line(str(i)))
    took = time.perf_counter() - started

    assert took < 0.1, f"500 puts took {took:.3f}s against a page that takes 0.2s a round trip"
    assert pump.drain(5)


def test_what_piles_up_during_a_round_trip_goes_over_as_one():
    sent, arrived, send = slow_page(0.15)
    pump = Pump(send)

    pump.put(line("first"))
    assert arrived.wait(2)
    for i in range(1000):
        pump.put(line(str(i)))
    assert pump.drain(5)

    assert len(sent) <= 3, f"{len(sent)} round trips for 1001 lines"
    texts = [text for event in flat(sent) for text, _ in event["lines"]]
    assert texts == ["first"] + [str(i) for i in range(1000)], "lines lost or out of order"


def test_order_holds_across_kinds():
    sent, _, send = slow_page(0)
    pump = Pump(send)
    for event in (line("a"), {"type": "hd", "stage": "step", "index": 0}, line("b"),
                  {"type": "hd", "stage": "done"}, {"type": "dropped", "paths": ["x"]}):
        pump.put(event)
    assert pump.drain(5)

    kinds = [e.get("stage") or e["type"] for e in flat(sent)]
    assert kinds == ["lines", "step", "lines", "done", "dropped"], kinds


def test_one_bad_event_doesnt_take_the_rest_down():
    """json can't take a Path, and a dropped batch is a terminal that stops for no reason"""
    sent = []
    pump = Pump(sent.append)
    pump.put(line("before"))
    pump.put({"type": "hd", "stage": "step", "label": Path("nope")})
    pump.put(line("after"))
    assert pump.drain(5)

    texts = [text for event in flat(sent) for text, _ in event.get("lines", [])]
    assert texts == ["before", "after"], sent


def test_a_page_that_throws_doesnt_stop_the_pump():
    calls = []

    def send(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("window closed mid run")

    pump = Pump(send)
    pump.put(line("a"))
    assert pump.drain(5)
    pump.put(line("b"))
    assert pump.drain(5)

    assert len(calls) == 2


def test_the_shell_queues_rather_than_calling_the_page():
    """and uses run_js where pywebview has it: evaluate_js keeps a callback per call, forever"""
    import shell

    seen = []

    class Window:
        def run_js(self, script):
            seen.append(("run_js", script))

        def evaluate_js(self, script):
            seen.append(("evaluate_js", script))

    api = shell.Api()
    api._window = Window()
    api._emit({"type": "hd", "stage": "line", "text": "hello", "stream": "out"})
    assert api._pump.drain(5)

    assert [how for how, _ in seen] == ["run_js"]
    assert seen[0][1].startswith("window.__patch && window.__patch.receive(")
    assert '"hello"' in seen[0][1]

    class Older:
        def evaluate_js(self, script):
            seen.append(("evaluate_js", script))

    api._window = Older()
    api._emit({"type": "launch", "message": "ok"})
    assert api._pump.drain(5)
    assert seen[-1][0] == "evaluate_js", "pywebview 5.0 has no run_js"
