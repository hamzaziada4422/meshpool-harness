// Screenshots and checks of the memory page. Usage: node shots_memory.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const errs = [];
  for (const theme of ["light", "dark"]) {
    const p = await b.newPage({viewport: {width: 1280, height: 860}, deviceScaleFactor: 1});
    p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/favicon/.test(m.text())) errs.push(m.text()); });
    await p.addInitScript(t => { try { localStorage.setItem("mp_onboarded", "true"); } catch (e) {} }, theme);
    await p.goto(`http://127.0.0.1:${port}/`);
    await p.waitForTimeout(1200);
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Get started"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(600); }
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await p.evaluate(() => goPage("memory")); await p.waitForSelector(".mem-row", {timeout: 8000});
    await p.waitForTimeout(800);
    await p.screenshot({path: `${out}/memory-${theme}.png`});
    await p.evaluate(() => { document.querySelector("#conv").scrollTop = 760; }); await p.waitForTimeout(300);
    await p.screenshot({path: `${out}/memory-list-${theme}.png`});
    await p.evaluate(() => { document.querySelector("#conv").scrollTop = 0; });
    {
      await p.click('[data-mtab="projects"]'); await p.waitForTimeout(300); await p.screenshot({path: `${out}/memory-projects-${theme}.png`});
      await p.click('[data-mtab="history"]'); await p.waitForTimeout(400); await p.screenshot({path: `${out}/memory-history-${theme}.png`});
      await p.click('[data-mtab="memory"]'); await p.waitForTimeout(300);
    }
    await p.close();
  }
  console.log(errs.length ? "ERRORS " + errs.join(" | ") : "no page errors"); await b.close();
})();
