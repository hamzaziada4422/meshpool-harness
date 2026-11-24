// Settings > Plugins > Office apps: add the panel to Office from the app, then remove it.
// Usage: node testkit/office_settings.js http://127.0.0.1:8786 https://localhost:44398
const {chromium} = require("playwright");
const APP = process.argv[2] || "http://127.0.0.1:8786", PANEL = process.argv[3] || "https://localhost:44398";
let failures = 0;
const ok = (c, w) => { console.log((c ? "PASS " : "FAIL ") + w); if (!c) failures++; };
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ignoreHTTPSErrors: true, viewport: {width: 1280, height: 860}});
  const p = await ctx.newPage();
  const errors = []; p.on("pageerror", e => errors.push(String(e)));
  await p.goto(APP + "/#settings/plugins");
  await p.waitForSelector("[data-sub=office]", {timeout: 20000});
  await p.click("[data-sub=office]");
  await p.waitForSelector("text=Not added yet.");
  ok(await p.locator("#ofInstall").isVisible(), "the Add button shows before the add-in is installed");
  ok(/limited to localhost/.test(await p.innerText("#ofNote")), "it explains the certificate before the click");
  await p.click("#ofInstall");
  await p.waitForSelector("#ofRemove:visible", {timeout: 30000});
  ok(/Wrote the add-in manifest/.test(await p.innerText("#ofLines")), "install reports what it did");
  const r = await ctx.request.get(PANEL + "/office/taskpane.html");
  ok(r.ok() && /MESHPOOL_TOKEN = "/.test(await r.text()), "the panel is served over HTTPS right away, no restart");
  await p.screenshot({path: (process.env.SHOT_DIR || "/tmp") + "/office_settings.png"});
  await p.click("#ofRemove");
  await p.waitForSelector("text=Not added yet.", {timeout: 30000});
  let gone = false; try { await ctx.request.get(PANEL + "/office/taskpane.html", {timeout: 3000}); } catch (e) { gone = true; }
  ok(gone, "remove stops serving the panel");
  ok(errors.length === 0, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  await b.close();
  console.log(failures ? `${failures} failed` : "all passed");
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
