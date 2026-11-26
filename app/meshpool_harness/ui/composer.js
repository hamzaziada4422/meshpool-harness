/* The composer's controls and the home screens: the plus menu, the project, environment and repository selectors, the
   model and permission buttons under the box, and the Chat and Code home views. Loaded before app.js; every function
   runs on a click or a render, never at load. */
"use strict";

// The three things a chat may do without asking. The keys are what the server stores; the words are for people.
const MODE_NAME = {ask: "Ask", edits: "Accept edits", auto: "Full access"};

/* ---------- a searchable list in a popover: project, environment and repository selectors ---------- */
// groups: [{label, items: [{id, label, desc, icon, check, note, fn}]}], footer: same item shape. Arrow keys, Enter, Esc.
function listPop({anchor, groups, footer = [], placeholder = "Search", search = true, width = 300, tools = "", empty = "Nothing matches.", note = ""}) {
  closeCtx();
  const p = openPop(anchor, `<div class="lp">${search ? `<div class="lp-s">${ICON.search}<input class="lp-q" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" autocomplete="off" spellcheck="false">${tools}</div>` : ""}<div class="lp-l" role="listbox"></div>${note ? `<div class="lp-n">${note}</div>` : ""}<div class="lp-f"></div></div>`, {width});
  p.classList.add("listpop");
  let act = 0, flat = [];
  const row = (it, i) => `<button class="lp-i ${i === act ? "act" : ""}" role="option" data-i="${i}" ${it.disabled ? "disabled" : ""}>${it.icon ? `<span class="lp-ic">${it.icon}</span>` : ""}<span class="lp-t">${esc(it.label)}${it.desc ? `<small>${esc(it.desc)}</small>` : ""}</span>${it.note ? `<span class="mnote">${esc(it.note)}</span>` : ""}${it.check ? `<span class="lp-ck">${ICON.check}</span>` : ""}</button>`;
  const draw = () => {
    const q = ($(".lp-q", p)?.value || "").trim().toLowerCase();
    flat = [];
    const gs = (typeof groups === "function" ? groups(q) : groups).map(g => ({...g, items: g.items.filter(it => !q || (it.label + " " + (it.desc || "")).toLowerCase().includes(q))})).filter(g => g.items.length);
    $(".lp-l", p).innerHTML = gs.map(g => `<div class="grp">${esc(g.label)}</div>` + g.items.map(it => row(it, flat.push(it) - 1)).join("")).join("") || `<div class="pe">${esc(empty)}</div>`;
    const f = footer.filter(it => !q || it.always); $(".lp-f", p).innerHTML = f.map(it => { const i = flat.push(it) - 1; return row(it, i); }).join("");
    p._place?.();
  };
  const pick = i => { const it = flat[i]; if (!it || it.disabled) return; if (!it.keep) closePop(); it.fn?.(); };
  p.addEventListener("click", e => { const b = e.target.closest(".lp-i"); if (b) pick(+b.dataset.i); });
  const mark = n => { const rows = $$(".lp-i", p); if (!rows.length) return; act = (n + rows.length) % rows.length; rows.forEach((r, i) => r.classList.toggle("act", i === act)); rows[act].scrollIntoView({block: "nearest"}); };
  p.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); mark(act + 1); } else if (e.key === "ArrowUp") { e.preventDefault(); mark(act - 1); }
    else if (e.key === "Enter") { e.preventDefault(); pick(act); }
    else if (e.key === "Escape") { e.stopPropagation(); closePop(); anchor.focus?.({preventScroll: true}); }
  });
  $(".lp-q", p)?.addEventListener("input", () => { act = 0; draw(); });
  draw(); $(".lp-q", p)?.focus();
  p.redraw = draw;
  return p;
}

/* ---------- the plus menu ---------- */
async function loadCaps() { try { S.caps = (await api("/api/capabilities")).features; } catch (e) { S.caps = S.caps || []; } if (!S.cur) S.draft.plan = !!capOn("plan_first"); }   // Settings, Plan first: the next new chat starts with a plan
async function loadConnectors() { try { S.connCache = (await api("/api/connectors")).connectors; } catch (e) { S.connCache = S.connCache || []; } }
const capOn = id => (S.caps || []).find(f => f.id === id)?.on;
function plusMenu(anchor) {
  const c = cfg(), h = harness(c.harness);
  const skills = (S.skills || []).filter(s => s.enabled).slice(0, 14).map(s => ({label: "$" + s.name, desc: clip(s.description || "", 60), fn: () => { const inp = $("#input"); inp.value = `$${s.name} ` + inp.value; inp.focus(); inp.dispatchEvent(new Event("input")); }}));
  const conns = (S.connCache || []).filter(x => x.connected && x.enabled).map(x => ({label: x.name, checked: true, fn: () => goPage("plugins")}));
  ctxMenu({type: "click", currentTarget: anchor}, [
    {label: "Upload or add files", icon: ICON.clip, kbd: `${MOD}U`, fn: () => $("#fileIn").click()},
    {label: "Plan first", icon: ICON.list, checked: !!c.plan, desc: "Write a plan and wait for your OK", fn: () => setPlan(!cfg().plan)},
    {label: "Memory", icon: ICON.brain, checked: capOn("memory") ? true : undefined, fn: async () => { try { await post("/api/capabilities", {id: "memory", on: !capOn("memory")}); await loadCaps(); toast(capOn("memory") ? "Memory is on for new chats" : "Memory is off for new chats"); } catch (e) { toast(e.message); } }},
    {label: "Style", icon: ICON.pencil, note: styleOf(styleId()).name, sub: styleMenuItems()},
    {sep: true},
    {label: "Skills", icon: ICON.skill, sub: skills.length ? skills : [{label: "No skills yet", fn: () => openSettings("skills")}]},
    {label: "Plugins", icon: ICON.plug, sub: [...conns, ...(conns.length ? [{sep: true}] : []), {label: "Manage plugins", icon: ICON.gear, fn: () => goPage("plugins")}]},
    ...(typeof wtMenuItem === "function" ? wtMenuItem() : []),
    ...(!S.cur && S.view !== "project" ? [{sep: true}, {label: S.draft.project ? "Change folder" : "Work in a folder", icon: ICON.folder, desc: S.draft.project ? projName(S.draft.project) : "", fn: chooseDraftFolder}, ...(S.draft.project ? [{label: "Don’t use a folder", icon: ICON.x, fn: () => { S.draft.project = ""; renderTop(); renderBar(); renderHero(); }}] : [])] : []),
  ], {cls: "plusmenu", up: true});
}

/* ---------- writing styles: how Meshpool words its replies; per chat, new chats start with the default ---------- */
async function loadStyles() { try { S.styles = await api("/api/styles"); } catch (e) { S.styles = S.styles || {styles: [], default: "normal"}; } }
const styleOf = id => (S.styles?.styles || []).find(x => x.id === id) || {id: "normal", name: "Normal"};
const styleId = () => cfg().style || S.styles?.default || "normal";
async function setStyle(id) {
  if (S.cur) { try { await post(`/api/agent/${S.cur.id}/style`, {style: id, since: 1e9}); S.cur.view.style = id; } catch (e) { return toast(e.message); } }
  else S.draft.style = id;
  renderBar();
}
function styleMenuItems() {
  const cur = styleId();
  return [...(S.styles?.styles || [{id: "normal", name: "Normal"}]).map(x => ({label: x.name, desc: x.desc || clip(x.instruction || "", 60), checked: x.id === cur ? true : undefined, fn: () => setStyle(x.id)})),
    {sep: true}, {label: "Manage styles", icon: ICON.gear, fn: () => openSettings("styles")}];
}
function styleMenu(anchor) {
  ctxMenu({type: "click", currentTarget: anchor}, [{head: S.cur ? "Style · this chat" : "Style · new chats"}, ...styleMenuItems()], {cls: "stylemenu", up: true});
}

/* ---------- harness: Auto (recommended) or one of the available agents ---------- */
// The default for new chats is kept on this computer; a chat keeps the harness it started with.
const modeHarness = () => store("harness_default") || "meshpool-auto";
const harnessChoices = () => S.harnesses.filter(h => !h.error && h.available !== false && h.id !== "meshpool-auto");
function harnessShort() { const c = cfg().harness; return c === "meshpool-auto" || !c ? "Auto" : (c.startsWith("meshpool") ? "meshpool" : harness(c).name); }
function pickHarness(id) {
  if (S.cur) { if (id === S.cur.view.harness?.id || (id === "meshpool-auto" && S.cur.auto)) return; toast("An agent can’t change mid-chat, so this started a new chat with " + (id === "meshpool-auto" ? "Auto" : harness(id).name) + "."); newThread(); }
  S.draft.harness = id; renderBar(); renderHero();
}
function harnessMenuItems() {
  const c = cfg().harness, auto = !S.cur ? c === "meshpool-auto" : !!S.cur.auto;
  return [{label: "Auto (recommended)", desc: "meshpool picks the agent that suits each request", checked: auto ? true : undefined, fn: () => pickHarness("meshpool-auto")},
    ...harnessChoices().map(h => ({label: h.name, desc: h.ready === false ? "Needs setup" : (h.tagline || h.description || ""), checked: !auto && h.id === c ? true : undefined, fn: () => {
      if (h.ready === false) return openSettings(h.login ? "harnesses" : "general");
      pickHarness(h.id); }}))];
}

/* ---------- permission mode and model ---------- */
function permMenu(anchor) {
  const c = cfg();
  ctxMenu({type: "click", currentTarget: anchor}, [
    {head: "Permissions"},
    ...Object.entries(MODES).map(([k, [lb, d]]) => ({label: lb, desc: d, checked: k === c.mode ? true : undefined, fn: () => setMode(k)})),
  ], {cls: "permmenu", up: true});
}
function modelsForMenu() {
  const M = S.models || {models: [], favorites: [], default: ""}, c = cfg(), seen = new Set(), out = [];
  const add = id => { if (!id || seen.has(id)) return; const m = M.models.find(x => x.id === id) || (id === c.model ? {id} : null); if (m) { seen.add(id); out.push(m); } };
  add(c.model); add(M.default); (M.favorites || []).forEach(add); M.models.forEach(m => out.length < 4 && add(m.id));
  return out.slice(0, 4);
}
const EFFORT_NAME = {auto: "Auto", low: "Low", medium: "Medium", high: "High"};
function effortMenuItems(c) {
  if (S.cur && c.effort == null) return [{head: "Effort"}, {label: "Set by this agent", desc: "This agent chooses its own effort.", checked: true, fn: () => {}}];
  const cur = c.effort || "auto";
  const rows = [["auto", "meshpool sizes the effort to each request: quick questions stay quick, big jobs get more steps."],
    ["low", "Least thinking: fastest and cheapest. For simple, clear asks."], ["medium", "More thinking before it acts."],
    ["high", "The most thinking the model offers. Slower and uses more tokens. Models without effort levels ignore it."]];
  return [{head: "Effort"}, ...rows.map(([id, desc]) => ({label: EFFORT_NAME[id], desc, checked: id === cur ? true : undefined, fn: () => setEffort(id)}))];
}
function modelMenu(anchor) {
  const c = cfg(), M = S.models || {models: [], groups: []};
  const rows = modelsForMenu().map((m, i) => ({label: modelName(m.id) + (provOf(m.id) === "chatgpt" ? " · ChatGPT" : ""), desc: (m.caps || []).join(", ") || (provOf(m.id) === "chatgpt" ? "Through your ChatGPT sign-in" : m.id === M.default ? "Your default model" : "From your meshpool endpoint"), checked: m.id === c.model ? true : undefined, key: String(i + 1), fn: () => pickModel(m.id)}));
  ctxMenu({type: "click", currentTarget: anchor}, [
    {head: S.cur ? "Model · this chat" : "Model · new chats"},
    ...(rows.length ? rows : [{label: "No models yet", desc: "Add a key in Settings to see models", fn: () => openSettings("general")}]),
    {sep: true},
    {label: "Harness", note: S.cur && !S.cur.auto ? harness(c.harness).name.replace(/^meshpool .*/i, "meshpool") : harnessShort(), sub: harnessMenuItems()},
    {label: "Effort", note: EFFORT_NAME[c.effort || ""] || "Auto", sub: effortMenuItems(c)},
    {label: "More models", more: true, fn: () => modelPop(anchor)},
  ], {cls: "modelmenu", up: true});
}

/* ---------- the row under the composer ---------- */
// The home title names the context: the project the next chat goes into, else "New chat". No greeting.
const heroTitle = () => (S.pj || []).find(p => p.id === S.draft?.pid)?.name || "New chat";
function renderSel() {
  const c = cfg(), code = S.mode === "code", home = !S.cur && S.view === "chat";
  const label = c.model ? modelName(c.model) + (provOf(c.model) === "chatgpt" ? " · ChatGPT" : "") : "Model";
  const eff = EFFORT_NAME[c.effort || ""] || "Auto", perm = MODE_NAME[c.mode] || "Ask";   // the muted word is the effort
  const pj = S.pj.find(p => p.id === S.draft.pid);
  const root = S.draft.project, mg = isMgd(root);
  let left = "";
  if (home && !code) left = `<button class="sel" id="projSel" aria-haspopup="listbox">${pj ? esc(clip(pj.name, 28)) : "Project"}${ICON.chev}</button>`;
  else if (home && code) left = `<button class="sel" id="envSel" aria-haspopup="listbox">${mg ? ICON.cloud : ICON.monitor}<span>${root ? esc(clip(projName(root), 24)) : "Choose a folder"}</span>${ICON.chev}</button><button class="sel" id="repoSel" aria-haspopup="listbox">${ICON.pr}<span>${mg ? esc(clip(projName(root), 22)) : "Repo"}</span>${ICON.chev}</button>`;
  else left = `<span class="disc">Answers can be wrong. Check what matters.</span>`;
  const html = `<span class="sl">${left}</span><span class="sr2"><button class="sel mdl" id="mPill" aria-haspopup="menu" aria-label="Model: ${esc(label)}, effort: ${esc(eff)}" data-tip="Model, and how hard it thinks (effort: ${esc(eff)})" data-tip-pos="above"><span class="lbl">${esc(label)}</span><em>${esc(eff)}</em></button><button class="sel ${c.mode === "auto" ? "hot" : ""}" id="aPill" aria-haspopup="menu" aria-label="Permissions: ${esc(perm)}" data-tip="Permissions: what it may do without asking (${esc(perm)})" data-tip-pos="above">${esc(perm)}</button></span>`;
  if (setHTML($("#selrow"), html, true)) {
    $("#mPill").onclick = e => modelMenu(e.currentTarget);
    $("#aPill").onclick = e => permMenu(e.currentTarget);
    for (const [id, fn] of [["projSel", projectSelector], ["envSel", envSelector], ["repoSel", repoSelector]]) { const b = $("#" + id); if (b) b.onclick = e => fn(e.currentTarget); }
  }
}
function renderBar() {
  const c = cfg();
  $("#app").classList.toggle("planning", !!c.plan);
  const barHTML = `
    <button class="pill icon" id="attBtn" aria-haspopup="menu" data-tip="Add files and more" data-kbd="${MOD}U" data-tip-pos="above" aria-label="Add files and more">${ICON.plus}</button>
    ${c.plan ? `<button class="pill planchip" id="planChip" data-tip="Plan first is on. Click to turn it off." aria-label="Plan first is on">${ICON.list}<span>Plan first</span></button>` : ""}
    ${styleId() !== "normal" ? `<button class="pill stylechip" id="styleChip" aria-haspopup="menu" data-tip="Writing style: ${esc(styleOf(styleId()).name)}. Click to change." data-tip-pos="above" aria-label="Writing style: ${esc(styleOf(styleId()).name)}">${ICON.pencil}<span class="lbl">${esc(clip(styleOf(styleId()).name, 24))}</span></button>` : ""}
    ${window.cbar010 ? cbar010() : ""}
    <span class="sp"></span>
    ${c.busy ? `<button class="send stop ${S.stopping ? "stopping" : ""}" id="stopBtn" data-tip="Stop" data-kbd="Esc" aria-label="Stop">${S.stopping ? `<span class="spbody"></span>` : ICON.stop}</button><button class="intr" id="intrBtn" data-tip="Stop what it is doing and send this instead" data-kbd="${MOD}Enter" ${S.stopping || !$("#input")?.value.trim() ? "disabled" : ""}>Send now</button>` : ""}
    <button class="send ${c.busy ? "add" : ""}" id="sendBtn" data-tip="${c.busy ? "Add to the task" : "Send"}" data-kbd="Enter" aria-label="${c.busy ? "Add to the task" : "Send"}">${ICON.send}</button>`;
  if (setHTML($("#cbar"), barHTML, true)) {       // rebuilt only when something in it changed; focus and hover survive a run
    $("#attBtn").onclick = e => plusMenu(e.currentTarget);
    const on = (id, fn) => { const b = $("#" + id); if (b) b.onclick = fn; };    // onclick: a kept button is bound once
    on("planChip", () => setPlan(false));
    window.cbar010bind?.();
    on("styleChip", e => styleMenu(e.currentTarget));
    on("sendBtn", () => sendNow(false));
    on("stopBtn", stopNow);
    on("intrBtn", () => { if ($("#input").value.trim() || S.attachments.length) sendNow(true); else { $("#input").focus(); toast("Type what to do instead, then press Send now."); } });
  }
  renderSel();
  const goal = S.cur?.view?.goal;
  const touch = matchMedia("(pointer: coarse)").matches;
  const hintHTML = (goal ? `<span class="goalh" title="${esc(goal)}">Goal: <b>${esc(clip(goal, 70))}</b> <button class="linkbtn" id="goalClr">Clear</button></span>${c.busy && !touch ? " · " : ""}` : "")
    + (c.busy && !touch ? `Enter adds to the task${store("escstop") === "off" ? "" : " · Esc stops"}` : "");
  if (setHTML($("#hintTxt"), hintHTML)) { const g = $("#goalClr"); if (g) g.onclick = () => setGoal(""); }
  const inp = $("#input"), pj = S.view === "project" ? S.pj.find(p => p.id === S.pjId) : null;
  const code = S.mode === "code" && !S.cur;
  const ph = c.busy ? "Reply" : S.cur ? "Reply" : pj ? `Start a chat in ${pj.name}` : code ? "Describe a task or ask a question" : "What would you like to do?";
  if (inp.placeholder !== ph && !inp.disabled) inp.placeholder = ph;
}

/* ---------- selectors ---------- */
function projectSelector(anchor) {
  const draw = () => {
    const pins = S.pj.filter(p => p.pinned), rest = S.pj.filter(p => !p.pinned).slice(0, 30);
    const it = p => ({label: p.name, icon: ICON.stack, check: p.id === S.draft.pid, fn: () => { S.draft.pid = p.id; renderBar(); renderTop(); }});
    return [...(S.draft.pid ? [{label: "", items: [{label: "No project", icon: ICON.x, fn: () => { S.draft.pid = ""; renderBar(); }}]}] : []), ...(pins.length ? [{label: "Pinned", items: pins.map(it)}] : []), ...(rest.length ? [{label: pins.length ? "Recent" : "Projects", items: rest.map(it)}] : [])];
  };
  listPop({anchor, groups: draw, placeholder: "Search projects", width: 300, empty: "No projects yet.",
    tools: `<button class="iconbtn" id="lpPjAll" data-tip="All projects" aria-label="All projects">${ICON.grid}</button>`,
    footer: [{label: "New project", icon: ICON.plus, always: true, fn: async () => { const pid = await newProjectFlow({stay: true}); if (pid) { S.draft.pid = pid; renderBar(); } }}]});
  $("#lpPjAll")?.addEventListener("click", () => { closePop(); goPage("projects"); });
}
function envSelector(anchor) {
  const local = S.projects.filter(p => !p.managed && p.exists !== false), gh = S.projects.filter(p => p.managed);
  const use = p => () => { S.draft.project = p.path; renderBar(); renderTop(); renderHero(); };
  listPop({anchor, placeholder: "Search folders", width: 320,
    groups: () => [
      {label: "Local folder", items: [{label: "Choose a folder…", icon: ICON.folderopen, desc: "A folder on this computer", fn: chooseDraftFolder}, ...local.slice(0, 8).map(p => ({label: p.name, icon: ICON.monitor, desc: shortPath(p.path), check: p.path === S.draft.project, fn: use(p)}))]},
      {label: "GitHub workspace", items: [{label: "Work on a GitHub repository…", icon: ICON.pr, desc: "meshpool keeps its own copy", fn: () => openGithubPicker({title: "Work on a GitHub repository", cta: "Use repository", onReady: r => { S.draft.project = r.path; renderBar(); renderTop(); renderHero(); toast(`${r.name} on ${r.branch} is ready`); }})}, ...gh.map(p => ({label: p.name, icon: ICON.cloud, check: p.path === S.draft.project, fn: use(p)}))]},
    ],
    footer: [{label: "Manage folders", icon: ICON.gear, always: true, fn: () => openSettings("permissions")}]});
}
function repoSelector(anchor) {
  let repos = null, qT = null, p;
  const load = async q => {
    try { repos = (await api("/api/github/repos?q=" + encodeURIComponent(q || ""))).repos; } catch (e) { repos = []; p.note = e.message; $(".lp-n", p) && ($(".lp-n", p).innerHTML = `${esc(e.message)} ${/Connect GitHub/.test(e.message) ? `<button class="linkbtn" id="lpConn">Connect GitHub</button>` : ""}`); $("#lpConn", p)?.addEventListener("click", () => { closePop(); openSettings("connectors"); }); }
    p.redraw();
  };
  p = listPop({anchor, placeholder: "Search repositories", width: 340, empty: "No repositories found.",
    note: `Don’t see yours? <button class="linkbtn" id="lpGh">Connect GitHub</button>`,
    tools: `<button class="iconbtn" id="lpRef" data-tip="Refresh" aria-label="Refresh">${ICON.refresh}</button><button class="iconbtn" id="lpSet" data-tip="Connector settings" aria-label="Connector settings">${ICON.gear}</button>`,
    groups: () => [{label: "", items: [{label: "No repository", icon: ICON.x, check: !isMgd(S.draft.project), fn: () => { if (isMgd(S.draft.project)) S.draft.project = ""; renderBar(); renderTop(); renderHero(); }}]}, {label: "Repositories", items: (repos === null ? [{label: "Loading repositories…", disabled: true}] : repos.length ? [] : [{label: "No repositories found", disabled: true}]).concat((repos || []).map(r => ({label: r.full_name, desc: r.description || "", note: r.private ? "private" : "", fn: async () => {
      try { toast(`Getting ${r.full_name} ready…`); const b = await api("/api/github/branches?repo=" + encodeURIComponent(r.full_name)); const w = await post("/api/github/workspace", {repo: r.full_name, branch: b.default, create: false, base: b.default}); await loadProjects(); S.draft.project = w.path; renderBar(); renderTop(); renderHero(); toast(`${w.name} on ${w.branch} is ready`); } catch (e) { toast(e.message, 5000); }
    }})))}]});
  $("#lpRef", p)?.addEventListener("click", () => load($(".lp-q", p).value));
  $("#lpSet", p)?.addEventListener("click", () => { closePop(); openSettings("connectors"); });
  $("#lpGh", p)?.addEventListener("click", () => { closePop(); openSettings("connectors"); });
  $(".lp-q", p).addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(() => load($(".lp-q", p).value), 250); });
  load("");
}

/* ================================== home ================================== */
const CARDS = () => [
  {id: "key", show: S.settings && !S.settings.has_key && !S.settings.ready, title: "Add your " + lowBrand(S.settings?.key_name || "meshpool") + " key", text: "meshpool needs a key to talk to a model. It takes a minute.", cta: "Add key", fn: () => openSettings("general")},
  {id: "github", show: (S.connCache || []).some(c => /github/i.test(c.id || c.name) && !c.connected), title: "Connect GitHub", text: "Let meshpool work on your repositories and pull requests.", cta: "Connect", fn: () => openSettings("connectors")},
  {id: "project", show: !!S.settings?.ready && !(S.pj || []).length, title: "Keep related work together", text: "A project gives its chats shared instructions, files and memory.", cta: "New project", fn: () => newProjectFlow()},
];
function waitingList() { return chatList().filter(t => isCodeThread(t) && t.state === "waiting").slice(0, 3); }
function renderHero() {
  const col = $("#col"); if (!col || S.view !== "chat" || S.cur) return;
  const dismissed = store("cards_off") || [];
  const card = CARDS().find(c => c.show && !dismissed.includes(c.id));
  const cardHTML = card ? `<div class="setupcard" data-card="${card.id}"><span class="sci">${ICON.spark || ICON.bolt}</span><div class="sct"><b>${esc(card.title)}</b><span>${esc(card.text)}</span></div><button class="btn" id="cardGo">${esc(card.cta)}</button><button class="iconbtn" id="cardX" aria-label="Dismiss">${ICON.x}</button></div>` : "";
  const f = S.draft.project;
  if (S.mode === "code") {
    const w = waitingList();   // no tip on a fresh page: the Plan tip comes after the first reply (chat010.js)
    col.innerHTML = `<div class="hero code"><div class="chome"><img class="mlogo-mark" src="/brand/mark.png" alt="" width="40" height="40">
      ${w.length ? `<div class="waitcard" role="list">${w.map(t => `<button class="wrow" role="listitem" data-open="${esc(t.id)}"><span class="wl">Waiting</span><span class="wt">${esc(t.title)}</span></button>`).join("")}</div>` : `<h1>Start a coding session</h1><p class="sub">${f ? `Working in <b>${esc(projName(f))}</b>.` : "Choose a folder or a repository below, then describe the task."}</p>`}</div>
    </div>`;
    $$(".wrow", col).forEach(b => b.onclick = () => openThread(b.dataset.open));
    return;
  }
  col.innerHTML = `<div class="hero"><h1><img class="mlogo-mark" src="/brand/mark.png" alt="" width="28" height="28">${esc(heroTitle())}</h1>${f ? `<p class="sub">Working in <button class="linkbtn" id="heroProj" data-tip="Change folder">${esc(projName(f))}</button>. <button class="linkbtn" id="heroNoProj">Don’t use a folder</button></p>` : ""}
    ${cardHTML}<div class="starters">${STARTERS.map(([a, b]) => `<button data-s="${esc(b)}">${esc(a)}</button>`).join("")}</div></div>`;
  $("#heroProj")?.addEventListener("click", chooseDraftFolder);
  $("#heroNoProj")?.addEventListener("click", () => { S.draft.project = ""; renderTop(); renderBar(); renderHero(); });
  $("#cardGo")?.addEventListener("click", () => card.fn());
  $("#cardX")?.addEventListener("click", () => { store("cards_off", [...dismissed, card.id]); renderHero(); });
  $$(".starters button").forEach(b => b.onclick = () => { const inp = $("#input"); inp.value = b.dataset.s; inp.focus(); inp.dispatchEvent(new Event("input")); });
}
