// Screenshots of the main screens at desktop width, for the UX audit (docs/plan/CHAT_UX.md).
// Usage: node ux_audit_shots.js OUT_DIR PORT PROJECT_DIR [FRESH_PORT]
// The app must run against testkit/mock_llm_long.py. FRESH_PORT: an app with no key yet (onboarding screen).
const {chromium} = require("playwright");
const [out, port, proj, fresh] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  const shot = async n => { await p.waitForTimeout(500); await p.screenshot({path: `${out}/ux-audit-${n}.png`}); console.log("shot", n); };
  if (fresh) {
    const f = await b.newPage({viewport: {width: 1440, height: 900}});
    await f.addInitScript(() => { try { localStorage.clear(); } catch (e) {} });
    await f.goto(`http://127.0.0.1:${fresh}/`); await f.waitForSelector("#oKey", {timeout: 20000}); await f.waitForTimeout(500);
    await f.screenshot({path: `${out}/ux-audit-00-onboarding.png`}); console.log("shot 00-onboarding"); await f.close();
  }
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector(".hero");
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  await api("/api/projects", {action: "add", path: proj});
  await p.evaluate(() => loadProjects()); await p.evaluate(() => loadThreads());
  // a few chats so the sidebar looks lived in
  for (const [t, kind, h] of [["hello there", "cowork", "meshpool-work"], ["hello again", "code", "meshpool-code"], ["stream long answer one", "cowork", "meshpool-work"]]) {
    const v = await api("/api/agent", {workdir: proj, harness: h, kind, mode: "auto", task: t});
    await api(`/api/agent/${v.id}/send`, {text: t});
  }
  await p.waitForTimeout(9000); await p.evaluate(() => loadThreads());
  await p.evaluate(() => { setAppMode("cowork"); }); await p.waitForTimeout(600);
  await shot("01-home-work");
  await p.evaluate(() => setAppMode("code")); await shot("02-home-code");
  await p.evaluate(() => setAppMode("cowork"));
  await p.click("#hPill"); await shot("03-agent-picker"); await p.keyboard.press("Escape");
  await p.click("#mPill"); await shot("04-model-picker"); await p.keyboard.press("Escape");
  await p.click("#aPill"); await shot("05-access-picker"); await p.keyboard.press("Escape");
  await p.click("#heroProj"); await shot("06-folder-picker"); await p.keyboard.press("Escape");
  await p.click(".th >> nth=0"); await p.waitForTimeout(1500); await shot("07-chat-open");
  await p.click('[data-panel="progress"]').catch(() => {}); await shot("08-side-panel");
  await p.click("#openSettings");
  for (const [k, n] of [["general", "09-settings-account"], ["models", "10-settings-models"], ["personal", "11-settings-memory"], ["harnesses", "12-settings-agents"], ["mcp", "13-settings-tools"], ["permissions", "14-settings-permissions"], ["advanced", "15-settings-advanced"]]) {
    await p.click(`.mnav [data-s="${k}"]`); await p.waitForTimeout(700); await shot(n);
  }
  await p.keyboard.press("Escape");
  await p.evaluate(() => setAppMode("code")); await p.click(".th >> nth=0").catch(() => {}); await p.waitForTimeout(1200);
  await p.click('[data-panel="git"]').catch(() => {}); await shot("16-code-chat-git-panel");
  console.log("errors:", errs.join(" | ") || "none");
  await b.close();
})();
