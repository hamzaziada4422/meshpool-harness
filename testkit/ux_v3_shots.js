// Screenshots of every screen and menu of the v3 shell, dark and light, desktop (1274x744, like the design brief) and narrow.
// Usage: node testkit/ux_v3_shots.js OUT_DIR PORT PROJECT_DIR HOME_DIR     (app on PORT against testkit/mock_llm_long.py)
// Writes OUT_DIR/ux-v3-<theme>-<desktop|narrow>-<NN>-<name>.png. ONLY=desktop|narrow and THEMES=dark,light narrow the run.
// Seeds fixtures (projects, chats of different ages, code sessions, files made, scheduled tasks) once per home.
const {chromium} = require("playwright");
const fs = require("fs");
const path = require("path");
const [out, port, proj, home] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;

async function seed(b) {
  const p = await b.newPage({viewport: {width: 1274, height: 744}});
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector("#input");
  const api = (url, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [url, body]);
  if ((await api("/api/pj")).projects.length) { await p.close(); return; }
  const a = await api("/api/pj", {name: "Q3 board report", description: "Numbers, summaries and slides for the quarterly board meeting.", instructions: "Write in plain English. Lead with the answer."});
  const w = await api("/api/pj", {name: "Website redesign", description: "The marketing site.", link: {type: "folder", path: proj}});
  const r = await api("/api/pj", {name: "Customer research"});
  for (const x of [a, w]) await api(`/api/pj/${x.id}`, {action: "update", pinned: true});
  await api(`/api/pj/${a.id}`, {action: "knowledge_add", name: "board-notes.md", data: Buffer.from("The board meets on 3 March.\n").toString("base64")});
  // files made by chats: a saved chat whose tool calls wrote them
  const work = path.join(home, "work"); fs.mkdirSync(work, {recursive: true});
  const files = [["Board summary Q3.docx", 9000], ["Revenue model.xlsx", 14000], ["Launch deck.pptx", 52000], ["Pricing notes.md", 800], ["Market sizing.pdf", 22000], ["Site mock.html", 3000]];
  const calls = files.map(([f], i) => ({id: "c" + i, type: "function", function: {name: "write_file", arguments: JSON.stringify({path: f, content: "x"})}}));
  files.forEach(([f, n]) => fs.writeFileSync(path.join(work, f), Buffer.alloc(n, 65)));
  const sdir = path.join(home, "runs", "sessions");
  const mk = (id, title, kind, workdir, updated, meta = {}, msgs = null) => {
    fs.mkdirSync(path.join(sdir, id), {recursive: true});
    fs.writeFileSync(path.join(sdir, id, "session.json"), JSON.stringify({id, title, workdir, updated, created: updated, kind, harness: kind === "code" ? "meshpool-code" : "meshpool-work", model: "deepseek-v4.1-flash",
      messages: msgs || [{role: "system", content: "x"}, {role: "user", content: title}, {role: "assistant", content: "Done."}]}));
    fs.writeFileSync(path.join(sdir, id, "meta.json"), JSON.stringify(Object.assign({kind}, meta)));
  };
  const now = Date.now() / 1000, day = 86400;
  mk("20261110-000000-aaaa", "Draft the board summary", "cowork", work, now - 600, {project_id: a.id, unread: true, pinned: false},
    [{role: "system", content: "x"}, {role: "user", content: "Draft the board summary"}, {role: "assistant", content: "", tool_calls: calls}, ...calls.map(c => ({role: "tool", tool_call_id: c.id, content: "Saved"})), {role: "assistant", content: "I made the files."}]);
  mk("20261110-000001-aaab", "Find the revenue numbers", "cowork", work, now - 1800, {project_id: a.id});
  mk("20261110-000002-aaac", "Summarise the customer interviews", "cowork", work, now - 3 * day, {project_id: r.id});
  mk("20261110-000003-aaad", "What is a good name for a bakery", "cowork", work, now - 2 * day);
  mk("20261110-000004-aaae", "Plan the offsite", "cowork", work, now - 12 * day);
  mk("20261110-000005-aaaf", "Weekly recap (19 Nov 09:00)", "cowork", work, now - 3600, {scheduled: "x1", unread: true});
  mk("20261110-000006-aaag", "Fix the navigation bar", "code", proj, now - 7200, {project_id: w.id, unread: true, pr: {number: 39, title: "Fix navigation focus order", draft: true, additions: 128, deletions: 34, repo: "acme/site"}});
  mk("20261110-000007-aaah", "Add dark mode to settings", "code", proj, now - day, {pr: {number: 41, title: "Dark mode for the settings page", draft: false, additions: 56, deletions: 9, repo: "acme/site"}});
  mk("20261110-000008-aaai", "Upgrade the build toolchain", "code", proj, now - 4 * day);
  for (const [n, pr, when, day_] of [["Morning briefing", "Summarise my calendar and unread mail before the day starts.", "weekdays", ""], ["Weekly recap", "Write a short recap of what changed in my projects this week.", "weekly", "fri"], ["Repository health check", "Check this repository for failing tests and stale branches.", "daily", ""]])
    await api("/api/assistant", {action: "task_add", name: n, prompt: pr, when, day: day_ || "mon", time: n === "Weekly recap" ? "16:00" : "09:00", kind: n.startsWith("Repo") ? "code" : "cowork", folder: n.startsWith("Repo") ? proj : ""});
  // two live code sessions that stop to ask before they edit
  for (const t of ["hello: fix the footer links", "hello: tidy the changelog"]) await api("/api/agent", {workdir: proj, harness: "meshpool-code", kind: "code", mode: "ask", task: t}).then(v => api(`/api/agent/${v.id}/send`, {text: t}));
  await p.waitForTimeout(9000);
  await p.close();
}

(async () => {
  const b = await chromium.launch();
  await seed(b);
  const themes = (process.env.THEMES || "dark,light").split(",");
  const sizes = [["desktop", {width: 1274, height: 744}], ["narrow", {width: 390, height: 844}]].filter(([t]) => !process.env.ONLY || process.env.ONLY === t);
  for (const theme of themes) for (const [tag, vp] of sizes) {
    const narrow = tag === "narrow";
    const p = await b.newPage({viewport: vp});
    const errs = []; p.on("pageerror", e => errs.push(e.message));
    await p.addInitScript(t => { try { localStorage.clear(); localStorage.setItem("mp_permsAsked", "1"); localStorage.setItem("mp_theme", JSON.stringify(t)); localStorage.setItem("mp_cards_off", JSON.stringify(["key", "github", "project"])); } catch (e) {} }, theme);
    await p.goto(BASE + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(1800);
    await p.evaluate(() => { S.mode = "chat"; syncMode(); newThread(); }); await p.waitForTimeout(400);
    let n = 0;
    const shot = async name => { await p.waitForTimeout(350); n++; await p.screenshot({path: `${out}/ux-v3-${theme}-${tag}-${String(n).padStart(2, "0")}-${name}.png`}); };
    const esc = () => p.keyboard.press("Escape");
    const openSide = async () => { if (narrow && !(await p.$eval("#app", e => e.classList.contains("sideopen")))) { await p.click("#showSide"); await p.waitForTimeout(250); } };
    const closeSide = async () => { if (narrow) { await p.mouse.click(380, 400); await p.waitForTimeout(250); } };
    const go = async fn => { await openSide(); await fn(); await closeSide(); };
    const nav = id => go(() => p.click(`.nv[data-nv="${id}"]`));

    await shot("home");
    if (narrow) { await openSide(); await shot("sidebar"); await closeSide(); } else { await p.click("#hideSide"); await shot("sidebar-collapsed"); await p.click("#showSide"); await p.waitForTimeout(300); }
    await openSide(); await p.click('.nv[data-nv="search"]'); await p.waitForSelector("#srchList .srow"); await shot("search-empty");
    await p.fill("#srchIn", "board"); await p.waitForTimeout(120); await shot("search-loading"); await p.waitForTimeout(500); await shot("search-results"); await esc(); await closeSide();
    await p.click("#attBtn"); await shot("plus-menu"); await esc();
    await p.click("#mPill"); await shot("model-menu"); await esc();
    await p.click("#aPill"); await shot("permission-menu"); await esc();
    await p.click("#projSel"); await shot("project-selector"); await esc();
    await nav("projects"); await p.waitForSelector(".gcard"); await shot("projects");
    await nav("artifacts"); await p.waitForSelector(".gcard.art"); await shot("artifacts");
    await nav("scheduled"); await p.waitForSelector(".gcard.task"); await shot("scheduled");
    await p.click('[data-gtab="templates"]'); await shot("scheduled-templates");
    await nav("plugins"); await p.waitForSelector(".plogo", {timeout: 8000}).catch(() => {}); await shot("plugins");
    await openSide(); await p.click("#acctBtn"); await shot("account-menu"); await esc(); await closeSide();
    // a chat that is running
    await openSide(); await p.click("#newThread"); await closeSide();
    await p.evaluate(() => { S.draft.mode = "auto"; renderBar(); });
    await p.fill("#input", "stream long please"); await p.keyboard.press("Enter");
    await p.waitForSelector(".msg-user"); await p.waitForTimeout(1500);
    await p.click("#panelBtn"); await shot("chat-running-progress");
    await p.hover(".msg-user"); await shot("message-hover");
    await p.click("#ttl"); await shot("title-menu"); await esc();
    await p.click("#devBtn"); await shot("device-popover"); await esc();
    await p.waitForFunction(() => S.cur?.view?.state === "idle", null, {timeout: 120000, polling: 1000}).catch(() => {});
    await p.click("#panelBtn"); await p.waitForTimeout(300); await shot("chat-finished");
    // code mode
    await openSide(); await p.click("#modeCode"); await p.waitForTimeout(500); await closeSide();
    await p.evaluate(() => { newThread(); }); await shot("code-home");
    await p.click("#envSel"); await shot("code-environment-menu"); await esc();
    await p.click("#repoSel"); await shot("code-repo-menu"); await esc();
    await p.click("#mPill"); await shot("code-model-menu"); await esc();
    await openSide();
    const prRow = await p.$(".th[data-id] [data-prtip]");
    if (prRow) { const bb = await prRow.boundingBox(); await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await p.waitForTimeout(1200); await shot("code-session-pr-card"); await p.mouse.move(600, 300); } else await shot("code-sessions");
    await p.click('.nv[data-nv="more"]'); await shot("code-more-menu"); await esc(); await closeSide();
    await p.evaluate(() => { const t = S.threads.find(t => t.kind === "code" && !t.scratch); openThread(t.id); }); await p.waitForTimeout(1200); await shot("code-session");
    await openSide(); await p.click("#modeChat"); await p.waitForTimeout(400); await closeSide();
    // settings
    for (const [page, name] of [["profile", "settings-account"], ["billing", "settings-billing"], ["usage", "settings-usage"], ["appearance", "settings-appearance"], ["personal", "settings-your-meshpool"], ["caps", "settings-capabilities"], ["notify", "settings-notifications"], ["data", "settings-privacy"], ["connectors", "settings-plugins"], ["terminal", "settings-code"], ["general", "settings-models-and-providers"], ["harnesses", "settings-agents"], ["scheduled", "settings-scheduled"], ["about", "settings-about"]]) {
      await p.evaluate(k => openSettings(k), page); await p.waitForTimeout(700); await shot(name); await p.evaluate(() => closeSettings());
    }
    console.log("shots", theme, tag, n, errs.length ? "ERRORS: " + errs.join(" | ") : "ok");
    await p.close();
  }
  await b.close();
})().catch(e => { console.error("script error:", e.message); process.exit(1); });
