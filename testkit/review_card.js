// The independent check card under a big task's reply, and the "Check this" action. Desktop and phone, light and dark.
// Start the app first:  testkit/dev_app.sh PORT HOME   (the mock model answers as the reviewer too)
// Usage: node testkit/review_card.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const errs = []; const res = [];
  const ck = (n, ok, d = "") => { res.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + d)); };
  for (const [vname, vp] of [["desktop", {width: 1280, height: 860}], ["phone", {width: 390, height: 780}]]) {
    for (const theme of ["light", "dark"]) {
      const p = await b.newPage({viewport: vp});
      p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/favicon/.test(m.text())) errs.push(m.text()); });
      await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
      if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
      for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(600); }
      await p.evaluate(() => newThread());
      await p.fill("#input", "big job demo: make a 3 slide deck with problem, solution and pricing"); await p.keyboard.press("Enter");
      await p.waitForSelector(".rev010.rv-pass", {state: "attached", timeout: 90000}); await p.waitForSelector(".turnfoot", {state: "attached", timeout: 30000});
      await p.waitForTimeout(800);
      const cards = await p.$$(".rev010");
      ck(`${vname}/${theme}: two cards (found open items, then passed)`, cards.length === 2, String(cards.length));
      ck(`${vname}/${theme}: first card says what is open`, /1 of 4|Some things are still open|Independent check/.test(await cards[0].innerText()));
      const last = cards[cards.length - 1];
      ck(`${vname}/${theme}: last card is a pass in plain words`, /all 4 requirements met/.test(await last.innerText()), await last.innerText());
      ck(`${vname}/${theme}: summary is keyboard focusable`, await last.$eval("summary", s => s.tabIndex >= 0 || s.tagName === "SUMMARY"));
      await p.screenshot({path: `${out}/review-${vname}-${theme}-collapsed.png`});
      await cards[0].$eval("summary", s => s.click()); await last.$eval("summary", s => s.click()); await p.waitForTimeout(300);
      ck(`${vname}/${theme}: requirement list opens`, (await last.$$("li")).length === 4);
      ck(`${vname}/${theme}: status is words, not only colour`, /Met/.test(await last.innerText()) && /Not done/.test(await cards[0].innerText()));
      await cards[0].scrollIntoViewIfNeeded();
      await p.screenshot({path: `${out}/review-${vname}-${theme}-open.png`});
      ck(`${vname}/${theme}: no sideways scroll`, await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      // Check this: on demand, works any time the chat is idle
      const btn = await p.$('[data-ra="check"]');
      ck(`${vname}/${theme}: "Check this" button is there with a name`, !!btn && !!(await btn.getAttribute("aria-label")));
      if (btn && theme === "light") {
        await btn.click(); await p.waitForFunction(() => document.querySelectorAll(".rev010").length >= 3, null, {timeout: 60000});
        const n = (await p.$$(".rev010")).length;
        ck(`${vname}/${theme}: Check this adds a card`, n >= 3, String(n));
      }
      await p.close();
    }
  }
  console.log(errs.length ? "ERRORS " + errs.join(" | ") : "no page errors"); await b.close();
  process.exit(res.every(Boolean) && !errs.length ? 0 : 1);
})();
