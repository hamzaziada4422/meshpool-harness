/* The Browser panel, driven like a user: a page served on this computer opens in the app's own browser, shows in the
   panel as pictures, and clicks, scrolling, typing and the address bar reach the page. No other window opens.
   Start the app first:  testkit/dev_app.sh 8790 /tmp/mp-bp-home      Run:  node testkit/browser_panel.js 8790 [out_dir]
   Exit code 0 when every check passes. */
const http = require("http");
const {chromium} = require("playwright");

const PORT = process.argv[2] || "8790", OUT = process.argv[3] || "/tmp";
const PAGE = `<title>start</title><body style="margin:0;height:5000px">
<button id=b style="position:absolute;left:100px;top:100px;width:160px;height:50px" onclick="document.title='clicked'">Click me</button>
<input id=t style="position:absolute;left:100px;top:200px;width:300px;height:36px" oninput="document.title='typed:'+this.value">
<script>addEventListener('scroll',()=>{ if (scrollY > 100) document.title='scrolled' })</script></body>`;
const OTHER = `<title>other page</title><h1>Other</h1>`;

const results = [];
const check = (name, ok, extra) => { results.push(ok); console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : "  " + (extra || ""))); };

(async () => {
  const site = http.createServer((q, r) => { r.setHeader("Content-Type", "text/html"); r.end(q.url.startsWith("/other") ? OTHER : PAGE); });
  await new Promise(ok => site.listen(0, "127.0.0.1", ok));
  const base = `http://127.0.0.1:${site.address().port}`;
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(process.env.BP_STACK ? e.stack : e.message)); p.on("console", m => { if (m.type() === "error" && !/status of 400/.test(m.text())) errs.push(m.text()); });   // the 400 is the refused file address
  await p.goto(`http://127.0.0.1:${PORT}/`); await p.waitForSelector(".hero, #app");
  await p.waitForTimeout(1500); await p.evaluate(() => document.querySelector("#onb")?.remove());   // the first-run welcome covers the page

  // the sign-in route opens a page in the app's own browser and the panel shows it
  await p.evaluate(async u => { await post("/api/browser/open", {url: u}); showSignIn(); }, base);
  await p.waitForSelector("#bImg:not([hidden])", {timeout: 60000});
  check("panel is open with the page", await p.locator("#panel:not([hidden]) #bStage").count() === 1);
  await p.waitForFunction(() => BRW.info && BRW.info.title === "start", null, {timeout: 20000});
  check("address bar shows the page address", (await p.inputValue("#bUrl")).startsWith(base));
  check("the image is a live picture", await p.evaluate(() => document.querySelector("#bImg").src.startsWith("blob:")));
  const title = () => p.evaluate(async () => (await api(`/api/browser/state?src=signin&chat=`)).title);
  const until = async (want, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await title() === want) return true; await p.waitForTimeout(150); } return false; };

  // clicking the picture clicks the page: the picture is scaled, so the point maps back to page pixels
  const at = async (x, y) => { const r = await p.locator("#bImg").boundingBox(); const w = await p.evaluate(() => BRW.info.width); const k = r.width / w; return {x: r.x + x * k, y: r.y + y * k}; };
  let pt = await at(180, 125); await p.mouse.click(pt.x, pt.y);
  check("a click reaches the page", await until("clicked"));
  pt = await at(200, 218); await p.mouse.click(pt.x, pt.y); await p.keyboard.type("hello 42");
  check("typing reaches the page", await until("typed:hello 42"));
  await p.keyboard.press("Backspace");
  check("a key press reaches the page", await until("typed:hello 4"));
  pt = await at(600, 400); await p.mouse.move(pt.x, pt.y); await p.mouse.wheel(0, 600);
  check("scrolling reaches the page", await until("scrolled"));
  await p.fill("#bUrl", `${base}/other`); await p.keyboard.press("Enter");
  check("the address bar goes to a new page", await until("other page"));
  await p.fill("#bUrl", "file:///etc/passwd"); await p.keyboard.press("Enter");
  await p.waitForSelector(".toast", {timeout: 5000}).catch(() => {});
  check("a file address is refused", await title() === "other page");
  await p.screenshot({path: `${OUT}/browser-panel.png`});

  // Done closes the sign-in browser and the panel
  await p.click("#bDone");
  await p.waitForFunction(() => $("#panel")?.hidden !== false || !$("#bStage"), null, {timeout: 10000});
  check("Done closes the panel", await p.evaluate(() => $("#panel").hidden));

  // the real path of a sign-in started from Settings: the server opens the link (/api/open-url), the answer says
  // "inapp", and the panel steps in front of Settings; Done brings Settings back
  await p.evaluate(() => openSettings("connectors")); await p.waitForSelector("#cnl", {timeout: 15000});
  await p.evaluate(async u => { await post("/api/open-url", {url: u}); }, base);
  await p.waitForSelector("#bStage", {timeout: 20000});
  check("from Settings: Settings steps aside and the panel shows the page", await p.evaluate(() => !document.querySelector(".scrim:not(.dlg-scrim)") && !$("#panel").hidden));
  await p.waitForFunction(() => BRW.info && BRW.info.title === "start", null, {timeout: 30000});
  await p.click("#bDone");
  await p.waitForSelector("#cnl", {timeout: 15000});
  check("Done brings Settings back", true);
  await p.evaluate(() => closeSettings());

  // the chat's own browser: nothing running yet gives a plain note, and the panel opens once, quietly
  await p.evaluate(() => { BRW.src = "chat"; togglePanel("browser", true); });
  await p.waitForFunction(() => /Nothing is open yet/.test($("#bNote")?.textContent || ""), null, {timeout: 15000}).catch(() => {});
  check("no browser yet: a plain note", /Nothing is open yet/.test(await p.textContent("#bNote")));
  await p.evaluate(() => { togglePanel("browser"); S.cur = {id: "t-1", view: {kind: "cowork"}, log: [], stage: {}, q: []}; browserAutoOpen(S.cur); });
  check("the panel opens by itself on the first page", await p.evaluate(() => S.panel === "browser"));
  await p.evaluate(() => { togglePanel("browser"); browserAutoOpen(S.cur); });
  check("and only once", await p.evaluate(() => S.panel !== "browser"));

  // Settings: the show-in-a-window option exists and is off
  await p.evaluate(() => { S.cur = null; S.panel = null; renderPanel(); });
  await p.evaluate(() => openSettings("caps")); await p.waitForSelector("#capShow", {timeout: 10000});
  check("Settings has 'Show the browser in its own window', off", (await p.inputValue("#capShow")) === "0");

  check("no page errors", errs.length === 0, errs.join(" | "));
  await b.close(); site.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch(e => { console.error("FAIL", e.message); process.exit(1); });
