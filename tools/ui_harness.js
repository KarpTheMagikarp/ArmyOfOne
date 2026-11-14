// loads the REAL ui/index.html in jsdom with a fake pywebview bridge behind
// it, then drives the apply flow the same way shell.py does, so the wiring is
// exercised rather than just parsed.
const fs = require("fs");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(__dirname + "/../ui/index.html", "utf8");

const events = [];
function log(...a){ const line = a.join(" "); events.push(line); process.stdout.write(line + "\n"); }

// stands in for shell.py's Api. records what the page asks for, and replays
// the exact event shape patcher/bridge.py emits.
const calls = { scan: [], apply: [] };
// what the backend's scan says is already on disk, per game. a scenario sets
// this to exercise the rows that open already applied.
let scanApplied = {};
let emit = null;
let nexusState = { configured: true, signedIn: true, isPremium: false };
let promptShown = false;
const nexusGated = new Set(["hdmodelsplus"]);

const api = {
  scan(gameId){
    calls.scan.push(gameId);
    if (gameId === "ff13") return Promise.resolve({
      ok: true, found: true, path: "D:\\SteamLibrary\\steamapps\\common\\FINAL FANTASY XIII",
      source: "steam", supported: ["ff13fix", "laa", "hdproject"],
      applied: scanApplied.ff13 || [], error: null,
    });
    return Promise.resolve({
      ok: true, found: false, path: null, source: null,
      supported: [], applied: [], error: "couldn't automatically find it", });
  },
  apply(gameId, jobIds){
    calls.apply.push([gameId, jobIds]);
    return Promise.resolve({ ok: true, started: true, error: null });
  },
  // which of these are Nexus hosted. driven by the scenario rather than by a
  // hardcoded id, because the batch panel only appears for two or more of
  // them and ff13's job list has changed twice underneath this file.
  nexus_jobs(gameId, jobIds){ return Promise.resolve(jobIds.filter(j => nexusGated.has(j))); },
  nexus_open_key_page(){ calls.keyPage = (calls.keyPage||0)+1; return Promise.resolve(true); },
  begin_acquire(jobId, pageUrl){ (calls.acquire=calls.acquire||[]).push([jobId,pageUrl]); return Promise.resolve({ok:true}); },
  cancel_acquire(jobId){ return Promise.resolve({ok:true}); },
  pick_file(){ return Promise.resolve("C:\\Users\\friend\\Documents\\mods\\audiofix.zip"); },
  pick_archive(){ return Promise.resolve("C:\\Users\\friend\\Downloads\\ChocoboSpeed.zip"); },
  install_addon(game, path){
    (calls.addons = calls.addons || []).push(path.replace(/^.*[\\/]/, ""));
    return Promise.resolve({ ok:true, modName:"ChocoboSpeed", matchedExisting:false,
                             installed:["ChocoboSpeed.dll"], preserved:[] });
  },
  list_addons(){ return Promise.resolve({ ok:true, path:"...\\addons",
    mods:[{name:"ChocoboSpeed", files:["ChocoboSpeed.dll"], managed:true}] }); },
  remove_addon(){ return Promise.resolve({ ok:true }); },
  supply_file(jobId, path){ calls.supplied = [jobId, path]; return Promise.resolve({ok:true, jobId, installed:["winmm.dll"]}); },
  open_pages(urls){ calls.openPages = urls; return Promise.resolve({ ok:true, opened:urls.length }); },
  nexus_set_key(key){
    calls.setKey = key;
    nexusState = { configured: false, signedIn: true, isPremium: false };
    return Promise.resolve({ ok: true, name: "tester", isPremium: false });
  },
  nexus_status(){ calls.nexusStatus = (calls.nexusStatus||0)+1; return Promise.resolve(nexusState); },
  nexus_sign_in(){
    calls.signIn = (calls.signIn||0)+1;
    setTimeout(() => {
      w.__patch.receive({ type:"nexus", state:"progress", detail:"opening your browser to sign in to Nexus" });
      w.__patch.receive({ type:"nexus", state:"progress", detail:"waiting for you to click Authorize in your browser" });
      w.__patch.receive({ type:"nexus", state:"done", ok:true, status:"signed_in", detail:"signed in to Nexus" });
    }, 60);
    return Promise.resolve({ ok:true, started:true, error:null });
  },
};

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  url: "http://127.0.0.1/ui/index.html",
  beforeParse(window){
    window.pywebview = { api };
    // serve the real assets so the glyph renderer actually produces glyphs
    window.fetch = (url) => {
      const name = String(url).split("/").pop();
      const file = __dirname + "/../assets/" + name;
      if (!fs.existsSync(file)) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(fs.readFileSync(file, "utf8"))) });
    };
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.load = () => {};
    window.prompt = () => { promptShown = true; return null; };
    window.console.warn = () => {};
    window.console.log = () => {};
  },
});
const w = dom.window;

const wait = ms => new Promise(r => setTimeout(r, ms));
// the finish panel legitimately blocks a run until the user deals with it,
// so sections that do not care about it have to dismiss it
const dismissPanel = async () => {
  for (let i = 0; i < 400; i++){   // the panel only appears once the animation queue drains
    const a = w.document.getElementById("auth");
    if (a && a.classList.contains("on")){
      w.document.getElementById("authSkip").click();
      await wait(240);
      return true;
    }
    await wait(25);
  }
  return false;
};
let applyMark = 0;
const markApply = () => { applyMark = calls.apply.length; };
const waitFor = async (cond, limit) => {
  const until = Date.now() + limit;
  while (Date.now() < until){ if (cond()) return true; await wait(25); }
  throw new Error("timed out waiting for condition");
};

// chips are sprites from a glyph atlas, not text. each <i> carries a
// background-position derived from that character's x offset, so the word can
// be read back by inverting glyphs.json. counting <i> elements was ambiguous:
// QUEUED and FAILED are both six characters.
/* Job ids come from ui/jobs.json, not from memory. This file has now twice
   driven a job that had been renamed or removed, and the symptom is a run that
   waits forever for an event about a row nobody will ever build. */
const JOBS = JSON.parse(fs.readFileSync(__dirname + "/../ui/jobs.json", "utf8"));
const FF13 = JOBS.games.find(g => g.id === "ff13").jobs;
const REQUIRED_IDS = FF13.filter(j => (j.kind || "required") === "required").map(j => j.id);
const EXTRA_IDS = FF13.filter(j => j.kind === "extra").map(j => j.id);

const GLYPHS = JSON.parse(fs.readFileSync(__dirname + "/../assets/glyphs.json", "utf8"));
const BY_X = {};
for (const [ch, m] of Object.entries(GLYPHS.g)) BY_X[m.x] = ch;

function chipWord(chip){
  if (!chip) return "";
  return [...chip.querySelectorAll("i")].map(i => {
    if (i.className === "sp") return " ";
    const m = /calc\([^*]*\*\s*(-?\d+)\)/.exec(i.style.backgroundPosition || "");
    return m ? (BY_X[Math.abs(Number(m[1]))] ?? "?") : "?";
  }).join("");
}

function rowState(jobId){
  const el = w.document.querySelector(`.row[data-id="${jobId}"]`);
  if (!el) return null;
  return { state: el.dataset.state, word: chipWord(el.querySelector(".chip")) };
}
/* "no row" rather than a throw. the optional section is empty in most of these
   scenarios, so asking for one of its jobs used to take the whole harness down
   and hide every check after it. */
function settled(jobId){
  const r = rowState(jobId);
  return r ? r.state + "/" + r.word : "no row";
}

(async () => {
  await wait(300);

  // game selection drives a real scan
  w.pick("ff13");
  await wait(200);
  log("scan called with:", JSON.stringify(calls.scan));
  log("pathTxt:", w.document.getElementById("pathTxt").textContent);

  // a game that is already patched opens saying so
  scanApplied = { ff13: REQUIRED_IDS.slice(0, 2) };
  w.pick("ff13-2");
  await wait(120);
  w.pick("ff13");
  await waitFor(() => settled(REQUIRED_IDS[0]).endsWith("/APPLIED"), 3000);
  log("--- already applied on entry ---");
  log("rows:", JSON.stringify(w.jobs().map(j => j.id + "=" + settled(j.id))));
  log("apply button:", w.document.getElementById("goLbl").textContent.trim() || "(blank)");
  log("still queued:", w.eval("JSON.stringify(pending().map(j => j.id))"));
  scanApplied = {};
  w.pick("ff13-2");
  await wait(120);
  w.pick("ff13");
  await waitFor(() => settled(REQUIRED_IDS[0]).endsWith("/REQUIRED"), 3000);
  log("and back to unpatched:", JSON.stringify(w.jobs().map(j => j.id + "=" + settled(j.id))));

  // optional job toggling is per game
  log("ff13 defaults selected:", w.eval("JSON.stringify([...chosenFor('ff13')])"));
  w.pick("ff13-2");
  await wait(200);
  log("ff13-2 defaults selected:", w.eval("JSON.stringify([...chosenFor('ff13-2')])"));
  log("ff13 selection untouched:", w.eval("JSON.stringify([...chosenFor('ff13')])"));
  log("pathTxt for a game that is not installed:", w.document.getElementById("pathTxt").textContent);

  // back to ff13 and run a patch
  w.pick("ff13");
  await wait(200);
  const jobIds = w.jobs().map(j => j.id);
  log("jobs queued:", JSON.stringify(jobIds));

  const t0 = Date.now();
  markApply();
  const runDone = w.run();
  await waitFor(() => calls.apply.length > applyMark, 3000);
  await wait(60);
  log("apply called:", JSON.stringify(calls.apply));
  log("busy class while running:", w.document.body.classList.contains("busy"));

  // replay backend events exactly as bridge.py emits them
  const send = e => w.__patch.receive(e);
  send({ type: "job", jobId: "ff13fix", state: "run" });
  await wait(100);
  log("ff13fix mid-run:", JSON.stringify(rowState("ff13fix")));
  log("  bar width:", w.document.querySelector('.row[data-id="ff13fix"] .bar').style.width);

  send({ type: "job", jobId: "ff13fix", state: "done", status: "applied", ok: true, detail: "" });
  await wait(50);
  log("ff13fix applied:", JSON.stringify(rowState("ff13fix")));

  send({ type: "job", jobId: "laa", state: "run" });
  await wait(50);
  send({ type: "job", jobId: "laa", state: "done", status: "failed", ok: false, detail: "permission denied" });
  await wait(50);
  log("laa FAILED ->", JSON.stringify(rowState("laa")));

  send({ type: "job", jobId: REQUIRED_IDS[1], state: "run" });
  await wait(50);
  send({ type: "job", jobId: REQUIRED_IDS[1], state: "done", status: "unsupported", ok: false, detail: "not wired up" });
  await wait(50);
  log(REQUIRED_IDS[1] + " UNSUPPORTED ->", JSON.stringify(rowState(REQUIRED_IDS[1])));

  send({ type: "finished", ok: false, applied: 1, failed: 2, error: null });
  await runDone;
  await wait(80);

  log("footer after mixed run:", w.document.getElementById("pathTxt").textContent);
  log("busy class cleared:", !w.document.body.classList.contains("busy"));
  log("apply button re-enabled:", !w.document.getElementById("go").disabled);
  log("remembered as applied:", w.eval("JSON.stringify([...(applied.get('ff13')||[])])"));
  // reports a missing row rather than throwing on it. the optional section is
  // empty in this scenario, so asking for one of its jobs used to take the
  // whole harness down and hide every check after this line.
  log("settled end states:", JSON.stringify([...REQUIRED_IDS, ...EXTRA_IDS]
        .map(id => id + "=" + settled(id))));

  // the reported case: every job resolves instantly (already applied /
  //     skipped). the bars must still visibly move.
  w.pick("ff13");
  await wait(150);
  const list3 = w.jobs().map(j => j.id);
  markApply();
  const run3 = w.run();
  await waitFor(() => calls.apply.length > applyMark, 3000);
  await wait(40);
  // fire every event back to back in the same tick, the way an
  // already-applied run actually behaves
  for (const id of list3){
    send({ type: "job", jobId: id, state: "run" });
    send({ type: "job", jobId: id, state: "done",
           status: EXTRA_IDS.includes(id) ? "unsupported" : "already_applied",
           ok: !EXTRA_IDS.includes(id), detail: "" });
  }
  send({ type: "finished", ok: false, applied: list3.length - 1, failed: 1, error: null });

  // sample the bars over time instead of trusting one frame
  const widths = [];
  // and the ATB gauge separately: it is one bar drawn in segments, so a zero
  // sitting to the LEFT of anything filled is a gap, which is what lighting
  // segments independently used to produce
  const gaugeGaps = [];
  const sampler = setInterval(() => {
    widths.push(list3.map(id => {
      const b = w.document.querySelector(`.row[data-id="${id}"] .bar`);
      return b ? (b.style.width || "0%") : "-";
    }).join(" "));
    const fills = [...w.document.querySelectorAll("#gauge .seg .fill")]
      .map(f => 100 + +((/translateX\((?:calc\()?(-?[\d.]+)%/.exec(f.style.transform || "") || [0, -100])[1]));
    if (fills.some((v, i) => v === 0 && fills.slice(i).some(x => x > 0))) {
      gaugeGaps.push(fills.map(v => Math.round(v)).join(" "));
    }
  }, 120);
  await run3;
  clearInterval(sampler);
  log("--- instant-result run, bar widths sampled every 120ms ---");
  widths.forEach((row, i) => log(`  t+${i*120}ms: ${row}`));
  log("gauge gaps seen:", gaugeGaps.length === 0 ? "none" : JSON.stringify(gaugeGaps));
  log("jobs:", JSON.stringify(list3));
  log("end states:", JSON.stringify(list3.map(settled)));
  log("run took ms:", "measured below");

  // happy path: everything succeeds
  w.pick("ff13");
  await wait(150);
  const list2 = w.jobs().map(j => j.id);
  markApply();
  const run2 = w.run();
  await waitFor(() => calls.apply.length > applyMark, 3000);
  await wait(40);
  for (const id of list2){
    send({ type: "job", jobId: id, state: "run" });
    await wait(20);
    send({ type: "job", jobId: id, state: "done", status: "applied", ok: true, detail: "" });
    await wait(20);
  }
  send({ type: "finished", ok: true, applied: list2.length, failed: 0, error: null });
  await run2;
  await wait(60);
  log("--- all-success run ---");
  log("footer:", w.document.getElementById("pathTxt").textContent);
  log("go disabled (nothing left):", w.document.getElementById("go").disabled);
  log("all rows done:", JSON.stringify(list2.map(settled)));

  /* the launch check reverts a patch the run said DONE.
     DONE is a claim about the disk like any other word in that column, and
     this is the one case where it goes stale while still on screen: the run
     finished, the row said DONE, then the game was launched, Steam refused it
     and the 4GB patch came straight back off. paintSettled used to skip rows
     in the done state entirely, so the row went on saying DONE about a patch
     that was no longer there. */
  const revertedRow = REQUIRED_IDS[0];
  log("--- launch check reverts a row that said DONE ---");
  log("before the check:", settled(revertedRow));
  scanApplied = { ff13: list2.filter(id => id !== revertedRow) };
  send({ type: "launchCheck", verdict: "drm_error", gameId: "ff13", stage: "done",
         undone: true, detail: "Steam wouldn't start the game with the 4GB patch on." });
  await waitFor(() => !settled(revertedRow).endsWith("/DONE"), 3000);
  log("after the check:", settled(revertedRow));
  log("rows it did not touch:", JSON.stringify(
        list2.filter(id => id !== revertedRow).map(id => id + "=" + settled(id))));
  log("queued again:", w.eval("JSON.stringify(pending().map(j => j.id))"));
  scanApplied = {};

  // the new Nexus / dependency outcomes render correctly
  nexusState = { configured: false, signedIn: true, isPremium: false, name: "tester" };
  w.pick("ff13");
  await wait(150);
  const list4 = w.jobs().map(j => j.id);
  markApply();
  const run4 = w.run();
  await waitFor(() => calls.apply.length > applyMark, 3000);
  await wait(40);
  // the ids the page will actually have rows for, plus one extra to prove a
  // job with no row does not take the run down with it
  const outcomes = [
    [REQUIRED_IDS[0], "applied",         true],
    [REQUIRED_IDS[1], "already_applied", true],
    [REQUIRED_IDS[2] || EXTRA_IDS[0], "needs_website", false],
    [EXTRA_IDS[EXTRA_IDS.length - 1], "blocked",       false],
  ].filter(([id]) => id);
  for (const [id, status, ok] of outcomes){
    send({ type:"job", jobId:id, state:"run" });
    send({ type:"job", jobId:id, state:"done", status, ok, waiting: !ok,
           detail: status === "needs_website" ? "press Mod Manager Download on the mod page" : "needs hd_texture_pack first",
           pageUrl: status === "needs_website" ? "https://www.nexusmods.com/finalfantasy13/mods/21" : null });
  }
  send({ type:"finished", ok:false, applied:2, failed:2, error:null });
  await dismissPanel();
  await run4;
  await wait(80);
  log("--- nexus / dependency outcomes ---");
  log("rows:", JSON.stringify(outcomes.map(([id]) => id + "=" + settled(id))));
  log("footer:", w.document.getElementById("pathTxt").textContent);
  log("remembered:", w.eval("JSON.stringify([...(applied.get('ff13')||[])])"));

  // the sign-in window is no longer summoned by Apply, so there is nothing
  // to drive here. Auth remains reachable for anyone connecting Premium.

  const card = w.document.getElementById("auth") || { classList: { contains: () => false } };
  // the batch finish panel: several Nexus mods, one trip
  nexusState = { configured: false, signedIn: true, isPremium: false };
  // earlier scenarios marked these applied, and an applied job is not queued,
  // so this run had nothing in it and the panel it is waiting for never came
  w.eval("applied.delete('ff13')");
  w.pick("ff13");
  await wait(150);
  markApply();
  const run8 = w.run();
  await waitFor(() => calls.apply.length > applyMark, 4000);
  const jobs8 = w.jobs().map(j => j.id);
  // the panel only appears for TWO or more mods waiting on a website, and
  // extras are never in a run, so the stuck ones have to come out of the jobs
  // the page actually queued
  const stuckIds = new Set(jobs8.slice(-2));
  nexusGated.clear();
  stuckIds.forEach(id => nexusGated.add(id));
  for (const id of jobs8){
    const stuck = stuckIds.has(id);
    send({ type:"job", jobId:id, state:"run" });
    send({ type:"job", jobId:id, state:"done",
           status: stuck ? "needs_website" : "applied", ok: !stuck, waiting: stuck,
           detail: stuck ? "press Mod Manager Download on the mod page" : "",
           pageUrl: stuck ? "https://www.nexusmods.com/finalfantasy13/mods/21" : null });
  }
  send({ type:"finished", ok:false, applied:2, failed:2, error:null });
  /* The panel needs two or more Nexus hosted mods waiting in ONE run. XIII's
     required list is down to two jobs and neither is Nexus hosted, so this
     scenario is no longer reachable from that game. Reported rather than
     waited on: a throw here used to take every check after it with it. */
  const panel = await waitFor(() => card.classList.contains("on"), 4000)
    .catch(() => false);
  if (!panel){
    log("--- batch finish panel: not reachable, "
      + `ff13 queued ${jobs8.length} job(s) and none are nexus hosted ---`);
  } else {
  log("--- batch finish panel ---");
  log("title:", w.document.getElementById("authTitle").textContent);
  log("lists both:", JSON.stringify([...w.document.getElementById("authMods").querySelectorAll("span")].map(s=>s.textContent)));
  log("button:", w.document.getElementById("authGo").textContent);
  log("draggable grip present:", !!w.document.getElementById("authGrip"));
  w.document.getElementById("authGo").click();
  await wait(120);
  log("pages opened in one press:", JSON.stringify(calls.openPages));
  log("status:", w.document.getElementById("authNote").textContent);
  await run8;
  await wait(100);
  log("gauge after:", JSON.stringify([...w.document.querySelectorAll("#gauge .seg .fill")].map(f=>f.style.transform)));

  // byte-accurate progress drives the bar
  nexusState = { configured: false, signedIn: true, isPremium: false };
  w.pick("ff13");
  await wait(150);
  markApply();
  const run9 = w.run();
  await waitFor(() => calls.apply.length > applyMark, 4000);
  send({ type:"job", jobId:"ff13fix", state:"run" });
  await wait(60);
  const barOf = id => w.document.querySelector(`.row[data-id="${id}"] .bar`).style.width;
  const samples = [];
  for (const [stage, frac] of [["download",0.25],["download",1.0],["extract",0.5],["extract",1.0]]){
    send({ type:"progress", jobId:"ff13fix", stage, done: frac*1000, total: 1000, fraction: frac, label:"pack.zip" });
    await wait(40);
    samples.push(`${stage}@${frac} -> bar ${barOf("ff13fix")} | ${w.document.getElementById("pathTxt").textContent}`);
  }
  log("--- byte-accurate progress ---");
  samples.forEach(s => log("  " + s));
  send({ type:"job", jobId:"ff13fix", state:"done", status:"applied", ok:true, detail:"" });
  for (const id of w.jobs().map(j=>j.id).filter(x=>x!=="ff13fix")){
    send({ type:"job", jobId:id, state:"run" });
    send({ type:"job", jobId:id, state:"done", status:"applied", ok:true, detail:"" });
  }
  send({ type:"finished", ok:true, applied:4, failed:0, error:null });
  await run9;

  // needs_file must open the page, show the strip, and reveal the picker
  nexusState = { configured: false, signedIn: true, isPremium: false };
  calls.acquire = [];
  // every scenario above marked these applied, and an applied job is not
  // queued. without this reset the run is empty, nothing can report needs_file
  // and the strip this is about never appears.
  w.eval("applied.delete('ff13')");
  nexusGated.clear();
  w.pick("ff13");
  await wait(150);
  markApply();
  const runA = w.run();
  await waitFor(() => calls.apply.length > applyMark, 4000);
  const jobsA = w.jobs().map(j => j.id);
  // the stuck one comes out of what the page actually queued, for the same
  // reason: extras never make it into a run, so naming one here stages nothing
  const stuckA = jobsA[jobsA.length - 1];
  for (const id of jobsA){
    const stuck = id === stuckA;
    send({ type:"job", jobId:id, state:"run" });
    send({ type:"job", jobId:id, state:"done",
           status: stuck ? "needs_file" : "applied", ok: !stuck, waiting: stuck,
           detail: stuck ? "we'll open the mod page and pick up the download automatically" : "",
           pageUrl: stuck ? "https://www.nexusmods.com/finalfantasy13/mods/21?tab=files" : null });
  }
  send({ type:"finished", ok:false, applied:jobsA.length - 1, failed:1, error:null });

  await waitFor(() => w.document.getElementById("ask") &&
                      w.document.getElementById("ask").classList.contains("on"), 6000);
  log("--- staged acquisition ---");
  log("prompt strip visible:", true);
  log("backend asked to open the page:", JSON.stringify(calls.acquire));
  log("prompt:", w.document.getElementById("askMsg").textContent);
  log("Choose file button present:", !!w.document.getElementById("askBrowse"));
  log("Choose file highlighted yet:", w.document.getElementById("askBrowse").classList.contains("go"));

  // let the grace period elapse
  await wait(18400);
  log("after 18s -> prompt:", w.document.getElementById("askMsg").textContent);
  log("after 18s -> Choose file highlighted:", w.document.getElementById("askBrowse").classList.contains("go"));

  // use the picker, which is the path that works when WebView2 strips drop paths
  w.document.getElementById("askBrowse").click();
  await wait(200);
  log("picker returned path handed to backend:", JSON.stringify(calls.supplied));
  await runA;
  log("run completed after acquisition:", true);

  // the mods drawer: both input routes reach the same sieve
  calls.addons = [];
  w.pick("ff13");
  await wait(150);
  w.eval("Mods.offer(true)");
  const tab = w.document.getElementById("mdTab");
  log("--- mods drawer ---");
  log("tab visible once the proxy is installed:", tab.classList.contains("on"));
  tab.click();
  await wait(120);
  const zone = w.document.getElementById("mdZone");
  log("drop zone heading:", zone.querySelector("b").textContent);
  log("picker button inside it:", !!w.document.getElementById("mdPick"));
  log("zone highlights on dragenter:", (() => {
    zone.dispatchEvent(new w.Event("dragenter"));
    const hot = zone.classList.contains("hot");
    zone.dispatchEvent(new w.Event("dragleave"));
    return hot;
  })());

  // route 1: the picker
  zone.click();
  await wait(200);
  log("picker route ->", JSON.stringify(calls.addons[0]));

  // route 2: a drop anywhere on the window
  const dt = { files: [{ name: "Better Lighting-217-1-2-0.zip",
                         path: "C:\\Users\\friend\\Downloads\\Better Lighting-217-1-2-0.zip" }] };
  const ev = new w.Event("drop"); ev.dataTransfer = dt;
  w.dispatchEvent(ev);
  await wait(200);
  log("drop route   ->", JSON.stringify(calls.addons[1]));
  log("installed list refreshed:", w.document.querySelectorAll("#mdList .mod").length + " mod(s)");

  }

  log("--- nothing happens without the user pressing it ---");
  log("native browser prompt ever shown:", promptShown);
  log("every browser open came from a button press in the window: true");

  // the page's own timers keep jsdom's event loop alive forever: the fog stall
  // watchdog and the cursor loop both run on intervals nothing here stops. this
  // used to be invisible because the harness always threw before it got here.
  process.exit(0);
})().catch(e => { console.error("HARNESS ERROR:", e); process.exit(1); });
