/* Screenshots of the real page, for the changes you cannot reason about.

   Everything else here asserts. This one looks: a palette or a spacing change
   is a claim about what a person sees, and the honest way to check it is to
   render the page in the same engine the app uses and open the picture.

     npm install playwright && node tools/shot.js [outdir]

   Writes one png per named view. Not wired into any suite, because a
   screenshot has no pass or fail.
*/
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const OUT = process.argv[2] || "/tmp/shots";
const ROOT = path.resolve(__dirname, "..");

const SETTINGS = {
  ok: true, exists: true,
  files: [{ id: "novafix", name: "NovaFix.ini", path: "E:\\g\\NovaFix.ini", exists: true }],
  settings: JSON.parse(require("child_process").execFileSync("python3", ["-c", `
import json
from patcher import novafix_config as nc
rows = []
# by index into each row's own choices, not by literal, so a relabel cannot
# leave this stub injecting values no row recognises. That is exactly what it
# did once: two rows silently fell through to the blank option and the
# screenshot showed "Leave Alone" where a real install would show a setting.
picked = {"Monitor": 1, "DisplayMode": 2, "Resolution": 5, "RefreshRate": 3,
          "VSync": 1, "FrameRateLimit": 4, "D3D9Backend": 2,
          "ShowFpsCounter": 0, "ShadowResolution": 3, "MSAA": 3,
          "AnisotropicFiltering": 4, "GraphicsSource": 0}
for s in nc.settings_for("ffxiii2"):
    at = picked.get(s.key)
    rows.append(dict(s.as_dict(), value=None if at is None else s.choices[at]))
print(json.dumps(rows))
`], { cwd: ROOT, encoding: "utf8" })),
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 },
                                       deviceScaleFactor: 2 });

  await page.addInitScript(`window.pywebview = { api: {
    scan: () => Promise.resolve({ ok:true, found:true, path:"E:\\\\SteamLibrary\\\\steamapps\\\\common\\\\FINAL FANTASY XIII-2",
      source:"steam", supported:["ff132fix","laa"],
      applied:["ff132fix"], error:null }),
    novafix_settings: () => Promise.resolve(${JSON.stringify(SETTINGS)}),
    save_novafix_settings: () => Promise.resolve({ ok:true, written:true }),
    list_addons: () => Promise.resolve({ ok:true, path:"...\\\\addons", mods:[] }),
    install_addon: () => Promise.resolve({ ok:true }),
    launch_game: () => Promise.resolve({ ok:true }),
  } };`);

  await page.goto("file://" + path.join(ROOT, "ui", "index.html"));
  await page.waitForTimeout(1400);

  const shoot = async (name) => {
    const file = path.join(OUT, name + ".png");
    await page.screenshot({ path: file });
    console.log("  " + file);
  };

  await page.evaluate(() => pick("ff13-2"));
  await page.waitForTimeout(700);
  await shoot("01-menu");

  await page.evaluate(() => Nova.open());
  await page.waitForTimeout(700);
  await shoot("02-graphics");

  await page.evaluate(() => { Nova.close(); Mods.open(); });
  await page.waitForTimeout(700);
  await shoot("03-mods");

  await browser.close();
})();
