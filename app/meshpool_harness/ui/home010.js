/* 0.0.10 on the home screen: starters from the user's own files, "While you were away", and the Suggestions and
   quiet-hours settings. Wraps renderHero; draws only at app open and chat open, never while a turn runs. */
"use strict";

const _renderHero010 = renderHero;
let hero010Token = 0;
renderHero = function () {
  _renderHero010.apply(this, arguments);
  if (S.mode === "code" || S.cur || S.view !== "chat") return;
  const my = ++hero010Token;
  fresh010(my); away010(my);
};
async function fresh010(my) {
  if (store("firstrun_done") || (S.threads || []).length > 2) return;          // new users only: the static starters stay otherwise
  const box = $(".starters"); if (!box) return;
  let r = {starters: [], files: 0};
  try { r = await api("/api/firstrun/scan?folder=" + encodeURIComponent(S.draft.project || "")); } catch (e) { r = {starters: [], files: 0}; }
  if (my !== hero010Token || !$(".starters")) return;
  if (r.starters.length) {
    $(".starters").innerHTML = r.starters.map(s => `<button data-s="${esc(s.prompt)}" title="Meshpool has only seen the file's name, not what is inside">${esc(s.prompt.replace(/\.$/, ""))}</button>`).join("") + `<button data-fr="more">Something else</button>`;
  } else if (!r.files) {
    $(".starters").insertAdjacentHTML("afterbegin", `<button data-fr="samples" class="primary">Try it with sample files</button>`);
  }
  $(".starters").addEventListener("click", async e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.fr === "samples") {
      e.stopPropagation(); b.disabled = true; b.textContent = "Getting the samples ready…";
      try { const s = await post("/api/firstrun/samples", {}); S.draft.project = s.folder; store("firstrun_done", true); renderTop(); renderBar(); $("#input").value = s.prompt; sendNow(false); }
      catch (err) { toast(err.message); b.disabled = false; }
      return;
    }
    if (b.dataset.fr === "more") { e.stopPropagation(); $(".starters").innerHTML = STARTERS.map(([a, c]) => `<button data-s="${esc(c)}">${esc(a)}</button>`).join(""); return; }
    if (b.dataset.s) store("firstrun_done", true);
  }, true);
}
async function away010(my) {
  let a; try { a = await api("/api/away"); } catch (e) { return; }
  if (my !== hero010Token || !$(".hero")) return;
  for (const c of (a.cards || []).slice(0, 1)) insightCard010(c);          // at most one nudge on the home screen
  if (!(a.runs || []).length) return;
  const el = document.createElement("section"); el.className = "away010"; el.setAttribute("aria-label", "While you were away");
  el.innerHTML = `<div class="a-h"><b>While you were away</b><button class="linkbtn" id="awayOk">Got it</button></div>
    ${a.runs.map(r => `<button class="a-run" data-open="${esc(r.chat_id)}"><span class="a-n">${esc(r.name)}</span><span class="a-l">${esc(r.line)}</span></button>`).join("")}
    ${a.quiet && a.quiet.length ? `<p class="a-q">${esc(a.quiet.join(", "))}: no change.</p>` : ""}`;
  const hero = $(".hero"); hero.insertBefore(el, $(".starters") || null);
  $$(".a-run", el).forEach(b => b.onclick = () => openThread(b.dataset.open));
  $("#awayOk", el).onclick = async () => { await post("/api/away/seen", {}).catch(() => {}); el.remove(); };
}

/* ---------- Settings > Features > Suggestions ---------- */
const TYPE_NAME = {schedule: "Repeat tasks", skill: "Repeated procedures", project: "Repeated background", route: "Better agent or cost", connect: "Connect a service",
  rerun: "A new version of a file", teach: "Learn from your edits", remember: "Things worth remembering", followup: "Next-step chips"};
window.renderSuggestSettings = async function (b, close) {
  const v = await api("/api/insights/settings");
  const pct = r => r == null ? "not enough data yet" : Math.round(r * 100) + "% accepted";
  const types = v.types || [];
  const typeList = ts => `<ul class="rows2">${ts.map(t => `<li class="row2"><label class="switchrow"><input type="checkbox" data-type="${esc(t.type)}" ${t.on ? "checked" : ""}> ${esc(t.label || TYPE_NAME[t.type] || t.type)}</label><small>${esc(pct(t.rate))}${t.shown ? ` · shown ${t.shown} time${t.shown === 1 ? "" : "s"}` : ""}${t.throttled ? " · stopped automatically" : ""}</small></li>`).join("")}</ul>`;
  b.innerHTML = `${close}<h3>Suggestions</h3><p class="lead">Tips when a chat ends or you open the app. Never while it works.</p>
    <ul class="rows2"><li class="row2"><label class="switchrow"><input type="checkbox" id="sgOn" ${v.insights_on !== false ? "checked" : ""}> Show suggestions</label></li></ul>
    ${(v.notices || []).map(n => `<div class="gbanner"><div><b>${esc(n.title || "Stopped a suggestion type")}</b><span>${esc(n.text || "")}</span></div><button class="btn" data-ack="1">OK</button></div>`).join("")}
    <h3 style="font-size:16px;margin:18px 0 4px">By type</h3>${typeList(types.filter(t => t.shown || !t.on || t.throttled))}${types.some(t => !(t.shown || !t.on || t.throttled)) ? `<details class="sgmore"><summary>${types.filter(t => !(t.shown || !t.on || t.throttled)).length} types with nothing to show yet</summary>${typeList(types.filter(t => !(t.shown || !t.on || t.throttled)))}</details>` : ""}
    <h3 style="font-size:16px;margin:18px 0 4px">"Show me less like this"</h3>${(v.history || []).filter(m => m.active).length ? `<ul class="rows2">${v.history.filter(m => m.active).map(m => `<li class="row2"><span>${esc(m.title)}</span><button class="linkbtn" data-undo="${esc(m.id)}">Undo</button></li>`).join("")}</ul>` : `<p class="lead">Nothing muted.</p>`}
    <div style="margin-top:16px"><button class="btn" id="sgReset">Reset suggestion learning</button></div><div class="res" id="sgRes"></div>`;
  const re = () => window.renderSuggestSettings(b, close);
  $("#sgOn", b).onchange = async e => { await post("/api/insights/settings", {insights_on: e.target.checked}); re(); };
  $$("[data-type]", b).forEach(x => x.onchange = async () => { await post("/api/insights/settings", {type: x.dataset.type, on: x.checked}); re(); });
  $$("[data-undo]", b).forEach(x => x.onclick = async () => { await post("/api/insights/settings", {action: "undo_mute", id: x.dataset.undo}); re(); });
  $$("[data-ack]", b).forEach(x => x.onclick = async () => { await post("/api/insights/settings", {action: "ack_notices"}); re(); });
  $("#sgReset", b).onclick = async () => { if (await ask({title: "Reset suggestion learning?", text: "Forget what you accepted or dismissed.", ok: "Reset"})) { await post("/api/insights/settings", {action: "reset"}); re(); } };
};
window.renderQuietHours = async function (box) {
  const a = await api("/api/insights/settings"), q = a.quiet_hours || {on: false, start: "22:00", end: "07:00"};
  box.innerHTML = `<label class="switchrow"><input type="checkbox" id="qhOn" ${q.on ? "checked" : ""}> Turn on quiet hours</label><div class="tkrow" style="margin-top:8px"><label class="field"><span>From</span><input type="time" id="qhS" value="${esc(q.start)}"></label><label class="field"><span>Until</span><input type="time" id="qhE" value="${esc(q.end)}"></label></div>`;
  const save = () => post("/api/insights/settings", {quiet_hours: {on: $("#qhOn", box).checked, start: $("#qhS", box).value, end: $("#qhE", box).value}});
  ["#qhOn", "#qhS", "#qhE"].forEach(s => { $(s, box).onchange = save; });
};
