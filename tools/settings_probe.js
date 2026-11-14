/* The Settings drawer, rendered from the REAL schema rather than a stub row.

   Everything else that touches this drawer feeds it one invented setting, so
   the actual list has never been through the renderer. That list is now two
   mods' worth of settings written into two differently shaped ini files, and
   the failure modes are all visual: a heading that never appears, a row whose
   choices render blank, an ambient occlusion control that was supposed to be
   left out, or a header that has gone back to naming an ini file nobody asked
   about.

   So this pulls the payload straight out of patcher/novafix_config.py, hands
   it to the page as the bridge would, opens the drawer and reads back what a
   person would see.

     npm install jsdom && node tools/settings_probe.js
*/
const fs = require("fs");
const { execFileSync } = require("child_process");
const { JSDOM } = require("jsdom");

// the real thing, for a game folder that has neither ini yet, which is what a
// freshly patched install looks like. The voices row is the bridge's, read off
// both voice sets on disk and the launcher's setup.xml, with XIII-2's HD in
// the English set only. "ffxiii-fresh" has never been started, so no setup.xml
const payload = JSON.parse(execFileSync("python3", ["-c", `
import json, pathlib, tempfile
from patcher import novafix_config as nc, voices
from patcher.paths import undo_store_for

# XIII's character pair and the image pair beside it, both per language
XIII_SETS = ("filelist_scr{}.win32.bin", "white_scr{}.win32.bin",
             "filelist{}.win32.bin", "white_img{}.win32.bin")
out = {}
for key, game, app, data, pair in (
        ("ffxiii", "ffxiii", "292120", "white_data", XIII_SETS),
        ("ffxiii-fresh", "ffxiii", "292120", "white_data", XIII_SETS),
        ("ffxiii2", "ffxiii2", "292140", "alba_data", ("filelist{}.win32.bin", "white_img{}.win32.bin")),
        ("ffxiii3", "ffxiii3", "345350", "", ())):
    tmp = pathlib.Path(tempfile.mkdtemp())
    if game == "ffxiii3":
        # Savior's Patch is the user's own download, so its rows wait for its
        # dll, and a stand-in naming the ini it reads is that
        (tmp / "dinput8.dll").write_bytes(b"MZ SaviorsPatch.ini")
    state = dict(nc.current(tmp, ".", game), ok=True)
    if data:
        sys_dir = tmp / data / "sys"
        sys_dir.mkdir(parents=True)
        for voice in "uc":
            for name in pair:
                (sys_dir / name.format(voice)).write_bytes(b"x")
        if key != "ffxiii-fresh":
            (tmp / "setup.xml").write_bytes(b"<SaveData><voice_mode>1</voice_mode></SaveData>")
        if game == "ffxiii2":
            backup = undo_store_for(tmp) / "hdproject"
            backup.mkdir(parents=True)
            for path in sys_dir.iterdir():
                (backup / path.name).write_bytes(path.read_bytes())
            (sys_dir / "white_imgu.win32.bin").write_bytes(b"x and HD")
    # HD in English only, installed without the voices box, so a switch to
    # Japanese lands on files without it and the row says so
    gap = {"from": "u", "mod": "FFXIII-2 HD", "none": True}
    row = voices.row(tmp, app, gaps={"Japanese": gap} if game == "ffxiii2" else None)
    if row:
        state["settings"].append(row)
    out[key] = state
print(json.dumps(out))
`], { cwd: __dirname + "/..", encoding: "utf8" }));
// what the bridge answers a save with, every save sent, and how many reads
let fresh = false, answer = { ok: true, written: true }, reads = 0;
const saves = [], argsSent = [];
// XIII-2 with an HD install running on it, which the shell greys the voices for
let busy = false;
payload["ffxiii2-busy"] = JSON.parse(JSON.stringify(payload.ffxiii2));
Object.assign(payload["ffxiii2-busy"].settings.at(-1),
              { unavailable: "After the Install", warn: {} });

const html = fs.readFileSync(__dirname + "/../ui/index.html", "utf8");
const dom = new JSDOM(html, {
  runScripts: "dangerously", pretendToBeVisual: true,
  url: "http://127.0.0.1/ui/index.html",
  beforeParse(window){
    window.pywebview = { api: {
      scan: (id) => Promise.resolve({ ok: true, found: true, path: "E:\\g",
        source: "steam", supported: [], applied: [], error: null }),
      // a fresh object every read, the way the bridge's JSON arrives, so the
      // drawer can't tell two answers apart by identity
      novafix_settings: (id) => { reads++; return Promise.resolve(JSON.parse(JSON.stringify(
        payload[({ ff13: fresh ? "ffxiii-fresh" : "ffxiii", "ff13-2": busy ? "ffxiii2-busy" : "ffxiii2",
                   lr: "ffxiii3" })[id]]))); },
      save_novafix_settings: (...args) => {
        saves.push(args[1]); argsSent.push(args.length); return Promise.resolve(answer);
      },
      launch_game: () => Promise.resolve({ ok: true }),
      install_addon: () => Promise.resolve({ ok: true }),
      list_addons: () => Promise.resolve({ ok: true, mods: [] }),
    } };
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
const wait = ms => new Promise(r => setTimeout(r, ms));

const results = [];
const check = (name, got, want) => results.push([name, got, want, got === want]);

// what the drawer renders: headings in order, and the label of every row
function rendered(){
  const body = w.document.getElementById("nvBody");
  const groups = [...body.querySelectorAll(".grp")].map(e => e.textContent);
  const rows = [...body.querySelectorAll("label")].map(l => ({
    label: l.querySelector("span").textContent,
    options: [...(l.querySelector("select") || { children: [] }).children]
               .map(o => o.textContent),
    kind: l.querySelector("input") ? "number" : l.dataset.key.endsWith("/GraphicsPreset")
      ? "preset" : "choice",
    value: (l.querySelector("select") || {}).value,
  }));
  const head = w.document.querySelector("#nv header");
  return { groups, rows,
           tabs: [...head.querySelectorAll(".dtab")]
                   .filter(b => !b.hidden)
                   .map(b => b.textContent + (b.classList.contains("now") ? "*" : "")),
           head: head.textContent.replace(/\s+/g, " ").trim() };
}

(async () => {
  await wait(400);

  w.pick("lr");
  await wait(250);
  await w.eval("Nova.open()");
  await wait(250);
  const lr = rendered();

  check("each heading appears exactly once",
        lr.groups.length === new Set(lr.groups).size, true);

  /* Open, already read. The game is read when its tab is offered, so the
     drawer comes up on rows in the same task as the press, never on a
     "Reading..." line, and the read it makes underneath only redraws when the
     game says something different. */
  w.eval("Nova.close()");
  w.pick("ff13-2");
  const offered = reads;
  await wait(250);
  check("a game's settings are read before the drawer is opened", reads > offered, true);
  const first = w.eval(`(() => {
    Nova.open();
    const body = document.getElementById("nvBody");
    return body.querySelectorAll("label").length + "|" + /Reading/.test(body.textContent);
  })()`);
  check("and it opens on them, not on a placeholder", /^[1-9]\d*\|false$/.test(first), true);
  const held = w.document.querySelector("#nvBody select");
  await wait(150);
  check("an answer that's the same leaves the rows alone",
        w.document.querySelector("#nvBody select") === held, true);
  w.eval("Nova.close()");
  busy = true;
  // the voices, last, which an install running greys
  const voicesOff = () => {
    const box = w.document.querySelector("#nvBody label:last-of-type select");
    return box ? box.disabled : "no row";
  };
  w.eval("Nova.open()");
  const stale = voicesOff();
  await wait(150);
  check("one that isn't redraws them", `${stale} -> ${voicesOff()}`, "false -> true");
  busy = false;
  w.eval("Nova.close()");
  // and the redraw keeps whoever's in a control in it, rather than dropping
  // the focus with the old rows
  w.eval(`(() => {
    Nova.open();
    [...document.querySelectorAll("#nvBody label")]
      .find(l => l.querySelector("span").textContent === "VSync").querySelector("select").focus();
  })()`);
  await wait(150);
  // the voices coming back on is the redraw having happened
  check("a redraw under an edit keeps the control it was on", w.eval(`(() => {
    const row = document.activeElement && document.activeElement.closest("#nvBody label");
    return row ? row.querySelector("span").textContent : "lost";
  })()`) + "|" + voicesOff(), "VSync|false");
  w.eval("Nova.close()");
  w.pick("lr");
  await wait(250);

  check("LR groups are the ones we authored",
        lr.groups.join(" | "), "Display | Performance | Quality | World");

  const labels = lr.rows.map(r => r.label);
  check("no ambient occlusion row",
        labels.filter(l => /occlusion|ambient/i.test(l)).join(",") || "none", "none");

  /* the streamlining, asserted as a number. seventeen rows with three ways to
     set the frame rate and three separate anti-aliasing controls is a
     configuration file with a skin on it, not a settings panel. */
  check("the whole panel fits in ten rows, the preset one of them", lr.rows.length <= 10, true);

  check("one row per question",
        labels.join(" | "),
        "Render Resolution | Frame Rate | Stutter Reduction | Renderer | "
        + "FPS Counter | Graphics Preset | Shadow Quality | Anti-Aliasing | Texture Filtering | "
        + "NPC Spawn Distance");

  // the mod's own switch names are ours to hide. nobody is looking for
  // "LockRect discard fix" in a graphics menu
  check("no raw mod switch names leak through",
        labels.filter(l => /LockRect|ReadFile|throttle|SimDelta|limiter/i.test(l))
              .join(",") || "none", "none");

  // the bug this shape exists to prevent: a row whose choices render blank,
  // which is what a Savior's toggle looked like before it carried its labels
  const blank = lr.rows.filter(r => r.kind === "choice" && r.options.length < 2);
  check("every choice row has options", blank.map(r => r.label).join(",") || "none", "none");

  const shadows = lr.rows.find(r => r.label === "Shadow Quality");
  check("shadow quality reads as words, not raw numbers",
        shadows && shadows.options.includes("Very High")
          && !shadows.options.some(o => /\d{4}/.test(o)), true);

  const fps = lr.rows.find(r => r.label === "Frame Rate");
  check("frame rate hides the x100 encoding",
        fps && fps.options.includes("60 FPS") && !fps.options.includes("6000"), true);

  const res = lr.rows.find(r => r.label === "Render Resolution");
  check("resolution is offered", res && res.options.includes("1440p"), true);

  // the label everything unset falls back to, which is now just the one word
  check("unset rows say Default",
        lr.rows.every(r => r.kind !== "choice" || r.options[0] === "Default"), true);

  /* the header is a tab pair now, not a subtitle. "Editing NovaFix.ini" named
     a file the user never chose and could not act on, in the widest line of an
     open panel. */
  check("no ini path in the header", /\.ini/.test(lr.head) ? lr.head : "none", "none");
  check("the header is the tab pair, Settings selected",
        lr.tabs.join(" / "), "Mods / Settings*");
  // LR starts straight into the game, so there's no launcher keeping voices
  check("LR has no voices row", labels.includes("Voices"), false);

  /* XIII and XIII-2 get the same treatment: same headings, same one-row-one-
     question rule, and none of LR's rows. Their panel was thirteen rows with
     VSync and triple buffering as separate switches and a launcher-override
     toggle whose only correct answer was implied by the rows under it. The
     voices are theirs alone, under a heading of their own, last. */
  for (const [id, name] of [["ff13", "XIII"], ["ff13-2", "XIII-2"]]){
    w.pick(id);
    await wait(200);
    await w.eval("Nova.open()");
    await wait(200);
    const other = rendered();
    const theirs = other.rows.map(r => r.label);

    check(`${name} headings appear exactly once`,
          other.groups.length === new Set(other.groups).size, true);
    check(`${name} uses LR's headings, and Language last`,
          other.groups.join(" | "), "Display | Performance | Quality | Language");
    check(`${name} is one row per question`,
          theirs.join(" | "),
          "Graphics Source | Monitor | Window Mode | Resolution | "
          + "Refresh Rate | VSync | Frame Rate | Renderer | FPS Counter | "
          + "Graphics Preset | Shadow Quality | Anti-Aliasing | Anisotropic Filtering | Voices");
    const voicesRow = other.rows.find(r => r.label === "Voices");
    check(`${name} offers both voices`,
          voicesRow && voicesRow.options.join(","), "Leave Alone,English,Japanese");

    // it kept its escape hatch and lost the wording nobody parsed
    const src = other.rows.find(r => r.label === "Graphics Source");
    check(`${name} names the launcher choice in words`,
          src && src.options.includes("This Patcher")
            && src.options.includes("The Game's Launcher"), true);
    check(`${name} has no row called Override anything`,
          theirs.filter(l => /^override/i.test(l)).join(",") || "none", "none");
    // triple buffering only means anything with VSync on, so it rides with it
    check(`${name} has no separate triple buffering row`,
          theirs.filter(l => /triple/i.test(l)).join(",") || "none", "none");

    const aa = other.rows.find(r => r.label === "Anti-Aliasing");
    check(`${name} anti-aliasing offers no Off`,
          aa && !aa.options.includes("Off"), true);
    check(`${name} anti-aliasing reads as multipliers`,
          aa && aa.options.includes("4x"), true);

    const af = other.rows.find(r => r.label === "Anisotropic Filtering");
    check(`${name} anisotropic filtering does offer Off`,
          af && af.options.filter(o => o === "Off").length === 1, true);

    const mode = other.rows.find(r => r.label === "Window Mode");
    check(`${name} window mode reads as words`,
          mode && mode.options.includes("Borderless Fullscreen")
            && !mode.options.includes("BorderlessFullscreen"), true);

    check(`${name} gets no Savior's rows`,
          theirs.some(l => /Stutter|NPC/.test(l)), false);
    check(`${name} gets the same tab pair`,
          other.tabs.join(" / "), "Mods / Settings*");
  }

  const choose = (label, value) => w.eval(`(() => {
    const row = [...document.querySelectorAll("#nvBody label")]
      .find(l => l.querySelector("span").textContent === ${JSON.stringify(label)});
    const sel = row.querySelector("select");
    sel.value = ${JSON.stringify(value)};
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return sel.value;
  })()`);
  const footNote = () => w.document.getElementById("nvNote").textContent;
  const openOn = async id => { w.pick(id); await wait(200); await w.eval("Nova.open()"); await wait(200); };
  const apply = async () => { w.document.getElementById("nvSave").click(); await wait(120); };

  /* the Graphics Preset row: a tier sets the Quality rows together, a hand
     edit turns it Custom, and nothing it does touches the frame rate. An ini
     nobody's touched is NovaFix's own defaults, which are its High */
  await openOn("ff13");
  const rowNamed = label => rendered().rows.find(r => r.label === label);
  check("an untouched game reads as High", rowNamed("Graphics Preset").value, "High");
  check("Auto says what it picked for this PC",
        rowNamed("Graphics Preset").options.some(o => /^Auto \((Low|Medium|High)\)$/.test(o)), true);
  const capped = rowNamed("Frame Rate").value;
  choose("Graphics Preset", "Low");
  check("Low sets the rows under it",
        [rowNamed("Shadow Quality").value, rowNamed("Anti-Aliasing").value, rowNamed("Anisotropic Filtering").value]
          .join(","), "1024,2,4");
  check("and leaves the frame rate alone", rowNamed("Frame Rate").value, capped);
  choose("Shadow Quality", "4096");
  check("a row changed by hand reads Custom", rowNamed("Graphics Preset").value, "Custom");
  check("shown, never offered", w.document.querySelector(
    '#nvBody label[data-key="Quality/GraphicsPreset"] option[value="Custom"]').disabled, true);
  choose("Shadow Quality", "1024");
  check("and back to the tier's own values is the tier again", rowNamed("Graphics Preset").value, "Low");
  choose("Graphics Preset", "High");
  w.closeDrawers();
  await wait(50);
  await openOn("ff13-2");

  /* XIII-2 has HD in its English set only, installed without the voices box,
     so a switch to Japanese lands on files without it. The drawer says so when
     it's picked rather than after Apply, and the switch is the write either way */
  w.document.getElementById("nvNote").textContent = "";
  choose("Voices", "Japanese");
  check("picking the voices HD isn't in says what the switch leaves behind",
        /^FFXIII-2 HD is only in the English voice files, and Japanese loads its own/
          .test(footNote()), true);
  choose("VSync", "On");
  check("and another row changing doesn't take it down",
        /^FFXIII-2 HD is only in the English/.test(footNote()), true);
  choose("Voices", "English");
  check("picking back does", footNote(), "");
  choose("Voices", "Japanese");
  w.document.getElementById("nvNote").textContent = "Applied.";
  choose("Voices", "");
  check("but never a line it didn't put there", footNote(), "Applied.");

  // the note belongs to one game, and a switch takes its edits with it
  choose("Voices", "Japanese");
  await openOn("lr");
  check("another game's drawer opens without it", footNote(), "");

  /* after Apply, the notice and whether the game is up are both still true,
     the rows read the game again, and Mods surveys the set it loads now */
  await openOn("ff13-2");
  w.eval(`(() => { const real = Mods.resurvey;
                   window.__resurveys = 0;
                   Mods.resurvey = () => { window.__resurveys++; return real(); }; })()`);
  answer = { ok: true, written: true, gameRunning: true, changed: { Language: ["Voices"] },
             notice: "NovaFix will pick this up at the next start." };
  choose("Voices", "Japanese");
  const before = reads;
  await apply();
  check("a notice keeps the restart line", footNote(),
        "Applied. The game is running, so restart it to see these. "
        + "NovaFix will pick this up at the next start.");
  check("the rows read the game again", reads - before, 1);
  check("and Mods reads it again too", w.eval("window.__resurveys"), 1);
  answer = { ok: true, written: true };

  /* a switch onto files short of HD is the launcher's write and a note, never
     a run: no terminal, nothing sent but the changes, and the drawer stays up
     while there's a note to read */
  answer = { ok: true, written: true, changed: { Language: ["Voices"] },
             notice: "FFXIII-2 HD is only in the English voice files." };
  choose("Voices", "Japanese");
  await apply();
  check("a save sends the changes and nothing else", argsSent.at(-1), 2);
  check("a switch short of HD says so after Apply too", footNote(),
        "Applied. Takes effect when you start the game. "
        + "FFXIII-2 HD is only in the English voice files.");
  check("and no terminal comes up on it", w.document.getElementById("term").classList.contains("on"),
        false);
  await wait(1200);
  check("and the drawer stays up to be read", w.document.getElementById("nv").classList.contains("on"),
        true);
  answer = { ok: true, written: true };

  // a game that's never been started has no setup.xml to change yet
  await openOn("ff13");
  choose("Voices", "Japanese");
  fresh = true;
  await w.eval("Nova.open()");
  await wait(200);
  const waiting = [...w.document.querySelectorAll("#nvBody label")]
    .find(l => l.querySelector("span").textContent === "Voices");
  const box = waiting && waiting.querySelector("select");
  check("before the first launch the voices are greyed",
        box && box.disabled && box.textContent, "After First Launch");
  if (waiting) waiting.dispatchEvent(new w.MouseEvent("mouseenter"));
  check("with the reason in the help bar on a hover",
        /first time it starts/.test(w.document.getElementById("hintText").textContent), true);
  const rate = [...w.document.querySelectorAll("#nvBody label")]
    .find(l => l.querySelector("span").textContent === "Frame Rate");
  if (rate) rate.querySelector("select").focus();
  const frameNote = w.document.getElementById("hintText").textContent;
  check("and a setting says what it does to the keyboard too",
        /Caps the frame rate/.test(frameNote), true);
  // shut from outside, its note goes with it, and only its own: a press on a
  // patch row shuts it too, and that row's hint is the one to keep
  const said = () => w.document.getElementById("hintText").textContent;
  w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await wait(320);
  check("and shutting the drawer takes that note down", said(), "");
  await w.eval("Nova.open()");
  await wait(200);
  // the rows the open drew, not the ones before it
  const again = [...w.document.querySelectorAll("#nvBody label")]
    .find(l => l.querySelector("span").textContent === "Frame Rate");
  if (again) again.querySelector("select").focus();
  check("the note's up again for the row the open drew", said(), rate ? frameNote : "");
  // Tab onto a row with nothing to say takes the last note down
  const quiet = [...w.document.querySelectorAll("#nvBody label")]
    .find(l => l.querySelector("span").textContent === "Window Mode");
  if (quiet) quiet.querySelector("select").focus();
  await wait(320);
  check("and a row without one takes it down", said(), "");
  if (again) again.querySelector("select").focus();
  // switching to Mods takes it down too
  w.document.getElementById("mdTab").click();
  await wait(320);
  check("so does switching to Mods", said(), "");
  w.document.getElementById("nvTab").click();
  await wait(200);
  // a note up, then a patch row's hint over it
  const back = [...w.document.querySelectorAll("#nvBody label")]
    .find(l => l.querySelector("span").textContent === "Frame Rate");
  if (back) back.querySelector("select").focus();
  const row = w.document.querySelector("#patches .row");
  row.dispatchEvent(new w.MouseEvent("mouseenter"));
  const rowHint = said();
  w.closeDrawers();
  await wait(320);
  check("but not a patch row's hint that went up after it", said() === rowHint && rowHint !== "", true);
  const sent = saves.length;
  await apply();
  check("and a pick made before it greyed isn't sent",
        `${footNote()} | ${saves.length - sent} sent`, "Nothing changed. | 0 sent");
  fresh = false;

  // nor is its warning left up beside a row that can't take the pick any more
  await openOn("ff13-2");
  choose("Voices", "Japanese");
  busy = true;
  await w.eval("Nova.open()");
  await wait(200);
  check("a warning goes when its row greys", footNote(), "");
  busy = false;

  /* switching between the two drawers without closing first. Both are
     position:absolute right:0, so opening one while the other is on used to
     stack them: Mods rendered over Graphics with both still carrying .on, and
     it took two more presses to untangle. Pressing the other tab is a switch. */
  const drawers = () => ["nv", "md"].map(id =>
    id + "=" + (w.document.getElementById(id).classList.contains("on") ? "on" : "off")).join(" ");

  w.pick("ff13-2");
  await wait(200);
  await w.eval("Nova.open()");
  await wait(200);
  check("graphics opens alone", drawers(), "nv=on md=off");

  w.document.getElementById("mdTab").click();
  await wait(300);
  check("the mods tab switches rather than stacking", drawers(), "nv=off md=on");

  w.document.getElementById("nvTab").click();
  await wait(300);
  check("and back again", drawers(), "nv=on md=off");

  /* an edit in progress belongs to the game, not to whether the panel happens
     to be visible, so a detour through Mods must not throw it away. Driven
     through the control rather than the module's own state, which is private
     and would not prove the listener ran. */
  const pickOption = (label, text) => w.eval(`(() => {
    const row = [...document.querySelectorAll("#nvBody label")]
      .find(l => l.querySelector("span").textContent === ${JSON.stringify(label)});
    const sel = row.querySelector("select");
    sel.value = ${JSON.stringify(text)};
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return sel.value;
  })()`);
  const shownFor = (label) => w.eval(`(() => {
    const row = [...document.querySelectorAll("#nvBody label")]
      .find(l => l.querySelector("span").textContent === ${JSON.stringify(label)});
    return row ? row.querySelector("select").value : "no row";
  })()`);

  check("an option can be picked", pickOption("Anti-Aliasing", "8"), "8");
  w.document.getElementById("mdTab").click();
  await wait(250);
  w.document.getElementById("nvTab").click();
  await wait(400);
  check("a pending edit survives the detour", shownFor("Anti-Aliasing"), "8");

  /* The next start, with this one's reads kept: the drawer opens on what the
     last one read before its own read has answered, which here it never does */
  const kept = JSON.stringify(payload.ffxiii3);
  const next = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true,
    url: "http://127.0.0.1/ui/index.html",
    beforeParse(window){
      window.localStorage.setItem("ffxiii.settings.lr", kept);
      window.pywebview = { api: {
        scan: () => Promise.resolve({ ok: true, found: true, path: "E:\\g", source: "steam",
                                      supported: [], applied: [], error: null }),
        novafix_settings: () => new Promise(() => {}),
        list_addons: () => new Promise(() => {}),
        install_addon: () => Promise.resolve({ ok: true }),
      } };
      window.fetch = () => Promise.resolve({ ok: false });
      window.HTMLMediaElement.prototype.play = () => Promise.resolve();
      window.HTMLMediaElement.prototype.load = () => {};
      window.console.warn = () => {};
    },
  }).window;
  await wait(400);
  next.pick("lr");
  await wait(250);
  check("the next start opens on the last read before its own lands", next.eval(`(() => {
    Nova.open();
    return document.querySelectorAll("#nvBody label").length > 0
      && !/Reading/.test(document.getElementById("nvBody").textContent);
  })()`), true);

  let bad = 0;
  for (const [name, got, want, ok] of results){
    console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}: ${got}${ok ? "" : ` (wanted ${want})`}`);
    if (!ok) bad++;
  }
  process.exit(bad ? 1 : 0);
})();
