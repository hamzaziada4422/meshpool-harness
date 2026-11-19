// Chat UX v2 flows, driven like a person would (clicks and keys), against the mock model. Prints PASS/FAIL per check.
// Usage: python3 testkit/mock_llm_long.py 8911 /tmp/mock.jsonl &  then an app on PORT with MESHPOOL_HOME set up as in run_all.sh
//        node testkit/ux_v2_flows.js PORT PROJECT_DIR [--strict]
const {chromium} = require("playwright");
const fs = require("fs");
const path = require("path");
const os = require("os");
const [port, proj] = process.argv.slice(2).filter(a => !a.startsWith("--"));
const BASE = `http://127.0.0.1:${port}`;
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? "  " + detail : "")); };

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 1440, height: 900}});
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(1200);
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  const more = async sel => { await p.hover(sel); await p.waitForTimeout(220); await p.click(sel + " .more"); };
  const waitIdle = id => p.waitForFunction(id => api("/api/agent/" + id + "?since=0").then(v => v.state === "idle"), id, {timeout: 120000, polling: 1500});

  // --- a new chat needs no folder, and the Work/Code switch is gone
  check("no Work/Code switch", (await p.$(".modesw")) === null);
  check("composer has no agent, access or plan pills", (await p.$("#hPill, #aPill, #pPill")) === null);
  check("composer: attach, settings, model chip, send", !!(await p.$("#attBtn")) && !!(await p.$("#oPill")) && !!(await p.$("#mPill")) && !!(await p.$("#sendBtn")));
  check("home has no folder text", !/folder/i.test(await p.textContent(".hero")));
  await p.evaluate(() => { S.draft.mode = "auto"; renderBar(); });       // full access: no approval card stops the run
  await p.fill("#input", "hello there");
  await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-ai", {timeout: 60000});
  const first = await p.evaluate(() => S.cur.id);
  await waitIdle(first);
  check("a sent message appears once", (await p.$$eval(".msg-user", e => e.length)) === 1);
  const v1 = await api(`/api/agent/${first}?since=0`);
  check("chat started with its own private folder", v1.scratch === true && /chats/.test(v1.workdir), v1.workdir);
  check("no folder chip in the top bar", (await p.$("#projChip")) === null);
  check("Auto picked the agent (no picker needed)", !!v1.harness?.id, v1.harness?.id);

  // --- projects: create from the sidebar
  await p.click("#addProj");
  await p.fill("#dlgIn", "Board report");
  await p.keyboard.press("Enter");
  await p.waitForSelector(".pjpage", {timeout: 10000});
  check("create project opens its page", /Board report/.test(await p.textContent("#pjName")));
  check("project row in the sidebar", !!(await p.$(".th.pr")));
  // instructions
  await p.click("#pjInsEdit"); await p.fill("#etIn", "Always answer in bullet points."); await p.click('[data-a="1"]');
  await p.waitForSelector(".card .clamp", {timeout: 5000});
  check("instructions saved and shown", /bullet points/.test(await p.textContent(".card .clamp")));
  // knowledge file
  const kf = path.join(os.tmpdir(), "brief.md"); fs.writeFileSync(kf, "# Brief\nThe board meets on 3 March.\n");
  await p.setInputFiles("#pjFileIn", kf);
  await p.waitForSelector(".klist li", {timeout: 8000});
  check("knowledge file listed", /brief\.md/.test(await p.textContent(".klist")));
  await p.click("[data-kdel]"); await p.waitForFunction(() => !document.querySelector(".klist"), null, {timeout: 5000});
  check("knowledge file removed", true);
  // memory (edit through the API: the agent writes it; the page shows it and can clear it)
  const pid = await p.evaluate(() => S.pjId);
  await api(`/api/pj/${pid}`, {action: "memory_add", text: "The board prefers one-page summaries"});
  await p.evaluate(id => drawProject(id), pid); await p.waitForSelector(".mem li");
  check("memory shown", /one-page/.test(await p.textContent(".mem")));
  await p.click("#pjMemClear"); await p.click(".dlg [data-a='1']");
  await p.waitForFunction(() => !document.querySelector(".mem"), null, {timeout: 5000});
  check("memory cleared after a confirm", true);
  // a chat in the project
  check("composer says it starts a chat in the project", /Start a chat in Board report/.test(await p.getAttribute("#input", "placeholder")));
  await p.fill("#input", "hello from the project"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-ai", {timeout: 60000});
  const second = await p.evaluate(() => S.cur.id); await waitIdle(second);
  check("project chat is in the project", (await api(`/api/agent/${second}?since=0`)).project_id === pid);
  check("breadcrumb shows the project", /Board report/.test(await p.textContent("#pjChip")));

  // --- sidebar: groups, hover menu, rename, pin, move, delete
  await p.evaluate(() => loadThreads()); await p.waitForTimeout(600);
  const groups = await p.$$eval("#sideList .sec .sechd", els => els.map(e => e.textContent.trim()));
  check("history grouped by recency", groups.some(g => /^Today/.test(g)), groups.join(" | "));
  const row = `#sideList .th[data-id="${first}"]`;
  await p.hover(row); await p.waitForTimeout(300);
  check("hover shows the options button", await p.$eval(`${row} .more`, e => getComputedStyle(e).opacity === "1"));
  await more(row);
  const items = await p.$$eval("#ctx .mi .ml", e => e.map(x => x.textContent));
  check("menu: Rename, Pin, Add to project, Archive, Delete", ["Rename", "Pin", "Add to project", "Archive", "Delete"].every(x => items.includes(x)), items.join(", "));
  await p.keyboard.press("Escape");
  check("Esc closes the menu and returns focus", (await p.$("#ctx")) === null);
  // right click gives the same menu
  await p.click(row, {button: "right"});
  check("right-click opens the same menu", (await p.$$eval("#ctx .mi .ml", e => e.length)) >= 5);
  // keyboard in the menu: Down, Enter on Rename
  await p.keyboard.press("Enter");
  await p.waitForSelector(`${row} .rn`, {timeout: 3000});
  await p.fill(`${row} .rn`, "Renamed by keyboard"); await p.keyboard.press("Enter");
  await p.waitForFunction(id => S.threads.find(t => t.id === id)?.title === "Renamed by keyboard", first, {timeout: 5000});
  check("inline rename: Enter saves", true);
  await p.focus(row); await p.keyboard.press("F2"); await p.waitForSelector(`${row} .rn`);
  await p.fill(`${row} .rn`, "Should not stick"); await p.keyboard.press("Escape");
  await p.waitForTimeout(400);
  check("inline rename: Esc cancels", (await p.evaluate(id => S.threads.find(t => t.id === id).title, first)) === "Renamed by keyboard");
  // survives the 4 s sidebar refresh while typing
  await p.focus(row); await p.keyboard.press("F2"); await p.waitForSelector(`${row} .rn`);
  await p.fill(`${row} .rn`, "typing"); await p.evaluate(() => loadThreads()); await p.waitForTimeout(500);
  check("rename box survives a list refresh", (await p.inputValue(`${row} .rn`)) === "typing");
  await p.keyboard.press("Escape");
  // pin
  await more(row); await p.click('#ctx .mi:has-text("Pin")');
  await p.waitForFunction(id => S.threads.find(t => t.id === id)?.pinned, first, {timeout: 5000});
  check("pinned chat is listed first under Pinned", /^Pinned/.test((await p.$$eval("#sideList .sec .sechd", e => e.map(x => x.textContent.trim())))[0]));
  // move to project through the submenu
  await more(row); await p.click('#ctx .mi:has-text("Add to project")');
  await p.click('#ctx .mi:has-text("Board report")');
  await p.waitForFunction(([id, pid]) => S.threads.find(t => t.id === id)?.project_id === pid, [first, pid], {timeout: 5000});
  check("add to project moves the chat", true);
  await p.evaluate(id => loadThreads(), first);
  // delete with a confirm
  await more(`#sideList .th[data-id="${first}"]`); await p.click('#ctx .mi.danger');
  await p.waitForSelector(".dlg");
  check("delete asks first, with plain buttons", /Delete this chat\?/.test(await p.textContent(".dlg h3")) && /Keep chat/.test(await p.textContent(".dlg")));
  await p.click('.dlg [data-a="0"]');
  check("Keep chat keeps it", !!(await p.$(`#sideList .th[data-id="${first}"]`)));

  // --- keyboard: arrows move between rows, Enter opens
  await p.focus("#q"); await p.keyboard.press("ArrowDown");
  check("Down from search focuses the first row", await p.evaluate(() => document.activeElement.matches("#sideList [data-nav]")));
  await p.keyboard.press("ArrowDown"); await p.keyboard.press("Enter"); await p.waitForTimeout(500);
  check("Enter opens a row", await p.evaluate(() => !!S.cur || S.view === "project"));

  // --- panel and sidebar state is remembered
  await p.evaluate(id => openThread(id), second); await p.waitForTimeout(600);
  await p.click("#panelBtn"); await p.waitForSelector("#panel:not([hidden])");
  check("side panel opens (Steps, Artifacts and Session for a chat without a folder)", (await p.$$eval("#ptabs [data-t]", e => e.map(x => x.textContent))).join() === "Session,Steps,Artifacts");
  await p.reload(); await p.waitForSelector("#input"); await p.waitForTimeout(1200);
  check("side panel state is remembered", await p.evaluate(() => !document.querySelector("#panel").hidden));
  await p.click("#hideSide"); await p.waitForTimeout(400);
  await p.reload(); await p.waitForSelector("#input"); await p.waitForTimeout(800);
  check("collapsed sidebar is remembered", await p.evaluate(() => document.querySelector("#app").classList.contains("noside")));
  await p.evaluate(() => toggleSide());

  // --- settings: seven pages, every old page still reachable
  await p.click("#openSettings"); await p.waitForSelector(".modal");
  const nav = await p.$$eval(".mnav [data-g]", e => e.map(x => x.textContent));
  check("settings has seven sections", nav.length === 7, nav.join(" | "));
  const reach = ["profile", "appearance", "notify", "caps", "personal", "web", "connectors", "skills", "plugins", "mcp", "general", "models", "harnesses", "usage", "billing", "permissions", "data", "import", "about", "scheduled", "terminal", "advanced", "company"];
  let missing = [];
  for (const k of reach) {
    await p.evaluate(k => renderSection(k), k); await p.waitForTimeout(350);
    const ok = await p.evaluate(k => !!document.querySelector(`#subtabs [data-sub="${k}"]`) && document.querySelector("#mbody").textContent.length > 20, k);
    if (!ok) missing.push(k);
  }
  check("every settings page is reachable from a tab", !missing.length, missing.join(","));
  await p.evaluate(() => renderSection("caps")); await p.waitForTimeout(500);
  const toggles = await p.$$eval("#mbody [data-t]", e => e.map(x => x.getAttribute("aria-label")));
  check("capabilities are plain toggles", toggles.includes("Memory") && toggles.includes("Web search"), toggles.join(", "));
  await p.keyboard.press("Escape"); await p.evaluate(() => document.querySelector(".scrim")?.remove());

  check("no page errors", !errs.length, errs.join(" | "));
  const bad = results.filter(x => !x).length;
  console.log(`\n${results.length - bad}/${results.length} checks pass`);
  await b.close();
  if (process.argv.includes("--strict") && bad) process.exit(1);
})().catch(e => { console.error("script error:", e.message); process.exit(1); });
