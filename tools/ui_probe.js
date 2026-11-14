/*
The checks that need a real layout engine.

The jsdom probes next to this one can read the DOM but not measure it, so
anything answered by "where did this end up on screen" lives here: the stage
scaling rather than stretching, the drawer tabs sitting in the help bar, and the
Launch row appearing only when the game is worth starting.

  NODE_PATH=<wherever playwright is> node tools/ui_probe.js
*/

const path = require("path");
const { chromium } = require("playwright");

const UI = "file://" + path.resolve(__dirname, "..", "ui", "index.html");
// CHROME=<path> runs another build: WebView2 is evergreen, so the one that
// matters is the newest
const CHROME = process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

// XIII-2: two required, two optional. Matches ui/jobs.json.
const REQUIRED = ["ff132fix", "laa"];

function bridge({ found = true, applied = [], proxy = true } = {}) {
  // proxy false drops the two methods the drawers gate on, which is what a
  // game with no NovaFix installed looks like from the page's side
  const drawers = proxy ? `
    novafix_settings: () => Promise.resolve({ ok:true, exists:true, path:"E:\\\\g\\\\NovaFix.ini",
      settings:[{section:"Graphics", key:"Resolution", label:"Resolution", kind:"choice",
                 choices:["1920x1080","2560x1440"], value:"1920x1080"}] }),
    save_novafix_settings: (g, c) => Promise.resolve({ ok:true, written:true,
      gameRunning: !!window.__gameRunning }),
    launch_game: () => { window.__launched = true; return Promise.resolve({ ok:true, via:"steam" }); },
    install_addon: () => Promise.resolve({ ok:true }),` : "";
  return `window.pywebview = { api: {
    scan: () => Promise.resolve({ ok:true, found:${found}, path:"E:\\\\g",
      source:"steam", supported:${JSON.stringify([...REQUIRED, "audio"])},
      applied:${JSON.stringify(applied)}, error:null }),${drawers}
    toggle_fullscreen: () => { window.__full = !window.__full; window.__toggles = (window.__toggles || 0) + 1;
      return Promise.resolve({ ok:true, fullscreen: window.__full }); },
    resolve_addons_dir: () => Promise.resolve({ ok:true, path:"E:\\\\g\\\\addons" }),
    list_addons: () => Promise.resolve({ ok:true, path:"E:\\\\g\\\\addons", mods:[] }),
    nexus_status: () => Promise.resolve({ configured:false, signedIn:false })
  } };`;
}

async function open(browser, opts = {}, size = [1920, 1017]) {
  const page = await browser.newPage({ viewport: { width: size[0], height: size[1] } });
  await page.addInitScript(bridge(opts));
  // two cores and SwiftShader read as a machine for the fog's still. A case
  // about the fog clip itself says otherwise
  if (opts.fog) await page.addInitScript(() => {
    Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 8 });
    // the renderer is asked from a worker where it can be, and the worker
    // would get the real one. Without OffscreenCanvas the page asks itself
    Object.defineProperty(window, "OffscreenCanvas", { value: undefined });
    const real = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
      if (kind === "webgl") return { getExtension: () => null, getParameter: () => "ANGLE (probe)" };
      return real.call(this, kind, ...rest);
    };
  });
  await page.goto(UI);
  await page.waitForTimeout(800);
  if (opts.game !== false) {
    await page.evaluate(() => {
      [...document.querySelectorAll("#games .row")]
        .find(li => li.textContent.includes("XIII-2")).click();
    });
    await page.waitForTimeout(1000);
  }
  return page;
}

const shown = sel => `(() => {
  const el = document.querySelector("${sel}");
  if (!el) return false;
  const s = getComputedStyle(el), b = el.getBoundingClientRect();
  return s.display !== "none" && s.opacity !== "0" && b.width > 2 && b.height > 2;
})()`;

/* A closed drawer is not hidden, it is translated out and clipped by #app's
   overflow, so it still measures full size wherever it is parked. Asking
   whether it has actually come inside the stage is the only test that tells
   an open drawer from a closed one. */
const onStage = sel => `(() => {
  const el = document.querySelector("${sel}");
  if (!el) return false;
  const b = el.getBoundingClientRect(), a = document.querySelector("#app").getBoundingClientRect();
  return b.left < a.right - 20;
})()`;

/* The drawers arrive on a .26s transition, so a fixed sleep is a race. Poll
   for the state the check is about and give up at a deadline, which turns a
   slow frame into a wait rather than an intermittent failure. */
async function settles(page, expr, want, limit = 2000) {
  const until = Date.now() + limit;
  let got = null;
  while (Date.now() < until) {
    got = await page.evaluate(expr);
    if (got === want) return got;
    await page.waitForTimeout(40);
  }
  return got;
}

let bad = 0;
function check(name, got, want, why = "") {
  const ok = got === want;
  if (!ok) bad++;
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}` + (ok ? "" : `: got ${got}, wanted ${want}`)
              + (ok || !why ? "" : ` (${why})`));
}

async function launchCases(browser) {
  const cases = [
    ["fresh install, nothing applied", { applied: [] }, false],
    ["already patched before the app opened", { applied: [...REQUIRED] }, true],
    ["one required still missing", { applied: ["ff132fix"] }, false],
    ["patched, optional left unticked", { applied: [...REQUIRED, "audio"] }, true],
    ["game folder not found", { found: false }, false],
  ];
  for (const [name, opts, want] of cases) {
    const page = await open(browser, opts);
    check(name, await page.evaluate(shown("#launchWrap")), want);
    await page.close();
  }

  // ticking an extra onto an already-working game must not retract the offer
  const page = await open(browser, { applied: [...REQUIRED] });
  await page.click('.row[data-id="audio"]');
  await page.waitForTimeout(300);
  check("patched, then an extra ticked on", await page.evaluate(shown("#launchWrap")), true);
  await page.close();
}

async function cornerBarCases(browser) {
  /* The bar fades with the clip, so it's there as the game's screen comes
     up rather than travelling in after the panels, which read as late. */
  {
    const fresh = await open(browser, { game: false });
    await fresh.evaluate(() => {
      window.__bar = [];
      const t0 = performance.now();
      const loop = () => {
        const el = document.querySelector("#toolBar");
        if (el) window.__bar.push(getComputedStyle(el).transform);
        if (performance.now() - t0 < 800) requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });
    await fresh.evaluate(() => [...document.querySelectorAll("#games .row")]
      .find(li => li.textContent.includes("XIII-2")).click());
    await fresh.waitForTimeout(900);
    const seen = await fresh.evaluate(() => [...new Set(window.__bar)]);
    const offsets = seen.map(t => {
      const m = /matrix\(1, 0, 0, 1, ([-\d.]+)/.exec(t);
      return m ? Math.abs(parseFloat(m[1])) : 0;
    });
    check("the corner bar never slides in",
          Math.max(0, ...offsets), 0);
    await fresh.close();
  }

  const page = await open(browser, { applied: [...REQUIRED] });

  const placed = await page.evaluate(() => {
    const box = sel => document.querySelector(sel).getBoundingClientRect();
    const bar = box("#toolBar"), stage = box("#stage"), help = box("#help");
    const md = box("#mdTab"), nv = box("#nvTab");
    return {
      inFrame: !!document.querySelector("#frame > #toolBar"),
      outOfMenu: !document.querySelector("#sectGo #mdTab, #sectGo #nvTab"),
      // horizontal, not the vertical labels it used to be
      horizontal: bar.width > bar.height * 1.6 && md.width > md.height,
      // and lighter than a menu row. it shipped once at 40 against 30px rows
      // and a 50px help bar, which read as bulky rather than as chrome
      barH: Math.round(bar.height), rowH: Math.round(box(".row").height),
      helpH: Math.round(help.height),
      // hard against the help bar's bottom edge: a pixel of art between them
      // read as a separate strip floating under it
      underHelp: Math.round((bar.top - help.bottom) * 4) / 4,
      // hard into the corner
      rightInset: Math.round(stage.right - bar.right),
      // mods on the left, and the two sit side by side on one line
      order: md.right <= nv.left && Math.abs(md.top - nv.top) < 1,
      label: document.querySelector("#nvTab").textContent,
      // the drawer heading is the selected half of the tab pair now, not an h2
      heading: document.querySelector("#nv header .dtab.now").textContent,
      // the underline belongs to each tab, so the seam between them cuts it in
      // two and they read as two buttons rather than one strip with a rule
      // under it. lifted off .row .lit rather than picked by eye, so this
      // compares the two rather than trusting a hex.
      rule: getComputedStyle(document.querySelector("#mdTab")).borderBottomColor,
      barRule: getComputedStyle(document.querySelector("#toolBar")).borderBottomStyle,
      // and the bar is open on both sides, the way the patch rows are
      sides: getComputedStyle(document.querySelector("#toolBar")).borderLeftStyle
           + "/" + getComputedStyle(document.querySelector("#toolBar")).borderRightStyle,
      // no daylight between them. a gap at any width reads as two separate
      // buttons, and the divider only has to say "two options"
      split: Math.round(nv.left - md.right),
      // #app carries a zoom, so computed widths come back divided by it. the
      // seam is measured against the underline rather than against a px value.
      seam: parseFloat(getComputedStyle(document.querySelector("#nvTab")).borderLeftWidth)
          / parseFloat(getComputedStyle(document.querySelector("#nvTab")).borderBottomWidth),
      seamAlpha: getComputedStyle(document.querySelector("#nvTab")).borderLeftColor,
      // and the first tab grows no stray line down its left
      firstSeam: parseFloat(getComputedStyle(document.querySelector("#mdTab")).borderLeftWidth),
      // the gradient stop the underline is supposed to be the same material as
      rowGrey: getComputedStyle(document.querySelector(".row .lit"))
        .backgroundImage.includes("183, 187, 192"),
    };
  });
  check("the bar lives in the frame, over the rule under the help bar", placed.inFrame, true);
  check("and not among the menu rows", placed.outOfMenu, true);
  check("it reads horizontally", placed.horizontal, true);
  check("lighter than a menu row", placed.barH < placed.rowH, true);
  check("and well under the help bar", placed.barH < placed.helpH * 0.6, true);
  check("it touches the help bar's bottom edge", placed.underHelp, 0);
  check("hard into the top right corner", placed.rightInset, 0);
  check("mods left of settings, one line", placed.order, true);
  check("the settings tab says Settings", placed.label, "Settings");
  check("so does the drawer heading", placed.heading, "Settings");
  check("the underline is the row slab's grey, not gold", placed.rule, "rgb(183, 187, 192)");
  check("and it is the rows' own grey", placed.rowGrey, true);
  check("the tabs touch, no daylight between them", placed.split, 0);
  check("a hairline seam marks the second one, thinner than the underline",
        placed.seam > 0 && placed.seam <= 0.5, true);
  check("and it is faint, not an edge", /0\.1[0-9]?\)/.test(placed.seamAlpha), true);
  check("the first tab has no stray line", placed.firstSeam, 0);
  check("the bar itself has no underline", placed.barRule, "none");
  check("and no sides, it runs off the edge like a row", placed.sides, "none/none");

  /* The keyboard owns the vertical list and nothing else. The corner opens
     drawers full of dropdowns and a drop zone, which are pointer controls, so
     walking into them with WASD was a second and worse way to use them.
     Driven with real key events, because this is entirely about what the
     handlers do with focus. */
  const where = () => page.evaluate(() => {
    const a = document.activeElement;
    return a && a.id ? a.id : (a && a.closest && a.closest(".row")
      ? "row:" + (a.closest(".row").dataset.id || "?") : "none");
  });
  const press = async k => { await page.keyboard.press(k); await page.waitForTimeout(120); };

  await page.evaluate(() => {
    const first = [...document.querySelectorAll("#patches .row:not(.slot)")][0];
    if (first) first.focus();
  });
  check("starting on the first patch row", (await where()).startsWith("row:"), true);

  // up off the first row wraps to the bottom of the list, which is Launch
  // game. It used to hop sideways into the corner instead.
  const last = await page.evaluate(() => {
    const r = ring();
    return r.length ? (r[r.length - 1].id || "row:" + (r[r.length - 1].dataset.id || "?")) : "none";
  });
  await press("ArrowUp");
  check("up from the top wraps to the end of the list", await where(), last);
  check("which is Launch game", last, "launch");

  await press("ArrowDown");
  check("and back down to the first row", (await where()).startsWith("row:"), true);

  // down off the last row wraps to the top, the other half of the same ring
  await page.evaluate(() => {
    const rows = ring();
    if (rows.length) rows[rows.length - 1].focus();
  });
  await press("ArrowDown");
  const landed = await where();
  check("down off the end wraps to the first row", landed.startsWith("row:"), true);
  check("and never into the corner", landed === "mdTab" || landed === "nvTab", false);

  /* swept straight past, a row's hint doesn't stay up. The blank said on the
     way out had the line's own frame take its fade back off, so it never
     cleared */
  const swept = await page.evaluate(async () => {
    const [one, two] = document.querySelectorAll("#patches .row:not(.slot)");
    // another row's line up first, or the sweep's own is already showing
    two.dispatchEvent(new MouseEvent("mouseenter"));
    await new Promise(r => setTimeout(r, 400));
    one.dispatchEvent(new MouseEvent("mouseenter"));
    one.dispatchEvent(new MouseEvent("mouseleave"));
    await new Promise(r => setTimeout(r, 450));
    return document.getElementById("hintText").textContent;
  });
  check("a row swept straight past leaves no hint up", swept, "");

  // the corner is off the ring AND off the tab order, so focus cannot arrive
  // there by any route the keyboard has
  const corner = await page.evaluate(() => ({
    inRing: ring().some(el => el.id === "mdTab" || el.id === "nvTab"),
    tabbable: [...document.querySelectorAll("#toolBar button")]
                .some(b => b.tabIndex >= 0),
  }));
  check("the tabs are not rows in the list", corner.inRing, false);
  check("and not in the tab order either", corner.tabbable, false);

  // a real press, not el.click(), so an overlay swallowing the hit shows up
  await page.click("#mdTab");
  check("clicking a tab opens its drawer", await settles(page, onStage("#md"), true), true);

  await page.keyboard.press("Escape");
  check("escape closes it", await settles(page, onStage("#md"), false), false);

  await page.click("#nvTab");
  check("the other tab opens its own drawer", await settles(page, onStage("#nv"), true), true);
  // the stage is centred now, so an "outside" click has to be inside #app and
  // clear of the drawer rather than anywhere in the viewport
  const spot = await page.evaluate(() => {
    const a = document.querySelector("#app").getBoundingClientRect();
    return [Math.round(a.left + a.width * 0.25), Math.round(a.top + a.height * 0.7)];
  });
  await page.mouse.click(spot[0], spot[1]);
  check("clicking outside closes it", await settles(page, onStage("#nv"), false), false);

  // the game list has its own art up in that corner
  await page.keyboard.press("Escape");
  check("gone on the startup screen", await settles(page, shown("#toolBar"), false), false);
  await page.close();

  // no proxy installed means no tabs, and no empty slab where they would be
  const bare = await open(browser, { applied: [...REQUIRED], proxy: false });
  check("no tabs without the proxy", await bare.evaluate(shown("#mdTab")), false);
  check("and no empty bar behind them", await bare.evaluate(shown("#toolBar")), false);
  await bare.close();
}

/* NovaFix reads its ini once at startup, so a graphics change costs a restart.
   That is the workflow the whole launcher detour was about, and the part of it
   this app controls is how many presses the restart takes. */
async function restartLoopCases(browser) {
  const page = await open(browser, { applied: [...REQUIRED] });
  await page.click("#nvTab");
  await page.waitForTimeout(500);

  // a change has to be pending or Apply reports "Nothing changed"
  await page.selectOption("#nv select", "2560x1440");
  await page.click("#nvSave");
  await page.waitForTimeout(600);

  check("the restart is offered where the change was made",
        await page.evaluate(shown("#nvPlay")), true);
  check("and the drawer waits rather than closing under it",
        await page.evaluate(onStage("#nv")), true);

  await page.click("#nvPlay");
  check("pressing it starts the game",
        await settles(page, "!!window.__launched", true), true);
  check("and closes the drawer", await settles(page, onStage("#nv"), false), false);
  await page.close();

  // nothing to offer when the game is already up: the settings cannot reach it
  const running = await open(browser, { applied: [...REQUIRED] });
  await running.evaluate(() => { window.__gameRunning = true; });
  await running.click("#nvTab");
  await running.waitForTimeout(500);
  await running.selectOption("#nv select", "2560x1440");
  await running.click("#nvSave");
  await running.waitForTimeout(600);
  check("no restart offer while the game is running",
        await running.evaluate(shown("#nvPlay")), false);
  await running.close();
}

/* The ghost box. Backing out of a game used to leave the cutscene's poster
   still parented in #cinema at full opacity, cleared only if you happened to
   enter another game, because the harvest runs on the way IN. The layer's own
   fade was supposed to hide it and lost the race with the menu sliding in, so
   for a few hundred milliseconds a bright full stage image sat over a list
   that had not faded up yet.

   It fades again now, on purpose, with the patch list leaving on a stagger
   over it and the start page arriving into it. So what's held is the thing
   that went wrong: every frame of the way back, a clip that's still mostly up
   has a list on it, and once it's down the layer is empty. */
async function leavingCases(browser) {
  const page = await open(browser, { applied: [...REQUIRED] });

  const inside = await page.evaluate(() => document.querySelectorAll("#cinema > *").length);
  check("entering a game puts something in the cinema layer", inside > 0, true);

  await page.evaluate(() => {
    window.__back = [];
    const most = sel => Math.max(0, ...[...document.querySelectorAll(sel)]
      .map(el => Number(getComputedStyle(el).opacity)));
    const t0 = performance.now();
    const look = () => {
      window.__back.push({
        clip: Number(getComputedStyle(document.querySelector("#cinema")).opacity),
        list: Math.max(most("#m2list .sect .row, #m2list .sect .hdr, #goWrap") *
                       Number(getComputedStyle(document.querySelector("#menu2")).visibility === "visible"),
                       most("#games .row, #m1list > .hdr")) });
      if (performance.now() - t0 < 1500) requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  });
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1700);
  const frames = await page.evaluate(() => window.__back);
  const bare = frames.filter(f => f.clip > 0.5 && f.list < 0.3);
  check("the clip is never up without a list on it", bare.length, 0);
  check("it fades rather than cuts", frames.some(f => f.clip > 0.05 && f.clip < 0.95), true);
  const after = await page.evaluate(() => ({
    op: Number(getComputedStyle(document.querySelector("#cinema")).opacity),
    left: document.querySelectorAll("#cinema > *").length,
  }));
  check("and once it's down the layer is out", after.op, 0);
  check("and nothing is left on it", after.left, 0);

  // three round trips, because the leak only showed up as accumulation
  for (let i = 0; i < 3; i++) {
    await page.evaluate(() => [...document.querySelectorAll("#games .row")]
      .find(li => li.textContent.includes("XIII-2")).click());
    await page.waitForTimeout(900);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
  }
  const left = await settles(page, `document.querySelectorAll("#cinema > *").length`, 0);
  check("nothing accumulates over repeated visits", left, 0);
  await page.close();

  /* Escape with the mouse: back() hides the glove and hands the keyboard the
     glove it'll come back in on, and switching modes used to un-hide the
     shadow on its own. It sat where the mouse had been, glove-less, for the
     whole change of screen */
  const mouse = await open(browser, { applied: [...REQUIRED] });
  for (const [x, y] of [[640, 420], [700, 460], [760, 500]]) {
    await mouse.mouse.move(x, y);
    await mouse.waitForTimeout(60);
  }
  const mouseAt = await mouse.evaluate(() => {
    window.__shade = [];
    const cur = document.querySelector("#cursor");
    const op = sel => Number(getComputedStyle(document.querySelector(sel)).opacity);
    const t0 = performance.now();
    const look = () => {
      window.__shade.push({ glove: op("#cursor"), shade: op("#cursorShade"),
                            keys: document.body.classList.contains("keys"),
                            kind: cur.dataset.kind, at: cur.style.translate });
      if (performance.now() - t0 < 1800) requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
    return cur.style.translate;
  });
  await mouse.keyboard.press("Escape");
  await mouse.waitForTimeout(1900);
  const seen = await mouse.evaluate(() => window.__shade);
  check("Escape with the mouse leaves no shadow without its glove",
        seen.filter(f => f.shade > 0.05 && f.glove < 0.05).length, 0);
  // the swap to the keyboard's glove happens under the hide. .target fades
  // its opacity, so a hide that wasn't committed first faded the POINTING
  // glove out where the mouse had been, a fifth of a second of it
  check("nor the pointing glove where the mouse was",
        seen.filter(f => f.kind === "target" && f.glove > 0.02 && f.at === mouseAt).length, 0);
  check("and the glove does come back in with its shadow",
        seen.some(f => f.keys && f.glove > 0.9 && f.shade > 0.9), true);

  /* changing hands mid page. From the mouse the glove glided from wherever
     the mouse was over to the row, and back to the mouse the oval chased the
     arrow while it faded, so each is traced a frame at a time */
  const hands = () => mouse.evaluate(() => new Promise(done => {
    const at = sel => { const el = document.querySelector(sel), r = el.getBoundingClientRect();
      return { x: r.left, y: r.top, o: Number(getComputedStyle(el).opacity) }; };
    const out = [], t0 = performance.now();
    (function tick(){
      out.push({ g: at("#cursor"), s: at("#cursorShade") });
      if (performance.now() - t0 < 420) requestAnimationFrame(tick); else done(out);
    })();
  }));
  const off = (a, b) => Math.hypot(a.x - b.x, a.y - b.y) > 80;
  for (const [x, y] of [[1100, 140], [1110, 150]]) { await mouse.mouse.move(x, y); await mouse.waitForTimeout(60); }
  await mouse.waitForTimeout(300);
  let swap = hands();
  await mouse.keyboard.press("ArrowDown");
  swap = await swap;
  const landed = swap[swap.length - 1].g;
  check("from the mouse the glove lands on the row rather than flying there",
        swap.filter(f => f.g.o > 0.05 && f.s.o > 0.05 && off(f.g, landed)).length, 0);
  const ground = swap[swap.length - 1].s;
  swap = hands();
  for (const [x, y] of [[1000, 200], [1010, 210]]) { await mouse.mouse.move(x, y); await mouse.waitForTimeout(30); }
  swap = await swap;
  check("and to the mouse the oval doesn't trail after the arrow",
        swap.filter(f => f.s.o > 0.05 && off(f.s, ground)).length, 0);
  await mouse.close();
}

/* The change between the start page and a game, frame by frame: the start
   page's items slide out before the patch list comes in, the fog fades into
   the clip with the patch queue on the same fade, nothing on the start page
   moves up or down because of the queue, and Q/E between games never fades
   the clip's layer. */
async function choreographyCases(browser) {
  const page = await open(browser, { game: false, fog: true });
  check("the fog's the clip for this", await page.evaluate(() =>
    getComputedStyle(document.querySelector("#fogA")).display !== "none"), true);
  const watch = (ms) => page.evaluate(ms => {
    window.__seen = [];
    const op = el => el ? Number(getComputedStyle(el).opacity) : 0;
    const most = sel => Math.max(0, ...[...document.querySelectorAll(sel)].map(op));
    const shift = el => el ? new DOMMatrixReadOnly(getComputedStyle(el).transform).m41 : 0;
    const $ = sel => document.querySelector(sel);
    const t0 = performance.now();
    const look = () => {
      const row = document.querySelector("#games .row");
      window.__seen.push({
        start: most("#games .row, #m1list > .hdr"),
        patch: most("#patches .row, #sectPatch .hdr") * op(document.querySelector("#sectPatch")),
        clip: op(document.querySelector("#cinema")),
        queue: op(document.querySelector("#statusbar")),
        art: op($("#artStage")), artX: shift($("#artStage")),
        sw: op($("#switcher")), swX: shift($("#switcher")),
        path: op($("#pathStat")), pathX: shift($("#pathStat")),
        // a <video> left at 1 is one Chromium on Windows can make an underlay
        opaque: [...document.querySelectorAll("video")].filter(v => v.isConnected
          && getComputedStyle(v).display !== "none" && op(v) >= 1).length,
        top: row ? row.getBoundingClientRect().top : 0 });
      if (performance.now() - t0 < ms) requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  }, ms);
  const seen = () => page.evaluate(() => window.__seen);
  const listTop = () => page.evaluate(() => document.querySelector("#games .row").getBoundingClientRect().top);

  const before = await listTop();
  await watch(1400);
  await page.evaluate(() => [...document.querySelectorAll("#games .row")]
    .find(li => li.textContent.includes("XIII-2")).click());
  await page.waitForTimeout(1600);
  let frames = await seen();
  check("into a game, the two lists are never both up",
        frames.filter(f => f.start > 0.6 && f.patch > 0.6).length, 0);
  check("the fog fades into the clip", frames.some(f => f.clip > 0.05 && f.clip < 0.95), true);
  check("the patch queue fades with it",
        Math.max(...frames.map(f => Math.abs(f.clip - f.queue))) < 0.06, true);
  check("the art fades out on the clip's fade",
        Math.max(...frames.map(f => Math.abs(f.art - (1 - f.clip)))) < 0.06, true);
  check("in place, it doesn't slide", frames.every(f => Math.abs(f.artX) < 0.5), true);
  check("the Q/E name comes in on the path's slide",
        frames.some(f => f.swX > 1) && frames.every(f => Math.abs(f.sw - f.path) < 0.02
          && Math.abs(f.swX - f.pathX) < 0.5), true);
  check("no video is ever drawn opaque", frames.every(f => f.opaque === 0), true);
  check("the start page's list slides out level, the queue moves nothing",
        Math.max(...frames.map(f => Math.abs(f.top - before))) < 0.5, true);
  // within a device pixel: zoomed, borders snap to whole ones, so the strip
  // comes out a fraction under the --sb-h it's laid out on
  const fits = await page.evaluate(() => {
    const menu = document.querySelector("#menu2").getBoundingClientRect();
    const strip = document.querySelector("#statusbar").getBoundingClientRect();
    return Math.abs(menu.bottom - strip.top) < 1.5 && strip.height > 30;
  });
  check("the patch list ends where the queue starts", fits, true);

  await watch(1400);
  await page.keyboard.press("e");
  await page.waitForTimeout(1600);
  frames = await seen();
  check("Q/E switches the clip under a layer that stays up",
        frames.every(f => f.clip > 0.99), true);
  check("and neither clip is opaque through the cross", frames.every(f => f.opaque === 0), true);

  await watch(1600);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1800);
  frames = await seen();
  check("back out, the two lists are never both up",
        frames.filter(f => f.start > 0.6 && f.patch > 0.6).length, 0);
  check("the clip fades back to the fog", frames.some(f => f.clip > 0.05 && f.clip < 0.95), true);
  check("with the queue on the same fade",
        Math.max(...frames.map(f => Math.abs(f.clip - f.queue))) < 0.06, true);
  check("and the art fades back in on it, in place",
        Math.max(...frames.map(f => Math.abs(f.art - (1 - f.clip)))) < 0.06
          && frames.every(f => Math.abs(f.artX) < 0.5), true);
  check("the Q/E name goes out with the path",
        frames.some(f => f.swX > 1) && frames.every(f => Math.abs(f.sw - f.path) < 0.02
          && Math.abs(f.swX - f.pathX) < 0.5), true);
  check("and the fog's never opaque either", frames.every(f => f.opaque === 0), true);
  check("and the start page lands where it was", Math.abs(await listTop() - before) < 0.5, true);
  check("the glove comes back to the game it left",
        await page.evaluate(() => document.activeElement && document.activeElement.dataset.id), "lr");

  // the second time in has to fade too: something left on the layer by the
  // way back held it down, and every entry after the first was a cut again
  await watch(1400);
  await page.evaluate(() => document.querySelector('#games .row[data-id="ff13"]').click());
  await page.waitForTimeout(1600);
  frames = await seen();
  check("and the next time in fades as well", frames.some(f => f.clip > 0.05 && f.clip < 0.95), true);

  /* Focus goes with the screen the moment it changes, so a key pressed mid
     change acts on the screen that's arriving. Left behind, Enter on the list
     going out unticked an extra for the next Apply, and a second Enter on the
     way in picked the game again */
  await page.keyboard.press("Escape");
  const right = await page.evaluate(() => ({
    on: document.activeElement && document.activeElement.dataset.id,
    bar: getComputedStyle(document.querySelector("#toolBar")).pointerEvents }));
  check("back out, focus is on the game it left straight away", right.on, "ff13");
  check("and the corner bar can't be pressed while it fades", right.bar, "none");
  await page.waitForTimeout(100);
  await page.keyboard.press("Enter");
  check("Enter mid way back goes into that game again",
        await page.evaluate(() => document.body.classList.contains("m2")
          && document.querySelector('#games .row[aria-selected="true"]').dataset.id), "ff13");
  check("and focus left the start page with it",
        await page.evaluate(() => !!document.activeElement.closest("#m2list")), true);
  await page.waitForTimeout(1600);
  await page.close();
}

async function stageCases(browser) {
  /* Scale, then fill. The zoom is whatever fits 1280x720, and the stage
     takes the rest of the window's shape in its own px, so a maximised window
     or a 16:10 or 21:9 screen goes edge to edge with nothing stretched. Past
     3:2 and 12:5 it's held to those and centred, with even bars: a 4:3, 32:9
     or portrait screen would crop the clips into faces. At least 1280x720 of
     its own px whatever the window does */
  const shapes = [
    ["authored", 1280, 720],
    ["16:9 larger", 1920, 1080],
    ["16:9 1440p", 2560, 1440],
    ["a 4K panel in fullscreen", 3840, 2160],
    ["past the 4x clamp", 5400, 3000],
    ["maximised 1080p", 1920, 1009],
    ["maximised 1440p", 2560, 1369],
    ["16:10 fullscreen", 1920, 1200],
    ["3:2 laptop fullscreen", 2256, 1504],
    ["ultrawide fullscreen", 3440, 1440],
    ["32:9 fullscreen", 3840, 1080],
    ["4:3 fullscreen", 1600, 1200],
    ["portrait monitor", 1080, 1920],
    ["tall and narrow", 1000, 940],
    ["under the floor", 960, 560],
  ];
  for (const [name, w, h] of shapes) {
    const page = await open(browser, { applied: [...REQUIRED] }, [w, h]);
    const it = await page.evaluate(() => {
      const el = document.querySelector("#app");
      const box = el.getBoundingClientRect();
      const zoom = Number(getComputedStyle(el).zoom);
      return { w: box.width / zoom, h: box.height / zoom, zoom,
               left: box.left, top: box.top, width: box.width, height: box.height };
    });
    // to whole device px, as fit() holds it
    const sw = Math.floor(Math.min(w, h * 12 / 5) + 1e-6), sh = Math.floor(Math.min(h, w / (3 / 2)) + 1e-6);
    const placed = Math.abs(it.width - sw) < 0.05 && Math.abs(it.height - sh) < 0.05
      && Math.abs(it.left - (w - sw) / 2) <= 0.5 && Math.abs(it.top - (h - sh) / 2) <= 0.5;
    const floor = it.w >= 1280 - 0.01 && it.h >= 720 - 0.01;
    const how = sw === w && sh === h ? "fills the window" : `${Math.round(sw)}x${Math.round(sh)} centred`;
    check(`${name} ${w}x${h}: stage ${it.w.toFixed(1)}x${it.h.toFixed(1)} of its own px @ ${it.zoom.toFixed(3)}, ${how}`,
          placed && floor, true);
    await page.close();
  }

  /* The zoom is the smaller of the two fits, so 1280x720 of stage always
     fits and the extra goes to whichever side the window has it on. */
  for (const [w, h, want] of [[1280, 720, 1], [1920, 1080, 1.5], [2560, 1440, 2],
                              [3840, 2160, 3], [5400, 3000, 4], [1920, 1017, 1.4125]]) {
    const page = await open(browser, { applied: [...REQUIRED] }, [w, h]);
    const zoom = await page.evaluate(() =>
      Number(getComputedStyle(document.querySelector("#app")).zoom));
    check(`${w}x${h} scales ${want}x`, Math.abs(zoom - want) < 0.001, true);
    await page.close();
  }
}

/* F11 and Alt+Enter are the window's, not the menu's: they work with a
   drawer open, a held key toggles once, Alt+Enter never reaches the menu as
   an Enter, and Escape on the start page is the last step out */
async function fullscreenCases(browser) {
  const page = await open(browser, { applied: [...REQUIRED], game: false });
  const toggles = () => page.evaluate(() => window.__toggles || 0);
  const hint = () => page.evaluate(() => document.querySelector("#hintText").textContent);
  const said = await hint();
  await page.keyboard.press("F11");
  await page.waitForTimeout(150);
  check("F11 asks the shell for fullscreen", await toggles(), 1);
  // the window's business, not a hint
  check("and the help bar keeps what it was saying", await hint(), said);
  await page.keyboard.down("F11");
  for (let i = 0; i < 4; i++) await page.keyboard.down("F11");   // auto repeat
  await page.keyboard.up("F11");
  await page.waitForTimeout(150);
  check("a held F11 toggles once, not on every repeat", await toggles(), 2);
  // focus on a game row, where a plain Enter would go into the game
  await page.keyboard.press("s");
  await page.waitForTimeout(200);
  await page.keyboard.press("Alt+Enter");
  await page.waitForTimeout(400);
  check("Alt+Enter toggles too", await toggles(), 3);
  check("without going into the game it's on", await page.evaluate(() => document.body.classList.contains("m1")), true);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(150);
  check("Escape on the start page leaves fullscreen", await toggles(), 4);
  // into a game: Escape there goes back a screen first
  await page.keyboard.press("F11");
  await page.evaluate(() => [...document.querySelectorAll("#games .row")]
    .find(li => li.textContent.includes("XIII-2")).click());
  await page.waitForTimeout(1200);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check("Escape on a game's screen goes back, it stays fullscreen", await toggles(), 5);
  check("and back is where it went", await page.evaluate(() => document.body.classList.contains("m1")), true);
  // with a drawer open, which keeps every other key for itself
  await page.evaluate(() => [...document.querySelectorAll("#games .row")]
    .find(li => li.textContent.includes("XIII-2")).click());
  await page.waitForTimeout(1200);
  await page.evaluate(() => document.querySelector("#nvTab").click());
  await settles(page, onStage("#nv"), true);
  await page.keyboard.press("F11");
  await page.waitForTimeout(150);
  check("F11 works with a drawer open", await toggles(), 6);
  await page.close();
}

/* What a row says when you walk into the menu.

   The chip used to be a fixed word decided at build time: every required row
   opened saying REQUIRED whether or not the thing was already on the disk, and
   the only way to find out was to press Apply and watch it report
   already-applied three times over. */
async function appliedRowCases(browser) {
  const word = sel => `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return "no row";
    const chip = el.querySelector(".chip");
    return chip ? chip.getAttribute("data-word") || chip.textContent.trim() : "no chip";
  })()`;

  // nothing applied: every required row asks for work
  let page = await open(browser, { applied: [] });
  let states = await page.evaluate(() => [...document.querySelectorAll("#m2list .row[data-id]")]
    .map(li => li.dataset.id + "=" + li.dataset.settled));
  check("nothing applied: no row claims to be settled",
        states.every(s => s.endsWith("=0")), true, states.join(" "));
  await page.close();

  // the required ones done, the optional one not
  page = await open(browser, { applied: [...REQUIRED] });
  states = await page.evaluate(() => Object.fromEntries(
    [...document.querySelectorAll("#m2list .row[data-id]")]
      .map(li => [li.dataset.id, li.dataset.settled])));
  for (const id of REQUIRED) check(`${id} opens settled`, states[id], "1");
  check("an unapplied optional stays unsettled", states.audio, "0");
  /* the ticked optional is the only thing left. settled means "on the disk",
     which the audio fix is not here, and it is ticked by default, so Apply
     still has exactly one job. this used to expect zero, from when the audio
     fix defaulted off and the window disagreed with the manifest about it. */
  check("and Apply has only the ticked optional left",
        await page.evaluate(() => pending().map(j => j.id).join(",")), "audio");
  await page.close();

  page = await open(browser, { applied: [...REQUIRED, "audio"] });
  check("and nothing at all once that is applied too",
        await page.evaluate(() => pending().length), 0);
  await page.close();

  // an optional that IS applied says so, and ticking it does not overwrite that
  page = await open(browser, { applied: [...REQUIRED, "audio"] });
  check("an applied optional opens settled",
        await page.evaluate(() => document.querySelector('.row[data-id="audio"]').dataset.settled), "1");
  const before = await page.evaluate(word('.row[data-id="audio"] .chip'));
  await page.evaluate(() => document.querySelector('.row[data-id="audio"]').click());
  await page.waitForTimeout(120);
  const after = await page.evaluate(word('.row[data-id="audio"] .chip'));
  check("and toggling it does not claim it was uninstalled", after, before);
  await page.close();

  // a game we cannot find tells the truth rather than inheriting a stale set
  page = await open(browser, { found: false, applied: [...REQUIRED] });
  check("a game that was not found settles nothing",
        await page.evaluate(() => [...document.querySelectorAll("#m2list .row[data-id]")]
          .every(li => li.dataset.settled !== "1")), true);
  await page.close();
}

/* Where the names end against the highlight, and where the keyboard glove
   lands, measured off the layout at the user's window size and the smallest.
   "Fabula-Lous Shader" behind its checkbox ended in the plate's fade at the
   old indent, which only a measurement says. */
async function layoutCases(browser) {
  const measure = () => {
    const app = document.getElementById("app").getBoundingClientRect();
    const st = getComputedStyle(document.getElementById("stage"));
    const fadeAt = parseFloat(st.getPropertyValue("--lit-w"))
      * parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--lit-fade")) / 100;
    const rows = [...document.querySelectorAll(
      "body.m1 #games .row, body.m1 #btnAbout, body.m1 #btnCredits, "
      + "body.m2 #m2list .row:not(.slot), body.m2 #go, body.m2 #launchWrap.on #launch")]
      .filter(r => r.getBoundingClientRect().width > 4);
    return rows.map(row => {
      const label = row.querySelector(".label, .lbl");
      // the checkbox sits on the left inside the label, so the contents' right
      // edge is still the last letter
      const range = document.createRange();
      range.selectNodeContents(label);
      const ink = range.getBoundingClientRect();
      const walk = document.createTreeWalker(label, NodeFilter.SHOW_TEXT,
        { acceptNode: n => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP });
      const start = document.createRange();
      start.selectNodeContents(walk.nextNode());
      const box = row.querySelector(".box");
      const first = (box || start).getBoundingClientRect();
      return { name: label.textContent.trim(), clear: fadeAt - (ink.right - app.left) / SCALE,
               first: (first.left - app.left) / SCALE };
    });
  };
  const glove = async (page, name) => page.evaluate(async name => {
    const app = document.getElementById("app").getBoundingClientRect();
    const row = [...document.querySelectorAll("#games .row, #btnAbout, #btnCredits, #m2list .row, #go, #launch")]
      .find(r => (r.querySelector(".label, .lbl") || r).textContent.trim() === name);
    Pointer.set("keys");
    Pointer.heldUntil = 0;
    Pointer.snap(row);
    await new Promise(done => setTimeout(done, 420));
    const c = document.getElementById("cursor").getBoundingClientRect();
    return { left: (c.left - app.left) / SCALE, right: (c.right - app.left) / SCALE };
  }, name);

  // the glove and its ground shadow are placed as one, so going into a game
  // and back with the keys can't leave the shadow lying apart from the glove
  const pair = page => page.evaluate(() => {
    const c = document.getElementById("cursor").getBoundingClientRect();
    const s = document.getElementById("cursorShade").getBoundingClientRect();
    return (s.top - c.top) / SCALE;
  });
  for (const size of [[2000, 1150], [1280, 720]]) {
    const page = await open(browser, { applied: [...REQUIRED], game: false }, size);
    await page.keyboard.press("ArrowDown"); await page.waitForTimeout(500);
    const rest = await pair(page);
    await page.keyboard.press("Enter"); await page.waitForTimeout(2600);
    await page.keyboard.press("Escape"); await page.waitForTimeout(2200);
    const after = await pair(page);
    check(`${size[0]}: back on the start page the glove's shadow is still under it`,
          Math.abs(after - rest) < .5, true, `${rest.toFixed(2)} then ${after.toFixed(2)}`);
    await page.close();
  }
  for (const size of [[2000, 1150], [1280, 720]]) {
    const page = await open(browser, { applied: [...REQUIRED], game: false }, size);
    const screens = [["the start page", null]];
    for (const game of ["Final Fantasy XIII", "Final Fantasy XIII-2", "Lightning Returns"]) {
      screens.push([game, game]);
    }
    for (const [where, game] of screens) {
      if (game) {
        await page.evaluate(game => {
          if (!document.body.classList.contains("m1")) back();
          setTimeout(() => [...document.querySelectorAll("#games .row")]
            .find(li => li.textContent.trim().startsWith(game)).click(), 900);
        }, game);
        await page.waitForTimeout(3600);
      }
      const rows = await page.evaluate(measure);
      if (game) {
        const tight = rows.filter(r => r.clear < 8);
        check(`${size[0]}: on ${where} every name ends before the highlight fades`,
              rows.length > 2 && !tight.length, true,
              tight.map(r => `${r.name} ${r.clear.toFixed(1)}`).join(", "));
      } else {
        // the title menu has no plates: each name stands on its own shadow,
        // touching it and not through it, and no shadow reaches the art
        const lines = await page.evaluate(() => {
          const list = document.getElementById("games").getBoundingClientRect();
          return [...document.querySelectorAll("#games .ink, #btnAbout .ink, #btnCredits .ink")].map(ink => {
            const r = document.createRange(); r.selectNodeContents(ink.firstChild);
            const text = r.getBoundingClientRect(), box = ink.getBoundingClientRect();
            const z = box.width / ink.offsetWidth, after = getComputedStyle(ink, "::after");
            const top = box.top + parseFloat(after.top) * z;
            const right = box.left + (parseFloat(after.left) + parseFloat(after.width)) * z;
            // the capitals' foot, off the font's own metrics: the text box's
            // top plus its ascent, which the canvas gives for this font
            const c = document.createElement("canvas").getContext("2d");
            c.font = getComputedStyle(ink).font;
            const m = c.measureText(ink.textContent.toUpperCase());
            const foot = text.top + (m.fontBoundingBoxAscent) * z;
            // the shadow's middle on the capitals' foot: as seen,
            // the letters touching it rather than sunk into it or floating
            return { name: ink.textContent, gap: (top + parseFloat(after.height) / 2 * z - foot) / z,
                     past: (right - list.right) / z };
          });
        });
        const off = lines.filter(l => l.gap < -1.25 || l.gap > 1.25 || l.past > -20);
        check(`${size[0]}: on the start page each name stands on its own shadow, clear of the art`,
              lines.length === 5 && !off.length, true,
              off.map(l => `${l.name} gap ${l.gap.toFixed(1)} past ${l.past.toFixed(1)}`).join(", "));
      }
      const off = [];
      for (const row of rows) {
        const g = await glove(page, row.name);
        const gap = row.first - g.right;
        if (gap < 2 || gap > 12 || g.left < 15) off.push(`${row.name} gap ${gap.toFixed(1)} left ${g.left.toFixed(1)}`);
      }
      check(`${size[0]}: on ${where} the glove sits just left of each row, clear of the edge`,
            off.length, 0, off.join(", "));
    }
    await page.close();
  }
}

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME });
  console.log("names, the highlight and the glove");
  await layoutCases(browser);
  console.log("launch row");
  await launchCases(browser);
  console.log("corner bar");
  await cornerBarCases(browser);
  console.log("restart loop");
  await restartLoopCases(browser);
  console.log("leaving a game");
  await leavingCases(browser);
  console.log("start page to a game and back");
  await choreographyCases(browser);
  console.log("stage scaling");
  await stageCases(browser);
  console.log("fullscreen keys");
  await fullscreenCases(browser);
  console.log("rows that open already applied");
  await appliedRowCases(browser);
  await browser.close();
  process.exit(bad ? 1 : 0);
})();
