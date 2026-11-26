// The Office panel end to end, with stand-in Excel, Word and PowerPoint objects (no real Office here).
// Usage: node testkit/office_panel.js https://localhost:44399
// Needs: the mock model on 8911 and an app started with MH_OFFICE_PORT and a certificate (meshpool office install).
const {chromium} = require("playwright");
const BASE = process.argv[2] || "https://localhost:44399";

const STUB = String.raw`
(function () {
  const host = new URLSearchParams(location.search).get("host");
  if (!host) {                                        // a plain browser tab: office.js loads, but there is no host
    window.Office = {onReady(cb) { setTimeout(() => cb({host: null}), 0); return Promise.resolve({host: null}); }};
    return;
  }
  const rec = window.__rec = {inserted: [], setText: [], boxes: [], slidesAdded: 0};
  const A1 = (r, c) => { let s = "", n = c + 1; while (n) { s = String.fromCharCode(65 + (n - 1) % 26) + s; n = Math.floor((n - 1) / 26); } return s + (r + 1); };
  const book = window.__book = {sheets: {Sheet1: {"0,0": "Name", "0,1": "Qty", "1,0": "apple", "1,1": "3"}}, order: ["Sheet1"]};
  class Range {
    constructor(sh, r, c, rows, cols) { Object.assign(this, {sh, r, c, rows, cols}); this.format = {autofitColumns() {}}; }
    load() { return this; }
    get address() { return this.sh + "!" + A1(this.r, this.c) + (this.rows * this.cols > 1 ? ":" + A1(this.r + this.rows - 1, this.c + this.cols - 1) : ""); }
    get rowCount() { return this.rows; } get columnCount() { return this.cols; }
    grid() { const s = book.sheets[this.sh]; return Array.from({length: this.rows}, (_, i) => Array.from({length: this.cols}, (_, j) => s[(this.r + i) + "," + (this.c + j)] || "")); }
    get values() { return this.grid().map(r => r.map(v => String(v).startsWith("=") ? 0 : v)); }
    get formulas() { return this.grid(); }
    set formulas(m) { const s = book.sheets[this.sh]; m.forEach((row, i) => row.forEach((v, j) => { const k = (this.r + i) + "," + (this.c + j); if (v === "") delete s[k]; else s[k] = v; })); }
    getCell(i, j) { return new Range(this.sh, this.r + i, this.c + j, 1, 1); }
    getResizedRange(dr, dc) { return new Range(this.sh, this.r, this.c, this.rows + dr, this.cols + dc); }
  }
  const sheet = name => ({name, getRange(a) { return new Range(name, 0, 0, 1, 1); }, getRange2: null, activate() {}, delete() { delete book.sheets[name]; book.order = book.order.filter(x => x !== name); }});
  const sheets = {load() {}, get items() { return book.order.map(sheet); },
                  add(n) { book.sheets[n] = {}; book.order.push(n); return sheet(n); }, getItem(n) {
                    const s = sheet(n); s.getRange = a => { const m = /^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/.exec(a); const col = x => x.split("").reduce((t, ch) => t * 26 + ch.charCodeAt(0) - 64, 0) - 1;
                      const r = +m[2] - 1, c = col(m[1]); return new Range(n, r, c, m[3] ? +m[4] - r : 1, m[3] ? col(m[3]) - c + 1 : 1); }; return s; }};
  const sync = async () => {};
  window.Excel = {run: fn => fn({sync, workbook: {getSelectedRange: () => new Range("Sheet1", 0, 0, 2, 2), worksheets: sheets}})};
  const doc = window.__doc = {selection: "Teh quick fox", body: "Teh quick fox jumps."};
  window.Word = {run: fn => fn({sync, document: {getSelection: () => ({load() {}, get text() { return doc.selection; },
                  insertHtml(html, where) { rec.inserted.push({html, where}); }}), body: {load() {}, get text() { return doc.body; }}}})};
  const slides = [{shapes: {load() {}, items: [], addTextBox(t, o) { rec.boxes.push(t); }}}];
  window.PowerPoint = {run: fn => fn({sync, presentation: {slides: {add() { rec.slidesAdded++; slides.push({shapes: {load() {}, items: [], addTextBox(t) { rec.boxes.push(t); }}}); }, load() {}, get items() { return slides; }}}})};
  window.Office = {
    HostType: {}, CoercionType: {Text: "text"}, AsyncResultStatus: {Succeeded: "succeeded"}, EventType: {DocumentSelectionChanged: "sel"},
    onReady(cb) { setTimeout(() => cb({host}), 0); return Promise.resolve({host}); },
    context: {requirements: {isSetSupported: () => true}, document: {
      addHandlerAsync() {}, getSelectedDataAsync(t, cb) { cb({status: "succeeded", value: host === "PowerPoint" ? "Old title" : ""}); },
      setSelectedDataAsync(text, o, cb) { rec.setText.push(text); cb({status: "succeeded"}); }}},
  };
})();`;

let failures = 0, PAGE = null;
const ok = (cond, what) => { console.log((cond ? "PASS " : "FAIL ") + what); if (!cond) failures++; };
const sent = [];                                       // what the panel sent to meshpool, message by message
const lastRequest = () => sent[sent.length - 1] || "";

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 360, height: 720}});
  await ctx.route("https://appsforoffice.microsoft.com/**", r => r.fulfill({contentType: "text/javascript", body: STUB}));
  const page = PAGE = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  page.on("request", r => { if (/\/api\/agent\/[\w-]+\/send$/.test(r.url())) sent.push(JSON.parse(r.postData() || "{}").text || ""); });

  // ---------------- Excel
  await page.goto(BASE + "/office/taskpane.html?host=Excel");
  await page.waitForFunction(() => /Sheet1!A1:B2/.test(document.getElementById("ctxText").textContent));
  ok(true, "Excel: the panel shows the selected range");
  ok(await page.locator("#quick button").count() === 4, "Excel: four quick actions");
  await page.fill("#inp", "office table demo");
  await page.press("#inp", "Enter");
  await page.waitForSelector("text=Insert at selection", {timeout: 30000});
  ok(await page.locator(".msg.bot table td").count() >= 6, "Excel: the reply's table is shown as a table");
  const req = lastRequest();
  ok(/apple\t3/.test(req) && /<selection>/.test(req) && /I am in Excel/.test(req), "Excel: the selection went to meshpool with the request");
  await page.click("text=Insert at selection");
  await page.waitForSelector("text=Inserted 3 × 3 at Sheet1!A1:C3");
  let cells = await page.evaluate(() => window.__book.sheets.Sheet1);
  ok(cells["0,0"] === "Region" && cells["1,2"] === "=B2/SUM(B$2:B$3)" && cells["2,1"] === "80", "Excel: values and formulas written at the active cell");
  await page.click("text=Undo insert");
  await page.waitForSelector("text=Put back what was in Sheet1!A1:C3");
  cells = await page.evaluate(() => window.__book.sheets.Sheet1);
  ok(cells["0,0"] === "Name" && cells["1,1"] === "3" && !cells["1,2"] && !cells["2,0"], "Excel: Undo insert puts back exactly what was there");
  await page.click("text=Insert in a new sheet");
  await page.waitForSelector("text=Added the sheet meshpool.");
  ok(await page.evaluate(() => window.__book.sheets.meshpool["0,0"] === "Region"), "Excel: insert in a new sheet");
  await page.uncheck("#useSel");
  await page.fill("#inp", "office question demo");
  await page.press("#inp", "Enter");
  await page.waitForSelector("text=Which quarter?", {timeout: 30000});
  ok(!/<selection>/.test(lastRequest()), "Excel: with the box unticked, no selection is sent");
  await page.fill("#inp", "Q2");
  await page.press("#inp", "Enter");
  await page.waitForFunction(() => document.querySelectorAll(".msg.bot").length >= 2, null, {timeout: 30000});
  ok(true, "Excel: answering meshpool's question in the panel continues the task");
  await page.fill("#inp", "office question demo");
  await page.press("#inp", "Enter");
  await page.waitForSelector("text=Which quarter?", {timeout: 30000});
  const stops = [];
  page.on("request", r => { if (/\/stop$/.test(r.url())) stops.push(r.url()); });
  await page.click("#newChat");
  await page.waitForTimeout(500);
  ok(await page.locator(".msg").count() === 0 && stops.length === 1, "New chat during a turn stops it and clears the panel");

  // ---------------- Word
  await page.goto(BASE + "/office/taskpane.html?host=Word");
  await page.waitForFunction(() => /3 words selected/.test(document.getElementById("ctxText").textContent));
  ok(true, "Word: the panel counts the selected words");
  await page.fill("#inp", "office slide demo");
  await page.press("#inp", "Enter");
  await page.waitForSelector("text=Replace selection", {timeout: 30000});
  ok(/Teh quick/.test(lastRequest()), "Word: the selected text went with the request");
  await page.click("text=Replace selection");
  await page.waitForSelector("text=Replaced the selection.");
  let rec = await page.evaluate(() => window.__rec);
  ok(rec.inserted[0].where === "Replace" && /<strong>12%<\/strong>/.test(rec.inserted[0].html) && /<li>/.test(rec.inserted[0].html), "Word: replaced with formatted text (bold, list)");
  await page.click("text=Insert after");
  await page.waitForSelector("text=Inserted after the selection.");
  ok((await page.evaluate(() => window.__rec.inserted[1].where)) === "After", "Word: insert after");

  // ---------------- PowerPoint
  await page.goto(BASE + "/office/taskpane.html?host=PowerPoint");
  await page.waitForFunction(() => /selected text/.test(document.getElementById("ctxText").textContent));
  await page.fill("#inp", "office slide demo");
  await page.press("#inp", "Enter");
  await page.waitForSelector("text=New slide", {timeout: 30000});
  await page.click("text=New slide");
  await page.waitForSelector("text=Added slide 2.");
  rec = await page.evaluate(() => window.__rec);
  ok(rec.slidesAdded === 1 && rec.boxes[0] === "Quarterly results" && /• Sales grew 12%/.test(rec.boxes[1]), "PowerPoint: new slide with the title and bullets");
  await page.click("text=Insert on slide");
  await page.waitForSelector("text=Inserted on the slide.");
  ok(/^Quarterly results\n• Sales grew 12%/.test((await page.evaluate(() => window.__rec.setText[0]))), "PowerPoint: insert plain text on the slide");

  // ---------------- outside Office
  await page.goto(BASE + "/office/taskpane.html");
  ok(await page.locator("text=Open this from Office").count() === 1, "Outside Office the page says how to open it");
  await ctx.route("**/office/taskpane.js", r => r.fulfill({contentType: "text/javascript", body: "const a = {}?.b ?? ; // too new for old Office"}));
  await page.goto(BASE + "/office/taskpane.html?host=Excel");
  await page.waitForTimeout(2000);
  ok(await page.locator("text=too old for meshpool").count() === 1, "An Office too old to run the panel says so");
  errors.length = 0;                                  // that page's syntax error was the point
  ok(errors.length === 0, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  await page.screenshot({path: (process.env.SHOT_DIR || "/tmp") + "/office_panel.png"});
  await browser.close();
  console.log(failures ? `${failures} failed` : "all passed");
  process.exit(failures ? 1 : 0);
})().catch(async e => {
  console.error(e.message.split("\n")[0]);
  try { console.error("Panel text: " + (await PAGE.innerText("body")).slice(-800)); } catch (x) { /* no page */ }
  process.exit(1);
});
