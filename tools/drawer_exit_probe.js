/* Every way out of a side drawer, exercised against the real handlers.

   The one that needed proving: Escape used to close the drawer AND fall
   through to back(), because the drawer had its own keydown listener and the
   global Escape only knew about the About/Credits pops. Dismissing the NovaFix
   settings therefore dropped the user on the startup screen.

   Also pins the two halves of click-outside that are easy to get wrong: a
   press inside must not dismiss, and a press on the tab belongs to the tab's
   own toggle or the drawer closes and reopens in one gesture.

     npm install jsdom && node tools/drawer_exit_probe.js
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("ui/index.html", "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true,
                              url: "http://localhost/" });
const { window } = dom;

window.addEventListener("load", () => setTimeout(() => {
  const w = window, doc = w.document;
  let backCalls = 0;
  const realBack = w.back;
  w.back = function(){ backCalls++; };   // window-scoped, so this shadows nothing

  function makeDrawer(id, tabId){
    const p = doc.createElement("div"); p.id = id; p.className = "on";
    const inner = doc.createElement("div"); inner.className = "body";
    p.appendChild(inner); doc.body.appendChild(p);
    const t = doc.createElement("button"); t.id = tabId; doc.body.appendChild(t);
    return { p, inner, t };
  }

  const results = [];
  const check = (name, pass) => results.push([name, pass]);

  // 1. escape closes the drawer
  let d = makeDrawer("nv", "nvTab");
  doc.body.classList.add("m2");
  w.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("escape closes the drawer", !d.p.classList.contains("on"));

  // 2. escape with a drawer open must NOT also navigate back
  d.p.classList.add("on");
  const beforeBack = backCalls;
  doc.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("escape does not also go back", backCalls === beforeBack);

  // 3. a click outside closes it
  d.p.classList.add("on");
  doc.body.dispatchEvent(new w.MouseEvent("pointerdown", { bubbles: true }));
  check("outside pointerdown closes it", !d.p.classList.contains("on"));

  // 4. a click INSIDE does not
  d.p.classList.add("on");
  d.inner.dispatchEvent(new w.MouseEvent("pointerdown", { bubbles: true }));
  check("inside pointerdown keeps it open", d.p.classList.contains("on"));

  // 5. a press on the tab does not close (the tab owns the toggle)
  d.p.classList.add("on");
  d.t.dispatchEvent(new w.MouseEvent("pointerdown", { bubbles: true }));
  check("tab press leaves it to the toggle", d.p.classList.contains("on"));

  // 6. with nothing open, escape still navigates back
  d.p.classList.remove("on");
  const before6 = backCalls;
  doc.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("escape still goes back when nothing is open", backCalls > before6);

  // 7. the mods drawer behaves the same
  const m = makeDrawer("md", "mdTab");
  doc.body.dispatchEvent(new w.MouseEvent("pointerdown", { bubbles: true }));
  check("mods drawer closes on an outside click", !m.p.classList.contains("on"));

  /* 8. an open drawer owns the keyboard. These panels are dropdowns and a drop
     zone, so they are driven with a pointer; the menu behind must not move
     under one, and the keys must reach the controls inside unswallowed or a
     <select> the user clicked into stops responding to its own arrows. */
  d.p.classList.add("on");
  const keyed = k => {
    const e = new w.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true });
    doc.dispatchEvent(e);
    return e.defaultPrevented;
  };
  let moved = 0;
  const realStep = w.step;
  w.step = function(){ moved++; };
  check("W does not walk the list behind an open drawer", !keyed("w") && moved === 0);
  check("arrows are left to the control inside", !keyed("ArrowDown") && moved === 0);
  check("and so is Enter", !keyed("Enter"));
  w.step = realStep;
  check("escape still gets through", (d.p.classList.add("on"),
        keyed("Escape"), !d.p.classList.contains("on")));

  let bad = 0;
  for (const [name, pass] of results){
    console.log(`  ${pass ? "ok  " : "FAIL"}  ${name}`);
    if (!pass) bad++;
  }
  process.exit(bad ? 1 : 0);
}, 500));
