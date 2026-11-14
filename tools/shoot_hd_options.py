"""
screenshots the HD Project options in the real page, served over HTTP.

Over HTTP and not file://, which is not a detail. file:// blocks fetch(), so
the page silently falls back to FALLBACK_MANIFEST and every glyph label renders
empty. Two wrong conclusions came out of that before it was written down.

    python tools/shoot_hd_options.py

Drops png files beside the repo. Development tool, not shipped.
"""

import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 0        # let the OS pick, so a re-run never fights a lingering socket


def serve():
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(ROOT), **k)
    # set before binding, not after. TCPServer binds inside __init__, so
    # assigning it afterwards is too late and a re-run inside TIME_WAIT dies on
    # "address already in use"
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# the page talks to pywebview. this is the same shape patcher/bridge.py returns,
# so what gets shot is the real render path rather than a mock of it.
BRIDGE = """
window.__calls = [];
// anything the page throws, including inside a promise, where it would
// otherwise vanish without a trace
window.__err = [];
addEventListener('error', e => window.__err.push('error: ' + e.message));
addEventListener('unhandledrejection', e => window.__err.push('rejection: ' + e.reason));
const cats = %s;
let chosen = null;
window.pywebview = { api: {
  scan: () => Promise.resolve({ok:true, found:true,
      path:"D:\\\\SteamLibrary\\\\steamapps\\\\common\\\\FINAL FANTASY XIII",
      source:"steam", supported:["ff13fix","laa","hdproject"], applied:[], error:null}),
  list_addons: () => Promise.resolve({ok:true, mods:[]}),
  hd_categories: (g,j) => {
    window.__calls.push(["hd_categories",g,j]);
    if (chosen === null) chosen = cats.filter(c=>c.default).map(c=>c.id);
    return Promise.resolve({categories:cats, selected:chosen, installed:[]});
  },
  set_hd_categories: (g,j,sel) => {
    window.__calls.push(["set_hd_categories",g,j,sel.join("|")]);
    chosen = sel;
    const order = {}; cats.forEach((c,n)=>order[c.id]=n);
    const picked = cats.filter(c=>sel.includes(c.id));
    let steps;
    if (picked.length === cats.length) steps = ["A, to install everything"];
    else steps = picked.map(c => `C then ${c.menu}, to add ${c.label}`);
    return Promise.resolve({ok:true, selected:sel, steps,
      mesh: picked.filter(c=>c.mesh).map(c=>c.id)});
  },
  pick_file: () => { window.__calls.push(["pick_file"]);
    return Promise.resolve({ok:true, path:"D:/Mods/HDProject.zip"}); },
  // the live failure: the browser opens, a folder is chosen, and pywebview's
  // reflection hack hands back an empty tuple
  pick_folder: () => { window.__calls.push(["pick_folder"]);
    return Promise.resolve(window.__folderWorks
      ? {ok:true, path:"D:/Mods/FFXIII HD Project"}
      : {ok:false, empty:true, error:"the folder browser closed without handing back a path"}); },
  hd_survey: (g, j, root) => {
    window.__calls.push(["hd_survey", g, j, root]);
    return Promise.resolve({
      sizes: {weapons: 77*1024*1024, eidolons: 40*1024*1024,
        npcs: 303*1024*1024, enemies: 486*1024*1024, misc: 286*1024*1024,
        pcs: 1.4*1024*1024*1024,
        // the twenty zone archives, off their own Setup.bat's size checks
        zones: 4821827584},
      // as if weapons and npcs had already been installed, which is the state
      // a second run actually starts from
      applied: window.__allApplied
        ? ["weapons","npcs","eidolons","enemies","misc","pcs"]
        : ["weapons", "npcs"], verified: true,
      // what the pair check finds on a game carrying the shipped defect
      suspect: window.__suspect || {},
      // the Japanese box, with English's weapons and npcs not copied over yet
      voices: {language: "Japanese", has: [], owed: [],
        art: {weapons: 77*1024*1024, npcs: 303*1024*1024},
        parts: {weapons: ["filelist_scrc.win32.bin", "white_scrc.win32.bin"],
                npcs: ["filelist_scrc.win32.bin", "white_scrc.win32.bin"]},
        backup: {"filelist_scrc.win32.bin": 2*1024*1024,
                 "white_scrc.win32.bin": 2.6*1024*1024*1024}}});
  },
  unpack_drop: (path) => { window.__calls.push(["unpack_drop", path]);
    setTimeout(() => window.__patch.receive(
      {type:"dropped", paths:["D:/Mods/.HDProject-unpacked"]}), 120);
    return Promise.resolve({ok:true, started:true}); },
  identify_drop: (path, unpack) => {
    window.__calls.push(["identify_drop", path, String(unpack)]);
    if (window.__isZip && /[.]zip$/i.test(String(path)))
      return Promise.resolve({kind:"maybe-hdproject"});
    return Promise.resolve({kind:"hdproject", jobId:"hdproject",
      detail:{root:"D:/Mods/FFXIII HD Project", data:"D:/Mods/FFXIII HD Project/Data",
              lists:['LoadHDEidolons.txt','LoadHDEnemy.txt','LoadHDEnvironments.txt','LoadHDNPCs.txt','LoadHDOther.txt','LoadHDPCs.tx1','LoadHDPCs.tx2','LoadHDPCs.tx3','LoadHDWeapons.txt'],
              containers:['FFXIII_General_Models.bin'], tools:['ff13tool.exe'], has_textures:true}});
  },
  open_pages: () => Promise.resolve(true),
  // stands in for shell.install_hd_project: returns at once and then streams,
  // which is the whole point of the terminal
  install_hd_project: (g, root, sel, mode) => {
    window.__calls.push(["install_hd_project", g, root, sel.join("|"), mode || "add"]);
    const R = e => window.__patch.receive(Object.assign({type:"hd"}, e));
    if (mode === "set" && !sel.length){
      setTimeout(() => {
        R({stage:"started", log:"C:\\\\runs\\\\hdproject.log"});
        R({stage:"line", text:"[restore] white_scru.win32.bin"});
        R({stage:"done", steps:0, removed:2});
      }, 60);
      return Promise.resolve({ok:true, started:true});
    }
    setTimeout(() => {
      R({stage:"started", log:"C:\\\\Users\\\\x\\\\AppData\\\\Roaming\\\\FFXIIITrilogyPatcher\\\\runs\\\\hdproject.log"});
      R({stage:"line", text:"[prep] 14381 files, 11.4 GB unpacked, longest path 188 of 259"});
      R({stage:"line", text:"[backup] white_scru.win32.bin (3.9 GB)"});
      R({stage:"line", text:"> ff13tool.exe -x -ff13 filelist_scru.win32.bin", stream:"cmd"});
      let i = 0;
      const labels = ["unpacking white_scru", "n910 models", "s101 models",
                      "w001 textures", "repacking white_scru"];
      const tick = setInterval(() => {
        if (i >= labels.length){
          clearInterval(tick);
          R({stage:"line", text:"[copy] white_scru.win32.bin back into the game"});
          R({stage:"done", steps: labels.length});
          return;
        }
        R({stage:"step", label: labels[i], index:i, total: labels.length});
        R({stage:"line", text:`ff13tool: processing ${labels[i]}`, stream:"out"});
        if (i === 1) R({stage:"line", text:"warning: skipping an entry", stream:"err"});
        i++;
      }, 120);
    }, 60);
    return Promise.resolve({ok:true, started:true});
  },
  cancel_hd_project: () => { window.__calls.push(["cancel_hd_project"]); return Promise.resolve({ok:true}); },
}};
"""


# the real shape now: the page cancels the drop and learns nothing, then the
# host side sends back the path it could see. JavaScript never gets file.path.
DO_DROP = """() => {
  const dt = new DataTransfer();
  dt.items.add(new File(['x'], 'HDProject'));
  dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true}));
  setTimeout(() => window.__patch.receive(
    {type: 'dropped', paths: ['D:\\\\Mods\\\\FFXIII HD Project']}), 50);
}"""

CHECK_DROP = """() => {
  const row = document.querySelector('#mdExtras .mod.has-opts');
  const drawer = document.querySelector('#md');
  return {
    open: row ? row.dataset.open : null,
    drawerShown: drawer ? drawer.className + '|' + (drawer.dataset.open || '') : null,
    note: (document.querySelector('#mdNote') || {}).textContent || ''
  };
}"""


def main():
    from playwright.sync_api import sync_playwright

    manifest = json.loads((ROOT / "assets" / "manifest.json").read_text())
    categories = next(j["categories"] for g in manifest["games"]
                      for j in g["jobs"] if j.get("categories"))

    httpd, port = serve()
    shots = []
    try:
        with sync_playwright() as pw:
            # the pinned build, found rather than hardcoded: the directory carries a
            # revision number and PLAYWRIGHT_BROWSERS_PATH does not
            binaries = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
            browser = pw.chromium.launch(executable_path=str(binaries[-1]) if binaries else None)
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            console = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.add_init_script(BRIDGE % json.dumps(categories))
            page.goto(f"http://127.0.0.1:{port}/ui/index.html", wait_until="load")
            page.wait_for_timeout(1200)

            # into the game, then open the drawer to see the row at rest
            page.evaluate("document.querySelectorAll('.tab, .gameTab, #games li')[0]?.click()")
            page.wait_for_timeout(700)
            page.evaluate("typeof Mods !== 'undefined' && Mods.open()")
            page.wait_for_timeout(700)
            page.evaluate("""() => {
              const row = document.querySelector('#mdExtras .mod.has-opts');
              if (row) row.dataset.open = 'true';
            }""")
            page.wait_for_timeout(400)
            print("before any drop ->", page.evaluate("""() => ({
              rows: document.querySelectorAll('#mdExtras .opt-row').length,
              run: !!document.querySelector('#mdExtras .opts .run'),
              zonePickers: Array.from(
                document.querySelectorAll('#mdZone .pick')).map(e => e.textContent)
            })"""))
            # every route in, driven the way a person drives it
            page.evaluate("typeof Mods !== 'undefined' && Mods.open()")
            page.wait_for_timeout(300)
            print("== choose a file ==")
            page.click("#mdPick"); page.wait_for_timeout(500)
            print("  rows:", page.evaluate("document.querySelectorAll('#mdExtras .opt-row').length"),
                  "| note:", page.evaluate("$('#mdNote').textContent"))
            page.evaluate("window.__calls.length = 0")

            print("== choose a folder, the one that hands back nothing ==")
            page.click("#mdPickDir"); page.wait_for_timeout(400)
            print("  note:", page.evaluate("$('#mdNote').textContent"))

            print("== choose a folder, working ==")
            page.evaluate("window.__folderWorks = true")
            page.click("#mdPickDir"); page.wait_for_timeout(500)
            print("  rows:", page.evaluate("document.querySelectorAll('#mdExtras .opt-row').length"),
                  "| calls:", page.evaluate("window.__calls.map(c => c[0])"))

            print("== a zip that has to be unpacked ==")
            page.evaluate("window.__isZip = true; window.__calls.length = 0")
            page.evaluate("Mods.route('D:/Mods/HDProject.zip')")
            page.wait_for_timeout(200)
            print("  while unpacking:", page.evaluate("$('#mdNote').textContent"))
            page.wait_for_timeout(500)
            print("  after:", page.evaluate("$('#mdNote').textContent"),
                  "| calls:", page.evaluate("window.__calls.map(c => c[0])"))
            page.evaluate("window.__isZip = false")

            shots.append(("hd-1-inert.png", "drawer open, Install inert"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            page.evaluate("typeof Mods !== 'undefined' && Mods.close()")
            page.wait_for_timeout(400)      # let the close finish before dropping
            page.evaluate(DO_DROP)
            page.wait_for_timeout(1400)     # and the reopen slide, which is .26s

            opened = page.evaluate("document.querySelectorAll('#mdExtras .opt-row').length")
            live = page.evaluate("""() => {
              const b = document.querySelector('#mdExtras .opts .run');
              return b ? {label: b.textContent, disabled: b.disabled} : null;
            }""")
            print("after the drop ->", live)
            shots.append(("hd-2-dropped.png", f"dropped, {opened} toggles in the drawer"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            page.evaluate("document.querySelector('#mdExtras .opt-row[data-cat=\"pcs\"]')?.click()")
            page.wait_for_timeout(400)
            print("layout at shot time ->", page.evaluate("""() => {
              const md = document.querySelector('#md');
              const parent = md.offsetParent;
              const cs = getComputedStyle(parent);
              return {
                drawerX: Math.round(md.getBoundingClientRect().x),
                parent: parent.id || parent.tagName,
                parentRect: Math.round(parent.getBoundingClientRect().x) + '+' +
                            Math.round(parent.getBoundingClientRect().width),
                parentTransform: cs.transform,
                mdTransform: getComputedStyle(md).transform,
                mdClass: md.className,
                mdWidth: Math.round(md.getBoundingClientRect().width),
                mdCssWidth: getComputedStyle(md).width,
                extrasScroll: document.querySelector('#mdExtras').scrollWidth,
                widestKid: Math.max(...Array.from(
                  document.querySelectorAll('#mdExtras *')).map(e => e.scrollWidth)),
              };
            }"""))
            shots.append(("hd-3-all-seven.png", "all seven selected"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            page.evaluate("document.querySelector('#mdExtras .opts .run')?.click()")
            page.wait_for_timeout(500)
            shots.append(("hd-4-terminal.png", "terminal, mid run"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            page.wait_for_timeout(1400)
            shots.append(("hd-5-finished.png", "terminal, finished"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            # the reason the whole stderr split exists. Driven through the same
            # receiver the shell talks to rather than a mocked render, so the
            # colouring, the repeated reason and the log line are all real.
            page.evaluate("""() => window.__patch.receive({type:"hd", stage:"failed",
                error:"unpacking white_scru stopped with code 1",
                reason:"ERROR: could not create output file, path too long",
                tail:["at chr/pc/pc001/model/pc001_00_a.win32.trb",
                      "ERROR: could not create output file, path too long"],
                log:"C:\\\\Users\\\\x\\\\AppData\\\\Roaming\\\\FFXIIITrilogyPatcher\\\\runs\\\\hdproject.log"})""")
            page.wait_for_timeout(350)
            shots.append(("hd-6-failed.png", "failure, with the reason in red"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            print("applied badges  ->", page.evaluate("""() =>
              Array.from(document.querySelectorAll('#mdExtras .opt-row')).map(r =>
                (r.querySelector('.t')||{}).textContent + ' applied=' + r.dataset.applied
                + ' locked=' + r.dataset.locked)"""))
            print("install hint    ->", page.evaluate(
              "(document.querySelector('#mdExtras .opts .hint')||{}).textContent"))
            print("caveat markers  ->", page.evaluate("""() =>
              Array.from(document.querySelectorAll('#mdExtras .opt-row')).map(r => {
                const m = r.querySelector('.why');
                return (r.querySelector('.t')||{}).textContent + ' -> ' +
                       (m ? m.textContent.slice(0, 1) + ' tip:' +
                            (m.querySelector('.tip')||{}).textContent.length : 'none');
              })"""))
            print("old paragraphs  ->", page.evaluate(
              "document.querySelectorAll('#mdExtras .opts > .why').length"))

            # the buttons live at the bottom of a scrolling drawer, so the
            # shots that are about them have to scroll to them
            bottom = """() => {
              let el = document.querySelector('#mdExtras'), seen = [];
              while (el){
                seen.push((el.id || el.className || el.tagName) + ' ' +
                          el.scrollHeight + '/' + el.clientHeight);
                if (el.scrollHeight > el.clientHeight + 4){ el.scrollTop = el.scrollHeight; }
                el = el.parentElement;
              }
              return seen;
            }"""
            drawer = {"x": 880, "y": 0, "width": 400, "height": 720}

            # repair, tested the way a user hits it rather than by reading the
            # dataset: click an applied row and see whether it queued a rebuild
            page.evaluate("""() => { Term.close && Term.close();
              document.querySelector('#mdExtras .mod.has-opts').dataset.open = 'true'; }""")
            page.wait_for_timeout(250)
            before = page.evaluate(
              "(document.querySelector('#mdExtras .opts .run')||{}).textContent")
            page.evaluate("document.querySelector('#mdExtras .opt-row[data-cat=\"weapons\"]')?.click()")
            page.wait_for_timeout(200)
            print("clicking an applied row ->", page.evaluate("""() => {
              const el = document.querySelector('#mdExtras .opt-row[data-cat="weapons"]');
              return {button: (document.querySelector('#mdExtras .opts .run')||{}).textContent,
                      badge: (el.querySelector('.size')||{}).textContent,
                      on: el.dataset.on, fixing: el.dataset.fixing, locked: el.dataset.locked};
            }"""), "(was", before + ")")

            # the finding, rendered: a game carrying the shipped defect says so
            # on its own, which is the whole point of the pair check
            page.evaluate("""() => {
              window.__suspect = {npcs: "12 areas from an earlier install show up as "
                + "colored static. Reinstalling rebuilds them from the original files."};
              Mods.resurvey && Mods.resurvey();
            }""")
            page.wait_for_timeout(400)
            print("a flagged category ->", page.evaluate("""() => {
              const el = document.querySelector('#mdExtras .opt-row[data-cat="npcs"]');
              return {badge: (el.querySelector('.size')||{}).textContent,
                      wrong: el.dataset.wrong,
                      why: (el.querySelector('.why .tip')||{}).textContent};
            }"""))
            page.evaluate(bottom); page.wait_for_timeout(150)
            shots.append(("hd-15-flagged.png", "a half written category flagged on its own"))
            page.screenshot(path=str(ROOT / shots[-1][0]), clip=drawer)

            # and the case the button wording exists for: nothing queued but a
            # rebuild of something already in the game
            print("only a repair queued ->", page.evaluate("""() => {
              document.querySelectorAll('#mdExtras .opt-row').forEach(el => {
                if (el.dataset.on === 'true' && el.dataset.applied !== 'true') el.click();
              });
              const el = document.querySelector('#mdExtras .opt-row[data-cat="weapons"]');
              return {button: (document.querySelector('#mdExtras .opts .run')||{}).textContent,
                      badge: (el.querySelector('.size')||{}).textContent};
            }"""))

            page.evaluate(bottom); page.wait_for_timeout(150)
            shots.append(("hd-10-repair.png", "an applied row ticked into a repair"))
            page.screenshot(path=str(ROOT / shots[-1][0]), clip=drawer)

            # a real mouse move, because :hover cannot be dispatched
            for cat in ("zones", "weapons", "pcs"):
                page.hover(f'#mdExtras .opt-row[data-cat="{cat}"] .why')
                page.wait_for_timeout(250)
                print(f"tip {cat:6}     ->", page.evaluate("""(cat) => {
                  const t = document.querySelector(
                    '#mdExtras .opt-row[data-cat="' + cat + '"] .why .tip');
                  const r = t.getBoundingClientRect();
                  const d = document.querySelector('#md').getBoundingClientRect();
                  return {visible: getComputedStyle(t).visibility, w: Math.round(r.width),
                          insideDrawer: r.left >= d.left - 1 && r.right <= d.right + 1,
                          onScreen: r.top >= 0 && r.bottom <= innerHeight};
                }""", cat))
                page.mouse.move(640, 360); page.wait_for_timeout(150)
            page.hover('#mdExtras .opt-row[data-cat="pcs"] .why')
            page.wait_for_timeout(300)
            print("tip open        ->", page.evaluate("""() => {
              const t = document.querySelector('#mdExtras .opt-row[data-cat="pcs"] .why .tip');
              const r = t.getBoundingClientRect();
              const d = document.querySelector('#md').getBoundingClientRect();
              return {visible: getComputedStyle(t).visibility, w: Math.round(r.width),
                      h: Math.round(r.height),
                      insideDrawer: r.left >= d.left - 1 && r.right <= d.right + 1,
                      onScreen: r.top >= 0 && r.bottom <= innerHeight};
            }"""))
            shots.append(("hd-13-tooltip.png", "a caveat revealed on hover"))
            page.screenshot(path=str(ROOT / shots[-1][0]), clip=drawer)
            page.mouse.move(640, 360); page.wait_for_timeout(250)

            page.evaluate("window.__allApplied = true; Mods.resurvey()")
            page.wait_for_timeout(350)
            print("nothing left to add ->", page.evaluate("""() => {
              const b = document.querySelector('#mdExtras .opts .run');
              return {run: b && b.textContent, disabled: b && b.disabled,
                      locked: Array.from(document.querySelectorAll('#mdExtras .opt-row'))
                        .filter(r => r.dataset.locked === 'true').length};
            }"""))
            page.evaluate("window.__allApplied = false; Mods.resurvey()")
            page.wait_for_timeout(350)

            # the collapse, measured mid flight rather than at the ends
            page.evaluate("""() => { Term.open('Installing'); Term.expand && Term.expand(); }""")
            page.wait_for_timeout(350)
            tall = page.evaluate("document.querySelector('#term').getBoundingClientRect().height")
            page.evaluate("document.querySelector('#termMin').click()")
            page.wait_for_timeout(110)
            mid = page.evaluate("document.querySelector('#term').getBoundingClientRect().height")
            page.wait_for_timeout(400)
            short = page.evaluate("document.querySelector('#term').getBoundingClientRect().height")
            page.evaluate("document.querySelector('#termMin').click()")
            page.wait_for_timeout(110)
            back = page.evaluate("document.querySelector('#term').getBoundingClientRect().height")
            page.wait_for_timeout(400)
            print("collapse ->", {"open": round(tall), "midway": round(mid),
                                  "collapsed": round(short), "reopening": round(back),
                                  "animates": round(short) < round(mid) < round(tall),
                                  "and back": round(short) < round(back) < round(tall)})
            print("log still in the layout ->", page.evaluate(
              "getComputedStyle(document.querySelector('#termOut')).display"))

            print("vertical lines near the title ->", page.evaluate("""() => {
              const out = [];
              const host = document.querySelector('#mdExtras');
              host.querySelectorAll('*').forEach(el => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                if (s.borderLeftWidth !== '0px' && s.borderLeftStyle !== 'none')
                  out.push(['border-left', el.className || el.tagName,
                            s.borderLeftWidth + ' ' + s.borderLeftColor,
                            Math.round(r.left) + ',' + Math.round(r.top)]);
                if (s.boxShadow && s.boxShadow !== 'none')
                  out.push(['box-shadow', el.className || el.tagName, s.boxShadow,
                            Math.round(r.left) + ',' + Math.round(r.top)]);
              });
              return out;
            }"""))

            print("caveats everywhere ->", page.evaluate("""() =>
              Array.from(document.querySelectorAll('#mdExtras .opt-row')).map(r =>
                (r.querySelector('.t')||{}).textContent + ' = ' +
                ((r.querySelector('.why .tip')||{}).textContent || 'NONE').length)"""))

            print("sizes on the toggles ->", page.evaluate("""() =>
              Array.from(document.querySelectorAll('#mdExtras .opt-row')).map(r =>
                (r.querySelector('.t')||{}).textContent + ' = ' +
                ((r.querySelector('.size')||{}).textContent || 'none'))"""))
            print("closed row hint  ->", page.evaluate("""() => {
              const row = document.querySelector('#mdExtras .mod.has-opts');
              row.dataset.open = 'false';
              const peek = row.querySelector('.peek'), chev = row.querySelector('.chev');
              return {ready: row.dataset.ready, peek: peek && peek.textContent,
                      chev: chev && chev.textContent};
            }"""))
            page.evaluate("document.querySelector('#mdExtras .mod.has-opts').dataset.open = 'true'")
            # the terminal's own edge against the drawer's, both ways round
            page.evaluate("""() => { Term.open('Installing the HD Project');
              Term.step('unpacking white_scru', 3, 894); Term.within(0.42); }""")
            page.wait_for_timeout(300)
            for state in ("open", "closed"):
                page.evaluate(f"Mods.{'open' if state == 'open' else 'close'}()")
                page.wait_for_timeout(400)
                print(f"terminal, drawer {state:6} ->", page.evaluate("""() => {
                  const t = document.querySelector('#term').getBoundingClientRect();
                  const d = document.querySelector('#md').getBoundingClientRect();
                  const stop = document.querySelector('#termStop').getBoundingClientRect();
                  return {termRight: Math.round(t.right), drawerLeft: Math.round(d.left),
                          width: Math.round(t.width),
                          stopClear: Math.round(stop.right) <= Math.round(d.left) + 1};
                }"""))
            page.evaluate("Mods.open()"); page.wait_for_timeout(350)
            print("window controls  ->", page.evaluate("""() =>
              Array.from(document.querySelectorAll('#term header .win button')).map(b => {
                const r = b.getBoundingClientRect();
                return b.id + ' "' + b.textContent + '" ' + b.getAttribute('aria-label')
                       + ' ' + Math.round(r.width) + 'x' + Math.round(r.height);
              })"""))
            shots.append(("hd-14-terminal.png", "terminal beside the drawer"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            print("progress bar     ->", page.evaluate("""() => {
              Term.step('unpacking white_scru', 0, 894);
              Term.within(0.5);
              const bar = document.querySelector('#termBar');
              return {width: bar.firstElementChild.style.width,
                      pct: document.querySelector('#termPct').textContent};
            }"""))

            red = page.evaluate(
                "Array.from(document.querySelectorAll('#termOut .ln.err')).map(e => e.textContent)")
            print(f"stderr lines styled red: {len(red)}")
            for text in red:
                print("   ", text)

            # scrolling the log must not move the stage behind it, which is
            # what overscroll-behavior:contain is for
            before = page.evaluate("document.querySelector('#app').scrollTop || 0")
            page.evaluate("""() => {
              const out = document.querySelector('#termOut');
              out.scrollTop = out.scrollHeight;
              out.dispatchEvent(new WheelEvent('wheel', {deltaY: 600, bubbles: true}));
            }""")
            page.wait_for_timeout(200)
            contained = page.evaluate("""() => ({
              appScroll: document.querySelector('#app').scrollTop || 0,
              behavior: getComputedStyle(document.querySelector('#termOut')).overscrollBehaviorY
            })""")
            print(f"scroll containment: {contained}, app was at {before}")

            page.evaluate("document.querySelector('#termMin').click()")
            page.wait_for_timeout(300)
            collapsed = page.evaluate("""() => {
              const t = document.querySelector('#term');
              return {klass: t.className, height: Math.round(t.getBoundingClientRect().height),
                      outShown: getComputedStyle(document.querySelector('#termOut')).display,
                      label: document.querySelector('#termMin').textContent,
                      stillOn: t.classList.contains('on')};
            }""")
            print(f"minimized: {collapsed}")
            shots.append(("hd-7-minimized.png", "collapsed to its header"))
            page.screenshot(path=str(ROOT / shots[-1][0]))

            page.evaluate("document.querySelector('#termMin').click()")
            page.wait_for_timeout(300)
            print("restored:", page.evaluate(
                "document.querySelector('#term').className + ' / ' "
                "+ document.querySelector('#termMin').textContent"))

            state = page.evaluate("""() => ({
              calls: window.__calls.map(c => c.join(' ')),
              count: (document.querySelector('#mdExtras .count') || {}).textContent || '',
              title: (document.querySelector('#termTitle') || {}).textContent || '',
              stepline: (document.querySelector('#termStep') || {}).textContent || '',
              termLines: document.querySelectorAll('#termOut .ln').length,
              tail: Array.from(document.querySelectorAll('#termOut .ln')).slice(-4).map(e => e.textContent),
              stopHidden: (document.querySelector('#termStop') || {}).style?.display === 'none',
              errors: window.__err || [],
            })""")
            browser.close()
    finally:
        httpd.shutdown()

    print(f"toggles in drawer: {opened}")
    print(f"selected count  : {state['count']}")
    print(f"terminal title  : {state['title']}")
    print(f"step line       : {state['stepline']}")
    print(f"lines streamed  : {state['termLines']}")
    print(f"stop hidden     : {state['stopHidden']}")
    print("tail            :")
    for line in state["tail"]:
        print("   ", line)
    if state["errors"]:
        print("PAGE ERRORS     :", state["errors"])
    print("bridge calls    :")
    for call in state["calls"]:
        print("   ", call)
    noise = [c for c in console if "favicon" not in c and "404" not in c]
    print("console errors  :", noise or "none")
    if state["errors"]:
        print("PAGE ERRORS     :", state["errors"])
    for name, what in shots:
        print(f"shot: {name}  ({what})")
    if noise or state["errors"]:
        return 2
    return 0 if opened else 1


if __name__ == "__main__":
    sys.exit(main())
