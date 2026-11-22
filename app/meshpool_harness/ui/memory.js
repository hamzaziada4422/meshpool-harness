/* The memory page, "What Meshpool knows": portrait, the "Meshpool thinks" tray, facts filed by category with their
   scope, source and use, search and filters, projects, history, a review queue, conflicts and health. Loaded after
   app.js; draws into #col through drawPage("memory"). All data comes from /api/memory (memory_api.py). Rows are
   virtualised past 150 so 1,000 facts stay smooth; every change has an Undo toast. */
"use strict";

PAGE_TITLE.memory = "What Meshpool knows";
ICON.memory = ICON.brain;
const MEM = {tab: "memory", q: "", cat: "", scope: "", kind: "", unused: false, d: null, hl: new Set(), editing: "", sel: "", drag: ""};
const MEM_KIND = {stated: "You said it", learned: "Learned", noticed: "Noticed"};
const MEM_ROW_H = 74, MEM_HEAD_H = 40, MEM_VIRTUAL = 150;
let memT = 0;

async function memLoad(keep) {
  const p = new URLSearchParams({q: MEM.q, category: MEM.cat, scope: MEM.scope, kind: MEM.kind, unused: MEM.unused ? "1" : ""});
  try { MEM.d = await api("/api/memory?" + p); } catch (e) { MEM.err = e.message; }
  if (!keep) MEM.portrait = null;
  if (S.view === "page" && S.page === "memory") pageMemory(true);
}
async function memAct(body, undo) {
  try {
    const r = await post("/api/memory", body);
    await memLoad(true);
    if (undo) toast(undo.text, 7000, {label: "Undo", fn: async () => { await post("/api/memory", undo.body(r)); memLoad(true); }});
    return r;
  } catch (e) { toast(e.message); }
}
const memDate = t => t ? new Date(t * 1000).toLocaleDateString("en", {month: "short", day: "numeric"}) : "";

function memPortraitHTML(d) {
  if (!d.counts.active) return `<div class="mem-empty"><b>Nothing here yet.</b><span>Facts about you show up here as you work.</span><button class="btn primary" id="memAddEmpty">Add a memory</button></div>`;
  const p = MEM.portrait;
  if (!p) return `<div class="mem-portrait sk"><span></span><span></span><span></span></div>`;
  if (!p.sentences.length) return "";
  return `<div class="mem-portrait" aria-label="What Meshpool has understood about you"><h2>The short version</h2><p>${p.sentences.map((s, i) => `<button class="mem-sent" data-sent="${i}">${esc(s.text)}</button>`).join(" ")}</p><small>Click a sentence to see why.</small></div>`;
}
function memTrayHTML(d) {
  if (!d.pending.length) return "";
  return `<section class="mem-tray" aria-label="Meshpool thinks"><h2>Meshpool thinks <span class="cnt">${d.pending.length}</span></h2><p class="mem-sub">Not used until you say yes.</p>${d.pending.map(f => `<div class="mem-card" data-id="${esc(f.id)}"><div class="mem-ct"><b class="mem-text">${esc(f.text)}</b><span class="mem-src">${esc(f.source.text)}${f.source.chat_id ? ` <button class="linkbtn" data-chat="${esc(f.source.chat_id)}">Open</button>` : ""}</span></div><div class="mem-btns"><button class="btn primary" data-confirm="${esc(f.id)}">Yes</button><button class="btn" data-editpend="${esc(f.id)}">Edit</button><button class="btn" data-reject="${esc(f.id)}">No</button></div></div>`).join("")}</section>`;
}
function memConflictsHTML(d) {
  if (!d.conflicts.length) return "";
  return d.conflicts.map(c => `<section class="mem-conf" role="group" aria-label="Two memories disagree"><h2>These two disagree</h2><p class="mem-sub">${esc(c.reason)} The newer one is used for now.</p><div class="mem-pair">${[["a_fact", "b"], ["b_fact", "a"]].map(([k, o]) => `<div class="mem-card"><b class="mem-text">${esc(c[k].text)}</b><span class="mem-src">${esc(c[k].source.text)}</span><button class="btn ${c.winner === c[k].id ? "primary" : ""}" data-win="${esc(c[k].id)}" data-lose="${esc(c[o])}">Use this one</button></div>`).join("")}</div><button class="linkbtn" data-keepboth="${esc(c.a)}|${esc(c.b)}">Keep both</button></section>`).join("");
}
function memHealthHTML(d) {
  const h = d.health, pct = Math.min(100, Math.round(h.share * 100));
  const prop = h.proposal && (h.proposal.merges.length || h.proposal.archive.length)
    ? `<div class="mem-prop"><span>${h.proposal.merges.length ? `${h.proposal.merges.length} duplicate${h.proposal.merges.length > 1 ? "s" : ""}` : ""}${h.proposal.merges.length && h.proposal.archive.length ? ", " : ""}${h.proposal.archive.length ? `${h.proposal.archive.length} unused` : ""}</span><button class="btn" id="memConsolidate">Tidy up</button></div>` : "";
  return `<div class="mem-health" title="Memory shared with every chat uses ${h.used} of ${h.budget} characters"><span class="bar"><i style="width:${pct}%" class="${h.near ? "hot" : ""}"></i></span><small>${pct}% full${h.near ? ". Nothing is dropped." : ""}</small>${prop}</div>`;
}
function memRowHTML(f) {
  const ed = MEM.editing === f.id;
  const hl = MEM.hl.has(f.id) ? " hl" : "";
  return `<div class="mem-row${hl}${f.kind !== "stated" ? " " + f.kind : ""}${f.status === "archived" ? " arch" : ""}" role="listitem" tabindex="${MEM.sel === f.id ? 0 : -1}" data-id="${esc(f.id)}" draggable="true" style="height:${MEM_ROW_H}px">
    <div class="mem-main">${ed ? `<input class="mem-edit" value="${esc(f.text)}" aria-label="Edit this memory" maxlength="400">` : `<span class="mem-text" data-edit="${esc(f.id)}" title="Click to edit">${f.pinned ? `<i class="mem-pin" title="Pinned: always given to agents">${ICON.pin}</i>` : ""}${esc(f.text)}</span>`}
      <span class="mem-meta"><button class="chip mem-scope" data-scope="${esc(f.id)}" title="Where this applies">${esc(f.scope_label)}</button><span class="mem-kind k-${f.kind}">${MEM_KIND[f.kind]}</span><span class="mem-src">${esc(f.source.text)}${f.source.chat_id ? ` <button class="linkbtn" data-chat="${esc(f.source.chat_id)}">Open</button>` : ""}${f.valid_until ? ` · ${f.expired ? "Expired" : "Until"} ${memDate(f.valid_until)}` : ""}</span><span class="mem-use">${esc(f.given_line)}</span></span></div>
    <div class="mem-acts"><button class="iconbtn" data-pin="${esc(f.id)}" data-tip="${f.pinned ? "Unpin" : "Pin"}" aria-label="${f.pinned ? "Unpin" : "Pin"}">${ICON.pin}</button><button class="iconbtn" data-edit="${esc(f.id)}" data-tip="Edit" aria-label="Edit">${ICON.pencil}</button><button class="iconbtn" data-expiry="${esc(f.id)}" data-tip="Set an expiry" aria-label="Set an expiry">${ICON.clock}</button><button class="iconbtn" data-arch="${esc(f.id)}" data-tip="${f.status === "archived" ? "Restore" : "Archive"}" aria-label="${f.status === "archived" ? "Restore" : "Archive"}">${f.status === "archived" ? ICON.undo : ICON.archive}</button><button class="iconbtn" data-del="${esc(f.id)}" data-tip="Delete" aria-label="Delete">${ICON.trash}</button></div></div>`;
}
function memItems(d) {
  const items = [];
  let last = "";
  for (const f of d.facts) {
    if (f.category !== last) { last = f.category; items.push({head: d.labels[f.category] || f.category, n: d.facts.filter(x => x.category === f.category).length}); }
    items.push({f});
  }
  return items;
}
function memListHTML(d) {
  const items = memItems(d);
  if (!items.length) return `<div class="mem-empty"><b>${MEM.q || MEM.cat || MEM.scope || MEM.kind || MEM.unused ? "No memories match." : "No memories yet."}</b><span>${MEM.q ? "Try fewer words." : "Add one, or keep working."}</span></div>`;
  if (d.facts.length <= MEM_VIRTUAL) return `<div class="mem-list" role="list" id="memList">${items.map(i => i.head ? `<h3 class="mem-h">${esc(i.head)} <span class="cnt">${i.n}</span></h3>` : memRowHTML(i.f)).join("")}</div>`;
  return `<div class="mem-list virt" role="list" id="memList" data-virt="1"></div>`;
}
function memVirtualize(d) {
  const box = $("#memList"); if (!box || !box.dataset.virt) return;
  const items = memItems(d), h = it => it.head ? MEM_HEAD_H : MEM_ROW_H;
  const tops = []; let y = 0; for (const it of items) { tops.push(y); y += h(it); }
  box.style.height = y + "px"; box.style.position = "relative";
  const sc = $("#conv");
  const paint = () => {
    const top = Math.max(0, sc.scrollTop - box.offsetTop - 200), bot = sc.scrollTop - box.offsetTop + sc.clientHeight + 200;
    let a = 0; while (a < items.length - 1 && tops[a] + h(items[a]) < top) a++;
    let b = a; while (b < items.length && tops[b] < bot) b++;
    box.innerHTML = `<div style="position:absolute;left:0;right:0;top:${tops[a]}px">${items.slice(a, b).map(i => i.head ? `<h3 class="mem-h" style="height:${MEM_HEAD_H}px">${esc(i.head)} <span class="cnt">${i.n}</span></h3>` : memRowHTML(i.f)).join("")}</div>`;
  };
  sc.onscroll = () => requestAnimationFrame(paint); paint();
}
function memFiltersHTML(d) {
  const sc = [["", "Everywhere and projects"], ["everywhere", "Everywhere"], ...d.projects.map(p => ["project:" + p.id, p.name])];
  return `<div class="mem-filters"><div class="mem-chips" role="group" aria-label="Category"><button class="chip ${MEM.cat ? "" : "on"}" data-cat="">All</button>${d.categories.map(c => `<button class="chip ${MEM.cat === c ? "on" : ""}" data-cat="${c}">${esc(d.labels[c])}</button>`).join("")}</div>
    <div class="mem-sel"><select id="memScope" aria-label="Scope">${sc.map(([v, l]) => `<option value="${esc(v)}" ${MEM.scope === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select><select id="memKind" aria-label="How it was learned">${[["", "Any source"], ["stated", "You said it"], ["learned", "Learned"], ["noticed", "Noticed"]].map(([v, l]) => `<option value="${v}" ${MEM.kind === v ? "selected" : ""}>${l}</option>`).join("")}</select><label class="mem-chk"><input type="checkbox" id="memUnused" ${MEM.unused ? "checked" : ""}> Not used in 60 days</label></div></div>`;
}
function memProjectsHTML(d) {
  if (!d.projects.length) return `<div class="mem-empty"><b>No projects yet.</b><span>Each project keeps its own memory.</span></div>`;
  return `<div class="mem-projects">${d.projects.map(p => { const fs = d.facts.filter(f => f.scope === "project:" + p.id);
    return `<section class="mem-proj" data-drop="project:${esc(p.id)}"><h3>${ICON.stack} ${esc(p.name)}</h3><small>${p.instructions ? "Has instructions" : "No instructions"} · ${p.knowledge} file${p.knowledge === 1 ? "" : "s"} · ${fs.length} memor${fs.length === 1 ? "y" : "ies"}</small>${fs.map(f => `<div class="mem-mini" draggable="true" data-id="${esc(f.id)}">${esc(f.text)}</div>`).join("") || `<span class="mem-hint">Drop a memory here to keep it in this project only.</span>`}</section>`; }).join("")}
    <section class="mem-proj" data-drop="everywhere"><h3>${ICON.globe} Everywhere</h3><small>Applies in every chat</small><span class="mem-hint">Drop a memory here to use it everywhere.</span></section></div>`;
}
async function memHistoryHTML() {
  const h = (await api("/api/memory/history")).history;
  if (!h.length) return `<div class="mem-empty"><b>No changes yet.</b><span>Edits show up here.</span></div>`;
  const verb = {added: "Added", edited: "Edited", archived: "Archived", deleted: "Deleted", restored: "Restored", migrated: "Moved from earlier memory", changed: "Changed", reviewed: "Confirmed"};
  return `<ol class="mem-hist">${h.map(r => `<li><span class="when">${esc(memDate(r.ts))}</span><span class="what"><b>${verb[r.action] || r.action}</b> ${esc(r.text || "")}<small>${r.by === "you" ? "You" : "Meshpool"}: ${esc(r.reason || "")}${r.before_text && r.action === "edited" ? ` · was “${esc(r.before_text)}”` : ""}</small></span>${["archived", "deleted", "edited"].includes(r.action) ? `<button class="btn" data-restore="${r.action}|${esc(r.id)}|${esc(r.before_text || "")}">Restore</button>` : ""}</li>`).join("")}</ol>`;
}
function memReviewHTML(d) {
  if (!d.review.length) return `<div class="mem-empty"><b>Nothing to check.</b><span>Old or expired facts show up here.</span></div>`;
  const f = d.review[0];
  return `<div class="mem-rev"><p class="mem-sub">${d.review.length} to check. Y keeps it, N archives.</p><div class="mem-card big" data-id="${esc(f.id)}"><b class="mem-text">${esc(f.text)}</b><span class="mem-src">${f.review_reason === "expired" ? "This expired on " + memDate(f.valid_until) : "No chat has used it in 60 days"} · ${esc(f.source.text)}</span><div class="mem-btns"><button class="btn primary" data-still="${esc(f.id)}">Still true <kbd>Y</kbd></button><button class="btn" data-arch="${esc(f.id)}">No, archive <kbd>N</kbd></button></div></div></div>`;
}
async function pageMemory(redraw) {
  if (!MEM.d) { shell0(`<header class="gh"><h1>${PAGE_TITLE.memory}</h1></header><div class="gskel"><div class="gcard sk"><div class="gm"></div></div></div>`); await memLoad(true); if (!MEM.d) return shell0(`<header class="gh"><h1>${PAGE_TITLE.memory}</h1></header><div class="gempty"><b>Could not load memory.</b><span>${esc(MEM.err || "")}</span></div>`); return; }
  const d = MEM.d, tab = MEM.tab;
  const focusQ = document.activeElement?.id === "memQ", caret = focusQ ? document.activeElement.selectionStart : 0;
  const scrollY = $("#conv")?.scrollTop || 0;
  const tabs = [["memory", "Memory"], ["projects", "Projects"], ["history", "History"], ["review", `Review${d.review.length ? " (" + d.review.length + ")" : ""}`]];
  let body = "";
  if (tab === "memory") body = memPortraitHTML(d) + memConflictsHTML(d) + memTrayHTML(d) + memFiltersHTML(d) + memListHTML(d) + memHealthHTML(d);
  else if (tab === "projects") body = memProjectsHTML(d);
  else if (tab === "history") body = await memHistoryHTML();
  else body = memReviewHTML(d);
  const off = !d.settings.memory_on ? `<div class="gbanner"><div><b>Memory is off</b><span>Nothing is read or saved.</span></div><button class="btn" id="memOn">Turn on</button></div>` : "";
  shell0(`<header class="gh"><h1>${PAGE_TITLE.memory}</h1></header>
    <div class="gbar"><div class="gtabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-mtab="${k}" aria-selected="${tab === k}">${esc(l)}</button>`).join("")}</div><span class="sp"></span>
      <div class="mem-search"><input id="memQ" placeholder="Search memory" aria-label="Search memory" value="${esc(MEM.q)}" autocomplete="off"></div>
      <button class="iconbtn gbtn" id="memExp" data-tip="Export" data-tip-pos="below" aria-label="Export">${ICON.dl}</button><button class="btn primary" id="memAdd">Add memory</button></div>${off}<div class="mem-body">${body}</div>`);
  const q = $("#memQ"); if (focusQ) { q.focus(); q.setSelectionRange(caret, caret); }
  if ($("#conv")) $("#conv").scrollTop = scrollY;
  bindMemory(d);
  if (tab === "memory") {
    memVirtualize(d);
    if (!MEM.portrait && d.counts.active) api("/api/memory/portrait").then(p => { MEM.portrait = p; if (S.view === "page" && S.page === "memory" && MEM.tab === "memory") { const el = $(".mem-portrait"); if (el) el.outerHTML = memPortraitHTML(MEM.d) || "<span></span>"; bindPortrait(); } }).catch(() => {});
    else bindPortrait();
  }
}
function bindPortrait() {
  $$("[data-sent]").forEach(b => b.onclick = () => {
    const ids = (MEM.portrait.sentences[+b.dataset.sent] || {}).ids || [];
    MEM.hl = new Set(ids); $$("[data-sent]").forEach(x => x.classList.toggle("on", x === b && ids.length));
    $$(".mem-row").forEach(r => r.classList.toggle("hl", MEM.hl.has(r.dataset.id)));
    const first = $(".mem-row.hl"); if (first) first.scrollIntoView({block: "center", behavior: "smooth"});
    else if (ids.length) memScrollTo(ids[0]);
  });
}
function memScrollTo(id) { const i = (MEM.d.facts || []).findIndex(f => f.id === id); if (i < 0) return; const box = $("#memList"); if (box?.dataset.virt) { $("#conv").scrollTop = box.offsetTop + i * MEM_ROW_H; } }
function memScopeMenu(e, id) {
  const d = MEM.d, f = d.facts.find(x => x.id === id) || d.pending.find(x => x.id === id);
  ctxMenu(e, [{head: "Where this applies"}, {label: "Everywhere", checked: f.scope === "everywhere" ? true : undefined, fn: () => memAct({action: "scope", id, scope: "everywhere"}, {text: "Moved to Everywhere.", body: () => ({action: "scope", id, scope: f.scope})})},
    ...d.projects.map(p => ({label: p.name, checked: f.scope === "project:" + p.id ? true : undefined, fn: () => memAct({action: "scope", id, scope: "project:" + p.id}, {text: `Moved to ${p.name}.`, body: () => ({action: "scope", id, scope: f.scope})})}))]);
}
async function memExpiry(id) {
  const v = await ask({title: "Forget on", text: "Date as YYYY-MM-DD. Empty removes it.", value: "", ok: "Set"});
  if (v === null) return;
  if (v && isNaN(Date.parse(v))) return toast("Use a date like 2026-12-31.");
  memAct({action: "expiry", id, until: v || null});
}
function memArchive(id) {
  const f = MEM.d.facts.find(x => x.id === id) || (MEM.d.review || []).find(x => x.id === id);
  const arch = f && f.status === "archived";
  memAct({action: arch ? "unarchive" : "archive", id}, {text: arch ? "Restored." : "Archived.", body: () => ({action: arch ? "archive" : "unarchive", id})});
}
function bindMemory(d) {
  const col = $("#col");
  $$("[data-mtab]").forEach(b => b.onclick = () => { MEM.tab = b.dataset.mtab; pageMemory(true); });
  $("#memQ")?.addEventListener("input", e => { MEM.q = e.target.value; clearTimeout(memT); memT = setTimeout(() => memLoad(true), 120); });
  $("#memAdd")?.addEventListener("click", memAddFlow); $("#memAddEmpty")?.addEventListener("click", memAddFlow);
  $("#memOn")?.addEventListener("click", async () => { await post("/api/memory", {action: "settings", memory_on: true}); memLoad(true); });
  $("#memExp")?.addEventListener("click", e => ctxMenu(e, [{label: "Export all", fn: () => memExport("md")}, {label: "How I work (1 page)", fn: () => memExport("onepager")}]));
  $("#memConsolidate")?.addEventListener("click", async () => { const r = await memAct({action: "consolidate"}); toast(`Tidied up: ${r?.archived || 0} archived. They stay in Archived.`); });
  $$("[data-cat]").forEach(b => b.onclick = () => { MEM.cat = b.dataset.cat; memLoad(true); });
  $("#memScope")?.addEventListener("change", e => { MEM.scope = e.target.value; memLoad(true); });
  $("#memKind")?.addEventListener("change", e => { MEM.kind = e.target.value; memLoad(true); });
  $("#memUnused")?.addEventListener("change", e => { MEM.unused = e.target.checked; memLoad(true); });
  col.onclick = e => {
    const t = e.target.closest("button, .mem-text[data-edit]"); if (!t) return;
    const D = t.dataset;
    if (D.confirm) return memAct({action: "confirm", id: D.confirm}, {text: "Added to memory.", body: () => ({action: "archive", id: D.confirm})});
    if (D.reject) return memAct({action: "reject", id: D.reject});
    if (D.editpend) { const f = d.pending.find(x => x.id === D.editpend); return ask({title: "Edit this memory", value: f.text, ok: "Save and add"}).then(v => v && memAct({action: "confirm_edit", id: f.id, text: v})); }
    if (D.win) return memAct({action: "resolve", winner: D.win, loser: D.lose}, {text: "Kept one, archived the other.", body: () => ({action: "unarchive", id: D.lose})});
    if (D.keepboth) { const [a, b] = D.keepboth.split("|"); return memAct({action: "keep_both", a, b}); }
    if (D.edit) { MEM.editing = D.edit; MEM.sel = D.edit; return pageMemory(true).then(() => { const i = $(".mem-edit"); i?.focus(); i?.select(); }); }
    if (D.pin) { const f = d.facts.find(x => x.id === D.pin); return memAct({action: "pin", id: D.pin, on: !f.pinned}); }
    if (D.expiry) return memExpiry(D.expiry);
    if (D.arch) return memArchive(D.arch);
    if (D.del) { const f = d.facts.find(x => x.id === D.del); return memAct({action: "delete", id: D.del}, {text: "Deleted.", body: () => ({action: "undelete", id: D.del})}); }
    if (D.scope) return memScopeMenu({type: "click", currentTarget: t}, D.scope);
    if (D.chat) return openThread?.(D.chat);
    if (D.still) return memAct({action: "still_true", id: D.still});
    if (D.restore) { const [act, id, text] = D.restore.split("|"); return memAct(act === "edited" ? {action: "restore_text", id, text} : {action: "unarchive", id}).then(() => act === "deleted" && post("/api/memory", {action: "undelete", id}).then(() => memLoad(true))); }
  };
  col.onkeydown = e => {
    if (e.target.classList.contains("mem-edit")) {
      if (e.key === "Enter") { e.preventDefault(); const id = e.target.closest(".mem-row").dataset.id, v = e.target.value.trim(); MEM.editing = ""; const old = d.facts.find(x => x.id === id).text; return v && v !== old ? memAct({action: "edit", id, text: v}, {text: "Saved.", body: () => ({action: "edit", id, text: old})}) : pageMemory(true); }
      if (e.key === "Escape") { MEM.editing = ""; return pageMemory(true); }
      return;
    }
    if (e.target.matches("input, select, textarea")) { if (e.key === "Escape") e.target.blur(); return; }
    if (MEM.tab === "review" && !e.metaKey && !e.ctrlKey) { const f = d.review[0]; if (f && e.key.toLowerCase() === "y") return memAct({action: "still_true", id: f.id}); if (f && e.key.toLowerCase() === "n") return memArchive(f.id); }
    const row = e.target.closest?.(".mem-row"); if (!row) { if (e.key === "/") { e.preventDefault(); $("#memQ")?.focus(); } return; }
    const rows = $$(".mem-row"), i = rows.indexOf(row), id = row.dataset.id;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = rows[i + (e.key === "ArrowDown" ? 1 : -1)]; if (n) { MEM.sel = n.dataset.id; rows.forEach(r => r.tabIndex = -1); n.tabIndex = 0; n.focus(); } }
    else if (e.key === "Enter") { e.preventDefault(); MEM.editing = id; pageMemory(true).then(() => { const x = $(".mem-edit"); x?.focus(); x?.select(); }); }
    else if (e.key.toLowerCase() === "p") { const f = d.facts.find(x => x.id === id); memAct({action: "pin", id, on: !f.pinned}); }
    else if (e.key === "Backspace" || e.key === "Delete") { e.preventDefault(); const n = rows[i + 1] || rows[i - 1]; if (n) MEM.sel = n.dataset.id; memArchive(id); }
  };
  col.ondragstart = e => { const r = e.target.closest("[data-id]"); if (r) { MEM.drag = r.dataset.id; e.dataTransfer.setData("text/plain", MEM.drag); e.dataTransfer.effectAllowed = "move"; } };
  col.ondragover = e => { const z = e.target.closest("[data-drop]"); if (z && MEM.drag) { e.preventDefault(); z.classList.add("over"); } };
  col.ondragleave = e => e.target.closest?.("[data-drop]")?.classList.remove("over");
  col.ondrop = e => { const z = e.target.closest("[data-drop]"); if (!z || !MEM.drag) return; e.preventDefault(); const id = MEM.drag, was = d.facts.find(x => x.id === id)?.scope; MEM.drag = ""; memAct({action: "scope", id, scope: z.dataset.drop}, {text: "Moved.", body: () => ({action: "scope", id, scope: was})}); };
}
async function memAddFlow() {
  const v = await ask({title: "Add a memory", text: "For example: Prefers sentence case.", value: "", ok: "Add"});
  if (v) memAct({action: "add", text: v});
}
async function memExport(kind) {
  const r = await fetch("/api/memory/export?type=" + kind, {headers: {"X-Meshpool-Token": MP.token}});
  const blob = await r.blob(), a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = kind === "md" ? "what-meshpool-knows.md" : "how-i-work.md"; document.body.appendChild(a); a.click(); a.remove();
}

/* "Your work" page: the module is yourwork.js; this is the shell glue. */
window.pageYourWork = function () {
  shell0(`<div id="mwBox"></div>`);
  if (window.MeshYourWork) window.MeshYourWork.render($("#mwBox"));
};
/* A suggestion on the "Your work" page (or a card) acts here: the same actions the insight cards use. */
document.addEventListener("meshpool:work-action", e => { const a = e.detail && e.detail.action; if (a && window.runInsightAction) window.runInsightAction(a, e.detail); });
