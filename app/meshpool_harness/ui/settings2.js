/* Settings, regrouped into seven pages with tabs inside. Every page that existed before is still here (renderSection in
   app.js draws it); the pages new in this version (profile, features, notifications, plan, your data) are drawn here.
   Loaded after app.js and before company.js, which adds Company and brand on top. */
"use strict";
const SET_GROUPS = [
  ["g-general", "General", ["profile", "appearance", "notify"]],
  ["g-caps", "Capabilities", ["caps", "personal", "web"]],
  ["g-conn", "Connectors", ["connectors", "skills", "plugins", "mcp"]],
  ["g-models", "Models and accounts", ["general", "models", "harnesses"]],
  ["g-usage", "Usage and billing", ["usage", "billing"]],
  ["g-privacy", "Privacy and data", ["permissions", "data", "import"]],
  ["g-adv", "Advanced", ["about", "scheduled", "terminal", "advanced", "company"]],
];
const SET_NAMES = {profile: "Profile", appearance: "Appearance", notify: "Notifications", caps: "Features", personal: "Instructions and memory", web: "Web search",
  connectors: "Directory", skills: "Skills", plugins: "Plugins", mcp: "Custom tools", general: "Account and key", models: "Models", harnesses: "Agents",
  usage: "Usage", billing: "Plan and billing", permissions: "Permissions", data: "Your data", import: "Import chats",
  about: "About and updates", scheduled: "Scheduled tasks", terminal: "Terminal command", advanced: "More options"};
// company.js adds "Company and brand" to this list, as it did before.
const SECTIONS = Object.entries(SET_NAMES);
const setName = k => SET_NAMES[k] || (SECTIONS.find(x => x[0] === k) || [])[1] || k;

function groupOfSection(sec) { return SET_GROUPS.find(g => g[2].includes(sec)) || SET_GROUPS[0]; }
function markSettings(sec) {
  const g = groupOfSection(sec);
  $$(".mnav [data-g]").forEach(x => x.setAttribute("aria-selected", x.dataset.g === g[0]));
  const st = $("#subtabs"); if (!st) return;
  const subs = g[2].filter(k => SECTIONS.some(x => x[0] === k));
  st.hidden = subs.length < 2;
  if (st.dataset.g !== g[0]) { st.dataset.g = g[0]; st.innerHTML = subs.map(k => `<button role="tab" data-sub="${k}">${esc(setName(k))}</button>`).join(""); $$("[data-sub]", st).forEach(b => b.onclick = () => renderSection(b.dataset.sub)); }
  $$("[data-sub]", st).forEach(b => b.setAttribute("aria-selected", b.dataset.sub === sec));
}
function openSettings(sec = "profile") {
  closePop(); closeCtx();
  $(".scrim:not(.dlg-scrim)")?.remove();
  const g0 = groupOfSection(sec);
  const sc = document.createElement("div"); sc.className = "scrim";
  sc.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="Settings"><nav class="mnav" aria-label="Settings sections"><h2>Settings</h2>${SET_GROUPS.map(([k, lb, subs]) => `<button data-g="${k}" data-first="${subs[0]}" aria-selected="${k === g0[0]}">${lb}</button>`).join("")}<span class="sp"></span>${MP.desktop && !S.settings?.native ? `<button id="quitApp">Quit Meshpool</button>` : ""}</nav><div class="mmain"><div class="subtabs" id="subtabs" role="tablist" aria-label="Pages"></div><div class="mbody" id="mbody"></div></div></div>`;
  document.body.appendChild(sc);
  sc.addEventListener("mousedown", e => { if (e.target === sc) sc.remove(); });
  $$(".mnav [data-g]", sc).forEach(b => b.onclick = () => renderSection(b.dataset.first));
  $("#quitApp")?.addEventListener("click", () => post("/api/quit").then(() => document.body.innerHTML = `<div class="onb"><div class="onb-card"><h1>Meshpool has stopped</h1><p>You can close this window.</p></div></div>`));
  renderSection(sec);
}

(() => {
  const base = renderSection;
  const toggleRow = (id, name, desc, on, extra = "", disabled = false) => `<li><span class="tg"><b>${esc(name)}</b><small>${esc(desc)}</small>${extra}</span><label class="switch"><input type="checkbox" data-t="${esc(id)}" ${on ? "checked" : ""} ${disabled ? "disabled" : ""} aria-label="${esc(name)}"><span></span></label></li>`;
  const LANGS = ["English", "Spanish", "French", "German", "Portuguese", "Italian", "Arabic", "Chinese", "Japanese", "Korean", "Hindi", "Turkish", "Dutch", "Russian"];
  const NOTIFY_DEFAULT = {done: true, wait: true, sound: false};
  const nset = () => Object.assign({}, NOTIFY_DEFAULT, store("notify") || {});

  // Notifications: told by the sidebar poll when a chat finishes or needs an answer while the window is in the background.
  window.notifyEvent = function (kind, title) {
    const n = nset(); if (!n[kind]) return;
    if (!document.hidden && document.hasFocus()) return;
    const text = kind === "wait" ? "Needs your answer" : "Finished";
    post("/api/notify", {title: title || "Meshpool", body: text}).catch(() => {});
    if (n.sound) { try { const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain(); o.connect(g); g.connect(a.destination); o.frequency.value = 660; g.gain.setValueAtTime(.08, a.currentTime); g.gain.exponentialRampToValueAtTime(.0001, a.currentTime + .35); o.start(); o.stop(a.currentTime + .36); } catch (e) { /* no audio */ } }
  };

  renderSection = async function (sec) {
    markSettings(sec);
    const b = $("#mbody"); if (!b) return;
    const close = `<button class="iconbtn mclose" aria-label="Close settings">${ICON.x}</button>`;
    const done = () => $(".mclose", b)?.addEventListener("click", () => $(".scrim:not(.dlg-scrim)")?.remove());
    const res = (id, cls, t) => { const el = $("#" + id); if (el) { el.className = "res " + cls; el.textContent = t; } };
    if (sec === "profile") {
      await Promise.all([loadSettings(), S.models ? 0 : loadModels()]);
      const p = await api("/api/profile");
      const ms = S.models?.models || [];
      b.innerHTML = `${close}<h3>Profile</h3><p class="lead">So Meshpool knows who it is talking to. This stays on this computer and goes into new chats.</p>
        <label class="field"><span>Your name</span><input id="pfName" value="${esc(p.name)}" maxlength="80" autocomplete="off" placeholder="For example: Sam Rivera"></label>
        <label class="field"><span>What to call you</span><input id="pfAddr" value="${esc(p.address)}" maxlength="80" autocomplete="off" placeholder="For example: Sam"><small>Leave empty to use your name.</small></label>
        <label class="field"><span>Reply language</span><input id="pfLang" list="pfLangs" value="${esc(p.language)}" maxlength="40" autocomplete="off" placeholder="Match what I write"><datalist id="pfLangs">${LANGS.map(l => `<option value="${l}">`).join("")}</datalist><small>Meshpool answers in this language unless you write in another one.</small></label>
        <label class="field"><span>Default model for new chats</span><select id="pfModel">${ms.map(m => `<option value="${esc(m.id)}" ${m.id === S.models.default ? "selected" : ""}>${esc(modelLabel(m.id, ms))}</option>`).join("") || `<option value="">Add a key to see models</option>`}</select></label>
        <div style="display:flex;gap:8px"><button class="btn primary" id="pfSave">Save</button></div><div class="res" id="pfRes"></div>`;
      $("#pfSave").onclick = async () => {
        try {
          await post("/api/profile", {name: $("#pfName").value, address: $("#pfAddr").value, language: $("#pfLang").value});
          if ($("#pfModel").value && $("#pfModel").value !== S.models?.default) { S.models = await post("/api/models/default", {id: $("#pfModel").value}); S.draft.model = S.models.default; store("model", S.draft.model); renderBar(); }
          res("pfRes", "ok", "Saved. New chats use it.");
        } catch (e) { res("pfRes", "bad", e.message); }
      };
    } else if (sec === "notify") {
      const n = nset();
      b.innerHTML = `${close}<h3>Notifications</h3><p class="lead">Hear from Meshpool when you have switched to another window. Nothing shows while you are looking at Meshpool.</p>
        <ul class="rows2">${toggleRow("done", "When a chat finishes", "A notification when a long task is done.", n.done)}${toggleRow("wait", "When Meshpool needs you", "A notification when it asks a question or waits for your OK.", n.wait)}${toggleRow("sound", "Play a sound", "A short tone with each notification.", n.sound)}</ul>
        <div style="display:flex;gap:8px;margin-top:12px"><button class="btn" id="nTest">Send a test notification</button></div><div class="res" id="nRes"></div>`;
      $$("[data-t]", b).forEach(x => x.onchange = () => { const v = nset(); v[x.dataset.t] = x.checked; store("notify", v); });
      $("#nTest").onclick = () => { post("/api/notify", {title: "Meshpool", body: "This is a test notification."}).then(() => res("nRes", "ok", "Sent. If nothing appeared, allow notifications for Meshpool in your system settings.")).catch(e => res("nRes", "bad", e.message)); if (nset().sound) { const keep = document.hidden; try { const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(); o.connect(a.destination); o.frequency.value = 660; o.start(); o.stop(a.currentTime + .25); } catch (e) { /* no audio */ } } };
    } else if (sec === "caps") {
      const [feat, st] = await Promise.all([api("/api/capabilities"), loadSettings()]);
      const mode = get0("MH_AGENT_PERMISSIONS") || "ask";
      const row = f => toggleRow(f.id, f.name, f.desc, f.on, f.id === "memory" ? `<button class="linkbtn" data-go="personal" style="padding:0;text-align:left">Manage memory</button>` : "", !f.available);
      b.innerHTML = `${close}<h3>Capabilities</h3><p class="lead">What Meshpool can do for you. Changes apply to new chats; chats already open keep what they started with.</p>
        <ul class="rows2" id="capL">${feat.features.filter(f => !f.advanced).map(row).join("")}</ul>
        <h3 style="font-size:16px;margin:20px 0 4px">Always available</h3><p class="lead">Making Word documents, slides, spreadsheets and PDFs, reading your files, and running scripts are part of Meshpool. Meshpool asks first, unless you change that here.</p>
        <label class="field"><span>Editing files and running commands</span><select id="capMode">${Object.entries(MODES).map(([k, [lb, d]]) => `<option value="${k}" ${k === mode ? "selected" : ""}>${lb}: ${d.toLowerCase()}</option>`).join("")}</select><small>The starting point for new chats. You can change it for one chat from the settings button next to the message box.</small></label>
        <h3 style="font-size:16px;margin:20px 0 4px">Advanced</h3>
        <ul class="rows2">${feat.features.filter(f => f.advanced).map(row).join("")}</ul>
        <p class="lead" style="margin-top:12px">Which agent does the work, and how each one is set up: <button class="linkbtn" data-go="harnesses">Agents</button></p><div class="res" id="capRes"></div>`;
      $$("[data-t]", b).forEach(x => x.onchange = async () => {
        try { const r = await post("/api/capabilities", {id: x.dataset.t, on: x.checked}); if (x.dataset.t === "plan_first") S.draft.plan = x.checked; res("capRes", "ok", "Saved. New chats use it."); void r; renderBar(); }
        catch (e) { x.checked = !x.checked; res("capRes", "bad", e.message); }
      });
      $("#capMode").onchange = async () => { try { await post("/api/settings", {values: {MH_AGENT_PERMISSIONS: $("#capMode").value}}); S.draft.mode = $("#capMode").value; store("mode", S.draft.mode); renderBar(); res("capRes", "ok", "Saved. New chats start this way."); } catch (e) { res("capRes", "bad", e.message); } };
      $$("[data-go]", b).forEach(x => x.onclick = () => renderSection(x.dataset.go));
    } else if (sec === "billing") {
      const sp = await api("/api/spend").catch(() => ({billing_url: "https://meshpool.ai"}));
      const key = (S.settings?.fields || []).find(f => f.key === "MESHPOOL_API_KEY") || {};
      b.innerHTML = `${close}<h3>Plan and billing</h3><p class="lead">Plans, payment methods, invoices and keys are managed on your Meshpool account. Meshpool does not handle payments in the app.</p>
        <div class="card" style="max-width:480px"><div class="chd"><span class="ci">${ICON.globe}</span><h3>Meshpool account</h3></div>
          <p class="muted">${key.set ? "A Meshpool key is saved on this computer." : "No Meshpool key is saved yet."} ${key.set ? "Open your account to add credit, see invoices or create another key." : "Open your account to get one."}</p>
          <div class="row"><button class="btn primary" id="bOpen">Open your account</button><button class="btn" id="bUsage">See usage</button></div></div>`;
      $("#bOpen").onclick = () => openExternal(sp.billing_url || "https://meshpool.ai");
      $("#bUsage").onclick = () => renderSection("usage");
    } else if (sec === "data") {
      const d = await api("/api/data");
      b.innerHTML = `${close}<h3>Your data</h3><p class="lead">Chats, memory, projects and settings are stored on this computer. Only what you send to the model service leaves it.</p>
        <div class="card" style="max-width:560px"><div class="chd"><span class="ci">${ICON.folder}</span><h3>Where it is</h3></div><p class="muted" style="word-break:break-all">${esc(d.folder)}</p><p class="muted">${d.chats} chat${d.chats === 1 ? "" : "s"}, ${d.bytes > 1048576 ? (d.bytes / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(d.bytes / 1024)) + " KB"} of history.</p><div class="row"><button class="btn" id="dOpen">Open the folder</button></div></div>
        <div class="card" style="max-width:560px;margin-top:12px"><div class="chd"><span class="ci">${ICON.upload}</span><h3>Export</h3></div><p class="muted">A zip with your chats, memory and project settings. Project files you added are not included.</p><div class="row"><button class="btn" id="dExport">Export my data</button></div></div>
        <div class="card" style="max-width:560px;margin-top:12px"><div class="chd"><span class="ci">${ICON.trash}</span><h3>Delete chats</h3></div><p class="muted">Removes every chat and the files made in chats without a folder. Memory, projects and settings stay.</p><div class="row"><button class="btn danger" id="dClear">Delete all chats</button></div></div><div class="res" id="dRes"></div>`;
      $("#dOpen").onclick = () => post("/api/open-data").catch(e => res("dRes", "bad", e.message));
      $("#dExport").onclick = async () => {
        try { const r = await post("/api/data", {action: "export"}); const a = Object.assign(document.createElement("a"), {href: "data:application/zip;base64," + r.data, download: r.name}); document.body.appendChild(a); a.click(); a.remove(); res("dRes", "ok", "Exported. Check your downloads."); }
        catch (e) { res("dRes", "bad", e.message); }
      };
      $("#dClear").onclick = async () => {
        if (!await ask({title: "Delete all chats?", text: `All ${d.chats} chats are removed from this computer. This can’t be undone. Memory, projects and settings stay.`, ok: "Delete all chats", cancel: "Keep them", danger: true})) return;
        try { const r = await post("/api/data", {action: "clear_chats"}); await loadThreads(); newThread(); res("dRes", "ok", `${r.deleted} chat${r.deleted === 1 ? "" : "s"} deleted.`); } catch (e) { res("dRes", "bad", e.message); }
      };
    } else if (sec === "usage") {
      await base(sec);
      try {
        const [sp] = await Promise.all([api("/api/spend")]);
        const k = sp.key, days = sp.days || [];
        const pct = k.available && k.budget ? Math.min(100, Math.round(100 * k.spent / k.budget)) : null;
        const money = v => "$" + Number(v).toFixed(v < 10 ? 2 : 0);
        const card = `<div class="card" style="max-width:560px;margin:0 0 16px"><div class="chd"><span class="ci">${ICON.bolt}</span><h3>Spent on your Meshpool key</h3></div>
          ${k.available ? `<p style="font-size:var(--fs-xl);font-weight:600;margin:0 0 4px">${money(k.spent)}${k.budget ? ` <span class="muted" style="font-size:var(--fs-s);font-weight:400">of ${money(k.budget)}</span>` : ""}</p>${pct !== null ? `<div class="spbar ${pct >= 90 ? "bad" : pct >= 70 ? "warn" : ""}" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Budget used"><i style="width:${pct}%"></i></div>` : `<p class="muted">No spending limit is set on this key.</p>`}`
            : `<p class="muted">Your model service doesn’t report spending, or no Meshpool key is saved. Your account page has the full picture.</p>`}
          ${days.length ? `<div class="spsub" style="margin-top:12px">By day, on this computer</div><ul class="spl">${days.slice(0, 7).map(d => `<li><span class="cn">${esc(d.day)}</span><span class="cs">${fmt(d.in + d.out)} tokens · ${d.calls} calls</span></li>`).join("")}</ul>` : ""}</div>`;
        const h3 = $("h3", b); if (h3) h3.insertAdjacentHTML("afterend", ""); b.querySelector(".lead")?.insertAdjacentHTML("afterend", card);
      } catch (e) { /* the page below still works */ }
    } else {
      return base(sec);
    }
    done();
    b.scrollTop = 0;
  };
})();
