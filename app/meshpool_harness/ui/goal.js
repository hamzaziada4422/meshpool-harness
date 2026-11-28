/* Goal mode (agent/goal.py): a compact card above the thread, the same in Chat and Code mode.
   It shows the goal, the bar, the score and its history, each criterion (met, not met, could not check), turns, spend,
   elapsed time and the state: writing the rubric, open for edits, working, checking, paused with a reason, met, or
   stopped with a reason. While the rubric is open the user can edit it until it freezes (60 s, or Start). After a
   restart a goal that was working resumes by itself after a countdown, with a Cancel button.
   Drawn from the chat view's goal_state; a "goal" event redraws it at once. */
(function () {
  const STATE = {drafting: "Writing the rubric", review: "Rubric open for edits", working: "Working", checking: "Checking",
                 paused: "Paused", met: "Met", stopped: "Stopped", cleared: "Cleared"};
  const MARK = {met: "Met", not_met: "Not met", unverified: "Could not check", none: "Not checked yet"};
  const fmtN = x => { const v = Math.round(Number(x || 0) * 10) / 10; return Number.isInteger(v) ? String(v) : v.toFixed(1); };
  const money = x => { const v = Number(x || 0); return v > 0 && v < 0.01 ? "under $0.01" : "$" + v.toFixed(2); };
  const dur = s => { s = Math.floor(Number(s || 0)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`; };
  const e = s => (typeof esc === "function" ? esc(s) : String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]));

  function stateWords(g) {
    let w = STATE[g.status] || g.status || "";
    if (g.status === "stopped" && g.reason === "budget") w = "Stopped at the budget";
    else if ((g.status === "paused" || g.status === "stopped") && g.reason) w += ": " + g.reason;
    return w;
  }
  function left(until, g) {          // seconds left on a countdown, on the server's clock
    const off = (g.now || 0) * 1000 - (g._seen || Date.now());
    return Math.max(0, Math.ceil(((until || 0) * 1000 - (Date.now() + off)) / 1000));
  }
  function itemsOf(g) {
    const found = {}; for (const i of (g.findings && g.findings.items) || []) found[i.id] = i;
    return (g.rubric || []).map(c => Object.assign({}, c, {status: found[c.id] ? found[c.id].status : "none", evidence: found[c.id] ? found[c.id].evidence || "" : ""}));
  }
  function meta(g) {
    const b = g.budgets || {};
    const bits = [`Turn ${g.turns || 0} of ${b.turns || 0}`, `${g.checks || 0} of ${b.checks || 0} checks`,
                  money(g.spent) + (Number(b.usd) > 0 ? ` of ${money(b.usd)}` : ""), dur(g.elapsed)];
    return bits.join(" · ");
  }
  function hist(g) {
    const h = (g.history || []).filter(x => !x.error).map(x => fmtN(x.score));
    return h.length > 1 ? h.slice(-8).join(" → ") : "";
  }
  function scoreHTML(g) {
    const h = (g.history || []); const last = h.length ? h[h.length - 1] : null;
    return last ? `<span class="gc-score" aria-label="Score ${fmtN(last.score)} out of 10, bar ${fmtN(g.bar)}"><b>${fmtN(last.score)}</b>/10 <small>bar ${fmtN(g.bar)}</small></span>`
                : `<span class="gc-score gc-none"><small>bar ${fmtN(g.bar)}/10</small></span>`;
  }
  function listHTML(g) {
    return `<ul class="gc-list" aria-label="Criteria">${itemsOf(g).map(c => `<li class="gc-${e(c.status)}"><span class="gc-mark">${MARK[c.status] || e(c.status)}</span> <span class="gc-cb"><span class="gc-ct">${e(c.text)}</span>${c.must ? ` <span class="gc-must">must</span>` : ""}${c.evidence ? `<small>${e(c.evidence)}</small>` : ""}</span></li>`).join("")}</ul>`;
  }
  function editorHTML(g) {
    const rows = (g.rubric || []).map((c, i) => `<li data-i="${i}"><input class="gc-in" type="text" value="${e(c.text)}" aria-label="Criterion ${i + 1}" maxlength="300">
      <label class="gc-mustl"><input type="checkbox" class="gc-mustc"${c.must ? " checked" : ""}> must</label>
      <select class="gc-w" aria-label="Weight of criterion ${i + 1}">${[1, 2, 3, 4, 5].map(w => `<option${Number(c.weight) === w ? " selected" : ""}>${w}</option>`).join("")}</select>
      <button class="linkbtn gc-del" type="button" aria-label="Remove criterion ${i + 1}">Remove</button></li>`).join("");
    return `<p class="gc-help">This is what the independent check will score. Edit it now: it freezes when you press Start, or by itself <span class="gc-cd"></span>.</p>
      <ol class="gc-edit">${rows}</ol><button class="linkbtn gc-add" type="button">Add a criterion</button>`;
  }
  function read(card) {
    return [...card.querySelectorAll(".gc-edit li")].map(li => ({text: li.querySelector(".gc-in").value.trim(), check: ((window._gcChecks || {})[li.dataset.i] || {}).check || "",
      needs_user: !!((window._gcChecks || {})[li.dataset.i] || {}).needs_user,
      must: li.querySelector(".gc-mustc").checked, weight: Number(li.querySelector(".gc-w").value) || 1})).filter(c => c.text);
  }
  async function op(cur, name, body) {
    try { const v = await post(`/api/agent/${cur.id}/goal`, Object.assign({op: name, since: 1e9}, body || {}));
      const c = typeof S === "object" && S.cur && S.cur.id === cur.id ? S.cur : cur;   // the chat as it is now on screen
      if (c.view) { c.view.goal_state = v.goal_state; c.view.goal = v.goal; } window.renderGoal010(c); if (typeof renderBar === "function") renderBar(); }
    catch (err) { if (typeof toast === "function") toast(err.message, 4000); }
  }

  function host() {
    let card = document.getElementById("goalCard");
    const conv = document.getElementById("conv");
    if (!conv) return null;
    if (!card) { card = document.createElement("section"); card.id = "goalCard"; card.className = "goalcard"; card.setAttribute("aria-label", "Goal"); conv.parentNode.insertBefore(card, conv); }
    return card;
  }
  function tick(card, g) {
    const cd = card.querySelector(".gc-cd");
    if (cd) { const s = left(g.draft_until, g); cd.textContent = s > 0 ? `in ${s} s` : "now"; }
    const rs = card.querySelector(".gc-rs");
    if (rs) { const s = left(g.resume_at, g); rs.textContent = s > 0 ? `Resuming the goal in ${s} s.` : "Resuming the goal now."; }
  }

  window.renderGoal010 = function (cur) {
    if (!cur) { clearInterval(window._gcTimer); document.getElementById("goalCard")?.remove(); return; }   // no chat open
    if (typeof S === "object" && S.cur !== cur) return;
    const g = cur.view && cur.view.goal_state;
    const old = document.getElementById("goalCard");
    clearInterval(window._gcTimer);
    if (!g) { old && old.remove(); return; }
    g._seen = g._seen || Date.now();
    const card = host(); if (!card) return;
    const editing = g.status === "review" || g.status === "drafting";
    // While the rubric is open the editor is drawn once: the user's typing is never redrawn away (only the countdown ticks)
    const key = editing ? [g.status, g.draft_until].join("|") : JSON.stringify([g.status, g.reason, g.question, g.turns, g.checks,
      g.spent, Math.floor((g.elapsed || 0) / 60), g.history, g.findings && g.findings.items, g.resume_at, g.rubric_hash]);
    card.className = `goalcard gc-s-${g.status}`;
    if (card.dataset.key !== key) {
      window._gcChecks = Object.fromEntries((g.rubric || []).map((c, i) => [i, {check: c.check || "", needs_user: !!c.needs_user}]));
      const resuming = g.status === "working" && g.resume_at && left(g.resume_at, g) > 0;
      const acts = [];
      if (g.status === "review" || g.status === "drafting") acts.push(`<button class="btn sm primary" data-gc="start"${g.status === "drafting" ? " disabled" : ""}>Start</button>`);
      if (resuming) acts.push(`<button class="btn sm" data-gc="cancel_resume">Cancel</button>`);
      if (g.status === "paused" || (g.status === "stopped" && g.reason !== "budget")) acts.push(`<button class="btn sm primary" data-gc="resume">Resume</button>`);
      if (["drafting", "review", "working", "checking", "paused"].includes(g.status)) acts.push(`<button class="btn sm" data-gc="clear">End goal</button>`);
      if (["met", "stopped", "cleared"].includes(g.status)) acts.push(`<button class="btn sm" data-gc="dismiss">Close</button>`);
      const open = editing || g.status === "paused" || g.status === "met" || g.status === "stopped" || card.dataset.open === "1";
      card.innerHTML = `<div class="gc-head"><span class="gc-label">Goal</span><span class="gc-text" title="${e(g.goal)}">${e(g.goal)}</span>${scoreHTML(g)}</div>
        <div class="gc-row"><span class="gc-state" role="status">${e(stateWords(g))}</span><span class="gc-meta">${e(meta(g))}</span>${hist(g) ? `<span class="gc-hist" aria-label="Score history">${e(hist(g))}</span>` : ""}</div>
        ${resuming ? `<p class="gc-rs" role="status"></p>` : ""}
        ${g.status === "paused" && g.question ? `<p class="gc-q">${e(g.question)}</p>` : ""}
        ${g.status === "drafting" ? `<p class="gc-help">Writing a rubric for this goal. You can edit it before work starts.</p>` :
          editing ? editorHTML(g) : `<details class="gc-crit"${open ? " open" : ""}><summary>Criteria (${(g.rubric || []).length})</summary>${listHTML(g)}</details>`}
        <div class="gc-acts">${acts.join("")}</div>`;
      card.dataset.key = key;
      const det = card.querySelector(".gc-crit"); if (det) det.addEventListener("toggle", () => { card.dataset.open = det.open ? "1" : ""; });
      for (const b of card.querySelectorAll("[data-gc]")) b.onclick = () => {
        const name = b.dataset.gc;
        if (name === "start") { clearTimeout(t); return op(cur, "start", {rubric: read(card)}); }   // no late edit after it
        op(cur, name);
      };
      let t = 0;
      const save = () => { clearTimeout(t); t = setTimeout(() => op(cur, "edit", {rubric: read(card)}), 600); };
      for (const el of card.querySelectorAll(".gc-in, .gc-w, .gc-mustc")) el.addEventListener(el.classList.contains("gc-in") ? "input" : "change", save);
      for (const b of card.querySelectorAll(".gc-del")) b.onclick = () => { b.closest("li").remove(); save(); };
      const add = card.querySelector(".gc-add");
      if (add) add.onclick = () => {
        const ol = card.querySelector(".gc-edit"); const i = ol.children.length; if (i >= 12) return;
        ol.insertAdjacentHTML("beforeend", `<li data-i="n${i}"><input class="gc-in" type="text" value="" aria-label="Criterion ${i + 1}" maxlength="300"> <label class="gc-mustl"><input type="checkbox" class="gc-mustc"> must</label> <select class="gc-w" aria-label="Weight of criterion ${i + 1}">${[1, 2, 3, 4, 5].map(w => `<option>${w}</option>`).join("")}</select> <button class="linkbtn gc-del" type="button">Remove</button></li>`);
        const li = ol.lastElementChild; li.querySelector(".gc-in").focus();
        li.querySelector(".gc-in").addEventListener("input", save); li.querySelector(".gc-del").onclick = () => { li.remove(); save(); };
      };
    } else {
      const sc = card.querySelector(".gc-state"); if (sc) sc.textContent = stateWords(g);
    }
    tick(card, g);
    if ((g.status === "review") || (g.status === "working" && g.resume_at)) window._gcTimer = setInterval(() => { if (document.getElementById("goalCard") === card) tick(card, g); else clearInterval(window._gcTimer); }, 1000);
  };

  /* One line in the thread per independent check; the card keeps the detail. */
  window.goalCheckLine010 = function (ev, add) {
    const cls = ev.met ? "note goalcheck gc-ok" : "note goalcheck";
    const icon = typeof ICON === "object" ? (ev.met ? ICON.check2 : ICON.warn) || "" : "";
    return add(`${icon}<span>${e(ev.text || `Check ${ev.n}: ${fmtN(ev.score)}/${fmtN(ev.bar)}`)}</span>`, cls);
  };
})();
