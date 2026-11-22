// The memory page with about 1,000 facts: time to first row, rows in the DOM, scroll cost. Usage: node perf_memory.js PORT
const {chromium} = require("playwright");
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1280, height: 860}});
  await p.goto(`http://127.0.0.1:${process.argv[2]}/`); await p.waitForTimeout(1200);
  for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
  const t0 = Date.now();
  await p.evaluate(() => goPage("memory")); await p.waitForSelector(".mem-row"); const first = Date.now() - t0;
  const rows = await p.$$eval(".mem-row", e => e.length);
  const total = await p.evaluate(() => MEM.d.facts.length);
  const scrollMs = await p.evaluate(async () => { const sc = document.querySelector("#conv"); const t = performance.now(); for (let i = 0; i < 40; i++) { sc.scrollTop += 600; await new Promise(r => requestAnimationFrame(r)); } return (performance.now() - t) / 40; });
  const rowsAfter = await p.$$eval(".mem-row", e => e.length);
  const q0 = Date.now(); await p.fill("#memQ", "topic 7"); await p.waitForFunction(() => MEM.d.facts.length < 900, null, {timeout: 5000}); const search = Date.now() - q0;
  console.log(JSON.stringify({facts: total, firstRowMs: first, rowsInDom: rows, rowsInDomAfterScroll: rowsAfter, msPerScrollFrame: Math.round(scrollMs), searchMs: search}));
  await b.close();
})();
