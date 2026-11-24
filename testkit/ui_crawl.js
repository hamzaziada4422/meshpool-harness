// Meshpool UI test: onboarding on a fresh install, then every settings page, every composer control and panel,
// clicking each non-destructive button and recording page errors and failed requests.
// Usage: node ui_crawl.js OUT_DIR FRESH_PORT MAIN_PORT
const {chromium} = require("playwright");
const [out, freshPort, mainPort] = process.argv.slice(2);
const results = []; const check = (name, ok, detail = "") => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 300))); };
const SKIP = /quit|delete|remove|disconnect|sign in|install|uninstall|reset|clear|open the|open in|choose folder|add a folder|folders i choose|^allow all|ask again|run now|^allow$|open settings/i;

async function watch(p) {
  const errs = [];
  p.on("pageerror", e => errs.push("pageerror: " + e.message));
  p.on("console", m => { if (m.type() === "error" && !/favicon|ERR_FILE_NOT_FOUND/.test(m.text())) errs.push("console: " + m.text()); });
  p.on("response", r => { const u = r.url(); if (u.includes("/api/") && r.status() >= 500) errs.push(`HTTP ${r.status()} ${u.split("?")[0]}`); if (r.status() >= 400) r.text().then(t => console.log(`  [${r.status()}] ${r.request().method()} ${u.replace(/^https?:\/\/[^/]+/, "").slice(0, 80)} ${t.slice(0, 160)}`)).catch(() => {}); });
  p.on("dialog", d => d.dismiss());
  return errs;
}

(async () => {
  const b = await chromium.launch();
  // ---------- 1. onboarding on a fresh install (FRESH_PORT "-" skips it)
  if (freshPort !== "-") {
    const p = await b.newPage({viewport: {width: 1280, height: 860}}); const errs = await watch(p);
    await p.addInitScript(() => { try { localStorage.clear(); } catch (e) {} });
    await p.goto(`http://127.0.0.1:${freshPort}/`);
    await p.waitForSelector("#oKey", {timeout: 20000});
    check("onboarding shows the key screen", true);
    // One provider choice: the Meshpool key, another OpenAI-compatible key, or a ChatGPT plan
    check("onboarding offers the three providers", (await p.$$eval("[data-prov]", x => x.map(e => e.dataset.prov).join(","))) === "meshpool,custom,chatgpt");
    await p.click("#oGo"); await p.waitForTimeout(300);
    check("empty key shows a message", /Paste your API key/.test(await p.textContent("#oRes")));
    await p.fill("#oKey", "test-key-1234");
    await p.click("[data-prov=custom]"); await p.fill("#oBase", "http://127.0.0.1:8911/v1");   // another API key: the mock
    await p.fill("#oModel", "deepseek-v4.1-flash");
    await p.click("#oGo");
    await p.waitForSelector("#onbPerm:not([hidden])", {timeout: 30000});
    check("key accepted, permissions step shown", true);
    await p.screenshot({path: out + "/u1-perms.png"});
    await p.click("[data-scope=later]"); await p.waitForTimeout(300);
    check("folder choice responds", /Meshpool Projects/i.test(await p.textContent("#oScope")));
    await p.click("[data-scope=home]"); await p.waitForTimeout(800);
    check("home folder choice adds a project", /home folder/i.test(await p.textContent("#oScope")));
    await p.click("#oDone");
    await p.waitForSelector("#onbSetup", {timeout: 10000});
    check("setup step shown", true);
    await p.waitForFunction(() => document.querySelector("#oImport") && !/Looking/.test(document.querySelector("#oImport").textContent), null, {timeout: 30000});
    const n = await p.$$eval("#oImport [data-key]", x => x.length);
    check("setup step lists skills and MCP servers from other apps", n >= 5, n);
    check("setup step leaves sign-ins to the provider step", !(await p.$("#oSubs")));   // providers are chosen in step 1
    await p.screenshot({path: out + "/u2-setup.png"});
    await p.$$eval("#oImport [data-key]", xs => xs.forEach(x => { if (!x.disabled) x.checked = true; }));   // the panel has no select-all buttons since 0.0.10
    // Start imports what is ticked (there is no separate Import button in onboarding)
    await p.click("#oFinish"); await p.waitForFunction(() => !document.querySelector("#onb"), null, {timeout: 30000}).catch(() => {});
    const imp = await p.evaluate(() => api("/api/skills").then(r => r.skills.map(s => s.name)));
    check("import from onboarding works (Start imports the ticked items)", imp.includes("claude-skill") && imp.includes("codex-skill"), imp.join(","));
    await p.waitForTimeout(800);
    check("onboarding closes into the app", !(await p.$("#onb")) && !!(await p.$(".hero")));
    check("onboarding: no page errors", !errs.length, errs.join(" | "));
    await p.close();
  }
  // ---------- 2. main app: settings pages
  const p = await b.newPage({viewport: {width: 1440, height: 900}}); const errs = await watch(p);
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(`http://127.0.0.1:${mainPort}/`); await p.waitForSelector(".hero");
  const openSet = () => p.evaluate(() => openSettings());          // Settings opens from the account menu since 0.0.11
  await openSet(); await p.waitForSelector(".scrim #mbody", {timeout: 10000});
  const sections = await p.evaluate(() => SECTIONS.map(s => s[0]));
  check("settings has all sections", ["personal", "scheduled", "web", "connectors", "skills", "harnesses", "mcp", "permissions", "about"].every(s => sections.includes(s)), sections);
  for (const sec of sections) {
    const before = errs.length;
    if (!(await p.$(".scrim"))) await openSet();
    await p.evaluate(s => renderSection(s), sec); await p.waitForTimeout(900);
    const labels = await p.$$eval("#mbody button:not([disabled]), #mbody summary", xs => xs.map((x, i) => [i, (x.textContent || x.getAttribute("aria-label") || "").trim()]));
    let clicked = 0;
    for (const [i, label] of labels) {
      if (SKIP.test(label) || /close settings/i.test(label) || !label) continue;
      const el = (await p.$$("#mbody button:not([disabled]), #mbody summary"))[i];
      if (!el || !(await el.isVisible())) continue;
      try { await el.click({timeout: 2000}); clicked++; await p.waitForTimeout(350); } catch (e) { /* re-rendered */ }
      if (await p.$(".dlg")) { await p.keyboard.press("Escape"); }
      if (!(await p.$(".scrim"))) { await openSet(); await p.evaluate(s => renderSection(s), sec); await p.waitForTimeout(500); }
    }
    await p.screenshot({path: `${out}/s-${sec}.png`});
    // 400s are validation messages (e.g. "No GitHub login found on this computer"), as in the whole-app check below.
    const pageErrs = errs.slice(before).filter(e => !/status of 400/.test(e));
    check(`settings: ${sec} (${clicked} buttons) without errors`, !pageErrs.length, pageErrs.join(" | "));
  }
  // specific settings flows
  await p.evaluate(() => renderSection("personal")); await p.waitForTimeout(600);
  await p.fill("#paIns", "Be brief."); await p.click("#paSave"); await p.waitForTimeout(600);
  check("instructions save from the UI", /Saved/.test(await p.textContent("#paRes")));
  await p.evaluate(() => renderSection("scheduled")); await p.waitForTimeout(600);
  await p.fill("#tsName", "UI task"); await p.fill("#tsPrompt", "Reply with the single word ok"); await p.selectOption("#tsWhen", "weekly");
  check("weekly shows the day picker", await p.isVisible("#tsDay"));
  await p.click("#tsAdd"); await p.waitForTimeout(800);
  check("scheduled task added from the UI", /UI task/.test(await p.textContent("#mbody")));
  await p.evaluate(() => renderSection("connectors")); await p.waitForSelector("#cnl");
  await p.fill("#cxUrl", "not a url"); await p.click("#cxAdd"); await p.waitForTimeout(800);
  check("bad connector address shows an error", /https/.test(await p.textContent("#cnRes")), await p.textContent("#cnRes"));
  await p.keyboard.press("Escape"); await p.$eval(".scrim", x => x.remove()).catch(() => {});
  // ---------- 3. composer, palette and panels
  const before = errs.length;
  // the plus menu (files, Plan first, memory, style, skills, plugins) and the model menu (models, harness, effort)
  await p.evaluate(() => newThread()); await p.waitForSelector("#attBtn", {state: "visible", timeout: 15000});   // Settings clicks can leave another page open
  const menuItems = () => p.$$eval("#ctx .mi", xs => xs.map(x => x.textContent.trim()));
  const clickItem = async rx => { for (const x of await p.$$("#ctx .mi")) if (rx.test((await x.textContent()).trim())) { await x.click(); return true; } return false; };
  for (const id of ["#attBtn", "#mPill"]) {
    await p.click(id); await p.waitForTimeout(400);
    const opts = (await menuItems()).length;
    check(`menu ${id} opens with options`, opts > 0, opts);
    await p.keyboard.press("Escape"); await p.waitForTimeout(200);
  }
  await p.click("#mPill"); await p.waitForTimeout(300); await clickItem(/^Harness/); await p.waitForTimeout(300);
  const agents = await menuItems();
  // Agents only (Codex is an agent; a subscription is a provider, chosen in the model menu)
  check("harness picker shows Automatic and agents, no subscription entries", agents.some(g => /Auto/.test(g)) && !agents.some(g => /subscription/i.test(g)), agents);
  check("each agent shows what it is best at", (await p.$$eval("#ctx .mi small", x => x.filter(e => e.textContent.trim()).length)) >= 3);
  await clickItem(/^Auto/); await p.waitForTimeout(300);
  check("pick Meshpool Auto", await p.evaluate(() => (S.draft.harness || "meshpool-auto") === "meshpool-auto"));
  await p.click("#attBtn"); await p.waitForTimeout(300); await clickItem(/^Plan first/); await p.waitForTimeout(200);
  check("plan toggle on", await p.evaluate(() => !!S.draft.plan));
  await p.click("#attBtn"); await p.waitForTimeout(300); await clickItem(/^Plan first/); await p.waitForTimeout(200);
  await p.fill("#input", "/"); await p.waitForTimeout(300); check("slash palette opens", (await p.$$(".pop .opt")).length > 3); await p.keyboard.press("Escape"); await p.fill("#input", "");
  await p.fill("#input", "$"); await p.waitForTimeout(300); check("skills palette opens", (await p.$$(".pop .opt")).length > 0); await p.keyboard.press("Escape"); await p.fill("#input", "");
  // Auto routes a real task
  await p.fill("#input", "Build a revenue forecast in Excel"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => (S.cur?.log || []).some(e => /^Using /.test(e.text || "")), null, {timeout: 30000}).catch(() => {});
  check("Auto picks a harness and says why", await p.evaluate(() => (S.cur?.log || []).some(e => /^Using /.test(e.text || ""))));
  await p.waitForFunction(() => S.cur?.view?.state === "idle", null, {timeout: 120000}).catch(() => {});
  for (const pn of ["progress", "outputs"]) { await p.evaluate(n => togglePanel(n, true), pn); await p.waitForTimeout(500); }
  await p.evaluate(() => newThread("/tmp/proj1")); await p.waitForTimeout(500);
  await p.fill("#input", "say hello and create hello.py"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => S.cur?.view?.state && S.cur.view.state !== "starting", null, {timeout: 30000}).catch(() => {});
  for (let i = 0; i < 40; i++) { const ok = await p.$(".appr:not(.done) button"); if (ok) { await ok.click(); } if (await p.evaluate(() => S.cur?.view?.state === "idle" && (S.cur.log || []).some(e => e.type === "turn_end"))) break; await p.waitForTimeout(500); }
  check("code thread finishes with approval click", await p.evaluate(() => (S.cur.log || []).some(e => e.type === "turn_end")));
  for (const pn of ["diff", "files", "term", "git", "preview"]) {
    await p.evaluate(n => togglePanel(n, true), pn); await p.waitForTimeout(900); await p.screenshot({path: `${out}/p-${pn}.png`});
  }
  check("panels open without errors", errs.length === before, errs.slice(before).join(" | "));
  // keyboard shortcuts and sidebar
  const mod = process.platform === "darwin" ? "Meta" : "Control";
  await p.keyboard.press(`${mod}+k`); await p.waitForTimeout(400); check("Ctrl+K search opens", !!(await p.$(".pop, .search, #tq, [placeholder*=Search]"))); await p.keyboard.press("Escape");
  await p.keyboard.press(`${mod}+b`); await p.waitForTimeout(300); await p.keyboard.press(`${mod}+b`);
  await p.keyboard.press(`${mod}+n`); await p.waitForTimeout(400); check("Ctrl+N new thread", await p.evaluate(() => !S.cur));
  const realErrs = errs.filter(e => !/status of 400/.test(e)); check("whole app: no page errors or server errors (400s are validation messages)", !realErrs.length, realErrs.join(" | "));
  await b.close();
  const ok = results.filter(r => r[1]).length; console.log(`\n${ok}/${results.length} passed`); results.filter(r => !r[1]).forEach(r => console.log("  failed:", r[0]));
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.error("CRASH", e.message); process.exit(2); });
