// The teach suggestion on the home screen. Usage: node shots_teach.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const errs = [];
  for (const theme of ["light", "dark"]) {
    const p = await b.newPage({viewport: {width: 1280, height: 860}});
    p.on("pageerror", e => errs.push(e.message));
    await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await p.evaluate(() => newThread()); await p.waitForTimeout(2500);
    const t = await p.textContent(".nudge010").catch(() => "");
    console.log((/defaults|sentence/i.test(t) ? "PASS " : "FAIL ") + "teach nudge text: " + t.slice(0, 120));
    await p.screenshot({path: `${out}/teach-nudge-${theme}.png`});
    await p.close();
  }
  console.log(errs.length ? "ERRORS " + errs.join("|") : "no page errors"); await b.close();
})();
