/* The HD install's terminal under a real install's stream, in jsdom.

   A tool can print fourteen thousand lines in one step. The pane used to append
   each one on its own and read scrollHeight after it, so every line cost a full
   layout of up to two thousand rows: a third of a minute of renderer time per
   20,000 lines in Chromium. This pins the batching that replaced it, using the
   scrollHeight reads as the stand-in for layouts, since jsdom has no layout.

     npm install jsdom && node tools/term_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync(__dirname + "/../ui/index.html", "utf8");

const api = new Proxy({}, {
  get: (target, key) => key === "then" || typeof key !== "string" ? undefined
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
const results = [];
const check = (name, pass, seen) => results.push([name, !!pass, seen]);

const rows = () => [...doc.querySelectorAll("#termOut .ln")];
const lines = (from, count) => Array.from({ length: count }, (_, i) =>
  [`Extracted white_imgu/chr/c${from + i}.win32.imgb (uncompressed)`, "out"]);

w.addEventListener("load", () => setTimeout(async () => {
  try {
    const out = doc.getElementById("termOut");
    let reads = 0;
    const real = Object.getOwnPropertyDescriptor(w.Element.prototype, "scrollHeight");
    Object.defineProperty(out, "scrollHeight", { configurable: true,
      get(){ reads++; return real.get.call(this); } });
    let added = 0;
    new w.MutationObserver(records => {
      added += records.filter(r => r.addedNodes.length).length;
    }).observe(out, { childList: true });

    w.eval("Term.open('Installing FFXIII-2 HD')");
    reads = 0;
    const burst = lines(0, 5000);
    w.__patch.receive({ type: "batch", events: [
      { type: "hd", stage: "step", label: "unpacking white_imgu", index: 0, total: 2060 },
      { type: "hd", stage: "lines", lines: burst.slice(0, 2500) },
      { type: "hd", stage: "progress", share: 0.5 },
      { type: "hd", stage: "lines", lines: burst.slice(2500) },
    ] });
    await wait(80);
    const shown = rows();
    check("a burst of 5000 lines lands as one append", added === 1, added);
    // where it was before the append, and where the bottom is after it
    check("and the scroll is looked at twice, not twice a line", reads <= 2, reads);
    check("the pane keeps the last 2000", shown.length === 2000, shown.length);
    check("in order, oldest trimmed",
          shown[0].textContent === burst[3000][0] && shown[1999].textContent === burst[4999][0],
          `${shown[0].textContent} .. ${shown[1999].textContent}`);
    check("the step and the bar still moved inside the batch",
          /^1 of 2060/.test(doc.getElementById("termStep").textContent)
            && doc.getElementById("termPct").textContent === "0%",
          doc.getElementById("termStep").textContent + " " + doc.getElementById("termPct").textContent);

    // a quiet stream still shows each line within a frame
    added = 0; reads = 0;
    w.__patch.receive({ type: "hd", stage: "line", text: "solo", stream: "out" });
    await wait(50);
    check("a single line lands within a frame", rows().at(-1).textContent === "solo",
          rows().at(-1).textContent);
    check("still one append for it", added === 1, added);

    // stderr on a collapsed pane marks the header at once, not on the frame a
    // hidden window may never get
    w.eval("Term.minimize()");
    w.__patch.receive({ type: "hd", stage: "lines", lines: [["it broke", "err"]] });
    check("stderr marks a minimized pane straight away",
          doc.getElementById("term").classList.contains("alert"));
    await wait(50);
    check("and the line is red", rows().at(-1).classList.contains("err"));
    w.eval("Term.expand()");

    // lines from the last run never leak into the next one
    w.eval("Term.line('stale one'); Term.line('stale two')");
    w.eval("Term.open('Rebuilding the game archives')");
    await wait(50);
    check("a new run starts clean", rows().length === 0, rows().length);

    // a run's verdict comes after everything it printed, batching or not
    w.__patch.receive({ type: "batch", events: [
      { type: "hd", stage: "lines", lines: lines(0, 3) },
      { type: "hd", stage: "done", steps: 3, applied: 3, skipped: 0, nothing: 0, removed: 0 },
    ] });
    await wait(80);
    check("the verdict lands after the lines", /^Finished\. 3 applied/.test(rows().at(-1).textContent),
          rows().map(r => r.textContent).join(" | "));

    // the voices box ticked with no category: the copy on its own, a run the
    // page starts like any other, titled for where it's going
    w.eval(`Hd.arm({ jobId: "hdproject2", detail: { name: "FFXIII-2 HD", root: "D:\\\\HD" } })`);
    w.eval(`Hd.start("ff13-2", [], null, "add", "Japanese")`);
    await wait(50);
    check("a copy opens the pane as one",
          doc.getElementById("termTitle").textContent
            === "Copying FFXIII-2 HD into the Japanese voice files",
          doc.getElementById("termTitle").textContent);
    check("and says what it carries",
          /^\[plan\] copying what's in these voice files into the Japanese ones/
            .test(rows()[0] && rows()[0].textContent), rows().map(r => r.textContent).join(" | "));
    w.__patch.receive({ type: "hd", stage: "done", log: "",
                        copied: { files: 12, zones: 1, language: "Japanese", built: "" } });
    await wait(80);
    check("it ends on what went across",
          rows().at(-1).textContent === "Finished. 12 character files and 1 zone into the "
                                         + "Japanese voice files.",
          rows().at(-1).textContent);
    check("and lets go of the run", !w.eval("Hd.isRunning()"));

    // what the copy had to leave goes to the mod's tools in the same run
    w.eval(`Hd.start("ff13-2", [], null, "add", "Japanese")`);
    await wait(30);
    w.__patch.receive({ type: "hd", stage: "done", log: "",
                        copied: { files: 0, zones: 1, language: "Japanese",
                                  built: "didn't build, see above." } });
    await wait(80);
    check("a copy whose tools didn't build says the rest didn't",
          rows().at(-1).textContent === "Finished. 1 zone into the Japanese voice files. "
                                         + "The rest didn't build, see above.",
          rows().at(-1).textContent);
    check("and finishes with gaps", doc.getElementById("termTitle").textContent === "Done, with gaps",
          doc.getElementById("termTitle").textContent);

    // an install with the box ticked says so in its plan
    w.eval(`Hd.start("ff13-2", ["weapons"], null, "add", "Japanese")`);
    await wait(30);
    check("an install with the box plans both sets",
          rows()[0].textContent === "[plan] 1 categories from D:\\HD, then into the Japanese "
                                    + "voice files too",
          rows()[0] && rows()[0].textContent);
    w.__patch.receive({ type: "hd", stage: "cancelled", note: "Stopped." });
    await wait(30);
    check("a follow event from an older backend opens nothing",
          (w.__patch.receive({ type: "hd", stage: "follow", carrying: { language: "Japanese" } }),
           !w.eval("Hd.isRunning()")));
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
