// Code sessions grouped by folder. Usage: node shots_code.js PORT OUTDIR
const {chromium} = require("playwright");
const [port, out] = process.argv.slice(2);
const B = `http://127.0.0.1:${port}`;
(async () => {
  const html = await (await fetch(B + "/")).text(); const TOK = html.match(/token: "([^"]+)"/)[1];
  const call = async (p, body) => (await fetch(B + p, {method: body ? "POST" : "GET", headers: {"X-Meshpool-Token": TOK, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined})).json();
  for (const d of ["/tmp/cg-alpha", "/tmp/cg-beta", "/tmp/cg-gamma"]) await call("/api/projects", {action: "add", path: d});
  const plan = [["/tmp/cg-alpha", 8], ["/tmp/cg-beta", 3], ["/tmp/cg-gamma", 1]];
  for (const [d, n] of plan) for (let i = 0; i < n; i++) {
    const v = await call("/api/agent", {workdir: d, harness: "meshpool-code", kind: "code", mode: "auto"});
    for (let k = 0; k < 40; k++) { if ((await call(`/api/agent/${v.id}?since=0`)).state !== "starting") break; await new Promise(r => setTimeout(r, 250)); }
    await call(`/api/agent/${v.id}/send`, {text: `Fix issue ${i} in ${d.split("/").pop()}`});
    await new Promise(r => setTimeout(r, 700));
  }
  const b = await chromium.launch(); const errs = []; const res = [];
  const ck = (n, ok, d = "") => { res.push(ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + d)); };
  for (const theme of ["light", "dark"]) {
    const p = await b.newPage({viewport: {width: 1280, height: 860}});
    p.on("pageerror", e => errs.push(e.message)); p.on("console", m => { if (m.type() === "error" && !/favicon/.test(m.text())) errs.push(m.text()); });
    await p.goto(B + "/"); await p.waitForTimeout(1200);
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(600); }
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await p.click("#modeCode"); await p.waitForTimeout(1500);
    const groups = await p.$$eval(".cghd .cgn", e => e.map(x => x.textContent));
    {
      ck("sessions are grouped by folder by default", groups.length === 3 && groups[0].length > 0, groups.join(","));
      ck("alpha has a Show more row", await p.$('[data-cgmore]') !== null);
      await p.click(".cghd >> nth=1"); await p.waitForTimeout(300);
      ck("a folder collapses", (await p.$$eval(".cg.shut", e => e.length)) === 1);
      await p.click(".cghd >> nth=1"); await p.waitForTimeout(200);
      await p.click("#codeFind"); await p.fill("#codeQ", "issue 1"); await p.waitForTimeout(400);
      ck("search narrows the list", (await p.$$eval(".sub .th, .threads .th", e => e.length)) <= 4);
      await p.screenshot({path: `${out}/code-search-${theme}.png`}); await p.fill("#codeQ", ""); await p.keyboard.press("Escape"); await p.waitForTimeout(300);
      await p.click("#codeFilter"); await p.waitForTimeout(300); await p.screenshot({path: `${out}/code-menu-${theme}.png`}); await p.keyboard.press("Escape");
      await p.hover(".cg >> nth=0"); await p.click("[data-cgnew] >> nth=0"); await p.waitForTimeout(500);
      ck("the folder's + starts a session in it", (await p.evaluate(() => S.draft.project)).includes("cg-"));
    }
    await p.screenshot({path: `${out}/code-groups-${theme}.png`});
    await p.close();
  }
  console.log(errs.length ? "ERRORS " + errs.join(" | ") : "no page errors"); await b.close();
  process.exit(res.every(Boolean) ? 0 : 1);
})();
