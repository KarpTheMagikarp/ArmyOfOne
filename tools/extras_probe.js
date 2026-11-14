/* Checks that Apply offers only what Apply can finish.

   A mod that ships its own installer, or lives behind a login, ends at
   NEEDS_INSTALLER or MANUAL however the download arrived, so listing it as a
   tickable job promises a run that can only end by telling the user to go do
   it themselves. Those are kind "extra" and live in the Mods drawer.

   Drives the page's own jobsOf(), which is what both rendered sections use.

     npm install jsdom && node tools/extras_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("ui/index.html", "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true,
                              url: "http://localhost/" });
const { window } = dom;

window.addEventListener("load", () => setTimeout(() => {
  const w = window;
  const results = [];
  const check = (n, p) => results.push([n, p]);

  // const/let at script scope are not window properties, so read the
  // shipped file and drive the page's own functions with it
  const manifest = JSON.parse(fs.readFileSync("ui/jobs.json", "utf8"));
  const byId = Object.fromEntries(manifest.games.map(g => [g.id, g]));

  // the ones that finish outside Apply are marked extra, with a page. HD
  // Models Plus is the drawer's to build, so it's one of XIII's, HD Fonts and
  // GUI is a download the drawer catches on all three, and Savior's Patch is
  // LR's, the user's own download since it stopped shipping
  const expected = {
    "ff13":   ["hdfonts", "hdmodelsplus", "hdproject"],
    "ff13-2": ["dlcrestore", "hdfonts", "hdproject2"],
    "lr":     ["dlcrestore", "hdfonts", "saviors"],
  };
  for (const [gid, ids] of Object.entries(expected)){
    const extra = byId[gid].jobs.filter(j => j.kind === "extra").map(j => j.id).sort();
    check(`${gid}: extras are ${JSON.stringify(ids)}`, JSON.stringify(extra) === JSON.stringify(ids.sort()));
    for (const j of byId[gid].jobs.filter(j => j.kind === "extra")){
      check(`${gid}/${j.id} has a page link`, typeof j.page === "string" && j.page.startsWith("https://"));
    }
  }

  // LR's DLC restore is under Worth getting, where it's easy to find, and
  // isn't anything Apply has to run
  check("lr's dlcrestore is under Worth getting",
        byId["lr"].jobs.find(j => j.id === "dlcrestore").kind === "extra");

  // jobsOf drives both rendered sections, and neither should see an extra
  for (const gid of Object.keys(expected)){
    const g = byId[gid];
    const shown = [...w.jobsOf(g, "required"), ...w.jobsOf(g, "optional")].map(j => j.id);
    const leaked = shown.filter(id => g.jobs.find(j => j.id === id).kind === "extra");
    check(`${gid}: no extra reaches a rendered section`, leaked.length === 0);
  }

  let bad = 0;
  for (const [n, p] of results){ console.log(`  ${p ? "ok  " : "FAIL"}  ${n}`); if (!p) bad++; }
  process.exit(bad ? 1 : 0);
}, 500));
