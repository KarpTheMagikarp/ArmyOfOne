/* Proves the ATB gauge only ever moves forward during a run.

   It used to lurch. jobEnd() filled a whole segment for every outcome, held
   it, and then gave the segment back for anything that was not a success, so
   a run with four skipped rows played four forward-and-back thrusts. The .12s
   width transition in the stylesheet animated the retreat rather than hiding
   it, which is what made it read as the bar fighting itself.

   Measured off the rendered widths rather than the counters: `let` bindings in
   a classic script are not window properties, and the widths are what a person
   actually sees.

     npm install jsdom && node tools/gauge_probe.js

   Exits non-zero if the total fill ever decreases. */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "ui", "index.html"), "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true,
                              url: "http://localhost/" });
const { window } = dom;

// the outcomes from a real FFXIII run, which is mostly NOT successes. that mix
// is the point: a run of six applied patches never showed the bug.
const OUTCOMES = [
  ["install_ff13fix", false, "skipped"],
  ["install_novafix", true,  "applied"],
  ["exe_redirect",    false, "failed"],
  ["hd_texture_pack", false, "skipped"],
  ["hd_models_plus",  false, "skipped"],
  ["flip_laa_flag",   true,  "applied"],
];

window.addEventListener("load", () => setTimeout(async () => {
  const w = window, doc = w.document;
  const gauge = doc.querySelector("#gauge");

  gauge.innerHTML = "";
  for (let i = 0; i < OUTCOMES.length; i++) {
    const seg = doc.createElement("span");
    seg.className = "seg";
    seg.innerHTML = '<span class="fill"></span>';
    gauge.appendChild(seg);
  }

  // the fill slides in from the left: -100% is empty, 0% is full
  const part = f => 1 + +((/translateX\((?:calc\()?(-?[\d.]+)%/.exec(f.style.transform || "") || [0, -100])[1]) / 100;
  const total = () => [...gauge.querySelectorAll(".seg .fill")]
    .reduce((sum, f) => sum + part(f), 0);

  const trace = [];
  new w.MutationObserver(() => trace.push(+total().toFixed(4)))
    .observe(gauge, { subtree: true, attributes: true, attributeFilter: ["style"] });

  for (const [id, ok, status] of OUTCOMES) {
    const el = doc.createElement("div");
    el.innerHTML = '<span class="bar"></span><span class="chip"></span>';
    el.dataset.state = "idle";
    await w.jobEnd({ id, name: id, el }, ok, status, "");
  }
  await new Promise(r => setTimeout(r, 50));

  const seen = trace.filter((v, i) => i === 0 || v !== trace[i - 1]);
  const retreats = [];
  for (let i = 1; i < seen.length; i++) {
    if (seen[i] - seen[i - 1] < -0.0001) retreats.push(`${seen[i - 1]} -> ${seen[i]}`);
  }

  console.log("fill, in segments:", seen.join("  "));
  console.log("ends at          :", seen[seen.length - 1], "of", OUTCOMES.length);

  if (retreats.length) {
    console.error(`\nthe gauge ran backwards ${retreats.length} time(s): ${retreats.join(", ")}`);
    process.exit(1);
  }
  if (seen[seen.length - 1] !== OUTCOMES.length) {
    console.error("\nevery job was decided, so the bar should have reached full");
    process.exit(1);
  }
  console.log("\nforward only, reaches full");

  // A jump of several, the way the gauge fills when a game's menu opens: one
  // bar sweeping across, each segment starting as the one before it fills,
  // and draining from the right the same way
  const fills = () => [...gauge.querySelectorAll(".seg .fill")];
  const delays = () => fills().map(f => parseFloat(f.style.transitionDelay) || 0);
  w.emptyGauge();
  w.eval("gaugeDone = 3; gaugeLive = 0; renderGauge();");
  const up = delays().slice(0, 3);
  console.log("fill delays      :", up.join("  "));
  if (!(up[0] === 0 && up[1] > up[0] && up[2] > up[1])) {
    console.error("\nthe segments filled at once rather than one after another");
    process.exit(1);
  }
  w.eval("gaugeDone = 0; renderGauge();");
  const down = delays().slice(0, 3);
  console.log("drain delays     :", down.join("  "));
  if (!(down[2] === 0 && down[1] > down[2] && down[0] > down[1])) {
    console.error("\ndraining didn't start from the right");
    process.exit(1);
  }
  console.log("one bar, left to right");

  // a run's start drains it all at once, or its first job fills the left
  // segment while the right ones are still going down
  w.eval("gaugeDone = 3; renderGauge(0);");
  w.eval("resetGauge();");
  const drain = delays().slice(0, 3);
  console.log("reset delays     :", drain.join("  "));
  if (drain.some(d => d !== 0)){
    console.error("\na run's start drained the gauge a segment at a time");
    process.exit(1);
  }

  // a segment never starts ahead of one still going from the render before
  w.emptyGauge();
  w.eval("gaugeDone = 2; renderGauge();");
  w.eval("gaugeDone = 3; renderGauge();");
  const third = delays()[2];
  console.log("held third delay :", third);
  if (!(third >= 200)){
    console.error("\nthe third segment started before the second had filled");
    process.exit(1);
  }

  // ticking a row reshapes the gauge without sweeping what's already full
  w.eval(`target = "ff13"; applied.set("ff13", new Set(jobs().map(j => j.id)));
          gaugeSweep = false; gauge.innerHTML = ""; buildGauge();`);
  const kept = fills().map(part);
  console.log("after a reshape  :", kept.join("  "));
  if (!kept.length || kept.some(x => x !== 1)){
    console.error("\nthe applied segments were emptied and swept back up");
    process.exit(1);
  }
  console.log("a reshape keeps what's full");

  // an empty fill sits past its own glow, so an empty segment has no gold edge
  w.emptyGauge();
  const parked = fills()[0].style.transform;
  console.log("an empty fill    :", parked);
  if (!/calc\(-100% - 8\.00px\)/.test(parked)){
    console.error("\nan empty fill sits where its glow shows");
    process.exit(1);
  }
  process.exit(0);
}, 500));
