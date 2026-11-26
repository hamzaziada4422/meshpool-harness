/* Gallery pages: Projects, Artifacts, Scheduled tasks and Plugins. One pattern: a title, tabs, toolbar icons, an
   optional banner, then a grid of cards. Loaded before app.js; each page draws into #col when it is opened. */
"use strict";

const PAGE_TITLE = {projects: "Projects", artifacts: "Artifacts", scheduled: "Scheduled tasks", plugins: "Plugins", memory: "What meshpool knows", work: "Your work"};
function relDate(t) {
  t = Number(t) > 1e12 ? Number(t) / 1000 : Number(t); if (!t) return "";
  const d = Math.floor((Date.now() / 1000 - t) / 86400);
  if (d < 1) return "Today"; if (d === 1) return "Yesterday"; if (d < 7) return `${d} days ago`;
  return new Date(t * 1000).toLocaleDateString("en", {month: "short", day: "numeric", ...(new Date(t * 1000).getFullYear() !== new Date().getFullYear() ? {year: "numeric"} : {})});
}
const G = {tab: {}, q: {}, sort: {projects: "recent"}, view: "", find: {}};
function gHead(name, {tabs, tools = "", extra = ""}) {
  return `<header class="gh"><h1>${PAGE_TITLE[name]}</h1>${extra}</header>
    <div class="gbar"><div class="gtabs" role="tablist">${(tabs || []).map(([k, l]) => `<button role="tab" data-gtab="${k}" aria-selected="${(G.tab[name] || tabs[0][0]) === k}">${esc(l)}</button>`).join("")}</div><span class="sp"></span>${tools}</div>
    <div class="gfind" id="gfind" ${G.find[name] ? "" : "hidden"}><input id="gq" placeholder="Search ${PAGE_TITLE[name].toLowerCase()}" value="${esc(G.q[name] || "")}" aria-label="Search ${PAGE_TITLE[name].toLowerCase()}" autocomplete="off"></div>`;
}
const gBtn = (id, ic, tip, on) => `<button class="iconbtn gbtn ${on ? "on" : ""}" id="${id}" data-tip="${tip}" data-tip-pos="below" aria-label="${tip}">${ICON[ic]}</button>`;
function bindHead(name, redraw) {
  $$("[data-gtab]").forEach(b => b.onclick = () => { G.tab[name] = b.dataset.gtab; redraw(); });
  $("#gSearch")?.addEventListener("click", () => { G.find[name] = !G.find[name]; $("#gfind").hidden = !G.find[name]; if (G.find[name]) $("#gq").focus(); else { G.q[name] = ""; redraw(); } });
  $("#gq")?.addEventListener("input", e => { G.q[name] = e.target.value; const inp = e.target; clearTimeout(G.qT); G.qT = setTimeout(() => { const pos = inp.selectionStart; redraw(true); const nq = $("#gq"); if (nq && pos != null) { nq.focus({preventScroll: true}); try { nq.setSelectionRange(pos, pos); } catch {} } }, 120); });
  if (G.find[name]) { const q = $("#gq"); if (q && document.activeElement !== q && !G.noFocus) q.focus({preventScroll: true}); }
}
function drawPage(name) {
  if (S.view !== "page" || S.page !== name) return;
  ({projects: pageProjects, artifacts: pageArtifacts, scheduled: pageScheduled, plugins: pagePlugins,
    memory: window.pageMemory, work: window.pageYourWork})[name]?.();
}
const shell0 = html => { const c = $("#col"); if (c) c.innerHTML = `<div class="gal">${html}</div>`; };
const gcard = (id, inner, cls = "") => `<div class="gcard ${cls}" tabindex="0" data-id="${esc(id)}">${inner}</div>`;

/* ---------- Projects ---------- */
function pageProjects() {
  const q = (G.q.projects || "").toLowerCase();
  let list = S.pj.filter(p => !q || p.name.toLowerCase().includes(q) || (p.description || "").toLowerCase().includes(q));
  list = [...list].sort(G.sort.projects === "name" ? (a, z) => a.name.localeCompare(z.name) : (a, z) => (z.updated || 0) - (a.updated || 0));
  list = [...list.filter(p => p.pinned), ...list.filter(p => !p.pinned)];
  const cards = list.map(p => gcard(p.id, `<span class="gico">${ICON.stack}</span><span class="gt"><b>${esc(p.name)}</b><small>${esc(relDate(p.updated))}${p.link ? " · " + (p.link.type === "github" ? "GitHub" : "Folder") : ""}</small></span>${p.pinned ? `<span class="gpin" title="Pinned">${ICON.pin}</span>` : ""}<button class="iconbtn more" data-pmenu="${esc(p.id)}" aria-label="Options for ${esc(p.name)}">${ICON.more}</button>`, "proj")).join("");
  const body = cards ? `<div class="ggrid c2">${cards}</div>` : `<div class="gempty"><b>${q ? "No projects match." : "No projects yet."}</b><span>${q ? "Try fewer words." : "A project keeps related chats together, with its own instructions, files and memory."}</span>${q ? "" : `<button class="btn primary" id="gNew2">New project</button>`}</div>`;
  const keep = $("#gq") && document.activeElement === $("#gq");
  if (!$(".gal .gcards")) {
    shell0(gHead("projects", {tabs: [["all", "All projects"]], tools: `${gBtn("gSearch", "search", "Search", G.find.projects)}${gBtn("gSort", "sort", "Sort")}<button class="btn primary" id="gNew">New project</button>`}) + `<div class="gcards" id="gcards"></div>`);
    $("#gNew").onclick = () => newProjectFlow();
    $("#gSort").onclick = e => ctxMenu({type: "click", currentTarget: e.currentTarget}, [["recent", "Recent activity"], ["name", "Name"]].map(([k, l]) => ({label: l, checked: G.sort.projects === k ? true : undefined, fn: () => { G.sort.projects = k; pageProjects(); }})));
    bindHead("projects", pageProjects);
  }
  $("#gcards").innerHTML = body;
  $("#gNew2")?.addEventListener("click", () => newProjectFlow());
  if (keep) $("#gq")?.focus();
  const grid = $("#gcards");
  grid.onclick = e => { const m = e.target.closest("[data-pmenu]"); if (m) { e.stopPropagation(); return projMenu({type: "click", currentTarget: m}, m.dataset.pmenu); } const c = e.target.closest(".gcard"); if (c) openProject(c.dataset.id); };
  grid.onkeydown = e => { if (e.key === "Enter" && e.target.classList.contains("gcard")) openProject(e.target.dataset.id); };
  grid.oncontextmenu = e => { const c = e.target.closest(".gcard"); if (c) { e.preventDefault(); projMenu(e, c.dataset.id); } };
}

/* ---------- Artifacts ---------- */
const MAKE = [
  ["Docs", "blue", "docx", "Write a Word document: "], ["Slides", "orange", "pptx", "Make a slide deck about "], ["Sheets", "green", "xlsx", "Build an Excel spreadsheet that "],
  ["PDF", "red", "pdf", "Make a PDF report on "], ["Data", "purple", "json", "Analyse the data I attach and "], ["Web page", "pink", "html", "Build a single web page that "],
];
const TYPE_IC = {Docs: "files", Slides: "grid", Sheets: "lines", PDF: "file", Data: "list", Web: "globe", Images: "eye"};
async function pageArtifacts() {
  G.view = G.view || store("art_view") || "grid";
  if (!G.art) { shell0(`<header class="gh"><h1>Artifacts</h1></header><div class="gskel">${Array(6).fill(`<div class="gcard sk"><div class="gth"></div><div class="gm"></div></div>`).join("")}</div>`); }
  let d;
  try { d = G.art = await api("/api/hub/artifacts"); } catch (e) { return shell0(`<header class="gh"><h1>Artifacts</h1></header><div class="gempty"><b>Could not load artifacts.</b><span>${esc(e.message)}</span></div>`); }
  if (S.view !== "page" || S.page !== "artifacts") return;
  const draw = () => {
    const tab = G.tab.artifacts || "all", q = (G.q.artifacts || "").toLowerCase();
    const types = Object.keys(d.types);
    const list = d.artifacts.filter(a => (tab === "all" || a.type === tab) && (!q || a.name.toLowerCase().includes(q) || a.session_title.toLowerCase().includes(q)));
    const banner = store("art_banner_off") ? "" : `<div class="gbanner"><div><b>Everything meshpool makes lands here</b><span>Documents, sheets, decks and images from all your chats are collected on this page. Open one to see it in its own app.</span></div><button class="iconbtn" id="gbX" aria-label="Dismiss">${ICON.x}</button></div>`;
    const tiles = MAKE.map(([n, tint, ext, prompt]) => `<button class="mk ${tint}" data-mk="${esc(prompt)}"><span class="mkt">${ICON[TYPE_IC[n] || "file"]}</span><span class="mkl">${n}</span></button>`).join("");
    const card = a => `<div class="gcard art ${G.view}" tabindex="0" data-open="${esc(a.id)}"><div class="gth ${a.tint}">${/^(png|jpe?g|gif|svg)$/.test(a.ext) ? `<img alt="" loading="lazy" src="/raw?t=${encodeURIComponent(MP.token)}&root=${encodeURIComponent(a.root)}&path=${encodeURIComponent(a.path)}">` : `<span class="gx">${ICON[TYPE_IC[a.type] || "file"]}<b>.${esc(a.ext)}</b></span>`}</div><div class="gm"><b title="${esc(a.name)}">${esc(a.name)}</b><small>${esc(a.type)} · ${esc(relDate(a.updated))}</small></div></div>`;
    const body = list.length ? `<div class="ggrid ${G.view === "list" ? "c1" : "c3"}">${list.map(card).join("")}</div>` : `<div class="gempty"><b>${d.artifacts.length ? "No artifacts match." : "Nothing made yet."}</b><span>${d.artifacts.length ? "Try another type or fewer words." : "Ask meshpool for a document, a sheet or a deck. Files it makes show up here."}</span></div>`;
    shell0(gHead("artifacts", {tabs: [["all", "All"], ...types.map(t => [t, t])], tools: `${gBtn("gSearch", "search", "Search", G.find.artifacts)}${gBtn("gView", G.view === "list" ? "grid" : "lines", G.view === "list" ? "Grid view" : "List view")}${gBtn("gPlus", "plus", "New artifact")}`}) + banner + `<h2 class="gsub">Make something new</h2><div class="mkrow">${tiles}</div><div id="gart">${body}</div>`);
    bindHead("artifacts", () => draw());
    $("#gbX")?.addEventListener("click", () => { store("art_banner_off", true); draw(); });
    $("#gView").onclick = () => { G.view = G.view === "list" ? "grid" : "list"; store("art_view", G.view); draw(); };
    $("#gPlus").onclick = e => newArtifact(e.currentTarget);
    $$("[data-mk]").forEach(b => b.onclick = () => { newThread(); const i = $("#input"); i.value = b.dataset.mk; i.focus(); i.dispatchEvent(new Event("input")); });
    const open = id => { const a = d.artifacts.find(x => x.id === id); if (a) openPreview(a.root, a.path); };
    $$("[data-open]").forEach(c => { c.onclick = () => open(c.dataset.open); c.onkeydown = e => { if (e.key === "Enter") open(c.dataset.open); }; c.oncontextmenu = e => { e.preventDefault(); const a = d.artifacts.find(x => x.id === c.dataset.open); ctxMenu(e, [{label: "Open", icon: ICON.files, fn: () => open(a.id)}, {label: "Open the chat", icon: ICON.chat, fn: () => openThread(a.session_id)}]); }; });
  };
  draw();
}
function newArtifact(anchor) {
  ctxMenu({type: "click", currentTarget: anchor || $("#gPlus") || $("#sideList")}, MAKE.map(([n, , , prompt]) => ({label: n, icon: ICON[TYPE_IC[n] || "file"], fn: () => { newThread(); const i = $("#input"); i.value = prompt; i.focus(); i.dispatchEvent(new Event("input")); }})));
}

/* ---------- Scheduled tasks ---------- */
const WHENS = [["daily", "Every day"], ["weekdays", "Every weekday"], ["weekly", "Every week"], ["hourly", "Every hour"], ["every", "Every few minutes"], ["once", "Once"]];
const DAYS = [["mon", "Monday"], ["tue", "Tuesday"], ["wed", "Wednesday"], ["thu", "Thursday"], ["fri", "Friday"], ["sat", "Saturday"], ["sun", "Sunday"]];
function taskDialog(t = {}) {
  return new Promise(res => {
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    sc.innerHTML = `<div class="dlg wide" role="dialog" aria-modal="true" aria-label="${t.id ? "Edit task" : "New task"}"><h3>${t.id ? "Edit task" : "New task"}</h3>
      <label class="field"><span>Name</span><input id="tkName" value="${esc(t.name || "")}" placeholder="Morning briefing" maxlength="80"></label>
      <label class="field"><span>What should meshpool do?</span><textarea id="tkPrompt" rows="4" placeholder="Describe the task as you would to a colleague.">${esc(t.prompt || "")}</textarea></label>
      <div class="tkrow"><label class="field"><span>Repeat</span><select id="tkWhen">${WHENS.map(([k, l]) => `<option value="${k}" ${k === (t.when || "daily") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field" id="tkDayF"><span>Day</span><select id="tkDay">${DAYS.map(([k, l]) => `<option value="${k}" ${k === (t.day || "mon") ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="field" id="tkTimeF"><span>Time</span><input id="tkTime" type="time" value="${esc(t.time || "09:00")}"></label>
      <label class="field" id="tkMinF"><span>Minutes</span><input id="tkMin" type="number" min="15" value="${t.minutes || 60}"></label>
      <label class="field" id="tkAtF"><span>Date and time</span><input id="tkAt" type="datetime-local" value="${esc(t.at || "")}"></label></div>
      <label class="field" id="tkSkipF"><span>Skip a run that is missed by more than (hours)</span><input id="tkSkip" type="number" min="0" step="1" value="${t.skip_if_missed_h || ""}" placeholder="Never skip: run it late"></label>
      <label class="switchrow"><input type="checkbox" id="tkChange" ${t.only_on_change ? "checked" : ""}> Only tell me when something changed</label>
      <div class="row"><button class="btn" data-a="0">Cancel</button><button class="btn primary" data-a="1">${t.id ? "Save task" : "Create task"}</button></div></div>`;
    document.body.appendChild(sc);
    const touched = new Set();
    for (const [k, id] of [["time", "tkTime"], ["day", "tkDay"], ["minutes", "tkMin"], ["at", "tkAt"], ["only_on_change", "tkChange"], ["skip_if_missed_h", "tkSkip"]]) $("#" + id, sc).addEventListener("change", () => touched.add(k));
    const show = () => { const w = $("#tkWhen", sc).value; $("#tkDayF", sc).hidden = w !== "weekly"; $("#tkTimeF", sc).hidden = !["daily", "weekdays", "weekly"].includes(w); $("#tkMinF", sc).hidden = w !== "every"; $("#tkAtF", sc).hidden = w !== "once"; };
    $("#tkWhen", sc).onchange = show; show();
    const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); res(v); };
    const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
    document.addEventListener("keydown", key, true);
    sc.addEventListener("mousedown", e => { if (e.target === sc) done(null); });
    $("[data-a='0']", sc).onclick = () => done(null);
    $("[data-a='1']", sc).onclick = () => {
      const v = {name: $("#tkName", sc).value.trim(), prompt: $("#tkPrompt", sc).value.trim(), when: $("#tkWhen", sc).value, time: $("#tkTime", sc).value, day: $("#tkDay", sc).value, minutes: +$("#tkMin", sc).value || 60, at: $("#tkAt", sc).value, only_on_change: $("#tkChange", sc).checked, skip_if_missed_h: +$("#tkSkip", sc).value || 0};
      if (t.id) {                                  // editing: send only what the person changed, never the dialog's defaults
        const init = {when: t.when || "daily", time: t.time || "09:00", day: t.day || "mon", minutes: t.minutes || 60, at: t.at || "", only_on_change: !!t.only_on_change, skip_if_missed_h: t.skip_if_missed_h || 0};
        if (v.when === init.when) for (const k of ["time", "day", "minutes", "at"]) if (v[k] === init[k] && !touched.has(k)) delete v[k];
        if (v.only_on_change === init.only_on_change && !touched.has("only_on_change")) delete v.only_on_change;
        if (v.skip_if_missed_h === init.skip_if_missed_h && !touched.has("skip_if_missed_h")) delete v.skip_if_missed_h;
        if (v.when === init.when && t.when === undefined) delete v.when;
      } else {
        if (t.kind) v.kind = t.kind;
        if (t.folder) v.folder = t.folder;
      }
      if (!v.prompt) { $("#tkPrompt", sc).focus(); return; }
      done(v);
    };
    $("#tkName", sc).focus();
  });
}
async function taskSave(v, id) {
  try { await post("/api/assistant", id ? {action: "task_update", id, ...v} : {action: "task_add", ...v}); toast(id ? "Task saved" : "Task created"); return true; } catch (e) { toast(e.message, 5000); return false; }
}
async function pageScheduled() {
  let d;
  try { d = await api("/api/hub/scheduled"); } catch (e) { return shell0(`<header class="gh"><h1>Scheduled tasks</h1></header><div class="gempty"><b>Could not load tasks.</b><span>${esc(e.message)}</span></div>`); }
  if (S.view !== "page" || S.page !== "scheduled") return;
  const draw = () => {
    const tab = G.tab.scheduled || "yours", q = (G.q.scheduled || "").toLowerCase();
    const tasks = d.tasks.filter(t => !q || t.name.toLowerCase().includes(q) || t.prompt.toLowerCase().includes(q));
    const card = t => gcard(t.id, `<div class="gt"><b>${esc(t.name)}</b>${t.unread ? `<i class="bdot" aria-label="Unread result"></i>` : ""}<p class="clip2">${esc(t.prompt)}</p><small class="st ${t.enabled ? "on" : ""}">${esc(t.status)} · ${esc(t.schedule)}</small>${t.error ? `<small class="st bad">Could not start: ${esc(t.error)}</small>` : ""}${t.invalid ? `<small class="st bad">${esc(t.invalid)}</small>` : ""}${t.skipped ? `<small>Skipped a run: ${esc(t.skipped)}</small>` : ""}${t.running ? `<small>Running now</small>` : ""}</div>${t.project ? `<span class="gchip">${ICON.stack}${esc(t.project)}</span>` : ""}<button class="iconbtn more" data-tk="${esc(t.id)}" aria-label="Options for ${esc(t.name)}">${ICON.more}</button>`, "task");
    const tpl = (t, i) => gcard("t" + i, `<div class="gt"><b>${esc(t.name)}</b><p class="clip2">${esc(t.prompt)}</p><small>${esc(WHENS.find(w => w[0] === t.when)?.[1] || "")}${t.time ? " at " + t.time : ""}</small></div>`, "task tpl");
    const body = tab === "templates" ? `<div class="ggrid c2">${d.templates.map(tpl).join("")}</div>` : tasks.length ? `<div class="ggrid c2">${tasks.map(card).join("")}</div>` : `<div class="gempty"><b>${q ? "No tasks match." : "No scheduled tasks yet."}</b><span>${q ? "Try fewer words." : "Have meshpool do something for you on a schedule: a daily briefing, a weekly recap, a repository check."}</span>${q ? "" : `<button class="btn primary" id="tkNew2">New task</button>`}</div>`;
    shell0(gHead("scheduled", {tabs: [["yours", "Yours"], ["templates", "Templates"]], tools: `${gBtn("gSearch", "search", "Search", G.find.scheduled)}<button class="btn primary split" id="tkNew">New task<span class="sd">${ICON.chev}</span></button>`}) + (d.problem ? `<div class="gempty"><b>Your scheduled tasks could not be read.</b><span>${esc(d.problem)}</span><button class="btn" id="tkReset">Start fresh</button></div>` : "") + `<div id="gtk">${body}</div>`);
    $("#tkReset")?.addEventListener("click", async () => { try { await post("/api/assistant", {action: "tasks_reset"}); pageScheduled(); } catch (err) { toast(err.message); } });
    bindHead("scheduled", () => draw());
    const add = async preset => { const v = await taskDialog(preset); if (v && await taskSave(v)) { await loadAccount(); pageScheduled(); } };
    $("#tkNew").onclick = () => add();
    if (tab === "yours") closedApp011();
    $("#tkNew2")?.addEventListener("click", () => add());
    $$(".gcard.tpl").forEach(c => c.onclick = () => add(d.templates[+c.dataset.id.slice(1)]));
    $$(".gcard.task:not(.tpl)").forEach(c => {
      const t = d.tasks.find(x => x.id === c.dataset.id);
      const open = () => t.last_thread ? openThread(t.last_thread) : add2(t);
      const add2 = async t0 => { const v = await taskDialog(t0); if (v && await taskSave(v, t0.id)) pageScheduled(); };
      c.onclick = e => { if (e.target.closest("[data-tk]")) return; open(); };
      c.querySelector("[data-tk]").onclick = e => ctxMenu({type: "click", currentTarget: e.currentTarget}, [
        ...(t.last_thread ? [{label: "Open the last run", icon: ICON.chat, fn: () => openThread(t.last_thread)}] : []),
        {label: "Run now", icon: ICON.bolt, fn: async () => { try { const r = await post("/api/assistant", {action: "task_run", id: t.id}); toast("Started"); loadThreads(); if (r.thread) openThread(r.thread); } catch (err) { toast(err.message); } }},
        {label: "Edit", icon: ICON.pencil, fn: () => add2(t)},
        {label: t.enabled ? "Pause" : t.status === "Done" ? "Run again at a new time" : "Resume", icon: ICON.clock, fn: async () => { if (t.status === "Done") return add2(t); try { await post("/api/assistant", {action: "task_update", id: t.id, enabled: !t.enabled}); pageScheduled(); } catch (err) { toast(err.message); } }},
        {sep: true},
        {label: "Delete", icon: ICON.trash, danger: true, fn: async () => { if (await ask({title: `Delete “${clip(t.name, 40)}”?`, text: "It stops running. Chats it already made stay.", ok: "Delete task", cancel: "Keep task", danger: true})) { await post("/api/assistant", {action: "task_delete", id: t.id}); pageScheduled(); } }},
      ]);
    });
  };
  draw();
}

/* ---------- Plugins ---------- */
const PLUGIN_SUB = {connector: "Connector", skill: "Skill", plugin: "Plugin"};
async function pagePlugins() {
  if (!G.plg) shell0(`<header class="gh"><h1>Plugins</h1></header><div class="gskel">${Array(4).fill(`<div class="gcard sk"><div class="gm"></div></div>`).join("")}</div>`);
  try { const [c, sk, pl] = await Promise.all([api("/api/connectors"), api("/api/skills"), api("/api/plugins").catch(() => ({plugins: []}))]); S.connCache = c.connectors; S.skills = sk.skills; G.plg = {conns: c.connectors, skills: sk.skills, plugins: pl.plugins || []}; }
  catch (e) { return shell0(`<header class="gh"><h1>Plugins</h1></header><div class="gempty"><b>Could not load plugins.</b><span>${esc(e.message)}</span></div>`); }
  if (S.view !== "page" || S.page !== "plugins") return;
  const draw = () => {
    const {conns, skills, plugins} = G.plg, tab = G.tab.plugins || "all", q = (G.q.plugins || "").toLowerCase();
    const mine = [
      ...(tab !== "skills" ? conns.filter(c => c.connected).map(c => ({kind: "connector", id: c.id, name: c.name, by: c.custom ? (() => { try { return new URL(c.url || "https://custom.local").host; } catch { return c.name || "Custom"; } })() : "meshpool directory", on: c.enabled, desc: c.description})) : []),
      ...(tab !== "connectors" ? skills.filter(s => s.enabled).map(s => ({kind: "skill", id: s.id, name: s.name, by: "On this computer", on: true, desc: s.description})) : []),
      ...(tab === "all" ? plugins.map(p => ({kind: "plugin", id: p.name, name: p.name, by: p.version ? "v" + p.version : "Installed", on: p.enabled, desc: p.description || ""})) : []),
    ].filter(x => !q || (x.name + x.desc).toLowerCase().includes(q));
    const more = conns.filter(c => !c.connected && (tab !== "skills") && (!q || (c.name + c.description).toLowerCase().includes(q)));
    const row = x => `<li class="prow" data-k="${x.kind}:${esc(x.id)}"><span class="plogo">${esc(x.name.slice(0, 1).toUpperCase())}</span><span class="pt"><b>${esc(x.name)}${x.on ? `<i class="vchk" title="Active">${ICON.check}</i>` : ""}</b><small>${PLUGIN_SUB[x.kind]} · ${esc(x.by)}</small></span><button class="iconbtn more" data-pm="${x.kind}:${esc(x.id)}" aria-label="Options for ${esc(x.name)}">${ICON.more}</button></li>`;
    const shown = G.plgAll ? mine : mine.slice(0, 6);
    shell0(`<header class="gh"><h1>Plugins</h1><button class="iconbtn" id="plMore" aria-label="More" aria-haspopup="menu">${ICON.more}</button></header>
      <div class="gbar"><label class="gsearch">${ICON.search}<input id="gq" placeholder="Search plugins" value="${esc(G.q.plugins || "")}" aria-label="Search plugins" autocomplete="off"></label><div class="gtabs pills" role="tablist">${[["all", "All"], ["connectors", "Connectors"], ["skills", "Skills"]].map(([k, l]) => `<button role="tab" data-gtab="${k}" aria-selected="${tab === k}">${l}</button>`).join("")}</div><button class="btn split" id="plAdd">Add<span class="sd">${ICON.chev}</span></button></div>
      <div class="gline"><h2 class="gsub">Your plugins</h2>${mine.length > 6 ? `<button class="linkbtn" id="plAll">${G.plgAll ? "Show fewer" : "Show all"} ${ICON.chevR}</button>` : ""}</div>
      ${mine.length ? `<ul class="plist">${shown.map(row).join("")}${mine.length > 6 && !G.plgAll ? `<li class="prow sum"><span class="pt"><small>${esc(mine.slice(0, 3).map(x => x.name).join(", "))} and ${mine.length - 3} more</small></span></li>` : ""}</ul>` : `<div class="gempty"><b>Nothing turned on yet.</b><span>Connect a service or add a skill to give meshpool more to work with.</span></div>`}
      ${more.length ? `<div class="gline"><h2 class="gsub">Available to connect</h2></div><ul class="pcols">${more.slice(0, 12).map(c => `<li class="pcard" data-conn="${esc(c.id)}"><span class="plogo">${esc(c.name.slice(0, 1).toUpperCase())}</span><span class="pt"><b>${esc(c.name)}</b><small>${esc(clip(c.description || "", 90))}</small></span></li>`).join("")}</ul>` : ""}`);
    $$("[data-gtab]").forEach(b => b.onclick = () => { G.tab.plugins = b.dataset.gtab; draw(); });
    $("#gq").oninput = e => { const pos = e.target.selectionStart; G.q.plugins = e.target.value; clearTimeout(G.qT); G.qT = setTimeout(() => { draw(); const i = $("#gq"); i.focus(); i.setSelectionRange(pos, pos); }, 150); };
    $("#plAll")?.addEventListener("click", () => { G.plgAll = !G.plgAll; draw(); });
    $("#plMore").onclick = e => ctxMenu({type: "click", currentTarget: e.currentTarget}, [{label: "Plugin settings", icon: ICON.gear, fn: () => openSettings("plugins")}, {label: "Refresh", icon: ICON.refresh, fn: () => { G.plg = null; pagePlugins(); }}]);
    $("#plAdd").onclick = e => ctxMenu({type: "click", currentTarget: e.currentTarget}, [
      {label: "Connect a service", icon: ICON.plug, fn: () => openSettings("connectors")}, {label: "Add a custom connector", icon: ICON.link, fn: () => openSettings("mcp")},
      {label: "Install a plugin", icon: ICON.dl, fn: () => openSettings("plugins")}, {label: "Create or import a skill", icon: ICON.skill, fn: () => openSettings("skills")}]);
    $$("[data-conn]").forEach(c => c.onclick = () => openSettings("connectors"));
    $$("[data-pm]").forEach(b => b.onclick = e => {
      const [kind, ...r] = b.dataset.pm.split(":"), id = r.join(":"), x = mine.find(m => m.kind === kind && m.id === id);
      ctxMenu({type: "click", currentTarget: b}, [
        ...(kind === "connector" ? [{label: x.on ? "Turn off" : "Turn on", icon: ICON.bolt, fn: async () => { try { await post("/api/connectors", {action: "enable", id, on: !x.on}); G.plg = null; pagePlugins(); } catch (err) { toast(err.message); } }}] : []),
        {label: "Manage", icon: ICON.gear, fn: () => openSettings(kind === "connector" ? "connectors" : kind === "skill" ? "skills" : "plugins")}]);
    });
  };
  draw();
}

/* "Also run when Meshpool is closed": registers the system job (cron, launchd or Task Scheduler) */
async function closedApp011() {
  let st; try { st = await api("/api/scheduler"); } catch (e) { return; }
  if (!st.available || $("#closedApp")) return;
  const host = $(".ggrid, .gempty")?.parentElement;
  if (!host) return;
  const row = document.createElement("div"); row.id = "closedApp"; row.className = "closedapp";
  row.innerHTML = `<label class="switchrow"><input type="checkbox" ${st.installed ? "checked" : ""}> Also run tasks when meshpool is closed</label><small>${st.installed ? "On. Your computer checks every 10 minutes." : "Off. Tasks run only while meshpool is open."}</small>`;
  host.appendChild(row);
  $("input", row).onchange = async e => { try { const r = await post("/api/scheduler", {on: e.target.checked}); toast(r.message || "Saved"); $("small", row).textContent = r.installed ? "On. Your computer checks every 10 minutes." : "Off. Tasks run only while meshpool is open."; } catch (err) { toast(err.message); e.target.checked = !e.target.checked; } };
}
