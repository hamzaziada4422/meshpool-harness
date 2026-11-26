// Probe: on Settings, Custom tools, click each button and print the stack of any page error with the button's label.
// Usage: node mcp_err_probe.js PORT
const {chromium} = require("playwright");
const port = process.argv[2];
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1440, height: 900}});
  let last = "";
  p.on("pageerror", e => console.log("PAGEERROR after", JSON.stringify(last), "\n", e.stack));
  p.on("dialog", d => d.dismiss());
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(`http://127.0.0.1:${port}/`); await p.waitForSelector(".hero");
  await p.evaluate(() => openSettings()); await p.evaluate(() => renderSection("mcp")); await p.waitForTimeout(1200);
  const labels = await p.$$eval("#mbody button:not([disabled]), #mbody summary", xs => xs.map(x => (x.textContent || x.getAttribute("aria-label") || "").trim()));
  for (let i = 0; i < labels.length; i++) {
    if (/remove|delete|disconnect|install|sign in|reset|clear/i.test(labels[i])) continue;
    const el = (await p.$$("#mbody button:not([disabled]), #mbody summary"))[i]; if (!el || !(await el.isVisible())) continue;
    last = labels[i]; try { await el.click({timeout: 2000}); } catch (e) {} await p.waitForTimeout(700);
    if (!(await p.$(".scrim"))) { await p.evaluate(() => openSettings()); await p.evaluate(() => renderSection("mcp")); await p.waitForTimeout(600); }
  }
  await b.close();
})();
