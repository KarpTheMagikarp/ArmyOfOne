/* A mod dropped on the window, against the real handlers and a stand-in for
   pywebview's own drop listener.

   pywebview's listener calls stopPropagation, and when it sat on document the
   drop never reached the page's listener on window, so the overlay stayed up
   over the drawer for good. Also pins the drawer naming the drop the moment it
   lands, before the host has said where it is, the disc beside Close while it
   reads, and Install asking for the game's required fixes to go in ahead of
   the mod.

     npm install jsdom && node tools/drop_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("ui/index.html", "utf8");

// settled by hand, so "while it's loading" is a state the probe can stop in
function later(){
  let settle;
  const promise = new Promise(r => { settle = r; });
  return { promise, settle };
}

const ROOT = "D:\\mods\\FFXIII-2 HD";
const JOBS = JSON.parse(fs.readFileSync("ui/jobs.json", "utf8"));
const REQUIRED = JOBS.games.find(g => g.id === "ff13-2").jobs
  .filter(j => (j.kind || "required") === "required").map(j => j.id);

const calls = { install: [], unpack: [], survey: [] };
let identify = later(), survey = later(), addon = later();
// shell.unpack_drop's two refusals: busy while one is still going, and a plain
// one, like an HD install running, that it also says through "dropped"
const BUSY = "still unpacking the last archive. Drop this one again once it has finished.";
let unpackingNow = false, refuseNext = "";

const known = {
  scan: () => Promise.resolve({ ok: true, found: true, path: "E:\\FINAL FANTASY XIII-2",
                                supported: [], applied: [], error: null }),
  identify_drop: path => { calls.identify = (calls.identify || []).concat([path]);
                          return identify.promise; },
  hd_survey: (game, job) => { calls.survey.push([game, job]); return survey.promise; },
  hd_categories: () => Promise.resolve({ categories: [], selected: [], installed: [] }),
  list_addons: () => Promise.resolve({ ok: true, path: "addons", mods: [] }),
  install_addon: () => addon.promise,
  // the drawer's one door now; an add-on comes back through it the same way
  install_mod: () => addon.promise,
  unpack_drop: path => {
    calls.unpack.push(path);
    if (refuseNext) return Promise.resolve({ ok: false, error: refuseNext });
    if (unpackingNow) return Promise.resolve({ ok: false, busy: true, error: BUSY });
    unpackingNow = true;
    return Promise.resolve({ ok: true, started: true });
  },
  install_hd_project: (...args) => { calls.install.push(args);
                                     return Promise.resolve({ ok: true, started: true }); },
  // the shell's answer to Apply while an HD install has this game, and then
  // a run that starts and stops at bridge.apply's own gate before any patch
  apply: (...args) => {
    calls.apply = (calls.apply || []).concat([args]);
    if (calls.apply.length === 1)
      return Promise.resolve({ ok: false, started: false, error: REFUSED });
    setTimeout(() => w.__patch.receive({ type: "finished", ok: false, applied: 0, failed: 1,
                                         error: "the game folder isn't writable" }), 50);
    return Promise.resolve({ ok: true, started: true, error: null });
  },
};
const REFUSED = "the HD install is rewriting this game's archives. Apply once it has finished.";
// everything else answers ok, and nothing pretends to be a thenable
const api = new Proxy(known, {
  get: (target, key) => key in target ? target[key]
    : key === "then" || typeof key !== "string" ? undefined
    : () => Promise.resolve({ ok: true }),
});

const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/ui/index.html",
  beforeParse(window){
    window.pywebview = { api };
    window.fetch = () => Promise.resolve({ ok: false });
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.load = () => {};
    window.HTMLMediaElement.prototype.pause = () => {};
    window.console.warn = () => {};
    window.console.log = () => {};
  },
});
const w = dom.window, doc = w.document;
const wait = ms => new Promise(r => setTimeout(r, ms));
// for a state that's a task or two off: polled to a deadline, so a loaded
// machine is a longer wait rather than a red run
async function until(test, ms = 2000){
  const end = Date.now() + ms;
  while (!test() && Date.now() < end) await wait(10);
}

const results = [];
const check = (name, pass, seen) => results.push([name, !!pass, seen]);

const note = () => (doc.getElementById("mdNote") || {}).textContent || "";
const hint = () => (doc.getElementById("hintText") || {}).textContent || "";
const askMsg = () => (doc.getElementById("askMsg") || {}).textContent || "";
const spinning = () => !!doc.querySelector("#md footer.loading");
const overlay = () => doc.getElementById("drop").classList.contains("on");
const install = () => doc.querySelector("#mdExtras button.run");

// Explorer's drags say "Files"; one of the art inside the window doesn't
function dragIn(types = ["Files"]){
  const e = new w.Event("dragenter", { bubbles: true, cancelable: true });
  Object.defineProperty(e, "dataTransfer", { value: { types } });
  doc.body.dispatchEvent(e);
}

function drop(files = [{ name: "FFXIII-2 HD" }]){
  const e = new w.Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(e, "dataTransfer",
                        { value: { files, types: files.length ? ["Files"] : ["text/uri-list"] } });
  // the overlay takes the drop while it's up
  (overlay() ? doc.getElementById("drop") : doc.body).dispatchEvent(e);
}

w.addEventListener("load", () => setTimeout(async () => {
  try {
    w.pick("ff13-2");
    await wait(400);

    // what pywebview's DOMEventHandler(on_drop, True, True) puts on window
    let host = 0;
    w.addEventListener("drop", e => { e.preventDefault(); e.stopPropagation(); host++; });

    dragIn();
    check("dragging a folder in shows the overlay", overlay());
    drop();
    check("the host side still hears the drop", host === 1, host);
    check("the overlay goes the moment it's dropped", !overlay());
    check("and the drawer names it before the host has answered",
          doc.getElementById("md").classList.contains("on") && spinning()
          && note() === "Reading FFXIII-2 HD...", note());

    w.__patch.receive({ type: "dropped", paths: [ROOT] });
    await wait(30);
    check("the drawer opens on it", doc.getElementById("md").classList.contains("on"));
    check("and spins while it looks", spinning() && /^Reading /.test(note()), note());
    const disc = doc.getElementById("mdDisc");
    check("the disc sits just left of Close",
          disc && disc.nextElementSibling && disc.nextElementSibling.id === "mdClose");

    identify.settle({ kind: "hdproject", jobId: "hdproject2",
                      detail: { root: ROOT, name: "FFXIII-2 HD" } });
    // the read starts a task after the rows go up, which on a busy machine
    // was past a fixed 60ms. The read itself is held open until settled below
    await until(() => install() && install().textContent === "Checking...");
    check("it says it's reading the game for the mod, by name",
          note() === "Checking your game for FFXIII-2 HD...", note());
    check("still spinning while the game is read", spinning());
    check("Install waits for the read",
          install() && install().disabled && install().textContent === "Checking...",
          install() && install().textContent);

    survey.settle({ sizes: {}, applied: [], verified: true, suspect: {} });
    await wait(60);
    check("then says it's ready", note() === "FFXIII-2 HD is ready. Pick what you want below.",
          note());
    check("and the disc stops", !spinning());
    check("and Install is live", install() && !install().disabled
          && /^Install \(\d+\)$/.test(install().textContent), install() && install().textContent);

    install().click();
    await wait(30);
    const sent = calls.install[0] || [];
    check("Install asks for the required fixes first",
          JSON.stringify(sent[4]) === JSON.stringify(REQUIRED), JSON.stringify(sent));

    w.__patch.receive({ type: "hd", stage: "patched", gameId: "ff13-2", applied: REQUIRED });
    await wait(30);
    const marks = () => JSON.stringify(
      JSON.parse(w.eval("JSON.stringify([...(applied.get('ff13-2') || [])])")).sort());
    check("the fixes that went in count as applied",
          marks() === JSON.stringify([...REQUIRED].sort()), marks());
    const row = id => doc.querySelector(`.row[data-id="${id}"]`);
    check("and their rows say so", row("laa") && row("laa").dataset.settled === "1"
          && row("audio") && row("audio").dataset.settled === "0");
    const launch = () => !!doc.querySelector("#launchWrap.on");
    await wait(30);
    check("Launch turns up with the required fixes in", launch());

    // the optional Audio Fix is still queued, so Apply is live mid-install,
    // and the shell refuses it there
    doc.getElementById("go").click();
    await wait(1200);
    check("Apply was asked and refused", (calls.apply || []).length === 1);
    check("a refused Apply leaves the marks alone", marks() === JSON.stringify([...REQUIRED].sort()),
          marks());
    check("and Launch with them", launch());
    doc.getElementById("go").click();
    await wait(1400);
    check("a run stopped before its first patch leaves them too",
          calls.apply.length === 2 && marks() === JSON.stringify([...REQUIRED].sort())
          && launch(), marks());

    w.__patch.receive({ type: "hd", stage: "done", steps: 3, applied: 3, skipped: 0,
                        nothing: 0, removed: 0, log: "" });
    await wait(60);

    // an ordinary mod spins while it unpacks and stops when it's in
    identify = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\ChocoboSpeed.zip"] });
    await wait(20);
    identify.settle({ kind: "archive" });
    await wait(30);
    check("an add-on spins while it unpacks", spinning() && /^Reading /.test(note()),
          note());
    addon.settle({ ok: true, modName: "ChocoboSpeed", installed: ["x.dll"], preserved: [] });
    await wait(30);
    check("and stops once it's in", !spinning() && note() === "Installed ChocoboSpeed.", note());

    // a second add-on while the first is going keeps the disc on and says why
    identify = later(); addon = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\First.zip"] });
    await wait(20);
    identify.settle({ kind: "archive" });
    await wait(30);
    identify = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\Second.zip"] });
    await wait(20);
    identify.settle({ kind: "archive" });
    await wait(30);
    check("a second add-on mid-install says so and keeps spinning",
          spinning() && /^Still installing the last one\. Drop Second\.zip/.test(note()), note());
    addon.settle({ ok: true, modName: "First", installed: ["x.dll"], preserved: [] });
    await wait(30);
    check("and the first one's answer stops it", !spinning() && note() === "Installed First.",
          note());

    // an archive that turns out not to be the mod answers the drawer
    const unpack = event => { unpackingNow = false; w.__patch.receive(event); };
    identify = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\one.zip"] });
    await wait(20);
    identify.settle({ kind: "maybe-hdproject" });
    await wait(30);
    check("unpacking the mod spins", spinning() && calls.unpack.length === 1);
    unpack({ type: "dropped", paths: [], from: "D:\\mods\\one.zip",
             error: "that archive is not the HD Project" });
    await wait(30);
    check("a failed unpack says so in the drawer, by name, and stops",
          !spinning() && note() === "one.zip: that archive is not the HD Project", note());

    // a second archive while one is unpacking: the backend says busy, and the
    // first is still going, so the disc stays on
    identify = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\two.zip"] });
    await wait(20);
    identify.settle({ kind: "maybe-hdproject" });
    await wait(30);
    identify = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\three.zip"] });
    await wait(20);
    identify.settle({ kind: "maybe-hdproject" });
    await wait(30);
    // the backend's reasons are parts of its own sentences, so the line starts them big
    const said = text => text[0].toUpperCase() + text.slice(1);
    check("a busy unpack keeps the disc on and says why", spinning() && note() === said(BUSY), note());
    unpack({ type: "dropped", paths: [], from: "D:\\mods\\two.zip",
             error: "that archive is not the HD Project" });
    await wait(30);
    check("the first one failing doesn't read as the second",
          note() === "two.zip: that archive is not the HD Project", note());

    // the overlay comes down on the host's answer too, in case the page's own
    // listener never runs in some WebView2 build
    dragIn();
    check("the overlay is up again", overlay());
    identify = later();
    w.__patch.receive({ type: "dropped", paths: [ROOT] });
    await wait(20);
    check("the host's answer takes it down", !overlay());

    // any other refusal is final, so the disc stops on it
    identify.settle({ kind: "folder" });
    await wait(30);
    identify = later();
    refuseNext = "an HD install is running, so drop the archive again once it has finished";
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\four.zip"] });
    await wait(20);
    identify.settle({ kind: "maybe-hdproject" });
    await wait(30);
    check("a refused unpack stops the disc", !spinning() && note() === said(refuseNext), note());

    // something dragged that isn't a file, like the art inside the window:
    // no overlay, and the host's empty answer to it isn't a complaint
    const quiet = [note(), hint()];
    dragIn(["text/uri-list"]);
    check("a drag with no files in it leaves the overlay down", !overlay());
    drop([]);
    w.__patch.receive({ type: "dropped", paths: [] });
    await wait(30);
    check("and nothing is said about it", note() === quiet[0] && hint() === quiet[1],
          `${note()} | ${hint()}`);

    // the overlay takes pointer events now, so one stranded by a dragleave
    // that never came would swallow every click. A drag holds the mouse, so
    // the mouse moving means it's over
    dragIn();
    check("the overlay is up for a file", overlay());
    doc.body.dispatchEvent(new w.MouseEvent("mousemove", { bubbles: true }));
    check("moving the mouse takes a stranded one down", !overlay());
    dragIn();
    check("and the next drag still brings it up", overlay());
    doc.body.dispatchEvent(new w.MouseEvent("mousemove", { bubbles: true }));

    // a drop the host never answers can't leave the disc going
    const realTimeout = w.setTimeout;
    w.setTimeout = (fn, ms, ...rest) => realTimeout(fn, ms >= 8000 ? 40 : ms, ...rest);
    dragIn();
    drop([{ name: "Lost.zip" }]);
    check("a drop spins on its name straight away", spinning() && note() === "Reading Lost...",
          note());
    await wait(150);
    w.setTimeout = realTimeout;
    check("and stops if the host never answers", !spinning() && /^Couldn't read/.test(note()), note());

    // a job waiting on a file: the strip answers the drop, not the drawer
    const waiting = w.eval("Drop.request('job-x', 'Download it, then drop it here.')");
    dragIn();
    drop([{ name: "Needed.zip" }]);
    check("a waiting job says it's reading the drop, in the strip",
          askMsg() === "Reading Needed.zip...", askMsg());
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\Needed.zip"] });
    const handed = await Promise.race([waiting.then(() => true), wait(300).then(() => false)]);
    check("and hands it to the job", handed);

    // an unpack's answer isn't the one a drop in flight is waiting on, so it
    // can't cancel that drop's timer or swallow its answer
    dragIn();
    drop([{ name: "Other.zip" }]);
    w.__patch.receive({ type: "dropped", paths: [], from: "D:\\mods\\five.zip",
                        error: "that archive is not the HD Project" });
    await wait(20);
    w.__patch.receive({ type: "dropped", paths: [] });
    await wait(20);
    check("an unpack answering mid-drop leaves the drop its own answer",
          /^Couldn't read/.test(note()) && !spinning(), note());

    // the drawer took this one, so a job that starts waiting before the host
    // answers never asked for it
    identify = later();
    dragIn();
    drop([{ name: "Patch.zip" }]);
    const late = w.eval("Drop.request('job-y', 'Waiting on a file.')");
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\Patch.zip"] });
    await wait(20);
    check("a drop the drawer took stays the drawer's when a job starts waiting",
          askMsg() === "Waiting on a file." && /^Reading Patch/.test(note()),
          `${askMsg()} | ${note()}`);
    identify.settle({ kind: "folder" });
    await wait(20);

    // and if the host never answers it, the drawer is what stops, not the strip
    w.eval("Drop.finish(null)");
    await late;
    w.setTimeout = (fn, ms, ...rest) => realTimeout(fn, ms >= 8000 ? 40 : ms, ...rest);
    dragIn();
    drop([{ name: "Slow.zip" }]);
    w.eval("Drop.request('job-z', 'Waiting on a file.')");
    await wait(150);
    w.setTimeout = realTimeout;
    check("an unanswered drop stops the disc where it was taken",
          !spinning() && /^Couldn't read .*Mods/.test(note()) && askMsg() === "Waiting on a file.",
          `${note()} | ${askMsg()}`);
    w.eval("Drop.finish(null)");

    // a folder holding the HD Project's zip: the drawer names the zip, and
    // the page opens that the way it would have opened it dropped on its own
    calls.identify = [];
    refuseNext = "";
    unpackingNow = false;
    identify = later();
    addon = later();
    w.__patch.receive({ type: "dropped", paths: ["D:\\mods\\Holder"] });
    await wait(20);
    identify.settle({ kind: "folder" });
    await wait(30);
    identify = later();
    const inner = "D:\\mods\\Holder\\FF XIII-2 HD.zip";
    addon.settle({ ok: false, kind: "maybe-hdproject", path: inner });
    await wait(30);
    identify.settle({ kind: "maybe-hdproject" });
    await wait(30);
    check("a folder holding the HD Project's zip opens the zip",
          calls.identify[calls.identify.length - 1] === inner
          && calls.unpack[calls.unpack.length - 1] === inner,
          JSON.stringify([calls.identify, calls.unpack.slice(-1)]));
    unpackingNow = false;

    // a voice change asks Mods to read the game again, and the mod it holds
    // is XIII-2's. Surveyed against XIII it answers empty, and that wiped the
    // XIII-2 survey's sizes and its NEEDS REBUILD
    calls.survey.length = 0;
    w.pick("ff13");
    await wait(300);
    w.eval("Mods.resurvey()");
    await wait(30);
    check("a mod for another game isn't surveyed against this one",
          !calls.survey.some(([game]) => game === "ff13"), JSON.stringify(calls.survey));
    // it's owed, though, so a run that finished while the user was elsewhere
    // isn't shown as never having happened when they come back
    w.pick("ff13-2");
    await wait(300);
    w.eval("Mods.open()");
    await wait(30);
    check("and back on its own game it's read again",
          JSON.stringify(calls.survey) === JSON.stringify([["ff13-2", "hdproject2"]]),
          JSON.stringify(calls.survey));
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }

  let bad = 0;
  for (const [name, pass, seen] of results){
    console.log(`  ${pass ? "ok  " : "FAIL"}  ${name}${pass || seen === undefined ? "" : `  (${seen})`}`);
    if (!pass) bad++;
  }
  process.exit(bad ? 1 : 0);
}, 500));
