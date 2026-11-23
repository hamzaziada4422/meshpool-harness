// In-app files: the folder tree, the editor, and the promise that no click opens another program.
// Usage: testkit/dev_app.sh PORT HOME                 (app on the mock model)
//        node testkit/ux_files.js PORT PROJECT_DIR SHOT_DIR   (PROJECT_DIR: a new, empty folder this script fills)
const {chromium} = require("playwright");
const fs = require("fs");
const path = require("path");
const [port, proj, shots] = process.argv.slice(2);
const B = `http://127.0.0.1:${port}`;
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (!ok && detail ? "  -> " + detail : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
fs.mkdirSync(shots, {recursive: true});

const APP_PY = `# Greets the user
import sys


def greet(name):
    """Say hello."""
    msg = "Hello, " + name
    print(msg)  # shown on screen
    return 42


class Greeter:
    pass
`;
const files = {
  "src/app.py": APP_PY, "src/utils/deep.js": "// deep file\nexport const answer = 42;\n", "notes.md": "# Project notes\n\nSome **bold** text and a list:\n\n- one\n- two\n",
  "data.json": JSON.stringify({name: "demo", tags: ["a", "b"], nested: {ok: true, n: 3}}, null, 2) + "\n", "README.txt": "plain text file\n",
  "node_modules/skipme.js": "x", ".git/config": "x",
};

(async () => {
  for (const [rel, text] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(proj, rel)), {recursive: true}); fs.writeFileSync(path.join(proj, rel), text); }
  fs.mkdirSync(path.join(proj, "assets"), {recursive: true});
  // a 1x1 PNG
  fs.writeFileSync(path.join(proj, "assets/dot.png"), Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));

  const html = await (await fetch(B + "/")).text(); const TOK = html.match(/token: "([^"]+)"/)[1];
  const call = async (p, body) => (await fetch(B + p, {method: body ? "POST" : "GET", headers: {"X-Meshpool-Token": TOK, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined})).json();
  await call("/api/projects", {action: "add", path: proj});
  const v = await call("/api/agent", {workdir: proj, harness: "meshpool-code", kind: "code", mode: "auto"});
  for (let k = 0; k < 40; k++) { if ((await call(`/api/agent/${v.id}?since=0`)).state !== "starting") break; await sleep(250); }

  const b = await chromium.launch();
  const mk = async (w, h, theme) => {
    const ctx = await b.newContext({viewport: {width: w, height: h}, deviceScaleFactor: 1});
    const p = await ctx.newPage(); p.errs = []; p.reqs = [];
    p.on("pageerror", e => p.errs.push(e.message));
    p.on("request", r => { const u = new URL(r.url()); if (u.pathname.startsWith("/api/")) p.reqs.push(r.method() + " " + u.pathname); });
    await p.addInitScript(t => { try { localStorage.setItem("mp_permsAsked", "1"); localStorage.setItem("mp_theme", JSON.stringify(t)); localStorage.setItem("mp_cards_off", '["key","github","project"]'); } catch (e) {} }, theme);
    await p.goto(B + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(800);
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
    await p.evaluate(id => loadThreads().then(() => openThread(id)), v.id);
    await p.waitForTimeout(600);
    return p;
  };
  const p = await mk(1440, 900, "light");
  const shot = (pg, name) => pg.screenshot({path: `${shots}/${name}.png`});
  const text = sel => p.textContent(sel);
  const taVal = () => p.inputValue("#edTa");
  const reqCount = re => p.reqs.filter(r => re.test(r)).length;
  const row = rel => `.fsr[data-path="${rel}"] >> visible=true`;
  const toTree = async () => { if (await p.$eval("#pvp", e => !e.hidden)) await p.click("#pvBack"); };   // the viewer's back arrow shows the folder again

  // ---- the tree
  await p.evaluate(() => togglePanel("files", true));
  await p.waitForSelector(".fs-tree .fsr", {timeout: 15000});
  let top = await p.$$eval(".fs-tree > li > .fsr .fsn", e => e.map(x => x.textContent));
  check("the tree lists the project, folders first", top[0] === "assets" && top.includes("notes.md"), top.join(","));
  check("the tree hides .git and node_modules", !top.includes(".git") && !top.includes("node_modules"), top.join(","));
  const listsBefore = reqCount(/\/api\/fs\/list/);
  await p.click(row("src")); await p.waitForSelector('.fsr[data-path="src/app.py"]');
  check("a folder loads only when opened", reqCount(/\/api\/fs\/list/) === listsBefore + 1);
  await p.click(row("src/utils")); await p.waitForSelector(row("src/utils/deep.js"));
  await p.fill(".fs-q", "deep"); await p.waitForSelector(".fs-res .fsr");
  check("search finds a file in a closed folder", (await p.$$eval(".fs-res .fsr", e => e.map(x => x.dataset.path))).join() === "src/utils/deep.js");
  await p.fill(".fs-q", "zzzz-nothing"); await p.waitForFunction(() => /No files match/.test($(".fs-note")?.textContent || ""));
  await p.fill(".fs-q", ""); await p.waitForSelector(".fs-tree:not([hidden])");

  // ---- open a code file: line numbers and colours
  await p.click(row("src/app.py")); await p.waitForSelector("#edTa", {timeout: 15000});
  check("a file opens in the Meshpool editor pane", await p.evaluate(() => !$("#pvp").hidden) && (await taVal()) === APP_PY);
  check("it has one line number per line", (await p.$$eval("#edHl .ln", e => e.length)) === APP_PY.split("\n").length);
  check("keywords, strings and comments are coloured", (await p.$$eval("#edHl .tk-k", e => e.length)) > 3 && (await p.$$eval("#edHl .tk-s", e => e.length)) >= 2 && (await p.$$eval("#edHl .tk-c", e => e.length)) >= 2);
  check("the file type is named", /Python/.test(await text("#pvType")));
  await shot(p, "01-editor-desktop-light");

  // ---- edit and save, Ctrl+S, checked on disk
  await p.click("#edTa"); await p.keyboard.press("Control+End"); await p.keyboard.type("# added by the test\n");
  await p.waitForFunction(() => /Unsaved/.test($("#edSt").textContent));
  check("typing marks the file as unsaved", /Unsaved/.test(await text("#edSt")) && /^● /.test(await text("#pvName")) && !(await p.$eval("#edSave", e => e.disabled)));
  await p.keyboard.press("Control+s"); await p.waitForFunction(() => /Saved/.test($("#edSt").textContent));
  check("Ctrl+S saves to disk", fs.readFileSync(path.join(proj, "src/app.py"), "utf8") === APP_PY + "# added by the test\n");
  check("after saving there is nothing left to save", await p.$eval("#edSave", e => e.disabled));

  // ---- a change on disk while editing: refuse, offer reload
  fs.writeFileSync(path.join(proj, "src/app.py"), "# changed by someone else\n");
  await p.click("#edTa"); await p.keyboard.type("my change "); await p.keyboard.press("Control+s");
  await p.waitForSelector("#edWarn:not([hidden])");
  check("a save over a changed file is refused", /changed on disk/.test(await text("#edWarn")) && fs.readFileSync(path.join(proj, "src/app.py"), "utf8") === "# changed by someone else\n");
  check("Reload is offered", !!(await p.$('#edWarn button:has-text("Reload from disk")')));
  await shot(p, "02-conflict");
  await p.click('#edWarn button:has-text("Reload from disk")'); await p.waitForFunction(() => $("#edWarn").hidden);
  check("Reload shows what is on disk", (await taVal()) === "# changed by someone else\n");
  fs.writeFileSync(path.join(proj, "src/app.py"), APP_PY);
  await p.waitForFunction(v => $("#edTa").value === v, APP_PY, {timeout: 12000});
  check("an unedited file reloads by itself when it changes on disk", true);

  // ---- find in file and wrap
  await p.click("#edTa"); await p.keyboard.press("Control+f"); await p.waitForSelector("#edFind:not([hidden])");
  await p.fill("#edFq", "msg"); await p.waitForFunction(() => /of/.test($("#edFc").textContent));
  check("find counts matches and marks the current one", /1 of 2/.test(await text("#edFc")) && (await p.$$eval("#edFm mark", e => e.length)) === 2 && !!(await p.$("#edFm mark.cur")));
  await p.keyboard.press("Enter"); check("Enter goes to the next match", /2 of 2/.test(await text("#edFc")));
  await shot(p, "03-find");
  await p.keyboard.press("Escape"); check("Esc closes find", await p.$eval("#edFind", e => e.hidden));
  await p.click("#edWrap"); check("the wrap toggle works", (await p.getAttribute("#edWrap", "aria-pressed")) === "true" && await p.$eval("#edTa", e => e.classList.contains("wrap")));
  await p.click("#edWrap");
  await p.click("#edTa"); await p.keyboard.type("x"); await p.keyboard.press("Backspace");   // leave it as it was

  // ---- markdown and JSON, with a Source toggle
  await toTree(); await p.click(row("notes.md")); await p.waitForSelector(".pv-md h1, .pv-md h2, .pv-md h3", {timeout: 10000});
  check("Markdown opens rendered", /Project notes/.test(await text(".pv-md")) && (await p.$$eval(".pv-md li", e => e.length)) === 2);
  await p.click('#pvSeg button:has-text("Source")'); await p.waitForSelector("#edTa", {state: "visible"});
  check("Markdown has a Source toggle that shows the text", (await taVal()).startsWith("# Project notes"));
  await p.click('#pvSeg button:has-text("Preview")'); check("and back to the rendered page", await p.$eval("#edView", e => !e.hidden));
  await toTree(); await p.click(row("data.json")); await p.waitForSelector(".ed-json details");
  check("JSON opens as a folding tree", /demo/.test(await text(".ed-json")) && (await p.$$eval(".ed-json details", e => e.length)) >= 2);
  await shot(p, "04-json");
  await p.click('#pvSeg button:has-text("Source")'); check("JSON Source is editable text", (await taVal()).includes('"tags"'));

  // ---- other kinds reuse the existing previews
  await toTree(); await p.click(row("assets")); await p.waitForSelector(row("assets/dot.png"));
  await p.click(row("assets/dot.png")); await p.waitForSelector("#pvStage .pv-img img", {timeout: 10000});
  check("an image opens in the image preview, not the editor", !(await p.$("#edTa")));

  // ---- the context menu holds the only route to the system file manager
  const revealReqs = () => p.reqs.filter(r => /fs\/reveal/.test(r)).length;
  await p.route("**/api/fs/reveal", r => r.fulfill({status: 200, contentType: "application/json", body: '{"ok":true}'}));   // never reach the real file manager
  await p.click("#pvClose"); await p.waitForTimeout(200);
  await p.click(row("README.txt"), {button: "right"}); await p.waitForSelector("#ctx");
  const items = await p.$$eval("#ctx .ml", e => e.map(x => x.textContent));
  check("the menu names the secondary action plainly", items.some(t => /^Show in (Finder|File Explorer|file manager)$/.test(t)) && items.includes("Open in Meshpool"), items.join(","));
  await shot(p, "05-context-menu");
  await p.keyboard.press("Escape"); await p.waitForTimeout(150);
  check("opening the menu does not reveal anything", revealReqs() === 0);
  await p.click(row("README.txt")); await p.waitForSelector("#edTa");
  check("a plain click opens the file in the app", (await taVal()) === "plain text file\n" && revealReqs() === 0);

  // ---- folders and the settings folders open in the tree
  await p.evaluate(r => openFolderInApp(r, "src/utils"), proj); await p.waitForSelector(row("src/utils/deep.js"));
  check("a folder opens in the tree, expanded to it", !!(await p.$('.fsr.dir.open[data-path="src/utils"] >> visible=true')));
  await p.evaluate(() => openHomeFolder("skills")); await p.waitForSelector("#pvp .fs-tree");
  check("the skills folder opens in the in-app tree", /Folder/.test(await text("#pvType")));
  await p.evaluate(() => openHomeFolder("")); await p.waitForSelector("#pvp .fs-tree .fsr", {timeout: 10000});
  check("the data folder opens in the in-app tree", (await p.$$eval("#pvp .fs-tree .fsr .fsn", e => e.map(x => x.textContent))).length > 0);
  await p.click("#pvClose");

  // ---- unsaved changes are guarded
  await p.evaluate(([r]) => openPreview(r, "README.txt"), [proj]); await p.waitForSelector("#edTa");
  await p.click("#edTa"); await p.keyboard.type("dirty ");
  await p.click("#pvClose"); await p.waitForSelector(".dlg");
  check("closing with unsaved changes asks first", /Leave without saving/.test(await text(".dlg h3")));
  await p.click('.dlg button:has-text("Keep editing")'); check("Keep editing keeps the pane", await p.evaluate(() => !$("#pvp").hidden));
  fs.writeFileSync(path.join(proj, "README.txt"), "plain text file\n");

  // ---- nothing called the old routes
  const old = p.reqs.filter(r => /open-path|open-file|open-data|open-editor|company\/open/.test(r));
  check("no request went to an old open route", old.length === 0, old.join(", "));
  check("no request asked the system file manager", revealReqs() === 0);
  check("the page has no errors", p.errs.length === 0, p.errs.join("; "));

  // ---- the explicit menu item is the only way to reveal
  await p.click("#pvClose"); await p.click('.dlg button:has-text("Leave without saving")'); await p.waitForTimeout(250);
  await p.click(row("README.txt"), {button: "right"}); await p.waitForSelector("#ctx");
  await p.click('#ctx .mi:has-text("Show in")'); await p.waitForTimeout(300);
  check("choosing Show in ... asks once, for that file", revealReqs() === 1);

  // ---- phone width and dark
  const m = await mk(390, 844, "dark");
  await m.evaluate(r => openPreview(r, "src/app.py"), proj); await m.waitForSelector("#edTa", {timeout: 15000}); await m.waitForTimeout(400);
  await shot(m, "06-editor-phone-dark");
  check("no sideways scroll at phone width", await m.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  check("every button has a name", await m.evaluate(() => [...document.querySelectorAll("#pvp button")].every(b => (b.textContent || "").trim() || b.getAttribute("aria-label"))));
  await m.evaluate(() => closePreview());
  await m.evaluate(r => openFolderInApp(r, ""), proj); await m.waitForTimeout(600);
  await shot(m, "07-tree-phone-dark");
  const d = await mk(1440, 900, "dark");
  await d.evaluate(r => openPreview(r, "src/app.py"), proj); await d.waitForSelector("#edTa", {timeout: 15000}); await d.waitForTimeout(400);
  await shot(d, "08-editor-desktop-dark");
  check("no page errors (phone, dark)", m.errs.length === 0 && d.errs.length === 0, m.errs.concat(d.errs).join("; "));
  await b.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})();
