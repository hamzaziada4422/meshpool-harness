/* 0.0.10 in the chat: the memory sources chip under a reply, the "What the agent will see" X-ray, incognito chats,
   visible quality checks, the per-task receipt, next-step chips and the one insight card at turn end. Loaded after
   app.js and composer.js; it wraps applyEv and renderTop and adds two buttons to the composer bar.
   Everything here draws after a turn ends or on a click, never while a turn is running. */
"use strict";

/* ---------- composer: X-ray and incognito ---------- */
const inc010 = () => !!(S.cur ? S.cur.view?.incognito : S.draft.incognito);
window.cbar010 = function () {
  const xr = `<button class="pill icon" id="xrayBtn" data-tip="What the agent will see" data-tip-pos="above" aria-haspopup="dialog" aria-label="What the agent will see">${ICON.eye}</button>`;
  const ic = S.cur
    ? (inc010() ? `<span class="pill incog-pill" data-tip="Incognito: nothing from this chat is remembered" data-tip-pos="above">${ICON.lock}<span class="lbl">Incognito</span></span>` : "")
    : `<button class="pill icon ${S.draft.incognito ? "on" : ""}" id="incogBtn" data-tip="${S.draft.incognito ? "Incognito is on" : "Incognito chat"}" data-kbd="${MOD}⇧N" data-tip-pos="above" aria-pressed="${!!S.draft.incognito}" aria-label="Incognito chat">${ICON.lock}${S.draft.incognito ? `<span class="lbl">Incognito</span>` : ""}</button>`;
  return xr + ic;
};
window.cbar010bind = function () {
  $("#xrayBtn")?.addEventListener("click", e => xray010(e.currentTarget));
  $("#incogBtn")?.addEventListener("click", () => setIncognito010(!S.draft.incognito));
};
function setIncognito010(on) {
  if (S.cur) return;
  S.draft.incognito = on; renderBar(); renderTop(); renderHero?.();
  toast(on ? "Incognito: nothing is remembered." : "Incognito is off.");
}
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "n") { e.preventDefault(); newThread(); setIncognito010(true); }
}, true);
const _renderTop010 = renderTop;
renderTop = function () {
  _renderTop010.apply(this, arguments);
  const tb = $("#topbar"); if (!tb) return;
  tb.querySelector(".incog-tag")?.remove();
  if (inc010() && S.view !== "page" && S.view !== "project") { const t = document.createElement("span"); t.className = "incog-tag"; t.innerHTML = `${ICON.lock}Incognito`; t.title = "No memory is read or saved, and nothing is logged for suggestions or summaries."; tb.insertBefore(t, tb.querySelector(".sp")); }
};

/* ---------- X-ray ---------- */
function popAt(anchor, html, cls = "") {
  $(".mem-pop")?.remove();
  const p = document.createElement("div"); p.className = "mem-pop " + cls; p.setAttribute("role", "dialog"); p.innerHTML = html; document.body.appendChild(p);
  const r = anchor.getBoundingClientRect();
  p.style.left = Math.max(8, Math.min(innerWidth - p.offsetWidth - 8, r.left)) + "px";
  const below = r.bottom + p.offsetHeight + 8 < innerHeight;
  p.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - p.offsetHeight - 6)) + "px";
  const off = ev => { if (!p.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) close(); };
  const key = ev => { if (ev.key === "Escape") close(); };
  const close = () => { p.remove(); document.removeEventListener("mousedown", off, true); document.removeEventListener("keydown", key, true); };
  setTimeout(() => { document.addEventListener("mousedown", off, true); document.addEventListener("keydown", key, true); }, 0);
  p.close = close; return p;
}
async function xray010(anchor) {
  const q = new URLSearchParams({chat: S.cur?.id || "", text: $("#input").value || "", project: S.cur?.view?.project_id || S.draft.pid || "", workdir: S.cur ? "" : (S.draft.project || ""), incognito: inc010() ? "1" : ""});
  let x; try { x = await api("/api/xray?" + q); } catch (e) { return toast(e.message); }
  const exclude = new Set([...(x.excluded || []).map(f => f.id), ...(S.cur ? [] : (S.draft.exclude || []))]);
  const draw = () => `<h4>What the agent sees</h4><p class="note">${esc(x.note)} ${x.incognito ? "Incognito: no memory." : x.memory_on ? "" : "Memory is off."}</p>
    ${x.sections.map(sec => `<div class="sec2">${esc(sec.title)}</div>${sec.facts.length ? sec.facts.map(f => `<label class="it"><span><input type="checkbox" data-leave="${esc(f.id)}" ${exclude.has(f.id) ? "" : "checked"}> ${esc(f.text)}<small>${esc(f.scope_label)}</small></span></label>`).join("") : `<details><summary class="note">Show</summary><pre>${esc(sec.text.length > 500 ? sec.text.slice(0, 500) + "…" : sec.text)}</pre></details>`}`).join("")}
    ${(x.excluded || []).length ? `<div class="sec2">Left out of this chat</div>${x.excluded.map(f => `<label class="it"><span><input type="checkbox" data-leave="${esc(f.id)}" ${exclude.has(f.id) ? "" : "checked"}> ${esc(f.text)}</span></label>`).join("")}` : ""}
    <p class="note" style="margin-top:10px">Untick to leave a fact out of this chat. <button class="linkbtn" id="xrMem">Open memory</button></p>`;
  const p = popAt(anchor, draw(), "xray");
  const bind = () => {
    $$("[data-leave]", p).forEach(c => c.onchange = async () => {
      c.checked ? exclude.delete(c.dataset.leave) : exclude.add(c.dataset.leave);
      if (S.cur) await post("/api/memory/exclude", {chat: S.cur.id, ids: [...exclude]}); else S.draft.exclude = [...exclude];
    });
    $("#xrMem", p)?.addEventListener("click", () => { p.close(); goPage("memory"); });
  };
  bind();
}

/* ---------- the independent check under a big task's reply: one line, the requirements open on click ---------- */
const REV_WORD = {met: "Met", not_met: "Not done", unverified: "Could not check"};
window.reviewCard010 = function (ev, add) {
  const v = ev.verdict || "error", items = ev.items || [], open = items.filter(i => i.status !== "met").length;
  const acc = {accept: "The reviewer would accept this as it is.", accept_with_notes: "The reviewer would accept this, with notes.", would_send_back: "The reviewer would send this back."}[ev.acceptance] || "";
  const meta = ev.secs ? `${Math.round(ev.secs)} s · ${(((ev.tokens?.in || 0) + (ev.tokens?.out || 0)) / 1000).toFixed(1)}k tokens` : "";
  const head = esc(String(ev.text || "Independent check").split("\n")[0]);
  const rows = items.map(i => `<li class="${esc(i.status)}"><span class="rv-s">${REV_WORD[i.status] || esc(i.status)}</span> ${esc(i.requirement)}${i.evidence ? `<small>${esc(i.evidence)}</small>` : ""}</li>`).join("");
  const why = (ev.acceptance_reasons || []).map(r => `<p class="rv-why">${esc(r)}</p>`).join("");
  const fixBtn = !ev.rework && v !== "pass" && v !== "error" && open ? `<button class="btn sm" data-rv="fix">Ask the agent to fix these</button>` : "";
  const note = ev.rework ? `<p class="rv-why">Sending these back to the agent to fix.</p>` : v === "error" ? `<p class="rv-why">Nothing was blocked. This work was not independently checked.</p>` : "";
  const el = add(`<details class="rev010 rv-${esc(v)}"><summary>${v === "pass" ? ICON.check2 : ICON.warn}<span class="rv-t">${head}</span>${meta ? `<span class="rv-m">${esc(meta)}</span>` : ""}</summary>
    <div class="rv-body">${acc ? `<p class="rv-acc">${esc(acc)}</p>` : ""}${why}${note}${rows ? `<ul class="rv-list" aria-label="Requirements">${rows}</ul>` : ""}${fixBtn}</div></details>`, "note review");
  const b = el?.querySelector("[data-rv=fix]");
  if (b) b.onclick = () => {
    const todo = items.filter(i => i.status !== "met").map(i => `- ${i.requirement}${i.evidence ? " (" + i.evidence + ")" : ""}`).join("\n");
    const fixes = (ev.fix || []).map(f => `- ${f}`).join("\n");
    $("#input").value = "[Independent review] These are not done:\n" + todo + (fixes ? "\n\nHow to fix:\n" + fixes : "") + "\n\nFix them, check your fixes yourself, then finish.";
    sendNow(false);
  };
  return el;
};
async function checkThis010(btn) {
  if (!S.cur || S.cur.busy) return toast("Wait for it to finish, or stop it first.");
  btn.disabled = true; toast("A separate reviewer is checking this...", 4000);
  try { await post(`/api/agent/${S.cur.id}/grade`, {}); clearTimeout(S.cur.pollT); poll(); } catch (e) { toast(e.message); }
  btn.disabled = false;
}

/* ---------- events after a turn ---------- */
const seen010 = new Set();
function foot010() { return S.cur?.lastFoot || null; }
function under010(html, cls) {
  const f = foot010(); if (!f) return null;
  let box = f.parentElement?.querySelector(`:scope > .after010[data-for="${f.dataset.f010 || (f.dataset.f010 = String(Math.random()).slice(2))}"]`);
  if (!box) { box = document.createElement("div"); box.className = "after010"; box.dataset.for = f.dataset.f010; f.after(box); }
  const d = document.createElement("div"); d.className = cls; d.innerHTML = html; box.appendChild(d); return d;
}
function ev010(ev) {
  if (!S.cur) return;
  if (ev.type === "user" && !ev.steer) { $$(".after010 .chips010").forEach(c => c.remove()); return; }   // next-step chips expire on the next message
  if (ev.type === "check") {
    const g = workGroup(); const r = $(".rows", g); if (!r) return;
    const d = document.createElement("div"); d.className = "chk010 " + (ev.ok ? "ok" : "bad"); d.innerHTML = `${ev.ok ? ICON.check : ICON.warn}<span>${esc(ev.text)}</span>`; r.appendChild(d); return;
  }
  if (ev.type === "checks_line") {
    under010(`<details class="chkline"><summary>${ICON.check2}<span>${esc(ev.text)}</span></summary><ul>${(ev.items || []).map(i => `<li>${esc(i.text || i.label || i)}</li>`).join("")}</ul></details>`, "chkwrap"); return;
  }
  if (ev.type === "task_receipt") { under010(`<span title="${esc(ev.reason || "")}">${esc(ev.text || "")}</span>`, "rcpt010"); return; }
  if (ev.type === "insight" && ev.card) {
    insightUnder010(ev.card);
    const ns = Object.assign({suggest: false}, store("notify") || {});
    if (ev.card.type !== "followup" && ns.suggest && document.hidden) {        // only if the user allowed it, and Meshpool is not in view
      post("/api/insights/os", {}).then(r => { if (r && r.card) post("/api/notify", {title: "Meshpool", body: r.card.title}).catch(() => {}); }).catch(() => {});
    }
    return;
  }
  if (ev.type === "turn_end") sources010();
}
function insightUnder010(c) {
  if (c.type === "followup") {            // one card per chip: they share one row under the reply
    const f = foot010(); if (!f) return;
    const box = under010("", "chips010 schips"); if (!box) return;
    const row = box.parentElement.querySelector(".chips010") || box;
    const b = document.createElement("button"); b.className = "schip"; b.textContent = c.action?.label || c.title;
    b.onclick = () => { post("/api/insights/act", {id: c.id, event: "accepted"}).catch(() => {}); $("#input").value = c.action?.payload?.text || b.textContent; sendNow(false); };
    row.appendChild(b); post("/api/insights/act", {id: c.id, event: "shown"}).catch(() => {});
    return;
  }
  insightCard010(c);
}
/* A suggestion is one quiet line above the composer: what it is, one button, a way to dismiss. One at a time. */
const nudgeQ010 = [];
function insightCard010(c) {
  if (nudgeQ010.some(x => x.id === c.id) || $(`.nudge010[data-id="${c.id}"]`)) return;
  nudgeQ010.push(c); nextNudge010();
}
function nextNudge010() {
  if ($(".nudge010") || !nudgeQ010.length) return;
  if (S.cur && S.cur.busy) { clearTimeout(nextNudge010.t); nextNudge010.t = setTimeout(nextNudge010, 1500); return; }   // never while a turn runs
  const wrap = $(".composer-wrap"); if (!wrap) return;
  const c = nudgeQ010.shift();
  const el = document.createElement("div"); el.className = "nudge010"; el.dataset.id = c.id; el.setAttribute("role", "status");
  el.innerHTML = `<span class="n-t" title="${esc(c.body || "")}"><b>${esc(c.title)}</b> <span class="n-b">${esc(c.body || "")}</span></span><button class="btn primary sm" data-ic="accepted">${esc(c.action?.label || "Do it")}</button><button class="iconbtn" data-ic="more" aria-label="More" aria-haspopup="menu">${ICON.more}</button><button class="iconbtn" data-ic="dismissed" aria-label="Dismiss">${ICON.x}</button>`;
  wrap.appendChild(el);
  post("/api/insights/act", {id: c.id, event: "shown"}).catch(() => {});
  const done = () => { el.remove(); setTimeout(nextNudge010, 400); };
  el.addEventListener("click", async e => {
    const b = e.target.closest("[data-ic]"); if (!b) return;
    const event = b.dataset.ic;
    if (event === "more") {
      return ctxMenu({type: "click", currentTarget: b}, [
        {label: "Why this?", fn: () => whyPop010(b, c)},
        {label: "Not now", fn: async () => { done(); await post("/api/insights/act", {id: c.id, event: "dismissed"}).catch(() => {}); }},
        {label: "Show me less like this", fn: async () => { done(); const r = await post("/api/insights/act", {id: c.id, event: "muted"}).catch(() => null); toast("Okay, fewer like this.", 7000, {label: "Undo", fn: () => post("/api/insights/settings", {action: "undo_mute", id: r?.mute?.id}).catch(() => {})}); }}]);
    }
    done();
    let r = null;
    try { r = await post("/api/insights/act", {id: c.id, event}); } catch (err) { /* the card is gone either way */ }
    if (event === "accepted") runInsightAction(r?.action || c.action, {card: c});
  });
}
function whyPop010(anchor, c) {
  const ev = c.evidence || [];
  const p = popAt(anchor, `<h4>${esc(c.title)}</h4><p class="note">${esc(c.body || "")}</p>${ev.map(x => `<div class="it"><span>${esc(x.text)}</span>${x.chat_id ? `<button class="linkbtn" data-ichat="${esc(x.chat_id)}">Open</button>` : ""}</div>`).join("")}`, "why");
  p.onclick = e => { const b = e.target.closest("[data-ichat]"); if (b) { p.close(); openThread(b.dataset.ichat); } };
}
window.insightCard010 = insightCard010;

/* What clicking a card's action does. Each one opens a prefilled form or a draft first; nothing changes until the
   person confirms, and every result has an Undo or a way back. */
window.runInsightAction = async function (a, ctx) {
  if (!a) return;
  const p = a.payload || {};
  const kind = {draft_skill: "skill", make_project: "project", new_chat: "rerun", set_default: "route", teach_accept: "teach"}[a.kind] || a.kind;
  if (kind === "schedule") {                     // the form opens prefilled; nothing is saved until Create task
    const v = await taskDialog({name: p.name || "", prompt: p.prompt || "", when: p.when || "daily", time: p.time || "09:00", day: p.day || "mon", minutes: p.minutes || 60, only_on_change: p.only_on_change !== false});
    if (v && await taskSave(v)) { toast("Task created.", 8000, {label: "Open", fn: () => goPage("scheduled")}); }
    return;
  }
  if (kind === "project") {
    const r = await post("/api/pj", {name: p.name || "New project", description: "", instructions: p.instructions || ""}).catch(e => toast(e.message));
    if (r?.id) {
      S.pj = (await api("/api/pj")).projects; renderSide();
      toast(`Project "${r.name}" is ready.${(p.files || []).length ? " Add its files from the project page." : ""}`, 9000, {label: "Open", fn: () => openProject(r.id)});
    }
    return;
  }
  if (kind === "connect") return openSettings("connectors");
  if (kind === "remember") return toast("Added to \"Meshpool thinks\" on the memory page. Nothing is remembered until you say yes.", 9000, {label: "Review", fn: () => goPage("memory")});
  if (kind === "rerun") { newThread(); $("#input").value = p.prompt || ""; $("#input").focus(); if ((p.files || []).length) toast("Attach the new file with +."); return; }
  if (kind === "send") { $("#input").value = p.text || ""; return sendNow(false); }
  if (kind === "route") return openSettings("models");
  if (kind === "skill") {
    let d; try { d = await post("/api/insights/draft-skill", p); } catch (e) { return toast(e.message); }
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    sc.innerHTML = `<div class="dlg" role="dialog" aria-modal="true" aria-label="Review the skill" style="width:min(640px,94vw)"><h3>Review this skill</h3><p>Drafted from ${d.evidence.length} of your chats. Edit it, then save. It is used only when its description matches.</p><input id="skN" value="${esc(d.name)}" aria-label="Skill name"><textarea id="skB" rows="12" style="width:100%;margin-top:8px">${esc(d.body)}</textarea><div class="row"><button class="btn" data-a="0">Cancel</button><button class="btn primary" data-a="1">Save skill</button></div></div>`;
    document.body.appendChild(sc);
    sc.addEventListener("click", async e => { const b = e.target.closest("[data-a]"); if (e.target === sc || b?.dataset.a === "0") return sc.remove(); if (!b) return;
      try { await post("/api/skills", {action: "create", name: $("#skN", sc).value, description: d.description, body: $("#skB", sc).value}); sc.remove(); toast("Skill saved.", 8000, {label: "Open skills", fn: () => openSettings("skills")}); } catch (err) { toast(err.message); } });
    return;
  }
  if (kind === "teach") {
    const r = await post("/api/insights/accept", {action: a, card: ctx?.card}).catch(e => toast(e.message));
    if (r) toast("Saved as your defaults. See them in memory.", 8000, {label: "Open memory", fn: () => goPage("memory")});
    return;
  }
  toast("Done.");
};

/* ---------- the memory sources chip ---------- */
const srcCache010 = {};
async function sources010() {
  const id = S.cur?.id, foot = foot010(); if (!id || !foot || foot.dataset.src010) return;
  foot.dataset.src010 = "1";
  let r = srcCache010[id];
  if (!r || Date.now() - r.t > 5000) { try { r = srcCache010[id] = {t: Date.now(), d: await api("/api/memory/sources?chat=" + encodeURIComponent(id))}; } catch (e) { return; } }
  const n = r.d.facts.length; if (!n) return;
  const b = document.createElement("button"); b.className = "mem-srcchip"; b.innerHTML = `${ICON.brain}${n} memor${n === 1 ? "y" : "ies"}`; b.setAttribute("aria-haspopup", "dialog");
  b.title = r.d.wording; foot.appendChild(b);
  b.onclick = () => srcPop010(b, id);
}
async function srcPop010(anchor, id) {
  let d; try { d = await api("/api/memory/sources?chat=" + encodeURIComponent(id)); } catch (e) { return toast(e.message); }
  const draw = () => `<h4>${d.facts.length} memor${d.facts.length === 1 ? "y" : "ies"} given</h4><p class="note">${esc(d.wording)}</p>${d.facts.map(f => `<div class="it" data-id="${esc(f.id)}"><span>${esc(f.text)}<small>${esc(f.scope_label)} · ${esc(f.source.text)}</small></span><span class="mem-btns"><button class="linkbtn" data-sedit="${esc(f.id)}">Edit</button><button class="linkbtn" data-swrong="${esc(f.id)}">Wrong</button></span></div>`).join("")}<p class="note" style="margin-top:8px"><button class="linkbtn" data-sopen="1">Open memory</button></p>`;
  const p = popAt(anchor, draw(), "src");
  p.onclick = async e => {
    const t = e.target.closest("button"); if (!t) return; const D = t.dataset;
    if (D.sopen) { p.close(); return goPage("memory"); }
    if (D.sedit) { const f = d.facts.find(x => x.id === D.sedit); p.close(); const v = await ask({title: "Edit this memory", value: f.text, ok: "Save"}); if (v) { await post("/api/memory", {action: "edit", id: f.id, text: v}); toast("Saved."); } return; }
    if (D.swrong) { await post("/api/memory", {action: "wrong", id: D.swrong}); toast("Archived.", 7000, {label: "Undo", fn: () => post("/api/memory", {action: "unarchive", id: D.swrong})}); p.close(); delete srcCache010[id]; }
  };
}

/* ---------- wrap the event handler ---------- */
const _applyEv010 = applyEv;
applyEv = function (ev) { const r = _applyEv010.apply(this, arguments); try { ev010(ev); } catch (e) { /* never break the transcript */ } return r; };

/* "Noted for next time" gets an Undo: the status event carries the fact ids. */
const _status010 = new MutationObserver(() => {
  $$(".note:not([data-u010])").forEach(n => {
    if (!/^Noted for next time/.test(n.textContent)) return;
    n.dataset.u010 = "1";
    const ids = n.dataset.undo ? JSON.parse(n.dataset.undo) : null; if (!ids) return;
    const b = document.createElement("button"); b.className = "linkbtn"; b.textContent = "Undo"; b.style.marginLeft = "8px";
    b.onclick = async () => { await post("/api/memory", {action: "undo_noted", ids}); b.remove(); n.textContent += " (undone)"; };
    n.appendChild(b);
  });
});
window.addEventListener("load", () => { const c = $("#col"); if (c) _status010.observe(c, {childList: true, subtree: true}); });

/* ---------- one-line chat summaries on sidebar hover ---------- */
window.SUMM010 = {};
async function loadSummaries010() {
  try { SUMM010 = (await api("/api/summaries/chats")).summaries || {}; } catch (e) { return; }
  document.querySelectorAll(".th[data-id]").forEach(li => {
    const sm = SUMM010[li.dataset.id];
    if (sm && !li.dataset.sm) { li.dataset.sm = "1"; li.title = (li.title ? li.title + "\n" : "") + sm; }
  });
}
window.addEventListener("load", () => setTimeout(loadSummaries010, 2500));
setInterval(() => { if (document.visibilityState === "visible") loadSummaries010(); }, 60000);

/* ---------- at chat open: at most one quiet suggestion for this chat ---------- */
const _openThread010 = openThread;
openThread = function (id, fresh) {
  const r = _openThread010.apply(this, arguments);
  if (!fresh) setTimeout(async () => {
    try {
      const d = await api("/api/insights?where=chat:" + encodeURIComponent(id));
      const c = (d.cards || []).find(x => x.type !== "followup");
      if (c && S.cur && S.cur.id === id) insightCard010(c);
    } catch (e) { /* no suggestion is fine */ }
  }, 1500);
  return r;
};


/* ---------- under every reply: copy, try again, thumbs. Edit on your own message rewinds to that point. ---------- */
const THUMB = d => `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"/></svg>`;
const UP = THUMB("M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5.5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 17.3 20H7");
const DOWN = THUMB("M7 13V4H4v9zM7 13l4 8a2 2 0 0 0 2-2v-4h5.5a2 2 0 0 0 2-2.3l-1.2-6A2 2 0 0 0 17.3 4H7");
function feedback010(outcome) { if (S.cur) post("/api/receipt/feedback", {chat: S.cur.id, outcome}).catch(() => {}); }
function acts010(n, text) {
  const f = foot010(); if (!f || f.dataset.acts010) return; f.dataset.acts010 = "1";
  const w = document.createElement("span"); w.className = "racts010";
  w.innerHTML = `<button class="iconbtn" data-ra="copy" data-tip="Copy reply" data-tip-pos="above" aria-label="Copy reply">${ICON.copy}</button><button class="iconbtn" data-ra="check" data-tip="Check this" data-tip-pos="above" aria-label="Check this: have an independent reviewer check the result">${ICON.check2}</button><button class="iconbtn" data-ra="retry" data-tip="Try again" data-tip-pos="above" aria-label="Try again">${ICON.refresh}</button><button class="iconbtn" data-ra="up" data-tip="Good reply" data-tip-pos="above" aria-label="Good reply" aria-pressed="false">${UP}</button><button class="iconbtn" data-ra="down" data-tip="Not good" data-tip-pos="above" aria-label="Not good" aria-pressed="false">${DOWN}</button>`;
  f.appendChild(w);
  w.onclick = async e => {
    const b = e.target.closest("[data-ra]"); if (!b) return;
    if (b.dataset.ra === "copy") return navigator.clipboard.writeText(text || "").then(() => toast("Copied"), () => toast("Could not copy."));
    if (b.dataset.ra === "retry") return retry010(n);
    if (b.dataset.ra === "check") return checkThis010(b);
    const on = b.getAttribute("aria-pressed") !== "true";
    $$("[data-ra=up],[data-ra=down]", w).forEach(x => { x.setAttribute("aria-pressed", "false"); x.classList.remove("on"); });
    if (on) { b.setAttribute("aria-pressed", "true"); b.classList.add("on"); feedback010(b.dataset.ra); toast(b.dataset.ra === "up" ? "Thanks." : "Thanks. Meshpool will use agents like this less for this kind of task.", 3500); }
  };
}
async function rewind010(n) {
  await post(`/api/agent/${S.cur.id}/revert`, {turn: n, since: 1e9});
  renderBranch(); clearTimeout(S.cur.pollT); resetChat(S.cur); await poll(); if (S.panel === "diff") renderPanel();
}
async function retry010(n) {
  const asked = threadLog().filter(e => e.type === "user" && !e.steer)[n - 1]?.text || "";
  if (!S.cur || S.cur.busy) return toast("Wait for it to finish, or stop it first.");
  if (!asked) return toast("Could not find the message to send again.");
  try { await rewind010(n); feedback010("retry"); $("#input").value = asked; sendNow(false); } catch (e) { toast(e.message); }
}
document.addEventListener("click", async e => {
  const b = e.target.closest('[data-ma="edit"]'); if (!b || !S.cur) return;
  const msgs = $$(".msg-user").filter(m => $(".macts", m)), mine = b.closest(".msg-user"), n = msgs.indexOf(mine) + 1;
  const text = $(".b", mine)?.innerText.replace(/^Plan mode\n?/, "").trim() || "";
  e.stopImmediatePropagation();
  const fill = () => { const i = $("#input"); i.value = text; i.focus(); i.dispatchEvent(new Event("input")); };
  if (S.cur.busy || n < 1) { fill(); return toast("Edit it and send to ask again.", 3500); }
  if (n < msgs.length && !await ask({title: "Edit this message?", text: "The replies after it are removed, and files Meshpool changed after it go back.", ok: "Edit it", danger: true})) return;
  try { await rewind010(n); fill(); toast("Edit it and send. The old reply is gone.", 4000); } catch (err) { toast(err.message); }
}, true);
const _ev010b = ev010;
ev010 = function (ev) { _ev010b(ev); if (ev.type === "turn_end" && S.cur) acts010(S.cur.turn, S.cur.lastText); };


/* ---------- diagrams and maths in chat: Mermaid and KaTeX are bundled and load only when a reply uses them ---------- */
const _load010 = {};
function script010(src) { return _load010[src] || (_load010[src] = new Promise((ok, no) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => no(new Error("could not load " + src)); document.head.appendChild(s); })); }
const unb64 = b => decodeURIComponent(escape(atob(b)));
async function rich010(node) {
  const boxes = $$(".mermaid-box:not([data-done])", node), maths = $$(".mathsrc:not([data-done])", node);
  if (boxes.length) {
    try {
      await script010("/ui/vendor/mermaid.min.js");
      if (!window._mm010) { window.mermaid.initialize({startOnLoad: false, securityLevel: "strict", theme: (matchMedia("(prefers-color-scheme: dark)").matches || document.documentElement.dataset.theme === "dark") ? "dark" : "default"}); window._mm010 = 1; }
      for (const b of boxes) {
        b.dataset.done = "1";
        try { const r = await window.mermaid.render("mm" + Math.random().toString(36).slice(2), b.dataset.mermaid); b.innerHTML = r.svg; b.classList.add("rendered"); }
        catch (e) { b.insertAdjacentHTML("beforeend", `<small class="mm-err">This diagram could not be drawn, so its source is shown.</small>`); $$("[id^=dmm]").forEach(x => x.parentNode && x.parentNode.removeChild(x)); }
      }
    } catch (e) { /* the source stays visible */ }
  }
  if (maths.length) {
    try {
      if (!$("link[href*=katex]")) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/ui/vendor/katex.min.css"; document.head.appendChild(l); }
      await script010("/ui/vendor/katex.min.js");
      for (const m of maths) {
        m.dataset.done = "1";
        const tex = unb64(m.dataset.tex);
        try { window.katex.render(tex, m, {displayMode: m.dataset.display === "1", throwOnError: true, trust: false}); } catch (e) { m.textContent = tex; }
      }
    } catch (e) { maths.forEach(m => { if (!m.dataset.done) m.textContent = unb64(m.dataset.tex); }); }
  }
}
const _mdRender010 = mdRender;
mdRender = function (node, text) { const r = _mdRender010.apply(this, arguments); rich010(node); return r; };
