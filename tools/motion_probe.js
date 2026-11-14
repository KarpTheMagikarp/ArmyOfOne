/* What moves on screen, and what is left still on purpose, in jsdom.

   The fog is the biggest standing cost the page has. Under software rendering
   it is a 60fps decode plus a full-window composite every frame, so the page
   asks WebGL what it is running on and sits on the still when the answer is
   a software rasteriser. Under an HD install the cutscene layer covers it for
   up to forty minutes, so the decoder rests for the length of the run. And
   the glove follows the mouse by translate, so a moving mouse never lays the
   page out.

     npm install jsdom && node tools/motion_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync(__dirname + "/../ui/index.html", "utf8");

const results = [];
const check = (name, pass, seen) => results.push([name, !!pass, seen]);
const wait = ms => new Promise(r => setTimeout(r, ms));

const api = new Proxy({}, {
  get: (target, key) => key === "then" || typeof key !== "string" ? undefined
    : () => Promise.resolve({ ok: true }),
});

/* `gpu` is the renderer WebGL reports, or null for a page with no WebGL at all */
function open(gpu){
  const dom = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/ui/index.html",
    beforeParse(window){
      window.pywebview = { api };
      window.fetch = () => Promise.resolve({ ok: false });
      // eight cores and no reduced motion, so the clip tier is the one in question
      Object.defineProperty(window.navigator, "hardwareConcurrency", { get: () => 8 });
      window.matchMedia = () => ({ matches: false, addEventListener(){}, addListener(){} });
      window.__media = { play: 0, pause: 0 };
      window.HTMLMediaElement.prototype.play = function(){
        if (this.id === "fogA") window.__media.play++;
        return Promise.resolve();
      };
      window.HTMLMediaElement.prototype.pause = function(){
        if (this.id === "fogA") window.__media.pause++;
      };
      window.HTMLMediaElement.prototype.load = () => {};
      // a clip with a length, so the fog's "metadata never arrived" fallback
      // can't fire part way through and take the clip away from the checks
      // below on a slow run
      Object.defineProperty(window.HTMLMediaElement.prototype, "duration", { get: () => 12 });
      window.HTMLCanvasElement.prototype.getContext = function(kind){
        if (kind !== "webgl" || gpu === null) return null;
        return { getExtension: n => n === "WEBGL_debug_renderer_info" ? { UNMASKED_RENDERER_WEBGL: 1 } : null,
                 getParameter: () => gpu, RENDERER: 2 };
      };
      window.console.warn = () => {};
      window.console.log = () => {};
    },
  });
  return new Promise(ok => dom.window.addEventListener("load", () => {
    const fog = dom.window.document.getElementById("fogA");
    if (fog) fog.dispatchEvent(new dom.window.Event("loadedmetadata"));
    ok(dom.window);
  }));
}

(async () => {
  try {
    // no WebGL: a software rasteriser as far as anyone can tell
    let w = await open(null);
    await wait(2200);
    check("ready with no WebGL", w.__uiReady === true);
    check("no WebGL is read as software, so the fog is the still",
          w.eval("Fog.tier") === "still" && w.eval("weakMachine") === true, w.eval("Fog.tier"));
    check("and says so in the diagnostics",
          w.__diag.some(l => l.startsWith("rendering: software (no WebGL)")), w.__diag.join(" | "));
    w.close();

    w = await open("ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)");
    await wait(2200);
    check("SwiftShader is software too", w.eval("Fog.tier") === "still", w.eval("Fog.tier"));
    w.close();

    w = await open("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)");
    await wait(2200);
    check("a real GPU keeps the clip", w.eval("Fog.tier") === "video" && !w.eval("weakMachine"),
          w.eval("Fog.tier"));
    check("and says nothing about it", !w.__diag.some(l => l.startsWith("rendering:")));

    // an HD install under menu 2 rests the fog for the length of the run
    w.eval("pick('ff13-2')");
    await wait(200);
    const before = { ...w.__media };
    w.eval(`Hd.arm({ jobId: "hdproject2", detail: { name: "FFXIII-2 HD", root: "D:\\\\HD" } });
            Hd.start("ff13-2", ["weapons"], null, "add")`);
    check("an install holds the fog", w.eval("Fog.held") === true);
    check("by pausing its clip", w.__media.pause > before.pause, JSON.stringify(w.__media));
    w.eval("unlockAll()");
    check("and a click doesn't wake it", w.__media.play === before.play, JSON.stringify(w.__media));

    // menu 1 is the fog on screen, install or not
    w.eval("back()");
    check("going back to menu 1 lets it play", w.eval("Fog.held") === false
          && w.__media.play > before.play, JSON.stringify(w.__media));
    w.eval("pick('ff13-2')");
    check("menu 2 lets the fog show through its fade in, install or not",
          w.eval("Fog.held") === false);
    await wait(900);
    check("and holds it again once it's covered", w.eval("Fog.held") === true);

    const played = w.__media.play;
    w.__patch.receive({ type: "hd", stage: "done", steps: 1, applied: 1, skipped: 0, nothing: 0,
                        removed: 0 });
    check("the run ending leaves it resting under the cutscene, where it can't be seen",
          w.eval("Fog.held") === true && w.__media.play === played
            && w.document.body.classList.contains("m2"), JSON.stringify(w.__media));

    // a window that was hidden mid install doesn't come back playing
    w.eval(`Hd.start("ff13-2", [], null, "add", "Japanese")`);
    const quiet = w.__media.play;
    w.eval("Fog.set('still'); Fog.set('video')");
    check("coming back from hidden respects the hold", w.__media.play === quiet,
          JSON.stringify(w.__media));
    w.__patch.receive({ type: "hd", stage: "cancelled", note: "Stopped." });
    check("and so does a stopped one", w.eval("Fog.held") === true);
    w.eval("back()");
    check("the start page lets it go", w.eval("Fog.held") === false);

    // without an install too: once the clip's layer is all the way up, the
    // fog under it rests, and the start page wakes it as the layer goes
    await wait(900);
    const resting = { ...w.__media };
    w.eval("pick('ff13')");
    check("not while the layer's still fading in", w.eval("Fog.held") === false);
    await wait(900);
    check("a game's screen rests the fog once it's covered",
          w.eval("Fog.held") === true && w.__media.pause > resting.pause, JSON.stringify(w.__media));
    w.eval("back()");
    check("and going back wakes it at once",
          w.eval("Fog.held") === false && w.__media.play > resting.play, JSON.stringify(w.__media));

    // the glove follows the mouse by translate, never by left and top
    const cur = w.document.getElementById("cursor");
    w.document.dispatchEvent(new w.MouseEvent("mousemove", { clientX: 300, clientY: 200,
                                                             bubbles: true }));
    await wait(60);
    check("the glove is placed by translate", /px .*px/.test(cur.style.translate),
          cur.style.translate);
    check("and nothing writes left or top", cur.style.left === "" && cur.style.top === "",
          `${cur.style.left} ${cur.style.top}`);
    check("its shadow goes with it",
          w.document.getElementById("cursorShade").style.translate === cur.style.translate);

    /* The terminal's diagnostics: nothing goes until the shell asks, then each
       burst goes as one call, every burst after it too, and a bridge that
       isn't up yet gets them once pywebview says it is */
    const sent = [];
    w.pywebview = { api: { diag: lines => { sent.push(lines); return Promise.resolve(); } } };
    w.eval("diag('before it asks')");
    await wait(20);
    check("diagnostics wait to be asked for", sent.length === 0, JSON.stringify(sent));
    w.__diagPush = true;
    w.eval("window.__diag.splice(0); diag('one'); diag('two')");
    await wait(20);
    w.eval("diag('three')");
    await wait(20);
    check("then go a burst a call, every burst", JSON.stringify(sent) === '[["one","two"],["three"]]',
          JSON.stringify(sent));
    check("and leave nothing behind to send twice", w.__diag.length === 0, w.__diag.length);
    const later = w.pywebview;
    w.pywebview = undefined;
    w.eval("diag('early')");
    await wait(20);
    w.pywebview = later;
    w.dispatchEvent(new w.Event("pywebviewready"));
    await wait(20);
    check("a bridge that's late gets them when it's ready",
          JSON.stringify(sent.slice(2)) === '[["early"]]', JSON.stringify(sent));
    w.close();

    /* each game's screen is its own menu: XIII's light bars and HELP mark,
       XIII-2's and LR's dark ones, which they share. The start page's is the
       game its art is of */
    w = await open("ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)");
    await wait(2200);
    const look = () => w.document.body.dataset.look;
    check("the start page opens in XIII's look", look() === "xiii", look());
    w.eval("preview('lr')");
    check("with LR's art up it's in LR's", look() === "xiii2", look());
    w.eval("preview('ff13')");
    check("and back in XIII's with XIII's", look() === "xiii", look());
    w.eval("pick('ff13-2')");
    check("XIII-2's screen is in its own", look() === "xiii2", look());
    await wait(900);
    w.eval("cycle(1)");
    check("E to LR keeps it, the look the two share", look() === "xiii2", look());
    await wait(700);
    w.eval("cycle(1)");
    check("E on to XIII turns with the name, before the list is rebuilt",
          look() === "xiii" && w.eval("target") === "lr", `${look()} ${w.eval("target")}`);
    await wait(700);
    w.eval("cycle(-1); back()");
    check("out before a Q lands, the start page is the game it came from, in its look",
          look() === "xiii" && w.eval("shown") === "ff13", `${look()} ${w.eval("shown")}`);
    w.close();
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }

  let bad = 0;
  for (const [name, pass, seen] of results){
    console.log(`  ${pass ? "ok  " : "FAIL"}  ${name}${pass || seen === undefined ? "" : `  (${seen})`}`);
    if (!pass) bad++;
  }
  process.exit(bad ? 1 : 0);
})();
