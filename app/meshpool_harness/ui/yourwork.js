/* "Your work" page and the project brief. Two self-contained modules; the shell calls
     MeshYourWork.render(container)       a Reflect-style look back: 1, 3, 6 or 12 months, ending in real next steps
     MeshBrief.render(container, pid)     a project's living brief, with Catch me up
   They use the shell's helpers when present (api, esc, MP.token) and its CSS variables, so light and dark just work. */
"use strict";
(function () {
  const esc = s => (typeof window.esc === "function" ? window.esc(s) : String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c])));
  const get = path => (typeof window.api === "function" ? window.api(path) : fetch(path, {headers: {"X-Meshpool-Token": (window.MP || {}).token || ""}}).then(r => r.json()));
  const post = (path, body) => (typeof window.post === "function" ? window.post(path, body) : fetch(path, {method: "POST", headers: {"X-Meshpool-Token": (window.MP || {}).token || "", "Content-Type": "application/json"}, body: JSON.stringify(body || {})}).then(r => r.json()));
  const num = n => Number(n || 0).toLocaleString("en");
  const day = t => new Date(t * 1000).toLocaleDateString("en", {month: "short", day: "numeric"});
  const hourLabel = h => (h === null || h === undefined ? "" : `${String(h).padStart(2, "0")}:00`);
  const RANGES = [[1, "1 month"], [3, "3 months"], [6, "6 months"], [12, "12 months"]];
  const state = {range: 3};

  function css() {
    if (document.getElementById("mw-css")) return;
    const s = document.createElement("style");
    s.id = "mw-css";
    s.textContent = `
.mw{max-width:960px;margin:0 auto;padding:36px 32px 48px;color:var(--ink)}
.mw h2{font:600 var(--fs-l)/1.3 var(--display);margin:0 0 8px}
.mw .mw-sub{color:var(--ink-3);font-size:var(--fs-s);margin:0 0 16px}
.mw-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:28px}
.mw-stat{border:1px solid var(--line);border-radius:var(--r-l);padding:16px 16px;background:var(--bg)}
.mw-stat b{display:block;font:500 var(--fs-2xl)/1.1 var(--display);letter-spacing:0;font-variant-numeric:tabular-nums}
.mw-stat span{color:var(--ink-3);font-size:var(--fs-s)}
.mw-sec{margin:0 0 32px}
.mw-cols{display:grid;grid-template-columns:1fr 1fr;gap:28px}
.mw-bar{display:grid;grid-template-columns:150px 1fr 40px;align-items:center;gap:8px;margin:4px 0;font-size:var(--fs-m)}
.mw-bar i{display:block;height:10px;border-radius:5px;background:var(--pool);min-width:4px}
.mw-bar em{font-style:normal;color:var(--ink-3);text-align:right;font-size:var(--fs-s)}
.mw-chips{display:flex;flex-wrap:wrap;gap:8px}
.mw-chip{border:1px solid var(--line);border-radius:var(--r-m);padding:4px 12px;font-size:var(--fs-s);background:var(--bg);color:var(--ink-2)}
.mw-chip b{color:var(--ink);font-weight:600;margin-left:4px}
.mw-hours{display:flex;align-items:flex-end;gap:4px;height:64px}
.mw-hours span{flex:1;background:var(--pool-soft);border-radius:3px 3px 0 0;min-height:2px}
.mw-hours span.top{background:var(--pool)}
.mw-axis{display:flex;justify-content:space-between;color:var(--ink-3);font-size:var(--fs-xs);margin-top:4px}
.mw-gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.mw-usage{margin:0 0 32px}.mw-usage>summary{cursor:pointer;font:600 var(--fs-l)/1.3 var(--display);margin:0 0 16px}
.mw-file{text-align:left;font:inherit;color:inherit;cursor:pointer;display:block;width:100%;border:1px solid var(--line);border-radius:var(--r-m);padding:12px 12px;background:var(--bg);min-width:0}
.mw-file b{display:block;font-weight:500;font-size:var(--fs-s);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mw-file small{display:block;color:var(--ink-3);font-size:var(--fs-xs)}.mw-file:hover{background:var(--hover)}
.mw-line{display:flex;gap:8px;padding:8px 0;border-bottom:1px solid var(--line-2);font-size:var(--fs-m)}
.mw-line small{color:var(--ink-3);min-width:56px}
.mw-sug{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:var(--r-l);padding:16px 16px;margin-bottom:8px;background:var(--bg)}
.mw-sug div{flex:1;min-width:0}.mw-sug b{display:block;font-weight:600}.mw-sug span{color:var(--ink-3);font-size:var(--fs-s)}
.mw-recap{display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}
.mw-recap img{width:240px;border-radius:var(--r-m);border:1px solid var(--line);background:var(--fill)}
.mw-note{color:var(--ink-3);font-size:var(--fs-s);margin-top:4px}
.pjfiles{list-style:none;margin:8px 0;padding:0}.pjfiles .pjf{display:flex;align-items:center;gap:8px;width:100%;text-align:left}.pjfiles svg{width:16px;height:16px;flex:none;color:var(--ink-3)}
.mw-brief h3{font:600 var(--fs-m)/1.3 var(--body);margin:16px 0 4px}
.mw-brief ul{margin:0;padding-left:20px}.mw-brief li{margin:4px 0}
.mw-catch{border:1px solid var(--line);border-radius:var(--r-l);padding:16px 16px;background:var(--pool-soft);margin:16px 0}
@media (max-width:640px){.mw{padding:24px 16px 40px}.mw-stats{grid-template-columns:1fr 1fr}.mw-cols{grid-template-columns:1fr}.mw-bar{grid-template-columns:110px 1fr 36px}}`;
    document.head.appendChild(s);
  }

  const bars = (rows, label, val) => {
    const top = Math.max(1, ...rows.map(val));
    return rows.map(r => `<div class="mw-bar"><span>${esc(label(r))}</span><i style="width:${Math.max(4, Math.round(100 * val(r) / top))}%"></i><em>${num(val(r))}</em></div>`).join("");
  };

  function actionButton(sug, i) {
    return `<button class="btn" data-mw-act="${i}">${esc((sug.action || {}).label || "Do it")}</button>`;
  }

  function draw(box, d) {
    if (d.empty) {
      box.innerHTML = `<div class="gempty"><b>Nothing to look back on yet.</b><span>After a few chats this shows what you worked on, what you made, and what to try next. Incognito chats are never counted.</span></div>`;
      const bar = box.parentElement && box.parentElement.querySelector(".gbar");
      if (bar) bar.hidden = true;
      return;
    }
    const bar0 = box.parentElement && box.parentElement.querySelector(".gbar");
    if (bar0) bar0.hidden = false;
    const t = d.totals, u = d.usage, w = d.when;
    const maxH = Math.max(1, ...w.hour);
    const sec = (title, inner, extra = "") => inner ? `<section class="mw-sec ${extra}"><h2>${title}</h2>${inner}</section>` : "";
    const dirOf = g => (g.path.includes("/") ? g.path.slice(0, g.path.lastIndexOf("/")) : (g.root || "").split(/[\\/]/).filter(Boolean).pop() || "");
    const files = d.gallery.length ? `<section class="mw-sec mw-files"><h2>Your files</h2><div class="mw-gal">${d.gallery.slice(0, 40).map((g, i) => `<button type="button" class="mw-file" data-mw-file="${i}" title="${esc(g.root + "/" + g.path)}"><b>${esc(g.name)}</b><small>${esc(g.type)} · ${day(g.updated)}</small><small class="mw-dir">in ${esc(dirOf(g))}</small></button>`).join("")}</div></section>` : `<section class="mw-sec mw-files"><h2>Your files</h2><p class="mw-note">Files meshpool makes for you show up here, newest first. Click one to open it.</p></section>`;
    box.innerHTML = files + `<details class="mw-usage"><summary>Usage and habits</summary>
      <div class="mw-stats">
        <div class="mw-stat"><b>${num(t.chats)}</b><span>chats</span></div>
        <div class="mw-stat"><b>${num(t.turns)}</b><span>requests</span></div>
        <div class="mw-stat"><b>${num(t.deliverables)}</b><span>files made</span></div>
        <div class="mw-stat"><b>${num(u.tokens)}</b><span>tokens${u.measured_turns ? "" : " (not measured)"}</span></div>
      </div>
      ${sec("What you worked on", d.topics.length ? `<div class="mw-chips">${d.topics.map(x => `<span class="mw-chip">${esc(x.topic)}<b>${x.chats}</b></span>`).join("")}</div>` : "")}
      <div class="mw-cols">
        ${sec("What you asked for", d.kinds.length ? bars(d.kinds, k => k.label, k => k.count) : `<p class="mw-note">Kinds of work show once meshpool has routed a few requests.</p>`)}
        ${sec("Agents", d.agents.length ? bars(d.agents, a => a.name, a => a.chats) + (d.models.length ? `<p class="mw-note">Models: ${d.models.map(m => `${esc(m.name)} (${m.chats})`).join(", ")}</p>` : "") : "")}
      </div>
      ${sec("When you work", `<div class="mw-hours" role="img" aria-label="Requests by hour of the day">${w.hour.map((n, h) => `<span class="${h === w.busiest_hour ? "top" : ""}" style="height:${Math.round(100 * n / maxH)}%" title="${hourLabel(h)}: ${n}"></span>`).join("")}</div>
        <div class="mw-axis"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>23:00</span></div>
        <p class="mw-note">${w.busiest_weekday ? `Busiest day: ${esc(w.busiest_weekday)}. ` : ""}${w.busiest_hour !== null ? `Busiest hour: ${hourLabel(w.busiest_hour)}.` : ""}</p>`)}
      <div class="mw-cols">
        ${sec("Skills and connectors", (d.skills.length || d.connectors.length) ? `<div class="mw-chips">${d.skills.map(s => `<span class="mw-chip">Skill ${esc(s.name)}<b>${s.chats}</b></span>`).join("")}${d.connectors.map(s => `<span class="mw-chip">${esc(s.name)}<b>${s.chats}</b></span>`).join("")}</div>` : "")}
        ${sec("Cost", `<p class="mw-note">${esc(u.note)}</p>`)}
      </div>
      ${sec("What you taught meshpool", d.taught.length ? d.taught.slice(0, 12).map(x => `<div class="mw-line"><small>${day(x.ts)}</small><span>${esc(x.what)}: ${esc(x.text)}</span></div>`).join("") : "")}
      </details>
      ${sec("Worth doing next", d.suggestions.length ? d.suggestions.map((s, i) => `<div class="mw-sug"><div><b>${esc(s.title)}</b><span>${esc(s.detail)}</span></div>${actionButton(s, i)}</div>`).join("") : `<p class="mw-note">No repeated pattern worth a change yet. meshpool stays quiet when it has nothing real to suggest.</p>`)}
      <section class="mw-sec"><h2>Share a recap</h2><div class="mw-recap"><div><button class="btn" id="mwRecap">Preview recap image</button><p class="mw-note">Drawn on your computer from counts and categories only. No chat text, file names or memory.</p></div><div id="mwPrev"></div></div></section>
      <p class="mw-note">${esc(d.privacy)}</p>`;
    box.querySelectorAll("[data-mw-file]").forEach(b => b.onclick = () => { const g = d.gallery[+b.dataset.mwFile]; if (g && typeof window.openPreview === "function") window.openPreview(g.root, g.path); });
    box.querySelectorAll("[data-mw-act]").forEach(b => b.onclick = () => {
      const s = d.suggestions[+b.dataset.mwAct];
      document.dispatchEvent(new CustomEvent("meshpool:work-action", {detail: s}));   // the shell opens the matching flow
    });
    const prev = box.querySelector("#mwPrev");
    box.querySelector("#mwRecap").onclick = async e => {
      e.target.disabled = true;
      try {
        const p = await get(`/api/work/recap?range=${state.range}`);
        prev.innerHTML = `<img alt="Recap preview" src="data:image/png;base64,${p.png_b64}"><div style="margin-top:8px"><button class="btn primary" id="mwSave">Save as image</button></div>`;
        prev.querySelector("#mwSave").onclick = async () => {
          const r = await fetch(`/api/work/recap.png?range=${state.range}&download=1`, {headers: {"X-Meshpool-Token": (window.MP || {}).token || ""}});
          const url = URL.createObjectURL(await r.blob());
          const a = document.createElement("a");
          a.href = url; a.download = p.filename || "meshpool-recap.png"; document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 2000);
        };
      } catch (err) { prev.innerHTML = `<p class="mw-note">${esc(err.message)}</p>`; }
      e.target.disabled = false;
    };
  }

  async function load(root, box) {
    root.querySelectorAll("[data-mw-range]").forEach(b => b.setAttribute("aria-selected", String(+b.dataset.mwRange === state.range)));
    box.innerHTML = `<p class="mw-note">Looking back…</p>`;
    try { draw(box, await get(`/api/work?range=${state.range}`)); }
    catch (e) { box.innerHTML = `<div class="gempty"><b>Could not load this page.</b><span>${esc(e.message)}</span></div>`; }
  }

  window.MeshYourWork = {
    render(container) {
      css();
      container.innerHTML = `<div class="gal mw"><header class="gh"><h1>Your work</h1></header>
        <p class="mw-sub">Your last months of work. Private, on this computer.</p>
        <div class="gbar"><div class="gtabs" role="tablist">${RANGES.map(([n, l]) => `<button role="tab" data-mw-range="${n}" aria-selected="${n === state.range}">${l}</button>`).join("")}</div></div>
        <div id="mwBody"></div></div>`;
      const box = container.querySelector("#mwBody");
      container.querySelectorAll("[data-mw-range]").forEach(b => b.onclick = () => { state.range = +b.dataset.mwRange; load(container, box); });
      return load(container, box);
    },
  };

  function drawBrief(box, pid, b) {
    const list = xs => xs && xs.length ? `<ul>${xs.map(x => `<li>${esc(typeof x === "string" ? x : x.text)}</li>`).join("")}</ul>` : `<p class="mw-note">Nothing yet.</p>`;
    box.innerHTML = `<div class="mw-brief">
      <p>${esc(b.what)}</p>
      <button class="btn" id="mwCatch">Catch me up</button>
      <div id="mwCatchOut"></div>
      <h3>Decisions</h3>${list(b.decisions)}
      <h3>Open threads</h3>${list(b.open_threads)}
      <h3>Files made</h3>${b.files && b.files.length ? `<div class="mw-chips">${b.files.slice(0, 12).map(f => `<span class="mw-chip">${esc(f.name)}</span>`).join("")}</div>` : `<p class="mw-note">None yet.</p>`}
      <p class="mw-note">${b.chats} chat${b.chats === 1 ? "" : "s"}${b.last_activity ? " · last activity " + day(b.last_activity) : ""}</p></div>`;
    box.querySelector("#mwCatch").onclick = async e => {
      e.target.disabled = true;
      const out = box.querySelector("#mwCatchOut");
      try {
        const r = await post("/api/summaries/catchup", {id: pid});
        out.innerHTML = `<div class="mw-catch"><b>${r.since ? "Since " + day(r.since) : "So far"}</b><p style="margin:4px 0 0">${esc(r.text)}</p>${r.items.length ? `<ul>${r.items.slice(0, 6).map(i => `<li>${esc(i.summary || i.title)}</li>`).join("")}</ul>` : ""}</div>`;
      } catch (err) { out.innerHTML = `<p class="mw-note">${esc(err.message)}</p>`; }
      e.target.disabled = false;
    };
  }

  window.MeshBrief = {
    async render(container, pid) {
      css();
      container.innerHTML = `<p class="mw-note">Reading the project…</p>`;
      // the brief can wait on a model: after 8 s the page says so and moves on, it never sits on "Reading the project"
      const slow = new Promise(res => setTimeout(() => res(null), 8000));
      try {
        const b = await Promise.race([get(`/api/summaries/project?id=${encodeURIComponent(pid)}`), slow]);
        if (b) drawBrief(container, pid, b);
        else { container.innerHTML = `<p class="mw-note">The summary is taking a while. The files and chats are below. <button class="linkbtn" id="mwAgain">Try again</button></p>`; container.querySelector("#mwAgain").onclick = () => window.MeshBrief.render(container, pid); }
      } catch (e) { container.innerHTML = `<p class="mw-note">${esc(e.message)}</p>`; }
    },
  };
})();
