/*
The cutscene and fog layers, driven hard in a real browser.

None of this is reachable from jsdom: it needs a decoder, a real media pool and
real timing. The clips are generated next to the probe rather than committed,
because the shipped ones are large and this only needs something that decodes.

What it is guarding, all of which shipped broken:
  - one clip failing must not disable video for the whole session
  - a failure arriving from a game you already left must not touch the current
    one, and must not latch anything
  - a retired layer must release its decoder, or the media pool starves and
    everything after it is slow
  - a missing file must not be diagnosed as a blocked environment
  - a stalled fog clip must be noticed and restarted
  - a window at 1x gets the 720p cut, a bigger one the 1440p, and a missing
    720p file falls through to the 1440p rather than failing the game
  - a cut that's been down once plays out of the page's memory, so a server
    that's stopped answering doesn't stop it
  - one choppy cut isn't the machine's verdict, and stills don't last the
    session: two in a row demote, and video gets another go
  - a fog that stalls twice rests on the still and comes back, never the
    gradient for good
  - an assets folder without the 720p cuts asks for each once, and the clip
    that plays instead is held before its first play
  - a GPU that decodes H.264 and leaves VP9 to the CPU leads with the mp4,
    and an mp4 that can't keep up, or won't decode at all, steps off to the
    webm before any still
  - a game whose every clip 404s gets the same layer every visit

  NODE_PATH=<wherever playwright is> node tools/video_probe.js
*/

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
// CHROME=<path> runs another build: WebView2 is evergreen, so the one that
// matters is the newest
const CHROME = process.env.CHROME || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const FFMPEG = process.env.FFMPEG || "ffmpeg";

let bad = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) bad++;
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}` + (ok ? "" : `: got ${got}, wanted ${want}`));
}

function makeClips(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const clip = path.join(dir, "clip.webm");
  if (!fs.existsSync(clip)) {
    execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "lavfi",
      "-i", "testsrc=size=320x180:rate=24:duration=3",
      "-c:v", "libvpx-vp9", "-b:v", "200k", "-an", clip]);
  }
  // and a frame for any still the assets folder doesn't have
  const still = path.join(dir, "still.jpg");
  if (!fs.existsSync(still)) {
    execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "lavfi",
      "-i", "testsrc=size=320x180", "-frames:v", "1", still]);
  }
  return clip;
}

/* the same clip with its back half scrambled: metadata and a few frames, then
   a decode error, which a browser takes as the element's end rather than a
   reason to try the next source */
function breakClip(clip) {
  const broken = path.join(path.dirname(clip), "broken.webm");
  const buf = Buffer.from(fs.readFileSync(clip));
  let seed = 7;
  for (let i = Math.floor(buf.length * 0.45); i < buf.length; i++) {
    seed = (seed * 1103515245 + 12345) >>> 0;
    buf[i] = seed >>> 24;
  }
  fs.writeFileSync(broken, buf);
  return broken;
}

/* The page is served over http rather than file://, because file:// hands the
   media stack direct random access and hides every buffering problem the shell
   would actually hit. A tiny handler is enough, and it lets a route 404 on
   demand, which is the case that used to take the whole session down. */
function serve(clip, missing, delayMs, hold, special) {
  const http = require("http");
  const types = { ".html": "text/html", ".json": "application/json", ".png": "image/png",
                  ".jpg": "image/jpeg", ".webm": "video/webm", ".mp4": "video/mp4" };
  const asked = [];
  const server = http.createServer((req, res) => {
    const name = decodeURIComponent(req.url.split("?")[0]);
    asked.push(name);
    if (missing.some(m => name.endsWith(m))) { res.writeHead(404); return res.end(); }
    if (name.endsWith(".webm") || name.endsWith(".mp4")) {
      // a server that has stopped answering: the request just hangs
      if (hold && hold.stalled) return;
      const odd = Object.entries(special || {}).find(([end]) => name.endsWith(end));
      const buf = fs.readFileSync(odd ? odd[1] : clip);
      const send = () => {
        res.writeHead(200, { "Content-Type": "video/webm", "Content-Length": buf.length,
                             "Accept-Ranges": "bytes" });
        res.end(buf);
      };
      return delayMs ? setTimeout(send, delayMs) : send();
    }
    const file = path.join(ROOT, name.replace(/^\/+/, ""));
    if (name.endsWith(".jpg") && !fs.existsSync(file)) {
      res.writeHead(200, { "Content-Type": "image/jpeg" });
      return res.end(fs.readFileSync(path.join(path.dirname(clip), "still.jpg")));
    }
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); return res.end();
    }
    res.writeHead(200, { "Content-Type": types[path.extname(file)] || "application/octet-stream" });
    res.end(fs.readFileSync(file));
  });
  server.asked = asked;
  return new Promise(ok => server.listen(0, "127.0.0.1", () => ok(server)));
}

const BRIDGE = `window.pywebview = { api: {
  scan: () => Promise.resolve({ ok:true, found:true, path:"E:\\\\g", source:"steam",
    supported:["ff132fix","laa","audio"], applied:[], error:null }),
  resolve_addons_dir: () => Promise.resolve({ ok:false }),
  list_addons: () => Promise.resolve({ ok:false })
} };`;

async function openUi(browser, port, size, gpu) {
  const page = await browser.newPage({ viewport: size ? { width: size.width, height: size.height }
                                                    : { width: 1280, height: 720 } });
  const warnings = [];
  page.on("console", m => warnings.push(m.text()));
  await page.addInitScript(BRIDGE);
  // headless draws with SwiftShader, which the page reads as software
  // rendering and answers with the fog's still. A run about the fog says GPU
  if (gpu) await page.addInitScript(() => {
    // the page asks from a worker where it can, and the worker would get the
    // real renderer. Without OffscreenCanvas it asks the canvas below
    Object.defineProperty(window, "OffscreenCanvas", { value: undefined });
    const real = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
      if (kind === "webgl") return { getExtension: () => null, getParameter: () => "ANGLE (probe)" };
      return real.call(this, kind, ...rest);
    };
  });
  // the container reports 2 cores, which trips weakMachine and turns the fog
  // off before any of this runs. the probe is here to exercise the video paths.
  await page.addInitScript(() => Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 8 }));
  // and a GPU that decodes VP9, unless a run says otherwise: SwiftShader says
  // nothing is power efficient, which starts every window on the 720p cuts
  // or one answer per codec, { vp9, h264 }
  await page.addInitScript(decoder => {
    const base = { supported: true, powerEfficient: true, smooth: true };
    const said = type => Object.assign({}, base, !decoder ? {}
      : decoder.vp9 || decoder.h264 ? (/avc1/.test(type) ? decoder.h264 : decoder.vp9) || {} : decoder);
    Object.defineProperty(navigator, "mediaCapabilities", { get: () => ({
      decodingInfo: c => Promise.resolve(said(c.video.contentType)) }) });
  }, (size && size.decoder) || null);
  /* Counts videos that leave the document without having been retired first.

     Element counts cannot see this: harvestCuts only queries #cinema, so a node
     that was removed but never drop()ed is invisible to it while its decoder,
     its sources and its timers all stay alive. Hooking remove() is the only
     place the two paths are distinguishable. */
  await page.addInitScript(() => {
    window.__leaked = 0;
    const real = Element.prototype.remove;
    Element.prototype.remove = function () {
      if (this.tagName === "VIDEO" && this.dataset.dead !== "1") window.__leaked++;
      return real.call(this);
    };
  });
  await page.goto(`http://127.0.0.1:${port}/ui/index.html`);
  await page.waitForTimeout(1200);
  page._warnings = warnings;
  return page;
}

/* evaluate with an argument, not a stringified template: the first version of
   this built the click expression as a string and then evaluated the builder,
   so every "click" returned source code and nothing was ever pressed. */
/* which file a cut is playing, since one that's held plays as a blob: URL */
function chosen(page) {
  return page.evaluate(() => {
    const v = document.querySelector("#cinema video");
    if (!v) return "";
    const s = [...v.querySelectorAll("source")].find(s => s.src === v.currentSrc);
    return s ? s.dataset.file : v.currentSrc;
  });
}

function pick(page, i) {
  return page.evaluate(n => {
    const rows = [...document.querySelectorAll("#games .row")];
    if (rows[n]) rows[n].click();
  }, i);
}

async function spin(page, times) {
  for (let i = 0; i < times; i++) {
    await pick(page, i % 3).catch(() => {});
    await page.waitForTimeout(160);
  }
}

async function main() {
  const clip = makeClips(path.join(os.tmpdir(), "ffxiii-probe-media"));
  const browser = await chromium.launch({ executablePath: CHROME, args: ["--autoplay-policy=no-user-gesture-required"] });

  // every clip present: the healthy path, and the baseline for the rest
  let server = await serve(clip, []);
  let port = server.address().port;
  let page = await openUi(browser, port);
  await pick(page, 0);
  await page.waitForTimeout(1800);
  let state = await page.evaluate(() => ({
    playing: [...document.querySelectorAll("#cinema video")].some(v => !v.paused && v.currentTime > 0),
    dead: cutDead.size, blocked: mediaBlocked,
  }));
  check("a present clip actually plays", state.playing, true);
  check("nothing latched on the happy path", !!(state.dead || state.blocked), false);
  // an opaque one is the underlay Windows drew at the wrong size
  check("a cut that's up is never opaque", await page.evaluate(() => {
    const v = document.querySelector("#cinema video.up");
    return !!v && Number(getComputedStyle(v).opacity) < 1;
  }), true);
  check("a 1x window plays the 720p cut", /cut_xiii_720\.webm$/.test(await chosen(page)), true);

  // and the decoders do not pile up while you spin through the list
  await spin(page, 9);
  await page.waitForTimeout(900);
  const live = await page.evaluate(() => ({
    videos: document.querySelectorAll("#cinema video").length,
    canvases: document.querySelectorAll("#cinema canvas.seq, #cinema img.seq").length,
    pending: typeof pendingPlay !== "undefined" ? pendingPlay.size : -1,
    leaked: window.__leaked,
  }));
  check("layers do not accumulate", live.videos <= 2, true);
  check("retired layers leave pendingPlay", live.pending <= 2, true);
  check("stills do not accumulate either", live.canvases <= 2, true);
  check("every retired clip released its decoder", live.leaked, 0);

  /* a Q/E spin inside a game: every switch crosses straight into its clip,
     nothing stands in as a still, and the clip on screen is the only one a
     new one has to fade over, so there are never more than two decoding */
  await pick(page, 0);
  await page.waitForTimeout(1500);
  await page.evaluate(() => {
    window.__made = 0;
    const make = document.createElement.bind(document);
    document.createElement = (t, o) => { if (/^video$/i.test(t)) window.__made++; return make(t, o); };
  });
  await page.evaluate(() => {
    window.__most = 0; window.__stills = 0;
    new MutationObserver(() => {
      const c = document.querySelector("#cinema");
      window.__most = Math.max(window.__most, c.querySelectorAll("video").length);
      if (c.querySelector("img.seq")) window.__stills++;
    }).observe(document.querySelector("#cinema"), { childList: true, subtree: true });
  });
  for (let i = 0; i < 10; i++) { await page.keyboard.press("e"); await page.waitForTimeout(250); }
  await page.waitForTimeout(1200);
  const landed = await page.evaluate(() => ({ most: window.__most, stills: window.__stills, dead: cutDead.size,
    playing: [...document.querySelectorAll("#cinema video.up")].some(v => v.currentTime > 0) }));
  check("a spin never puts a still up", landed.stills, 0);
  check("and never has more than two clips decoding", landed.most <= 2, true);
  check("and the game it stops on plays", landed.playing, true);
  check("without the spin counting against the machine", landed.dead, 0);
  await page.close();
  server.close();

  // a 2x window wants every pixel of the 1440p one
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440 });
  await pick(page, 0);
  await page.waitForTimeout(1800);
  check("a 2x window plays the 1440p cut", /cut_xiii\.webm$/.test(await chosen(page)), true);
  await page.close();
  server.close();

  // no 720p file is a bigger decode, not a broken game
  server = await serve(clip, ["cut_xiii_720.webm"]);
  port = server.address().port;
  page = await openUi(browser, port);
  await pick(page, 0);
  await page.waitForTimeout(1800);
  const fell = await page.evaluate(() => ({
    playing: [...document.querySelectorAll("#cinema video")].some(v => !v.paused && v.currentTime > 0),
    dead: cutDead.size,
  }));
  check("a missing 720p cut falls through to the 1440p", /cut_xiii\.webm$/.test(await chosen(page)), true);
  check("and plays", fell.playing && fell.dead === 0, true);
  await page.close();
  server.close();

  // XIII's clip is missing, the other two are fine. the miss must stay local.
  server = await serve(clip, ["cut_xiii_720.webm", "cut_xiii.webm", "cut_xiii.mp4"]);
  port = server.address().port;
  page = await openUi(browser, port);
  await pick(page, 0);          // the broken one first
  await page.waitForTimeout(1600);
  // marked dead, or with every file of it known missing if the warm-up's
  // 404s landed first, which is the same thing said sooner
  const afterMiss = await page.evaluate(() => ({ blocked: mediaBlocked,
    marked: [...new Set([...cutDead.keys(), ...GAMES().filter(g => !cutSources(g).length).map(g => g.id)])] }));
  check("a missing clip is not read as a blocked page", afterMiss.blocked, false);
  check("only the broken game is marked", afterMiss.marked.join(","), "ff13");

  await pick(page, 1);          // a good one after it
  await page.waitForTimeout(2000);
  const recovered = await page.evaluate(() =>
    [...document.querySelectorAll("#cinema video")].some(v => !v.paused && v.currentTime > 0));
  check("the next game still plays", recovered, true);
  await page.close();
  server.close();

  /* A clip that is merely slow is not a clip that failed.

     playCut arms a 420ms net, which used to call fail() when readyState was
     still 0. 420ms is nothing for a real 1440p clip off a cold disk, so a slow
     load dropped the element, marked the game unplayable and left the poster
     on screen: a video frozen on its first frame, which is exactly what it
     looked like. 1500ms here is comfortably past that net and well inside what
     a real clip takes. */
  server = await serve(clip, [], 1500);
  port = server.address().port;
  page = await openUi(browser, port);
  await pick(page, 0);
  await page.waitForTimeout(4500);
  const slow = await page.evaluate(() => {
    const v = document.querySelector("#cinema video");
    return {
      survived: !!v && v.dataset.dead !== "1",
      marked: cutDead.size,
      ready: v ? v.readyState : -1,
      up: !!v && v.classList.contains("up"),
      started: !!v && v.currentTime > 0,
      // the poster is the clip's own first frame, so a separate still layer is
      // never needed and would only be there because something gave up
      stills: document.querySelectorAll("#cinema img.seq").length,
    };
  });
  check("a slow clip is not dropped", slow.survived, true);
  check("and its game is not marked unplayable", slow.marked, 0);
  check("it loads and plays", slow.ready >= 3 && slow.started, true);
  check("the layer came up while it loaded", slow.up, true);
  check("no still stood in for it", slow.stills, 0);
  await page.close();
  server.close();

  // a slow miss landing after you already moved on must not touch the new game
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port);
  await pick(page, 0);
  await page.waitForTimeout(120);
  await pick(page, 1);          // switch mid load
  await page.evaluate(() => {
    // the stale generation's element fails now, long after we left it
    const v = document.querySelector("#cinema video");
    if (v) v.dispatchEvent(new Event("error"));
  });
  await page.waitForTimeout(2000);
  const late = await page.evaluate(() => ({
    dead: cutDead.size,
    playing: [...document.querySelectorAll("#cinema video")].some(v => !v.paused && v.currentTime > 0),
  }));
  check("a stale failure marks nothing", late.dead, 0);
  check("and does not stop the current game", late.playing, true);
  await page.close();
  server.close();

  // down once, it's in the page: the server can stop answering and it plays
  const hold = { stalled: false };
  server = await serve(clip, [], 0, hold);
  port = server.address().port;
  page = await openUi(browser, port);
  await page.waitForTimeout(2500);         // the clips come down behind the first paint
  hold.stalled = true;
  await pick(page, 1);
  await page.waitForTimeout(1500);
  const held = await page.evaluate(() => {
    const v = document.querySelector("#cinema video");
    return { blob: !!v && v.currentSrc.startsWith("blob:"), moving: !!v && v.currentTime > 0.3 };
  });
  check("a cut that's down plays out of memory", held.blob, true);
  check("with the server not answering", held.moving, true);
  await page.close();
  server.close();

  /* choppy cuts on the lightest tier there is play on rather than turning
     into stills. The decoder's numbers are faked, since nothing here drops
     frames on cue */
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port);
  await page.evaluate(() => {
    window.__drop = 0.6;
    HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
      const total = Math.round(this.currentTime * 60);
      return { totalVideoFrames: total, droppedVideoFrames: Math.round(total * window.__drop) };
    };
  });
  await pick(page, 0); await page.waitForTimeout(1700);
  await pick(page, 1); await page.waitForTimeout(1700);
  await pick(page, 2); await page.waitForTimeout(900);
  const rough = await page.evaluate(() => ({
    still: !!document.querySelector("#cinema img.seq"),
    video: [...document.querySelectorAll("#cinema video.up")].some(v => !v.paused && v.currentTime > 0) }));
  check("two choppy cuts in a row still leave the next one playing", rough.video, true);
  check("with no still standing in for it", rough.still, false);
  await page.close();
  server.close();

  // a 2x window on a decoder that says the 1440p ones won't keep up starts on
  // the 720p ones, rather than learning it off two choppy cuts
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440, decoder: { powerEfficient: false } });
  await page.waitForTimeout(400);
  await pick(page, 0);
  await page.waitForTimeout(1200);
  check("no hardware decode starts a 2x window on the 720p cut",
        /cut_xiii_720\.webm$/.test(await chosen(page)), true);
  check("and every cut after it", await page.evaluate(() => cutSmall), true);
  await page.close();
  server.close();

  // a 1440p cut that can't keep up tries the 720p one before any still
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440 });
  await page.evaluate(() => {
    HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
      const total = Math.round(this.currentTime * 60);
      return { totalVideoFrames: total, droppedVideoFrames: Math.round(total * 0.6) };
    };
  });
  await pick(page, 0); await page.waitForTimeout(1700);
  await pick(page, 1); await page.waitForTimeout(1700);
  check("a choppy 1440p cut steps down to 720p", await page.evaluate(() => cutSmall), true);
  await pick(page, 2); await page.waitForTimeout(800);
  check("and the next cut is the 720p one", /cut_lr_720\.webm$/.test(await chosen(page)), true);
  await page.close();
  server.close();

  // an assets folder without the 720p cuts, which the user's was: each is
  // asked for once, and the 1440p one that plays instead is held from the start
  const no720 = ["cut_xiii_720.webm", "cut_xiii2_720.webm", "cut_lr_720.webm"];
  server = await serve(clip, no720);
  port = server.address().port;
  page = await openUi(browser, port);
  await page.waitForTimeout(1500);
  await pick(page, 0); await page.waitForTimeout(1200);
  await pick(page, 1); await page.waitForTimeout(1200);
  await pick(page, 0); await page.waitForTimeout(1200);
  check("a missing 720p cut is asked for once",
        server.asked.filter(n => n.endsWith("cut_xiii_720.webm")).length, 1);
  check("and the clip that plays instead is held before it plays",
        await page.evaluate(() => /^blob:/.test(Clips.get("../assets/cut_xiii.webm"))), true);
  check("and plays", /cut_xiii\.webm$/.test(await chosen(page)), true);
  await page.close();
  server.close();

  // a GPU that decodes H.264 and leaves VP9 to the CPU, AMD's RX 400s and 500s
  // among them: the mp4 leads at full size, and in a small window that has no
  // 720p cut. The 720p one still leads a small window that has it
  const lead = page => page.evaluate(() => {
    const s = document.querySelector("#cinema video source");
    return s ? s.dataset.file : "";
  });
  const h264 = { vp9: { powerEfficient: false }, h264: { powerEfficient: true } };
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440, decoder: h264 });
  await page.waitForTimeout(400);
  await pick(page, 0); await page.waitForTimeout(1200);
  check("H.264 on the GPU and VP9 on the CPU leads a 2x window with the mp4",
        /cut_xiii\.mp4$/.test(await lead(page)), true);
  await page.close();
  page = await openUi(browser, port, { width: 1280, height: 720, decoder: h264 });
  await page.waitForTimeout(400);
  await pick(page, 0); await page.waitForTimeout(1200);
  check("and a 1x window that has its 720p cut with that", /cut_xiii_720\.webm$/.test(await lead(page)), true);
  await page.close();
  server.close();
  server = await serve(clip, no720);
  port = server.address().port;
  page = await openUi(browser, port, { width: 1280, height: 720, decoder: h264 });
  await page.waitForTimeout(1500);
  await pick(page, 0); await page.waitForTimeout(1200);
  check("and a 1x window without one with the mp4", /cut_xiii\.mp4$/.test(await lead(page)), true);
  await page.close();
  server.close();

  // an mp4 that drops frames steps off to the webm before anything's a still.
  // The mp4 here is the same VP9 clip, which Chromium plays out of an .mp4
  // with or without H.264, so this is the page's call and not the codec's
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440, decoder: h264 });
  await page.evaluate(() => {
    HTMLVideoElement.prototype.getVideoPlaybackQuality = function () {
      const total = Math.round(this.currentTime * 60);
      return { totalVideoFrames: total, droppedVideoFrames: Math.round(total * 0.6) };
    };
  });
  await pick(page, 0); await page.waitForTimeout(1700);
  check("the mp4 is what's playing", /cut_xiii\.mp4$/.test(await chosen(page)), true);
  await pick(page, 1); await page.waitForTimeout(1700);
  check("an mp4 that can't keep up steps off it", await page.evaluate(() => cutMp4), false);
  await pick(page, 2); await page.waitForTimeout(800);
  check("and the next cut is a webm", /cut_lr_720\.webm$/.test(await lead(page)), true);
  await page.close();
  server.close();

  // a game whose every file 404s is the same thing on every visit, its
  // poster where it has one. Marked dead, the second visit drew the gradient
  const layer = page => page.evaluate(() => {
    const c = document.querySelector("#cinema");
    return c.querySelector("video.up") ? "video" : c.querySelector("img.seq.up") ? "poster"
      : c.querySelector(".cinefall.up") ? "gradient" : "nothing";
  });
  server = await serve(clip, ["cut_lr_720.webm", "cut_lr.webm", "cut_lr.mp4"]);
  port = server.address().port;
  page = await openUi(browser, port);
  await page.waitForTimeout(1500);
  await pick(page, 2); await page.waitForTimeout(900);
  const first = await layer(page);
  await pick(page, 0); await page.waitForTimeout(900);
  await pick(page, 2); await page.waitForTimeout(900);
  check("a game with no clip at all looks the same the second time", await layer(page), first);
  check("and isn't a video", first !== "video", true);
  // the pose the clip would have ended on, not the one it starts from
  check("its still is the clip's last frame", await page.evaluate(() =>
    /_last\.jpg$/.test((document.querySelector("#cinema img.seq") || {}).src || "")), true);
  await page.close();
  server.close();

  // an mp4 that loads and then won't decode steps off to the webm, and the
  // game isn't marked dead for it
  server = await serve(clip, [], 0, null, { ".mp4": breakClip(clip) });
  port = server.address().port;
  page = await openUi(browser, port, { width: 2560, height: 1440, decoder: h264 });
  await page.waitForTimeout(400);
  await pick(page, 0); await page.waitForTimeout(4000);
  const broke = await page.evaluate(() => ({ mp4: cutMp4, dead: [...cutDead.keys()] }));
  check("an mp4 that won't decode steps off it", broke.mp4, false);
  check("onto a webm", /\.webm$/.test(await chosen(page)), true);
  check("without the game marked dead", broke.dead.join(","), "");
  await page.close();
  server.close();

  // a fog that stalls twice rests on the still, and comes back
  server = await serve(clip, []);
  port = server.address().port;
  page = await openUi(browser, port, null, true);
  check("the fog starts as the clip", await page.evaluate(() => Fog.tier), "video");
  check("and blends, so it can't be an underlay", await page.evaluate(() =>
    Number(getComputedStyle(document.getElementById("fogA")).opacity) < 1), true);
  await page.evaluate(() => {
    Fog.restMs = 1500;
    const A = document.getElementById("fogA");
    window.__frozen = true;
    const real = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "currentTime");
    Object.defineProperty(A, "currentTime", {
      get() { return window.__frozen ? 1.25 : real.get.call(this); },
      set(t) { real.set.call(this, t); },
    });
  });
  // two stalls of 2.5s and a restart between them: watched for, not timed
  await page.waitForFunction(() => Fog.tier !== "video" || Fog.dead, null, { timeout: 12000 })
    .catch(() => {});
  const rested = await page.evaluate(() => ({ tier: Fog.tier, dead: Fog.dead }));
  check("a fog stalled twice rests on the still", rested.tier, "still");
  check("rather than dying", rested.dead, false);
  await page.evaluate(() => { window.__frozen = false; });
  await page.waitForFunction(() => Fog.tier === "video", null, { timeout: 5000 }).catch(() => {});
  check("and comes back", await page.evaluate(() => Fog.tier), "video");
  await page.close();
  server.close();

  await browser.close();
  process.exit(bad ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });
