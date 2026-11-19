// Screenshots of every main screen and menu of the chat UX v2, light and dark, desktop and narrow.
// Usage: node testkit/ux_v2_shots.js OUT_DIR PORT PROJECT_DIR HOME_DIR     (app on PORT against testkit/mock_llm_long.py)
// Writes OUT_DIR/ux-v2-<theme>-<desktop|narrow>-<name>.png
const {chromium} = require("playwright");
const fs = require("fs");
const path = require("path");
const [out, port, proj, home] = process.argv.slice(2);
const BASE = `http://127.0.0.1:${port}`;

(async () => {
  const b = await chromium.launch();
  const seedPage = await b.newPage({viewport: {width: 1440, height: 900}});
  await seedPage.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await seedPage.goto(BASE + "/"); await seedPage.waitForSelector("#input");
  const api = (path, body) => seedPage.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  // ---- seed: two projects and a handful of chats of different ages
  const old = await api("/api/pj");
  if (!old.projects.length) {
    const kf = "Q3 board report. Revenue grew 12 percent. The board meets on 3 March and wants a one-page summary.\n";
    const a = await api("/api/pj", {name: "Q3 board report", description: "Numbers, summaries and slides for the quarterly board meeting.", instructions: "Write in plain English. Lead with the answer. Put every number in a table."});
    await api(`/api/pj/${a.id}`, {action: "knowledge_add", name: "board-notes.md", data: Buffer.from(kf).toString("base64")});
    await api(`/api/pj/${a.id}`, {action: "knowledge_add", name: "q3-revenue.csv", data: Buffer.from("region,revenue\nEMEA,120\nAPAC,95\n").toString("base64")});
    await api(`/api/pj/${a.id}`, {action: "memory_set", text: "The board prefers one-page summaries\nRevenue is reported in USD thousands"});
    const w = await api("/api/pj", {name: "Website redesign", description: "The marketing site.", link: {type: "folder", path: proj}});
    const chats = [["Draft the board summary", a.id], ["Find the revenue numbers", a.id], ["hello again", ""], ["stream long answer one", ""], ["Fix the navigation bar", w.id], ["What is a good name for a bakery", ""], ["Plan the offsite", ""], ["Notes from the old roadmap", ""]];
    const ids = [];
    for (const [t, pid] of chats) {
      const v = await api("/api/agent", {project_id: pid, harness: "meshpool-auto", mode: "auto", task: t});
      ids.push(v.id); await api(`/api/agent/${v.id}/send`, {text: t});
    }
    await seedPage.waitForTimeout(16000);
    // backdate some chats so the sidebar shows every group
    const days = [0, 0, 0, 1, 3, 12, 45, 400];
    const sdir = path.join(home, "runs", "sessions");
    ids.forEach((id, i) => {
      const f = path.join(sdir, id, "session.json");
      try { const d = JSON.parse(fs.readFileSync(f, "utf8")); d.updated = Date.now() / 1000 - days[i] * 86400 - i * 60; fs.writeFileSync(f, JSON.stringify(d)); } catch (e) { console.log("backdate failed", id, e.message); }
    });
    await api("/api/pj"); await seedPage.waitForTimeout(500);
  }
  await seedPage.close();

  for (const [theme, vp, tag] of [["light", {width: 1440, height: 900}, "desktop"], ["dark", {width: 1440, height: 900}, "desktop"], ["light", {width: 390, height: 844}, "narrow"], ["dark", {width: 390, height: 844}, "narrow"]]) {
    if (process.env.ONLY && process.env.ONLY !== tag) continue;
    const narrow = tag === "narrow";
    const p = await b.newPage({viewport: vp});
    const errs = []; p.on("pageerror", e => errs.push(e.message));
    await p.addInitScript(t => { try { localStorage.clear(); localStorage.setItem("mp_permsAsked", "1"); localStorage.setItem("mp_theme", JSON.stringify(t)); } catch (e) {} }, theme);
    await p.goto(BASE + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(1500);
    const shot = async n => { await p.waitForTimeout(450); await p.screenshot({path: `${out}/ux-v2-${theme}-${tag}-${n}.png`}); console.log("shot", theme, tag, n); };
    const esc = () => p.keyboard.press("Escape");
    const openSide = async () => { if (narrow && !(await p.$eval("#app", e => e.classList.contains("sideopen")))) { await p.click("#showSide"); await p.waitForTimeout(250); } };
    const closeSide = async () => { if (narrow) { await p.mouse.click(380, 400); await p.waitForTimeout(200); } };

    await shot("01-home");
    await openSide(); await shot("02-sidebar");
    const ids = await p.evaluate(() => S.threads.filter(t => !t.archived).map(t => t.id));
    const row = `#sideList .th[data-id="${ids[3] || ids[0]}"]`;
    await p.hover(row); await p.waitForTimeout(250); await p.click(row + " .more"); await shot("03-chat-menu");
    await p.click('#ctx .mi:has-text("Add to project")'); await shot("04-add-to-project");
    await esc();
    await p.focus(row); await p.keyboard.press("F2"); await shot("05-rename-inline"); await esc();
    await p.click("#sideList .th.pr", {button: "right"}); await shot("06-project-menu"); await esc();
    await p.click("#sideList .th.pr"); await p.waitForSelector(".pjpage"); await closeSide(); await shot("07-project-page");
    await p.click("#pjInsEdit"); await shot("08-instructions-dialog"); await esc();
    await p.evaluate(() => { const t = S.threads.find(t => t.project_id && !t.archived); openThread(t.id); }); await p.waitForSelector(".msg-ai", {timeout: 20000}); await p.waitForTimeout(600);
    await shot("09-project-chat");
    await p.click("#panelBtn"); await p.waitForTimeout(500); await shot("10-side-panel-session");
    await p.evaluate(() => togglePanel("outputs", true)); await shot("11-side-panel-artifacts");
    await p.evaluate(() => togglePanel("progress", true)); await shot("12-side-panel-steps");
    await p.evaluate(() => { if (S.panel) togglePanel(S.panel); });
    await p.click("#oPill"); await shot("13-chat-settings"); await esc();
    await p.click("#mPill"); await shot("14-model-picker"); await esc();
    await p.click("#tmore"); await shot("15-chat-options-menu"); await esc();
    // a chat in a linked project (code)
    await p.evaluate(() => { const w = S.pj.find(x => x.link); const t = S.threads.find(t => t.project_id === w.id); openThread(t.id); }); await p.waitForTimeout(1500);
    if (!narrow) { await p.click("#panelBtn").catch(() => {}); await p.waitForTimeout(600); await shot("16-linked-project-chat-panel"); await p.evaluate(() => { if (S.panel) togglePanel(S.panel); }); }
    // delete confirm and search
    await openSide();
    await p.hover(row); await p.waitForTimeout(250); await p.click(row + " .more"); await p.click("#ctx .mi.danger"); await shot("17-delete-confirm"); await p.click('.dlg [data-a="0"]');
    await p.fill("#q", "board"); await p.waitForTimeout(900); await shot("18-search"); await p.fill("#q", ""); await p.waitForTimeout(300);
    await p.evaluate(() => newThread()); await closeSide();
    if (!narrow) { await p.click("#hideSide"); await shot("19-sidebar-collapsed"); await p.click("#showSide"); await p.waitForTimeout(400); }
    // settings
    await p.click("#openSettings").catch(async () => { await openSide(); await p.click("#openSettings"); });
    await p.waitForSelector(".modal");
    const pages = [["profile", "20-settings-profile"], ["appearance", "21-settings-appearance"], ["notify", "22-settings-notifications"], ["caps", "23-settings-capabilities"], ["personal", "24-settings-memory"], ["connectors", "25-settings-connectors"], ["general", "26-settings-account"], ["models", "27-settings-models"], ["usage", "28-settings-usage"], ["billing", "29-settings-billing"], ["permissions", "30-settings-permissions"], ["data", "31-settings-data"], ["about", "32-settings-about"], ["advanced", "33-settings-advanced"]];
    for (const [k, n] of pages) { if (narrow && !["profile", "caps", "connectors", "usage"].includes(k)) continue; await p.evaluate(k => renderSection(k), k); await p.waitForTimeout(700); await shot(n); }
    console.log(tag, theme, "errors:", errs.join(" | ") || "none");
    await p.close();
  }
  await b.close();
})().catch(e => { console.error("script error:", e.message); process.exit(1); });
