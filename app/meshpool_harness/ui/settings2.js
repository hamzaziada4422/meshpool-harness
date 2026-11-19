/* Settings: a modal over the current page. A left navigation (with search) of pages in groups, a thin header with a close
   button, and the page on the right. Every page that existed before is still here (renderSection in app.js draws the
   old ones, the code below draws the new ones: Models and providers, Appearance, Profile, Notifications, Capabilities,
   Plan and billing, Your data). The address hash follows the page: #settings/appearance. Loaded after app.js and
   before company.js, which adds Company and brand on top. */
"use strict";
// Navigation: [page, label, icon, [the sections it holds]]. A page with several sections gets tabs under the header.
const SET_NAV = [
  [["account", "Account", "user", ["profile"]], ["billing", "Billing", "card", ["billing"]], ["usage", "Usage", "bolt", ["usage"]]],
  [["appearance", "Appearance", "palette", ["appearance"]], ["personal", "Your Meshpool", "brain", ["personal", "import"]], ["caps", "Capabilities", "tool", ["caps", "web"]],
   ["notify", "Notifications", "bell", ["notify"]], ["privacy", "Privacy", "shield", ["data", "permissions"]]],
  [["plugins", "Plugins", "plug", ["connectors", "skills", "plugins", "mcp"]], ["code", "Code", "codebr", ["terminal", "advanced"]]],
  [["models", "Models and providers", "cpu", ["general", "models", "harnesses"]], ["scheduled", "Scheduled tasks", "clock", ["scheduled"]],
   ["company", "Company and brand", "stack", ["company"]], ["about", "About and updates", "help", ["about"]]],
];
const SET_GROUPS = SET_NAV.flat().map(([k, lb, , subs]) => [k, lb, subs]);       // kept for company.js and old callers
const SET_NAMES = {profile: "Profile", appearance: "Appearance", notify: "Notifications", caps: "Features", personal: "Instructions and memory", web: "Web search",
  connectors: "Directory", skills: "Skills", plugins: "Plugins", mcp: "Custom tools", general: "Connection", models: "Models", harnesses: "Agents",
  usage: "Usage", billing: "Plan and billing", permissions: "Permissions", data: "Your data", import: "Import chats",
  about: "About and updates", scheduled: "Scheduled tasks", terminal: "Terminal command", advanced: "More options"};
// company.js adds "Company and brand" to this list, as it did before.
const SECTIONS = Object.entries(SET_NAMES);
const setName = k => SET_NAMES[k] || (SECTIONS.find(x => x[0] === k) || [])[1] || k;

function groupOfSection(sec) { return SET_GROUPS.find(g => g[2].includes(sec)) || SET_GROUPS.find(g => g[0] === sec) || SET_GROUPS[0]; }
function markSettings(sec) {
  const g = groupOfSection(sec);
  $$(".mnav [data-g]").forEach(x => x.setAttribute("aria-selected", x.dataset.g === g[0]));
  const st = $("#subtabs"); if (!st) return;
  const subs = g[2].filter(k => SECTIONS.some(x => x[0] === k));
  st.hidden = subs.length < 2;
  if (st.dataset.g !== g[0]) { st.dataset.g = g[0]; st.innerHTML = subs.map(k => `<button role="tab" data-sub="${k}">${esc(setName(k))}</button>`).join(""); $$("[data-sub]", st).forEach(b => b.onclick = () => renderSection(b.dataset.sub)); }
  $$("[data-sub]", st).forEach(b => b.setAttribute("aria-selected", b.dataset.sub === sec));
  try { history.replaceState(null, "", "#settings/" + g[0]); } catch (e) { /* file or sandboxed frames */ }
}
function closeSettings() { $(".scrim:not(.dlg-scrim):not(.srch-scrim)")?.remove(); try { if (/^#settings/.test(location.hash)) history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* ignore */ } }
function openSettings(sec = "profile") {
  closePop(); closeCtx(); closeSearch?.();
  $(".scrim:not(.dlg-scrim)")?.remove();
  const g0 = groupOfSection(sec);
  const sc = document.createElement("div"); sc.className = "scrim";
  const item = ([k, lb, ic, subs]) => `<button data-g="${k}" data-first="${subs[0]}" data-l="${esc((lb + " " + subs.map(setName).join(" ")).toLowerCase())}" aria-selected="${k === g0[0]}">${ICON[ic] || ""}<span>${lb}</span></button>`;
  sc.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="Settings"><header class="mhead2"><h2 id="mtitle">Settings</h2><button class="iconbtn mx" aria-label="Close settings">${ICON.x}</button></header>
    <nav class="mnav" aria-label="Settings sections"><label class="msearch">${ICON.search}<input id="mnavq" placeholder="Search" aria-label="Search settings" autocomplete="off" spellcheck="false"></label>
      ${SET_NAV.map(grp => `<div class="mgrp">${grp.map(item).join("")}</div>`).join("")}
      <span class="sp"></span>${MP.desktop && !S.settings?.native ? `<button id="quitApp">${ICON.logout}<span>Quit Meshpool</span></button>` : ""}
      <button class="mext" id="apiKeys">API keys<span>${ICON.ext}</span></button></nav>
    <div class="mmain"><div class="subtabs" id="subtabs" role="tablist" aria-label="Pages"></div><div class="mbody" id="mbody"></div></div></div>`;
  document.body.appendChild(sc);
  sc.addEventListener("mousedown", e => { if (e.target === sc) closeSettings(); });
  $(".mx", sc).onclick = closeSettings;
  $$(".mnav [data-g]", sc).forEach(b => b.onclick = () => renderSection(b.dataset.first));
  $("#apiKeys", sc).onclick = () => openExternal(S.acct?.keys_url || "https://meshpool.ai");
  $("#mnavq", sc).oninput = e => { const q = e.target.value.trim().toLowerCase(); $$(".mnav [data-g]", sc).forEach(b => b.hidden = !!q && !b.dataset.l.includes(q)); $$(".mgrp", sc).forEach(g => g.hidden = !$("[data-g]:not([hidden])", g)); };
  $("#mnavq", sc).onkeydown = e => { if (e.key === "Enter") { const first = $(".mnav [data-g]:not([hidden])", sc); if (first) first.click(); } };
  $("#quitApp")?.addEventListener("click", () => post("/api/quit").then(() => document.body.innerHTML = `<div class="onb"><div class="onb-card"><h1>Meshpool has stopped</h1><p>You can close this window.</p></div></div>`));
  renderSection(sec);
  $("#mnavq", sc).focus();
}
addEventListener("hashchange", () => { const m = /^#settings\/([\w-]+)/.exec(location.hash); if (m && !$(".modal")) openSettings(m[1]); });

/* ---------- Appearance ---------- */
function setRow(title, desc, control) { return `<div class="sr-row"><div class="sr-t"><b>${esc(title)}</b>${desc ? `<small>${esc(desc)}</small>` : ""}</div><div class="sr-c">${control}</div></div>`; }
const selOpts = (id, opts, cur) => `<select class="sr-sel" id="${id}">${opts.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
function appearancePage(b, close) {
  const keys = [["Esc", "Stop the running task, or close what is open"], [`${MOD}N`, "New chat"], [`${MOD}K`, "Search"], [`${MOD}B`, "Show or hide the sidebar"], [`${MOD}U`, "Add files"], [`${MOD}E`, "Show or fold steps"],
    [`${MOD}⇧D`, "Changes panel"], [`${MOD}J`, "Terminal"], [`${MOD},`, "Settings"], ["Enter", "Send"], ["Shift+Enter", "New line"], ["/", "Commands and skills"], ["$", "Skills"],
    ["P  R  A  D", "In a chat's title menu: pin, rename, archive, delete"], ["1 – 4", "In the model menu: pick that model"]];
  const th = store("theme") || "system";
  b.innerHTML = `${close}<h3>General</h3>
    ${setRow("Theme", "", `<div class="seg" role="radiogroup" aria-label="Theme">${[["system", "monitor", "Match the system"], ["light", "sun", "Light"], ["dark", "moon", "Dark"]].map(([v, ic, l]) => `<button role="radio" aria-checked="${th === v}" data-th="${v}" data-tip="${l}" aria-label="${l}">${ICON[ic]}</button>`).join("")}</div>`)}
    ${setRow("Interface font", "", selOpts("apFont", [["plex", "IBM Plex Sans"], ["system", "System font"]], store("font") || "plex"))}
    ${setRow("Response font", "", selOpts("apRFont", [["match", "Match interface"], ["system", "System font"], ["serif", "Serif"]], store("rfont") || "match"))}
    ${setRow("Transcript width", "", selOpts("apWidth", [["narrow", "Narrow"], ["normal", "Normal"], ["wide", "Wide"]], store("width") || "narrow"))}
    ${setRow("Text size", "", selOpts("apSize", [["small", "Small"], ["normal", "Normal"], ["large", "Large"]], store("size") || "normal"))}
    ${setRow("Density", "Compact fits more chats in the sidebar and tightens the spacing.", selOpts("apDensity", [["comfortable", "Comfortable"], ["compact", "Compact"]], store("density") || "comfortable"))}
    ${setRow("Steps", "Show every step as Meshpool works, or fold them into a summary.", selOpts("apSteps", [["1", "Show every step"], ["0", "Fold into a summary"]], S.steps ? "1" : "0"))}
    <h4 class="sr-h">Accessibility</h4>
    ${setRow("Motion", "Reduce motion in the app, or follow your system setting.", selOpts("apMotion", [["system", "System"], ["reduce", "Reduce"]], store("motion") || "system"))}
    ${setRow("Contrast", "Increase contrast for text and lines, or follow your system setting.", selOpts("apContrast", [["system", "System"], ["high", "High"]], store("contrast") || "system"))}
    ${setRow("Popups", "Keep popups on screen until you close them. Otherwise they go away after a few seconds.", selOpts("apPopups", [["auto", "Auto-dismiss"], ["keep", "Keep until closed"]], store("popups") || "auto"))}
    <h4 class="sr-h">Code</h4>
    ${setRow("Code font", "A font installed on this computer. Leave empty for the default.", `<input class="sr-in" id="apCodeFont" value="${esc(store("codefont") || "")}" placeholder="IBM Plex Mono" spellcheck="false" autocomplete="off">`)}
    ${setRow("Code font size", "", selOpts("apCodeSize", [["12", "12 px"], ["13", "13 px (default)"], ["14", "14 px"], ["16", "16 px"]], String(store("codesize") || 13)))}
    <h4 class="sr-h">Keyboard shortcuts</h4><div class="keys">${keys.map(([k, d]) => `<kbd>${esc(k)}</kbd><span>${esc(d)}</span>`).join("")}</div>`;
  const on = (id, fn) => { $("#" + id).onchange = e => { fn(e.target.value); applyAppearance(); }; };
  $$("[data-th]", b).forEach(x => x.onclick = () => { store("theme", x.dataset.th); applyAppearance(); $$("[data-th]", b).forEach(y => y.setAttribute("aria-checked", String(y === x))); });
  on("apFont", v => store("font", v)); on("apRFont", v => store("rfont", v)); on("apWidth", v => store("width", v)); on("apSize", v => store("size", v)); on("apDensity", v => store("density", v));
  on("apMotion", v => store("motion", v)); on("apContrast", v => store("contrast", v)); on("apPopups", v => store("popups", v)); on("apCodeSize", v => store("codesize", +v));
  $("#apCodeFont").onchange = e => { store("codefont", e.target.value.trim()); applyAppearance(); };
  $("#apSteps").onchange = () => { if (($("#apSteps").value === "1") !== !!S.steps) toggleSteps(); };
}

/* ---------- Models and providers > Connection ---------- */
// Base URL and key -> Test connection -> the endpoint's models -> pick the default -> Save. Logins already on this
// computer come first. The key is never shown: a saved one reads "•••• 1234" with a Change link.
async function providerPage(b, close) {
  b.innerHTML = `${close}<h3>Models and providers</h3><p class="lead">Connect Meshpool to a model endpoint: yours, or a login you already have on this computer.</p><div class="pnote">Loading…</div>`;
  let cur, lg;
  try { [cur, lg] = await Promise.all([api("/api/provider"), api("/api/provider/logins").catch(() => ({logins: []})), loadSettings()]); }
  catch (e) { b.innerHTML = `${close}<h3>Models and providers</h3><div class="res bad">${esc(e.message)}</div>`; return; }
  const st = {base: cur.base_url || "", wire: cur.wire || "auto", env: "", changeKey: !cur.key_set, models: null, picked: "", typed: "", q: "", ok: false};
  const logins = (lg.logins || []);
  const row = (title, text, btn, id, badge) => `<li><span class="tg"><b>${title}</b><small>${text}</small></span>${badge ? `<span class="badge added">${badge}</span>` : ""}${btn ? `<button class="btn ${badge ? "" : "primary"}" id="${id}">${btn}</button>` : ""}</li>`;
  const found = [];
  for (const l of logins) {
    if (l.kind === "chatgpt") {
      if (!l.found) found.push(row("ChatGPT", "Use your ChatGPT plan with Meshpool's agents.", "Sign in with ChatGPT", "lgChatgptIn"));
      else if (l.usable === false) found.push(row("ChatGPT", esc(l.note || "Signed in, but this sign-in can't be used here."), "", ""));
      else found.push(row("ChatGPT", `Signed in with the Codex CLI${l.account ? " as " + esc(l.account) : ""}${l.plan ? " · " + esc(l.plan) : ""}`, l.active ? "Using" : "Use", "lgChatgpt", l.active ? "In use" : ""));
    } else if (l.kind === "claude_code" && l.found) found.push(row("Claude Code is installed", `${esc(l.version || "")} ${l.path ? "· " + esc(shortPath(l.path)) : ""}`, l.active ? "Using" : "Use as an agent", "lgCode", l.active ? "In use" : ""));
    else if (l.kind === "env_key" && l.found) found.push(row(`${esc(l.env)} found in your environment`, `${esc(l.key_hint || "")}${l.base_url ? " · " + esc(PV_host(l.base_url)) : ""}`, "Use", "lgEnv-" + l.env));
  }
  const keyHint = cur.key_hint ? "•••• " + String(cur.key_hint).replace(/[^A-Za-z0-9]/g, "").slice(-4) : "••••";
  b.innerHTML = `${close}<h3>Models and providers</h3><p class="lead">Connect Meshpool to a model endpoint: yours, or a login you already have on this computer.</p>
    ${found.length ? `<h4 class="sr-h first">Use an existing login</h4><ul class="rows2">${found.join("")}</ul>` : ""}
    <h4 class="sr-h ${found.length ? "" : "first"}">Connection</h4>
    <div class="provnow">${cur.provider === "chatgpt" ? "Using your ChatGPT sign-in." : cur.default_model ? `Using <b>${esc(cur.default_model)}</b> on ${esc(cur.host || PV_host(cur.base_url))}.` : "No default model chosen yet."}</div>
    <label class="field"><span>Base URL</span><input id="pvBase" spellcheck="false" autocomplete="off" value="${esc(st.base)}" placeholder="https://api.example.com/v1"><small>The address of your model service. It usually ends in /v1.</small></label>
    <div class="field"><span>API key</span><span class="keyrow" id="pvKeyRow">${st.changeKey ? `<input id="pvKey" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your key"><button class="btn" id="pvShow" type="button">Show</button>` : `<input id="pvMask" value="${esc(keyHint)}" readonly aria-label="Saved key"><button class="btn" id="pvChange" type="button">Change</button>`}</span></div>
    <div class="pvact"><button class="btn primary" id="pvTest">Test connection</button><span class="pvres" id="pvRes" role="status"></span></div>
    <div id="pvModels" hidden></div>
    <div class="pvact"><button class="btn primary" id="pvSave" disabled>Save</button><span class="pvres" id="pvSaveRes" role="status"></span></div>`;
  const say = (id, cls, t) => { const el = $("#" + id, b); el.className = "pvres " + cls; el.textContent = t; };
  const key = () => ($("#pvKey", b)?.value || "").trim();
  const drawModels = () => {
    const box = $("#pvModels", b); box.hidden = !st.models;
    if (!st.models) return;
    const q = st.q.toLowerCase(), list = st.models.filter(m => !q || (m.id + " " + (m.name || "")).toLowerCase().includes(q));
    box.innerHTML = st.models.length
      ? `<h4 class="sr-h">Default model</h4><p class="lead small">Pick the model new chats start with. You can change it per chat later.</p><label class="gsearch full">${ICON.search}<input id="pvQ" placeholder="Search ${st.models.length} models" value="${esc(st.q)}" autocomplete="off" aria-label="Search models"></label>
         <div class="pvlist" role="radiogroup" aria-label="Default model">${list.map(m => `<button class="pvm" role="radio" aria-checked="${m.id === st.picked}" data-m="${esc(m.id)}"><span>${esc(m.name || m.id)}${m.name && m.name !== m.id ? `<small>${esc(m.id)}</small>` : ""}</span>${m.context ? `<em>${m.context >= 1000 ? Math.round(m.context / 1000) + "K" : m.context}</em>` : ""}${m.id === st.picked ? `<i class="vchk">${ICON.check}</i>` : ""}</button>`).join("") || `<div class="pnote">No models match.</div>`}</div>`
      : `<h4 class="sr-h">Default model</h4><p class="lead small">This endpoint connected but doesn't list its models. Type the name of the one to use.</p><label class="field"><span>Model name</span><input id="pvTyped" spellcheck="false" autocomplete="off" placeholder="for example gpt-4o" value="${esc(st.typed)}"></label>`;
    $("#pvQ", box)?.addEventListener("input", e => { const pos = e.target.selectionStart; st.q = e.target.value; drawModels(); const i = $("#pvQ", box); i.focus(); i.setSelectionRange(pos, pos); });
    $$("[data-m]", box).forEach(x => x.onclick = () => { st.picked = x.dataset.m; drawModels(); gate(); });
    $("#pvTyped", box)?.addEventListener("input", e => { st.typed = e.target.value.trim(); gate(); });
  };
  const gate = () => { $("#pvSave", b).disabled = !(st.ok && (st.models?.length ? st.picked : st.typed)); };
  const touched = () => { st.ok = false; st.models = null; st.picked = ""; $("#pvModels", b).hidden = true; $("#pvRes", b).textContent = ""; gate(); };
  $("#pvBase", b).oninput = e => { st.base = e.target.value.trim(); st.env = ""; touched(); };
  b.addEventListener("input", e => { if (e.target.id === "pvKey") touched(); });
  b.addEventListener("click", e => {
    if (e.target.closest("#pvChange")) { st.changeKey = true; $("#pvKeyRow", b).innerHTML = `<input id="pvKey" type="password" autocomplete="off" spellcheck="false" placeholder="Paste the new key"><button class="btn" id="pvShow" type="button">Show</button>`; $("#pvKey", b).focus(); touched(); }
    if (e.target.closest("#pvShow")) { const i = $("#pvKey", b); i.type = i.type === "password" ? "text" : "password"; $("#pvShow", b).textContent = i.type === "password" ? "Show" : "Hide"; }
  });
  $("#pvTest", b).onclick = async () => {
    if (!st.base) { $("#pvBase", b).focus(); return say("pvRes", "bad", "Enter the base URL first."); }
    const btn = $("#pvTest", b); btn.disabled = true; say("pvRes", "", "Testing…");
    try {
      const r = await post("/api/provider/test", {base_url: st.base, ...(key() ? {api_key: key()} : {}), wire: st.wire, ...(st.env ? {env: st.env} : {})});
      if (!r.ok) { st.ok = false; st.models = null; drawModels(); say("pvRes", "bad", r.error || "That didn't work."); }
      else {
        st.ok = true; st.base = r.base_url || st.base; st.wire = r.wire || st.wire; st.models = r.models || []; $("#pvBase", b).value = st.base;
        if (st.picked && !st.models.some(m => m.id === st.picked)) st.picked = "";
        say("pvRes", "ok", `Connected in ${r.latency_ms} ms.${st.models.length ? ` ${st.models.length} models available.` : ""}`);
        drawModels();
      }
    } catch (err) { say("pvRes", "bad", err.message); }
    btn.disabled = false; gate();
  };
  $("#pvSave", b).onclick = async () => {
    const model = st.models?.length ? st.picked : st.typed; if (!model) return;
    let confirm_env = false;
    if (st.env) { if (!await ask({title: `Use ${st.env} from your environment?`, text: `Meshpool saves this key in its settings on this computer, and sends it only to ${PV_host(st.base)}.`, ok: "Use this key"})) return; confirm_env = true; }
    const btn = $("#pvSave", b); btn.disabled = true; say("pvSaveRes", "", "Saving…");
    try {
      await post("/api/provider/save", {base_url: st.base, ...(key() ? {api_key: key()} : {}), wire: st.wire, default_model: model, allow_custom: !st.models?.length, ...(st.env ? {env: st.env, confirm_env} : {})});
      await loadSettings(); await loadModels(true); await loadHarnesses(); S.draft.model = S.models.default; store("model", S.draft.model); renderBar(); renderHero?.();
      toast("Saved. New chats use " + model + "."); providerPage(b, close);
    } catch (err) { say("pvSaveRes", "bad", err.message); btn.disabled = false; }
  };
  const useLogin = async kind => { try { const r = await post("/api/provider/use", {kind}); await loadSettings(); await loadModels(true); await loadHarnesses(); renderBar(); toast(r.message || "Switched."); providerPage(b, close); } catch (err) { toast(err.message, 6000); } };
  $("#lgChatgpt", b)?.addEventListener("click", () => useLogin("chatgpt"));
  $("#lgCode", b)?.addEventListener("click", () => useLogin("claude_code"));
  $("#lgChatgptIn", b)?.addEventListener("click", async () => { try { const r = await post("/api/harness-login", {id: "codex"}); toast(r.message || "Finish signing in in your browser.", 6000); setTimeout(() => { if ($("#pvBase", b)) providerPage(b, close); }, 6000); } catch (err) { toast(err.message, 6000); } });
  for (const l of logins.filter(x => x.kind === "env_key" && x.found)) $("#lgEnv-" + l.env, b)?.addEventListener("click", () => {
    st.env = l.env; st.base = l.base_url || st.base; st.wire = l.wire || "auto"; $("#pvBase", b).value = st.base;
    $("#pvKeyRow", b).innerHTML = `<input id="pvMask" value="${esc(l.env)}: ${esc(l.key_hint || "••••")}" readonly aria-label="Key from your environment"><span class="pvnote">from your environment</span>`;
    st.ok = false; st.models = null; $("#pvModels", b).hidden = true; $("#pvTest", b).click();
  });
}
const PV_host = u => { try { return new URL(u).host; } catch (e) { return String(u || ""); } };

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
    const done = () => $(".mclose", b)?.addEventListener("click", closeSettings);
    const res = (id, cls, t) => { const el = $("#" + id); if (el) { el.className = "res " + cls; el.textContent = t; } };
    if (sec === "general") { await providerPage(b, close); b.scrollTop = 0; return; }
    if (sec === "appearance") { appearancePage(b, close); done(); b.scrollTop = 0; return; }
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
        <label class="field"><span>Harness for new chats</span><select id="capHarness"><option value="meshpool-auto">Auto (recommended)</option>${harnessChoices().map(h => `<option value="${esc(h.id)}" ${h.id === store("harness_default") ? "selected" : ""}>${esc(h.name)}</option>`).join("")}</select><small>Auto picks the agent that suits each request. Choose one here to use it for every new chat; you can still change it for one chat from the model menu.</small></label>
        <h3 style="font-size:16px;margin:20px 0 4px">Advanced</h3>
        <ul class="rows2">${feat.features.filter(f => f.advanced).map(row).join("")}</ul>
        <p class="lead" style="margin-top:12px">Which agent does the work, and how each one is set up: <button class="linkbtn" data-go="harnesses">Agents</button></p><div class="res" id="capRes"></div>`;
      $$("[data-t]", b).forEach(x => x.onchange = async () => {
        try { const r = await post("/api/capabilities", {id: x.dataset.t, on: x.checked}); if (r?.features) S.caps = r.features; if (x.dataset.t === "plan_first") S.draft.plan = x.checked; res("capRes", "ok", "Saved. New chats use it."); void r; renderBar(); }
        catch (e) { x.checked = !x.checked; res("capRes", "bad", e.message); }
      });
      $("#capHarness").onchange = () => { const v = $("#capHarness").value; store("harness_default", v === "meshpool-auto" ? null : v); if (!S.cur) { S.draft.harness = modeHarness(); renderBar(); renderHero(); } res("capRes", "ok", "Saved. New chats use it."); };
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
          ${days.length ? `<div class="spsub" style="margin-top:12px">By day, on this computer</div><ul class="spl">${days.slice(0, 7).map(d => `<li><span class="cn">${esc(d.day)}</span><span class="cs">${fmt(d.in + d.out)} tokens · ${d.calls} call${d.calls === 1 ? "" : "s"}</span></li>`).join("")}</ul>` : ""}</div>`;
        const h3 = $("h3", b); if (h3) h3.insertAdjacentHTML("afterend", ""); b.querySelector(".lead")?.insertAdjacentHTML("afterend", card);
      } catch (e) { /* the page below still works */ }
    } else {
      return base(sec);
    }
    done();
    b.scrollTop = 0;
  };
})();
