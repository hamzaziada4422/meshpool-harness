/* The app shell: sidebar (Chat and Code modes), top bar, search dialog, account menu, keyboard shortcuts.
   Loaded before app.js (which boots the app after every script is in); everything here runs from functions, never at
   load. The chat engine, the panels and Settings pages live in app.js, projects.js and settings2.js. */
"use strict";

/* ---------- what the shell knows about the account (name, plan, counts for the dots) ---------- */
async function loadAccount() {
  try { S.acct = await api("/api/hub/account"); } catch (e) { S.acct = S.acct || null; }
  paintDots();
}
const firstName = () => S.acct?.first_name || "";
// The dots that say "something needs you": on the Code button, on Scheduled, and on the toggle of a hidden sidebar.
function paintDots() {
  const a = S.acct || {}, code = $("#modeCode");
  if (code) {
    code.classList.toggle("waits", !!a.code_waiting);
    code.setAttribute("data-tip", a.code_waiting ? "Code, awaiting your input" : "Code");
    code.setAttribute("aria-label", a.code_waiting ? "Code, awaiting your input" : "Code");
  }
  const dot = $("#showSide .ndot"); if (dot) dot.hidden = !(a.chat_unread || a.code_waiting || a.code_unread);
}
const attention = () => { const a = S.acct || {}; return !!(a.chat_unread || a.code_waiting || a.code_unread); };

/* ================================== shell ================================== */
function shell() {
  $("#root").innerHTML = `
  <div class="app" id="app">
    <aside class="side" id="side" aria-label="Sidebar">
     <div class="side-in">
      <div class="side-head">
        <button class="iconbtn" id="hideSide" data-tip="Hide sidebar" data-kbd="${MOD}B" data-tip-pos="below" aria-label="Hide sidebar">${ICON.side}</button>
        <span class="wm" id="wordmark"><img class="mlogo" src="${logoSrc()}" alt="Meshpool" width="102" height="20"><span class="wmode" id="wmode"></span></span>
        <span class="modesw" role="tablist" aria-label="Mode">
          <button id="modeChat" role="tab" data-tip="Chat" data-tip-pos="below" aria-label="Chat">${ICON.chat}</button>
          <button id="modeCode" role="tab" data-tip="Code" data-tip-pos="below" aria-label="Code">${ICON.codebr}<i class="odot"></i></button>
        </span>
      </div>
      <nav class="side-list" id="sideList" aria-label="Chats and pages"></nav>
      <button class="updbar" id="updBar" hidden></button>
      <div class="side-foot">
        <button class="acct" id="acctBtn" aria-label="Settings" data-tip="Settings" data-tip-pos="above">${ICON.gear}<span class="an">Settings</span><span class="ap" id="acctPlan"></span></button>
        <button class="iconbtn" id="footBtn" aria-label="Get apps and command line tool"></button>
        <span class="kdot" id="kdot" hidden></span>
      </div>
     </div>
    </aside>
    <main class="main" id="main">
      <div class="topbar" id="topbar"></div>
      <div class="conv" id="conv"><div class="col" id="col"></div></div>
      <div class="composer-wrap">
        <div class="sugg" id="sugg" hidden></div>
        <div class="composer" id="composer">
          <div class="attach-row" id="attachRow"></div>
          <label class="sr" for="input">Message</label>
          <textarea id="input" dir="auto" rows="1" placeholder="Message Meshpool"></textarea>
          <div class="cbar" id="cbar"></div>
          <input type="file" id="fileIn" multiple hidden>
        </div>
        <div class="selrow" id="selrow"></div>
        <div class="hintline" id="hintline"><span class="hl-status" id="statusSlot"></span><span class="hl-hint" id="hintTxt"></span></div>
      </div>
    </main>
    <aside class="panel" id="panel" aria-label="Side panel" ${S.panel ? "" : "hidden"}><div class="rz" id="rz"></div><div class="ptabs" id="ptabs"></div><div class="pbody" id="pbody"></div></aside>
  </div>`;
  $("#updBar").onclick = e => e.currentTarget.dataset.act === "apply" ? applyUpdate() : openSettings("about");
  $("#hideSide").onclick = toggleSide;
  $("#modeChat").onclick = () => setAppMode3("chat");
  $("#modeCode").onclick = () => setAppMode3("code");
  $("#acctBtn").onclick = () => openSettings();
  $("#footBtn").onclick = e => appsMenu(e);
  bindSide();
  setupComposer();
  setupPanelResize();
  document.addEventListener("keydown", globalKeys);
  if (store("sidehidden")) $("#app").classList.add("noside");
  $("#app").classList.toggle("steps", !!S.steps);
  syncMode();
  let wasNarrow = narrow();
  addEventListener("resize", () => { if (narrow() !== wasNarrow) { wasNarrow = narrow(); $("#app").classList.remove("sideopen"); renderTop(); } });
  document.addEventListener("mousedown", e => { if ($("#app").classList.contains("sideopen") && !e.target.closest(".side, #showSide, #ctx, #pop, .scrim")) $("#app").classList.remove("sideopen"); });
}
const narrow = () => innerWidth <= 760;
function setAppMode() { /* kept for old test scripts: the shell has Chat and Code modes now (setAppMode3) */ }
const GIT_WORD = {"??": "New", "A": "Added", "M": "Changed", "D": "Removed", "R": "Renamed", "AM": "Added", "MM": "Changed", "U": "Conflict", "UU": "Conflict"};
function syncMode() {
  const code = S.mode === "code", a = $("#app"); if (!a) return;
  a.classList.toggle("mode-code", code);
  $("#modeChat")?.setAttribute("aria-selected", String(!code)); $("#modeCode")?.setAttribute("aria-selected", String(code));
  const wm = $("#wmode"); if (wm) wm.textContent = code ? "Code" : "";
  const fb = $("#footBtn"); if (fb) { fb.innerHTML = ICON.monitor; fb.setAttribute("data-tip", "Get apps and tools"); fb.setAttribute("data-tip-pos", "above"); fb.setAttribute("aria-label", "Get apps and tools"); }
}
// Chat and Code are two views of the same chats: a code session works in a real folder or GitHub workspace.
function setAppMode3(m) {
  if (S.mode === m) { if (S.view === "page") newThread(); return; }
  S.mode = m; store("appmode", m); syncMode();
  const kind = S.cur && (S.cur.view?.kind === "code" && !S.cur.view?.scratch ? "code" : "chat");
  if (S.view === "page" || !S.cur || kind !== m) newThread(); else { renderSide(); renderBar(); }
}
const isCodeThread = t => t && t.kind === "code" && !t.scratch;
function toggleSteps() {
  S.steps = !S.steps; store("steps", S.steps);
  $("#app").classList.toggle("steps", S.steps);
  $$("#col .work").forEach(d => d.open = S.steps || !d.dataset.done || !!$(".appr:not(.done)", d));   // never fold away a pending approval
  $$("#col .think details").forEach(d => d.open = S.steps);
  renderTop(); toast(S.steps ? "Showing every step" : "Steps are folded; click one to open it");
}
function toggleSide() {
  const a = $("#app");
  if (a.classList.contains("autoside")) { S.keepSide = true; a.classList.remove("autoside"); renderTop(); return; }
  if (narrow()) { a.classList.toggle("sideopen"); return; }
  a.classList.toggle("noside"); store("sidehidden", a.classList.contains("noside")); renderTop();
}
function globalKeys(e) {
  const m = isMac ? e.metaKey : e.ctrlKey;
  if (e.key === "Escape") {
    // Esc closes the top-most thing that is open; with nothing open it stops the running task, wherever focus is.
    const open = $("#pop") || $("#ctx") || $(".scrim") || $("#onb") || $("#srch");
    closePop(); closeCtx(); closeSearch(); const sc = $(".scrim:not(.dlg-scrim)"); if (sc && !$("#onb") && !$(".dlg-scrim")) sc.remove();
    const typing = e.target?.closest?.("input, textarea, select, [contenteditable=true]") && e.target.id !== "input";
    if (!open && !typing && e.target?.id !== "input" && cfg().busy) { e.preventDefault(); stopNow(); }
  }
  if (!m) return;
  const k = e.key.toLowerCase();
  if (k === "n" && !e.shiftKey) { e.preventDefault(); newThread(); }
  else if (k === "k") { e.preventDefault(); openSearch(); }
  else if (k === "," || (k === "," && e.shiftKey)) { e.preventDefault(); openSettings(); }
  else if (k === "b") { e.preventDefault(); toggleSide(); }
  else if (k === "u" && !e.shiftKey && $("#fileIn") && !$(".scrim")) { e.preventDefault(); $("#fileIn").click(); }
  else if (k === "d" && e.shiftKey) { e.preventDefault(); togglePanel("diff"); }
  else if (k === "j") { e.preventDefault(); togglePanel("term"); }
  else if (k === "e") { e.preventDefault(); toggleSteps(); }
  else if (k === ".") { e.preventDefault(); if (S.view === "chat") togglePanel(S.panel || panelSet()[0], !S.panel); }
}

/* ================================== data loads ================================== */
async function loadSettings() { S.settings = await api("/api/settings"); const k = $("#kdot"); if (k) k.className = "kdot" + (S.settings.ready ? " ok" : ""); }
async function loadModels(refresh) {
  try { S.models = await api("/api/models" + (refresh ? "?refresh=1" : "")); } catch (e) { S.models = {models: [], favorites: [], default: "", error: e.message}; }
  if (!S.draft.model) S.draft.model = S.models.default;
}
async function loadProjects() { const r = await api("/api/projects"); S.projects = r.projects; S.defaultProject = r.default; }
async function loadPj() { try { S.pj = (await api("/api/pj")).projects; } catch (e) { S.pj = S.pj || []; } }
// Settings > Permissions: add a folder to the list Meshpool may open (a project's linked folder does not need this).
async function pickProject() {
  try {
    let r = await post("/api/projects", {action: "pick"});
    if (r.no_picker) { const path = await browseFolder(); if (!path) return; r = await post("/api/projects", {action: "add", path}); }
    S.projects = r.projects; renderSide();
  } catch (e) { toast(e.message); }
}
async function loadThreads() {
  const was = new Map((S.threads || []).map(t => [t.id, t.state]));
  S.threads = (await api("/api/agent")).sessions;
  for (const t of S.threads) {
    const w = was.get(t.id);
    if (w === "working" && t.state === "waiting") window.notifyEvent?.("wait", t.title);
    else if (["working", "waiting"].includes(w) && !["working", "waiting"].includes(t.state)) window.notifyEvent?.("done", t.title);
    if (t.unread && S.cur?.id === t.id && S.view === "chat") { t.unread = false; post(`/api/threads/${t.id}`, {action: "seen"}).catch(() => {}); }   // the chat in front of you is read
  }
  renderSide();
  if (!S.acctT || Date.now() - S.acctT > 8000) { S.acctT = Date.now(); loadAccount(); }
  const t = S.cur?.view && S.threads.find(x => x.id === S.cur.id);
  if (t && t.title && t.title !== S.cur.view.title && !$("#ttl[contenteditable=true]")) { S.cur.view.title = t.title; renderTop(); }
}
async function loadHarnesses() { const r = await api("/api/harnesses"); S.harnesses = r.harnesses; S.defaults = r.defaults; S.hInfo = r; }
async function loadSkills() { try { S.skills = (await api("/api/skills")).skills; } catch (e) { S.skills = []; } }
const harness = id => S.harnesses.find(h => h.id === id) || S.harnesses[0] || {id: "meshpool-code", name: "Meshpool Code", models: "meshpool"};
// ChatGPT sign-in runs Codex's login, which the installed app does not carry (server: codex row has `unsupported`)
const chatgptSignInGone = () => { const c = (S.harnesses || []).find(h => h.id === "codex"); return !!(c && c.unsupported) && !S.settings?.signed_in?.chatgpt; };
const projName = p => (S.projects.find(x => x.path === p) || {}).name || (p || "").split(/[\\/]/).filter(Boolean).pop() || p;

/* ================================== sidebar ================================== */
const NAV = {
  chat: [["new", "New", "plus"], ["search", "Search", "search"], ["projects", "Projects", "stack"], ["artifacts", "Artifacts", "cube"], ["scheduled", "Scheduled", "clock"], ["memory", "Memory", "brain"], ["work", "Your work", "star"], ["plugins", "Plugins", "plug"]],
  code: [["new", "New", "plus"], ["search", "Search", "search"], ["artifacts", "Artifacts", "cube"], ["plugins", "Plugins", "plug"], ["more", "More", "more"]],
};
// A chat's state as the sidebar draws it: running (brand dot), waiting for you (amber), finished and unread (blue).
const stateOf = t => t.state === "working" ? "run" : t.state === "waiting" ? "wait" : t.unread && !(S.cur?.id === t.id && S.view === "chat") ? "done" : "";
const pinnedPj = () => S.pj.filter(p => p.pinned).sort((a, z) => (z.updated || 0) - (a.updated || 0));
const chatList = () => S.threads.filter(t => !t.archived && !S.pendingDelete?.has(t.id)).sort((a, z) => (Number(z.updated) || 0) - (Number(a.updated) || 0));
function navHTML() {
  const a = S.acct || {};
  return `<div class="nav">${NAV[S.mode === "code" ? "code" : "chat"].map(([id, label, ic]) => {
    const on = id === "new" ? S.view === "chat" && !S.cur : S.view === "page" && S.page === id;
    const dot = id === "scheduled" && a.scheduled_unread ? `<i class="bdot" aria-label="Unread"></i>` : "";
    return `<button class="nv ${on ? "on" : ""}" data-nv="${id}" ${id === "new" ? `id="newThread"` : ""} ${id === "search" ? `id="navSearch" data-kbd="${MOD}K"` : ""} ${id === "new" ? `data-kbd="${MOD}N"` : ""}>${ICON[ic]}<span>${label}</span>${dot}${id === "artifacts" ? `<span class="nvplus" data-nv-add="artifacts" aria-hidden="true">${ICON.plus}</span>` : ""}</button>`;
  }).join("")}</div>`;
}
const secHead = (k, label, extra = "") => `<div class="sec"><button class="sechd" data-sec="${k}" aria-expanded="${!S.collapsed.has(k)}"><span>${label}</span><span class="chev">${ICON.chev}</span></button><span class="sp"></span>${extra}</div>`;
function pjRowHTML(p, threads) {
  const open = S.pjOpen.has(p.id), mine = threads.filter(t => t.project_id === p.id);
  const unread = mine.some(t => stateOf(t) === "done");
  const rows = open ? `<ul class="threads sub">${mine.slice(0, 6).map(t => thHTML(t)).join("")}${mine.length > 6 ? `<li class="vall" data-vall="${esc(p.id)}" tabindex="0">View all</li>` : ""}${!mine.length ? `<li class="vall none">No chats yet</li>` : ""}</ul>` : "";
  return `<li class="th pr ${S.view === "project" && S.pjId === p.id ? "on" : ""}" data-pid="${esc(p.id)}" data-nav tabindex="0" title="${esc(p.name)}"><span class="pi" data-ptog="${esc(p.id)}">${ICON.stack}</span><span class="tt">${esc(p.name)}</span><span class="end">${unread ? `<span class="dot done"></span>` : `<span class="dot"></span>`}<button class="iconbtn more" data-pmenu="${esc(p.id)}" aria-label="Options for ${esc(p.name)}" aria-haspopup="menu">${ICON.more}</button></span></li>${rows}`;
}
const PR_LABEL = {open: "Open", draft: "Draft", merged: "Merged", closed: "Closed"};
function thHTML(t, o = {}) {
  const st = stateOf(t), code = isCodeThread(t);
  const pj = t.project_id && S.pj.find(p => p.id === t.project_id);
  const icons = code ? `${t.pr ? `<span class="si pr" data-prtip="${esc(t.id)}" aria-label="Pull request #${t.pr.number}">${ICON.pr}</span>` : ""}${isMgd(t.workdir) ? `<span class="si" title="GitHub workspace">${ICON.cloud}</span>` : ""}${t.state === "waiting" ? `<span class="si warn" title="Waiting for you">${ICON.warn}</span>` : ""}` : "";
  return `<li class="th ${S.view === "chat" && S.cur?.id === t.id ? "on" : ""}" data-id="${esc(t.id)}" data-nav tabindex="0" title="${esc(t.title + (pj ? " · " + pj.name : ""))}">${t.scheduled ? `<span class="pi bolt" title="Started by a scheduled task">${ICON.bolt}</span>` : ""}<span class="tt">${esc(t.title)}${false && pj ? `<small class="tf">${esc(pj.name)}</small>` : ""}</span><span class="end">${icons}<span class="dot ${st}"></span><button class="iconbtn more" data-tmenu="${esc(t.id)}" aria-label="Options for ${esc(t.title)}" aria-haspopup="menu">${ICON.more}</button></span></li>`;
}

/* Code sessions grouped by folder: the folder with the newest activity first, pinned sessions on top, each folder
   collapsible (remembered), with its own "new session" button and a dot when something in it needs you. */
function codeGroupsHTML(list) {
  const pinned = list.filter(t => t.pinned), rest = list.filter(t => !t.pinned);
  const groups = new Map();
  for (const t of rest) { const k = t.workdir || ""; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(t); }
  const rows = [...groups.entries()].map(([k, ts]) => ({k, ts, at: Math.max(...ts.map(t => Number(t.updated) || 0))})).sort((a, z) => z.at - a.at);
  const curDir = S.cur?.view?.workdir || "";
  let out = pinned.length ? `<div class="cgl">Pinned</div><ul class="threads">${pinned.map(t => thHTML(t)).join("")}</ul>` : "";
  S.cgAll = S.cgAll || new Set();
  for (const g of rows) {
    const key = "cg:" + g.k, shut = S.collapsed.has(key) && g.k !== curDir, name = g.k ? projName(g.k) : "No folder";
    const wait = g.ts.some(t => t.state === "waiting"), run = g.ts.some(t => t.state === "working");
    const many = g.ts.length > 6 && !S.cgAll.has(g.k) && !(S.codeQ || "").trim();
    out += `<div class="cg ${shut ? "shut" : ""}"><button class="cghd" data-cgtog="${esc(g.k)}" aria-expanded="${!shut}" title="${esc(g.k || "Sessions without a folder")}"><span class="chev">${ICON.chev}</span><span class="cgn">${esc(name)}</span><span class="cnt">${g.ts.length}</span>${wait ? `<i class="cgdot wait" aria-label="Waiting for you"></i>` : run ? `<i class="cgdot run" aria-label="Running"></i>` : ""}</button>${g.k ? `<button class="iconbtn cgnew" data-cgnew="${esc(g.k)}" data-tip="New session in ${esc(name)}" aria-label="New session in ${esc(name)}">${ICON.plus}</button>` : ""}</div>`
      + (shut ? "" : `<ul class="threads sub cgl2">${(many ? g.ts.slice(0, 6) : g.ts).map(t => thHTML(t)).join("")}${many ? `<li class="vall" data-cgmore="${esc(g.k)}" tabindex="0">Show ${g.ts.length - 6} more</li>` : ""}</ul>`);
  }
  return out;
}
function renderSide() {
  const el = $("#sideList"); if (!el) return;
  if (S.renaming) return;                              // never rebuild the list under a name being typed
  S.pjOpen = S.pjOpen || new Set(store("pj_open") || []);
  const code = S.mode === "code";
  const all = chatList();
  const mine = all.filter(t => code ? isCodeThread(t) : !isCodeThread(t));
  const open = k => !S.collapsed.has(k);
  let html = navHTML();
  if (code) {
    const fl = S.codeFilter || "all", by = store("code_group") || "folder", q = (S.codeQ || "").trim().toLowerCase();
    const list = mine.filter(t => (fl === "all" || (fl === "waiting" && t.state === "waiting") || (fl === "unread" && t.unread) || (fl === "pr" && t.pr))
      && (!q || (t.title + " " + projName(t.workdir || "")).toLowerCase().includes(q)));
    const tools = `<button class="iconbtn flt ${S.codeFind || q ? "on" : ""}" id="codeFind" data-tip="Search sessions" aria-label="Search sessions">${ICON.search}</button><button class="iconbtn flt ${fl !== "all" || by !== "folder" ? "on" : ""}" id="codeFilter" data-tip="Group and filter" aria-label="Group and filter sessions">${ICON.filter}</button>`;
    const waiting = mine.filter(t => t.state === "waiting");
    html += (waiting.length && fl !== "waiting" ? `<button class="cwait" data-cfilter="waiting"><span class="dot wait"></span>${waiting.length} waiting for you</button>` : "")
      + secHead("recents", by === "folder" ? "Sessions by folder" : "Recents", tools)
      + (S.codeFind || q ? `<div class="cfind"><input id="codeQ" value="${esc(S.codeQ || "")}" placeholder="Search sessions" aria-label="Search sessions" autocomplete="off" spellcheck="false"></div>` : "");
    if (open("recents")) html += list.length ? (by === "folder" ? codeGroupsHTML(list) : `<ul class="threads">${list.map(t => thHTML(t)).join("")}</ul>`)
      : `<div class="empty-side">${mine.length ? "Nothing matches." : "Sessions show up here, grouped by folder. Start one with New."}</div>`;
  } else {
    const pjs = pinnedPj(), pinnedChats = mine.filter(t => t.pinned), rest = mine.filter(t => !t.pinned);
    if (pjs.length || pinnedChats.length) {
      html += secHead("pinned", "Pinned");
      if (open("pinned")) html += `<ul class="threads prj">${pjs.map(p => pjRowHTML(p, all.filter(t => !isCodeThread(t)))).join("")}${pinnedChats.map(t => thHTML(t, {})).join("")}</ul>`;
    }
    html += secHead("recents", "Recents");
    if (open("recents")) html += rest.length ? `<ul class="threads">${(S.allRecents ? rest : rest.slice(0, 40)).map(t => thHTML(t, {})).join("")}</ul>${rest.length > 40 ? `<button class="linkbtn moreproj" id="moreRecents">${S.allRecents ? "Show fewer" : `Show all ${rest.length}`}</button>` : ""}` : `<div class="empty-side">Your chats show up here. Start one with New.</div>`;
  }
  const arch = S.threads.filter(t => t.archived && (code ? isCodeThread(t) : !isCodeThread(t)));
  if (arch.length) {
    html += `<div class="sec"><button class="sechd" id="togArch" aria-expanded="${!!S.showArchived}"><span>Archived</span><span class="cnt">${arch.length}</span><span class="chev">${ICON.chev}</span></button></div>`;
    if (S.showArchived) html += `<ul class="threads">${arch.map(t => thHTML(t, {})).join("")}</ul>`;
  }
  // Rewriting identical HTML drops hover, eats a click that lands mid-rewrite, and flickers: skip it. When only the status
  // dots changed, patch them in place.
  if (el._html === html) return;
  const shape = html.replace(/class="dot[^"]*"/g, "").replace(/class="th[^"]*"/g, "").replace(/class="nv[^"]*"/g, "");
  if (el._shape === shape) {
    el._html = html;
    for (const li of $$(".th[data-id]", el)) {
      const t = S.threads.find(x => x.id === li.dataset.id); if (!t) continue;
      li.classList.toggle("on", S.view === "chat" && S.cur?.id === t.id);
      const d = $(".dot", li); if (d) d.className = "dot " + stateOf(t);
    }
    for (const li of $$(".th[data-pid]", el)) li.classList.toggle("on", S.view === "project" && S.pjId === li.dataset.pid);
    for (const b of $$(".nv[data-nv]", el)) b.classList.toggle("on", b.dataset.nv === "new" ? S.view === "chat" && !S.cur : S.view === "page" && S.page === b.dataset.nv);
    return;
  }
  const had = document.activeElement?.closest?.("#sideList") ? (document.activeElement.dataset.id || document.activeElement.dataset.pid) : "";
  const st = el.scrollTop;
  el._html = html; el._shape = shape; el.innerHTML = html; el.scrollTop = st;
  if (had) { const back = $(`[data-id="${CSS.escape(had)}"], [data-pid="${CSS.escape(had)}"]`, el); back?.focus({preventScroll: true}); }
  paintAccount();
}
function paintAccount() {
  const a = S.acct; if (!a) return;
  setTxt($("#acctPlan"), a.key_set ? "" : "· No API key");
  paintDots();
}
// One set of listeners for the whole list (it is rebuilt often): click, right-click, keyboard.
function bindSide() {
  const el = $("#sideList");
  el.addEventListener("input", e => { if (e.target.id === "codeQ") { S.codeQ = e.target.value; clearTimeout(S.codeQT); S.codeQT = setTimeout(() => { renderSide(); const i = $("#codeQ"); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }, 90); } });
  el.addEventListener("keydown", e => { if (e.target.id === "codeQ" && e.key === "Escape") { S.codeQ = ""; S.codeFind = false; renderSide(); } });
  el.addEventListener("click", e => {
    const cgt = e.target.closest("[data-cgtog]"); if (cgt) { const key = "cg:" + cgt.dataset.cgtog; S.collapsed.has(key) ? S.collapsed.delete(key) : S.collapsed.add(key); store("collapsed", [...S.collapsed]); return renderSide(); }
    const cgn = e.target.closest("[data-cgnew]"); if (cgn) { e.stopPropagation(); return newThread(cgn.dataset.cgnew); }
    const cgm = e.target.closest("[data-cgmore]"); if (cgm) { (S.cgAll = S.cgAll || new Set()).add(cgm.dataset.cgmore); return renderSide(); }
    const cfl = e.target.closest("[data-cfilter]"); if (cfl) { S.codeFilter = cfl.dataset.cfilter; return renderSide(); }
    if (e.target.closest("#codeFind")) { S.codeFind = !S.codeFind; if (!S.codeFind) S.codeQ = ""; renderSide(); if (S.codeFind) $("#codeQ")?.focus(); return; }
    const more = e.target.closest("[data-tmenu]"); if (more) { e.stopPropagation(); return threadMenu({type: "click", currentTarget: more}, more.dataset.tmenu); }
    const pm = e.target.closest("[data-pmenu]"); if (pm) { e.stopPropagation(); return projMenu({type: "click", currentTarget: pm}, pm.dataset.pmenu); }
    const nvAdd = e.target.closest("[data-nv-add]"); if (nvAdd) { e.stopPropagation(); return newArtifact?.(); }
    const nv = e.target.closest("[data-nv]");
    if (nv) { const id = nv.dataset.nv; return id === "new" ? newThread() : id === "search" ? openSearch() : id === "more" ? moreMenu(nv) : goPage(id); }
    const sec = e.target.closest("[data-sec]"); if (sec) { const k = sec.dataset.sec; S.collapsed.has(k) ? S.collapsed.delete(k) : S.collapsed.add(k); store("collapsed", [...S.collapsed]); return renderSide(); }
    if (e.target.closest("#codeFilter")) return codeFilterMenu(e.target.closest("#codeFilter"));
    if (e.target.closest("#togArch")) { S.showArchived = !S.showArchived; return renderSide(); }
    if (e.target.closest("#moreRecents")) { S.allRecents = !S.allRecents; return renderSide(); }
    const va = e.target.closest("[data-vall]"); if (va) return openProject(va.dataset.vall);
    if (e.target.closest(".rn")) return;
    const tog = e.target.closest("[data-ptog]");
    if (tog) { const id = tog.dataset.ptog; S.pjOpen.has(id) ? S.pjOpen.delete(id) : S.pjOpen.add(id); store("pj_open", [...S.pjOpen]); el._html = ""; return renderSide(); }
    const pr = e.target.closest(".th[data-pid]"); if (pr) { S.pjOpen.add(pr.dataset.pid); store("pj_open", [...S.pjOpen]); return openProject(pr.dataset.pid); }
    const th = e.target.closest(".th[data-id]"); if (th) return openThread(th.dataset.id);
  });
  el.addEventListener("dblclick", e => { const th = e.target.closest(".th[data-id]"); if (th && !e.target.closest("button") && !e.target.closest(".rn")) startRename(th.dataset.id); });
  el.addEventListener("contextmenu", e => {
    const th = e.target.closest(".th[data-id]"), pr = e.target.closest(".th[data-pid]");
    if (th) { e.preventDefault(); threadMenu(e, th.dataset.id); } else if (pr) { e.preventDefault(); projMenu(e, pr.dataset.pid); }
  });
  el.addEventListener("keydown", e => {
    if (e.target.classList?.contains("rn")) return;
    const row = e.target.closest?.("[data-nav], .nv, .vall"); if (!row || e.target !== row) return;
    if (row.classList.contains("nv") || row.classList.contains("vall")) {
      const all = $$("#sideList .nv, #sideList [data-nav], #sideList .vall"), j = all.indexOf(row);
      if (e.key === "ArrowDown") { e.preventDefault(); all[j + 1]?.focus(); } else if (e.key === "ArrowUp") { e.preventDefault(); all[j - 1]?.focus(); }
      else if ((e.key === "Enter" || e.key === " ") && row.classList.contains("vall")) { e.preventDefault(); row.click(); }
      return;
    }
    const id = row.dataset.id, pid = row.dataset.pid;
    const rows = $$("#sideList .nv, #sideList [data-nav], #sideList .vall"), i = rows.indexOf(row);
    if (e.key === "ArrowDown") { e.preventDefault(); rows[i + 1]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); rows[i - 1]?.focus(); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); id ? openThread(id) : openProject(pid); }
    else if (e.key === "F2" && id) { e.preventDefault(); startRename(id); }
    else if ((e.key === "Delete" || e.key === "Backspace") && id) { e.preventDefault(); confirmDelete(id); }
    else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) { e.preventDefault(); const r = row.getBoundingClientRect(); const ev = {type: "key", currentTarget: $(".more", row) || row, clientX: r.left + 24, clientY: r.bottom}; id ? threadMenu(ev, id) : projMenu(ev, pid); }
  });
  // the pull request card: fetched when the pointer rests on the icon, never while the list is drawn
  let tipT = null;
  el.addEventListener("mouseover", e => {
    const ic = e.target.closest("[data-prtip]"); if (!ic) return;
    clearTimeout(tipT); tipT = setTimeout(() => prCard(ic), 250);
  });
  el.addEventListener("mouseout", e => { if (e.target.closest("[data-prtip]")) { clearTimeout(tipT); closePrCard(); } });
}
const moreMenu = anchor => ctxMenu({type: "click", currentTarget: anchor}, [
  {label: "Scheduled", icon: ICON.clock, fn: () => goPage("scheduled")},
  {label: "Projects", icon: ICON.stack, fn: () => goPage("projects")},
]);
function codeFilterMenu(anchor) {
  const cur = S.codeFilter || "all", by = store("code_group") || "folder", pick = v => () => { S.codeFilter = v; renderSide(); };
  const groupBy = v => () => { store("code_group", v); renderSide(); };
  const allFolders = () => { const ks = [...new Set(chatList().filter(isCodeThread).map(t => "cg:" + (t.workdir || "")))]; const shut = ks.every(k => S.collapsed.has(k)); ks.forEach(k => shut ? S.collapsed.delete(k) : S.collapsed.add(k)); store("collapsed", [...S.collapsed]); renderSide(); };
  ctxMenu({type: "click", currentTarget: anchor}, [
    {head: "Group by"}, {label: "Folder", checked: by === "folder" ? true : undefined, fn: groupBy("folder")}, {label: "Date", checked: by === "date" ? true : undefined, fn: groupBy("date")},
    {sep: true}, {head: "Show"},
    ...[["all", "All sessions"], ["waiting", "Waiting for you"], ["unread", "Unread"], ["pr", "With a pull request"]].map(([v, l]) => ({label: l, checked: v === cur ? true : undefined, fn: pick(v)})),
    ...(by === "folder" ? [{sep: true}, {label: "Collapse or expand all folders", fn: allFolders}] : [])]);
}
/* the pull request card on a code session's row: title, state, repository, number and the size of the diff */
function closePrCard() { $("#prcard")?.remove(); }
async function prCard(ic) {
  const id = ic.dataset.prtip, t = S.threads.find(x => x.id === id); if (!t) return;
  let pr = t.pr;
  const c = S.prCache?.[id];
  if (!c || Date.now() - c.t > 60000) { try { const r = await api(`/api/session/${id}/pr`); pr = r.pr || pr; S.prCache = {...(S.prCache || {}), [id]: {t: Date.now(), pr}}; } catch (e) { /* the card still shows what is stored */ } }
  else pr = c.pr;
  if (!ic.isConnected || !ic.matches(":hover") || !pr) return;
  closePrCard();
  const r = ic.getBoundingClientRect(), d = document.createElement("div"); d.id = "prcard"; d.className = "prcard-tip";
  d.innerHTML = `<b>${esc(pr.title || "Pull request")}</b><span class="pst">${pr.draft ? "Draft" : "Open"}</span><small>${esc(pr.repo || t.repo || "")} · #${pr.number}</small><span class="nw"><span class="plus">+${pr.additions || 0}</span> <span class="minus">−${pr.deletions || 0}</span></span>`;
  document.body.appendChild(d);
  d.style.left = Math.min(innerWidth - d.offsetWidth - 8, r.right + 10) + "px"; d.style.top = Math.max(8, Math.min(innerHeight - d.offsetHeight - 8, r.top - 8)) + "px";
}

/* ================================== pages ================================== */
/* The Memory and Project pages bind handlers on the persistent #col; drop them whenever the column is reused. */
function clearColHandlers() { const c = $("#col"); if (c) for (const k of ["onclick", "onkeydown", "ondrop", "ondragover", "ondragleave", "ondragstart"]) c[k] = null; c?.classList.remove("drag"); const i = $("#input"); if (i) i.disabled = false; }
function goPage(name) {
  clearColHandlers(); window.navPush?.("page:" + name);
  leaveChat(S.cur); S.cur = null; setWorking(null);
  S.view = "page"; S.page = name; S.pjId = "";
  S.attachments = []; renderAttach?.();
  $("#app").classList.remove("sideopen");
  if (S.panel) { S.panelWas = S.panel; }
  renderTop(); renderBar(); renderSide(); renderPanel();
  $("#col").classList.remove("loading");
  $("#conv").scrollTop = 0; $(".jumpdown")?.setAttribute("hidden", "");
  drawPage(name);
}

/* ================================== search dialog ================================== */
const SRCH_TABS = [["all", "All"], ["artifacts", "Artifacts"], ["sessions", "Sessions"], ["code", "Code"], ["projects", "Projects"], ["scheduled", "Scheduled"]];
function closeSearch() { const s = $("#srch"); if (!s) return; s.remove(); document.removeEventListener("keydown", s._key, true); }
function srchIcon(it) {
  const ic = {chat: ICON.chat, code: ICON.codebr, project: ICON.stack, artifact: ICON.cube, scheduled: ICON.clock, action: it.id === "new_session" ? ICON.codebr : it.id === "settings" ? ICON.gear : ICON.plus}[it.kind] || ICON.chat;
  return `<span class="sic">${ic}${it.unread || it.waiting ? `<i class="bdot ${it.waiting ? "wait" : ""}"></i>` : ""}</span>`;
}
function openSearch(tab = "all", q0 = "") {
  closePop(); closeCtx(); closeSearch(); $(".scrim:not(.dlg-scrim)")?.remove();
  const sc = document.createElement("div"); sc.className = "scrim srch-scrim"; sc.id = "srch";
  sc.innerHTML = `<div class="srch" role="dialog" aria-modal="true" aria-label="Search">
    <div class="srch-top"><input id="srchIn" placeholder="Search" aria-label="Search" autocomplete="off" spellcheck="false" value="${esc(q0)}"><span class="spin" id="srchSpin" hidden></span><button class="iconbtn" id="srchX" aria-label="Close search">${ICON.x}</button></div>
    <div class="srch-tabs" role="tablist">${SRCH_TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === tab}">${l}</button>`).join("")}</div>
    <div class="srch-list" id="srchList" role="listbox"></div>
    <div class="srch-keys"><span>Close <kbd>Esc</kbd></span><span>Change type <kbd>←</kbd><kbd>→</kbd></span><span>Open <kbd>↵</kbd></span><span>Actions <kbd>${isMac ? "⌥" : "Alt+"}↵</kbd></span><span class="sp"></span><span>Compose <kbd>Tab</kbd></span></div></div>`;
  document.body.appendChild(sc);
  const inp = $("#srchIn", sc), list = $("#srchList", sc);
  let items = [], act = 0, seq = 0, timer = null;
  const mark = n => { const rows = $$(".sres", list); if (!rows.length) return; act = (n + rows.length) % rows.length; rows.forEach((r, i) => r.classList.toggle("act", i === act)); rows[act].scrollIntoView({block: "nearest"}); };
  const draw = r => {
    items = [];
    list.innerHTML = (r.groups || []).map(g => `<div class="sgrp">${esc(g.label)}</div>` + g.items.map(it => { const i = items.push(it) - 1; return `<div class="sres ${i === 0 ? "act" : ""}" role="option" data-i="${i}">${srchIcon(it)}<span class="stt">${esc(it.title)}</span><span class="ssub">${esc(it.sub || "")}</span><button class="iconbtn smore" aria-label="Actions" tabindex="-1">${ICON.more}</button><span class="sret">↵</span></div>`; }).join("")).join("")
      || `<div class="sempty">${inp.value.trim() ? `Nothing matches “${esc(inp.value.trim())}”. Try fewer words, or another type.` : "Nothing here yet."}</div>`;
    act = 0;
  };
  const skeleton = () => { list.innerHTML = `<div class="sk-wrap" aria-hidden="true">${[62, 48, 71, 40, 55].map(w => `<div class="sres sk"><span class="sic"><i class="sk-dot"></i></span><span class="sk-bar" style="width:${w}%"></span></div>`).join("")}</div>`; };
  const load = async () => {
    const mine = ++seq, tb = $('[aria-selected="true"]', $(".srch-tabs", sc)).dataset.tab;
    $("#srchSpin", sc).hidden = false; skeleton();
    try { const r = await api(`/api/hub/search?q=${encodeURIComponent(inp.value.trim())}&tab=${tb}`); if (mine === seq && $("#srch")) draw(r); }
    catch (e) { if (mine === seq) list.innerHTML = `<div class="sempty">${esc(e.message)}</div>`; }
    if (mine === seq) $("#srchSpin", sc).hidden = true;
  };
  const choose = async (it, alt) => {
    if (!it) return;
    if (alt && (it.kind === "chat" || it.kind === "code")) { const row = $(`.sres[data-i="${items.indexOf(it)}"] .smore`, sc); return threadMenu({type: "click", currentTarget: row}, it.id); }
    closeSearch();
    if (it.kind === "chat" || it.kind === "code") { S.mode = it.kind === "code" ? "code" : "chat"; store("appmode", S.mode); syncMode(); openThread(it.id); }
    else if (it.kind === "project") { S.mode = "chat"; syncMode(); openProject(it.id); }
    else if (it.kind === "artifact") openPreview(it.root, it.path);
    else if (it.kind === "scheduled") goPage("scheduled");
    else if (it.id === "new_chat") { S.mode = "chat"; syncMode(); newThread(); }
    else if (it.id === "new_session") { S.mode = "code"; syncMode(); newThread(); }
    else if (it.id === "settings") openSettings();
  };
  const tabTo = d => { const tabs = $$(".srch-tabs [data-tab]", sc), i = tabs.findIndex(b => b.getAttribute("aria-selected") === "true"), n = tabs[(i + d + tabs.length) % tabs.length]; tabs.forEach(b => b.setAttribute("aria-selected", String(b === n))); load(); };
  sc._key = e => {
    if (!$("#srch")) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if ($("#ctx")) closeCtx(); else closeSearch(); }
    else if ($("#ctx")) return;
    else if (e.key === "ArrowDown") { e.preventDefault(); mark(act + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); mark(act - 1); }
    else if (e.key === "Enter") { e.preventDefault(); choose(items[act], e.altKey); }
    else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !inp.value) { e.preventDefault(); tabTo(e.key === "ArrowRight" ? 1 : -1); }
    else if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); const q = inp.value.trim(); closeSearch(); newThread(); if (q) { $("#input").value = q; $("#input").dispatchEvent(new Event("input")); } $("#input")?.focus(); }
  };
  document.addEventListener("keydown", sc._key, true);
  sc.addEventListener("mousedown", e => { if (e.target === sc) closeSearch(); });
  $("#srchX", sc).onclick = closeSearch;
  $$("[data-tab]", sc).forEach(b => b.onclick = () => { $$(".srch-tabs [data-tab]", sc).forEach(x => x.setAttribute("aria-selected", String(x === b))); inp.focus(); load(); });
  list.addEventListener("mousemove", e => { const r = e.target.closest(".sres[data-i]"); if (r && +r.dataset.i !== act) mark(+r.dataset.i); });
  list.addEventListener("click", e => { const r = e.target.closest(".sres[data-i]"); if (!r) return; if (e.target.closest(".smore")) return choose(items[+r.dataset.i], true); choose(items[+r.dataset.i]); });
  inp.oninput = () => { clearTimeout(timer); timer = setTimeout(load, 180); };
  inp.focus(); load();
}

/* ======================================== apps menu ======================================== */
function appsMenu(e) {
  const a = S.acct || {};
  ctxMenu({type: "click", currentTarget: e.currentTarget}, [
    {head: "Meshpool on this computer"},
    {label: "Install the command line tool", icon: ICON.term, fn: async () => { try { const r = await post("/api/install-cli", {}); toast(r.message || "Installed. Open a new terminal and type meshpool.", 5000); } catch (err) { toast(err.message, 5000); } }},
    {label: "Download Meshpool apps", icon: ICON.dl, fn: () => openExternal(a.billing_url || "https://meshpool.ai")},
    {label: "Check for updates", icon: ICON.refresh, fn: () => openSettings("about")},
    ...(MP.desktop && !S.settings?.native ? [{sep: true}, {label: "Quit Meshpool", icon: ICON.logout, fn: () => post("/api/quit").then(() => document.body.innerHTML = `<div class="onb"><div class="onb-card"><h1>Meshpool has stopped</h1><p>You can close this window.</p></div></div>`)}] : []),
  ], {cls: "acctmenu", up: true});
}

/* ================================== top bar ================================== */
// Set an element's HTML only when it differs from what is there: a header or bar that did not change is not rebuilt,
// so nothing flashes, loses hover or focus, or does layout work during a run.
function setHTML(el, html, tight) { if (tight) html = html.replace(/>\s+</g, "><").trim(); if (!el || el._h === html) return false; el._h = html; el.innerHTML = html; return true; }
function stepLabel() { const pd = planData(); return pd ? `Step ${Math.min(pd.current + 1, pd.total)} of ${pd.total}` : "Progress"; }
function updateStep() { const el = $("#stepLbl"); if (el) setTxt(el, stepLabel()); }
function renderTop() {
  const v = S.cur?.view, onProject = S.view === "project", onPage = S.view === "page";
  const hidden = $("#app").classList.contains("noside") || $("#app").classList.contains("autoside") || narrow();
  const root = curRoot();
  const pid = onProject ? S.pjId : v ? (v.project_id || S.threads.find(t => t.id === v.id)?.project_id || "") : (S.draft.pid || "");
  const pj = pid && S.pj.find(p => p.id === pid);
  const folderChat = !onProject && !onPage && !!root && (v ? !v.scratch && !pj : !!S.draft.project);
  const title = onProject ? (pj?.name || "Project") : v ? (v.title || "New chat") : "";
  const open = !!S.panel && !$("#panel")?.hidden;
  const a = $("#app"); a.classList.toggle("pageview", onPage); a.classList.toggle("homeview", !v && !onProject && !onPage);
  $("#topbar").classList.toggle("newt", !v);
  const topHTML = `
    ${hidden ? `<button class="tbtn showside" id="showSide" data-tip="Show sidebar" data-kbd="${MOD}B" data-tip-pos="below" aria-label="Show sidebar">${ICON.side}<i class="ndot" ${attention() ? "" : "hidden"}></i></button>` : ""}
    ${pj && !onProject && v ? `<button class="chip" id="pjChip" data-tip="Open project" data-tip-pos="below">${ICON.stack}<span class="cl">${esc(pj.name)}</span></button><span class="crumb" aria-hidden="true">/</span>` : ""}
    ${!v && !onProject && !onPage && S.mode === "code" ? `<span class="toplabel">New session</span>` : ""}
    ${v || onProject ? `<span class="title" id="ttl" data-id="${esc(v?.id || "")}" ${v ? `role="button" tabindex="0" aria-haspopup="menu" data-tip="Chat options" data-tip-pos="below"` : ""}>${esc(title)}</span>${v ? `<button class="tbtn ttlchev" id="ttlChev" aria-label="Chat options" aria-haspopup="menu">${ICON.chev}</button>` : ""}` : ""}
    ${v ? `<button class="tbtn devbtn" id="devBtn" data-tip="This computer" data-tip-pos="below" aria-label="This computer" aria-haspopup="menu">${ICON.monitor}<i class="gdot"></i></button>` : ""}
    ${folderChat ? `<button class="chip" id="projChip" data-tip="${esc(isMgd(root) ? projName(root) : root)}" data-tip-pos="below">${ICON.dir}<span class="cl">${esc(projName(root))}</span></button>` : ""}
    <span id="branchChip">${v && curMode() !== "cowork" && !v.scratch ? (S.branchHTML || {})[root] || "" : ""}</span>
    <span class="sp"></span>
    ${v ? `<button class="tbtn stepbtn" id="panelBtn" aria-label="${open ? "Hide" : "Show"} progress" aria-pressed="${open}" data-tip="${open ? "Hide" : "Show"} progress" data-kbd="${MOD}." data-tip-pos="below">${ICON.list}<span id="stepLbl">${esc(stepLabel())}</span></button><button class="tbtn" id="shareBtn" aria-label="Export chat" data-tip="Copy or save this chat" data-tip-pos="below" aria-haspopup="menu">${ICON.share}<span>Export</span></button>` : ""}
    ${!v && !onPage && !onProject ? `<button class="tbtn" id="flagBtn" data-tip="Report a problem" data-tip-pos="below" aria-label="Report a problem">${ICON.flag}</button>` : ""}`;
  if (!setHTML($("#topbar"), topHTML, true)) { renderBranch(); return; }      // nothing changed: leave the header alone
  $("#showSide")?.addEventListener("click", toggleSide);
  $("#panelBtn")?.addEventListener("click", () => togglePanel(S.panel || (panelSet().includes("progress") ? "progress" : panelSet()[0]), !S.panel));
  $("#shareBtn")?.addEventListener("click", e => exportMenu(e));
  $("#devBtn")?.addEventListener("click", e => devPop(e));
  $("#flagBtn")?.addEventListener("click", () => reportProblem());
  $("#pjChip")?.addEventListener("click", () => openProject(pid));
  $("#projChip")?.addEventListener("click", () => { if (!v) chooseDraftFolder(); else if (isMgd(root)) togglePanel("session", true); else openFolderInApp(root, ""); });
  const ttl = $("#ttl");
  if (v) {
    ttl.ondblclick = renameTop; ttl.onclick = e => { if (ttl.contentEditable !== "true") chatMenu({type: "click", currentTarget: ttl}); };
    $("#ttlChev").onclick = () => chatMenu({type: "click", currentTarget: ttl});
    ttl.onkeydown = e => { if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); chatMenu({type: "key", currentTarget: ttl}); } };
  }
  renderBranch(); syncMode();
}
// The menu behind the chat's name: P, R, A and D work while it is open.
function chatMenu(e) {
  const v = S.cur?.view; if (!v) return;
  const t = S.threads.find(x => x.id === v.id) || {id: v.id, title: v.title, pinned: v.pinned};
  const inPj = (v.project_id || t.project_id) && S.pj.find(p => p.id === (v.project_id || t.project_id));
  const projects = [
    ...S.pj.map(p => ({label: p.name, icon: ICON.stack, checked: inPj && p.id === inPj.id ? true : undefined, fn: () => inPj && p.id === inPj.id || moveChat(v.id, p.id)})),
    ...(S.pj.length ? [{sep: true}] : []),
    {label: "New project…", icon: ICON.plus, fn: async () => { const pid = await newProjectFlow({stay: true}); if (pid) moveChat(v.id, pid); }},
    ...(inPj ? [{label: "Remove from project", icon: ICON.x, fn: () => moveChat(v.id, "")}] : []),
  ];
  ctxMenu(e, [
    {label: t.pinned ? "Unpin" : "Pin", icon: ICON.pin, key: "p", kbd: "P", fn: () => threadAct(v.id, t.pinned ? "unpin" : "pin")},
    {label: "Rename", icon: ICON.pencil, key: "r", kbd: "R", fn: renameTop},
    {label: inPj ? "Move to project" : "Add to project", icon: ICON.stack, sub: projects},
    {label: "Export chat", icon: ICON.share, fn: () => exportMenu({type: "click", currentTarget: $("#shareBtn") || $("#ttl")})},
    {label: "Show every step", icon: ICON.list, checked: !!S.steps, kbd: `${MOD}E`, fn: toggleSteps},
    {sep: true},
    {label: "Archive", icon: ICON.archive, key: "a", kbd: "A", fn: () => threadAct(v.id, "archive")},
    {label: "Delete", icon: ICON.trash, danger: true, key: "d", kbd: "D", fn: () => confirmDelete(v.id)},
  ]);
}
function renameTop() {
  const v = S.cur?.view, ttl = $("#ttl"); if (!v || !ttl) return;
  closeCtx();
  const old = v.title || "New chat";
  ttl.contentEditable = "true"; ttl.focus(); document.getSelection().selectAllChildren(ttl);
  let finished = false;
  const done = async save => {
    if (finished) return; finished = true;
    ttl.contentEditable = "false"; const n = ttl.textContent.trim();
    if (save && n && n !== old) { try { await post(`/api/threads/${v.id}`, {action: "rename", title: n}); v.title = n; loadThreads(); } catch (e) { toast(e.message); ttl.textContent = old; } }
    else ttl.textContent = old;
    ttl.onkeydown = null; renderTop();
  };
  ttl.onkeydown = e => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); done(true); } if (e.key === "Escape") { e.preventDefault(); done(false); } };
  ttl.onblur = () => done(true);
}
function devPop(e) {
  const a = S.acct || {}, v = S.cur?.view || {}, root = curRoot();
  const own = !!v.scratch;
  ctxMenu({type: "click", currentTarget: e.currentTarget}, [
    {head: `<b>${esc(a.device?.name || "This computer")}</b><span class="conn"><i class="gdot"></i>Connected</span><small>${own ? "This chat works in its own private folder on this computer." : `Meshpool can read and change files in ${esc(shortPath(root))} on this computer.`}</small>`, html: true},
    {label: own ? "Browse the chat's folder" : "Open in Meshpool editor", icon: ICON.folderopen, fn: () => root && openFolderInApp(root, "")},
  ], {cls: "devmenu"});
}
// What the chat says, as Markdown: copied, or saved as a file.
function chatMarkdown() {
  const out = [`# ${S.cur?.view?.title || "Chat"}`];
  for (const e of S.cur?.log || []) {
    if (e.type === "user" && !e.steer && !/^Continue (working|where)/.test(e.text || "")) out.push(`**You**\n\n${e.text}`);
    else if (e.type === "assistant" && e.text) out.push(`**Meshpool**\n\n${stripGoal(e.text)}`);
  }
  return out.join("\n\n---\n\n") + "\n";
}
function exportMenu(e) {
  if (!S.cur) return;
  ctxMenu({type: "click", currentTarget: e.currentTarget}, [
    {label: "Copy as Markdown", icon: ICON.copy, fn: () => navigator.clipboard.writeText(chatMarkdown()).then(() => toast("Chat copied"), () => toast("Could not copy. Save it as a file instead."))},
    {label: "Save as a file", icon: ICON.dl, fn: () => { const a = Object.assign(document.createElement("a"), {href: URL.createObjectURL(new Blob([chatMarkdown()], {type: "text/markdown"})), download: ((S.cur.view.title || "chat").replace(/[\\/:*?"<>|\x00-\x1f]+/g, "").trim().slice(0, 60) || "chat") + ".md"}); document.body.appendChild(a); a.click(); a.remove(); }},
  ]);
}
async function renderBranch() {
  const root = curRoot(); const el = $("#branchChip"); if (!el || !root) return;
  if (curMode() === "cowork" || S.cur?.view?.scratch) { el.innerHTML = ""; return; }
  try {
    const g = await api("/api/git?root=" + encodeURIComponent(root));
    S.git = g;
    const html = g.repo ? `<button class="chip" id="brChip" data-tip="Branch. Open the Git panel." data-tip-pos="below">${ICON.branch}${esc(g.branch)}${g.dirty ? ` · ${g.dirty} changed` : ""}</button>` : "";
    S.branchHTML = {...(S.branchHTML || {}), [root]: html};
    if (curRoot() === root && el.innerHTML !== html) el.innerHTML = html;
    $("#brChip")?.addEventListener("click", () => togglePanel("git", true));
    if (g.repo && g.github) ciChip(root);
  } catch (e) { el.innerHTML = ""; }
}
const CI_TEXT = {passing: "Checks passing", failing: "Checks failing", running: "Checks running"};
async function ciChip(root, fresh) {
  const c = S.ciCache?.[root];
  let ci = c && !fresh && Date.now() - c.t < 60000 ? c.ci : null;
  if (!ci) { try { ci = await api("/api/git/ci?root=" + encodeURIComponent(root)); } catch (e) { return; } S.ciCache = {...(S.ciCache || {}), [root]: {t: Date.now(), ci}}; }
  const el = $("#branchChip"); if (!el || curRoot() !== root || !CI_TEXT[ci.summary]) return;
  $("#ciChip")?.remove();
  el.insertAdjacentHTML("beforeend", `<button class="chip ci ${ci.summary}" id="ciChip" title="${esc(ci.pr ? "#" + ci.pr.number + " " + ci.pr.title : ci.branch)}"><span class="cidot"></span>${CI_TEXT[ci.summary]}</button>`);
  $("#ciChip").onclick = () => togglePanel("git", true);
}
const PANELS = {cowork: ["session", "progress", "outputs", "browser"], code: ["session", "diff", "files", "term", "git", "browser", "preview"]};
function curMode() { return S.cur?.view?.kind || "cowork"; }
// A chat with its own private folder has no repository, so it gets the simple tabs; a chat in a real folder gets
// the developer tabs when it is a code chat.
function panelSet() { const v = S.cur?.view; return !v || v.scratch ? PANELS.cowork : v.worktree ? ["worktree", ...PANELS[curMode()]] : PANELS[curMode()]; }
function linkOf(pid) { return (S.pj.find(p => p.id === pid) || {}).link || null; }
function curRoot() { return S.cur?.view?.workdir || S.draft.project || linkOf(S.draft.pid)?.path || ""; }

/* ---------- modal focus: Tab stays inside the open dialog, and focus goes back to what opened it ---------- */
(() => {
  const FOC = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  const top = () => { const all = document.querySelectorAll("body > .scrim"); return all[all.length - 1] || null; };
  const openers = new WeakMap();
  // B9: while a modal is open the page behind it is inert: no focus, no clicks, hidden from screen readers.
  const syncInert = () => {
    const app = document.getElementById("app"); if (!app) return;
    const modal = top();
    if (modal) {
      app.inert = true;
      if (!modal.contains(document.activeElement)) { const t = modal.querySelector('[autofocus],.modal,[role=dialog]'); if (t) { if (!t.hasAttribute("tabindex") && !/^(BUTTON|INPUT|A)$/.test(t.tagName)) t.setAttribute("tabindex", "-1"); t.focus?.({preventScroll: true}); } }
    } else app.inert = false;
  };
  new MutationObserver(ms => {
    syncInert();
    for (const m of ms) {
      for (const n of m.addedNodes) if (n.nodeType === 1 && n.classList?.contains("scrim")) openers.set(n, m.target === document.body ? lastFocus : null);
      for (const n of m.removedNodes) if (n.nodeType === 1 && n.classList?.contains("scrim")) { const o = openers.get(n); if (o && o.isConnected && !top()) setTimeout(() => { if (document.activeElement === document.body || !document.activeElement) o.focus?.({preventScroll: true}); }, 0); }
    }
  }).observe(document.body, {childList: true});
  let lastFocus = null;
  document.addEventListener("focusin", e => { if (!e.target.closest?.(".scrim")) lastFocus = e.target; }, true);
  document.addEventListener("keydown", e => {
    if (e.key !== "Tab") return;
    const sc = top(); if (!sc) return;
    const f = [...sc.querySelectorAll(FOC)].filter(x => x.offsetParent !== null || x === document.activeElement);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1], a = document.activeElement;
    if (!sc.contains(a)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && a === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && a === last) { e.preventDefault(); first.focus(); }
  }, true);
})();

/* ---------- Back / Forward move between pages and chats ---------- */
(() => {
  let restoring = false, lastKey = "";
  window.navPush = key => {
    if (restoring || key === lastKey) return; lastKey = key;
    try { history.pushState({mp: key}, "", location.pathname + location.search + (/^#settings/.test(location.hash) ? "" : location.hash)); } catch (e) { /* sandboxed frames */ }
  };
  addEventListener("popstate", e => {
    const k = e.state?.mp; if (!k || typeof k !== "string") return;
    restoring = true; lastKey = k;
    try {
      const [t, id] = [k.slice(0, k.indexOf(":")), k.slice(k.indexOf(":") + 1)];
      if (t === "chat") openThread(id); else if (t === "page") goPage(id); else if (t === "project") openProject(id); else if (t === "new") newThread();
    } finally { setTimeout(() => { restoring = false; }, 0); }
  });
})();
