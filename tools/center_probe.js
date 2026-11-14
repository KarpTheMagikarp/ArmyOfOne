/*
Is every label dead centre in what it sits in, at every window size? Read off
the painted pixels, since Blink snaps backgrounds and baselines separately and
the layout numbers don't match what lands on screen. That gap is what Grid in
ui/index.html exists to close.

A box's face goes white on black with everything else off, giving its top and
bottom to a fraction of a pixel. Labels become capital H's shot invisible and
in a known ink, giving their cap band. Glyph words, the drawn i, checkboxes and
version radios get the same treatment. Each is held against the middle of its
face in device pixels across window sizes and scales, maximised and fullscreen
included. PATCH QUEUE is exempt on purpose. A second label of another size in
the same box is allowed the half pixel parity leaves.

Then Grid's placement rule itself, at 100%, 125% and 150%: each kind of label
slid a 64th of a pixel at a time has to tip onto the next row exactly when
Blink's layout says it will.

Run it on the newest Chromium too (CHROME=<path>), since WebView2 updates
itself; Chromium 153 once read a missing border as 3px and passed here.

  NODE_PATH=<wherever playwright is> node tools/center_probe.js [--quick]

Needs Chromium, like ui_probe.js, so it's not in pytest.
*/

const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
// CHROME=<path> runs another build: WebView2 is evergreen, so the one that
// matters is the newest
const CHROME = process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const QUICK = process.argv.includes("--quick");

// device px. Exact means exact: what's left is how finely an edge's coverage
// can be read. A glyph word is a sprite scaled down off a 124px sheet, and a
// half pixel move on it at 1x shifts its filtered edges by a tenth or so
const TOL = { text: 0.06, glyph: 0.15, icon: 0.1, mark: 0.1, face: 0.06, tick: 0.06, second: 0.56, level: 0.2 };

// [device width, height, display scale]: windowed 16:9, the maximised and
// fullscreen shapes of common screens (a maximised window loses the taskbar
// and a 23px caption), and the same at 125% and 150%. 1302x732 is the user's
// own small window, where the patch queue's row came away from the gauge's
const SIZES = QUICK
  ? [[1280, 720, 1], [2560, 1369, 1], [2560, 1369, 1.25]]
  : [[1280, 720, 1], [1302, 732, 1], [1408, 792, 1], [1600, 900, 1], [1777, 1000, 1], [1920, 1009, 1],
     [1920, 1080, 1], [1920, 1200, 1], [2133, 1200, 1], [2412, 1357, 1], [2560, 1369, 1],
     [2560, 1440, 1], [3440, 1369, 1], [3840, 2160, 1],
     [1920, 1080, 1.25], [2560, 1369, 1.25], [2560, 1440, 1.25],
     [1920, 1080, 1.5], [2560, 1369, 1.5], [3840, 2089, 1.5]];

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json",
  ".png": "image/png", ".jpg": "image/jpeg", ".woff2": "font/woff2", ".webm": "video/webm",
  ".mp4": "video/mp4", ".css": "text/css" };

function serve() {
  const server = http.createServer((req, res) => {
    const want = path.normalize(decodeURIComponent(req.url.split("?")[0])).replace(/^([/\\])+/, "");
    const file = path.join(ROOT, want);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); return res.end();
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(done => server.listen(0, "127.0.0.1", () => done(server)));
}

const BRIDGE = `window.pywebview = { api: {
  scan: () => Promise.resolve({ ok:true, found:true, path:"E:\\\\SteamLibrary\\\\steamapps\\\\common\\\\FINAL FANTASY XIII",
    source:"steam", supported:["ff13fix","dinput8shim","laa","reshade"], applied:["ff13fix","dinput8shim","laa","reshade"], error:null }),
  novafix_settings: () => Promise.resolve({ ok:true, exists:true, path:"E:\\\\g\\\\NovaFix.ini",
    files:[{ id:"novafix", name:"NovaFix.ini", path:"E:\\\\g\\\\NovaFix.ini", exists:true }],
    settings:[
      { section:"Display", key:"DisplayMode", label:"Window Mode", kind:"choice", file:"novafix",
        choices:["Windowed","Borderless","Borderless Fullscreen"], value:"Borderless" },
      { section:"Display", key:"Resolution", label:"Resolution", kind:"choice", file:"novafix",
        choices:["1280x720","1920x1080","2560x1440"], value:null },
      { section:"Performance", key:"FrameRateLimit", label:"Frame Rate", kind:"choice", file:"novafix",
        choices:["Unlimited","30","60","120"], value:"60" },
      { section:"Quality", key:"MSAA", label:"Anti-Aliasing", kind:"choice", file:"novafix",
        choices:["Off","2x","4x","8x"], value:null }] }),
  save_novafix_settings: () => Promise.resolve({ ok:true, written:true }),
  launch_game: () => Promise.resolve({ ok:true }),
  install_addon: () => Promise.resolve({ ok:true }),
  install_mod: () => Promise.resolve({ ok:false, kind:"versions", modName:"HD Models Plus", title:"HD Models Plus",
    jobId:"hdmodelsplus", recommended:"Ultimate", versions:["Ultimate", "Ultimate, HQ Hair", "High Quality"],
    about:{ "Ultimate":"The most detailed: rounder arms, legs and fingers.",
      "Ultimate, HQ Hair":"The same, with Lightning's sharper hair, which holds still in a few cutscenes.",
      "High Quality":"The game's own cutscene models, used everywhere." } }),
  resolve_addons_dir: () => Promise.resolve({ ok:true, path:"E:\\\\g\\\\addons" }),
  list_addons: () => Promise.resolve({ ok:true, path:"E:\\\\g\\\\addons", mods:[
    { name:"reshade", kind:"patch", jobId:"reshade", title:"Fabula-Lous Shader", about:"ReShade preset, Home opens its menu", managed:true }] }),
  hd_survey: () => Promise.resolve({ ok:true, verified:false, sizes:{ zones:9.4e9, weapons:4.6e8, eidolons:3.1e8,
    npcs:1.2e9, enemies:8.1e8, misc:2.2e8, pcs:6.4e8 } }),
  nexus_status: () => Promise.resolve({ configured:false, signedIn:false }),
  toggle_fullscreen: () => Promise.resolve({ ok:true, fullscreen:false })
} };`;

// everything measured holds still, the glove and its bubble go, and a part
// under test can be made invisible, one known ink, or a white face on black
const MODE_CSS = `
*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}
#cursor,#cursorShade,#tip{visibility:hidden!important}
[data-mt="hide"],[data-mt="hide"] *{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important}
[data-mt="white"],[data-mt="white"] *{color:#fff!important;-webkit-text-fill-color:#fff!important;text-shadow:none!important;opacity:1!important}
[data-mt="black"],[data-mt="black"] *{color:#000!important;-webkit-text-fill-color:#000!important;text-shadow:none!important;opacity:1!important}
.gl[data-mt="hide"]{visibility:hidden!important}
.gl[data-mt="white"]{filter:brightness(0) invert(1)!important;opacity:1!important}
.gl[data-mt="black"]{filter:brightness(0)!important;opacity:1!important}
.gl[data-mt] i{opacity:1!important}
[data-mt].info::before,[data-mt].why::before,[data-mt]#mdMore::before{visibility:hidden}
[data-mt="white"].info::before,[data-mt="white"].why::before,[data-mt="white"]#mdMore::before{visibility:visible;background:#fff!important;opacity:1}
[data-mt="black"].info::before,[data-mt="black"].why::before,[data-mt="black"]#mdMore::before{visibility:visible;background:#000!important;opacity:1}
body.cp-iso :is(#app,#ask,#auth) *,body.cp-iso :is(#app,#ask,#auth) *::before,body.cp-iso :is(#app,#ask,#auth) *::after{visibility:hidden!important}
body.cp-iso,body.cp-iso :is(#app,#ask,#auth),body.cp-iso #auth .card{background:#000!important;border-color:#000!important;box-shadow:none!important}
body.cp-iso :is(#app,#ask,#auth) [data-face]{visibility:visible!important;background:#fff!important;background-image:none!important;
  -webkit-mask:none!important;mask:none!important;box-shadow:none!important;filter:none!important;
  outline:none!important;opacity:1!important;color:transparent!important;-webkit-text-fill-color:transparent!important}
body.cp-iso :is(#app,#ask,#auth) [data-face="padding"]{border-color:#000!important}
body.cp-iso :is(#app,#ask,#auth) [data-face="border"]{border-color:#fff!important}
body.cp-iso :is(#app,#ask,#auth) [data-face] *,body.cp-iso :is(#app,#ask,#auth) [data-face]::before{visibility:hidden!important}
body.cp-iso :is(#app,#ask,#auth) [data-face="tick"]{background:transparent!important;border-color:transparent!important}
body.cp-iso :is(#app,#ask,#auth) [data-face="shape"],body.cp-iso :is(#app,#ask,#auth) [data-face="shape"] *{visibility:visible!important;
  background:transparent!important;filter:brightness(0) invert(1)!important;opacity:1!important}
body.cp-iso :is(#app,#ask,#auth) [data-face="tick"]::after{visibility:visible!important;background:#fff!important;box-shadow:none!important;transform:none!important}`;

/* what's on the screen that's up: each box, how its face reads, and what's
   centred on it. Parts are [kind, element, primary]. The selectors follow
   Grid's specs, but the elements are found here independently */
const TARGETS = screen => {
  const q = (s, r = document) => r.querySelector(s), qa = (s, r = document) => [...r.querySelectorAll(s)];
  const tag = el => el.dataset.cp || (el.dataset.cp = "c" + Math.random().toString(36).slice(2));
  const out = [];
  // a note that's wrapped onto two lines is a block beside its button, not a
  // label in it, and Grid leaves it be. Its own test: trimmed, one line's
  // content is its cap height, under the font size
  const D = SCALE * devicePixelRatio, px = v => parseFloat(v) || 0;
  const wrapped = el => { const cs = getComputedStyle(el);
    return el.getBoundingClientRect().height * devicePixelRatio - (px(cs.paddingTop) + px(cs.paddingBottom)
      + px(cs.borderTopWidth) + px(cs.borderBottomWidth)) * D > px(cs.fontSize) * D; };
  // a box in a closed drawer, or scrolled part way out of its list, can't be
  // read off the pixels whole
  const inView = el => {
    const r = el.getBoundingClientRect();
    if (getComputedStyle(el).visibility !== "visible" || r.left >= innerWidth || r.right <= 0
        || r.top >= innerHeight || r.bottom <= 0) return false;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      if (getComputedStyle(a).overflowY === "visible") continue;
      const c = a.getBoundingClientRect();
      if (r.top < c.top - 0.5 || r.bottom > c.bottom + 0.5) return false;
    }
    return true;
  };
  const add = (name, box, face, parts) => {
    if (!box || !box.getClientRects().length || !inView(box)) return;
    parts = parts.filter(([, el, primary]) => el && el.getClientRects().length && (primary !== false || !wrapped(el)));
    out.push({ name, box: `[data-cp="${tag(box)}"]`, face,
      parts: parts.map(([kind, el, primary]) => ({ kind, el: `[data-cp="${tag(el)}"]`, primary: primary !== false })) });
  };
  if (screen === "start") {
    for (const row of qa("#games .row"))
      add(`game ${row.dataset.id}`, row, "padding", [["text", q(".label", row)], ["glyph", q(".right .gl", row)]]);
    add("About", q("#btnAbout"), "padding", [["text", q("#btnAbout .lbl")]]);
    add("Credits", q("#btnCredits"), "padding", [["text", q("#btnCredits .lbl")]]);
    add("footer", q("#foot"), "padding", [["glyph", q("#verNum")]]);
  }
  if (screen === "game") {
    for (const row of qa("#patches .row:not(.slot)"))
      add(`row ${row.dataset.id}`, row, "padding", [["text", q(".label", row)], ["glyph", q(".chip", row)]]);
    for (const row of qa("#optional .row:not(.slot)"))
      add(`row ${row.dataset.id}`, row, "padding", [["text", q(".label > span:not(.box)", row)],
        ["glyph", q(".chip", row)], ["dot", q(".info", row)], ["check", q(".label > .box", row)]]);
    add("Apply", q("#go"), "padding", qa("#go .right .gl").map(g => ["glyph", g]).concat([["text", q("#go .lbl")]]));
    add("Launch", q("#launch"), "padding", qa("#launch .right .gl").map(g => ["glyph", g]).concat([["text", q("#launch .lbl")]]));
    add("Mods tab", q("#mdTab"), "padding", [["text", q("#mdTab span")]]);
    add("Settings tab", q("#nvTab"), "padding", [["text", q("#nvTab span")]]);
    add("ATB gauge", q("#gauge"), "border", qa(".qtop .gl").map(g => ["glyph", g]));
    add("footer", q("#foot"), "padding", [["text", q("#pathTxt")], ["text", q("#swNow")],
      ["icon", q("#pathStat > svg")], ...qa("#switcher > img").map(i => ["icon", i])]);
  }
  if (screen === "mods") {
    qa("#mdZone .pick").forEach((b, i) => add(`drop zone button ${i + 1}`, b, "border", [["text", b]]));
    qa("#mdExtras .mod > button").forEach((b, i) => add(`Worth getting button ${i + 1}`, b, "border", [["text", b]]));
    qa("#mdList .mod > button").forEach((b, i) => add(`Installed button ${i + 1}`, b, "border", [["text", b]]));
    qa("#md footer > button").forEach((b, i) => add(`Mods footer button ${i + 1}`, b, "border",
      [["text", b], ["text", q("#mdNote"), false], ["dot", q("#mdMore")]]));
    qa("#mdExtras .opt-row").slice(0, 4).forEach((r, i) => add(`HD category ${i + 1}`, r, "border",
      [["text", q(".t", r)], ["check", q(":scope > .box", r)], ["dot", q(".why", r)], ["text", q(".size", r), false]]));
    qa("#mdExtras .opts .run").forEach(b => add("HD Install button", b, "border", [["text", b]]));
  }
  if (screen === "versions") {
    qa("#mdExtras .choice .opt-row").forEach((r, i) => add(`version ${i + 1}`, r, "border",
      [["text", q(".t", r)], ["radio", q(":scope > .box", r)], ["text", q(".flag", r), false]]));
    qa("#mdExtras .asks .run").forEach(b => add("version Install button", b, "border", [["text", b]]));
  }
  if (screen === "dialogs") {
    qa("#ask button").forEach((b, i) => add(`file strip button ${i + 1}`, b, "border",
      [["text", b], ["text", q("#ask .msg"), false]]));
    qa("#auth button").forEach((b, i) => add(`sign-in button ${i + 1}`, b, "border", [["text", b]]));
    qa("#md footer > button").forEach((b, i) => add(`Mods footer, a failure, button ${i + 1}`, b, "border",
      [["text", b], ["dot", q("#mdMore")]]));
  }
  if (screen === "terminal") {
    const stop = q("#termStop");
    add("terminal Stop", stop, "border", [["text", stop], ["text", q("#termTitle"), false],
      ["text", q("#termStep"), false], ["text", q("#termPct"), false]]);
  }
  if (screen === "settings") {
    qa("#nv .sel").slice(0, 4).forEach((s, i) => add(`setting ${i + 1}`, s, "border",
      [["text", q(".face > span", s)], ["text", q(":scope > span:first-child", s.closest("label")), false]]));
    qa("#nv footer > button").forEach((b, i) => add(`Settings footer button ${i + 1}`, b, "border", [["text", b]]));
  }
  return out;
};

// both shots decoded in the page: coverage per row of what changed between
// them, against the ink it was given, or of white in an isolated shot
const COVERAGE = async ([a, b, ink, cols]) => {
  const pixels = async b64 => {
    const img = await createImageBitmap(await (await fetch("data:image/png;base64," + b64)).blob());
    const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, img.width, img.height);
  };
  const B = await pixels(b), A = a ? await pixels(a) : null;
  const lum = (d, i) => 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  const inkL = ink === "white" ? 255 : 0, cov = [];
  const [x0, x1] = cols || [0, B.width];
  for (let y = 0; y < B.height; y++) {
    let best = 0;
    for (let x = Math.max(0, x0); x < Math.min(B.width, x1); x++) {
      const i = (y * B.width + x) * 4;
      if (!A) { best = Math.max(best, lum(B.data, i) / 255); continue; }
      const under = lum(A.data, i);
      if (Math.abs(inkL - under) < 48) continue;
      best = Math.max(best, (lum(B.data, i) - under) / (inkL - under));
    }
    cov.push(Math.max(0, best));
  }
  return cov;
};

// an edge's partial row counts for the share of it that's covered. Covered
// against the ink's own body rather than its brightest row: a bold H's stems
// can top out at 0.88 with its crossbar at 1, and every stem row would read as
// a partial edge
function band(cov, y0) {
  const peak = Math.max(...cov);
  if (peak < 0.15) return null;
  const body = cov.filter(v => v > peak / 2).sort((a, b) => a - b);
  const level = body[Math.floor(body.length / 2)];
  const n = cov.map(v => Math.min(1, v / level));
  const first = n.findIndex(v => v >= 0.9), last = n.length - 1 - [...n].reverse().findIndex(v => v >= 0.9);
  let top = first, bottom = last + 1;
  for (let y = first - 1; y >= 0 && n[y] > 0.02; y--) top -= n[y];
  for (let y = last + 1; y < n.length && n[y] > 0.02; y++) bottom += n[y];
  return { top: y0 + top, bottom: y0 + bottom, mid: y0 + (top + bottom) / 2 };
}

// a clip around an element, padded a little, inside the viewport. In css px
// but on whole device pixels: at 125% a clip on a css pixel starts a quarter
// of the way into a device one, and every edge read off it would be that far out
async function clipOf(page, sel, extra = 4) {
  return page.evaluate(([sel, extra]) => {
    const k = devicePixelRatio, r = document.querySelector(sel).getBoundingClientRect();
    // whole css px that are whole device px too: 4 apart at 125%, 2 at 150%
    let step = 1;
    while (step < 8 && Math.abs(step * k - Math.round(step * k)) > 1e-6) step++;
    const down = v => Math.floor(v / step) * step, up = v => Math.ceil(v / step) * step;
    // only the start has to be on the grid: the far edge can stop at the window's
    const x0 = Math.max(0, down(r.left - 1)), x1 = Math.min(innerWidth, up(r.right + 1));
    const y0 = Math.max(0, down(r.top - extra / k)), y1 = Math.min(innerHeight, up(r.bottom + extra / k));
    return { x: x0, y: y0, width: Math.max(step, x1 - x0), height: Math.max(step, y1 - y0) };
  }, [sel, extra]);
}

async function settle(page) {
  // whatever a swap queued in Grid has run before anything is shot
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
}

// a shot of what's settled on screen. The first raster after the whole page
// was hidden and shown again can anti-alias a curve differently from the next
// (the drawn i's circle came back with its edge a few levels off, which read as
// ink and put the i 0.14px low), so it shoots until two in a row agree
async function shoot(page, clip) {
  let last = (await page.screenshot({ clip })).toString("base64");
  for (let k = 0; k < 6; k++) {
    const next = (await page.screenshot({ clip })).toString("base64");
    if (next === last) break;
    last = next;
  }
  return last;
}

// the face, white on black with everything else off
async function face(page, sel, how, dsf) {
  await page.evaluate(([sel, how]) => {
    document.querySelector(sel).dataset.face = how;
    document.body.classList.add("cp-iso");
  }, [sel, how]);
  await settle(page);
  const clip = await clipOf(page, sel);
  const shot = await shoot(page, clip);
  await page.evaluate(sel => {
    delete document.querySelector(sel).dataset.face;
    document.body.classList.remove("cp-iso");
  }, sel);
  return band(await page.evaluate(COVERAGE, [null, shot, "white"]), Math.round(clip.y * dsf));
}

// two boxes that share an edge, both white on black: any row between them
// that isn't white is a seam, the art behind showing through. Counted in the
// middle of the shorter one, clear of anything rounded at its ends
async function seam(page, upper, lower, dsf) {
  const ok = await page.evaluate(([a, b]) => {
    const els = [document.querySelector(a), document.querySelector(b)];
    if (els.some(e => !e || !e.getClientRects().length)) return false;
    els.forEach(e => { e.dataset.face = "border"; });
    document.body.classList.add("cp-iso");
    return true;
  }, [upper, lower]);
  if (!ok) return null;
  await settle(page);
  const clip = await page.evaluate(([a, b]) => {
    const r = document.querySelector(a).getBoundingClientRect(), q = document.querySelector(b).getBoundingClientRect();
    const left = Math.max(r.left, q.left), right = Math.min(r.right, q.right), mid = (left + right) / 2;
    return { x: Math.floor(mid - 20), y: Math.floor(r.top) - 2, width: 40, height: Math.ceil(q.bottom - r.top) + 4 };
  }, [upper, lower]);
  const shot = await shoot(page, clip);
  await page.evaluate(([a, b]) => {
    [a, b].forEach(s => delete document.querySelector(s).dataset.face);
    document.body.classList.remove("cp-iso");
  }, [upper, lower]);
  await settle(page);
  const cov = await page.evaluate(COVERAGE, [null, shot, "white"]);
  const lit = cov.map(v => v > 0.98);
  const first = lit.indexOf(true), last = lit.lastIndexOf(true);
  if (first < 0) return null;
  return lit.slice(first, last + 1).filter(v => !v).length;
}

/* PATCH QUEUE is where its CSS puts it, on purpose, and Grid sizing the
   footer the strip stands on mustn't move it. Read as Grid has the footer and
   again with the footer held at its own 36px, and the two have to match. Held
   from a stylesheet, since Grid writes its own inline and the H's swapped in
   to read the label set it off again. Null when Grid's footer is 36px there
   already, which says nothing either way */
async function stays(page, dsf) {
  const own = await page.evaluate(() => {
    const was = document.querySelector("#frame").style.getPropertyValue("--foot-h");
    return !was || Math.abs(parseFloat(was) - 36) < 1e-3;
  });
  if (own) return null;
  const a = await ink(page, ".qlabel", "glyph", dsf);
  await page.evaluate(() => {
    const held = document.createElement("style");
    held.id = "cpFoot";
    held.textContent = "#frame{--foot-h:var(--foot-h0)!important}";
    document.head.appendChild(held);
  });
  await settle(page);
  const b = await ink(page, ".qlabel", "glyph", dsf);
  await page.evaluate(() => document.querySelector("#cpFoot").remove());
  await settle(page);
  return a && b ? b.mid - a.mid : null;
}

// a part's ink: invisible, then in a known ink, the difference per row
async function ink(page, sel, kind, dsf) {
  await page.evaluate(([sel, kind]) => {
    const el = document.querySelector(sel);
    window.__cpSaved = [];
    if (kind === "glyph") {
      window.__cpSaved.push([el, el.innerHTML]);
      ff13(el, "HHHH");
    } else if (kind === "mark") {
      // the drawn i is a hairline at small sizes: a solid block over exactly
      // its rows measures the same placement with something thick enough
      el.style.setProperty("--i-mark", `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Crect x='6' y='5' width='8' height='10'/%3E%3C/svg%3E")`);
    } else if (kind === "text") {
      const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        if (!n.nodeValue.trim()) continue;
        window.__cpSaved.push([n, n.nodeValue]);
        n.nodeValue = "H".repeat(Math.max(1, Math.min(6, n.nodeValue.trim().length)));
      }
    }
    el.setAttribute("data-mt", "hide");
  }, [sel, kind]);
  await settle(page);
  const clip = await clipOf(page, sel, 6);
  // the i's own columns only. Its circle crosses every row it does, and a shot
  // pair that anti-aliased that edge two ways read as ink beside the block and
  // put the i 0.14px low
  const cols = kind !== "mark" ? null : await page.evaluate(([sel, clip]) => {
    const r = document.querySelector(sel).getBoundingClientRect(), k = devicePixelRatio, mid = (r.left + r.right) / 2;
    return [Math.round((mid - r.width * .15 - clip.x) * k), Math.round((mid + r.width * .15 - clip.x) * k)];
  }, [sel, clip]);
  const a = await shoot(page, clip);
  // white on anything dark, black on anything light, so the ink stands out
  const inkName = await page.evaluate(([b64, sel]) => new Promise(async done => {
    const img = await createImageBitmap(await (await fetch("data:image/png;base64," + b64)).blob());
    const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, img.width, img.height).data;
    let sum = 0;
    for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    const kind = sum / (d.length / 4) < 128 ? "white" : "black";
    document.querySelector(sel).setAttribute("data-mt", kind);
    done(kind);
  }), [a, sel]);
  const b = await shoot(page, clip);
  const cov = await page.evaluate(COVERAGE, [a, b, inkName, cols]);
  await page.evaluate(([sel, kind]) => {
    const el = document.querySelector(sel);
    el.removeAttribute("data-mt");
    if (kind === "mark") el.style.removeProperty("--i-mark");
    for (const [node, v] of window.__cpSaved.reverse()) {
      if (node.nodeType === 3) node.nodeValue = v; else node.innerHTML = v;
    }
  }, [sel, kind]);
  await settle(page);
  return band(cov, Math.round(clip.y * dsf));
}

/* The rule all of that rests on, checked on its own. Blink draws a label's
   baseline where it laid it out, rounded half up, and a translate rounded on
   its own. Where it laid it out is the bottom of the content box, its padding
   and border as laid out, read here by taking them away rather than off any
   css value: a css padding is a few thousandths over what blink lays out,
   which is all it took to put Frame Rate a pixel low. Each label is slid down
   a 64th at a time to the one where that rounding says it tips, and its ink
   has to tip on exactly that 64th, not the one before */
const RULES = {
  start: [["a game's name", "#games .row .label"]],
  game: [["a patch's name", "#patches .row:not(.slot) .label"], ["Fabula-Lous Shader", "#optional .row .label > span:not(.box)"],
    ["Apply", "#go .lbl"], ["Mods tab", "#mdTab span"]],
  mods: [["an HD category", "#mdExtras .opt-row .t"], ["a Mods footer button", "#md footer > button"]],
  settings: [["a setting's name", "#nv label > span:first-child"], ["a setting's value", "#nv .sel .face > span"]],
  terminal: [["the terminal's title", "#termTitle"], ["the terminal's Stop", "#termStop"]],
  dialogs: [["the file strip", "#ask .msg"], ["a file strip button", "#ask button"], ["a sign-in button", "#auth button"]],
};

async function tipping(page, sel, dsf) {
  const at = await page.evaluate(sel => {
    const el = document.querySelector(sel);
    if (!el || !el.getClientRects().length || getComputedStyle(el).visibility !== "visible") return null;
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > innerHeight) return null;
    const k = devicePixelRatio, D = (document.getElementById("app").contains(el) ? SCALE : 1) * k;
    const unit = v => Math.round(v * 64) / 64;
    const saved = { el, style: el.getAttribute("style"), text: [] };
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      saved.text.push([n, n.nodeValue]);
      n.nodeValue = "H".repeat(Math.max(1, Math.min(6, n.nodeValue.trim().length)));
    }
    window.__cpRule = saved;
    el.style.translate = "none";
    const cs = getComputedStyle(el);
    if (cs.position === "static") el.style.position = "relative";
    // blink's 64ths, cut toward zero, and a length written a 256th further out
    // so the float it goes through can't cut it a 64th short
    const laid = v => Math.sign(v) * Math.floor(Math.abs(v) * 64 + 1e-3) / 64;
    const top = el.computedStyleMap().get("top");
    const base = top && top.unit === "px" ? laid(top.value * D) : 0;
    // what's under the content box, as laid out: the height it gives back
    let under = 0;
    if (cs.display !== "inline") {
      const had = [el.style.paddingBottom, el.style.borderBottomWidth], h = el.getBoundingClientRect().height;
      el.style.paddingBottom = el.style.borderBottomWidth = "0px";
      under = (h - el.getBoundingClientRect().height) * k;
      [el.style.paddingBottom, el.style.borderBottomWidth] = had;
    }
    window.__cpSlide = n => {
      const v = base + n / 64;
      el.style.top = (v + Math.sign(v) / 256) / D + "px";
      return unit(el.getBoundingClientRect().bottom * k - under);
    };
    const row = Math.floor(window.__cpSlide(0) + 0.5);
    let n = 1;
    while (n < 64 && Math.floor(window.__cpSlide(n) + 0.5) === row) n++;
    window.__cpSlide(0);
    return n;
  }, sel);
  if (!at) return null;
  const inkAt = async n => {
    await page.evaluate(n => window.__cpSlide(n), n);
    const b = await ink(page, sel, "text", dsf);
    return b ? b.bottom : NaN;
  };
  const from = at > 1 ? await inkAt(0) : null, before = await inkAt(at - 1), after = await inkAt(at);
  await page.evaluate(() => {
    const { el, style, text } = window.__cpRule;
    for (const [n, v] of text) n.nodeValue = v;
    if (style === null) el.removeAttribute("style"); else el.setAttribute("style", style);
  });
  await settle(page);
  // put up to that 64th, a pixel down on it. Where the ink sits on its
  // baseline is the H's own (a big one hangs a pixel under), and the checks
  // above hold that to the canvas Grid reads it off
  const err = Math.max(from === null ? 0 : Math.abs(before - from), Math.abs(after - before - 1));
  return Number.isFinite(err) ? err : 99;
}

// every letter of a glyph word against the others, on a flat grey
async function levelness(page, dsf) {
  const worst = [];
  for (const gh of [8, 13]) for (const word of ["APPLIED", "REQUIRED", "QUEUED", "WORKING", "ATB", "PATCH QUEUE", "0123456789"]) {
    const cells = await page.evaluate(([word, gh]) => {
      let host = document.getElementById("__lv");
      if (!host) {
        host = document.createElement("div"); host.id = "__lv";
        host.style.cssText = "position:absolute;left:40px;top:40px;z-index:999;background:#808080;padding:6px;display:flex";
        document.getElementById("app").appendChild(host);
      }
      host.innerHTML = "";
      const s = glyph(word, "gl"); s.style.setProperty("--gh", gh + "px");
      s.style.filter = "brightness(0) invert(1)";
      host.appendChild(s);
      return [...s.children].filter(i => i.className !== "sp").map(i => {
        const r = i.getBoundingClientRect(); return [r.left, r.right, r.top, r.bottom];
      });
    }, [word, gh]);
    await settle(page);
    const top = Math.floor(Math.min(...cells.map(c => c[2]))) - 2, bot = Math.ceil(Math.max(...cells.map(c => c[3]))) + 2;
    const left = Math.floor(cells[0][0]) - 1, right = Math.ceil(cells[cells.length - 1][1]) + 1;
    const shot = await shoot(page, { x: left, y: top, width: right - left, height: bot - top });
    const centres = await page.evaluate(async ([b64, cells, left, dsf]) => {
      const img = await createImageBitmap(await (await fetch("data:image/png;base64," + b64)).blob());
      const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d");
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, img.width, img.height);
      return cells.map(cell => {
        const x0 = Math.max(0, Math.round((cell[0] - left) * dsf)), x1 = Math.min(d.width, Math.round((cell[1] - left) * dsf));
        const cov = [];
        for (let y = 0; y < d.height; y++) {
          let best = 0;
          for (let x = x0; x < x1; x++) {
            const i = (y * d.width + x) * 4;
            best = Math.max(best, ((d.data[i] + d.data[i + 1] + d.data[i + 2]) / 3 - 128) / 127);
          }
          cov.push(Math.max(0, Math.min(1, best)));
        }
        const peak = Math.max(...cov), n = cov.map(v => v / peak);
        const f = n.findIndex(v => v >= 0.9), l = n.length - 1 - [...n].reverse().findIndex(v => v >= 0.9);
        let t = f, b = l + 1;
        for (let y = f - 1; y >= 0 && n[y] > 0.02; y--) t -= n[y];
        for (let y = l + 1; y < n.length && n[y] > 0.02; y++) b += n[y];
        return (t + b) / 2;
      });
    }, [shot, cells, left, dsf]);
    worst.push([`${word} at ${gh}px`, Math.max(...centres) - Math.min(...centres)]);
  }
  await page.evaluate(() => { const h = document.getElementById("__lv"); if (h) h.remove(); });
  return worst;
}

// the window's inside to the size asked for: --window-size is the outside,
// and the headless frame takes a toolbar's worth off it
async function inner(ctx, page, vw, vh) {
  const cdp = await ctx.newCDPSession(page);
  const { windowId } = await cdp.send("Browser.getWindowForTarget");
  for (let k = 0; k < 4; k++) {
    const [iw, ih] = await page.evaluate(() => [innerWidth, innerHeight]);
    if (iw === vw && ih === vh) return;
    const { bounds } = await cdp.send("Browser.getWindowBounds", { windowId });
    await cdp.send("Browser.setWindowBounds", { windowId, bounds: { width: bounds.width + vw - iw, height: bounds.height + vh - ih } });
    await page.waitForTimeout(100);
  }
}

// Grid only ever moves things up and down, and nothing but the thing it moves.
// Anything it made relative to take a top has its left and right back at auto,
// or a rule meant for another case (the patch rows' checkbox indent) slides it
// sideways, and holds nothing positioned, which would be anchored to it now
// instead of to whatever it was laid out against (a caveat's bubble, meant to
// span its row, shrank to the i's circle)
const SIDEWAYS = () => {
  const out = [], placed = e => ["absolute", "fixed"].includes(e);
  for (const e of document.querySelectorAll("#app *")) {
    if (!e.style.top || getComputedStyle(e).position !== "relative") continue;
    const name = e.id || String(e.className.baseVal ?? e.className), cs = getComputedStyle(e);
    if (!["auto", "0px"].includes(cs.left) || !["auto", "0px"].includes(cs.right))
      out.push(`${name} left ${cs.left} right ${cs.right}, moved sideways`);
    const was = e.style.position;
    e.style.position = "";
    const own = getComputedStyle(e).position;
    e.style.position = was;
    if (own !== "static") continue;
    const held = [...e.querySelectorAll("*")].filter(d => placed(getComputedStyle(d).position))
      .map(d => d.id || String(d.className.baseVal ?? d.className) || d.tagName.toLowerCase());
    for (const p of ["::before", "::after"]) if (placed(getComputedStyle(e, p).position)) held.push(p);
    if (held.length) out.push(`${name} made relative, now the anchor for ${held.join(", ")}`);
  }
  return out;
};

async function screens(page, url, sweep) {
  await page.goto(url);
  await page.waitForFunction(() => window.__uiReady, null, { timeout: 15000 });
  await page.waitForTimeout(1200);
  await page.mouse.move(2, 2);
  await page.addStyleTag({ content: MODE_CSS });
  await page.evaluate(() => document.querySelectorAll("video").forEach(v => v.pause()));
  await sweep("start");
  await page.evaluate(() => document.querySelector('#games .row[data-id="ff13"]').click());
  await page.waitForTimeout(2600);
  await sweep("game");
  await page.evaluate(() => document.querySelector("#mdTab").click());
  await page.waitForTimeout(1200);
  // an HD Project folder dropped, so its categories and Install are up too
  await page.evaluate(() => Mods.showOptions({ kind: "hdproject", jobId: "hdproject",
    detail: { root: "E:\\HD", name: "FFXIII HD Project" } }));
  await page.waitForTimeout(800);
  await page.evaluate(() => { const r = document.querySelector("#mdExtras .mod.has-opts"); if (r) r.dataset.open = "true"; });
  await page.waitForTimeout(300);
  await sweep("mods");
  // HD Models Plus dropped too, its versions a choice under its own row
  await page.evaluate(() => { const r = document.querySelector("#mdExtras .mod.has-opts"); if (r) r.dataset.open = "false"; });
  await page.evaluate(() => Mods.accept("E:\\Downloads\\HD Models Plus.zip", { title: "HD Models Plus", game: "ff13" }));
  await page.waitForTimeout(800);
  await sweep("versions");
  await page.evaluate(() => document.querySelector("#nvTab").click());
  await page.waitForTimeout(1000);
  await sweep("settings");
  // an install's terminal, part way through
  await page.evaluate(() => { closeDrawers(); Term.open("Installing FFXIII HD Project");
    Term.step("Environmental Zones", 2, 7); Term.within(0.4); });
  await page.waitForTimeout(500);
  await sweep("terminal");
  await page.evaluate(() => Term.close());
  // a run that needs a file asks along the bottom, the sign-in is its own
  // window, and a failure with more to say puts an i beside the Mods note
  await page.evaluate(() => { closeDrawers(); Drop.request("ff13fix", "Opening the mod page. Log in if you need to, then press Download."); });
  await page.waitForTimeout(400);
  await sweep("dialogs");
  await page.evaluate(() => { Drop.finish(null); Auth.open(window.pywebview.api, { state: { configured: false }, names: ["NovaFix", "Audio Fix"] }); });
  await page.waitForTimeout(500);
  await sweep("dialogs");
  await page.evaluate(() => { document.querySelector("#authSkip").click(); document.querySelector("#mdTab").click(); });
  await page.waitForTimeout(1000);
  await page.evaluate(() => Mods.fail("Couldn't install Lightning Hair. The archive is damaged.", "7-Zip said: Data Error in encrypted file."));
  await page.waitForTimeout(500);
  await sweep("dialogs");
}

(async () => {
  const server = await serve();
  const url = `http://127.0.0.1:${server.address().port}/ui/index.html`;
  let bad = 0;
  const fail = line => { bad++; console.log("  FAIL  " + line); };
  const seen = {};

  for (const [W, H, dsf] of SIZES) {
    const vw = Math.round(W / dsf), vh = Math.round(H / dsf);
    /* a real scale factor, not Playwright's emulated one. Emulated, blink lays
       out and snaps in css px and scales the raster after, so a 1px border is
       1.25 device px and nothing can sit on the device grid. A display at 125%
       (WebView2 included) lays out in device px, snaps to them and floors a
       border to a whole one, and that's what has to be measured */
    const real = await chromium.launch({ executablePath: CHROME,
      args: [`--force-device-scale-factor=${dsf}`, `--window-size=${vw},${vh}`] });
    const ctx = await real.newContext({ viewport: null });
    const page = await ctx.newPage();
    await page.setContent("<body></body>");
    await inner(ctx, page, vw, vh);
    page.on("pageerror", e => console.log("page error:", e.message));
    await page.addInitScript(BRIDGE);
    const size = `${W}x${H}${dsf === 1 ? "" : " @" + dsf}`;
    await screens(page, url, async screen => {
      await settle(page);
      for (const what of await page.evaluate(SIDEWAYS)) fail(`${screen} at ${size}: ${what}`);
      for (const t of await page.evaluate(TARGETS, screen)) {
        const f = await face(page, t.box, t.face, dsf);
        if (!f) continue;
        const key = `${screen}: ${t.name}`;
        const rows = (seen[key] = seen[key] || []);
        // the face itself on whole pixels: a crisp box has no partial edge row
        const fr = Math.max(Math.abs(f.top - Math.round(f.top)), Math.abs(f.bottom - Math.round(f.bottom)));
        rows.push({ size, what: "face edges off the pixel grid", err: fr, tol: TOL.face });
        for (const p of t.parts) {
          const kinds = p.kind === "dot" ? ["face", "mark"] : /^(check|radio)$/.test(p.kind) ? ["face", "tick"] : [p.kind];
          for (const k of kinds) {
            const m = k === "face" ? await face(page, p.el, "border", dsf)
              : k === "tick" ? await face(page, p.el, "tick", dsf)
              : k === "icon" ? await face(page, p.el, "shape", dsf)
              : await ink(page, p.el, k, dsf);
            if (!m) continue;
            const what = `${p.kind}${k !== p.kind ? " " + k : ""}${p.primary ? "" : " (second size)"}`;
            // a round dot's edge reads to a tenth either way, the way the i's circle does,
            // where a square tick's reads exact
            const tol = !p.primary ? TOL.second : p.kind === "radio" && k === "tick" ? TOL.mark : (TOL[k] || TOL.icon);
            rows.push({ size, what, err: m.mid - f.mid, tol });
          }
        }
      }
      if (screen === "game") {
        const gap = await seam(page, ".qrow-label", ".qrow-bar", dsf);
        if (gap !== null) (seen["game: PATCH QUEUE's row against the gauge's"] = seen["game: PATCH QUEUE's row against the gauge's"] || [])
          .push({ size, what: "rows of something else between them", err: gap, tol: 0 });
        const moved = await stays(page, dsf);
        if (moved !== null) (seen["game: PATCH QUEUE where its CSS puts it"] = seen["game: PATCH QUEUE where its CSS puts it"] || [])
          .push({ size, what: "moved by Grid's footer", err: moved, tol: 0 });
      }
      if (!QUICK && dsf === 1 && W !== 1280 && !(W === 2560 && H === 1369)) return;
      for (const [name, sel] of RULES[screen] || []) {
        const err = await tipping(page, sel, dsf);
        if (err === null) continue;
        const key = `rounding: ${name}`;
        (seen[key] = seen[key] || []).push({ size, what: "ink tips on the 64th the layout says", err, tol: TOL.text });
      }
    });
    if (dsf === 1 && (W === 1600 || W === 2560 && H === 1369)) {
      for (const [name, spread] of await levelness(page, dsf)) {
        const key = "glyph words";
        (seen[key] = seen[key] || []).push({ size, what: `${name}, highest to lowest letter`, err: spread, tol: TOL.level });
      }
    }
    await real.close();
  }

  console.log(`off the middle of what they sit in, device px, ${SIZES.length} window sizes`);
  for (const [name, rows] of Object.entries(seen)) {
    const by = {};
    for (const r of rows) (by[r.what] = by[r.what] || []).push(r);
    for (const [what, list] of Object.entries(by)) {
      const worst = list.reduce((m, v) => Math.abs(v.err) > Math.abs(m.err) ? v : m);
      const over = list.filter(v => Math.abs(v.err) > v.tol);
      const line = `${name}, ${what}: worst ${worst.err.toFixed(3)} (${worst.size})`
        + (over.length ? `, over at ${over.map(v => `${v.size} ${v.err.toFixed(2)}`).join(", ")}` : "");
      if (over.length) fail(line); else console.log("  ok    " + line);
    }
  }
  server.close();
  console.log(bad ? `${bad} off` : "all centred");
  process.exit(bad ? 1 : 0);
})();
