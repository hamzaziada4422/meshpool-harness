// Reply actions: copy, try again, thumbs, and edit-and-resend that rewinds. Usage: node chat_actions.js PORT
const {chromium} = require("playwright");
const port = process.argv[2];
const res = []; const ck = (n, ok, d = "") => { res.push(!!ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + String(d).slice(0, 200))); };
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({viewport: {width: 1280, height: 860}, permissions: ["clipboard-read", "clipboard-write"]});
  const p = await ctx.newPage(); const errs = []; const posts = [];
  p.on("pageerror", e => errs.push(e.message));
  p.on("request", r => { if (r.url().includes("/api/receipt/feedback")) posts.push(r.postData()); });
  await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
  for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
  await p.evaluate(() => newThread());
  const send = async t => { await p.fill("#input", t); await p.keyboard.press("Enter"); await p.waitForFunction(() => S.cur && !S.cur.busy && document.querySelectorAll(".turnfoot").length >= 1, null, {timeout: 40000}); await p.waitForTimeout(1500); };
  await send("Make a one slide summary of our Q3 results");
  ck("copy, retry and thumbs appear under the reply", (await p.$$(".racts010 [data-ra]")).length === 4);
  await p.hover(".turnfoot"); await p.click('[data-ra="copy"]'); await p.waitForTimeout(300);
  ck("copy puts the reply on the clipboard", /mock model/i.test(await p.evaluate(() => navigator.clipboard.readText())));
  await p.click('[data-ra="up"]'); await p.waitForTimeout(300);
  ck("thumbs up is recorded", posts.some(x => /"up"/.test(x)) && (await p.getAttribute('[data-ra="up"]', "aria-pressed")) === "true");
  await p.click('[data-ra="down"]'); await p.waitForTimeout(300);
  ck("thumbs down replaces it", posts.some(x => /"down"/.test(x)) && (await p.getAttribute('[data-ra="up"]', "aria-pressed")) === "false");
  const before = await p.$$eval(".msg-ai", e => e.length);
  await p.click('[data-ra="retry"]');
  await p.waitForFunction(() => S.cur && !S.cur.busy, null, {timeout: 40000}); await p.waitForTimeout(2500);
  ck("try again replaces the reply (one question, one reply)", (await p.$$eval(".msg-user", e => e.length)) === 1 && (await p.$$eval(".msg-ai", e => e.length)) === before, `${await p.$$eval(".msg-user", e => e.length)} asks, ${await p.$$eval(".msg-ai", e => e.length)} replies`);
  ck("try again was recorded", posts.some(x => /"retry"/.test(x)));
  await send("And a second question about the margins");
  await p.hover(".msg-user >> nth=1"); await p.click('.msg-user >> nth=1 >> [data-ma="edit"]'); await p.waitForTimeout(1800);
  const askedLeft = await p.$$eval(".msg-user", e => e.length), box = await p.inputValue("#input");
  ck("edit on the last message rewinds to it and fills the box", askedLeft === 1 && /second question/.test(box), `${askedLeft} asks, box="${box}"`);
  ck("no page errors", errs.length === 0, errs.join("|"));
  await b.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})();
