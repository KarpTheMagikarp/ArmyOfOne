"""
Where the seconds before the window appears actually go.

shell.py holds the window back until the page sets window.__uiReady, so
whatever that waits on is the startup. Guessing at it from the source is how
you end up optimising the wrong half: this reads the page's own resource
timings instead.

    python tools/boot_timing.py

Development tool, not shipped.
"""

import http.server
import json
import socketserver
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BRIDGE = """
window.__t0 = performance.now();
window.__marks = [];
const mark = n => window.__marks.push([n, Math.round(performance.now())]);
// the flag shell.py polls for
let ready = false;
Object.defineProperty(window, '__uiReady', {
  get: () => ready,
  set: v => { ready = v; if (v) mark('__uiReady'); },
  configurable: true,
});
window.pywebview = { api: {
  scan: () => Promise.resolve({ok:true, found:true, path:"D:/FF13",
      source:"steam", supported:["ff13fix","laa","hdproject"], applied:[]}),
  list_addons: () => Promise.resolve({ok:true, mods:[]}),
  hd_categories: () => Promise.resolve({categories:[], selected:[], installed:[]}),
}};
"""


def serve():
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(ROOT), **k)
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main():
    from playwright.sync_api import sync_playwright

    httpd, port = serve()
    try:
        with sync_playwright() as pw:
            found = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
            browser = pw.chromium.launch(executable_path=str(found[-1]) if found else None)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.add_init_script(BRIDGE)
            page.goto(f"http://127.0.0.1:{port}/ui/index.html", wait_until="load")

            # the same wait shell.py does, with the same 6s ceiling
            page.wait_for_function("window.__uiReady === true", timeout=6000)
            marks = page.evaluate("window.__marks")
            print("milestones (ms from navigation start):")
            for name, at in marks:
                print(f"   {at:>6}  {name}")

            rows = page.evaluate("""() =>
              performance.getEntriesByType('resource').map(r => ({
                name: r.name.split('/').pop(),
                kind: r.initiatorType,
                ms: Math.round(r.duration),
                start: Math.round(r.startTime),
                end: Math.round(r.responseEnd),
                kb: Math.round((r.transferSize || r.decodedBodySize || 0) / 1024),
              }))""")
            ready_at = next((at for n, at in marks if n == "__uiReady"), None)

            print(f"\nwindow would appear at ~{ready_at} ms\n")
            print(f"{'resource':34} {'kind':10} {'KB':>7} {'start':>7} {'end':>7} {'took':>7}")
            for r in sorted(rows, key=lambda r: -r["end"]):
                flag = "  <-- still loading at ready" if ready_at and r["end"] > ready_at else ""
                print(f"{r['name'][:33]:34} {r['kind']:10} {r['kb']:>7} "
                      f"{r['start']:>7} {r['end']:>7} {r['ms']:>7}{flag}")

            before = [r for r in rows if not ready_at or r["end"] <= ready_at]
            print(f"\n{len(before)} requests, {sum(r['kb'] for r in before)} KB "
                  f"before the window is allowed to appear")
            print("fonts:", page.evaluate(
                "document.fonts.status + ', ' + document.fonts.size + ' faces'"))
            browser.close()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
