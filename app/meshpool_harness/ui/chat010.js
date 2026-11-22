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
  toast(on ? "Incognito chat. Nothing in it is remembered, learned or logged." : "Incognito is off.");
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
  const draw = () => `<h4>What the agent will see</h4><p class="note">${esc(x.note)} ${x.incognito ? "This chat is incognito, so no memory is included." : x.memory_on ? "" : "Memory is off, so none is included."}</p>
    ${x.sections.map(sec => `<div class="sec2">${esc(sec.title)}</div>${sec.facts.length ? sec.facts.map(f => `<label class="it"><span><input type="checkbox" data-leave="${esc(f.id)}" ${exclude.has(f.id) ? "" : "checked"}> ${esc(f.text)}<small>${esc(f.scope_label)}</small></span></label>`).join("") : `<pre>${esc(sec.text.length > 500 ? sec.text.slice(0, 500) + "…" : sec.text)}</pre>`}`).join("")}
    ${(x.excluded || []).length ? `<div class="sec2">Left out of this chat</div>${x.excluded.map(f => `<label class="it"><span><input type="checkbox" data-leave="${esc(f.id)}" ${exclude.has(f.id) ? "" : "checked"}> ${esc(f.text)}</span></label>`).join("")}` : ""}
    <p class="note" style="margin-top:10px">${x.chars.toLocaleString()} characters of background. Untick a fact to leave it out of this chat only. <button class="linkbtn" id="xrMem">Open memory</button></p>`;
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
  if (ev.type === "insight" && ev.card) { insightUnder010(ev.card); return; }
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
  insightCard010(c, under010);
}
function insightCard010(c, place) {
  const ev = (c.evidence || []).slice(0, 4);
  const html = `<div class="ic010-t"><b>${esc(c.title)}</b><button class="iconbtn" data-ic="dismissed" aria-label="Dismiss">${ICON.x}</button></div><p>${esc(c.body)}</p>
    ${ev.length ? `<ul class="ic010-ev">${ev.map(x => `<li>${esc(x.text)}${x.chat_id ? ` <button class="linkbtn" data-ichat="${esc(x.chat_id)}">Open</button>` : ""}</li>`).join("")}</ul>` : ""}
    <div class="ic010-a"><button class="btn primary" data-ic="accepted">${esc(c.action?.label || "Do it")}</button><button class="btn" data-ic="dismissed">Not now</button><button class="linkbtn" data-ic="muted">Show me less like this</button></div>`;
  const el = place(html, "ic010 card"); if (!el) return;
  post("/api/insights/act", {id: c.id, event: "shown"}).catch(() => {});
  el.addEventListener("click", async e => {
    const ch = e.target.closest("[data-ichat]"); if (ch) return openThread(ch.dataset.ichat);
    const b = e.target.closest("[data-ic]"); if (!b) return;
    const event = b.dataset.ic;
    el.remove();
    let r = null;
    try { r = await post("/api/insights/act", {id: c.id, event}); } catch (err) { /* the card is gone either way */ }
    if (event === "accepted") runInsightAction(r?.action || c.action, {card: c});
    if (event === "muted") toast("Okay, fewer suggestions like this.", 7000, {label: "Undo", fn: () => post("/api/insights/settings", {action: "undo_mute", id: r?.mute?.id}).catch(() => {})});
  });
}
window.insightCard010 = insightCard010;

/* What clicking a card's action does. Each one opens a prefilled form or a draft first; nothing changes until the
   person confirms, and every result has an Undo or a way back. */
window.runInsightAction = async function (a, ctx) {
  if (!a) return;
  const p = a.payload || {};
  const kind = {draft_skill: "skill", make_project: "project", new_chat: "rerun", set_default: "route", teach_accept: "teach"}[a.kind] || a.kind;
  if (kind === "schedule") { S.prefillTask = p; openSettings("scheduled"); return; }
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
  if (kind === "rerun") { newThread(); $("#input").value = p.prompt || ""; $("#input").focus(); if ((p.files || []).length) toast("The new file is named in the message. Attach it with the plus button."); return; }
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
  const draw = () => `<h4>${d.facts.length} memor${d.facts.length === 1 ? "y" : "ies"} given to the agent</h4><p class="note">${esc(d.wording)}</p>${d.facts.map(f => `<div class="it" data-id="${esc(f.id)}"><span>${esc(f.text)}<small>${esc(f.scope_label)} · ${esc(f.source.text)}</small></span><span class="mem-btns"><button class="linkbtn" data-sedit="${esc(f.id)}">Edit</button><button class="linkbtn" data-swrong="${esc(f.id)}">Wrong</button><button class="linkbtn" data-sopen="1">Open in memory</button></span></div>`).join("")}`;
  const p = popAt(anchor, draw(), "src");
  p.onclick = async e => {
    const t = e.target.closest("button"); if (!t) return; const D = t.dataset;
    if (D.sopen) { p.close(); return goPage("memory"); }
    if (D.sedit) { const f = d.facts.find(x => x.id === D.sedit); p.close(); const v = await ask({title: "Edit this memory", value: f.text, ok: "Save"}); if (v) { await post("/api/memory", {action: "edit", id: f.id, text: v}); toast("Saved. New chats use the new wording."); } return; }
    if (D.swrong) { await post("/api/memory", {action: "wrong", id: D.swrong}); toast("Archived, and Meshpool will not suggest it again.", 7000, {label: "Undo", fn: () => post("/api/memory", {action: "unarchive", id: D.swrong})}); p.close(); delete srcCache010[id]; }
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
