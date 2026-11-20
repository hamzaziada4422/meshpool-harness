// In-app previews, driven like a person would: a turn makes a deck, a workbook, a PDF and a page; cards show under the
// reply; every kind opens in the viewer pane; Esc closes it; code blocks get a Preview button; light, dark and narrow.
// Usage: python3 testkit/make_preview_fixtures.py FIXDIR
//        MOCK_PREVIEW_DIR=FIXDIR python3 testkit/mock_llm_long.py 8911 /tmp/mock.jsonl &   (and an app on PORT with MESHPOOL_HOME
//        pointing at the mock, MH_ALLOW_SYSTEM_SOFFICE=1 for Office files; see run_all.sh)
//        node testkit/ux_previews.js PORT PROJECT_DIR SHOT_DIR      (PROJECT_DIR: an empty folder)
const {chromium} = require("playwright");
const fs = require("fs");
const [port, proj, shots] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? "  " + detail : "")); };
fs.mkdirSync(shots, {recursive: true});

(async () => {
  const b = await chromium.launch();
  const mk = async (theme, w = 1440, h = 900) => {
    const ctx = await b.newContext({viewport: {width: w, height: h}, deviceScaleFactor: 2});
    const p = await ctx.newPage();
    p.errs = []; p.on("pageerror", e => p.errs.push(e.message));
    await p.addInitScript(t => { try { localStorage.setItem("mp_permsAsked", "1"); localStorage.setItem("mp_theme", JSON.stringify(t)); localStorage.setItem("mp_cards_off", '["key","github","project"]'); } catch (e) {} }, theme);
    await p.goto(BASE + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(1000);
    return p;
  };
  const shot = (p, name) => p.screenshot({path: `${shots}/${name}.png`});
  const p = await mk("light");
  const opened = []; await p.route("**/api/open-file", r => { opened.push(r.request().postData()); r.fulfill({status: 200, contentType: "application/json", body: '{"ok":true}'}); });
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  const viewerReady = () => p.waitForFunction(() => { const i = $("#pvStage .pv-pg"); return i && i.complete && i.naturalWidth > 0; }, null, {timeout: 120000});

  // ---- a turn that makes files
  await api("/api/projects", {action: "add", path: proj}); await p.evaluate(() => loadProjects());
  await p.evaluate(p => { S.draft.project = p; S.draft.mode = "auto"; renderBar(); }, proj);
  await p.fill("#input", "make previews please"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-ai", {timeout: 60000});
  const id = await p.evaluate(() => S.cur.id);
  await p.waitForFunction(id => api("/api/agent/" + id + "?since=0").then(v => v.state === "idle"), id, {timeout: 120000, polling: 1500});
  await p.waitForSelector(".pvcards .pvcard", {timeout: 30000});
  await p.waitForTimeout(500);
  const names = await p.$$eval(".pvcards .pvcard b", e => e.map(x => x.textContent).sort());
  check("one card per file the turn made", names.join("|") === "deck.pptx|model.xlsx|page.html|report.pdf", names.join("|"));
  check("cards sit after the reply's footer, once", (await p.$$(".pvcards")).length === 1 && await p.evaluate(() => $(".pvcards").previousElementSibling.classList.contains("turnfoot")));
  check("a card says what the file is", /Deck/.test(await p.textContent('.pvcard[data-pv="deck.pptx"]')) && /Spreadsheet/.test(await p.textContent('.pvcard[data-pv="model.xlsx"]')));
  await p.waitForSelector('.pvcard[data-pv="deck.pptx"] .pvth img', {timeout: 90000});
  await p.waitForSelector('.pvcard[data-pv="report.pdf"] .pvth img', {timeout: 60000});
  check("decks and PDFs show a thumbnail of page 1", true);
  await shot(p, "01-chat-cards-light");

  // ---- deck
  await p.click('.pvcard[data-pv="deck.pptx"]');
  await p.waitForSelector("#pvp:not([hidden])"); await viewerReady();
  check("the viewer opens beside the chat (chat still visible)", await p.evaluate(() => { const a = $("#pvp").getBoundingClientRect(), m = $(".main").getBoundingClientRect(); return a.width > 440 && m.width > 300 && a.left >= m.right - 1; }));
  check("it shows the file name and type", /deck\.pptx/.test(await p.textContent("#pvName")) && /Deck/.test(await p.textContent("#pvType")));
  check("a deck opens with a strip of 5 slides", (await p.$$("#pvThumbs .pv-th")).length === 5 && /1 \/ 5/.test(await p.textContent("#pvCount")));
  check("the side panel is replaced while it is open", await p.evaluate(() => getComputedStyle($("#panel")).display === "none"));
  await p.waitForTimeout(400); await shot(p, "02-deck-slide1");
  await p.click("#pvNext"); await viewerReady();
  check("the arrow goes to slide 2", /2 \/ 5/.test(await p.textContent("#pvCount")));
  await p.focus("#pvp"); await p.keyboard.press("ArrowRight"); await p.keyboard.press("ArrowRight");
  await p.waitForTimeout(300);
  check("arrow keys move through slides", /4 \/ 5/.test(await p.textContent("#pvCount")));
  await p.click('#pvThumbs .pv-th[data-i="1"]'); await viewerReady();
  check("clicking a thumbnail jumps there", /2 \/ 5/.test(await p.textContent("#pvCount")));
  await shot(p, "03-deck-slide2");
  check("Open in PowerPoint is offered", /Open in PowerPoint/.test(await p.textContent("#pvOpen")));
  await p.click("#pvOpen"); await p.waitForTimeout(300);
  check("it uses the existing open-file call", opened.length === 1 && /deck\.pptx/.test(opened[0]));
  await p.keyboard.press("Escape"); await p.waitForTimeout(250);
  check("Esc closes the viewer and brings the panel back", await p.evaluate(() => $("#pvp").hidden && !$("#app").classList.contains("pvopen")));

  // ---- workbook
  await p.click('.pvcard[data-pv="model.xlsx"]'); await p.waitForSelector(".pv-sheet table");
  check("a workbook shows its sheet as a table", (await p.$$(".pv-sheet tbody tr")).length === 5 && /North/.test(await p.textContent(".pv-sheet")));
  check("formulas show their values", /1200/.test(await p.textContent(".pv-sheet")) || /\d{3,}/.test(await p.textContent(".pv-sheet tbody tr:nth-child(2)")));
  check("sheet tabs for each sheet", (await p.$$eval("#pvTabs button", e => e.map(x => x.textContent))).join("|") === "Revenue|Costs");
  await shot(p, "04-xlsx");
  await p.click('#pvTabs button:nth-child(2)'); await p.waitForTimeout(200);
  check("another tab shows that sheet", /Hosting/.test(await p.textContent(".pv-sheet")));
  await p.keyboard.press("Escape");

  // ---- pdf
  await p.click('.pvcard[data-pv="report.pdf"]'); await viewerReady();
  check("a PDF opens as pages", (await p.$$("#pvThumbs .pv-th")).length >= 1 && !(await p.$("#pvSeg:not([hidden])")));
  await shot(p, "05-pdf");
  await p.keyboard.press("Escape");

  // ---- html (sandboxed), markdown, svg, image
  await p.click('.pvcard[data-pv="page.html"]'); await p.waitForSelector("iframe.pv-frame");
  check("HTML runs in a sandbox with scripts only", await p.getAttribute("iframe.pv-frame", "sandbox") === "allow-scripts");
  const fr = p.frameLocator("iframe.pv-frame");
  await fr.locator("h1").waitFor(); await fr.locator("button, h1").first().waitFor();
  check("the page is drawn", /Made page|Hello/.test(await fr.locator("h1").textContent()));
  check("the page cannot reach the app (opaque origin)", await p.evaluate(() => { try { return $("iframe.pv-frame").contentDocument === null; } catch (e) { return true; } }));
  await shot(p, "06-html");
  await p.click('#pvSeg [data-v="code"]'); await p.waitForSelector(".pv-code");
  check("the Code toggle shows the source", /<h1>/.test(await p.textContent(".pv-code")));
  await p.click('#pvSeg [data-v="view"]'); await p.waitForSelector("iframe.pv-frame");
  await p.keyboard.press("Escape");
  // files that were already in the folder, opened from a link in the reply and the Artifacts list
  fs.copyFileSync(`${process.env.MOCK_PREVIEW_DIR || ""}/notes.md`, `${proj}/notes.md`); fs.copyFileSync(`${process.env.MOCK_PREVIEW_DIR || ""}/logo.svg`, `${proj}/logo.svg`);
  await p.evaluate(() => { const d = document.createElement("p"); d.id = "fl"; d.innerHTML = linkFiles(esc(curRoot() + "/notes.md")) + " " + linkFiles(esc(curRoot() + "/logo.svg")); $("#col").appendChild(d); });
  await p.click('#fl .flink[data-open="notes.md"]'); await p.waitForSelector(".pv-md h3");
  check("a file link in chat text opens the viewer (Markdown rendered)", /Notes/.test(await p.textContent(".pv-md h3")) && await p.evaluate(() => $$("#pvp .pv-md li").length === 2));
  await shot(p, "07-markdown");
  await p.keyboard.press("Escape");
  await p.click('#fl .flink[data-open="logo.svg"]'); await p.waitForSelector(".pv-img img");
  check("SVG is an image from a blob, not markup", await p.evaluate(() => /^blob:/.test($(".pv-img img").src) && !$("#pvp svg:not(.iconbtn svg)")?.closest(".pv-stage")));
  await shot(p, "08-svg");
  await p.keyboard.press("Escape");

  // ---- code blocks in a reply
  const btns = await p.$$eval(".msg-ai .pvbtn", e => e.map(x => x.dataset.pvcode));
  check("html and svg code blocks get a Preview button", btns.join("|") === "html|svg", btns.join("|"));
  await p.click('.msg-ai .pvbtn[data-pvcode="html"]'); await p.waitForSelector("iframe.pv-frame");
  check("Preview on html code opens it with no file", await p.evaluate(() => /Snippet page/.test($("iframe.pv-frame").srcdoc) && $("#pvOpen").hidden));
  await fr.locator("button").click(); await p.waitForTimeout(150);
  check("the snippet's script runs inside the sandbox", /ran/.test(await fr.locator("button").textContent()));
  await shot(p, "09-snippet-html");
  await p.keyboard.press("Escape");
  await p.click('.msg-ai .pvbtn[data-pvcode="svg"]'); await p.waitForSelector(".pv-img img");
  check("Preview on svg code shows the image", await p.evaluate(() => /^blob:/.test($(".pv-img img").src)));
  await p.keyboard.press("Escape");
  check("no Preview button on python code", (await p.$$(".msg-ai pre")).length >= 0 && !(await p.$('.codewrap [data-pvcode="python"]')));

  // ---- the Artifacts list in the Session panel opens the viewer, not the OS app
  const before = opened.length;
  await p.evaluate(() => togglePanel("session", true)); await p.waitForSelector('.spw [data-open]', {timeout: 15000});
  await p.waitForFunction(() => { const d = $('details.spsec[data-k="out"]'); if (d && !d.open) d.open = true; return $$(".spw .outs [data-open]").length > 0; }, null, {timeout: 15000});
  await p.click('.spw .outs [data-open="deck.pptx"]'); await p.waitForSelector("#pvp:not([hidden])");
  check("a Session panel artifact opens in the viewer, not the OS app", opened.length === before && /deck\.pptx/.test(await p.textContent("#pvName")));
  await p.keyboard.press("Escape");

  // ---- a path outside the project, a missing file, a binary
  await p.evaluate(() => openPreview(curRoot(), "../outside.txt")); await p.waitForFunction(() => /outside|gone|not/i.test($(".pv-note")?.textContent || ""), null, {timeout: 10000}).catch(() => {});
  check("a path outside the project shows an error, not the file", /outside/i.test(await p.textContent(".pv-note")));
  await p.keyboard.press("Escape");

  // ---- dark and narrow
  check("no page errors (light)", p.errs.length === 0, p.errs.join("; "));
  const d = await mk("dark");
  await d.route("**/api/open-file", r => r.fulfill({status: 200, contentType: "application/json", body: '{"ok":true}'}));
  await d.evaluate(([pr, id]) => { return loadThreads().then(() => openThread(id)); }, [proj, id]);
  await d.waitForSelector(".pvcards .pvcard", {timeout: 30000}); await d.waitForTimeout(1500);
  await shot(d, "10-chat-cards-dark");
  await d.click('.pvcard[data-pv="deck.pptx"]');
  await d.waitForFunction(() => { const i = $("#pvStage .pv-pg"); return i && i.complete && i.naturalWidth > 0; }, null, {timeout: 60000});
  await d.waitForTimeout(500); await shot(d, "11-deck-dark");
  check("dark theme: the pane uses the theme's background", await d.evaluate(() => getComputedStyle($("#pvp")).backgroundColor !== "rgb(255, 255, 255)"));
  await d.keyboard.press("Escape");
  await d.click('.pvcard[data-pv="model.xlsx"]'); await d.waitForSelector(".pv-sheet table"); await shot(d, "12-xlsx-dark");
  check("no page errors (dark)", d.errs.length === 0, d.errs.join("; "));

  const n = await mk("light", 390, 800);
  await n.evaluate(id => loadThreads().then(() => openThread(id)), id);
  await n.waitForSelector(".pvcards .pvcard", {timeout: 30000}); await n.waitForTimeout(800);
  await shot(n, "13-narrow-chat");
  await n.click('.pvcard[data-pv="deck.pptx"]');
  await n.waitForFunction(() => { const i = $("#pvStage .pv-pg"); return i && i.complete && i.naturalWidth > 0; }, null, {timeout: 60000});
  await n.waitForTimeout(500); await shot(n, "14-narrow-deck");
  check("narrow: the pane fills the screen and nothing scrolls sideways", await n.evaluate(() => { const r = $("#pvp").getBoundingClientRect(); return r.width >= innerWidth - 1 && document.documentElement.scrollWidth <= innerWidth; }));
  check("narrow: the Open and close buttons stay on screen", await n.evaluate(() => { const a = $("#pvOpen").getBoundingClientRect(), c = $("#pvClose").getBoundingClientRect(); return a.right <= innerWidth && c.right <= innerWidth && a.left >= 0; }));
  await n.click("#pvClose"); await n.waitForTimeout(200);
  check("narrow: the close button closes it", await n.evaluate(() => $("#pvp").hidden));
  check("no page errors (narrow)", n.errs.length === 0, n.errs.join("; "));

  await b.close();
  const ok = results.filter(Boolean).length;
  console.log(`${ok}/${results.length} passed`);
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.log("FAIL crashed: " + (e.stack || e)); process.exit(2); });
