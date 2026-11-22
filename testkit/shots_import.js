// Import from other agents: onboarding step and Settings. Needs a home whose HOME has fake agent folders
// (testkit/seed_agents.py). Usage: node shots_import.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
const res = []; const ck = (n, ok, d = "") => { res.push(!!ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + String(d).slice(0, 200))); };
(async () => {
  const b = await chromium.launch(); const errs = [];
  for (const theme of ["light", "dark"]) {
    const p = await b.newPage({viewport: {width: 1280, height: 1000}});
    p.on("pageerror", e => errs.push(e.message));
    await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    for (let i = 0; i < 5; i++) { if (await p.$("#oImport")) break; const c = await p.$('button:has-text("Continue")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
    if (theme === "light" && await p.$("#oImport")) {
      await p.waitForSelector(".impag", {timeout: 8000});
      ck("onboarding lists the agents found", (await p.$$(".impag")).length === 5);
      await p.screenshot({path: `${out}/agents-onboarding-${theme}.png`});
      await p.click("#impDry"); await p.waitForTimeout(800);
      const t = await p.textContent("#impRes");
      const pend0 = await p.evaluate(async () => (await api("/api/memory")).counts.pending);
      ck("preview only says what it would do and writes nothing", /Would import/.test(t) && pend0 === 0, t + " pending=" + pend0);
      await p.click('button:has-text("Start using Meshpool")'); await p.waitForTimeout(3000);
      ck("the ticked items came over, memories wait in the tray", (await p.evaluate(async () => (await api("/api/memory")).counts.pending)) >= 4);
      const cfg = await p.evaluate(async () => JSON.stringify(await api("/api/mcp")).slice(0, 4000)).catch(() => "");
      ck("no secret reached Meshpool's tool list", !/NEVER-READ/.test(cfg));
    }
    await p.evaluate(() => { try { document.querySelector(".onb")?.remove(); } catch (e) {} });
    await p.evaluate(() => openSettings("import")); await p.waitForSelector(".impag", {timeout: 8000});
    await p.screenshot({path: `${out}/agents-settings-${theme}.png`});
    await p.close();
  }
  console.log(errs.length ? "ERRORS " + errs.join("|") : "no page errors"); await b.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})();
