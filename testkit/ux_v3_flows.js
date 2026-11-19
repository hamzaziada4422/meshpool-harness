// UX v3 flows: the new shell, driven like a person would (clicks and keys), against the mock model. PASS/FAIL per check.
// It carries over every check of ux_v2_flows.js (projects, sidebar rows, menus, keyboard, panels, settings reach) onto the
// new selectors, then covers the Chat/Code modes, search dialog, composer menus, gallery pages, account menu, Settings
// modal (hash, nav, appearance, models and providers), unread dots, shortcuts, reduced motion and the narrow layout.
// Usage: python3 testkit/mock_llm_long.py 8911 /tmp/mock.jsonl &  then an app on PORT (home with the mock key, see run_all.sh)
//        node testkit/ux_v3_flows.js PORT PROJECT_DIR [--strict]
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
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); localStorage.setItem("mp_theme", '"dark"'); localStorage.setItem("mp_cards_off", '["key","github","project"]'); } catch (e) {} });
  await p.goto(BASE + "/"); await p.waitForSelector("#input"); await p.waitForTimeout(1200);
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  const more = async sel => { await p.hover(sel); await p.waitForTimeout(220); await p.click(sel + " .more"); };
  const waitIdle = id => p.waitForFunction(id => api("/api/agent/" + id + "?since=0").then(v => v.state === "idle"), id, {timeout: 120000, polling: 1500});
  const nav = id => p.click(`#sideList .nv[data-nv="${id}"]`);
  const texts = sel => p.$$eval(sel, e => e.map(x => x.textContent.trim()));
  const menuItems = () => texts("#ctx .mi .ml");

  // ---------------- the shell
  check("sidebar has the Chat and Code mode buttons", !!(await p.$("#modeChat")) && !!(await p.$("#modeCode")));
  const navs = await texts('#sideList .nav .nv');
  check("nav rows: New, Search, Projects, Artifacts, Scheduled, Plugins", navs.join("|") === "New|Search|Projects|Artifacts|Scheduled|Plugins", navs.join("|"));
  check("composer: plus, model, permission mode, send", !!(await p.$("#attBtn")) && !!(await p.$("#mPill")) && !!(await p.$("#aPill")) && !!(await p.$("#sendBtn")));
  check("composer has no microphone or voice controls", (await p.$$eval("#composer button, #selrow button", e => e.filter(x => /voice|dictat|microphone/i.test(x.getAttribute("aria-label") || x.dataset.tip || "")).length)) === 0);
  check("home greets without a folder prompt", !/folder/i.test(await p.textContent(".hero")));
  check("no Claude or Anthropic anywhere on screen", !/claude|anthropic/i.test(await p.evaluate(() => document.body.innerText)));
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

  // ---------------- projects: from the Projects page
  await nav("projects"); await p.waitForSelector("#gNew");
  await p.click("#gNew"); await p.fill("#dlgIn", "Board report"); await p.keyboard.press("Enter");
  await p.waitForSelector(".pjpage", {timeout: 10000});
  check("create project opens its page", /Board report/.test(await p.textContent("#pjName")));
  check("new project is pinned in the sidebar", !!(await p.$("#sideList .th.pr")));
  await p.click("#pjInsEdit"); await p.fill("#etIn", "Always answer in bullet points."); await p.click('[data-a="1"]');
  await p.waitForSelector(".card .clamp", {timeout: 5000});
  check("instructions saved and shown", /bullet points/.test(await p.textContent(".card .clamp")));
  const kf = path.join(os.tmpdir(), "brief.md"); fs.writeFileSync(kf, "# Brief\nThe board meets on 3 March.\n");
  await p.setInputFiles("#pjFileIn", kf);
  await p.waitForSelector(".klist li", {timeout: 8000});
  check("knowledge file listed", /brief\.md/.test(await p.textContent(".klist")));
  await p.click("[data-kdel]"); await p.waitForFunction(() => !document.querySelector(".klist"), null, {timeout: 5000});
  check("knowledge file removed", true);
  const pid = await p.evaluate(() => S.pjId);
  await api(`/api/pj/${pid}`, {action: "memory_add", text: "The board prefers one-page summaries"});
  await p.evaluate(id => drawProject(id), pid); await p.waitForSelector(".mem li");
  check("memory shown", /one-page/.test(await p.textContent(".mem")));
  await p.click("#pjMemClear"); await p.click(".dlg [data-a='1']");
  await p.waitForFunction(() => !document.querySelector(".mem"), null, {timeout: 5000});
  check("memory cleared after a confirm", true);
  check("composer says it starts a chat in the project", /Start a chat in Board report/.test(await p.getAttribute("#input", "placeholder")));
  await p.fill("#input", "hello from the project"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-ai", {timeout: 60000});
  const second = await p.evaluate(() => S.cur.id); await waitIdle(second);
  check("project chat is in the project", (await api(`/api/agent/${second}?since=0`)).project_id === pid);
  check("breadcrumb shows the project", /Board report/.test(await p.textContent("#pjChip")));

  // ---------------- sidebar rows: pinned project expands to its chats, hover menu, rename, pin, move, delete
  await p.evaluate(() => loadThreads()); await p.waitForTimeout(600);
  await p.click(`#sideList [data-ptog="${pid}"]`);
  check("a pinned project expands in place to show its chats", !!(await p.$(`#sideList .threads.sub .th[data-id="${second}"]`)));
  await p.click(`#sideList [data-ptog="${pid}"]`);
  const heads = await texts("#sideList .sec .sechd");
  check("sidebar sections: Pinned and Recents", /^Pinned/.test(heads[0]) && heads.some(h => /^Recents/.test(h)), heads.join(" | "));
  const row = `#sideList .th[data-id="${first}"]`;
  await p.hover(row); await p.waitForTimeout(300);
  check("hover shows the options button", await p.$eval(`${row} .more`, e => getComputedStyle(e).opacity === "1"));
  await more(row);
  const items = await menuItems();
  check("menu: Rename, Pin, Add to project, Archive, Delete", ["Rename", "Pin", "Add to project", "Archive", "Delete"].every(x => items.includes(x)), items.join(", "));
  await p.keyboard.press("Escape");
  check("Esc closes the menu", (await p.$("#ctx")) === null);
  await p.click(row, {button: "right"});
  check("right-click opens the same menu", (await p.$$eval("#ctx .mi .ml", e => e.length)) >= 5);
  await p.keyboard.press("Enter");
  await p.waitForSelector(`${row} .rn`, {timeout: 3000});
  await p.fill(`${row} .rn`, "Renamed by keyboard"); await p.keyboard.press("Enter");
  await p.waitForFunction(id => S.threads.find(t => t.id === id)?.title === "Renamed by keyboard", first, {timeout: 5000});
  check("inline rename: Enter saves", true);
  await p.focus(row); await p.keyboard.press("F2"); await p.waitForSelector(`${row} .rn`);
  await p.fill(`${row} .rn`, "Should not stick"); await p.keyboard.press("Escape");
  await p.waitForTimeout(400);
  check("inline rename: Esc cancels", (await p.evaluate(id => S.threads.find(t => t.id === id).title, first)) === "Renamed by keyboard");
  await p.focus(row); await p.keyboard.press("F2"); await p.waitForSelector(`${row} .rn`);
  await p.fill(`${row} .rn`, "typing"); await p.evaluate(() => loadThreads()); await p.waitForTimeout(500);
  check("rename box survives a list refresh", (await p.inputValue(`${row} .rn`)) === "typing");
  await p.keyboard.press("Escape");
  await more(row); await p.click('#ctx .mi:has-text("Pin")');
  await p.waitForFunction(id => S.threads.find(t => t.id === id)?.pinned, first, {timeout: 5000});
  check("pinned chat is listed under Pinned", /^Pinned/.test((await texts("#sideList .sec .sechd"))[0]) && !!(await p.$(`#sideList .threads.prj > .th[data-id="${first}"]`)));
  await more(row); await p.click('#ctx .mi:has-text("Add to project")');
  await p.click('#ctx .mi:has-text("Board report")');
  await p.waitForFunction(([id, pid]) => S.threads.find(t => t.id === id)?.project_id === pid, [first, pid], {timeout: 5000});
  check("add to project moves the chat", true);
  await p.evaluate(() => loadThreads());
  await more(`#sideList .th[data-id="${first}"]`); await p.click('#ctx .mi.danger');
  await p.waitForSelector(".dlg");
  check("delete asks first, with plain buttons", /Delete this chat\?/.test(await p.textContent(".dlg h3")) && /Keep chat/.test(await p.textContent(".dlg")));
  await p.click('.dlg [data-a="0"]');
  check("Keep chat keeps it", !!(await p.$(`#sideList .th[data-id="${first}"]`)));
  await p.focus('#sideList .nv[data-nv="plugins"]'); await p.keyboard.press("ArrowDown");
  check("Down from the nav rows focuses the first row", await p.evaluate(() => document.activeElement.matches("#sideList [data-nav]")));
  await p.keyboard.press("Enter"); await p.waitForTimeout(500);
  check("Enter opens a row", await p.evaluate(() => !!S.cur || S.view === "project"));

  // ---------------- the chat title menu: P R A D while it is open
  await p.evaluate(id => openThread(id), second); await p.waitForTimeout(700);
  await p.click("#ttl");
  const titleItems = await menuItems();
  check("title menu: Pin, Rename, Add to project or Move, Archive, Delete", ["Pin", "Rename", "Archive", "Delete"].every(x => titleItems.includes(x)), titleItems.join(", "));
  await p.keyboard.press("p");
  await p.waitForFunction(id => S.threads.find(t => t.id === id)?.pinned, second, {timeout: 5000});
  check("title menu: P pins the chat", true);
  await p.click("#ttl"); await p.keyboard.press("r");
  check("title menu: R starts renaming", await p.evaluate(() => document.querySelector("#ttl").contentEditable === "true"));
  await p.keyboard.press("Escape");
  await p.click("#devBtn");
  check("device popover shows this computer as connected", /Connected/.test(await p.textContent("#ctx")));
  await p.keyboard.press("Escape");

  // ---------------- panel and sidebar state is remembered; the Step button toggles Progress
  await p.click("#panelBtn"); await p.waitForSelector("#panel:not([hidden])");
  check("Progress panel opens from the top bar button", (await p.$$eval("#ptabs [data-t]", e => e.map(x => x.textContent))).join() === "Session,Progress,Artifacts");
  await p.reload(); await p.waitForSelector("#input"); await p.waitForTimeout(1200);
  check("panel state is remembered", await p.evaluate(() => !document.querySelector("#panel").hidden));
  await p.click("#hideSide"); await p.waitForTimeout(400);
  check("collapsed sidebar: only the toggle remains at the top left", await p.evaluate(() => { const r = document.querySelector("#showSide")?.getBoundingClientRect(); return !!r && r.left < 40 && r.top < 40; }));
  await p.reload(); await p.waitForSelector("#input"); await p.waitForTimeout(800);
  check("collapsed sidebar is remembered", await p.evaluate(() => document.querySelector("#app").classList.contains("noside")));
  await p.keyboard.press("Control+b"); await p.waitForTimeout(400);
  check("Ctrl+B shows the sidebar again", await p.evaluate(() => !document.querySelector("#app").classList.contains("noside")));
  await p.keyboard.press("Control+b"); await p.waitForTimeout(300); await p.keyboard.press("Control+b");
  await p.evaluate(() => { if (S.panel) togglePanel(S.panel); });

  // ---------------- search dialog
  await p.keyboard.press("Control+k"); await p.waitForSelector("#srch .sres");
  check("Ctrl+K opens the search dialog with six type tabs", (await texts("#srch [data-tab]")).join("|") === "All|Artifacts|Sessions|Code|Projects|Scheduled");
  const groups = await texts("#srch .sgrp");
  check("search with an empty query: Recents and Actions (Needs attention when something is)", groups.includes("Recents") && groups.includes("Actions"), groups.join(" | "));
  check("the first row is highlighted", await p.evaluate(() => document.querySelector("#srch .sres").classList.contains("act")));
  check("footer lists the keyboard hints", /Close/.test(await p.textContent(".srch-keys")) && /Change type/.test(await p.textContent(".srch-keys")) && /Compose/.test(await p.textContent(".srch-keys")));
  await p.keyboard.press("ArrowRight");
  check("Right arrow changes the type", (await p.getAttribute('#srch [data-tab="artifacts"]', "aria-selected")) === "true");
  await p.keyboard.press("ArrowLeft");
  await p.route("**/api/hub/search*", async r => { if (r.request().url().includes("q=Board")) await new Promise(x => setTimeout(x, 1500)); r.continue(); });
  await p.fill("#srchIn", "Board");
  await p.waitForFunction(() => document.querySelector("#srchIn").value === "Board" && document.querySelector("#srch .sres.sk"), null, {timeout: 8000});
  check("loading shows skeleton rows and a spinner", !!(await p.$("#srch .sres.sk")) && (await p.$eval("#srchSpin", e => !e.hidden)), await p.evaluate(() => document.querySelector("#srchList").innerText.slice(0, 120)));
  await p.waitForFunction(() => !document.querySelector("#srch .sres.sk") && !/Actions/.test(document.querySelector("#srchList").innerText), null, {timeout: 15000}); await p.unroute("**/api/hub/search*");
  const sg = await texts("#srch .sgrp");
  check("a query searches chats and projects", sg.includes("Chats") && sg.includes("Projects"), sg.join(" | ") + " q=" + await p.inputValue("#srchIn"));
  await p.keyboard.press("ArrowDown"); await p.keyboard.press("Enter");
  await p.waitForTimeout(600);
  check("Enter opens the highlighted result and closes the dialog", (await p.$("#srch")) === null && await p.evaluate(() => !!S.cur || S.view === "project"));
  await p.keyboard.press("Control+k"); await p.waitForSelector("#srch"); await p.keyboard.press("Escape");
  check("Esc closes the search dialog", (await p.$("#srch")) === null);

  // ---------------- composer menus
  await p.evaluate(() => newThread()); await p.waitForTimeout(300);
  await p.click("#attBtn");
  const plus = await menuItems();
  check("plus menu: upload, plan first, memory, skills, plugins, agent", ["Upload or add files", "Plan first", "Memory", "Skills", "Plugins", "Agent"].every(x => plus.some(y => y.startsWith(x))), plus.join(", "));
  await p.click('#ctx .mi:has-text("Plan first")');
  check("Plan first shows as a chip on the composer", !!(await p.$("#planChip")));
  await p.click("#planChip");
  await p.click("#aPill");
  const perms = await menuItems();
  check("permission menu: Manual, Auto, Full access", ["Manual", "Auto", "Full access"].every(x => perms.some(y => y.startsWith(x))), perms.join(", "));
  await p.click('#ctx .mi:has-text("Manual")');
  check("choosing a mode changes the label at once", (await p.textContent("#aPill")).trim() === "Manual");
  await p.click("#aPill"); await p.click('#ctx .mi:has-text("Auto") >> nth=0'); check("Auto maps to editing freely", await p.evaluate(() => S.draft.mode) === "edits"); await p.keyboard.press("Escape");
  await p.click("#mPill");
  const mm = await menuItems();
  check("model menu: models, Effort, More models", mm.some(x => /Effort/.test(x)) && mm.some(x => /More models/.test(x)) && mm.length >= 4, mm.join(" | "));
  await p.keyboard.press("2");
  await p.waitForTimeout(200);
  check("number key picks the model in that position", (await p.$("#ctx")) === null);
  await p.click("#projSel");
  check("project selector: search, projects, New project", !!(await p.$(".listpop .lp-q")) && /New project/.test(await p.textContent(".listpop")) && /Board report/.test(await p.textContent(".listpop")));
  await p.fill(".lp-q", "zzzz"); check("project selector filters as you type", /No projects|Nothing/.test(await p.textContent(".listpop .lp-l")) || (await p.$$(".listpop .lp-l .lp-i")).length === 0);
  await p.keyboard.press("Escape");
  await p.fill("#input", "/"); await p.waitForSelector("#pop.inline");
  check("typing / in the composer opens the commands and skills list", (await p.$$("#pop.inline .opt")).length >= 5);
  await p.fill("#input", ""); await p.keyboard.press("Escape");

  // ---------------- Code mode
  await p.click("#modeCode"); await p.waitForTimeout(500);
  check("Code mode: wordmark, nav (New, Search, Artifacts, Plugins, More), no Projects", (await texts("#sideList .nav .nv")).join("|") === "New|Search|Artifacts|Plugins|More" && /Code/.test(await p.textContent("#wmode")));
  check("Code home: describe a task, environment and repo selectors", (await p.getAttribute("#input", "placeholder")) === "Describe a task or ask a question" && !!(await p.$("#envSel")) && !!(await p.$("#repoSel")));
  await p.click("#envSel");
  check("environment menu: local folder and GitHub workspace groups", /Local folder/.test(await p.textContent(".listpop")) && /GitHub workspace/.test(await p.textContent(".listpop")));
  await p.keyboard.press("Escape");
  await p.click("#repoSel");
  check("repo menu: No repository option and a way to connect GitHub", /No repository/.test(await p.textContent(".listpop")) && /Connect GitHub/.test(await p.textContent(".listpop")));
  await p.keyboard.press("Escape");
  await p.click('#sideList .nv[data-nv="more"]');
  check("More menu lists Scheduled", (await menuItems()).includes("Scheduled")); await p.keyboard.press("Escape");
  await api("/api/projects", {action: "add", path: proj}); await p.evaluate(() => loadProjects());
  await p.evaluate(p => { S.draft.project = p; S.draft.mode = "auto"; renderBar(); }, proj);
  await p.fill("#input", "hello code session"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-ai", {timeout: 60000});
  const codeId = await p.evaluate(() => S.cur.id); await waitIdle(codeId);
  await p.evaluate(() => loadThreads()); await p.waitForTimeout(400);
  check("a session started in Code mode is a code session", await p.evaluate(id => { const t = S.threads.find(x => x.id === id); return t.kind === "code" && !t.scratch; }, codeId));
  check("code sessions are listed in Code mode, not in Chat mode", !!(await p.$(`#sideList .th[data-id="${codeId}"]`)));
  await p.click("#modeChat"); await p.waitForTimeout(400);
  check("Chat mode hides code sessions", (await p.$(`#sideList .th[data-id="${codeId}"]`)) === null);

  // ---------------- unread dots come from the server
  await p.evaluate(() => { S.draft.mode = "auto"; newThread(); }); await p.fill("#input", "stream long please"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-user"); await p.waitForTimeout(800); const ur = await p.evaluate(() => S.cur.id);
  await p.evaluate(() => newThread()); { let calm = 0; const t0 = Date.now(); while (calm < 6 && Date.now() - t0 < 180000) { await p.waitForTimeout(1500); const ok = await p.evaluate(id => loadThreads().then(() => { const t = S.threads.find(t => t.id === id); return !!(t?.unread && t.state === "idle"); }), ur); calm = ok ? calm + 1 : 0; } } await p.waitForTimeout(500);
  const urRow = (await api("/api/agent")).sessions.find(t => t.id === ur);
  if (urRow.state !== "idle") console.log("   debug events:", JSON.stringify((await api(`/api/agent/${ur}?since=0`)).events.slice(-8).map(e => [e.type, String(e.text || e.name || "").slice(0, 60)])));
  check("a chat that finished while you were elsewhere is marked unread", (await p.$$(`#sideList .th[data-id="${ur}"] .dot.done`)).length === 1 && urRow.unread === true, JSON.stringify({unread: urRow.unread, state: urRow.state, dots: await p.$$eval(`#sideList .th[data-id="${ur}"] .dot`, e => e.map(x => x.className)), cur: await p.evaluate(() => S.cur?.id)}));
  await p.click(`#sideList .th[data-id="${ur}"]`); await p.waitForTimeout(600);
  check("opening it clears the dot on the server", (await api("/api/agent")).sessions.find(t => t.id === ur).unread === false);

  // ---------------- gallery pages
  await nav("projects"); await p.waitForSelector("#gNew");
  check("Projects page: title, search, sort, New project, cards", /Projects/.test(await p.textContent(".gh h1")) && !!(await p.$("#gSearch")) && !!(await p.$("#gSort")) && !!(await p.$("#gNew")) && (await p.$$(".gcard")).length >= 1);
  await p.click(".gcard >> nth=0"); await p.waitForSelector(".pjpage");
  check("a project card opens the project", true);
  await nav("artifacts"); await p.waitForSelector(".mkrow"); await p.waitForSelector(".gcard.art", {timeout: 8000}).catch(() => {});
  check("Artifacts page: tabs, banner, Make something new, cards", !!(await p.$(".gtabs")) && /Make something new/.test(await p.textContent(".gal")) && (await p.$$(".gcard.art")).length >= 1);
  await p.click("#gbX"); check("the banner dismisses and stays dismissed", await p.evaluate(() => JSON.parse(localStorage.getItem("mp_art_banner_off"))) === true);
  await nav("scheduled"); await p.waitForSelector("#tkNew");
  check("Scheduled page: tabs Yours and Templates, New task", (await texts(".gtabs button")).join("|") === "Yours|Templates" && !!(await p.$("#tkNew")));
  await p.click("#tkNew"); await p.fill("#tkName", "Daily recap"); await p.fill("#tkPrompt", "Summarise yesterday."); await p.click('.dlg [data-a="1"]');
  await p.waitForSelector(".gcard.task", {timeout: 8000});
  check("a new task shows as a card with its status line", /Daily recap/.test(await p.textContent(".gal")) && /Active/.test(await p.textContent(".gcard.task")));
  await p.click('[data-gtab="templates"]'); check("Templates tab lists starters", (await p.$$(".gcard.tpl")).length >= 3);
  await nav("plugins"); await p.waitForSelector("#plAdd");
  check("Plugins page: search, All/Connectors/Skills, Add, Your plugins", /Your plugins/.test(await p.textContent(".gal")) && (await texts(".gtabs.pills button")).join("|") === "All|Connectors|Skills" && !!(await p.$("#plAdd")));
  check("search and galleries stay free of Claude and Anthropic", !/claude|anthropic/i.test(await p.evaluate(() => document.body.innerText)));

  // ---------------- account menu, apps menu
  await p.click("#acctBtn");
  const acct = await menuItems();
  check("account menu rows", ["Settings", "Usage", "Language", "Get help", "Learn more", "View all plans", "Get apps and tools", "Get API keys"].every(x => acct.includes(x)), acct.join(", "));
  await p.keyboard.press("Escape");
  await p.click("#footBtn"); check("device button opens the apps menu", /command line tool/i.test(await p.textContent("#ctx"))); await p.keyboard.press("Escape");

  // ---------------- settings modal
  await p.keyboard.press("Control+,"); await p.waitForSelector(".modal");
  const snav = await texts(".mnav [data-g]");
  check("settings nav: Account, Billing, Usage, Appearance, Capabilities, Notifications, Privacy, Plugins, Code", ["Account", "Billing", "Usage", "Appearance", "Capabilities", "Notifications", "Privacy", "Plugins", "Code"].every(x => snav.includes(x)), snav.join(" | "));
  check("API keys is an external link at the bottom of the nav", !!(await p.$("#apiKeys")));
  await p.click('.mnav [data-g="appearance"]'); await p.waitForTimeout(500);
  check("the address hash follows the page", /#settings\/appearance/.test(await p.evaluate(() => location.hash)));
  await p.click('[data-th="light"]'); check("Theme control switches the theme", await p.evaluate(() => document.documentElement.dataset.theme) === "light");
  await p.click('[data-th="dark"]');
  await p.selectOption("#apMotion", "reduce"); check("Motion: reduce sets the attribute", await p.evaluate(() => document.documentElement.dataset.motion) === "reduce"); await p.selectOption("#apMotion", "system");
  await p.selectOption("#apPopups", "keep"); await p.evaluate(() => toast("A popup")); await p.waitForTimeout(300);
  check("Popups: keep until closed adds a close button", !!(await p.$(".toast .tx"))); await p.selectOption("#apPopups", "auto");
  await p.fill("#mnavq", "usage"); check("settings search filters the nav", (await p.$$eval(".mnav [data-g]:not([hidden])", e => e.length)) === 1); await p.fill("#mnavq", "");
  const reach = ["profile", "appearance", "notify", "caps", "personal", "web", "connectors", "skills", "plugins", "mcp", "general", "models", "harnesses", "usage", "billing", "permissions", "data", "import", "about", "scheduled", "terminal", "advanced", "company"];
  let missing = [];
  for (const k of reach) {
    await p.evaluate(k => renderSection(k), k); await p.waitForTimeout(350);
    const ok = await p.evaluate(k => !!document.querySelector(`#subtabs [data-sub="${k}"]`) && document.querySelector("#mbody").textContent.length > 20, k);
    if (!ok) missing.push(k);
  }
  check("every settings page is reachable (23 sections)", !missing.length, missing.join(","));
  await p.evaluate(() => renderSection("caps")); await p.waitForTimeout(500);
  const toggles = await p.$$eval("#mbody [data-t]", e => e.map(x => x.getAttribute("aria-label")));
  check("capabilities are plain toggles", toggles.includes("Memory") && toggles.includes("Web search"), toggles.join(", "));
  // models and providers: Base URL + key -> Test -> models -> pick -> Save
  await p.evaluate(() => renderSection("general")); await p.waitForSelector("#pvBase");
  check("Connection page: base URL, key shown masked, Test connection", !!(await p.$("#pvTest")) && (await p.inputValue("#pvMask")).startsWith("••••"));
  check("the saved key is never shown", !/test-key-1234/.test(await p.evaluate(() => document.querySelector("#mbody").innerHTML)));
  await p.fill("#pvBase", "http://127.0.0.1:8911/v1"); await p.click("#pvChange"); await p.fill("#pvKey", "test-key-1234");
  await p.click("#pvTest"); await p.waitForSelector("#pvModels .pvm", {timeout: 15000});
  check("Test connection reports the result and lists the endpoint's models", /Connected in \d+ ms/.test(await p.textContent("#pvRes")) && (await p.$$("#pvModels .pvm")).length >= 2);
  check("nothing is preselected and Save waits for a choice", (await p.$$("#pvModels .pvm[aria-checked=true]")).length === 0 && await p.$eval("#pvSave", e => e.disabled));
  await p.fill("#pvQ", "flash"); check("the model list is searchable", (await p.$$("#pvModels .pvm")).length >= 1);
  await p.click("#pvModels .pvm >> nth=0"); check("picking a model enables Save", await p.$eval("#pvSave", e => !e.disabled));
  await p.click("#pvSave"); await p.waitForFunction(() => [...document.querySelectorAll(".toast")].some(t => /Saved/.test(t.textContent)), null, {timeout: 10000});
  check("Save applies the default model", (await texts(".toast")).some(x => /Saved/.test(x)));
  await p.keyboard.press("Escape"); await p.evaluate(() => closeSettings());
  check("closing Settings clears the hash", !/settings/.test(await p.evaluate(() => location.hash)));

  // ---------------- hub routes behind the pages
  check("hub routes answer", (await api("/api/hub/account")).device.connected === true && Array.isArray((await api("/api/hub/artifacts")).artifacts) && Array.isArray((await api("/api/hub/scheduled")).tasks) && Array.isArray((await api("/api/hub/search?q=hello")).groups));

  // ---------------- reduced motion and the narrow layout
  const rm = await b.newPage({viewport: {width: 1274, height: 744}, reducedMotion: "reduce"});
  await rm.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await rm.goto(BASE + "/"); await rm.waitForSelector("#input"); await rm.waitForTimeout(800); await rm.click("#attBtn");
  check("prefers-reduced-motion turns menu animation off", await rm.$eval("#ctx", e => getComputedStyle(e).animationName === "none")); await rm.close();
  const nr = await b.newPage({viewport: {width: 390, height: 844}});
  await nr.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {} });
  await nr.goto(BASE + "/"); await nr.waitForSelector("#input"); await nr.waitForTimeout(1000);
  const noScroll = async () => nr.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
  let narrowOk = await noScroll();
  await nr.click("#showSide"); await nr.waitForTimeout(300); narrowOk = narrowOk && await noScroll();
  await nr.click('.nv[data-nv="artifacts"]'); await nr.waitForTimeout(900); narrowOk = narrowOk && await noScroll();
  check("narrow width: no sideways scroll on home, sidebar or a gallery page", narrowOk);
  await nr.close();

  check("no page errors", !errs.length, errs.join(" | "));
  const bad = results.filter(x => !x).length;
  console.log(`\n${results.length - bad}/${results.length} checks pass`);
  await b.close();
  if (process.argv.includes("--strict") && bad) process.exit(1);
})().catch(e => { console.error("script error:", e.message); process.exit(1); });
