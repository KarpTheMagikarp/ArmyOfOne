/* The Mods drawer's line and list, against the real page and a stand-in
   bridge: what it says while it works (a verb per step, a count that ticks
   over in place, nothing after the answer), the one line it ends on with the
   whole account behind the i, Installed under its own heading with each mod by
   what it is, Open page on an HD Project watching Downloads and taking only
   the HD Project from there, and Playable Characters reading as in when HD
   Models Plus from the drawer has them. Then, each on a page of its own, what
   happens when two things go at once: one job's steps never talk over
   another's answer, an unrelated drop leaves a watch alone, a watch that's
   over can be started again, and Playable Characters being in is a badge,
   never a change to what Install builds.

     npm install jsdom && node tools/mods_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("ui/index.html", "utf8");

function later(){
  let settle;
  const promise = new Promise(r => { settle = r; });
  return { promise, settle };
}

const calls = { acquire: [], cancel: [], unpack: [], install: [], remove: [], identify: [] };
let identify = later(), installed = later(), removed = later(), survey = later();
let listed = { ok: true, path: "addons", mods: [] };

const known = {
  scan: game => Promise.resolve({ ok: true, found: true, path: `E:\\${game}`,
                                  supported: [], applied: [], error: null }),
  identify_drop: path => { calls.identify.push(path); return identify.promise; },
  install_mod: (...args) => { calls.install.push(args); return installed.promise; },
  remove_addon: (...args) => { calls.remove.push(args); return removed.promise; },
  list_addons: () => Promise.resolve(listed),
  hd_survey: () => survey.promise,
  hd_categories: () => Promise.resolve({ categories: [], selected: [], installed: [] }),
  unpack_drop: path => { calls.unpack.push(path); return Promise.resolve({ ok: true, started: true }); },
  begin_acquire: (...args) => { calls.acquire.push(args); return Promise.resolve({ ok: true }); },
  cancel_acquire: id => { calls.cancel.push(id); return Promise.resolve({ ok: true }); },
};
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
    window.HTMLCanvasElement.prototype.getContext = () => null;
    window.console.warn = () => {};
    window.console.log = () => {};
  },
});
const w = dom.window, doc = w.document;
const wait = ms => new Promise(r => setTimeout(r, ms));

const results = [];
const check = (name, pass, seen) => results.push([name, !!pass, seen]);

const note = () => (doc.getElementById("mdNote") || {}).textContent || "";
const kind = () => (doc.getElementById("mdNote") || {}).className || "";
const spinning = () => !!doc.querySelector("#md footer.loading");
const more = () => {
  const mark = doc.getElementById("mdMore");
  return mark && !mark.hidden ? mark.querySelector(".tip").textContent : "";
};
// the shell names the game a watch was started for on each of its events
const gameOf = (event, started) => event && event.type === "acquire" && !("game" in event)
  ? { ...event, game: (started.filter(a => a[0] === event.jobId).pop() || [])[3] || "" } : event;
const say = event => w.__patch.receive(gameOf(event, calls.acquire));

w.addEventListener("load", () => setTimeout(async () => {
  try {
    w.pick("ff13-2");
    await wait(400);
    w.eval("Mods.open()");
    await wait(60);

    const groups = () => [...doc.querySelectorAll("#md .grp")].map(g => g.textContent);
    check("Worth getting and Installed are two headings",
          JSON.stringify(groups()) === JSON.stringify(["Worth getting", "Installed"]),
          JSON.stringify(groups()));
    const list = doc.getElementById("mdList");
    check("and what's installed sits under its own",
          list.firstElementChild && list.firstElementChild.textContent === "Installed"
          && /Nothing installed yet/.test(list.textContent), list.textContent);

    // a drop, through every step it reports
    say({ type: "dropped", paths: ["D:\\Downloads\\(STANDALONE VERSION) HD Fonts and GUI - Base mod-40-2-2-1779722868.zip"] });
    await wait(30);
    check("the line reads the drop by what the mod is",
          note() === "Reading HD Fonts and GUI..." && spinning(), note());
    identify.settle({ kind: "archive" });
    await wait(30);
    say({ type: "mods", phase: "install", name: "HD Fonts and GUI", done: 25, of: 553 });
    await wait(20);
    check("then what it's doing now, with how far as a percent of the step",
          note() === "Installing HD Fonts and GUI... 4%" && kind() === "busy", note());
    const verb = doc.querySelector("#mdNote .ph");
    say({ type: "mods", phase: "install", name: "HD Fonts and GUI", done: 550, of: 553 });
    await wait(20);
    check("a count ticks over in place",
          note() === "Installing HD Fonts and GUI... 99%"
          && doc.querySelector("#mdNote .ph") === verb, note());
    say({ type: "mods", text: "[files] HD Fonts and GUI: 553 files into white_imgu.win32.bin" });
    await wait(20);
    check("a log line doesn't take the line over", /^Installing /.test(note()), note());

    const account = "Installed HD Fonts and GUI beside ffxiii2img.exe: version.dll and 2 folders, 553 files in all.";
    listed = { ok: true, mods: [{ name: "(STANDALONE VERSION) HD Fonts and GUI - Base mod",
                                  title: "HD Fonts and GUI", kind: "loader", managed: true,
                                  files: ["hd_textures/", "version.dll"],
                                  about: "Loads as version.dll" }] };
    installed.settle({ ok: true, kind: "loader", modName: "(STANDALONE VERSION) HD Fonts and GUI - Base mod",
                       title: "HD Fonts and GUI", summary: "Installed HD Fonts and GUI.",
                       message: account, preserved: [], rejected: [] });
    await wait(40);
    check("it ends on one line, ticked",
          note() === "Installed HD Fonts and GUI." && kind() === "done" && !spinning(), note());
    check("with the whole account behind the i", more() === account, more());
    say({ type: "mods", phase: "install", name: "HD Fonts and GUI", done: 553, of: 553 });
    await wait(20);
    check("a step that lands after the answer changes nothing",
          note() === "Installed HD Fonts and GUI." && !spinning(), note());

    const row = [...doc.querySelectorAll("#mdList .mod")][0];
    check("the list shows it by what it is, and nothing under the name",
          row && row.querySelector("b").textContent === "HD Fonts and GUI"
          && !row.querySelector(".n span"),
          row && row.textContent);

    row.querySelector("button").click();
    await wait(20);
    check("Remove says what it's doing", note() === "Removing HD Fonts and GUI..." && spinning(),
          note());
    check("by the name it's on record as",
          calls.remove[0] && calls.remove[0][1] === "(STANDALONE VERSION) HD Fonts and GUI - Base mod",
          JSON.stringify(calls.remove[0]));
    // what the list says next: one whose dll antivirus took since
    listed = { ok: true, mods: [{ name: "Old Proxy", title: "Old Proxy", kind: "loader",
                                  managed: true, files: ["dinput8.dll"],
                                  about: "dinput8.dll is missing", warn: "dinput8.dll is missing" }] };
    removed.settle({ ok: true, summary: "Removed HD Fonts and GUI.",
                     message: "Removed HD Fonts and GUI. Everything it put beside ffxiii2img.exe is gone." });
    await wait(40);
    check("and what it did", note() === "Removed HD Fonts and GUI." && !spinning(), note());
    const warned = [...doc.querySelectorAll("#mdList .mod")][0];
    check("a line goes under a name only when something's wrong with it",
          warned && warned.querySelector(".n span")
          && warned.querySelector(".n span").textContent === "dinput8.dll is missing",
          warned && warned.textContent);

    // a long refusal: what happened on the line, what to do behind the i
    identify = later(); installed = later();
    say({ type: "dropped", paths: ["D:\\Downloads\\Blocked.zip"] });
    await wait(20);
    identify.settle({ kind: "archive" });
    await wait(20);
    say({ type: "mods", phase: "install", name: "Blocked", done: 1, of: 3 });
    await wait(20);
    check("a mod of three files says how far too", note() === "Installing Blocked... 33%", note());
    const refusal = "Windows Security blocked version.dll in Blocked as a threat, so it wasn't installed. "
      + "If you trust it, restore it from Protection history and allow it, then drop the mod again.";
    installed.settle({ ok: false, error: refusal });
    await wait(40);
    check("a long refusal shows what happened",
          note() === "Windows Security blocked version.dll in Blocked as a threat, so it wasn't installed."
          && kind() === "bad", note());
    check("and keeps the rest a hover away", more() === refusal, more());

    // Open page on an HD Project watches Downloads, and says so as it goes
    const open = [...doc.querySelectorAll("#mdExtras .mod")]
      .find(r => /FFXIII-2 HD/.test(r.textContent)).querySelector("button:not(.run)");
    open.click();
    await wait(20);
    check("Open page on an HD Project watches for its download",
          calls.acquire.length === 1 && calls.acquire[0][0] === "hdproject2"
          && calls.acquire[0][2] === true, JSON.stringify(calls.acquire));
    check("and says what to press", /^Press Download on the main file/.test(note()) && spinning(), note());
    say({ type: "acquire", stage: "downloading", jobId: "hdproject2", bytes: 5 * 1048576 });
    await wait(20);
    check("a download in progress reads as one",
          note() === "Downloading FFXIII-2 HD... 5 MB" && spinning(), note());
    say({ type: "acquire", stage: "downloading", jobId: "hdproject2", bytes: 1536 * 1048576 });
    await wait(20);
    check("and counts up", note() === "Downloading FFXIII-2 HD... 1.5 GB", note());
    open.click();
    await wait(20);
    check("a second Open page doesn't start a second watch", calls.acquire.length === 1);

    // what lands goes no further than the HD Project's own door
    identify = later(); installed = later();
    const before = calls.install.length;
    say({ type: "acquire", stage: "arrived", jobId: "hdproject2",
          path: "C:\\Users\\me\\Downloads\\Something Else.zip" });
    await wait(20);
    identify.settle({ kind: "archive" });
    await wait(40);
    check("what landed is only ever opened as the HD Project",
          calls.unpack.length === 1 && calls.install.length === before,
          JSON.stringify([calls.unpack, calls.install.length - before]));
    check("and the unpack says how far it's got", (() => {
      say({ type: "mods", phase: "unpack", done: 42, of: 100, bytes: true });
      return true;
    })());
    await wait(20);
    check("in percent", note() === "Unpacking Something Else... 42%" && spinning(), note());
    say({ type: "dropped", paths: [], from: "C:\\Users\\me\\Downloads\\Something Else.zip",
          error: "that archive is not the HD Project" });
    await wait(30);
    check("and a stray archive is said and left",
          /that archive is not the HD Project/.test(note()) && !spinning(), note());

    // XIII: HD Models Plus from the drawer has the characters in
    w.pick("ff13");
    await wait(400);
    identify = later(); survey = later();
    w.eval("Mods.open()");
    await wait(30);
    say({ type: "dropped", paths: ["D:\\mods\\FFXIII HD Project"] });
    await wait(20);
    identify.settle({ kind: "hdproject", jobId: "hdproject",
                      detail: { root: "D:\\mods\\FFXIII HD Project", name: "FFXIII HD Project" } });
    await wait(60);
    check("the read of the game says it's that, not Loading up",
          note() === "Checking your game for FFXIII HD Project..." && spinning(), note());
    survey.settle({ sizes: { pcs: 58 * 1048576 }, applied: [], verified: true, suspect: {},
                    covered: { pcs: ["HD Models Plus - Lightning"] } });
    await wait(80);
    const pcs = doc.querySelector('#mdExtras .opt-row[data-cat="pcs"]');
    check("Playable Characters reads as in",
          pcs && pcs.dataset.applied === "true" && pcs.querySelector(".size").textContent === "APPLIED"
          && pcs.dataset.on === "false", pcs && pcs.outerHTML.slice(0, 200));
    const tip = pcs && pcs.querySelector(".why .tip");
    check("and says what has them in",
          tip && /^HD Models Plus - Lightning from the Mods drawer already covers this\./.test(tip.textContent),
          tip && tip.textContent);
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }
  try {
    await together();
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }
  try {
    await instant();
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }
  try {
    await installedRows();
  } catch (e){
    check(`threw: ${e && e.stack}`, false);
  }

  let bad = 0;
  for (const [name, pass, seen] of results){
    console.log(`  ${pass ? "ok  " : "FAIL"}  ${name}${pass || seen === undefined ? "" : `  (${seen})`}`);
    if (!pass) bad++;
  }
  process.exit(bad ? 1 : 0);
}, 50));

/* a page of its own per scenario, so nothing one leaves behind decides the next */
// a Worth getting row that's in says so beside its name, under which it still
// says what it is
const isIn = row => !!row && [...row.querySelectorAll(".tag")].some(t => t.textContent === "Installed");

function boot(){
  const calls = { acquire: [], cancel: [], unpack: [], install: [], remove: [], hd: [],
                  pages: [], surveys: 0 };
  const ctl = { identify: [], install: [], unpack: [], remove: later(), surveys: [],
                categories: { categories: [], selected: [], installed: [] },
                listed: { ok: true, path: "addons", mods: [] }, acquire: [], applied: {} };
  const known = {
    scan: game => Promise.resolve({ ok: true, found: true, path: `E:\\${game}`,
                                    supported: [], applied: ctl.applied[game] || [], error: null }),
    identify_drop: () => Promise.resolve(ctl.identify.shift() || { kind: "archive" }),
    install_mod: (...args) => { calls.install.push(args); return (ctl.install.shift() || later()).promise; },
    remove_addon: (...args) => { calls.remove.push(args); return ctl.remove.promise; },
    list_addons: game => ctl.lists && ctl.lists[game] ? ctl.lists[game]() : Promise.resolve(ctl.listed),
    hd_survey: () => { calls.surveys++; return (ctl.surveys.shift() || later()).promise; },
    hd_categories: () => Promise.resolve(ctl.categories),
    install_hd_project: (...args) => { calls.hd.push(args); return Promise.resolve({ ok: true, started: true }); },
    unpack_drop: path => { calls.unpack.push(path);
                           return Promise.resolve(ctl.unpack.shift() || { ok: true, started: true }); },
    begin_acquire: (...args) => { calls.acquire.push(args);
                                  return Promise.resolve(ctl.acquire.shift() || { ok: true }); },
    cancel_acquire: id => { calls.cancel.push(id); return Promise.resolve({ ok: true }); },
    open_pages: pages => { calls.pages.push(pages); return Promise.resolve({ ok: true }); },
    restore_hd_project: game => { calls.restored = (calls.restored || []).concat([game]);
                                  return ctl.remove.promise; },
  };
  const api = new Proxy(known, {
    get: (target, key) => key in target ? target[key]
      : key === "then" || typeof key !== "string" ? undefined
      : () => Promise.resolve({ ok: true }),
  });
  const page = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/ui/index.html",
    beforeParse(window){
      window.pywebview = { api };
      window.fetch = () => Promise.resolve({ ok: false });
      window.HTMLMediaElement.prototype.play = () => Promise.resolve();
      window.HTMLMediaElement.prototype.load = () => {};
      window.HTMLMediaElement.prototype.pause = () => {};
      window.HTMLCanvasElement.prototype.getContext = () => null;
      window.console.warn = () => {};
      window.console.log = () => {};
    },
  });
  const pw = page.window, pd = pw.document;
  return {
    w: pw, doc: pd, calls, ctl,
    loaded: new Promise(r => pw.addEventListener("load", () => setTimeout(r, 50))),
    note: () => (pd.getElementById("mdNote") || {}).textContent || "",
    kind: () => (pd.getElementById("mdNote") || {}).className || "",
    spinning: () => !!pd.querySelector("#md footer.loading"),
    say: event => pw.__patch.receive(event),
    acq: event => pw.__acquire.receive(gameOf(event, calls.acquire)),
    async on(game){ pw.pick(game); await wait(400); pw.eval("Mods.open()"); await wait(60); },
    openPage(){ return [...pd.querySelectorAll("#mdExtras .mod")]
      .find(r => /HD/.test(r.textContent) && r.querySelector("button:not(.run)"))
      .querySelector("button:not(.run)"); },
  };
}

async function installedRows(){
  // a download's version beside its name, never in it, and the HD Project
  // under Installed with a Remove that's asked twice, being its Revert
  const p = boot(); await p.loaded;
  p.ctl.listed = { ok: true, path: "addons", mods: [
    { name: "HD Texture Pack", title: "HD Texture Pack", kind: "hd", jobId: "hdproject",
      managed: true, tag: "all 7 parts" },
    { name: "Less Grinding 73 1.3 2026-08-27T12-00-00", title: "Less Grinding", kind: "files",
      managed: true, tag: "v1.3" },
    { name: "Gameplay Tweaks", title: "Gameplay Tweaks", kind: "files", managed: true, tag: "" }] };
  await p.on("ff13");
  const rows = [...p.doc.querySelectorAll("#mdList .mod")];
  const named = rows.map(r => r.querySelector("b").textContent);
  check("names are names alone", JSON.stringify(named)
        === JSON.stringify(["FFXIII HD Project", "Less Grinding", "Gameplay Tweaks"]), JSON.stringify(named));
  const ver = rows[1] && rows[1].querySelector(".ver");
  check("and the version sits beside one, dim", ver && ver.textContent === "v1.3"
        && !rows[1].querySelector(".n span"), rows[1] && rows[1].innerHTML);
  check("none where there's none", rows[2] && !rows[2].querySelector(".ver"), rows[2] && rows[2].innerHTML);
  const hd = rows[0] && rows[0].querySelector("button");
  hd.click(); await wait(20);
  check("the HD Project's Remove asks first", hd.textContent === "Sure?" && !p.calls.restored
        && !p.calls.remove.length, `${hd.textContent} ${JSON.stringify(p.calls.restored)}`);
  hd.click(); await wait(20);
  check("and the second press is its Revert, not a drawer Remove",
        JSON.stringify(p.calls.restored) === JSON.stringify(["ff13"]) && !p.calls.remove.length
        && p.note() === "Removing FFXIII HD Project...", `${JSON.stringify(p.calls.restored)} ${p.note()}`);
  p.ctl.listed = { ok: true, path: "addons", mods: [] };
  p.ctl.remove.settle({ ok: true, restored: ["white_scru.win32.bin"] }); await wait(40);
  check("ending on what it did", p.note() === "Removed FFXIII HD Project.", p.note());
  p.w.close();
  {
    // a first press left alone goes back to Remove
    const q = boot(); await q.loaded;
    q.ctl.listed = { ok: true, path: "addons", mods: [
      { name: "FFXIII HD Project", title: "FFXIII HD Project", kind: "hd", managed: true, tag: "3 of 7" }] };
    await q.on("ff13");
    const b = q.doc.querySelector("#mdList .mod button");
    b.click(); await wait(4200);
    check("an unanswered Sure? goes back to Remove", b.textContent === "Remove" && !q.calls.restored,
          b.textContent);
    q.w.close();
  }
}

async function instant(){
  // the drawer opens on its game's list rather than a gap the answer fills
  const p = boot(); await p.loaded;
  const fonts = { ok: true, path: "addons", mods: [{ name: "HD Fonts and GUI", title: "HD Fonts and GUI",
                  kind: "loader", managed: true, files: ["version.dll"], about: "x", warn: "" }] };
  const lrList = later();
  p.ctl.lists = { "ff13-2": () => Promise.resolve(fonts), lr: () => lrList.promise };
  const names = () => [...p.doc.querySelectorAll("#mdList .mod b")].map(b => b.textContent).join("|");
  p.w.pick("ff13-2"); await wait(400);
  const first = p.w.eval(`(() => { Mods.open();
    return [...document.querySelectorAll("#mdList .mod b")].map(b => b.textContent).join("|"); })()`);
  check("Mods opens on the list its game read as it came up", first === "HD Fonts and GUI", first);
  p.w.eval("Mods.close()");
  p.w.pick("lr"); await wait(400);
  const other = p.w.eval(`(() => { Mods.open(); return document.querySelectorAll("#mdList .mod").length; })()`);
  check("never on another game's rows while this one's is on its way",
        other === 0 && !/Nothing installed/.test(p.doc.getElementById("mdList").textContent), other);
  lrList.settle({ ok: true, path: "addons", mods: [{ name: "Aerith Garb Restauration",
                  title: "Aerith Garb Restauration", kind: "files", managed: true, files: ["x"], warn: "" }] });
  await wait(40);
  check("and on it once it lands", names() === "Aerith Garb Restauration", names());
  p.w.eval("Mods.close()");
  p.ctl.lists.lr = () => new Promise(() => {});
  const back = p.w.eval(`(() => { Mods.open();
    return [...document.querySelectorAll("#mdList .mod b")].map(b => b.textContent).join("|"); })()`);
  check("opened again, it's on the list it had while the read underneath goes",
        back === "Aerith Garb Restauration", back);
  p.w.close();

  // and the next start opens on what the last one read
  const q = boot();
  q.w.localStorage.setItem("ffxiii.installed.ff13-2", JSON.stringify(fonts));
  q.ctl.lists = { "ff13-2": () => new Promise(() => {}) };
  await q.loaded;
  q.w.pick("ff13-2"); await wait(400);
  const kept = q.w.eval(`(() => { Mods.open();
    return [...document.querySelectorAll("#mdList .mod b")].map(b => b.textContent).join("|"); })()`);
  check("the next start opens on the list the last one read", kept === "HD Fonts and GUI", kept);
  q.w.close();
}

async function together(){
  {
    // a second drop while one goes in: its "drop it again" stays up past the
    // first one's next step, and the first one gets the line back after
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Alpha Mod.zip"] }); await wait(40);
    p.say({ type: "mods", phase: "install", name: "Alpha Mod", done: 25, of: 553 }); await wait(20);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Bravo Mod.zip"] }); await wait(40);
    p.say({ type: "mods", phase: "install", name: "Alpha Mod", done: 50, of: 553 }); await wait(20);
    check("a refusal for a second drop isn't talked over by the first's next step",
          /Drop Bravo Mod\.zip again/.test(p.note()) && p.spinning(), p.note());
    await wait(6200);
    p.say({ type: "mods", phase: "install", name: "Alpha Mod", done: 75, of: 553 }); await wait(20);
    check("and once it's been read, the one going in has the line back",
          p.note() === "Installing Alpha Mod... 13%", p.note());
    a.settle({ ok: true, title: "Alpha Mod", summary: "Installed Alpha Mod.", message: "x" });
    await wait(40);
    check("ending on its answer", p.note() === "Installed Alpha Mod." && !p.spinning(), p.note());
    p.w.close();
  }
  {
    // Remove while a drop goes in
    const p = boot(); await p.loaded;
    p.ctl.listed = { ok: true, mods: [{ name: "Old Mod", title: "Old Mod", kind: "addon",
                                       managed: true, files: ["a"], about: "x" }] };
    await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Alpha Mod.zip"] }); await wait(40);
    [...p.doc.querySelectorAll("#mdList .mod")][0].querySelector("button").click(); await wait(20);
    p.ctl.remove.settle({ ok: true, summary: "Removed Old Mod.", message: "all of it" }); await wait(40);
    p.say({ type: "mods", phase: "install", name: "Alpha Mod", done: 50, of: 553 }); await wait(20);
    check("Remove's answer stays up through a drop's next step", p.note() === "Removed Old Mod.",
          p.note());
    p.w.close();
  }
  {
    // a mod dropped while the HD Project unpacks: told, the disc still going
    // for the unpack, and the unpack back on the line by its name later
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "FFXIII-2 HD" });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\FFXIII-2 HD.zip"] }); await wait(60);
    p.say({ type: "mods", phase: "unpack", done: 10, of: 100, bytes: true }); await wait(20);
    const c = later(); p.ctl.install.push(c);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Charlie Mod.zip"] }); await wait(40);
    c.settle({ ok: false, busy: true, error: "still unpacking the last archive. Drop this one "
                                           + "again once it has finished." });
    await wait(40);
    p.say({ type: "mods", phase: "unpack", done: 11, of: 100, bytes: true }); await wait(20);
    check("a drop turned away while an unpack goes says so, and the disc keeps going",
          /^Still unpacking the last archive/.test(p.note()) && p.spinning(), p.note());
    await wait(6200);
    p.say({ type: "mods", phase: "unpack", done: 40, of: 100, bytes: true }); await wait(20);
    check("then the unpack has its line back, by name", p.note() === "Unpacking FFXIII-2 HD... 40%",
          p.note());
    p.w.close();
  }
  {
    // the HD Project's download landing while a drop goes in waits for it
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Small Outfit.zip"] }); await wait(40);
    check("an unrelated drop leaves the HD Project's watch alone", p.calls.cancel.length === 0,
          JSON.stringify(p.calls.cancel));
    p.ctl.identify.push({ kind: "archive", title: "FFXIII-2 HD" });
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.zip" }); await wait(40);
    check("and what lands waits for the drop going in", p.calls.unpack.length === 0,
          JSON.stringify(p.calls.unpack));
    a.settle({ ok: true, title: "Small Outfit", summary: "Installed Small Outfit.", message: "x" });
    await wait(60);
    check("then goes to be unpacked", p.calls.unpack.length === 1, JSON.stringify(p.calls.unpack));
    p.w.close();
  }
  {
    // an unpack turned away with nothing of ours unpacking: no "dropped" is
    // coming, so a late step from a finished install changes nothing
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.ctl.identify.push({ kind: "maybe-hdproject" });
    p.ctl.unpack.push({ ok: false, busy: true, error: "still installing the last mod. Drop "
                                                     + "this one again once it's in." });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\FFXIII-2 HD.zip"] }); await wait(60);
    check("an unpack the backend turned away is said", /^Still installing the last mod/.test(p.note())
          && !p.spinning(), p.note());
    p.say({ type: "mods", phase: "unpack", done: 5, of: 100, bytes: true }); await wait(20);
    check("and leaves nothing waiting on it", /^Still installing/.test(p.note()) && !p.spinning(),
          p.note());
    p.w.close();
  }
  {
    // a watch that's over: said, and Open page starts another; one still
    // going from before opens the page again
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    p.acq({ type: "acquire", stage: "downloading", jobId: "hdproject2", bytes: 3 * 1048576 });
    await wait(20);
    p.w.pick("ff13"); await wait(400);
    p.acq({ type: "acquire", stage: "downloading", jobId: "hdproject2", bytes: 9 * 1048576 });
    await wait(20);
    check("a download keeps its name through a game switch",
          p.note() === "Downloading FFXIII-2 HD... 9 MB", p.note());
    p.acq({ type: "acquire", stage: "expired", jobId: "hdproject2" }); await wait(20);
    check("a watch that's over says so and stops the disc",
          /^Stopped watching Downloads for FFXIII-2 HD/.test(p.note()) && !p.spinning(), p.note());
    p.w.pick("ff13-2"); await wait(400); p.w.eval("Mods.open()"); await wait(60);
    p.ctl.acquire.push({ ok: true, already: true });
    p.openPage().click(); await wait(40);
    check("and Open page starts another", p.calls.acquire.length === 2, p.calls.acquire.length);
    check("which, still going from before, opens the page", p.calls.pages.length === 1,
          JSON.stringify(p.calls.pages));
    p.w.close();
  }
  {
    // Playable Characters in from HD Models Plus: a badge, not a change to
    // what Install builds or the voices cost, and gone once the mod is
    const p = boot(); await p.loaded;
    const MB = 1048576;
    p.ctl.categories = { categories: [{ id: "weapons" }, { id: "pcs" }],
                         selected: ["weapons", "pcs"], installed: [] };
    p.ctl.listed = { ok: true, mods: [{ name: "HD Models Plus - Lightning", kind: "files",
                                       title: "HD Models Plus - Lightning", managed: true,
                                       files: ["a"], about: "x" }] };
    await p.on("ff13");
    const first = later(), second = later();
    p.ctl.surveys.push(first, second);
    p.ctl.identify.push({ kind: "hdproject", jobId: "hdproject",
                          detail: { root: "D:\\mods\\FFXIII HD Project", name: "FFXIII HD Project" } });
    p.say({ type: "dropped", paths: ["D:\\mods\\FFXIII HD Project"] }); await wait(80);
    const box = { language: "Japanese", has: [], owed: [], parts: {}, backup: {},
                  art: { pcs: 300 * MB, weapons: 100 * MB } };
    first.settle({ sizes: { pcs: 58 * MB, weapons: 20 * MB }, applied: [], verified: true,
                   suspect: {}, voices: box, covered: { pcs: ["HD Models Plus - Lightning"] } });
    await wait(120);
    const row = () => p.doc.querySelector('#mdExtras .opt-row[data-cat="pcs"]');
    const install = () => p.doc.querySelector("#mdExtras .run");
    const voices = () => p.doc.querySelector("#mdExtras .opt-row.voices .size").textContent;
    check("a recorded tick on a category that's in from the drawer stays ticked",
          install().textContent === "Install (2)" && voices() === "400 MB",
          `${install().textContent} | ${voices()}`);
    check("and ticked, it's what's left to build, not a reinstall",
          row().querySelector(".size").textContent === "58 MB", row().querySelector(".size").textContent);
    row().click(); await wait(20);
    check("unticked, it reads as in", row().dataset.applied === "true"
          && row().querySelector(".size").textContent === "APPLIED", row().outerHTML.slice(0, 160));
    p.ctl.listed = { ok: true, mods: [] };
    [...p.doc.querySelectorAll("#mdList .mod")].find(r => /HD Models Plus/.test(r.textContent))
      .querySelector("button").click();
    await wait(20);
    p.ctl.remove.settle({ ok: true, summary: "Removed HD Models Plus - Lightning." }); await wait(60);
    check("taking the mod off reads the game again", p.calls.surveys === 2, p.calls.surveys);
    second.settle({ sizes: { pcs: 58 * MB, weapons: 20 * MB }, applied: [], verified: true,
                    suspect: {}, voices: box, covered: {} });
    await wait(120);
    check("and Playable Characters isn't in any more", row().dataset.applied === "false",
          row().outerHTML.slice(0, 160));
    p.w.close();
  }
  {
    // a version picker belongs to the game it was put up on
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Serah Hair.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "Serah Hair", title: "Serah Hair",
               versions: ["Pink", "Blonde"] });
    await wait(60);
    const asks = () => p.doc.querySelector("#mdExtras .mod.asks");
    const groups = () => [...p.doc.querySelectorAll("#mdExtras .grp")].map(g => g.textContent);
    check("a mod that comes in versions asks which, under Dropped since it's nobody's row",
          asks() && asks().querySelectorAll(".choice").length === 2 && groups()[0] === "Dropped"
          && asks().dataset.open === "true", p.doc.querySelector("#mdExtras").textContent);
    check("with nothing picked for it, so Install waits",
          asks() && !asks().querySelector('.choice[data-on="true"]') && asks().querySelector(".run").disabled,
          asks() && asks().querySelector(".run").outerHTML);
    asks().querySelectorAll(".choice")[1].click(); await wait(20);
    check("a pick is the one Install names", asks().querySelector(".run").textContent === "Install Blonde"
          && !asks().querySelector(".run").disabled
          && asks().querySelectorAll(".choice")[1].getAttribute("aria-checked") === "true",
          asks().querySelector(".run").textContent);
    p.w.pick("ff13"); await wait(400); p.w.eval("Mods.open()"); await wait(80);
    check("and it isn't on another game's drawer", !asks() && !groups().includes("Dropped"),
          JSON.stringify(groups()));
    p.w.pick("ff13-2"); await wait(400); p.w.eval("Mods.open()"); await wait(80);
    check("back on its own game it's still asking, with the pick it had",
          asks() && asks().querySelector(".run").textContent === "Install Blonde",
          asks() && asks().textContent);
    asks().querySelector(".n").click(); await wait(20);
    check("its name closes it, and the line under says what's picked",
          asks().dataset.open === "false" && /^Blonde picked/.test(asks().querySelector(".peek").textContent),
          asks().querySelector(".peek").textContent);
    asks().querySelector(".peek").click(); await wait(20);
    check("and that line opens it again", asks().dataset.open === "true", asks().dataset.open);
    p.w.close();
  }
  {
    // a pack of tweaks: one in, and the list stays up for the next with it ticked
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(), b = later(); p.ctl.install.push(a, b);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Gameplay Tweaks.zip"] }); await wait(40);
    const tweaks = ["Mod_Adamantite", "Mod_RunSpeed"];
    a.settle({ ok: false, kind: "versions", modName: "Gameplay Tweaks", title: "Gameplay Tweaks",
               versions: tweaks, tweaks: true });
    await wait(60);
    const asks = () => p.doc.querySelector("#mdExtras .mod.asks");
    check("a pack of tweaks says they each go in on their own",
          asks() && /2 tweaks, each its own mod/.test(asks().textContent), asks() && asks().textContent);
    const said = [...asks().querySelectorAll(".choice .t")].map(t => t.textContent);
    check("each by what it is, not its .bat", JSON.stringify(said) === '["Adamantite","RunSpeed"]',
          JSON.stringify(said));
    asks().querySelectorAll(".choice")[1].click(); await wait(20);
    asks().querySelector(".run").click(); await wait(20);
    b.settle({ ok: true, kind: "files", modName: "Gameplay Tweaks (RunSpeed)",
               title: "Gameplay Tweaks (RunSpeed)", summary: "Installed Gameplay Tweaks (RunSpeed).",
               message: "", whole: true,
               again: { modName: "Gameplay Tweaks", title: "Gameplay Tweaks", versions: tweaks,
                        done: ["Mod_RunSpeed"], tweaks: true } });
    await wait(80);
    const ticked = () => [...asks().querySelectorAll(".choice")]
      .filter(c => /installed/.test(c.textContent)).map(c => c.dataset.version);
    check("once one's in the list is still up, that one ticked and nothing picked",
          asks() && JSON.stringify(ticked()) === '["Mod_RunSpeed"]'
          && !asks().querySelector('.choice[data-on="true"]'), asks() && asks().textContent);
    check("and it says how many are in", /1 of 2 tweaks in/.test(asks().textContent),
          asks().textContent);
    check("with the install said on the line", /Installed Gameplay Tweaks \(RunSpeed\)/.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // a .bat's questions one at a time, each answer so far said once rather than per line
    const p = boot(); await p.loaded; await p.on("ff13");
    const a = later(), b = later(); p.ctl.install.push(a, b);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Lightning Nude Mod.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "Lightning Nude Mod", title: "Lightning Nude Mod",
               versions: ["Nude Lighting", "Default Lightning"] });
    await wait(60);
    const asks = () => p.doc.querySelector("#mdExtras .mod.asks");
    asks().querySelectorAll(".choice")[0].click(); await wait(20);
    asks().querySelector(".run").click(); await wait(20);
    b.settle({ ok: false, kind: "versions", modName: "Lightning Nude Mod", title: "Lightning Nude Mod",
               versions: ["Nude Lighting, Boots", "Nude Lighting, Bare feet"] });
    await wait(80);
    const said = [...asks().querySelectorAll(".choice .t")].map(t => t.textContent);
    check("the next question's answers by themselves", JSON.stringify(said) === '["Boots","Bare feet"]',
          JSON.stringify(said));
    check("and what's picked so far on the line", /Lightning Nude Mod: Nude Lighting\. Pick the next/.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // a FOMOD installer's questions, one after another, then in
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(), b = later(), c = later(); p.ctl.install.push(a, b, c);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Serah Outfits-77-1-0.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "Serah Outfits", title: "Serah Outfits",
               question: "Colour", versions: ["Red", "Blue"], recommended: "Red",
               about: { Blue: "The blue one." }, more: true });
    await wait(60);
    const asks = () => p.doc.querySelector("#mdExtras .mod.asks");
    check("an installer's question says what it's asking",
          asks() && asks().querySelector(".n span").textContent === "Colour"
          && /Serah Outfits: Colour\. Pick one and press Next\./.test(p.note()), p.note());
    check("its recommended pick is picked, and Install is Next while more are to come",
          asks() && asks().querySelector(".run").textContent === "Next"
          && asks().querySelector('.choice[data-on="true"]').dataset.version === "Red",
          asks() && asks().querySelector(".run").textContent);
    asks().querySelectorAll(".choice")[1].click(); await wait(20);
    asks().querySelector(".run").click(); await wait(40);
    check("Next answers with the pick", p.calls.install[1] && p.calls.install[1][3] === "Blue",
          JSON.stringify(p.calls.install[1]));
    b.settle({ ok: false, kind: "versions", modName: "Serah Outfits", title: "Serah Outfits",
               question: "Extras: Hood", versions: ["Install it", "Leave it out"], more: false });
    await wait(60);
    check("and the next question takes its place, its last saying Install",
          asks() && asks().querySelector(".n span").textContent === "Extras: Hood"
          && asks().querySelector(".run").textContent === "Install"
          && [...asks().querySelectorAll(".choice .t")].map(t => t.textContent).join("|")
             === "Install it|Leave it out", asks() && asks().textContent);
    asks().querySelectorAll(".choice")[1].click(); await wait(20);
    asks().querySelector(".run").click(); await wait(40);
    c.settle({ ok: true, kind: "files", title: "Serah Outfits", summary: "Installed Serah Outfits.",
               message: "x" });
    await wait(60);
    check("then it's in, and the question's gone", !asks() && p.note() === "Installed Serah Outfits.",
          p.note());
    p.w.close();
  }
  {
    // HD Models Plus: its versions are which recipes run, so each says what it is
    const p = boot(); await p.loaded; await p.on("ff13");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\FF_XIII_HD_Models_Plus-11-4-1-7-1649368856.zip"] });
    await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "HD Models Plus", title: "HD Models Plus",
               jobId: "hdmodelsplus", recommended: "Ultimate",
               versions: ["Ultimate", "Ultimate, HQ Hair", "High Quality"],
               about: { "Ultimate": "The most detailed: rounder arms, legs and fingers.",
                        "Ultimate, HQ Hair": "The same, with Lightning's sharper hair.",
                        "High Quality": "The game's own cutscene models, used everywhere." } });
    await wait(60);
    const row = p.doc.querySelector("#mdExtras .mod.asks");
    const picks = row ? [...row.querySelectorAll(".choice")] : [];
    check("it asks under its own Worth getting row, opened like the HD Project's",
          row && row.querySelector(".n b").textContent === "HD Models Plus" && row.dataset.open === "true"
          && !p.doc.querySelector("#mdList .choice")
          && ![...p.doc.querySelectorAll("#md .grp")].some(g => g.textContent === "Dropped"),
          row && row.textContent);
    check("its three versions each say what they are",
          picks.length === 3 && /rounder arms/.test(picks[0].textContent)
            && /cutscene models/.test(picks[2].textContent), picks.map(r => r.textContent).join(" | "));
    const page = row && row.querySelector(":scope > button");
    check("and no Open page while it's open, the download's in hand",
          page && p.w.getComputedStyle(page).display === "none", page && p.w.getComputedStyle(page).display);
    row.querySelector(".chev").click(); await wait(20);
    check("closed, Open page is back", p.w.getComputedStyle(page).display !== "none",
          p.w.getComputedStyle(page).display);
    row.querySelector(".chev").click(); await wait(20);
    check("one Install, with the recommended one picked and marked",
          row && row.querySelectorAll("button.run").length === 1 && picks[0].dataset.on === "true"
          && picks[0].querySelector(".flag").textContent === "recommended"
          && row.querySelector(".run").textContent === "Install Ultimate", row && row.querySelector(".run").textContent);
    const b = later(); p.ctl.install.push(b);
    if (row) row.querySelector(".run").click();
    await wait(40);
    const asked = p.calls.install[p.calls.install.length - 1] || [];
    check("and Install asks for that one", asked[3] === "Ultimate", JSON.stringify(asked));
    const run = () => p.doc.querySelector("#mdExtras .asks .run");
    check("the question stays up while it's asked, saying so",
          run() && run().textContent === "Installing..." && run().disabled, run() && run().textContent);
    p.say({ type: "mods", job: "install", phase: "build", name: "HD Models Plus" }); await wait(40);
    check("the line says it's building", /Building HD Models Plus/.test(p.note()), p.note());
    p.say({ type: "mods", job: "install", phase: "build", name: "HD Models Plus", done: 0, of: 100 });
    await wait(20);
    check("from 0%", p.note() === "Building HD Models Plus... 0%", p.note());
    const building = p.doc.querySelector("#mdNote .ph");
    p.say({ type: "mods", job: "install", phase: "build", name: "HD Models Plus", done: 27, of: 100 });
    await wait(20);
    check("how far the build has got, ticking over in place",
          p.note() === "Building HD Models Plus... 27%" && p.doc.querySelector("#mdNote .ph") === building
          && p.spinning(), p.note());
    p.say({ type: "mods", job: "install", phase: "install", name: "HD Models Plus", done: 0, of: 100 });
    await wait(20);
    check("then the install of what it built, a step of its own",
          p.note() === "Installing HD Models Plus... 0%", p.note());
    b.settle({ ok: true, kind: "files", modName: "HD Models Plus", title: "HD Models Plus",
               summary: "Installed HD Models Plus.", whole: true,
               message: "Built its Ultimate models for all six with its own tool." });
    await wait(60);
    check("then that it's in", /Installed HD Models Plus\./.test(p.note()), p.note());
    check("and the question's gone once it's answered", !p.doc.querySelector("#mdExtras .asks"),
          p.doc.querySelector("#mdExtras").textContent);
    p.w.close();
  }
  {
    // turned away, by an HD install going or a mod still going in, the
    // question stays to be pressed again rather than the download dropped again
    const p = boot(); await p.loaded; await p.on("ff13");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\HD Models Plus.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "HD Models Plus", title: "HD Models Plus",
               jobId: "hdmodelsplus", recommended: "Ultimate",
               versions: ["Ultimate", "Ultimate, HQ Hair", "High Quality"], about: {} });
    await wait(60);
    const row = () => p.doc.querySelector("#mdExtras .mod.asks");
    row().querySelectorAll(".choice")[2].click(); await wait(10);
    const b = later(); p.ctl.install.push(b);
    row().querySelector(".run").click(); await wait(20);
    b.settle({ ok: false, error: "an HD install is running on the archives. Press Install again "
                                 + "once that's finished." });
    await wait(60);
    check("turned away, the question's still up with its pick",
          row() && row().querySelector('.choice[data-on="true"]').dataset.version === "High Quality"
          && row().querySelector(".run").textContent === "Install High Quality" && !row().querySelector(".run").disabled,
          row() && row().textContent);
    const c = later(); p.ctl.install.push(c);
    row().querySelector(".run").click(); await wait(20);
    check("and pressed again, it's asked again for the same one",
          p.calls.install.length === 3 && p.calls.install[2][3] === "High Quality", JSON.stringify(p.calls.install));
    c.settle({ ok: false, busy: true, error: "still installing the last mod. Press Install again once it's in." });
    await wait(60);
    check("a busy answer keeps it up too", !!row() && !row().querySelector(".run").disabled, row() && row().textContent);
    p.w.close();
  }
  {
    // a question on each game: one asked on XIII-2 doesn't take XIII's
    const p = boot(); await p.loaded; await p.on("ff13");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\HD Models Plus.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "HD Models Plus", title: "HD Models Plus",
               jobId: "hdmodelsplus", recommended: "Ultimate",
               versions: ["Ultimate", "Ultimate, HQ Hair", "High Quality"], about: {} });
    await wait(60);
    p.doc.querySelector("#mdExtras .mod.asks").querySelectorAll(".choice")[1].click(); await wait(10);
    await p.on("ff13-2");
    const b = later(); p.ctl.install.push(b);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Serah Hair.zip"] }); await wait(40);
    b.settle({ ok: false, kind: "versions", modName: "Serah Hair", title: "Serah Hair", versions: ["Pink", "Blonde"] });
    await wait(60);
    check("XIII-2 asks its own", /Serah Hair/.test((p.doc.querySelector("#mdExtras .mod.asks") || {}).textContent || ""),
          p.doc.querySelector("#mdExtras").textContent);
    await p.on("ff13");
    const back = p.doc.querySelector("#mdExtras .mod.asks");
    check("and XIII's is still there, with its pick",
          back && /HD Models Plus/.test(back.textContent)
          && back.querySelector('.choice[data-on="true"]').dataset.version === "Ultimate, HQ Hair",
          back && back.textContent);
    p.w.close();
  }
  {
    // the HD Project's options open beside a version question stay open, with
    // a reinstall ticked in them
    const p = boot(); await p.loaded;
    p.ctl.categories = { categories: [{ id: "pcs" }, { id: "weapons" }], selected: [], installed: [] };
    await p.on("ff13");
    const survey = later(); p.ctl.surveys.push(survey);
    p.ctl.identify.push({ kind: "hdproject", jobId: "hdproject",
                          detail: { root: "D:\\mods\\FFXIII HD Project", name: "FFXIII HD Project" } });
    p.say({ type: "dropped", paths: ["D:\\mods\\FFXIII HD Project"] }); await wait(80);
    survey.settle({ sizes: { pcs: 1, weapons: 1 }, applied: ["weapons"], verified: true, suspect: {} });
    await wait(80);
    const hd = () => p.doc.querySelector('#mdExtras .mod[data-job="hdproject"]');
    const weapons = () => p.doc.querySelector('#mdExtras .opt-row[data-cat="weapons"]');
    weapons().click(); await wait(20);
    check("a reinstall ticked", weapons().dataset.fixing === "true", weapons().outerHTML.slice(0, 160));
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\HD Models Plus.zip"] }); await wait(40);
    a.settle({ ok: false, kind: "versions", modName: "HD Models Plus", title: "HD Models Plus",
               jobId: "hdmodelsplus", recommended: "Ultimate",
               versions: ["Ultimate", "Ultimate, HQ Hair", "High Quality"], about: {} });
    await wait(60);
    check("a version question beside it leaves it open", hd() && hd().dataset.open === "true", hd() && hd().dataset.open);
    check("with the reinstall still ticked", weapons() && weapons().dataset.fixing === "true",
          weapons() && weapons().outerHTML.slice(0, 160));
    p.w.close();
  }
  {
    // the HD Project's download landing while a drop goes in: the drop keeps
    // its line and its answer, and the unpack comes after
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Alpha Mod.zip"] }); await wait(40);
    p.ctl.identify.push({ kind: "archive", title: "FFXIII-2 HD" });
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.zip" }); await wait(40);
    p.say({ type: "mods", job: "install", phase: "install", name: "Alpha Mod", done: 50, of: 553 });
    await wait(20);
    check("a download landing mid-install leaves the install its line",
          p.note() === "Installing Alpha Mod... 9%", p.note());
    a.settle({ ok: true, title: "Alpha Mod", summary: "Installed Alpha Mod.", message: "x" });
    await wait(60);
    check("and its answer", p.note() === "Installed Alpha Mod." && p.calls.unpack.length === 1,
          `${p.note()} | unpack ${p.calls.unpack.length}`);
    p.say({ type: "mods", job: "unpack", phase: "unpack", done: 3, of: 100, bytes: true });
    await wait(20);
    check("which stays up to be read before the unpack takes the line", p.note() === "Installed Alpha Mod.",
          p.note());
    p.w.close();
  }
  {
    // a .7z dropped on the drawer unpacks inside install_mod, on its line
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Big Pack.7z"] }); await wait(40);
    p.say({ type: "mods", job: "install", phase: "unpack", name: "Big Pack", done: 5, of: 100,
            bytes: true });
    await wait(20);
    check("a drop's own unpack shows on its line", p.note() === "Unpacking Big Pack... 5%", p.note());
    p.say({ type: "mods", job: "install", phase: "unpack", name: "Big Pack", done: 300 * 1024,
            of: 0, bytes: true });
    await wait(20);
    check("one that won't say what it holds says nothing under a megabyte, not 0 MB",
          p.note() === "Unpacking Big Pack...", p.note());
    p.say({ type: "mods", job: "install", phase: "unpack", name: "Big Pack", done: 340 * 1048576,
            of: 0, bytes: true });
    await wait(20);
    check("and then what's out so far",
          p.note() === "Unpacking Big Pack... 340 MB", p.note());
    a.settle({ ok: false, busy: true, error: "still unpacking the last archive. Drop this one "
                                           + "again once it has finished." });
    await wait(40);
    check("a refusal with nothing of ours unpacking doesn't leave the disc going",
          /^Still unpacking/.test(p.note()) && !p.spinning(), `${p.note()} ${p.spinning()}`);
    p.w.close();
  }
  {
    // a watch that couldn't be started
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.ctl.acquire.push({ ok: false, error: "Couldn't watch Downloads for it: no such folder" });
    p.openPage().click(); await wait(40);
    check("a watch that couldn't start says so and stops the disc",
          /^Couldn't watch Downloads/.test(p.note()) && !p.spinning() && p.calls.pages.length === 1,
          `${p.note()} | pages ${p.calls.pages.length}`);
    p.openPage().click(); await wait(40);
    check("and Open page can try again", p.calls.acquire.length === 2, p.calls.acquire.length);
    p.w.close();
  }
  {
    // the read of the game for a drop that's been replaced since
    const p = boot(); await p.loaded;
    p.ctl.categories = { categories: [{ id: "pcs" }, { id: "npcs" }], selected: [], installed: [] };
    await p.on("ff13");
    const first = later(), second = later();
    p.ctl.surveys.push(first, second);
    p.ctl.identify.push({ kind: "hdproject", jobId: "hdproject",
                          detail: { root: "D:\\mods\\Old HD", name: "FFXIII HD Project" } });
    p.say({ type: "dropped", paths: ["D:\\mods\\Old HD"] }); await wait(80);
    p.ctl.identify.push({ kind: "hdproject", jobId: "hdproject",
                          detail: { root: "D:\\mods\\New HD", name: "FFXIII HD Project" } });
    p.say({ type: "dropped", paths: ["D:\\mods\\New HD"] }); await wait(80);
    second.settle({ sizes: { pcs: 1, npcs: 1 }, applied: [], verified: true, suspect: {} });
    await wait(60);
    first.settle({ sizes: { pcs: 1, npcs: 1 }, applied: ["pcs", "npcs"], verified: true, suspect: {} });
    await wait(80);
    const row = p.doc.querySelector('#mdExtras .opt-row[data-cat="pcs"]');
    check("an answer about the drop before isn't painted on this one",
          row && row.dataset.applied === "false", row && row.outerHTML.slice(0, 120));
    p.w.close();
  }
  {
    // versions answered after a game switch
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Serah Hair.zip"] }); await wait(40);
    p.w.pick("ff13"); await wait(400); p.w.eval("Mods.open()"); await wait(60);
    a.settle({ ok: false, kind: "versions", modName: "Serah Hair", title: "Serah Hair",
               versions: ["Pink", "Blonde"] });
    await wait(60);
    check("versions for another game's drop aren't offered on this one",
          !p.doc.querySelector("#mdExtras .asks") && /Drop it again on FINAL FANTASY XIII-2/i.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // a download landing during a drop of two files waits for both, and one
    // turned away busy anyway has another go rather than being lost
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    const one = later(), two = later();
    p.ctl.install.push(one, two);
    p.ctl.identify.push({ kind: "file" }, { kind: "file" });
    p.say({ type: "dropped", paths: ["D:\\m\\c108.win32.trb", "D:\\m\\c108.win32.imgb"] });
    await wait(40);
    p.ctl.identify.push({ kind: "archive", title: "FFXIII-2 HD" });
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.zip" }); await wait(40);
    one.settle({ ok: true, title: "c108", summary: "Installed c108.", message: "x" }); await wait(60);
    check("a download waits through a drop of two files", p.calls.unpack.length === 0,
          p.calls.unpack.length);
    p.ctl.unpack.push({ ok: false, busy: true, error: "still installing the last mod. Drop "
                                                     + "this one again once it's in." });
    two.settle({ ok: true, title: "c108", summary: "Installed c108.", message: "x" }); await wait(60);
    check("then goes to be unpacked", p.calls.unpack.length === 1, p.calls.unpack.length);
    await wait(1300);
    check("and turned away busy, has another go", p.calls.unpack.length === 2, p.calls.unpack.length);
    p.w.close();
  }
  {
    // an unpack with no steps to say, a .7z's, after an answer that's just
    // gone up: the line comes back to it once that's been read
    const p = boot(); await p.loaded;
    p.ctl.listed = { ok: true, mods: [{ name: "Old Mod", title: "Old Mod", kind: "addon",
                                       managed: true, files: ["a"], about: "x" }] };
    await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    [...p.doc.querySelectorAll("#mdList .mod")][0].querySelector("button").click(); await wait(20);
    p.ctl.remove.settle({ ok: true, summary: "Removed Old Mod.", message: "x" }); await wait(40);
    p.ctl.identify.push({ kind: "archive", title: "FFXIII-2 HD" });
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.7z" }); await wait(60);
    check("a fresh answer stays up through an arrival", p.note() === "Removed Old Mod.", p.note());
    await wait(6300);
    check("and then the unpack says it's going", p.note() === "Unpacking FFXIII-2 HD..." && p.spinning(),
          `${p.note()} ${p.spinning()}`);
    p.w.close();
  }
  {
    // a second archive dropped while the first unpacks, its busy answer late
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "One HD" });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\One HD.zip"] }); await wait(60);
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "Two HD" });
    let answer;
    p.ctl.unpack.push(new Promise(r => { answer = r; }));
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Two HD.zip"] }); await wait(60);
    p.say({ type: "mods", job: "unpack", phase: "unpack", done: 30, of: 100, bytes: true });
    await wait(20);
    check("the first unpack keeps its own name", p.note() === "Unpacking One HD... 30%", p.note());
    p.say({ type: "dropped", paths: [], from: "D:\\Downloads\\One HD.zip",
            error: "that archive is not the HD Project" }); await wait(40);
    answer({ ok: false, busy: true, error: "still unpacking the last archive. Drop this one again "
                                           + "once it has finished." });
    await wait(40);
    check("a busy answer after the unpack ended doesn't leave the disc going", !p.spinning(),
          `${p.note()} ${p.spinning()}`);
    p.w.close();
  }
  {
    // the backend's reasons start big, a file's name keeps its own
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.w.eval('Mods.fail("the file browser could not be opened: no display")');
    check("a reason starts with a capital", /^The file browser/.test(p.note()), p.note());
    p.w.eval('Mods.fail("my mod.7z: that archive is not the HD Project")');
    check("a file's name doesn't", /^my mod\.7z: /.test(p.note()), p.note());
    p.w.close();
  }
  const BUSY = { ok: false, busy: true, error: "still unpacking the last archive. Drop this one "
                                               + "again once it has finished." };
  {
    // a download whose unpack the backend keeps turning away: asked again,
    // never opening the drawer back up over what's been opened since
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    for (let i = 0; i < 10; i++){
      p.ctl.unpack.push(BUSY);
      p.ctl.identify.push({ kind: "archive", title: "FFXIII-2 HD" });
    }
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.7z" }); await wait(200);
    p.w.eval("Mods.close()");
    await wait(2500);
    check("a download turned away busy is asked about again", p.calls.unpack.length >= 2,
          p.calls.unpack.length);
    check("without the drawer coming back", !p.w.eval("Mods.isOpen()"));
    p.w.close();
  }
  {
    // what one install held back isn't said over the next one's line
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(), c = later(); p.ctl.install.push(a, c);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Alpha Mod.zip"] }); await wait(40);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Bravo Mod.zip"] }); await wait(40);
    p.say({ type: "mods", job: "install", phase: "install", name: "Alpha Mod", done: 300, of: 553 });
    await wait(20);
    a.settle({ ok: true, title: "Alpha Mod", summary: "Installed Alpha Mod.", message: "x" });
    await wait(40);
    const t0 = Date.now();
    await wait(3000);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Charlie Mod.zip"] }); await wait(60);
    await wait(Math.max(0, 6300 - (Date.now() - t0)));
    check("an install's held back step doesn't land on the next install's line",
          /Charlie/.test(p.note()), p.note());
    p.w.close();
  }
  {
    // a download's read, held back, doesn't land on an unpack that started meanwhile
    const p = boot(); await p.loaded;
    p.ctl.listed = { ok: true, mods: [{ name: "Old Mod", title: "Old Mod", kind: "addon",
                                       managed: true, files: ["a"], about: "x" }] };
    await p.on("ff13-2");
    p.openPage().click(); await wait(20);
    [...p.doc.querySelectorAll("#mdList .mod")][0].querySelector("button").click(); await wait(20);
    p.ctl.remove.settle({ ok: true, summary: "Removed Old Mod.", message: "x" }); await wait(40);
    const t0 = Date.now();
    const read = later(); p.ctl.identify.push(read.promise);
    p.acq({ type: "acquire", stage: "arrived", jobId: "hdproject2",
            path: "C:\\Users\\me\\Downloads\\FFXIII-2 HD.7z" }); await wait(60);
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "One HD" });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\One HD.zip"] }); await wait(60);
    read.settle({ kind: "archive", title: "FFXIII-2 HD" }); await wait(40);
    await wait(Math.max(0, 6300 - (Date.now() - t0)));
    check("a download's held back read doesn't land on another unpack", /One HD/.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // nor one unpack's held back step on the next unpack's line
    const p = boot(); await p.loaded; await p.on("ff13-2");
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "One HD" });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\One HD.zip"] }); await wait(60);
    const c = later(); p.ctl.install.push(c);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Charlie Mod.zip"] }); await wait(40);
    c.settle(BUSY); await wait(40);
    p.say({ type: "mods", job: "unpack", phase: "unpack", done: 70, of: 100, bytes: true });
    await wait(20);
    p.say({ type: "dropped", paths: [], from: "D:\\Downloads\\One HD.zip",
            error: "that archive is not the HD Project" }); await wait(40);
    const t0 = Date.now();
    await wait(2000);
    p.ctl.identify.push({ kind: "maybe-hdproject", title: "Two HD" });
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Two HD.zip"] }); await wait(60);
    await wait(Math.max(0, 6300 - (Date.now() - t0)));
    check("an unpack's held back step doesn't land on the next unpack's line", /Two HD/.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // LR's DLC restoration from Worth getting: Open page watches Downloads,
    // and what lands goes in like a drop, not through an HD Project's door
    const p = boot(); await p.loaded; await p.on("lr");
    const row = [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    check("LR's DLC restoration is under Worth getting", !!row);
    if (row){
      row.querySelector("button").click(); await wait(20);
      const asked = p.calls.acquire[0] || [];
      check("Open page watches Downloads for it, through the drawer",
            asked[0] === "dlcrestore" && asked[2] === true, JSON.stringify(asked));
      const a = later(); p.ctl.install.push(a);
      p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore",
              path: "C:\\Users\\me\\Downloads\\Aerith Garb and Outerworld Addons-23-1-1-1700000000.zip" });
      await wait(80);
      check("and what lands goes in like any drop",
            p.calls.install.length === 1 && /Outerworld/.test(p.calls.install[0][1]) && p.calls.unpack.length === 0,
            JSON.stringify(p.calls.install));
      a.settle({ ok: true, title: "Aerith Garb and Outerworld Addons", summary: "Installed Aerith Garb and Outerworld Addons.", message: "x" });
      await wait(40);
      check("and says so", /Installed Aerith Garb/.test(p.note()), p.note());
    }
    p.w.close();
  }
  {
    // one an earlier Apply put in reads as in, with no page to get it again,
    // whatever the drawer's own count of its two files says
    const p = boot();
    p.ctl.applied["lr"] = ["dlcrestore"];
    p.ctl.listed = { ok: true, path: "addons", mods: [], parts: { dlcrestore: [0, 2] } };
    await p.loaded; await p.on("lr");
    const row = [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    check("an LR DLC an older Apply put in reads as in", row && isIn(row)
          && !row.querySelector("button"), row && row.textContent);
    check("and still says what it is", row && /Aerith's garb/.test(row.querySelector(".n span").textContent),
          row && row.textContent);
    p.w.close();
  }
  {
    // LR's DLC landing while another game is up waits for LR, rather than
    // going into whichever game the drawer happens to be on
    const p = boot(); await p.loaded; await p.on("lr");
    const row = [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    row.querySelector("button").click(); await wait(20);
    p.w.pick("ff13"); await wait(500);
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore",
            path: "C:\\Users\\me\\Downloads\\Outerworld Addons-23-1-0.zip" });
    await wait(80);
    check("a download for LR doesn't go into XIII", p.calls.install.length === 0,
          JSON.stringify(p.calls.install));
    check("it says where it's going", /goes into Lightning Returns/.test(p.note()), p.note());
    const a = later(); p.ctl.install.push(a);
    p.w.eval("back()"); await wait(100);
    p.w.pick("lr"); await wait(600);
    check("and goes into LR once LR is up", p.calls.install.length === 1
          && p.calls.install[0][0] === "lr" && /Outerworld/.test(p.calls.install[0][1]),
          JSON.stringify(p.calls.install));
    a.settle({ ok: true, title: "Outerworld Addons", summary: "Installed Outerworld Addons.", message: "x" });
    await wait(40);
    p.w.close();
  }
  {
    // HD Models Plus from XIII's Worth getting: Open page watches Downloads
    // through the drawer, what lands asks for a version the way its drop
    // does, and once its build is in the row says so until Remove
    const p = boot(); await p.loaded; await p.on("ff13");
    const row = () => [...p.doc.querySelectorAll("#mdExtras .mod")]
      .find(r => /HD Models Plus/.test(r.textContent));
    check("HD Models Plus is under XIII's Worth getting, after the HD Project",
          row() && /HD Project/.test(row().previousElementSibling.textContent), row() && row().textContent);
    if (row()){
      row().querySelector("button").click(); await wait(20);
      const asked = p.calls.acquire[0] || [];
      check("its Open page watches Downloads for it, through the drawer",
            asked[0] === "hdmodelsplus" && asked[2] === true
            && asked[1] === "https://www.nexusmods.com/finalfantasy13/mods/11", JSON.stringify(asked));
      const a = later(); p.ctl.install.push(a);
      p.acq({ type: "acquire", stage: "arrived", jobId: "hdmodelsplus",
              path: "C:\\Users\\me\\Downloads\\FF_XIII_HD_Models_Plus-11-4-1-7-1649368856.zip" });
      await wait(80);
      check("what lands goes in like a drop", p.calls.install.length === 1
            && /Models_Plus/.test(p.calls.install[0][1]) && p.calls.unpack.length === 0,
            JSON.stringify(p.calls.install));
      a.settle({ ok: false, kind: "versions", modName: "HD Models Plus", title: "HD Models Plus",
                 jobId: "hdmodelsplus", recommended: "Ultimate",
                 versions: ["Ultimate", "Ultimate, HQ Hair", "High Quality"],
                 about: { "Ultimate": "The most detailed: rounder arms, legs and fingers." } });
      await wait(40);
      const picks = [...row().querySelectorAll(".choice")];
      check("and asks which version on its row, saying what each is", picks.length === 3
            && /rounder arms/.test(picks[0].textContent), picks.map(r => r.textContent).join(" | "));
      const b = later(); p.ctl.install.push(b);
      p.ctl.listed = { ok: true, path: "addons", mods: [{
        name: "HD Models Plus", title: "HD Models Plus", kind: "files", jobId: "hdmodelsplus",
        managed: true, files: ["x"], about: "Ultimate, 12 game files in both voice sets" }] };
      if (picks[2]){ picks[2].click(); await wait(10); row().querySelector(".run").click(); }
      await wait(20);
      check("the version picked is the one asked for",
            p.calls.install[1] && p.calls.install[1][3] === "High Quality", JSON.stringify(p.calls.install[1]));
      b.settle({ ok: true, kind: "files", title: "HD Models Plus", summary: "Installed HD Models Plus.",
                 message: "x" });
      await wait(60);
      check("once its build is in, its row says so and offers no page",
            isIn(row()) && !row().querySelector("button"), row().textContent);
      const mod = [...p.doc.querySelectorAll("#mdList .mod")].find(r => /HD Models Plus/.test(r.textContent));
      if (mod){
        mod.querySelector("button").click(); await wait(20);
        p.ctl.listed = { ok: true, path: "addons", mods: [] };
        p.ctl.remove.settle({ ok: true, summary: "Removed HD Models Plus.", message: "x" });
        await wait(60);
      }
      check("and after its Remove the page is offered again",
            !isIn(row()) && !!row().querySelector("button"), row().textContent);
    }
    p.w.close();
  }
  {
    // HD Fonts and GUI on every game's Worth getting: Open page goes to its
    // files, since the one its page recommends is a Nova package, watches
    // Downloads for it, what lands goes in like a drop, and once it's in beside
    // the exe the row says so
    const pages = { "ff13": "https://www.nexusmods.com/finalfantasy13/mods/67?tab=files",
                    "ff13-2": "https://www.nexusmods.com/finalfantasyxiii2/mods/40?tab=files",
                    "lr": "https://www.nexusmods.com/lightningreturnsfinalfantasy13/mods/33?tab=files" };
    for (const [game, page] of Object.entries(pages)){
      const p = boot(); await p.loaded; await p.on(game);
      const row = () => [...p.doc.querySelectorAll("#mdExtras .mod")]
        .find(r => /HD Fonts and GUI/.test(r.textContent));
      check(`${game}: HD Fonts and GUI is under Worth getting, last`,
            row() && !row().nextElementSibling, row() && row().textContent);
      if (!row()){ p.w.close(); continue; }
      check(`${game}: its row says what it is, not how to get it`,
            /4K/.test(row().querySelector(".n span").textContent)
              && !/Open the page|download/i.test(row().querySelector(".n span").textContent),
            row().textContent);
      row().querySelector("button").click(); await wait(20);
      check(`${game}: Open page says which of the page's files to get`,
            new RegExp(`Download the ${game === "lr" ? "Manual Install" : "Standalone Version"}`)
              .test(p.note()), p.note());
      const asked = p.calls.acquire[0] || [];
      check(`${game}: its Open page watches Downloads for it`,
            asked[0] === "hdfonts" && asked[1] === page && asked[2] === true, JSON.stringify(asked));
      const a = later(); p.ctl.install.push(a);
      p.acq({ type: "acquire", stage: "arrived", jobId: "hdfonts",
              path: "C:\\Users\\me\\Downloads\\4K HD Fonts and GUI-67-4-1-1752868320.zip" });
      await wait(80);
      check(`${game}: what lands goes in like a drop`,
            p.calls.install.length === 1 && /HD Fonts/.test(p.calls.install[0][1]),
            JSON.stringify(p.calls.install));
      p.ctl.listed = { ok: true, path: "addons", mods: [{
        name: "4K HD Fonts and GUI", title: "4K HD Fonts and GUI", kind: "loader", jobId: "hdfonts",
        managed: true, files: ["hd_textures/", "version.dll"], about: "Loads as version.dll" }] };
      a.settle({ ok: true, kind: "loader", title: "4K HD Fonts and GUI",
                 summary: "Installed 4K HD Fonts and GUI.", message: "x" });
      await wait(60);
      check(`${game}: once it's in beside the exe, its row says so and offers no page`,
            isIn(row()) && !row().querySelector("button"), row().textContent);
      p.w.close();
    }
  }
  {
    // LR's DLC restoration is two files off one page: the watch goes on past
    // the first, each goes in as it lands, the line says which one is coming
    // and how far along when that's known, and the row says how many are in
    const p = boot(); await p.loaded; await p.on("lr");
    const row = () => [...p.doc.querySelectorAll("#mdExtras .mod")]
      .find(r => /DLC Restoration/.test(r.textContent));
    const aerith = "Aerith_Garb_Restauration-23-1-1-1759690588.zip";
    const outer = "FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip";
    row().querySelector("button").click(); await wait(20);
    check("Open page says to get both of its files", /Download both main files/.test(p.note()), p.note());
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 10646736,
            total: 21293472, file: aerith });
    await wait(20);
    check("a download whose size is known says how far along it is, and which one it is",
          p.note() === "Downloading Aerith Garb Restauration... 50%", p.note());
    p.acq({ type: "acquire", stage: "held", jobId: "dlcrestore", bytes: 21293472, total: 21293472,
            file: aerith });
    await wait(20);
    check("one the browser sits on says to choose Keep",
          /^Your browser is holding Aerith Garb Restauration/.test(p.note())
          && /choose Keep/.test(p.note()) && !p.spinning(), p.note());
    const a = later(); p.ctl.install.push(a);
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 1,
            path: `C:\\Users\\me\\Downloads\\${aerith}` });
    await wait(80);
    check("the first goes in as it lands", p.calls.install.length === 1
          && p.calls.install[0][1].endsWith(aerith), JSON.stringify(p.calls.install));
    const one = { name: "Aerith Garb Restauration", title: "Aerith Garb Restauration", kind: "files",
                  jobId: "dlcrestore", recipe: "LRAerithGrabRestoration.exe", managed: true,
                  files: ["x"], about: "wdbpack.bin in both voice sets, and 4 DLC files" };
    p.ctl.listed = { ok: true, path: "addons", parts: { dlcrestore: [1, 2] }, mods: [one] };
    a.settle({ ok: true, kind: "files", title: "Aerith Garb Restauration",
               summary: "Installed Aerith Garb Restauration.", message: "x" });
    await wait(60);
    check("with one of the two in, the row says so and still offers the page",
          /1 of 2 in/.test(row().textContent) && !!row().querySelector("button") && !isIn(row()),
          row().textContent);
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 4096, file: outer });
    await wait(20);
    check("the first's answer stays up to be read", /Installed Aerith Garb Restauration/.test(p.note()),
          p.note());
    await wait(6200);
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 9000, file: outer });
    await wait(20);
    check("and the watch is still on for the other", /Downloading Outerworld Addons/.test(p.note()),
          p.note());
    const b = later(); p.ctl.install.push(b);
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 0,
            path: `C:\\Users\\me\\Downloads\\${outer}` });
    await wait(80);
    check("which goes in off the same watch", p.calls.install.length === 2
          && p.calls.install[1][1].endsWith(outer), JSON.stringify(p.calls.install));
    p.ctl.listed = { ok: true, path: "addons", parts: { dlcrestore: [2, 2] }, mods: [one, {
      ...one, name: "Outerworld Addons", title: "Outerworld Addons", recipe: "LROuterworldAddons.exe",
      about: "wdbpack.bin in both voice sets" }] };
    b.settle({ ok: true, kind: "files", title: "Outerworld Addons",
               summary: "Installed Outerworld Addons.", message: "x" });
    await wait(60);
    check("with both in, the row's in", isIn(row()) && !row().querySelector("button"),
          row().textContent);
    p.w.close();
  }
  {
    // a "choose Keep" that comes while a drop's going in waits for the line,
    // rather than going and leaving the count it stopped on to come back
    const p = boot(); await p.loaded; await p.on("lr");
    const row = () => [...p.doc.querySelectorAll("#mdExtras .mod")]
      .find(r => /DLC Restoration/.test(r.textContent));
    row().querySelector("button").click(); await wait(20);
    const a = later(); p.ctl.install.push(a);
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 1,
            path: "C:\\Users\\me\\Downloads\\Aerith_Garb_Restauration-23-1-1-1759690588.zip" });
    await wait(60);
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 68648 });
    p.acq({ type: "acquire", stage: "held", jobId: "dlcrestore", bytes: 68648 });
    await wait(20);
    check("a hold while a drop goes in leaves that drop's line be", /Aerith/.test(p.note()), p.note());
    a.settle({ ok: true, kind: "files", title: "Aerith Garb Restauration",
               summary: "Installed Aerith Garb Restauration.", message: "x" });
    await wait(60);
    check("whose answer is up to be read first", /^Installed Aerith Garb/.test(p.note()), p.note());
    await wait(6300);
    check("then the hold is said, not the count it stopped on",
          /^Your browser is holding DLC Restoration/.test(p.note()) && /choose Keep/.test(p.note()),
          p.note());
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 70000 });
    await wait(20);
    check("and a download under a megabyte counts in KB, not 0 MB",
          p.note() === "Downloading DLC Restoration... 68 KB", p.note());
    p.w.close();
  }
  {
    // both of a row's files landing together go in one after the other: the
    // second was turned away as "still installing", with nobody to drop it again
    const p = boot(); await p.loaded; await p.on("lr");
    const row = [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    row.querySelector("button").click(); await wait(20);
    const a = later(), b = later(); p.ctl.install.push(a, b);
    const aerith = "C:\\D\\Aerith_Garb_Restauration-23-1-1-1759690588.zip";
    const outer = "C:\\D\\FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip";
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 1, path: aerith });
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 0, path: outer });
    await wait(80);
    check("two landing at once go in one at a time",
          p.calls.install.length === 1 && !/Still installing/.test(p.note()), p.note());
    a.settle({ ok: true, kind: "files", title: "Aerith Garb Restauration",
               summary: "Installed Aerith Garb Restauration.", message: "x" });
    await wait(80);
    check("the second as soon as the first is in", p.calls.install.length === 2
          && p.calls.install[1][1] === outer, JSON.stringify(p.calls.install));
    b.settle({ ok: true, kind: "files", title: "Outerworld Addons",
               summary: "Installed Outerworld Addons.", message: "x" });
    await wait(40);
    check("and says so", /^Installed Outerworld Addons/.test(p.note()), p.note());
    p.w.close();
  }
  {
    // both landing while another game is up wait for LR, then go one at a time
    const p = boot(); await p.loaded; await p.on("lr");
    const row = [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    row.querySelector("button").click(); await wait(20);
    p.w.pick("ff13"); await wait(500);
    const aerith = "C:\\D\\Aerith_Garb_Restauration-23-1-1-1759690588.zip";
    const outer = "C:\\D\\FFXIII-LR_-_Outerworld_Addons-23-1-1-1759689437.zip";
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 1, path: aerith });
    p.acq({ type: "acquire", stage: "arrived", jobId: "dlcrestore", left: 0, path: outer });
    await wait(80);
    const a = later(), b = later(); p.ctl.install.push(a, b);
    p.w.eval("back()"); await wait(100);
    p.w.pick("lr"); await wait(600);
    check("two waiting for LR go in one at a time once it's up", p.calls.install.length === 1
          && !/Still installing/.test(p.note()), `${p.calls.install.length} ${p.note()}`);
    a.settle({ ok: true, kind: "files", title: "Aerith Garb Restauration",
               summary: "Installed Aerith Garb Restauration.", message: "x" });
    await wait(80);
    check("the second after the first", p.calls.install.length === 2
          && p.calls.install[1][1] === outer, JSON.stringify(p.calls.install));
    b.settle({ ok: true, kind: "files", title: "Outerworld Addons", summary: "x", message: "x" });
    await wait(40);
    p.w.close();
  }
  {
    // XIII-2's DLC Restoration and LR's are both dlcrestore, and each game's
    // Open page watches for its own
    const p = boot(); await p.loaded; await p.on("lr");
    const dlc = () => [...p.doc.querySelectorAll("#mdExtras .mod")].find(r => /DLC Restoration/.test(r.textContent));
    dlc().querySelector("button").click(); await wait(20);
    await p.on("ff13-2");
    dlc().querySelector("button").click(); await wait(20);
    check("each game's Open page starts a watch of its own",
          JSON.stringify(p.calls.acquire.map(a => [a[0], a[3]]))
          === JSON.stringify([["dlcrestore", "lr"], ["dlcrestore", "ff13-2"]]),
          JSON.stringify(p.calls.acquire));
    p.acq({ type: "acquire", stage: "downloading", jobId: "dlcrestore", bytes: 3 * 1048576,
            file: "FFXIII-2_-_Console_Content_Patch_v2.3.7z" });
    await wait(20);
    check("and says how its own is going", /^Downloading .*Console Content Patch.*3 MB$/.test(p.note()),
          p.note());
    p.w.close();
  }
  {
    // the drop target, Worth getting and Installed scroll as one: two lists
    // that each scrolled on their own were two wheels in one drawer
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const body = p.doc.getElementById("mdBody");
    check("the drawer's body holds the drop target and both lists",
          !!body && ["mdZone", "mdExtras", "mdList"].every(id => body.contains(p.doc.getElementById(id))),
          body && body.innerHTML.slice(0, 80));
    const overflow = id => p.w.getComputedStyle(p.doc.getElementById(id)).overflowY;
    check("and it's the only one of them that scrolls",
          overflow("mdBody") === "auto" && overflow("mdExtras") !== "auto" && overflow("mdList") !== "auto",
          ["mdBody", "mdExtras", "mdList"].map(overflow).join(" "));
    p.w.close();
  }
  {
    // XIII's own shader, ticked and in: Installed lists it, and its Remove
    // unticks the row so the next Apply doesn't put it straight back
    const p = boot();
    p.ctl.applied["ff13"] = ["reshade"];
    p.ctl.listed = { ok: true, path: "addons", mods: [{
      name: "Fabula-Lous Shader", title: "Fabula-Lous Shader", kind: "patch", jobId: "reshade",
      managed: true, files: ["ReShade/d3d9.dll"], about: "ReShade preset, Home opens its menu" }] };
    await p.loaded; await p.on("ff13");
    const opt = () => p.doc.querySelector('#optional .row[data-id="reshade"]');
    opt().click(); await wait(20);
    check("the shader's row ticks", opt().dataset.on === "true" && opt().dataset.settled === "1",
          `${opt().dataset.on} ${opt().dataset.settled}`);
    const mod = [...p.doc.querySelectorAll("#mdList .mod")].find(r => /Fabula-Lous/.test(r.textContent));
    check("Installed lists it by its name alone",
          mod && mod.querySelector("b").textContent === "Fabula-Lous Shader"
          && !mod.querySelector(".n span"), mod && mod.textContent);
    if (mod){
      mod.querySelector("button").click(); await wait(20);
      check("Remove asks for it as Apply's own", JSON.stringify(p.calls.remove[0])
            === JSON.stringify(["ff13", "Fabula-Lous Shader", "patch"]), JSON.stringify(p.calls.remove[0]));
      p.ctl.listed = { ok: true, path: "addons", mods: [] };
      p.ctl.remove.settle({ ok: true, summary: "Removed Fabula-Lous Shader.",
                            message: "Every file Fabula-Lous Shader put in is gone." });
      await wait(40);
      check("and once it's off, its row is unticked and not in",
            opt().dataset.on === "false" && opt().dataset.settled === "0",
            `${opt().dataset.on} ${opt().dataset.settled}`);
      check("the gauge drops the segment it had", p.doc.querySelectorAll("#gauge .seg").length
            === p.w.eval("jobs().length"), String(p.doc.querySelectorAll("#gauge .seg").length));
    }
    p.w.close();
  }
  {
    // in, but not all of it
    const p = boot(); await p.loaded; await p.on("ff13-2");
    const a = later(); p.ctl.install.push(a);
    p.say({ type: "dropped", paths: ["D:\\Downloads\\Half Mod.zip"] }); await wait(40);
    a.settle({ ok: true, whole: false, title: "Half Mod",
               summary: "Installed Half Mod. Some of it didn't go in.", message: "x" });
    await wait(40);
    check("an install that's missing a part gets no tick", p.kind() === "bad"
          && p.note() === "Installed Half Mod. Some of it didn't go in.", `${p.kind()} ${p.note()}`);
    p.w.close();
  }
}
