/* Every state a row's chip can be in, driven through the page's own code.

   The chip is the only thing telling a user what is on their disk, and it has
   been wrong twice: a required row said REQUIRED about a patch that was
   already applied, and a row left saying DONE went on saying it after the
   launch check undid the patch. Both were one branch, and both read as the
   tool not knowing what it had done.

   So rather than reasoning about the branches, this walks them: build the
   sections, set the state the backend would report, call the page's own
   painter, and read the glyph strip back off the rendered chip.

     npm install jsdom && node tools/chip_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("ui/index.html", "utf8");
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://127.0.0.1/ui/index.html",
  beforeParse(window){
    // the glyph renderer draws nothing without its metrics, and a chip with no
    // sprites in it reads the same as a chip showing the wrong word
    window.fetch = (url) => {
      const file = __dirname + "/../assets/" + String(url).split("/").pop();
      if (!fs.existsSync(file)) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true,
        json: () => Promise.resolve(JSON.parse(fs.readFileSync(file, "utf8"))) });
    };
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.load = () => {};
    window.console.warn = () => {};
  },
});
const { window: w } = dom;

const GLYPHS = JSON.parse(fs.readFileSync(__dirname + "/../assets/glyphs.json", "utf8"));
const BY_X = {};
for (const [ch, m] of Object.entries(GLYPHS.g)) BY_X[m.x] = ch;

// the chip renders as one background-positioned sprite per letter, so the word
// comes back out of the style rather than out of textContent
function chipWord(el){
  const chip = el && el.querySelector(".chip");
  if (!chip) return "no chip";
  return [...chip.children].map(i => {
    const m = /calc\([^*]*\*\s*(-?\d+)\)/.exec(i.style.backgroundPosition || "");
    return m ? (BY_X[Math.abs(Number(m[1]))] ?? "?") : "?";
  }).join("");
}

function row(id){
  const el = w.document.querySelector(`.row[data-id="${id}"]`);
  return el ? { state: el.dataset.state, word: chipWord(el) } : null;
}

w.addEventListener("load", () => setTimeout(async () => {
  const results = [];
  const check = (name, got, want) => results.push([name, got, want, got === want]);

  // XIII-2, whose audio fix is optional and ticked by default. LR's only
  // optional row was the DLC restoration, which is under Worth getting now
  const manifest = JSON.parse(fs.readFileSync("ui/jobs.json", "utf8"));
  const two = manifest.games.find(g => g.id === "ff13-2");
  const required = two.jobs.filter(j => (j.kind || "required") === "required")[0].id;
  const optional = two.jobs.filter(j => j.kind === "optional" && j.default)[0].id;

  // the page keeps "what the scan found on disk" in `applied`, keyed by game,
  // and paintSettled is what turns that into words. drive both directly: the
  // backend half is covered by pytest, this is about the rendering half.
  const paint = (onDisk) => {
    w.eval(`applied.set('ff13-2', new Set(${JSON.stringify(onDisk)}))`);
    w.eval("paintSettled(game())");
  };

  w.pick("ff13-2");
  w.eval("buildSections(game())");

  check("required row, nothing on disk", row(required).word, "REQUIRED");
  check("optional row, ticked, nothing on disk", row(optional).word, "QUEUED");

  paint([required, optional]);
  check("required row, on disk", row(required).word, "APPLIED");
  check("optional row, on disk", row(optional).word, "APPLIED");
  check("required row is idle, not done", row(required).state, "idle");

  // the tick decides what the next run does; the chip says what is on the disk
  // now. an applied row that gets unticked still says APPLIED, because the
  // file has not gone anywhere.
  w.eval(`chosenFor('ff13-2').delete(${JSON.stringify(optional)})`);
  w.eval(`document.querySelector('.row[data-id="${optional}"]').dataset.on = "false"`);
  w.eval("paintSettled(game())");
  check("optional row, on disk, unticked", row(optional).word, "APPLIED");

  paint([]);
  check("optional row, unticked, not on disk", row(optional).word, "SKIPPED");

  // the launch check case. the run said DONE, then Steam refused the game and
  // the backend took the patch back off, so the word has to follow the disk.
  w.eval(`(() => { const li = document.querySelector('.row[data-id="${required}"]');
                   li.dataset.state = "done"; ff13(li.querySelector('.chip'), "DONE"); })()`);
  check("required row after a successful run", row(required).word, "DONE");
  paint([]);
  check("required row after the launch check reverts it", row(required).word, "REQUIRED");

  // a row mid run belongs to the run. a scan landing underneath must not
  // repaint it, or the word flickers back and forth while the job is working.
  w.eval(`(() => { const li = document.querySelector('.row[data-id="${required}"]');
                   li.dataset.state = "run"; ff13(li.querySelector('.chip'), "WORKING"); })()`);
  paint([required]);
  check("row mid run is left to the run", row(required).word, "WORKING");

  // every status the engine can return has a word. a missing one renders an
  // empty chip, which is a row that looks broken rather than one that explains
  // itself.
  const statuses = ["applied", "already_applied", "would_apply", "failed", "skipped",
                    "unsupported", "needs_website", "needs_account", "needs_installer",
                    "manual", "blocked", "needs_file"];
  const missing = statuses.filter(s => !w.eval(`Boolean(CHIP_FOR[${JSON.stringify(s)}])`));
  check("every engine status maps to a word", missing.join(",") || "none", "none");

  // the guards' own outcomes, which used to fall through to `ok ? DONE :
  // SKIPPED`. repaired means the tool just took a patch back off after a
  // launch Steam refused, and reporting that as SKIPPED describes the one
  // thing it did not do.
  check("a repaired patch says so", w.eval("CHIP_FOR.repaired"), "UNDONE");
  check("an unsafe build is skipped", w.eval("CHIP_FOR.unsafe_build"), "SKIPPED");

  // every word in the map has to be drawable, or the chip renders blank and
  // the row looks broken rather than explaining itself
  const undrawable = w.eval(
    "JSON.stringify([...new Set(Object.values(CHIP_FOR).join(''))]"
    + ".filter(c => c !== ' ' && !GLYPH.g[c]))");
  check("every chip word is in the glyph sheet", undrawable, "[]");

  // what Apply said about a row goes behind its (i), and the help line keeps
  // saying what the row is: Fabula-Lous's had turned into a line about DXVK
  const said = "installed 15 files into ReShade. Renderer was on DXVK";
  const shader = () => w.document.querySelector('.row[data-id="reshade"]');
  await w.eval(`jobEnd({ el: document.querySelector('.row[data-id="reshade"]'), id: "reshade",
                         name: "Fabula-Lous Shader" }, true, "applied", ${JSON.stringify(said)})`);
  const help = two.jobs.find(j => j.id === "reshade").help;
  check("a row's help line is still what it is after Apply", shader()._hint === help, true);
  const info = shader().querySelector(".info");
  info.dispatchEvent(new w.Event("mouseenter"));
  const tip = w.document.getElementById("tip").textContent;
  check("and what Apply said is behind its (i), after what it's about",
        tip.endsWith(said) && tip.startsWith("Fabula-Lous Shader, a ReShade preset"), true);
  // a row with no (i) of its own gets one for what Apply said, and loses it
  // when the next run starts
  const laa = () => w.document.querySelector('.row[data-id="laa"]');
  await w.eval(`jobEnd({ el: document.querySelector('.row[data-id="laa"]'), id: "laa",
                         name: "4GB Patch" }, false, "manual", "Close the game, then Apply again.")`);
  check("a row without an (i) gets one for a reason it didn't finish",
        !!laa().querySelector(".info") && laa()._hint === two.jobs.find(j => j.id === "laa").help, true);
  w.eval(`jobStart({ el: document.querySelector('.row[data-id="laa"]'), id: "laa", name: "4GB Patch" })`);
  check("and it goes once the next run starts on the row", !laa().querySelector(".info"), true);

  // APPLIED already says it. "already applied" behind an (i) beside it is the
  // same word twice, so a bare one adds no (i) and a longer one keeps the rest
  await w.eval(`jobEnd({ el: document.querySelector('.row[data-id="laa"]'), id: "laa",
                         name: "4GB Patch" }, true, "already_applied", "already applied")`);
  check("an already applied row gets no (i) for saying so", !laa().querySelector(".info"), true);
  await w.eval(`jobEnd({ el: document.querySelector('.row[data-id="laa"]'), id: "laa",
                         name: "4GB Patch" }, true, "already_applied",
                         "already applied, kept a clean copy as ffxiiiimg_untouched.exe")`);
  const kept = laa().querySelector(".info");
  if (kept) kept.dispatchEvent(new w.Event("mouseenter"));
  const keptTip = w.document.getElementById("tip").textContent;
  check("one with more to say keeps only the more",
        !!kept && keptTip.endsWith("Kept a clean copy as ffxiiiimg_untouched.exe")
        && !/already applied/i.test(keptTip), true);
  // and nothing the engine says for it ever reaches an (i) as "already applied"
  const shown = w.eval(`[pastApplied("already applied"), pastApplied("Already applied."),
                         pastApplied(""), pastApplied(undefined)].join("|")`);
  check("no wording of it gets through", shown, "|||");

  let bad = 0;
  for (const [name, got, want, ok] of results){
    console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}: ${got}${ok ? "" : ` (wanted ${want})`}`);
    if (!ok) bad++;
  }
  process.exit(bad ? 1 : 0);
}, 500));
