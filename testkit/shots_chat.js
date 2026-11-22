// A chat on the mock model: sources chip, receipt, X-ray, incognito. Usage: node shots_chat.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const errs = []; const res = [];
  const ck = (n, ok, d = "") => { res.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + d)); };
  const p = await b.newPage({viewport: {width: 1280, height: 860}});
  p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/favicon/.test(m.text())) errs.push(m.text()); });
  await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
  for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(600); }
  await p.evaluate(() => newThread());
  await p.fill("#input", "Make a one slide summary of our Q3 results"); await p.keyboard.press("Enter");
  await p.waitForSelector(".turnfoot", {timeout: 60000}); await p.waitForTimeout(2500);
  ck("sources chip under the reply", await p.$(".mem-srcchip") !== null);
  ck("receipt line", await p.$(".rcpt010") !== null);
  await p.screenshot({path: `${out}/chat-turn-end-light.png`});
  if (await p.$(".mem-srcchip")) { await p.click(".mem-srcchip"); await p.waitForTimeout(400); await p.screenshot({path: `${out}/chat-sources-light.png`}); await p.keyboard.press("Escape"); }
  await p.click("#xrayBtn"); await p.waitForTimeout(500); ck("x-ray opens", await p.$(".mem-pop.xray") !== null);
  await p.screenshot({path: `${out}/chat-xray-light.png`}); await p.keyboard.press("Escape");
  // incognito
  await p.keyboard.press("Control+Shift+N"); await p.waitForTimeout(500);
  ck("incognito toggled by shortcut", (await p.evaluate(() => S.draft.incognito)) === true);
  await p.fill("#input", "Please remember that I like green charts"); await p.keyboard.press("Enter");
  await p.waitForSelector(".turnfoot", {timeout: 60000}); await p.waitForTimeout(1500);
  ck("incognito tag in the header", await p.$(".incog-tag") !== null);
  ck("no sources chip in an incognito chat", await p.$(".mem-srcchip") === null);
  await p.screenshot({path: `${out}/chat-incognito-light.png`});
  console.log(errs.length ? "ERRORS " + errs.join(" | ") : "no page errors"); await b.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})();
