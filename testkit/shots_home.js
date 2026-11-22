// Home screens, settings, Your work. Usage: node shots_home.js PORT NAME OUTDIR
const {chromium} = require("playwright");
const [port, name, out] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const errs = [];
  for (const theme of ["light", "dark"]) {
    const p = await b.newPage({viewport: {width: 1280, height: 860}});
    p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/favicon/.test(m.text())) errs.push(m.text()); });
    await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
    if (name === "fresh" && theme === "light") await p.screenshot({path: `${out}/import-onboarding-light.png`, fullPage: false});
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; if (name === "fresh" && theme === "light" && i === 1) await p.screenshot({path: `${out}/import-step-light.png`}); await c.click(); await p.waitForTimeout(700); }
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await p.evaluate(() => newThread()); await p.waitForTimeout(2500);
    await p.screenshot({path: `${out}/home-${name}-${theme}.png`});
    if (theme === "light" || name === "heavy") {
      await p.evaluate(() => goPage("work")); await p.waitForTimeout(1800); await p.screenshot({path: `${out}/yourwork-${name}-${theme}.png`});
      await p.evaluate(() => openSettings("suggest")); await p.waitForTimeout(1200); await p.screenshot({path: `${out}/settings-suggestions-${name}-${theme}.png`});
      await p.keyboard.press("Escape");
      await p.evaluate(() => openSettings("notify")); await p.waitForTimeout(1000); await p.screenshot({path: `${out}/settings-quiet-hours-${name}-${theme}.png`});
    }
    await p.close();
  }
  console.log(errs.length ? "ERRORS " + errs.join(" | ") : "no page errors"); await b.close();
})();
