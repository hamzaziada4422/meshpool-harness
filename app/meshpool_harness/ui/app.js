/* Meshpool desktop UI. Plain JS, no build step. Talks to the local server (server.py) with the per-launch token. */
"use strict";
const MP = window.MP || {token: "", desktop: false};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = n => Number(n || 0).toLocaleString("en-US");
const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
const MOD = isMac ? "⌘" : "Ctrl+";
// The route chip's menu: pick the harness for this chat (applies from the next message).
const ROUTES = [["chat", "Chat", "Quick answers, rewrites, questions"], ["research", "Research", "Sourced answers from the web"],
  ["data", "Data", "Analyse files and numbers"], ["docs", "Documents", "Word, Excel, PowerPoint, PDF"],
  ["code.debug", "Debug", "Find and fix what is broken"], ["code.build", "Build", "Write new code"],
  ["code.review", "Review", "Review changes, no edits"], ["code.refactor", "Refactor", "Restructure without changing behaviour"],
  ["code.test", "Tests", "Write and run tests"], ["code.terminal", "Terminal", "Install, configure, deploy"],
  ["browser", "Browser", "Websites, forms, sign-ins"], ["automation", "Automation", "Scheduled and repeating work"]];
function routeMenu(anchor) {
  document.querySelector(".routemenu")?.remove();
  const m = document.createElement("div"); m.className = "routemenu"; m.setAttribute("role", "menu");
  m.innerHTML = ROUTES.map(([id, name, d]) => `<button role="menuitem" data-spec="${id}"><b>${name}</b><small>${d}</small></button>`).join("");
  anchor.appendChild(m);
  const close = e => { if (!m.contains(e.target)) { m.remove(); document.removeEventListener("mousedown", close); } };
  setTimeout(() => document.addEventListener("mousedown", close), 0);
  m.addEventListener("keydown", e => { if (e.key === "Escape") m.remove(); });
  m.querySelectorAll("button").forEach(b => b.onclick = async () => {
    m.remove();
    const c = S.cur; if (!c) return;
    try { await post(`/api/agent/${c.id}/route`, {spec: b.dataset.spec, since: 1e9}); clearTimeout(c.pollT); poll(); }
    catch (e) { toast(e.message); }
  });
  m.querySelector("button")?.focus();
}
function ago(t) {
  if (typeof t === "string") t = isNaN(Number(t)) ? Date.parse(t) / 1000 : Number(t);   // ISO or numeric text
  if (!t || isNaN(t)) return "";
  if (t > 1e12) t = t / 1000;                                                           // milliseconds
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "now"; if (s < 3600) return Math.floor(s / 60) + "m"; if (s < 86400) return Math.floor(s / 3600) + "h";
  if (s < 86400 * 7) return Math.floor(s / 86400) + "d"; return new Date(t * 1000).toLocaleDateString(undefined, {month: "short", day: "numeric"});
}
// Paths as people read them: ~/Documents/report instead of /Users/name/Documents/report.
// Paths inside the project become buttons that open the file in Meshpool.
function linkFiles(html) {
  const root = String(curRoot() || "").replace(/[\\/]+$/, ""); if (!root) return html;
  const rx = new RegExp(esc(root).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[\\\\/]([^\\n<\"'`)]+?\\.(?:docx|doc|xlsx|xls|pptx|ppt|odt|odp|ods|pdf|csv|tsv|md|html|htm|txt|png|jpe?g|gif|webp|svg|py|js|ts|json)(?![\\w]))", "g");
  const img = /\.(png|jpe?g|gif|webp)$/i, tok = encodeURIComponent((window.MP && MP.token) || "");
  return html.replace(rx, (m, rel) => `<button class="flink" data-open="${rel}" title="Preview ${rel}">${ICON.files}${rel}</button>` +
    (img.test(rel) && tok ? `<button class="flink rimgb" data-open="${rel}" title="Open ${rel}"><img class="rimg" loading="lazy" alt="${rel}" src="/raw?t=${tok}&root=${encodeURIComponent(root)}&path=${encodeURIComponent(rel.replace(/&amp;/g, "&"))}" onerror="this.parentNode.remove()"></button>` : ""));   // a picture the reply names is shown in the reply
}
// Windows: PowerShell, Command Prompt or Git Bash for commands you type; Mac and Linux: your login shell.
function termShellPicker() {
  const sh = S.settings?.shells; if (!sh || Object.keys(sh.shells || {}).length < 2) return `<span style="color:var(--ink-3);font-family:var(--mono)">$</span>`;
  const cur = store("term_shell") || sh.default;
  return `<select id="tshell" aria-label="Shell" onchange="store('term_shell', this.value)">${Object.entries(sh.shells).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
}
// "deepseek-v4.1-flash" reads as "DeepSeek V4.1 Flash"; unknown ids are tidied the same way.
// A model id carries its provider (providers.py): "chatgpt:gpt-6.1-sol", or no prefix for the key endpoint.
const PROV_PREFIX = ["chatgpt"];
function provOf(id) { const [h, ...r] = String(id || "").split(":"); return r.length && PROV_PREFIX.includes(h) ? h : "key"; }
function bareModel(id) { const p = provOf(id); return p === "key" ? String(id || "") : String(id).slice(p.length + 1); }
function modelLabel(id, list) {                 // the name, plus the id when another model in the list shares it
  const n = modelName(id); return (list || []).filter(m => modelName(m.id || m) === n).length > 1 ? `${n} · ${bareModel(id)}` : n;
}
function modelName(id) {
  const known = {deepseek: "DeepSeek", gpt: "GPT", qwen: "Qwen", glm: "GLM", kimi: "Kimi", llama: "Llama", mistral: "Mistral", grok: "Grok", openai: "OpenAI", minimax: "MiniMax"};
  return bareModel(id).split("/").pop().split(/[-_ ]+/).map(w => known[w.toLowerCase()] || (/^v?\d/.test(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1))).join(" ").replace(/(\d) (?=\d)/g, "$1.");
}
function connMsg(d) {
  d = String(d || "");
  if (/reject|401|403/i.test(d)) return "The endpoint didn't accept this key. Check that you copied all of it.";
  if (/read operation timed out/i.test(d) && !/urlopen error|refused|resolve|getaddrinfo|ssl|certificate/i.test(d)) return "The endpoint took the connection but didn't answer in time: the gateway is up but isn't serving this key right now. Running tasks wait for it and carry on by themselves. Try the test again in a minute.";
  if (/429|rate.?limit|too many|parallel/i.test(d)) return "The endpoint says this key is at its limit right now (too many requests at once). Running tasks wait and carry on. Try again in a minute.";
  if (/HTTP 0|timed out|connection|refused|resolve|getaddrinfo/i.test(d)) return "Can't reach that address. Check the base URL and your internet connection.";
  if (/404/.test(d)) return "That address answered, but it isn't a model endpoint. Check the base URL (it usually ends in /v1).";
  return "The connection test didn't work: " + d.slice(0, 160);
}
function get0(k) { return ((S.settings?.fields || []).find(x => x.key === k) || {}).value; }
function shortPath(p) {
  p = String(p || ""); const h = S.settings?.user_home;
  if (h && (p === h || p.startsWith(h + "/") || p.startsWith(h + "\\"))) p = "~" + p.slice(h.length);
  const parts = p.split(/[\\/]/);
  return p.length > 48 && parts.length > 3 ? "…/" + parts.slice(-2).join("/") : p;
}
const RES = id => document.getElementById(id) || {className: "", textContent: "", innerHTML: ""};   // results outlive a re-render
// A folder is a non-empty path string. Anything else (an event a handler passed in, or {"isTrusted":true} that an
// earlier version saved when New chat was clicked) means no folder, so a saved bad value cannot break the app again.
const folderOrNone = v => typeof v === "string" ? v : "";
function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("mp_" + k)); localStorage.setItem("mp_" + k, JSON.stringify(v)); } catch (e) { return null; } }

const I = (p, w = 16) => `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
const ICON = {
  pin: I('<path d="M12 17v5"/><path d="M9 4h6l-1 5.5 3 3.5H7l3-3.5z"/>', 15), trash: I('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>', 15),
  archive: I('<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11h14V8M10 12h4"/>', 15), book: I('<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/>', 15),
  brain: I('<path d="M12 3v3M12 18v3M3 12h3M18 12h3M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/><circle cx="12" cy="12" r="2.5"/>', 15),
  sliders: I('<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>', 15),
  pencil: I('<path d="M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>', 15), link: I('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>', 15),
  upload: I('<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>', 15),
  plus: I('<path d="M12 5v14M5 12h14"/>'), search: I('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  folder: I('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  chev: I('<path d="m6 9 6 6 6-6"/>', 14), chevR: I('<path d="m9 6 6 6-6 6"/>', 14),
  cloud: I('<path d="M7 18a4 4 0 0 1-.6-8A6 6 0 0 1 18 9.5a4.2 4.2 0 0 1-.5 8.5z"/>', 14),
  gear: I('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  send: I('<path d="M12 19V5M5 12l7-7 7 7"/>'), stop: I('<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>'),
  clip: I('<path d="m21 11-8.6 8.6a5 5 0 0 1-7-7l8.5-8.6a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.3-2.3l7.9-8"/>'),
  diff: I('<path d="M12 3v14M5 10h14M5 21h14"/>'), files: I('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>'),
  term: I('<path d="m4 17 6-5-6-5M12 19h8"/>'), globe: I('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  branch: I('<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9a6 6 0 0 1-6 6H6"/>'),
  more: I('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'),
  side: I('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
  star: I('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>', 14),
  check: I('<path d="m5 12 5 5 9-10"/>', 14), x: I('<path d="M6 6l12 12M18 6 6 18"/>'),
  cpu: I('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>', 14),
  bolt: I('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>', 14), shield: I('<path d="M12 3 5 6v6c0 4.4 3 7.6 7 9 4-1.4 7-4.6 7-9V6z"/>', 14),
  list: I('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', 14), user: I('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>', 14),
  tool: I('<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/>', 14),
  edit: I('<path d="M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>', 14), run: I('<path d="m4 17 6-5-6-5M12 19h8"/>', 14),
  read: I('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', 14),
  skill: I('<path d="M12 2 2 7l10 5 10-5z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>', 14),
  refresh: I('<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>', 14), undo: I('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-1"/>', 14),
  chat: I('<path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.4A8 8 0 1 1 21 12z"/>'),
  check2: I('<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>', 14),
  ring: I('<circle cx="12" cy="12" r="9"/>', 14),
  pr: I('<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/><path d="M6 8v8M18 16V9a3 3 0 0 0-3-3h-4m0 0 2-2m-2 2 2 2"/>'),
  eye: I('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  plug: I('<path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0zM12 17v5"/>'),
  card: I('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h3"/>'),
  palette: I('<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.7-.8 1.7-1.7 0-1.3-1-1.5-1-2.6 0-.9.7-1.7 1.7-1.7H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7.5" r="1"/><circle cx="14.5" cy="7.5" r="1"/>'),
  bell: I('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"/>'),
  sun: I('<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4"/>'),
  moon: I('<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>'),
  stack: I('<rect x="4" y="3" width="16" height="6" rx="2"/><path d="M6 9v3a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V9M8 14v5a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-5"/>'),
  cube: I('<path d="M12 3 4 7.5v9L12 21l8-4.5v-9z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>'),
  clock: I('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  codebr: I('<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>'),
  filter: I('<path d="M4 6h16M7 12h10M10 18h4"/>'),
  monitor: I('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
  ext: I('<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>', 14),
  grid: I('<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>'),
  lines: I('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  sort: I('<path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3"/>'),
  warn: I('<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17.5v.01"/>', 14),
  lock: I('<rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>', 14),
  copy: I('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>', 14),
  flag: I('<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>'),
  folderopen: I('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H7l-4 8z"/><path d="M3 17l3-7h17l-3 7z"/>', 14),
  help: I('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17v.01"/>'),
  logout: I('<path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M16 8l4 4-4 4M20 12H9"/>'),
  globe2: I('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  share: I('<path d="M12 15V4M8 8l4-4 4 4M5 13v6a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6"/>'),
  dl: I('<path d="M12 4v11M8 11l4 4 4-4M5 19h14"/>'),
  file: I('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>', 14), dir: I('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>', 14),
};

async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, {...opts, headers: {"X-Meshpool-Token": MP.token, ...(opts.body ? {"Content-Type": "application/json"} : {}), ...(opts.headers || {})}}); }
  catch (e) { throw new Error("Meshpool isn't responding. If this keeps happening, quit and reopen Meshpool."); }
  let j = null; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (j && j.inapp && r.ok && typeof showSignIn === "function") { try { showSignIn(); } catch (e) { /* the page still opened */ } }   // a sign-in page opened inside Meshpool
  if (!r.ok) {
    if (j && j.error && r.status < 500) throw new Error(j.error);
    if (r.status === 403) throw new Error("This page lost its connection to Meshpool. Reload the window.");
    throw new Error("Something went wrong on Meshpool's side. Try again." + (j && j.error ? ` (${j.error})` : ""));
  }
  return j;
}
const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body || {})});
// Shorten at a word boundary with an ellipsis (the full text stays in the tooltip).
function clip(s, n) { if (s.length <= n) return s; const c = s.slice(0, n); const i = c.lastIndexOf(" "); return (i > n * 0.6 ? c.slice(0, i) : c).replace(/[,;:.]$/, "") + "…"; }

function toast(text, ms = 5000, action) {
  let box = $("#toasts"); if (!box) { box = document.createElement("div"); box.id = "toasts"; box.className = "toasts"; box.setAttribute("role", "status"); document.body.appendChild(box); }
  if ([...box.children].some(x => x.firstChild?.nodeValue === text && !x.classList.contains("out"))) return;
  const t = document.createElement("div"); t.className = "toast"; t.textContent = text; box.appendChild(t);
  if (action) { const b = document.createElement("button"); b.className = "tact"; b.textContent = action.label; b.onclick = () => { t.remove(); action.fn(); }; t.appendChild(b); ms = Math.max(ms, 6000); }
  const gone = () => { t.classList.add("out"); setTimeout(() => t.remove(), 160); };
  if (store("popups") === "keep") { const x = document.createElement("button"); x.className = "tact tx"; x.setAttribute("aria-label", "Dismiss"); x.innerHTML = ICON.x; x.onclick = gone; t.appendChild(x); }
  else setTimeout(gone, ms);
}


/* In-app confirm and prompt: native webviews do not always show window.confirm or window.prompt. */
function ask({title, text = "", value, ok = "OK", cancel = "Cancel", danger = false}) {
  return new Promise(res => {
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    const input = value !== undefined;
    sc.innerHTML = `<div class="dlg" role="dialog" aria-modal="true" aria-label="${esc(title)}"><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}${input ? `<input id="dlgIn" value="${esc(value)}" spellcheck="false">` : ""}<div class="row"><button class="btn" data-a="0">${esc(cancel)}</button><button class="btn ${danger ? "danger" : "primary"}" data-a="1">${esc(ok)}</button></div></div>`;
    document.body.appendChild(sc);
    const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); res(v); };
    const val = () => input ? ($("#dlgIn", sc).value.trim() || null) : true;
    const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(input ? null : false); } if (e.key === "Enter") { e.preventDefault(); done(document.activeElement?.dataset?.a === "0" ? (input ? null : false) : val()); } };
    document.addEventListener("keydown", key, true);
    sc.addEventListener("mousedown", e => { if (e.target === sc) done(input ? null : false); });
    $$("[data-a]", sc).forEach(b => b.onclick = () => done(b.dataset.a === "1" ? val() : (input ? null : false)));
    const f = input ? $("#dlgIn", sc) : $("[data-a='1']", sc); f.focus(); if (input) f.select();
  });
}
/* Beta: anything that breaks can be reported in one click, with what a fix needs and nothing from the chat itself. */
const ISSUES = "https://github.com/hamzaziada4422/meshpool-harness/issues/new";
async function reportProblem(what = "") {
  if (!S.version) { try { S.version = (await api("/api/about")).version; } catch (e) { S.version = "unknown"; } }
  const v = S.cur?.view || {};
  const q = new URLSearchParams({template: "bug_report.yml", title: "[Beta] " + (clip(String(what).replace(/\s+/g, " "), 70) || "Problem report"),
    version: S.version, os: navigator.userAgentData?.platform || navigator.platform || "", agent: [v.harness?.name, v.model].filter(Boolean).join(", "),
    error: String(what).slice(0, 1500)});
  openExternal(`${ISSUES}?${q}`);
}
function openExternal(url) { post("/api/open-url", {url}).catch(() => { if (/^https?:\/\//i.test(url)) window.open(url, "_blank", "noopener"); }); }

/* ================================== state ================================== */
const S = {
  settings: null, models: null, projects: [], pj: [], threads: [], skills: [], harnesses: [], defaults: {},
  cur: null,                         // the open chat: {id, total, view, epoch, ...}
  view: "chat",                      // "chat" (a chat or the new-chat home), "project" (a project's page) or "page" (a gallery)
  pjId: "",                          // the project page that is open
  mode: store("appmode") === "code" ? "code" : "chat",   // the sidebar mode: Chat or Code
  page: "",                          // the open gallery page when view is "page" (projects, artifacts, scheduled, plugins)
  // draft: what the next new chat will use. project is a folder the user picked for this chat (usually none: a chat
  // gets its own private folder), pid a Meshpool project, harness "meshpool-auto" (Meshpool picks the agent).
  draft: {project: "", pid: "", harness: "meshpool-auto", model: store("model") || "", mode: store("mode") || "ask", plan: false, style: ""},
  panel: store("panel") || null, steps: store("steps") ?? false, attachments: [], showArchived: false,
  collapsed: new Set(store("collapsed") || []),
};
// What a chat may do without asking. The keys are what the server stores; the words are for people.
const MODES = {ask: ["Manual", "Asks before it edits files or runs commands"], edits: ["Auto", "Edits files on its own; still asks before running commands"], auto: ["Full access", "Edits files and runs commands without asking"]};

function isDark() { const t = store("theme") || "system"; return t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches); }
function logoSrc() { return isDark() ? "/brand/logo-dark.png" : "/brand/logo-light.png"; }
try { matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyAppearance()); } catch (e) { /* old webviews */ }
function applyAppearance() {
  const w = store("width") || "narrow", d = store("density") || "comfortable";
  document.documentElement.style.setProperty("--colw", {narrow: "656px", normal: "760px", wide: "960px"}[w] || "656px");
  if (d === "compact") document.documentElement.dataset.density = "compact"; else document.documentElement.removeAttribute("data-density");
  document.querySelectorAll(".mlogo").forEach(i => i.src = logoSrc());
  const R = document.documentElement, ff = store("font") || "plex", rf = store("rfont") || "match";
  R.style.setProperty("--body", ff === "system" ? 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif' : '"IBM Plex Sans","Segoe UI",system-ui,sans-serif');
  R.style.setProperty("--resp", rf === "serif" ? 'Georgia,"Times New Roman",serif' : rf === "system" ? 'system-ui,-apple-system,"Segoe UI",sans-serif' : "inherit");
  R.style.setProperty("--mono", store("codefont") ? `${store("codefont")},"IBM Plex Mono",ui-monospace,Consolas,monospace` : '"IBM Plex Mono",ui-monospace,Consolas,"SFMono-Regular",monospace');
  R.style.setProperty("--code-fs", (store("codesize") || 13) + "px");
  for (const [k, a, d] of [["motion", "motion", "system"], ["contrast", "contrast", "system"]]) { const v = store(k) || d; if (v === "system") R.removeAttribute("data-" + a); else R.dataset[a] = v; }
  const t = store("theme") || "system", z = store("size") || "normal";
  if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
  if (z === "normal") document.documentElement.removeAttribute("data-size"); else document.documentElement.dataset.size = z;
}
applyAppearance();

/* ---------- tooltips: a name and its shortcut, after a short pause; never while the pointer is moving through ---------- */
function initTips() {
  let tip = null, timer = null, on = null;
  const hide = () => { clearTimeout(timer); timer = null; on = null; if (tip) { tip.remove(); tip = null; } };
  const show = el => {
    const text = el.dataset.tip; if (!text) return;
    hide(); on = el;
    tip = document.createElement("div"); tip.className = "tip"; tip.setAttribute("role", "tooltip");
    tip.innerHTML = esc(text) + (el.dataset.kbd ? `<kbd>${esc(el.dataset.kbd)}</kbd>` : "");
    document.body.appendChild(tip);
    const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    const below = r.top < h + 16 || el.dataset.tipPos === "below";
    tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
    tip.style.top = (below ? Math.min(innerHeight - h - 8, r.bottom + 6) : r.top - h - 6) + "px";
    requestAnimationFrame(() => tip && tip.classList.add("on"));
  };
  document.addEventListener("mouseover", e => { const el = e.target.closest?.("[data-tip]"); if (el === on) return; hide(); if (el) timer = setTimeout(() => show(el), 450); });
  document.addEventListener("focusin", e => { const el = e.target.closest?.("[data-tip]"); if (el && el.matches(":focus-visible")) show(el); });
  for (const t of ["focusout", "mousedown", "keydown", "scroll", "wheel"]) document.addEventListener(t, hide, true);
}
initTips();

/* ---------- menus: one component for every "more options" button and right-click ---------- */
// items: {label, icon, kbd, danger, checked, sub: [items], fn} or {sep: true}. Arrow keys move, Enter picks, Right opens a
// submenu, Left or Esc goes back, and focus returns to what opened the menu.
function ctxMenu(e, items, opts = {}) {
  closeCtx();
  const opener = document.activeElement;
  const m = document.createElement("div"); m.className = "ctx" + (opts.cls ? " " + opts.cls : ""); m.id = "ctx"; m.setAttribute("role", "menu");
  document.body.appendChild(m);
  const point = e.type === "contextmenu" ? {x: e.clientX, y: e.clientY} : null;
  const anchor = (e.currentTarget && e.currentTarget.getBoundingClientRect) ? e.currentTarget : e.target;
  const place = () => {
    const r = anchor?.getBoundingClientRect?.() || {left: e.clientX || 0, bottom: e.clientY || 0, right: e.clientX || 0, top: e.clientY || 0};
    const x = point ? point.x : r.left, y = point ? point.y : r.bottom + 4;
    m.style.left = Math.max(8, Math.min(innerWidth - m.offsetWidth - 8, x)) + "px";
    m.style.top = Math.max(8, Math.min(innerHeight - m.offsetHeight - 8, (y + m.offsetHeight > innerHeight - 8 && !point) ? r.top - m.offsetHeight - 4 : y)) + "px";
  };
  const stack = []; let curList = items;
  const draw = (list, title) => {
    curList = list;
    m.innerHTML = (title ? `<button class="mi" role="menuitem" tabindex="-1" data-back="1"><span class="mi-i">${ICON.chev}</span><span class="ml">${esc(title)}</span></button><div class="msep" role="separator"></div>` : "")
      + list.map((it, i) => it.sep ? `<div class="msep" role="separator"></div>` : it.head ? `<div class="mhead">${it.html ? it.head : esc(it.head)}</div>` : `<button class="mi ${it.danger ? "danger" : ""} ${it.checked ? "sel" : ""}" role="${it.checked !== undefined ? "menuitemcheckbox" : "menuitem"}" ${it.checked !== undefined ? `aria-checked="${!!it.checked}"` : ""} tabindex="-1" data-i="${i}"><span class="mi-i">${it.icon || ""}</span><span class="ml">${esc(it.label)}${it.desc ? `<small>${esc(it.desc)}</small>` : ""}</span>${it.note ? `<span class="mnote">${esc(it.note)}</span>` : ""}${it.kbd ? `<kbd>${esc(it.kbd)}</kbd>` : ""}${it.checked ? `<span class="mi-c">${ICON.check}</span>` : ""}${it.sub || it.more ? `<span class="mi-s">${ICON.chevR}</span>` : ""}</button>`).join("");
    place();
    $$(".mi", m).forEach(b => {
      b.onclick = () => {
        if (b.dataset.back) { const prev = stack.pop(); draw(prev.list, prev.title); $(".mi", m)?.focus(); return; }
        const it = list[+b.dataset.i];
        if (it.sub) { stack.push({list, title}); draw(it.sub, it.label); $(".mi:not([data-back])", m)?.focus(); return; }
        closeCtx(true); it.fn?.();
      };
      b.onmousemove = () => { if (document.activeElement !== b) b.focus({preventScroll: true}); };
    });
  };
  draw(items);
  m.addEventListener("keydown", ev => {
    const bs = $$(".mi", m), i = bs.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") { ev.preventDefault(); bs[(i + 1) % bs.length].focus(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    else if (ev.key === "Home") { ev.preventDefault(); bs[0].focus(); }
    else if (ev.key === "End") { ev.preventDefault(); bs[bs.length - 1].focus(); }
    else if (ev.key === "ArrowRight") { const b = bs[i]; if (b && b.querySelector(".mi-s")) { ev.preventDefault(); b.click(); } }
    else if (ev.key === "ArrowLeft" || ev.key === "Backspace") { if (stack.length) { ev.preventDefault(); $("[data-back]", m)?.click(); } else if (ev.key === "ArrowLeft") { closeCtx(); opener?.focus?.(); } }
    else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); closeCtx(); opener?.focus?.({preventScroll: true}); }
    else if (ev.key === "Tab") { closeCtx(); }
    else if (!ev.metaKey && !ev.ctrlKey && !ev.altKey && ev.key.length === 1) {          // a single letter picks the item that names it (P, R, A, D; 1 to 4)
      const hit = $$(".mi", m).find(b => { const it = curList[+b.dataset.i]; return it && it.key === ev.key.toLowerCase(); });
      if (hit) { ev.preventDefault(); hit.click(); }
    }
  });
  m.querySelector(".mi")?.focus({preventScroll: true});
  setTimeout(() => document.addEventListener("mousedown", ctxAway), 0);
  window.addEventListener("blur", closeCtx, {once: true});
}
function ctxAway(e) { if (!e.target.closest("#ctx")) closeCtx(); }
function closeCtx() { $("#ctx")?.remove(); document.removeEventListener("mousedown", ctxAway); }
function threadMenu(e, id) {
  const t = S.threads.find(x => x.id === id) || (S.cur?.id === id ? {id, ...S.cur.view} : {});
  const inPj = t.project_id && S.pj.find(p => p.id === t.project_id);
  const projects = [
    ...S.pj.map(p => ({label: p.name, icon: ICON.folder, checked: p.id === t.project_id ? true : undefined, fn: () => p.id === t.project_id || moveChat(id, p.id)})),
    ...(S.pj.length ? [{sep: true}] : []),
    {label: "New project…", icon: ICON.plus, fn: async () => { const pid = await newProjectFlow({stay: true}); if (pid) moveChat(id, pid); }},
    ...(inPj ? [{label: "Remove from project", icon: ICON.x, fn: () => moveChat(id, "")}] : []),
  ];
  ctxMenu(e, [
    {label: "Rename", icon: ICON.pencil, kbd: "F2", fn: () => startRename(id)},
    {label: t.pinned ? "Unpin" : "Pin", icon: ICON.pin, fn: () => threadAct(id, t.pinned ? "unpin" : "pin")},
    {label: inPj ? "Move to project" : "Add to project", icon: ICON.folder, sub: projects},
    {label: t.archived ? "Restore" : "Archive", icon: ICON.archive, fn: () => threadAct(id, t.archived ? "unarchive" : "archive")},
    {sep: true},
    {label: "Delete", icon: ICON.trash, danger: true, fn: () => confirmDelete(id)},
  ]);
}
async function confirmDelete(id) {
  const t = S.threads.find(x => x.id === id) || {};
  if (await ask({title: "Delete this chat?", text: `“${clip(t.title || "This chat", 60)}” is removed from Meshpool. Files it made in a project folder stay where they are.`, ok: "Delete chat", cancel: "Keep chat", danger: true})) threadAct(id, "delete");
}
async function moveChat(id, pid) {
  const t = S.threads.find(x => x.id === id), prev = t?.project_id || "";
  try { await post(`/api/threads/${id}`, {action: "move", project_id: pid}); } catch (e) { return toast(e.message); }
  if (t) t.project_id = pid;
  if (S.cur?.id === id && S.cur.view) S.cur.view.project_id = pid;
  await loadThreads(); await loadPj(); renderTop();
  const name = S.pj.find(p => p.id === pid)?.name;
  toast(pid ? `Moved to ${name || "the project"}` : "Removed from the project", 6000, {label: "Undo", fn: () => moveChat(id, prev)});
}
function projMenu(e, pid) {
  const p = S.pj.find(x => x.id === pid); if (!p) return;
  ctxMenu(e, [
    {label: "New chat in project", icon: ICON.plus, fn: () => openProject(pid, {focus: true})},
    {label: "Open project", icon: ICON.stack, fn: () => openProject(pid)},
    {label: p.pinned ? "Unpin from the sidebar" : "Pin to the sidebar", icon: ICON.pin, fn: async () => { try { await post(`/api/pj/${pid}`, {action: "update", pinned: !p.pinned}); await loadPj(); renderSide(); if (S.view === "page" && S.page === "projects") pageProjects(); } catch (e) { toast(e.message); } }},
    {label: "Rename", icon: ICON.pencil, fn: () => renameProject(pid)},
    {sep: true},
    {label: "Delete project", icon: ICON.trash, danger: true, fn: () => deleteProject(pid)},
  ]);
}
async function threadAct(id, action) {
  if (action === "delete") {                         // hidden at once, deleted after a few seconds unless undone
    S.pendingDelete = new Set([...(S.pendingDelete || []), id]);
    if (["working", "waiting"].includes(S.threads.find(t => t.id === id)?.state)) post(`/api/agent/${id}/stop`, {}).catch(() => {});
    if (S.cur?.id === id) newThread();
    renderSide();
    const t = setTimeout(async () => { S.pendingDelete.delete(id); try { await post(`/api/threads/${id}`, {action}); } catch (e) { toast(e.message); } await loadThreads(); }, 6000);
    toast("Chat deleted", 6000, {label: "Undo", fn: () => { clearTimeout(t); S.pendingDelete.delete(id); renderSide(); toast("Chat restored"); }});
    return;
  }
  try { await post(`/api/threads/${id}`, {action}); } catch (e) { return toast(e.message); }
  await loadThreads(); renderTop();
  if (action === "archive") toast("Chat archived", 6000, {label: "Undo", fn: () => threadAct(id, "unarchive")});
  else if (action === "unarchive") toast("Chat restored");
  else if (action === "pin") toast("Pinned to the top of the sidebar");
}
// Rename in place: the row's title becomes a text box. Enter saves, Esc cancels, clicking away saves.
function startRename(id) {
  const t = S.threads.find(x => x.id === id);
  const li = $(`#sideList .th[data-id="${CSS.escape(id)}"]`);
  if (!li || !t) { if (S.cur?.id === id) renameTop(); return; }
  if ($("#app").classList.contains("noside") || (narrow() && !$("#app").classList.contains("sideopen"))) return renameTop();
  closeCtx();
  const tt = $(".tt", li); S.renaming = id;
  tt.innerHTML = `<input class="rn" value="${esc(t.title)}" maxlength="120" aria-label="Chat name" spellcheck="false" autocomplete="off">`;
  const inp = $(".rn", tt); inp.focus(); inp.select();
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    const v = inp.value.trim(); S.renaming = null;
    const el = $("#sideList"); if (el) el._html = el._shape = "";
    if (save && v && v !== t.title) {
      try { await post(`/api/threads/${id}`, {action: "rename", title: v}); t.title = v; if (S.cur?.id === id && S.cur.view) { S.cur.view.title = v; renderTop(); } } catch (err) { toast(err.message); }
    }
    renderSide(); $(`#sideList .th[data-id="${CSS.escape(id)}"]`)?.focus({preventScroll: true}); loadThreads();
  };
  inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === "Enter") { ev.preventDefault(); finish(true); } else if (ev.key === "Escape") { ev.preventDefault(); finish(false); } };
  inp.onblur = () => finish(true);
}
async function renameThread(id) { startRename(id); }

function browseFolder(start) {
  return new Promise(resolve => {
    const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
    sc.innerHTML = `<div class="dlg fb" role="dialog" aria-modal="true" aria-label="Choose a folder"><h3>Choose a folder</h3>
      <div class="fbcrumbs" id="fbCrumbs"></div>
      <input id="fbIn" spellcheck="false" aria-label="Folder path" placeholder="Or type a folder path and press Enter">
      <ul class="fblist" id="fbList" role="listbox" tabindex="0"></ul>
      <div class="row"><span class="fbsel" id="fbSel"></span><button class="btn" data-a="0">Cancel</button><button class="btn primary" data-a="1">Use this folder</button></div></div>`;
    document.body.appendChild(sc);
    let cur = "", home = "", act = -1;
    const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); if (v) store("fb_last", v); resolve(v); };
    const items = () => $$("#fbList [data-p]", sc);
    const mark = n => { const it = items(); if (!it.length) return; act = Math.max(0, Math.min(it.length - 1, n)); it.forEach((b, k) => b.classList.toggle("act", k === act)); it[act].scrollIntoView({block: "nearest"}); };
    const key = e => {
      if (e.key === "Escape") { e.stopPropagation(); done(""); return; }
      if (e.target.id === "fbIn") { if (e.key === "Enter") { e.preventDefault(); go(e.target.value.trim()); } return; }
      if (e.key === "ArrowDown") { e.preventDefault(); mark(act + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); mark(act - 1); }
      else if (e.key === "Enter" && act >= 0) { e.preventDefault(); go(items()[act].dataset.p); }
      else if (e.key === "Backspace") { e.preventDefault(); const up = $("#fbList [data-up]", sc); if (up) go(up.dataset.p); }
    };
    document.addEventListener("keydown", key, true);
    const go = async path => {
      try {
        const r = await post("/api/projects", {action: "dirs", path});
        cur = r.path; home = r.home; act = -1;
        const sep = r.path.includes("\\") ? "\\" : "/";
        const parts = r.path.split(/[\\/]/).filter(Boolean);
        let acc = r.path.startsWith("/") ? "" : "";
        $("#fbCrumbs", sc).innerHTML = (r.path.startsWith("/") ? `<button data-c="/">/</button>` : "") + parts.map((p, k) => {
          acc = (k === 0 && !r.path.startsWith("/")) ? p + sep : acc + (acc.endsWith(sep) || acc === "" ? "" : sep) + p;
          const full = r.path.startsWith("/") ? "/" + parts.slice(0, k + 1).join("/") : acc;
          return `<button data-c="${esc(full)}" class="${k === parts.length - 1 ? "here" : ""}">${esc(full === home ? "Home" : p)}</button>`;
        }).join(`<span class="sep">›</span>`);
        $$("#fbCrumbs [data-c]", sc).forEach(b => b.onclick = () => go(b.dataset.c));
        $("#fbIn", sc).value = "";
        $("#fbList", sc).innerHTML = (r.parent ? `<li><button data-p="${esc(r.parent)}" data-up="1">${ICON.chev}<span>Up one level</span></button></li>` : "") +
          (r.dirs.length ? r.dirs.map(d => `<li><button data-p="${esc(r.path.replace(/[\\/]$/, "") + sep + d)}">${ICON.dir}<span>${esc(d)}</span></button></li>`).join("") : `<li class="pnote">No folders inside.</li>`);
        items().forEach((b, k) => { b.onclick = () => mark(k); b.ondblclick = () => go(b.dataset.p); });
        const name = r.path === r.home ? "your whole home folder" : (parts[parts.length - 1] || r.path);
        $("#fbSel", sc).textContent = `Selected: ${name}`;
        $("[data-a='1']", sc).textContent = r.path === r.home ? "Use the whole home folder" : `Use ${name}`;
        $("#fbList", sc).focus();
      } catch (e) { toast(e.message); }
    };
    $$("[data-a]", sc).forEach(b => b.onclick = () => done(b.dataset.a === "1" ? cur : ""));
    sc.addEventListener("mousedown", e => { if (e.target === sc) done(""); });
    go(start || store("fb_last") || "");
  });
}
/* ================================== composer ================================== */
function setupComposer() {
  const inp = $("#input");
  inp.addEventListener("input", () => { try { sessionStorage.setItem("mp_draft_" + (S.cur?.id || "new"), inp.value); } catch (e) {} });
  inp.addEventListener("input", () => { clearTimeout(S.suggT); S.suggT = setTimeout(suggestNow, 450); });
  inp.addEventListener("input", () => { inp.style.height = "auto"; inp.style.height = Math.min(260, inp.scrollHeight) + "px"; menuFromInput(); const ib = $("#intrBtn"); if (ib && !S.stopping) ib.disabled = !inp.value.trim() && !S.attachments.length; });
  inp.addEventListener("keydown", e => {
    const pop = $("#pop.inline");
    if (pop && ["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(e.key)) { e.preventDefault(); popKey(e.key); return; }
    if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey &&
        !inp.value.slice(0, inp.selectionStart).includes("\n") && recallSent(inp, e.key === "ArrowUp" ? -1 : 1)) { e.preventDefault(); return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendNow((isMac ? e.metaKey : e.ctrlKey) && cfg().busy); }
    if (e.key === "Escape" && !pop && cfg().busy && !$(".scrim")) { e.preventDefault(); stopNow(); }
  });
  inp.addEventListener("paste", e => {
    const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addAttachments(files); return; }
    const t = e.clipboardData?.getData("text") || "", lines = t.split("\n").length;
    if (t.length > 3000 || lines > 60) {                // a long paste: a chip that goes with the message, not a wall of text
      e.preventDefault();
      const n = S.attachments.filter(a => a.pasted).length + 1;
      S.attachments.push({name: `Pasted text ${n} (${lines} line${lines === 1 ? "" : "s"})`, pasted: true, text: t});
      renderAttach();
    }
  });
  const c = $("#composer");
  c.addEventListener("dragover", e => { e.preventDefault(); c.classList.add("drag"); });
  c.addEventListener("dragleave", () => c.classList.remove("drag"));
  c.addEventListener("drop", e => { e.preventDefault(); c.classList.remove("drag"); addAttachments([...e.dataTransfer.files]); });
  $("#fileIn").onchange = () => { addAttachments([...$("#fileIn").files]); $("#fileIn").value = ""; };
  renderBar();
}
function cfg() { // the settings that apply to the next message: the open thread's, or the draft's
  const v = S.cur?.view;
  if (v) return {harness: v.harness?.id, model: v.model, mode: v.mode, plan: v.plan, style: v.style, effort: v.effort, busy: ["working", "waiting"].includes(v.state), starting: v.state === "starting"};
  return {harness: S.draft.harness, model: S.draft.model || S.models?.default, mode: S.draft.mode, plan: S.draft.plan, style: S.draft.style, effort: S.draft.effort || "auto", busy: false};
}
async function chooseDraftFolder() {
  const path = await browseFolder(S.draft.project || "");
  if (!path) return;
  S.draft.project = path; renderTop(); renderBar(); if (!S.cur && S.view === "chat") renderHero();
}
async function setPlan(on) {
  if (S.cur) { try { S.cur.view = Object.assign(S.cur.view, await post(`/api/agent/${S.cur.id}/plan`, {on, since: 1e9})); } catch (e) { return toast(e.message); } }
  else S.draft.plan = on;
  renderBar();
}
async function setEffort(e) {
  if (S.cur) { try { S.cur.view = Object.assign(S.cur.view, await post(`/api/agent/${S.cur.id}/effort`, {effort: e, since: 1e9})); } catch (err) { return toast(err.message); } }
  else S.draft.effort = e;
  renderBar();
}
async function setMode(m) {
  if (S.cur) { try { await post(`/api/agent/${S.cur.id}/mode`, {mode: m, since: 1e9}); S.cur.view.mode = m; } catch (e) { return toast(e.message); } }
  else { S.draft.mode = m; store("mode", m); }
  renderBar();
}
async function setModel(id) {
  if (S.cur) { try { await post(`/api/agent/${S.cur.id}/model`, {model: id, since: 1e9}); S.cur.view.model = id; } catch (e) { return toast(e.message); } }
  else { S.draft.model = id; store("model", id); }
  renderBar();
}
async function addAttachments(files) {
  for (const f of files) {
    if (f.size > 150 * 1024 * 1024) { toast(`${f.name} is larger than 150 MB. Put it in the project folder instead.`); continue; }
    const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); });
    S.attachments.push({name: f.name, mime: f.type || "application/octet-stream", data, image: /^image\/(png|jpe?g|gif|webp)$/.test(f.type)});
  }
  renderAttach();
}
function renderAttach() {
  $("#attachRow").innerHTML = S.attachments.map((a, i) => `<span class="att-chip"${a.pasted ? ` title="${esc(a.text.slice(0, 600))}" data-paste="${i}"` : ""}>${a.image ? `<img src="data:${a.mime};base64,${a.data}" alt="">` : ICON.file}<span>${esc(a.name)}</span><button data-i="${i}" aria-label="Remove ${esc(a.name)}">×</button></span>`).join("");
  $$("#attachRow [data-paste] > span").forEach(x => x.onclick = () => {     // put a pasted block back in the box
    const i = +x.parentElement.dataset.paste, a = S.attachments[i], inp = $("#input");
    S.attachments.splice(i, 1); inp.value += (inp.value ? "\n" : "") + a.text; inp.dispatchEvent(new Event("input")); renderAttach();
  });
  $$("#attachRow button").forEach(b => b.onclick = () => { S.attachments.splice(+b.dataset.i, 1); renderAttach(); });
}
async function stopNow() {
  if (!S.cur || S.stopping) return;
  S.stopping = true; renderBar();
  const cur = S.cur, t0 = Date.now();
  try { await post(`/api/agent/${cur.id}/stop`); } catch (e) { toast(e.message); }
  if (S.cur !== cur) { S.stopping = false; renderBar(); return; }
  clearTimeout(cur.pollT); poll();
  const wait = () => { if (S.cur !== cur || !["working", "waiting"].includes(cur.view?.state) || Date.now() - t0 > 20000) { S.stopping = false; renderBar(); if (Date.now() - t0 > 20000 && ["working", "waiting"].includes(cur.view?.state)) toast("Stopping… finishing the current step."); return; } setTimeout(wait, 300); };
  wait();
}
async function sendNow(interrupt) {
  try { sessionStorage.removeItem("mp_draft_" + (S.cur?.id || "new")); } catch (e) {}
  const inp = $("#input"); let text = inp.value.trim(); const typed = text;
  if (!text && !S.attachments.length) return;
  S.recall = null; rememberSent(typed);
  const busy = cfg().busy;
  if (text.startsWith("/") && await runSlash(text)) { inp.value = ""; inp.dispatchEvent(new Event("input")); return; }
  if (/^#\s+\S/.test(text) && !text.includes("\n") && !S.attachments.length) {   // "# a fact": saved to memory, not sent
    try { const r = await post("/api/memory", {action: "add", text: text.replace(/^#\s+/, "")}); inp.value = ""; inp.dispatchEvent(new Event("input")); toast(r.created ? "Saved to memory." : "Already in memory.", 3000, {label: "Memory", fn: () => goPage("memory")}); }
    catch (e) { toast(e.message, 5000); }
    return;
  }
  if (!S.settings?.ready) { S.pendingGoal = null; showOnboarding(); return; }
  if (!S.cur && S.mode === "code" && !S.draft.project) { toast("Choose a folder or a repository first."); $("#envSel")?.click(); return; }
  const atts = S.attachments; S.attachments = []; renderAttach();
  clearTimeout(S.suggT); if ($("#sugg")) $("#sugg").hidden = true;
  inp.value = ""; inp.style.height = "auto";
  // Doherty threshold: answer the press at once, before the server does
  const sb = $("#sendBtn"); if (sb) { sb.classList.remove("go"); void sb.offsetWidth; sb.classList.add("go"); }
  S.sendHold = Date.now() + 2500; setWorking("Sending"); scrollConv(true);
  try {
    if (!S.cur) {
      const v = await post("/api/agent", {workdir: S.draft.project || "", project_id: S.draft.pid || "", harness: S.draft.harness || "meshpool-auto", ...(S.mode === "code" ? {kind: "code"} : {}), model: S.draft.model, mode: S.draft.mode, plan: S.draft.plan, style: styleId(), task: text, incognito: !!S.draft.incognito, memory_exclude: S.draft.exclude || [], effort: S.draft.effort || "auto"});
      S.draft.style = ""; S.draft.incognito = false; S.draft.exclude = []; S.draft.plan = !!capOn("plan_first"); S.draft.pid = "";
      openThread(v.id, true);
    }
    const cur = S.cur, root = curRoot();          // before any wait: the user may open another chat meanwhile
    for (const a of atts.filter(a => a.pasted)) text += `\n\n[${a.name}]\n\`\`\`\n${a.text}\n\`\`\``;
    const files = atts.filter(a => !a.image && !a.pasted);
    if (files.length) {
      const saved = [];
      for (const f of files) { const r = await post("/api/attach", {root, name: f.name, data: f.data}); saved.push(r.path); }
      text += `\n\nAttached files (in ${S.cur?.view?.scratch ? "this chat's folder" : "the project folder"}): ${saved.join(", ")}`;
    }
    const images = atts.filter(a => a.image).map(a => ({name: a.name, mime: a.mime, data: a.data}));
    if (interrupt) { S.stopping = true; renderBar(); }
    if (S.pendingGoal) { const g = S.pendingGoal; S.pendingGoal = null; await setGoal(g, cur); }
    const r = await post(`/api/agent/${cur.id}/send`, {text, images, since: 1e9, interrupt: !!interrupt});
    S.stopping = false;
    if (S.cur !== cur) { setTimeout(loadThreads, 500); return; }        // the user moved on: the sent chat keeps running
    if (busy && !interrupt && r.sent?.queued) toast(`${harness(cfg().harness).name} gets this when the current step finishes.`);
    if (cur.view) cur.view.state = "working";
    renderBar();
    clearTimeout(cur.pollT); poll(); setTimeout(loadThreads, 500);
  } catch (e) {
    S.pendingGoal = null;
    S.stopping = false; renderBar();
    if (!inp.value.trim()) { inp.value = typed; inp.dispatchEvent(new Event("input")); }
    if (!S.attachments.length) { S.attachments = atts; renderAttach(); }
    toast(e.message, 4000);
  }
}

/* ---------- slash commands and $skills ---------- */
const SLASH = [
  ["/new", "Start a new chat", () => newThread()],
  ["/model", "Choose the model", () => modelPop($("#mPill"))],
  ["/agent", "Choose the agent", () => harnessPop($("#oPill"))],
  ["/plan", "Turn plan mode on or off", () => setPlan(!cfg().plan)],
  ["/goal", "Set a goal: the agent keeps going until it is done (/goal clear ends it)", arg => goalCmd(arg)],
  ["/usage", "Usage, by model and by chat", () => openSettings("usage")],
  ["/review", "Review this branch's changes for bugs (/review what to focus on)", arg => sendCmd(REVIEW_PROMPT + (arg ? ` Focus on: ${arg}.` : ""))],
  ["/qa", "Click through your app and report UI and UX problems (/qa address)", arg => sendCmd(qaPrompt(arg))],
  ["/add-dir", "Let the agent read and edit in another folder too (/add-dir path)", async arg => {
    if (!S.cur) return toast("Open a chat first.");
    if (!arg) return toast("Type the folder's path after /add-dir.");
    try { const r = await post(`/api/agent/${S.cur.id}/add_dir`, {path: arg}); toast(`The agent may now work in ${r.folder} too.`, 5000); } catch (e) { toast(e.message, 6000); }
  }],
  ["/btw", "A quick side question about this chat; the answer is not added to it (/btw your question)", async arg => {
    if (!S.cur) return toast("Open a chat first.");
    if (!arg) return toast("Type your question after /btw.");
    toast("Asking on the side…", 2500);
    try { const r = await post(`/api/agent/${S.cur.id}/btw`, {question: arg}); await ask({title: "By the way", text: r.answer, ok: "Close", cancel: "Copy"}) === false && navigator.clipboard?.writeText(r.answer); }
    catch (e) { toast(e.message, 6000); }
  }],
  ["/compact", "Summarize the earlier steps to free up room (/compact what to keep in full)", async arg => {
    if (!S.cur) return toast("Open a chat first.");
    try { const r = await post(`/api/agent/${S.cur.id}/compact`, {focus: arg || ""}); toast(r.compacted ? `Summarized ${r.compacted.removed} earlier messages.` : "Nothing to summarize yet."); } catch (e) { toast(e.message); }
  }],
  ["/diff", "Show what changed", () => togglePanel("diff", true)],
  ["/files", "Browse the project's files", () => togglePanel("files", true)],
  ["/terminal", "Open the terminal", () => togglePanel("term", true)],
  ["/git", "Branch, commit, push and pull request", () => togglePanel("git", true)],
  ["/prs", "Pull requests: what each changes, its checks (CI) and deployments (CD)", () => openPRView()],
  ["/skills", "Manage skills", () => openSettings("skills")],
  ["/connectors", "Connect GitHub, Jira, Notion, Apollo and more", () => openSettings("connectors")],
  ["/schedule", "Scheduled tasks", () => goPage("scheduled")],
  ["/memory", "What Meshpool knows: every memory, where it came from and where it was used", () => goPage("memory")],
  ["/instructions", "Instructions for every chat", () => openSettings("personal")],
  ["/steps", "Show or fold every step", () => toggleSteps()],
  ["/settings", "Open settings", () => openSettings()],
];
const ARG_CMDS = new Set(["/goal", "/qa"]);         // these need words after them; /review works on its own
async function runSlash(text) {
  const cmd = text.split(/\s/)[0].toLowerCase(); const hit = SLASH.find(s => s[0] === cmd);
  if (!hit) return false; hit[2](text.slice(cmd.length).trim()); return true;
}
const REVIEW_PROMPT = "Review the changes on this branch against the branch it merges into: find the base with git, read `git diff <base>...HEAD` and the changed files in full. Look for correctness bugs, security problems, missing tests and anything that would break on Mac or Windows. Report findings most severe first, each with file:line, what goes wrong and the fix. Do not change any files.";
function qaPrompt(where) {
  return `Do a UI and UX pass of ${where || "the app in this project"}. If it is not running, start it the way its README or package.json says. Open it with the browser tools and click through every page and control at desktop width (1280x800) and phone width (390x844), in light and dark mode if it has both. Take a screenshot of each problem. Report issues most severe first: what you saw, the screenshot, the likely cause (file:line) and the fix. Look for broken flows, console errors, overlapping or cut-off elements, poor contrast, small tap targets, missing empty, loading and error states, and unclear wording. Do not change any files.`;
}
function sendCmd(prompt) { const inp = $("#input"); inp.value = prompt; sendNow(false); }
/* Suggestions while typing: the connector, skill, agent or command that fits the message (suggest.py). */
async function suggestNow() {
  const box = $("#sugg"); if (!box) return;
  const text = $("#input").value.trim();
  if (text.length < 12 || /^[\/$]/.test(text)) { box.hidden = true; return; }
  let r; try { r = await post("/api/suggest", {text, harness: cfg().harness, kind: curMode(), new: !S.cur}); } catch (e) { return; }
  if ($("#input").value.trim() !== text) return;                 // typed on meanwhile: the next pause asks again
  S.suggOff = S.suggOff || new Set();
  const items = (r.suggestions || []).filter(x => !S.suggOff.has(x.type + ":" + x.id));
  box.hidden = !items.length; if (!items.length) return;
  const ic = {connect: ICON.plug, enable: ICON.plug, skill: ICON.skill, agent: ICON.cpu, command: ICON.bolt};
  box.innerHTML = items.map((x, i) => `<button class="schip" data-i="${i}" title="${esc(x.why || "")}">${ic[x.type] || ""}<span>${esc(x.label)}</span></button>`).join("")
    + `<button class="iconbtn sx" id="suggX" title="Hide suggestions" aria-label="Hide suggestions">${ICON.x}</button>`;
  $$(".schip", box).forEach(b => b.onclick = () => useSuggestion(items[+b.dataset.i]));
  $("#suggX").onclick = () => { items.forEach(x => S.suggOff.add(x.type + ":" + x.id)); box.hidden = true; };
}
async function useSuggestion(x) {
  const inp = $("#input"); const box = $("#sugg");
  S.suggOff.add(x.type + ":" + x.id); box.hidden = true;
  if (x.type === "connect") openSettings("connectors");
  else if (x.type === "enable") { try { await post("/api/connectors", {action: "enable", id: x.id, on: true}); toast(`${x.label.replace(/^Turn on /, "")} is on for your chats.`); } catch (e) { toast(e.message, 4000); } }
  else if (x.type === "skill") { if (!inp.value.includes("$" + x.name)) inp.value = `$${x.name} ${inp.value}`; }
  else if (x.type === "agent") { S.draft.harness = x.id; renderBar(); toast(`${x.label.replace(/^Use /, "")} will do this one.`); }
  else if (x.type === "command") { const t = inp.value.trim(); inp.value = x.id === "/goal" ? `/goal ${t}` : x.id; }
  inp.focus(); inp.dispatchEvent(new Event("input"));
}
/* /goal: the agent keeps taking turns until it reports the goal met and checked, it needs you, or you stop it. */
async function goalCmd(arg) {
  const cur = S.cur?.view?.goal || "";
  if (!arg) { toast(cur ? `Goal: ${cur}. Type /goal clear to end it.` : "Type /goal and what should be done, e.g. /goal make every test pass", 5000); return; }
  if (/^(clear|off|stop|none|end)$/i.test(arg)) { if (S.cur) await setGoal(""); else toast("No goal is set."); return; }
  S.pendingGoal = arg;
  const inp = $("#input"); inp.value = arg; sendNow(false);     // sends it as the first message of the goal
}
async function setGoal(text, cur = S.cur) {
  if (!cur) return;
  try { const v = await post(`/api/agent/${cur.id}/goal`, {text, since: 1e9}); if (cur.view) cur.view.goal = v.goal; renderBar(); }
  catch (e) { toast(e.message, 4000); }
}
/* Frequency of use: the commands and skills you use most come first in their menus. */
const USE = (() => { try { return JSON.parse(localStorage.getItem("mp_use") || "{}"); } catch (e) { return {}; } })();
function used(key) { USE[key] = (USE[key] || 0) + 1; try { localStorage.setItem("mp_use", JSON.stringify(USE)); } catch (e) {} }
const byUse = items => items.map((x, i) => [x, i]).sort((a, b) => (USE[b[0].label] || 0) - (USE[a[0].label] || 0) || a[1] - b[1]).map(x => x[0]);
function menuFromInput() {
  const inp = $("#input"); const v = inp.value; const pos = inp.selectionStart;
  const before = v.slice(0, pos);
  const slash = /^\/(\w*)$/.exec(before);
  const skill = /(^|\s)\$([\w-]*)$/.exec(before);
  const at = /(^|\s)@([\w./-]*)$/.exec(before);
  if (at && !slash && !skill) return mentionPop(inp, v, pos, at[2]);
  if (slash) {
    const q = slash[1].toLowerCase();
    const items = byUse(SLASH.filter(s => s[0].slice(1).startsWith(q)).map(s => ({label: s[0], desc: s[1], fn: () => { used(s[0]); if (ARG_CMDS.has(s[0])) { inp.value = s[0] + " "; inp.focus(); inp.dispatchEvent(new Event("input")); return; } inp.value = ""; s[2](); }})));
    const sk = byUse(S.skills.filter(s => s.enabled && s.name.toLowerCase().startsWith(q)).map(s => ({label: "$" + s.name, desc: s.description, fn: () => { used("$" + s.name); inp.value = "$" + s.name + " "; inp.focus(); inp.dispatchEvent(new Event("input")); }}))).slice(0, 5);
    return items.length || sk.length ? inlinePop([...items, ...sk]) : closePop();
  }
  if (skill) {
    const q = skill[2].toLowerCase();
    const items = byUse(S.skills.filter(s => s.enabled && s.name.toLowerCase().includes(q)).map(s => ({label: "$" + s.name, desc: s.description, fn: () => {
      used("$" + s.name); const start = pos - skill[2].length - 1; inp.value = v.slice(0, start) + "$" + s.name + " " + v.slice(pos); inp.focus();
    }})));
    return items.length ? inlinePop(items.slice(0, 7)) : closePop();      // Miller: a short list to scan
  }
  if ($("#pop.inline")) closePop();
}

/* @ in the composer: pick a file in this chat's folder; it is attached when the message is sent (@path). */
let MENTION_T = null, MENTION_Q = null;
function mentionPop(inp, v, pos, q) {
  const root = S.cur?.view?.workdir || S.draft.project || "";
  if (!root) { if ($("#pop.inline")) closePop(); return; }
  clearTimeout(MENTION_T); MENTION_Q = q;
  MENTION_T = setTimeout(async () => {
    const sym = (q || "").length >= 2 ? api(`/api/fs/symbols?root=${encodeURIComponent(root)}&q=${encodeURIComponent(q)}`).catch(() => ({results: []})) : Promise.resolve({results: []});
    let r, s; try { [r, s] = await Promise.all([api(`/api/fs/search?root=${encodeURIComponent(root)}&q=${encodeURIComponent(q || ".")}`), sym]); } catch (e) { return; }
    if (MENTION_Q !== q || $("#input") !== inp) return;
    const put = text => {
      const cur = inp.value, p2 = inp.selectionStart, start = cur.lastIndexOf("@", p2 - 1);
      inp.value = cur.slice(0, start) + text + " " + cur.slice(p2); inp.focus();
      const c = start + text.length + 1; inp.setSelectionRange(c, c); inp.dispatchEvent(new Event("input"));
    };
    const hits = (r.results || []).sort((a, b) => a.path.length - b.path.length).slice(0, (s.results || []).length ? 5 : 8);
    const syms = (s.results || []).slice(0, 5);          // functions and classes: @path:line (name)
    if (!hits.length && !syms.length) { if ($("#pop.inline")) closePop(); return; }
    inlinePop([...hits.map(h => ({label: "@" + h.path, desc: h.dir ? "folder" : "", fn: () => put("@" + h.path)})),
      ...syms.map(x => ({label: `${x.name}`, desc: `${x.kind} · ${x.path}:${x.line}`, fn: () => put(`@${x.path}:${x.line} (${x.kind} ${x.name})`)}))]);
  }, 120);
}
/* Up and Down in an empty composer walk back through the messages you sent (newest first), as in a terminal. */
const SENT_KEY = "mp_sent";
function sentList() { try { return JSON.parse(localStorage.getItem(SENT_KEY) || "[]"); } catch (e) { return []; } }
function rememberSent(text) {
  if (!text || S.cur?.view?.incognito || S.draft.incognito) return;          // incognito chats leave nothing behind
  const l = sentList().filter(x => x !== text); l.push(text);
  try { localStorage.setItem(SENT_KEY, JSON.stringify(l.slice(-100))); } catch (e) {}
}
function recallSent(inp, dir) {
  const l = sentList(); if (!l.length) return false;
  if (S.recall == null) { if (inp.value.trim()) return false; S.recall = l.length; }
  else if (inp.value !== l[S.recall]) { S.recall = null; return false; }      // edited: stop walking history
  const n = S.recall + dir;
  if (n < 0) return true;
  if (n >= l.length) { S.recall = null; inp.value = ""; inp.dispatchEvent(new Event("input")); return true; }
  S.recall = n; inp.value = l[n]; inp.dispatchEvent(new Event("input")); inp.setSelectionRange(inp.value.length, inp.value.length);
  return true;
}
/* ================================== popovers ================================== */
function closePop() { $("#pop")?.remove(); document.removeEventListener("mousedown", popAway); }
function popAway(e) { if (!e.target.closest("#pop") && !e.target.closest(".pill") && !e.target.closest(".chip")) closePop(); }
function openPop(anchor, html, {above = true, width} = {}) {
  closePop();
  const p = document.createElement("div"); p.className = "pop"; p.id = "pop"; p.innerHTML = html; if (width) p.style.width = Math.min(width, innerWidth - 16) + "px";
  document.body.appendChild(p);
  p._place = () => {
    const r = anchor.getBoundingClientRect();
    p.style.left = Math.max(8, Math.min(innerWidth - p.offsetWidth - 8, r.left)) + "px";
    if (above && r.top > p.offsetHeight + 16) p.style.top = (r.top - p.offsetHeight - 6) + "px"; else p.style.top = Math.max(8, Math.min(innerHeight - p.offsetHeight - 8, r.bottom + 6)) + "px";
  };
  p._place();
  setTimeout(() => document.addEventListener("mousedown", popAway), 0);
  return p;
}
function inlinePop(items) {
  const anchor = $("#composer");
  const p = openPop(anchor, `<div class="pl">${items.map((it, i) => `<button class="opt ${i === 0 ? "act" : ""}" data-i="${i}"><span class="ol"><b>${esc(it.label)}</b><span class="od">${esc(it.desc || "")}</span></span></button>`).join("")}</div>`, {width: 420});
  p.classList.add("inline"); S.popItems = items; S.popAct = 0;
  $$(".opt", p).forEach(b => b.onmousedown = e => { e.preventDefault(); closePop(); items[+b.dataset.i].fn(); });
}
function popKey(k) {
  const p = $("#pop.inline"); const opts = $$(".opt", p);
  if (k === "ArrowDown") S.popAct = (S.popAct + 1) % opts.length; else if (k === "ArrowUp") S.popAct = (S.popAct - 1 + opts.length) % opts.length;
  else { const it = S.popItems[S.popAct]; closePop(); it.fn(); return; }
  opts.forEach((o, i) => o.classList.toggle("act", i === S.popAct)); opts[S.popAct].scrollIntoView({block: "nearest"});
}
function harnessPop(anchor) {
  const cur = cfg().harness;
  const groups = [["any", "Automatic"], ["cowork", "For documents and research"], ["code", "For code"]];
  const html = `<div class="pl">${groups.map(([m, lb]) => {
    const hs = S.harnesses.filter(h => !h.error && h.available !== false && h.mode === m); if (!hs.length) return "";
    return `<div class="grp">${lb}</div>` + hs.map(h => `<button class="opt" data-id="${esc(h.id)}" title="${esc(h.description || "")}" ${h.ready === false ? "style='opacity:.6'" : ""}><span class="ol">${esc(h.name)}${h.ready === false ? " · needs setup" : ""}<span class="od">${esc(h.ready === false ? (h.setup || "") : (h.tagline || h.description || ""))}</span></span>${h.id === cur ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("");
  }).join("")}</div><div class="pf"><button class="opt" id="hManage">${ICON.plus}<span class="ol">Add or manage agents</span></button></div>`;
  const p = openPop(anchor, html, {width: 380});
  $$(".opt[data-id]", p).forEach(b => b.onclick = async () => {
    closePop(); const id = b.dataset.id;
    if (harness(id).ready === false) { openSettings(harness(id).login ? "harnesses" : "general"); return; }
    if (S.cur) {                                   // the chat continues on the new agent, with what was said so far
      if (id === S.cur.view.harness.id) return;
      try { const v = await post(`/api/agent/${S.cur.id}/harness`, {harness: id, since: 1e9}); S.cur.view.harness = v.harness || harness(id); }
      catch (e) { return toast(e.message); }
      renderBar(); return;
    }
    S.draft.harness = id; renderBar(); renderHero();
  });
  $("#hManage").onclick = () => { closePop(); openSettings("harnesses"); };
}
function agentFor(prov) {   // the agent to switch to for a model the current agent cannot run
  const mine = curMode() === "code" ? "meshpool-code" : "meshpool-work";
  return harness(mine).providers?.includes(prov) ? mine : (S.harnesses.find(h => h.providers?.includes(prov) && h.available !== false) || {}).id;
}
async function connectProvider(g) {   // a provider that is not set up yet: sign in, or add its key in Settings
  closePop();
  if (g.id === "chatgpt") {
    try { const r = await post("/api/harness-login", {id: g.id}); toast(r.message); } catch (e) { return toast(e.message); }
    const t0 = Date.now(); const tick = async () => { await loadModels(true); const ok = (S.models.groups || []).find(x => x.id === g.id)?.ready; if (ok) { await loadHarnesses(); await loadSettings(); renderBar(); toast(g.name + " is connected."); } else if (Date.now() - t0 < 600000) setTimeout(tick, 4000); };
    setTimeout(tick, 4000); return;
  }
  openSettings("general");
}
async function pickModel(id) {
  const c = cfg(); const h = harness(c.harness); const prov = provOf(id);
  const auto = h.type === "auto" || !S.cur;
  if (h.providers && !h.providers.includes(prov) && !auto) {
    const to = agentFor(prov); if (!to) return toast("None of your agents can run that model.");
    const why = `${h.name} can’t run ${(S.models.groups || []).find(g => g.id === prov)?.name || "these"} models`;
    toast(`${why}, so this opened a new chat with ${harness(to).name}.`); newThread();
    S.draft.harness = to; S.draft.model = id; store("model", id); renderBar(); renderHero(); return;
  }
  if (!S.cur) { S.draft.model = id; store("model", id); renderBar(); return; }
  if (S.cur && h.type === "acp" && S.cur.view?.harness?.provider && prov !== S.cur.view.harness.provider) {
    toast(`This chat runs ${h.name} on ${S.cur.view.harness.runs_on}, so this opened a new chat for that model.`);
    newThread(); S.draft.harness = h.id; S.draft.model = id; store("model", id); renderBar(); renderHero(); return;
  }
  setModel(id);
}
function modelPop(anchor) {
  const c = cfg(); const h = harness(c.harness);
  const M = S.models || {models: [], favorites: [], groups: []};
  const own = S.cur && h.models !== "meshpool" ? (S.cur.view?.harness_models || []) : [];   // models a running agent offers itself
  // First choose where the model runs (a tab per provider), then the model.
  const keyName = (M.groups || []).find(g => g.id === "key")?.name || "Meshpool";
  const TABS = [["key", keyName, ["key"]], ["chatgpt", "ChatGPT", ["chatgpt"]]];
  let tab = (TABS.find(t => t[2].includes(provOf(c.model))) || TABS[0])[0];
  const tabBtn = ([id, name, gs]) => { const on = (M.groups || []).some(g => gs.includes(g.id) && g.ready); return `<button type="button" role="tab" data-tab="${id}" aria-selected="${id === tab}" style="flex:1;padding:6px 4px;border-radius:var(--r-s);border:1px solid ${id === tab ? "var(--pool)" : "var(--line)"};background:${id === tab ? "var(--pool-soft)" : "transparent"};color:var(--ink);font:inherit;font-size:13px;cursor:pointer">${esc(name)}${on ? "" : ` <span style="color:var(--ink-3)">· set up</span>`}</button>`; };
  const p = openPop(anchor, `<div class="ph"><div role="tablist" aria-label="Where the model runs" id="mtabs" style="display:flex;gap:4px;margin-bottom:6px"></div><input id="mq" placeholder="Search models" aria-label="Search models"></div><div class="pl" id="ml"></div>
    <div class="pf" style="display:flex;gap:4px"><button class="opt" id="mRefresh" style="flex:1">${ICON.refresh}<span class="ol">Refresh</span></button><button class="opt" id="mManage" style="flex:1">${ICON.gear}<span class="ol">Providers and keys</span></button></div>`, {width: 440});
  const draw = () => {
    $("#mtabs").innerHTML = TABS.map(tabBtn).join("");
    $$("#mtabs [data-tab]").forEach(x => x.onclick = () => { tab = x.dataset.tab; draw(); $("#mq").focus(); });
    const shown = (TABS.find(t => t[0] === tab) || TABS[0])[2];
    const q = $("#mq").value.trim().toLowerCase();
    const hit = m => !q || m.id.toLowerCase().includes(q) || modelName(m.id).toLowerCase().includes(q) || (m.provider || "").toLowerCase().includes(q);
    const row = (m, note) => `<button class="opt" data-id="${esc(m.id)}" ${note ? "style='opacity:.7'" : ""}><span class="ol" title="${esc(m.id)}">${esc(modelLabel(m.id, M.models))}${m.id === M.default ? ` <span class="cap">default</span>` : ""}${(m.caps || []).map(x => ` <span class="cap">${esc(x)}</span>`).join("")}${note ? `<span class="od">${esc(note)}</span>` : ""}</span>${m.id === c.model ? `<span class="on">${ICON.check}</span>` : ""}${provOf(m.id) === "key" ? `<span class="star ${M.favorites.includes(m.id) ? "f" : ""}" data-star="${esc(m.id)}" title="Favorite">${ICON.star}</span>` : ""}</button>`;
    let html = "";
    if (own.length) html += `<div class="grp">${esc(h.name)} on ${esc(h.runs_on || "its account")}</div>` + own.filter(m => !q || (m.name || m.id).toLowerCase().includes(q)).map(m => `<button class="opt" data-own="${esc(m.id)}"><span class="ol">${esc(m.name)}</span>${m.id === c.model || bareModel(c.model) === m.id ? `<span class="on">${ICON.check}</span>` : ""}</button>`).join("");
    const favs = M.models.filter(m => M.favorites.includes(m.id) && hit(m) && shown.includes(provOf(m.id)));
    if (favs.length && !own.length) html += `<div class="grp">Favorites</div>` + favs.map(m => row(m)).join("");
    for (const g of (M.groups || []).filter(g => shown.includes(g.id))) {
      const can = !h.providers || h.providers.includes(g.id);
      const note = can ? "" : `Runs in ${harness(agentFor(g.id) || "").name || "another agent"}`;
      html += `<div class="grp">${esc(g.name)}</div>`;
      if (!g.ready) { if (g.id === "chatgpt" && chatgptSignInGone()) continue; if (!q) html += `<button class="opt" data-connect="${esc(g.id)}"><span class="ol">${g.id === "chatgpt" ? "Sign in with ChatGPT" : "Add a key"}<span class="od">${esc(g.id === "chatgpt" ? "Opens the official sign-in. If you already use Codex on this computer, it is picked up by itself." : g.setup)}</span></span></button>`; continue; }
      const rows = g.models.filter(hit);
      if (g.error && !q) html += `<div class="pe">${esc(g.error)}</div>`;
      html += rows.map(m => row(m, note)).join("");
    }
    if (!html) html = `<div class="pe">No models match.</div>`;
    $("#ml").innerHTML = html; p._place();
    $$("#ml .opt[data-id]").forEach(b => b.onclick = async e => {
      const st = e.target.closest("[data-star]");
      if (st) { e.stopPropagation(); const on = !M.favorites.includes(st.dataset.star); try { S.models = await post("/api/models/favorite", {id: st.dataset.star, on}); Object.assign(M, S.models); draw(); } catch (err) { toast(err.message); } return; }
      closePop(); pickModel(b.dataset.id);
    });
    $$("#ml .opt[data-own]").forEach(b => b.onclick = () => { closePop(); setModel(b.dataset.own); });
    $$("#ml .opt[data-connect]").forEach(b => b.onclick = () => connectProvider((M.groups || []).find(g => g.id === b.dataset.connect)));
  };
  $("#mq").oninput = draw; draw(); $("#mq").focus();
  $("#mq").onkeydown = e => { if (e.key === "Enter") { const first = $("#ml .opt"); if (first) first.click(); } };
  $("#mRefresh").onclick = async () => { await loadModels(true); Object.assign(M, S.models); draw(); };
  $("#mManage").onclick = () => { closePop(); openSettings("general"); };
}
/* ================================== threads ================================== */
function restoreDraft() {
  let v = ""; try { v = sessionStorage.getItem("mp_draft_" + (S.cur?.id || (S.view === "project" ? "pj_" + S.pjId : "new"))) || ""; } catch (e) {}
  const inp = $("#input"); if (inp && v && !inp.value) { inp.value = v; inp.dispatchEvent(new Event("input")); }
}
// A new chat. Without an argument: a plain chat that needs no folder. With a folder path (old scripts): a chat in that
// folder. With {pid}: a chat in that project (its page, with the message box at the bottom).
function newThread(opt) {
  if (opt && typeof opt === "object" && opt.pid) return openProject(opt.pid, {focus: true});
  leaveChat(S.cur);
  S.view = "chat"; S.pjId = "";
  S.draft.project = typeof opt === "string" ? opt : "";
  S.draft.pid = "";
  S.cur = null; S.attachments = []; renderAttach(); setWorking(null); $("#col").classList.remove("loading");
  S.draft.harness = modeHarness();
  renderTop(); renderBar(); renderHero(); renderSide(); renderPanel();
  restoreDraft(); $("#input").focus();
}
const STARTERS = [
  ["Summarize a document", "Read the document I attach and write a one-page summary as a Word file."],
  ["Research a question", "Research this and give me a short answer with sources: "],
  ["Build a spreadsheet", "Build a 3-year revenue model for a SaaS company with 3 pricing tiers, as an Excel file with formulas."],
  ["Make a presentation", "Make a 6-slide deck on why engineering teams should track AI spend per team."],
  ["Analyse some data", "Analyse the spreadsheet I attach: the main trends, anything unusual, and a short summary."],
  ["Write or fix code", "Help me with some code. Here is what I need: "],
];
/* ---------- opening a chat, live delivery, scrolling ----------
   A chat is built off-screen (cur.stage) and swapped in whole, so switching never shows a blank or half-built list.
   Live events arrive over a Server-Sent Events stream (polling stays as the fallback and as a slow safety net) and are
   queued; one requestAnimationFrame loop applies them, a few characters per frame, so text flows instead of arriving
   in lumps. The reply is rendered as Markdown while it streams, into the same node it ends in. */
const CACHE = new Map();                         // chat id -> {log, total, next, epoch, view}: reopening replays it, then fetches only what is new
const colEl = () => S.cur?.stage || $("#col");
function stashChat(cur) {
  if (!cur || cur.stage || !cur.log || !cur.log.length || cur.log.length > 4000) return;
  try { flushQ(cur); } catch (e) { return; }
  const view = Object.assign({}, cur.view); delete view.events; delete view.prefill;
  CACHE.delete(cur.id); CACHE.set(cur.id, {log: cur.log.slice(), total: cur.total, next: cur.next || 0, epoch: cur.epoch, view, turn: cur.turn});
  while (CACHE.size > 6) CACHE.delete(CACHE.keys().next().value);
}
function leaveChat(cur) {
  if (!cur) return;
  clearTimeout(cur.pollT); stopStream(cur); clearTimeout(cur.sseT);
  stashChat(cur); EASE.clear(); if (typeof closePreview === "function") closePreview(true);
}
function beginStage(cur) {                       // the next events build a detached list; commitStage swaps it in
  cur.stage = document.createElement("div"); cur.group = null; cur.live = null; cur.liveText = ""; cur.thought = null; cur.plan = null; cur.planEv = null; cur.mcpNote = null; cur.route = null; cur.subs = null; cur.tools = {}; cur.q = []; cur.ready = false;
  $("#col")?.classList.add("loading");
}
function commitStage(cur) {
  const st = cur.stage; if (!st) return;
  cur.stage = null; EASE.clear();
  const col = $("#col"); col.replaceChildren(...st.childNodes); col.classList.remove("loading");
  for (const n of col.children) n.classList.remove("enter");
  S.pinned = true; scrollConv(true);
  if (cur.live) easeStart(cur.live);
}
function openThread(id, fresh) {
  const seen = S.threads.find(t => t.id === id);
  if (seen?.unread) { seen.unread = false; post(`/api/threads/${id}`, {action: "seen"}).catch(() => {}); }
  store("last_chat", id);
  S.view = "chat"; S.pjId = ""; S.draft.pid = "";
  $("#app").classList.remove("sideopen");
  leaveChat(S.cur);
  const known = S.threads.find(t => t.id === id);
  if (known) { const m = isCodeThread(known) ? "code" : "chat"; if (S.mode !== m) { S.mode = m; store("appmode", m); syncMode(); } }
  const hit = !fresh && CACHE.get(id);
  S.cur = {id, total: 0, view: fresh ? {id, state: "starting", harness: {id: S.draft.harness}, model: S.draft.model || S.models?.default, mode: S.draft.mode, events: []} : null, epoch: -1, groups: null, live: null, tools: {}, pollT: null, turn: 0, log: [], q: []};
  if (!fresh && known) S.cur.view = {id, title: known.title, state: known.state, kind: known.kind, workdir: known.workdir, scratch: known.scratch, project_id: known.project_id || "", pinned: known.pinned, harness: harness(known.harness), events: [], prefill: true};
  const cur = S.cur;
  S.pinned = true; setWorking(null);
  closePop();
  beginStage(cur);
  if (hit) {                                     // replay what we already had, off-screen, then ask only for the rest
    cur.view = Object.assign(cur.view || {}, hit.view, {events: []}); cur.total = hit.total; cur.next = hit.next; cur.epoch = hit.epoch; cur.turn = 0;
    for (const ev of hit.log) { cur.log.push(ev); applyEv(ev); }
    cur.turn = hit.turn || cur.turn; flushLive(cur); commitStage(cur); cur.cached = true;
  }
  renderSide(); if (cur.view) { renderTop(); renderBar(); } $("#input").value = ""; restoreDraft(); poll();
}
function resetChat(cur) {                        // the history changed under us (undo, rewind): rebuild from the start
  stopStream(cur); cur.total = 0; cur.next = 0; cur.log = []; cur.epoch = -2; cur.turn = 0; beginStage(cur);
}
async function poll() {
  const cur = S.cur; if (!cur) return;
  clearTimeout(cur.pollT);
  let v;
  try { v = await api(`/api/agent/${cur.id}?since=${cur.total}`); }
  catch (e) {
    if (S.cur !== cur) return;
    if (/not found/i.test(e.message)) { colEl().insertAdjacentHTML("beforeend", `<div class="note bad">This chat is gone.</div>`); commitStage(cur); return; }
    cur.fails = (cur.fails || 0) + 1;            // keep trying: one dropped request must not freeze a running chat
    let n = $("#pollErr"); if (!n && cur.fails > 1) { colEl().insertAdjacentHTML("beforeend", `<div class="note bad" id="pollErr"></div>`); n = $("#pollErr"); }
    if (n) n.textContent = `${e.message} Retrying…`;
    cur.pollT = setTimeout(poll, Math.min(15000, 500 * 2 ** cur.fails));
    return;
  }
  if (S.cur !== cur) return;
  if (cur.fails) { cur.fails = 0; $("#pollErr")?.remove(); }
  const again = ingest(cur, v);
  if (again) return poll();
  const busy = ["starting", "working", "waiting"].includes(v.state);
  if (busy) startStream(cur);
  cur.pollT = setTimeout(poll, cur.es && cur.es.readyState === 1 ? 4000 : busy ? 400 : document.hidden ? 15000 : 3000);
}
// Take one view of the chat (from a poll or the stream): queue its new events and update the chrome around them.
function ingest(cur, v) {
  if (S.cur !== cur) return;
  if (v.epoch !== cur.epoch && cur.epoch !== -1 && cur.total) { resetChat(cur); cur.epoch = v.epoch; return true; }
  const first = !cur.view || cur.view.prefill || cur.view.state === "starting" && !cur.total;
  cur.epoch = v.epoch; cur.total = Math.max(cur.total || 0, v.total || 0);
  const prevState = cur.view?.state, prevGoal = cur.view?.goal;
  const evs = v.events || []; v = Object.assign({}, v); delete v.events;
  cur.view = Object.assign(cur.view || {}, v); delete cur.view.prefill;
  const inpEl = $("#input"); if (inpEl) { inpEl.disabled = !!v.readonly; if (v.readonly) inpEl.placeholder = "This chat's folder is gone, so it can't continue. Start a new chat."; }
  let got = 0;
  for (const ev of evs) {
    if (typeof ev.i === "number") { if (ev.i < (cur.next || 0)) continue; cur.next = ev.i + 1; }   // the stream and a poll can overlap
    if (ev.type === "tool_start") { const raw = ev.name || ""; const nm = ev.mcp ? raw.split("__").slice(2).join("__") : raw; ev.label = TOOL_LABEL[nm] || nm.replace(/_/g, " "); }
    cur.q.push(ev); got++;
  }
  if (cur.stage || document.hidden) { flushQ(cur); }          // history, or a hidden tab (no animation frames): apply at once
  if (cur.stage) commitStage(cur);
  if (got || prevState !== v.state) progressSoon();
  const busy = ["starting", "working", "waiting"].includes(v.state);
  cur.busy = busy; cur.working = v.state === "working";
  if (v.state === "working") { if (!cur.since) cur.since = v.turn_started ? v.turn_started * 1000 : Date.now(); } else cur.since = null;
  if (first || prevState !== v.state || prevGoal !== v.goal || (v.title && v.title !== cur.shownTitle)) { cur.shownTitle = v.title; renderTop(); renderBar(); }
  renderTrust(cur);
  if (first && S.panel) renderPanel();
  if (first) scrollConv(true);
  if (!cur.ready) setTimeout(() => { if (S.cur === cur && !cur.stage) cur.ready = true; }, 50);
  if (prevState && prevState !== v.state && !busy) { loadThreads(); renderBranch(); if (S.panel === "diff") renderPanel(); }
  return false;
}
/* Folder trust: a project's own hooks and allow rules are ignored until the user trusts the folder (trust.py). */
function renderTrust(cur) {
  const col = $("#col"), t = cur.view?.trust, have = $("#trustBar");
  if (!col || !t || cur.trustHidden) { have?.remove(); return; }
  if (have) return;
  const d = document.createElement("div"); d.id = "trustBar"; d.className = "note warn trustbar"; d.setAttribute("role", "status");
  d.innerHTML = `${esc(t.text)} <button class="linkbtn" id="trustYes">Trust this folder</button> <button class="linkbtn" id="trustNo">Not now</button>`;
  col.prepend(d);
  $("#trustYes", d).onclick = async () => {
    if (!await ask({title: "Trust this folder?", text: `Its ${t.files.join(" and ")} will run commands and skip approval questions in every chat in ${cur.view.workdir}. Only trust folders you made or know.`, ok: "Trust"})) return;
    try { await post("/api/trust", {root: cur.view.workdir, action: "trust"}); cur.view.trust = null; d.remove(); toast("Folder trusted. New messages use its hooks and rules."); } catch (e) { toast(e.message); }
  };
  $("#trustNo", d).onclick = () => { cur.trustHidden = true; d.remove(); };
}
// ---- Server-Sent Events: the open chat's events pushed as they happen
function startStream(cur) {
  if (cur.es || cur.sseOff || !window.EventSource || S.cur !== cur) return;
  let es; try { es = new EventSource(`/api/agent/${cur.id}/events?since=${cur.total}&t=${encodeURIComponent(MP.token)}`); } catch (e) { cur.sseOff = true; return; }
  cur.es = es;
  es.onmessage = m => { let v; try { v = JSON.parse(m.data); } catch (e) { return; } cur.esFails = 0; if (S.cur === cur && ingest(cur, v)) { stopStream(cur); poll(); } };
  es.addEventListener("end", () => { stopStream(cur); if (S.cur === cur) poll(); });
  es.onerror = () => {                           // fall back to polling; try the stream again a few times
    stopStream(cur); cur.esFails = (cur.esFails || 0) + 1;
    if (cur.esFails >= 4) { cur.sseOff = true; return; }
    cur.sseT = setTimeout(() => { if (S.cur === cur && cur.busy) startStream(cur); }, 600 * cur.esFails);
    if (S.cur === cur) { clearTimeout(cur.pollT); cur.pollT = setTimeout(poll, 100); }
  };
}
function stopStream(cur) { if (cur?.es) { try { cur.es.close(); } catch (e) {} cur.es = null; } }
// ---- the paced drain: events leave the queue in order; text goes a few characters per frame
function drainQ(cur, all) {
  const q = cur.q; if (!q || !q.length) return;
  let backlog = 0; if (!all) for (const e of q) if (e.type === "text_delta") backlog += e.text.length;
  let budget = all ? Infinity : Math.max(8, Math.ceil(backlog / 8)), evs = 0;
  while (q.length && (all || evs < 3)) {
    const ev = q[0];
    if (ev.type === "text_delta") {
      if (budget <= 0) break;
      if (ev.text.length > budget) { const head = ev.text.slice(0, budget); ev.text = ev.text.slice(budget); const part = {type: "text_delta", text: head}; cur.log.push(part); applyEv(part); break; }
      q.shift(); budget -= ev.text.length;
    } else { q.shift(); evs++; }
    cur.log.push(ev); applyEv(ev);
  }
}
function flushLive(cur) { if (cur.live && cur.liveDirty) { mdRender(cur.live, cur.liveText); cur.liveDirty = false; } }
function flushQ(cur) { drainQ(cur, true); flushLive(cur); }
function frame() {
  requestAnimationFrame(frame);
  const cur = S.cur;
  if (cur && !cur.stage) { if (cur.q.length) drainQ(cur); flushLive(cur); }
  easeAll(); workTick(cur);
}
// The reply grows by easing its box open to the height of its text (a line is revealed in a few frames), so the page
// scrolls with it smoothly instead of stepping a whole line at a time.
const EASE = new Set();
function easeStart(node) { if (!node || EASE.has(node)) return; node.classList.add("rv"); if (!node.style.height) node.style.height = Math.round(node.firstElementChild?.offsetHeight || 0) + "px"; EASE.add(node); }
function easeEnd(node) { if (node) node.dataset.done = "1"; }
function easeAll() {
  for (const n of EASE) {
    if (!n.isConnected) { EASE.delete(n); n.style.height = ""; n.classList.remove("rv"); continue; }
    const inner = n.firstElementChild, target = inner ? inner.offsetHeight : 0, h = parseFloat(n.style.height) || 0, done = n.dataset.done === "1";
    if (target > h + 0.5) n.style.height = Math.min(target, h + Math.max(1, (target - h) * (target - h > 70 ? 0.25 : 0.11))).toFixed(1) + "px";
    else if (done) { n.style.height = ""; n.classList.remove("rv"); EASE.delete(n); }
  }
}
// ---- the "working" status: one slot under the composer, always the same size, updated in place
function setWorking(text, secs) {
  const slot = $("#statusSlot"); if (!slot) return;
  let wk = $("#working", slot);
  if (text == null) { wk?.remove(); return; }
  if (!wk) { wk = document.createElement("span"); wk.id = "working"; wk.className = "working"; wk.setAttribute("role", "status"); wk.innerHTML = `<span class="dot run"></span><span class="wt"></span><span class="el"></span>`; slot.appendChild(wk); }
  const wt = $(".wt", wk), el = $(".el", wk);
  if (wt.textContent !== text) wt.textContent = text;
  const e = secs == null ? "" : secs + "s"; if (el.textContent !== e) el.textContent = e;
}
function workTick(cur) {
  if (!cur) return;
  const g = cur.group; if (g && g.isConnected && !g.dataset.done && g._t0) { const el = $(".gel", g), s = Math.round((Date.now() - g._t0) / 1000) + "s"; if (el && el.textContent !== s) el.textContent = s; }
  const waiting = cur.working && ($(".appr:not(.done)") || $(".qcard:not(.done)"));
  const pd = cur.working ? planData() : null;
  if (cur.working && !waiting) setWorking(pd ? planLine(pd) : cur.live ? "Writing" : String(cur.activity || "Working").split(" ").slice(0, 3).join(" ").replace(/[:,]$/, ""), Math.round((Date.now() - (cur.since || Date.now())) / 1000));
  else if (cur.view?.state !== "starting" && Date.now() > (S.sendHold || 0)) setWorking(null);
}
// ---- scrolling: follow the end while the reader is at the end; never pull them back once they scroll up
function scrollConv(force) { const c = $("#conv"); if (!c) return; if (force) S.pinned = true; if (S.pinned) { c.scrollTop = c.scrollHeight; $(".jumpdown")?.setAttribute("hidden", ""); } }
function setupScroll() {
  const conv = $("#conv"), col = $("#col");
  const jd = Object.assign(document.createElement("button"), {className: "jumpdown", hidden: true, textContent: "Jump to latest", type: "button"});
  conv.parentElement.appendChild(jd);
  jd.onclick = () => { S.pinned = true; conv.scrollTop = conv.scrollHeight; jd.hidden = true; $("#input")?.focus({preventScroll: true}); };
  conv.addEventListener("scroll", () => { const gap = conv.scrollHeight - conv.scrollTop - conv.clientHeight; S.pinned = gap < 80; jd.hidden = gap < 160 || S.view !== "chat" || !S.cur; }, {passive: true});
  if (window.ResizeObserver) new ResizeObserver(() => { if (S.view !== "chat" || !S.cur) { jd.hidden = true; return; } if (S.pinned) conv.scrollTop = conv.scrollHeight; else { const gap = conv.scrollHeight - conv.scrollTop - conv.clientHeight; jd.hidden = gap < 160; } }).observe(col);
  requestAnimationFrame(frame);
}

/* ---------- markdown ---------- */
// One renderer for the live reply and for history. The text is cut into blocks at blank lines (a code fence is one block);
// each block becomes its own elements. When the text grows only the last, still-open block is re-rendered, so finished
// blocks are never touched and the finished reply is exactly what was on screen while it streamed.
function mdBlocks(text) {
  const out = []; let cur = [], fence = false;
  for (const ln of String(text || "").split("\n")) {
    if ((ln.split("```").length - 1) % 2) fence = !fence;
    if (!fence && !ln.trim()) { if (cur.length) { out.push(cur.join("\n")); cur = []; } continue; }
    cur.push(ln);
  }
  if (cur.length) out.push(cur.join("\n"));
  return out;
}
function mdRoot(node) { let r = node.firstElementChild; if (!r || !r.classList.contains("mdh")) { r = document.createElement("div"); r.className = "mdh"; node.textContent = ""; node.appendChild(r); node._blocks = []; } return r; }
function mdRender(node, text) {
  const root = mdRoot(node), prev = node._blocks || (node._blocks = []), srcs = mdBlocks(text);
  for (let i = 0; i < srcs.length; i++) {
    if (prev[i] && prev[i].src === srcs[i]) continue;
    const t = document.createElement("template"); t.innerHTML = linkFiles(md(srcs[i]));
    const nodes = [...t.content.childNodes], old = prev[i]?.nodes || [];
    const ref = old.length ? old[0] : (prev[i + 1]?.nodes[0] || null);
    for (const n of nodes) root.insertBefore(n, ref && ref.parentNode === root ? ref : null);
    for (const n of old) n.remove();
    prev[i] = {src: srcs[i], nodes};
  }
  while (prev.length > srcs.length) { for (const n of prev.pop().nodes) n.remove(); }
  return node;
}
function inline(s) {
  return s.replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
/* Code blocks in replies: coloured with the editor's tokenizer (editor.js), with a Copy button. */
const CODE_EXT = {python: "py", py: "py", javascript: "js", js: "js", jsx: "js", typescript: "ts", ts: "ts", tsx: "ts", json: "json",
  bash: "sh", sh: "sh", shell: "sh", zsh: "sh", console: "sh", powershell: "ps1", ps1: "ps1", sql: "sql", html: "html", xml: "xml",
  svg: "svg", css: "css", scss: "css", yaml: "yaml", yml: "yaml", toml: "toml", ini: "ini", go: "go", golang: "go", rust: "rs", rs: "rs",
  ruby: "rb", rb: "rb", php: "php", java: "java", kotlin: "kt", kt: "kt", swift: "swift", c: "c", h: "c", cpp: "cpp", "c++": "cpp",
  cs: "cs", csharp: "cs", "c#": "cs", markdown: "md", md: "md", vue: "vue", dockerfile: "dockerfile", makefile: "makefile"};
const CODE_LABEL = {py: "python", js: "javascript", ts: "typescript", sh: "shell", rs: "rust", rb: "ruby", md: "markdown"};
function codeHTML(text, lang) {
  const ext = CODE_EXT[lang];
  if (!ext || typeof edTokens !== "function" || typeof edLangOf !== "function" || text.length > 60000) return esc(text);
  try {
    const L = edLangOf(ext === "dockerfile" || ext === "makefile" ? ext : "x." + ext);
    return edTokens(text, L).map(([c, s]) => c ? `<span class="tk-${c}">${esc(s)}</span>` : esc(s)).join("");
  } catch (e) { return esc(text); }
}
document.addEventListener("click", e => {
  const b = e.target.closest?.("[data-copycode]"); if (!b) return;
  const code = b.closest(".codewrap")?.querySelector("pre code")?.textContent || "";
  navigator.clipboard.writeText(code).then(() => { b.textContent = "Copied"; setTimeout(() => { b.textContent = "Copy"; }, 1500); }, () => toast("Could not copy."));
});
function md(src) {
  const parts = String(src || "").split(/```/);
  return parts.map((p, i) => {
    if (i % 2) {
      const lang = (/^([\w+.#-]*)\n/.exec(p) || [, ""])[1].toLowerCase(), body = p.replace(/^[\w+.#-]*\n/, ""), pre = `<pre><code>${codeHTML(body, lang)}</code></pre>`;
      if (lang === "mermaid" && i < parts.length - 1) { const src = p.replace(/^[\w+.#-]*\n/, ""); return `<div class="mermaid-box" data-mermaid="${esc(src)}"><pre><code>${esc(src)}</code></pre></div>`; }
      const closed = i < parts.length - 1;          // a closed fence only: not while it streams
      const prev = (lang === "html" || lang === "svg") && closed ? `<button class="pvbtn" type="button" data-pvcode="${lang}" title="Preview this ${lang.toUpperCase()}">Preview</button>` : "";
      return `<div class="codewrap"><div class="codehd"><span>${esc(CODE_LABEL[lang] || lang || "text")}</span><span class="codeacts">${prev}<button class="pvbtn" type="button" data-copycode="1" title="Copy this code" aria-label="Copy this code">Copy</button></span></div>${pre}</div>`;
    }
    const b64 = t => btoa(unescape(encodeURIComponent(t.trim())));
    p = p.replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => `\u0001M${b64(t)}\u0002`).replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => `\u0001M${b64(t)}\u0002`)
      .replace(/\\\(([^\n]+?)\\\)/g, (_, t) => `\u0001m${b64(t)}\u0002`);
    const lines = esc(p).split("\n"); let out = "", list = null, para = [], table = [];
    const flushP = () => { if (para.length) { out += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
    const flushL = () => { if (list) { out += `<${list.t}${list.t === "ol" && list.start > 1 ? ` start="${list.start}"` : ""}>${list.items.map(x => `<li>${inline(x)}</li>`).join("")}</${list.t}>`; list = null; } };
    const flushT = () => { if (table.length) { const rows = table.filter(r => !/^\s*\|?\s*:?-{2,}/.test(r)).map(r => r.replace(/^\s*\||\|\s*$/g, "").split("|").map(c => c.trim())); out += `<table>${rows.map((r, ri) => `<tr>${r.map(c => ri ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`).join("")}</tr>`).join("")}</table>`; table = []; } };
    for (const ln of lines) {
      let m;
      if (/^\s*\|.*\|\s*$/.test(ln)) { flushP(); flushL(); table.push(ln); continue; } else flushT();
      if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) { flushP(); flushL(); out += `<h3>${inline(m[2])}</h3>`; }
      else if ((m = /^\s*[-*]\s+(.*)$/.exec(ln))) { flushP(); if (!list || list.t !== "ul") { flushL(); list = {t: "ul", items: []}; } list.items.push(m[1]); }
      else if ((m = /^\s*(\d+)[.)]\s+(.*)$/.exec(ln))) { flushP(); if (!list || list.t !== "ol") { flushL(); list = {t: "ol", items: [], start: +m[1]}; } list.items.push(m[2]); }
      else if ((m = /^&gt;\s?(.*)$/.exec(ln))) { flushP(); flushL(); out += `<blockquote>${inline(m[1])}</blockquote>`; }
      else if (!ln.trim()) { flushP(); flushL(); }
      else { flushL(); para.push(ln); }
    }
    flushP(); flushL(); flushT(); return out;
  }).join("").replace(/\u0001([Mm])([A-Za-z0-9+\/=]+)\u0002/g, (_, k, b) => `<span class="mathsrc" data-display="${k === "M" ? 1 : 0}" data-tex="${b}"></span>`);
}
function diffLines(d) { return String(d || "").split("\n").map(l => { const c = l.startsWith("+++") || l.startsWith("---") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : l.startsWith("@@") ? "d-hunk" : ""; return c ? `<span class="${c}">${esc(l)}</span>` : esc(l); }).join("\n"); }

/* ---------- events ---------- */
const TOOL_ICON = n => /bash|run|exec|command|terminal/.test(n) ? ICON.run : /edit|write|patch/.test(n) ? ICON.edit : /read|grep|glob|search|fetch|snapshot|list/.test(n) ? ICON.read : /skill/.test(n) ? ICON.skill : ICON.tool;
const TOOL_RUN = {delegate_task: "Handing off to sub-agents", bash: "Running", bash_many: "Running", read_file: "Reading", write_file: "Writing", edit: "Editing", grep: "Searching", glob: "Listing", fetch_url: "Reading a page", web_search: "Searching the web", web_research: "Researching", mcp__meshpool_web__web_search: "Searching the web", mcp__meshpool_web__web_fetch: "Reading a page", mcp__meshpool_web__web_research: "Researching", todo_write: "Updating the plan", use_skill: "Loading a skill", office_task: "Working on the file", make_document: "Making a Word document", make_pdf: "Making a PDF", make_spreadsheet: "Building a spreadsheet", make_deck: "Making a deck", analyze_data: "Analysing data", convert_document: "Converting", read_document: "Reading a document"};
const TOOL_BASE = {bash: "Run", bash_many: "Run", write_file: "Write", edit: "Edit", make_document: "Make a Word document", make_pdf: "Make a PDF", make_spreadsheet: "Build a spreadsheet", make_deck: "Make a deck"};
const TOOL_DONE = {make_document: "Made a Word document", make_pdf: "Made a PDF", make_spreadsheet: "Built a spreadsheet", make_deck: "Made a deck", analyze_data: "Analysed data", convert_document: "Converted", read_document: "Read a document", fetch_url: "Read a page", mcp__meshpool_web__web_fetch: "Read a page"};
// What a step is about, in words: a file name, a query or a command; never raw JSON.
function stepSummary(name, ev) {
  const s = String(ev.summary || "");
  if (!s.trim().startsWith("{")) return s;
  try { const a = JSON.parse(s); return String(a.filename || a.path || a.query || a.question || a.url || a.title || a.request || a.expression || "").split("\n")[0].slice(0, 120); }
  catch (e) { return ""; }
}
const TOOL_LABEL = {bash: "Ran", read_file: "Read", write_file: "Wrote", edit: "Edited", grep: "Searched", glob: "Listed", fetch_url: "Fetched", web_search: "Searched the web", web_research: "Researched", mcp__meshpool_web__web_search: "Searched the web", mcp__meshpool_web__web_fetch: "Read", mcp__meshpool_web__web_research: "Researched", mcp__meshpool_assistant__memory_save: "Saved to memory", mcp__meshpool_assistant__run_subtasks: "Ran sub-agents", run_subtasks: "Ran sub-agents", delegate_task: "Handed off to sub-agents", mcp__meshpool_assistant__schedule_task: "Scheduled", todo_write: "Updated the plan", use_skill: "Loaded skill", office_task: "Office"};
function workGroup() {
  const cur = S.cur;
  if (cur.group && cur.group.isConnected && cur.group === colEl().lastElementChild) return cur.group;
  const d = document.createElement("details"); d.className = "work"; d.open = !!S.steps; d.dataset.n = "0";
  d.innerHTML = `<summary><span class="chev">${ICON.chevR}</span><span class="gdots" aria-hidden="true"><i></i><i></i><i></i></span><span class="wl">Working</span><span class="gel"></span></summary><div class="rows"></div>`;
  d._t0 = Date.now();
  colEl().appendChild(d); cur.group = d; return d;
}
function bumpGroup(g, done) {
  const n = $$(".tool", g).length; const fails = $$(".tool .st.bad", g).length;
  $(".wl", g).textContent = `${n} step${n === 1 ? "" : "s"}${fails ? `, ${fails} failed` : ""}`;
  if (done) { g.dataset.done = "1"; g.open = !!S.steps || !!$(".appr:not(.done)", g); const el = $(".gel", g); if (el) el.textContent = ""; $(".gdots", g)?.remove(); }
}
function closeGroup() { const g = S.cur.group; if (g && g.isConnected) bumpGroup(g, true); S.cur.group = null; }
const GOAL_NEXT = "Continue working toward the goal.";
const RESUME_NEXT = "Continue where you left off.";
const stripGoal = t => String(t || "").replace(/\n*^\W*GOAL:\s*(DONE|CONTINUE)\b.*$/gim, "").trimEnd();
function applyEv(ev) {
  const cur = S.cur, col = colEl();
  const add = (html, cls) => { const d = document.createElement("div"); if (cls) d.className = cls; d.innerHTML = html; col.appendChild(d); return d; };
  if (questionEv(ev, add)) return;
  switch (ev.type) {
    case "queued": {
      const box = $("#attachRow"); $("#queuedChip")?.remove();
      if (ev.n) box.insertAdjacentHTML("afterbegin", `<span class="att-chip" id="queuedChip">${ICON.list}<span>Queued: ${esc(ev.text.slice(0, 60))}${ev.n > 1 ? ` and ${ev.n - 1} more` : ""}</span></span>`);
      break; }
    case "user": {
      if (String(ev.text).trim() === RESUME_NEXT) { closeGroup(); cur.thought = null; cur.plan = null; cur.turn += 1; cur.activity = "Thinking"; add(`${ICON.bolt}<span>Picking up where it left off</span>`, "goalturn"); break; }
      if (String(ev.text).trim() === GOAL_NEXT) { closeGroup(); cur.thought = null; cur.plan = null; cur.turn += 1; cur.activity = "Thinking"; add(`${ICON.bolt}<span>Continuing toward the goal</span>`, "goalturn"); break; }
      if (ev.steer) { closeGroup(); add(`<div class="b"><span class="tag">Added while working</span><br>${esc(ev.text)}</div>`, "msg-user"); $("#queuedChip")?.remove(); break; }
      closeGroup(); cur.thought = null; cur.plan = null; cur.turn += 1; cur.activity = "Thinking"; cur.tpaths = [];
      add(`<div class="macts" aria-hidden="false"><span class="mts">${cur.ready ? "just now" : ""}</span><button class="iconbtn" data-ma="edit" data-tip="Edit and send again" data-tip-pos="below" aria-label="Edit and send again">${ICON.pencil}</button><button class="iconbtn" data-ma="copy" data-tip="Copy" data-tip-pos="below" aria-label="Copy">${ICON.copy}</button></div><div class="b">${ev.plan ? `<span class="tag">Plan mode</span><br>` : ""}${esc(ev.text)}${ev.images && ev.images.length ? `<span class="att">${ICON.clip} ${ev.images.map(esc).join(", ")}</span>` : ""}</div>`, "msg-user");
      break; }
    case "text_delta":
      if (!cur.live) { closeGroup(); cur.live = add("", "msg-ai"); cur.liveText = ""; mdRoot(cur.live); if (!cur.stage) easeStart(cur.live); }
      cur.liveText += ev.text; cur.liveDirty = true; cur.activity = "Writing"; break;
    case "stream_reset": if (cur.live) { cur.live.remove(); cur.live = null; cur.liveDirty = false; } break;
    case "assistant":
      { const txt = stripGoal(ev.text);
      if (cur.live) { if (txt) { mdRender(cur.live, txt); easeEnd(cur.live); cur.lastAi = cur.live; } else cur.live.remove(); cur.live = null; cur.liveDirty = false; }
      else if (txt) { closeGroup(); cur.lastAi = add("", "msg-ai"); mdRender(cur.lastAi, txt); } }
      cur.lastText = ev.text || cur.lastText;
      break;
    case "tool_start": {
      const g = workGroup();
      const raw = ev.name || "";
      const name = ev.mcp ? raw.split("__").slice(2).join("__") : raw;
      const label = TOOL_RUN[name] || TOOL_LABEL[name] || (ev.mcp ? "Using " : "") + name.replace(/_/g, " ");
      const d = document.createElement("details"); d.className = "tool"; d.id = "tool-" + ev.id; d.dataset.name = name; if (ev.mcp) d.dataset.mcp = "1";
      d.innerHTML = `<summary><span class="ic">${TOOL_ICON(name)}</span><span class="tn">${esc(label)}</span><span class="ts">${esc(stepSummary(name, ev))}</span><span class="st run"></span></summary>${ev.diff ? `<pre>${diffLines(ev.diff)}</pre>` : ""}`;
      $(".rows", g).appendChild(d); cur.tools[ev.id] = d; bumpGroup(g);
      if (/(^|__|[.:])browser_open$/.test(raw) && cur.ready && typeof browserAutoOpen === "function") browserAutoOpen(cur);
      { const sm = stepSummary(name, ev); cur.activity = `${label}${sm ? " " + sm : ""}`; } break; }
    case "tool_end": {
      if (ev.output) for (const m of String(ev.output).matchAll(OUT_RX)) (cur.tpaths ||= []).push(relPath(m[1]));   // this turn's files, for the cards under the reply
      const d = cur.tools[ev.id] || document.getElementById("tool-" + ev.id); if (!d) break;
      // A step the user stopped, or one Plan mode blocked, did not fail: label it for what happened.
      const out = String(ev.output || ""), stopped = /not run: stopped by the user|^\[exit 130\]|stopped by the user|sub-agents were stopped by stop/i.test(out);
      const planned = !ev.ok && (/plan mode/i.test(out) || (cur.view?.plan && ["write", "edit", "write_file", "bash", "bash_many", "delete", "move"].includes(d.dataset.name)));
      const st = $(".st", d); st.className = "st " + (ev.ok || stopped || planned || ev.denied ? "" : "bad");
      st.textContent = ev.denied ? "not run" : ev.ok ? "" : stopped ? "stopped" : planned ? "not allowed in Plan mode" : "failed";
      const nm = d.dataset.name || "";
      $(".tn", d).textContent = ev.ok ? (TOOL_DONE[nm] || TOOL_LABEL[nm] || (d.dataset.mcp ? "Used " : "") + nm.replace(/_/g, " ")) : (TOOL_BASE[nm] || TOOL_LABEL[nm] || nm.replace(/_/g, " "));
      if (ev.output && !$("pre", d)) d.insertAdjacentHTML("beforeend", `<pre>${esc(ev.output)}</pre>`);
      const g = d.closest(".work"); if (g) bumpGroup(g);
      break; }
    case "approval": {
      const gKeep = cur.group && cur.group.isConnected && cur.group === col.lastElementChild ? cur.group : null;
      for (const t of Object.values(cur.tools || {})) { const st = $(".st.run", t); if (st) { st.textContent = "Needs your approval"; const nm = t.dataset.name || ""; $(".tn", t).textContent = TOOL_BASE[nm] || TOOL_LABEL[nm] || nm; } }
      closeGroup();
      const OPT = {allow_once: "Allow", allow_always: "Allow for this chat", reject_once: "Deny", reject_always: "Always deny"};
      const opts = ev.options && ev.options.length ? ev.options.map(o => `<button class="btn ${o.kind === "allow_once" ? "primary" : String(o.kind).startsWith("reject") ? "danger" : ""}" data-d="${esc(o.id)}">${esc(OPT[o.kind] || o.label)}</button>`).join("")
        : `<button class="btn primary" data-d="yes">Allow</button><button class="btn" data-d="always">Allow ${ev.kind === "edit" ? "all edits" : ev.kind === "bash" ? "all commands" : "this tool"} in this chat</button>${["edit", "bash", "mcp"].includes(ev.kind) ? `<button class="btn" data-d="save" title="Saves a rule for this folder in your own settings: this command and its subcommand with any arguments, file edits, or this tool. A test or build command that runs project code then runs whatever that code holds.">Always in this folder</button>` : ""}<button class="btn danger" data-d="no">Deny</button><input placeholder="Note for the agent (optional)" aria-label="Note for the agent">`;
      const title = String(ev.title || "").replace(/^(run a command|execute terminal|terminal)\s*:.*$/is, "Run this command?").replace(/^Approve edit:\s*(.+)$/i, "Change $1?");
      const g0 = gKeep;
      const d = add(`<b>${esc(title)}</b>${ev.detail ? (ev.kind === "doc" ? `<div class="docprev">${md(ev.detail)}</div>` : `<pre>${ev.kind === "edit" ? diffLines(ev.detail) : esc(ev.detail)}</pre>`) : ""}<div class="acts">${opts}</div>`, "appr");
      if (g0) { $(".rows", g0).appendChild(d); cur.group = g0; bumpGroup(g0); g0.open = true; }   // the Allow buttons must stay visible
      d.id = "appr-" + ev.id;
      $$("button", d).forEach(b => b.onclick = async () => {
        d.dataset.note = $("input", d)?.value || "";
        $$("button", d).forEach(x => x.disabled = true);
        try { await post(`/api/agent/${cur.id}/answer`, {id: ev.id, decision: b.dataset.d, note: $("input", d)?.value || ""}); clearTimeout(cur.pollT); poll(); }
        catch (e) { $$("button", d).forEach(x => x.disabled = false); toast(e.message); }
      });
      scrollConv(true); break; }
    case "approval_done": { for (const t of Object.values(cur.tools || {})) { const st = $(".st.run", t); if (st && /waiting/.test(st.textContent)) { st.textContent = ""; const nm = t.dataset.name || ""; $(".tn", t).textContent = TOOL_RUN[nm] || TOOL_LABEL[nm] || nm; } }
      const d = document.getElementById("appr-" + ev.id); if (d) { d.classList.add("done"); const a = $(".acts", d); $("pre", d)?.remove(); $(".docprev", d)?.remove(); { const bt = $("b", d); if (bt) bt.textContent = bt.textContent.replace(/\?$/, ""); } if (a) a.outerHTML = `<div class="note">— ${ev.decision === "stopped" ? "Stopped before running." : ev.decision === "no" || /deny|reject/.test(ev.decision) ? (d.dataset.note ? `Denied. Your note: ${esc(d.dataset.note)}` : "Denied.") : ev.decision === "save" ? "Allowed, and saved for this folder." : ev.decision === "always" || /always|session/.test(ev.decision) ? "Allowed for this chat." : "Allowed."}</div>`;
        const g = d.previousElementSibling; if (g?.classList.contains("work") && d === col.lastElementChild) { $(".rows", g).appendChild(d); cur.group = g; } } break; }
    case "thought": {
      if (ev.final && cur.thoughtLive) { cur.thoughtLive = false; break; }   // the saved copy of what streamed live
      if (ev.delta) cur.thoughtLive = true;
      if (!cur.thought || !cur.thought.isConnected) { closeGroup(); cur.thought = add(`<details ${S.steps ? "open" : ""}><summary>Thinking</summary><div></div></details>`, "think"); }
      const td = $("div", cur.thought); td.textContent += ev.text; td.scrollTop = td.scrollHeight; cur.activity = "Thinking"; break; }
    case "plan": { cur.planEv = ev; planSoon(); break; }       // shown in the Session panel and as one line above the composer
    case "subagent": {      // delegate_task progress: one row per sub-agent, live (agent/delegate.py)
      cur.subs = cur.subs || {};
      let box = cur.subs[ev.delegation];
      if (!box || !box.isConnected) box = document.getElementById("sub-" + ev.delegation);
      if (!box) { closeGroup(); box = add(`<b>Sub-agents</b><ol></ol>`, "planbox"); box.id = "sub-" + ev.delegation; }   // the chat's Stop stops them
      cur.subs[ev.delegation] = box;
      let li = box.querySelector(`[data-sid="${CSS.escape(String(ev.sid || ""))}"]`);
      if (!li) { li = document.createElement("li"); li.dataset.sid = ev.sid || ""; li.innerHTML = `<span class="sg"></span> <small class="sn"></small>`; $("ol", box).appendChild(li); $(".sg", li).textContent = ev.title || ""; li.className = "in_progress"; }
      const tl = ev.tool ? (TOOL_RUN[ev.tool] || TOOL_LABEL[ev.tool] || String(ev.tool).replace(/_/g, " ")) : "";
      if (ev.kind === "tool") $(".sn", li).textContent = "· " + tl + (ev.text ? " " + ev.text : "");
      if (ev.kind === "done") { $(".sn", li).textContent = "· " + (ev.text || ev.status || ""); li.className = ev.status === "completed" ? "completed" : ev.status === "interrupted" ? "stopped" : "failed"; }
      cur.activity = "Sub-agents working";
      break; }
    case "context": if (cur.view) cur.view.context = {used: ev.used, size: ev.size}; break;
    case "review": { closeGroup(); window.reviewCard010?.(ev, add); break; }   // the independent check (chat010.js)
    case "receipt": { closeGroup(); const t = String(ev.text || ""), first = t.split("\n").map(x => x.replace(/^[#*\s-]+|[*]+/g, "").trim()).find(Boolean) || "Reviewed";
      add(`<details><summary>${ICON.check2}<span>${esc(clip(first.replace(/:$/, ""), 80))}</span></summary><div class="rcbody">${md(t)}</div></details>`, "note receipt"); sessionSoon(); break; }   // details: here when opened, and in the Session panel
    case "status": {
      if (ev.using) { cur.using = ev.text; if (ev.auto) cur.auto = ev.auto; add(`<span>${esc(ev.text)}</span>`, "note using"); sessionSoon(); break; }   // one neutral line; the reason is in the Session panel
      // Housekeeping stays out of the transcript: which agent was picked and why, connector start-up, "ready". It is in the Session panel.
      if (ev.route) { cur.route = {label: String(ev.text || "").replace(/^(Harness|Working as): /, "").replace(/ \(.*$/, "").replace(/\.$/, ""), why: ""}; sessionSoon(); break; }
      if (/^Starting MCP|^MCP:|^MCP did not start|^No MCP servers/.test(ev.text || "")) { cur.mcpNote = ev.text; sessionSoon(); break; }
      if (/ is ready\. ?/.test(ev.text || "") && !cur.turn) break;
      { const nn = add(esc(ev.text), "note"); if (ev.undo && ev.undo.ids && ev.undo.ids.length) nn.dataset.undo = JSON.stringify(ev.undo.ids); } break; }
    case "error": { closeGroup(); const n = add(esc(ev.text) + (/settings/i.test(ev.text || "") ? ` <button class="linkbtn" data-os="1">Open Settings</button>` : "") + ` <button class="linkbtn" data-rp="1" title="Opens a GitHub issue with this error, your version and system. Your chat is not included.">Report this</button>`, "note bad"); $("[data-os]", n)?.addEventListener("click", () => openSettings("general")); $("[data-rp]", n)?.addEventListener("click", () => reportProblem(ev.text)); break; }
    case "interrupted": {
      settleSteps("stopped"); closeGroup();
      const n = add(esc(ev.text) + (ev.restarted ? ` <button class="linkbtn" data-cont="1">Continue</button>` : "") + (ev.resend ? ` <button class="linkbtn" data-resend="1">Put it back in the message box</button>` : ""), "note warn");
      $("[data-cont]", n)?.addEventListener("click", () => { if (cfg().busy) return; const inp = $("#input"); inp.value = RESUME_NEXT; inp.dispatchEvent(new Event("input")); sendNow(false); });
      $("[data-resend]", n)?.addEventListener("click", () => { const inp = $("#input"); inp.value = ev.resend; inp.dispatchEvent(new Event("input")); inp.focus(); });
      break; }
    case "checkpoint": {
      const t = (ev.turns || [])[0];
      if (t && cur.lastFoot && t.changed && t.changed.length && !$("[data-diff]", cur.lastFoot)) { cur.lastFoot.dataset.turn = t.n; pvTurnCards(cur, cur.lastFoot, t.n); cur.lastFoot.insertAdjacentHTML("beforeend", `<button data-diff="${t.n}">${ICON.diff} ${t.changed.length} file${t.changed.length === 1 ? "" : "s"} changed</button><button data-undo="${t.n}">${ICON.undo} Undo</button><button data-undomore="${t.n}" aria-label="More undo choices" title="Undo only the files, or only the conversation">${ICON.chev || "▾"}</button>`); bindFoot(cur.lastFoot); }
      break; }
    case "turn_end": {
      settleSteps("");
      closeGroup();
      const u = ev.turn || {};
      const foot = add("", "turnfoot"); if (u.calls) foot.title = `${u.calls} model call${u.calls === 1 ? "" : "s"} · ${fmt(u.in)} tokens in · ${fmt(u.out)} out`;
      cur.lastFoot = foot;
      const t = (cur.view?.turns || []).find(x => x.n === cur.turn);
      pvTurnCards(cur, foot, cur.turn);
      if (cur.turn && !$("[data-fork]", foot)) {
        foot.insertAdjacentHTML("beforeend", `<button data-fork="${cur.turn}" title="A new chat with the conversation up to here; this one stays as it is">${ICON.branch} Branch</button>`);
        const fb = $("[data-fork]", foot); fb.onclick = () => forkChat(+fb.dataset.fork);
      }
      if (t && t.changed && t.changed.length && !$("[data-diff]", foot)) { foot.insertAdjacentHTML("beforeend", `<button data-diff="${t.n}">${ICON.diff} ${t.changed.length} file${t.changed.length === 1 ? "" : "s"} changed</button><button data-undo="${t.n}">${ICON.undo} Undo</button><button data-undomore="${t.n}" aria-label="More undo choices" title="Undo only the files, or only the conversation">${ICON.chev || "▾"}</button>`); bindFoot(foot); }
      if (cur.view?.plan && cur.lastText) {
        const b = add(`<button class="btn primary">Build this plan</button><button class="btn">Keep planning</button>`, "buildplan");
        $$("button", b)[0].onclick = async () => { b.remove(); await setPlan(false); $("#input").value = "Go ahead and implement the plan above."; sendNow(); };
        $$("button", b)[1].onclick = () => { b.remove(); $("#input").focus(); };
      }
      break; }
  }
}
/* ---------- ask_user: questions with options ---------- */
// The agent asks 1-4 questions; each has 2-5 options (the recommended one is preselected) and a free-text "Other".
// Answers go to POST /api/agent/<id>/reply {question_id, answers: [{selected: [labels], other: text}]}.
function questionEv(ev, add) {
  if ((ev.type === "tool_start" || ev.type === "tool_end") && ev.name === "ask_user") return true;   // the card shows it
  if (ev.type === "question") { closeGroup(); questionCard(ev, add); S.cur.activity = "Waiting for your answer"; return true; }
  if (ev.type === "question_done") { questionDone(ev); return true; }
  return false;
}
// The card sits in the transcript like any message. Rows are full width; 1-9 pick a row, arrows move, Enter on a chosen row
// sends. Answered, it folds into one line (its height eases down, so nothing jumps).
const qId = ev => "q-" + String(ev.id).replace(/[^\w-]/g, "");
const QCHECK = `<svg class="qck" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>`;
function questionCard(ev, add) {
  const qs = ev.questions || [], cid = qId(ev), many = qs.length > 1;
  const html = qs.map((q, i) => {
    const multi = !!q.multi, opts = q.options || [], role = multi ? "checkbox" : "radio";
    return `<fieldset class="qq" data-i="${i}" data-multi="${multi ? 1 : 0}">
      <legend>${q.header ? `<span class="qchip">${esc(q.header)}</span>` : ""}<span class="qtext">${esc(q.question)}</span>${multi ? `<span class="qhint">Choose any</span>` : ""}</legend>
      <div class="qopts" role="${multi ? "group" : "radiogroup"}" aria-label="${esc(q.question)}">${opts.map((o, k) => `<button type="button" class="qopt" role="${role}" aria-checked="${o.recommended ? "true" : "false"}" data-l="${esc(o.label)}"><span class="qk">${k + 1}</span><span class="qbody"><span class="ql">${esc(o.label)}${o.recommended ? `<span class="qrec">Recommended</span>` : ""}</span>${o.description ? `<span class="qd">${esc(o.description)}</span>` : ""}</span>${QCHECK}</button>`).join("")}
        <label class="qopt qother" data-other="1"><span class="qk">${opts.length + 1}</span><span class="qbody"><input type="text" placeholder="Other: type your own answer" aria-label="Your own answer to: ${esc(q.question)}" autocomplete="off"></span>${QCHECK}</label>
      </div></fieldset>`;
  }).join("");
  const d = add(`<div class="qin">${html}<div class="qfoot"><span class="qnote">${ev.timeout_s ? "Or type a reply in the message box." : ""}</span><button type="button" class="btn primary qsend">${many ? "Send answers" : "Continue"}</button></div></div>`, "qcard");
  d.id = cid; d.setAttribute("role", "group"); d.setAttribute("aria-label", "A question from the agent"); d.dataset.n = qs.length;
  const sync = () => {
    $$(".qq", d).forEach(fs => { const inp = $("input", fs), oth = $(".qother", fs); oth.setAttribute("aria-checked", inp.value.trim() ? "true" : "false"); });
    const ok = $$(".qq", d).every(fs => $$(".qopt[aria-checked=true]", fs).length);
    $(".qsend", d).disabled = !ok || d.classList.contains("sent");
  };
  const pick = (fs, row) => {
    const multi = fs.dataset.multi === "1", inp = $("input", fs), rows = $$(".qopt[data-l]", fs);
    if (row.dataset.other) { inp.focus(); return; }
    const on = multi ? row.getAttribute("aria-checked") !== "true" : true;
    if (!multi) { rows.forEach(x => x.setAttribute("aria-checked", "false")); inp.value = ""; }
    row.setAttribute("aria-checked", on ? "true" : "false"); sync();
  };
  const submit = async () => {
    if ($(".qsend", d).disabled) return;
    const answers = $$(".qq", d).map(fs => ({selected: $$(".qopt[data-l][aria-checked=true]", fs).map(b => b.dataset.l), other: $("input", fs).value.trim()}));
    d.classList.add("sent"); $$("button, input", d).forEach(x => x.disabled = true);
    try { await post(`/api/agent/${S.cur.id}/reply`, {question_id: ev.id, answers}); clearTimeout(S.cur.pollT); poll(); $("#input")?.focus({preventScroll: true}); }
    catch (e) { d.classList.remove("sent"); $$("button, input", d).forEach(x => x.disabled = false); sync(); toast(e.message); }
  };
  d._submit = submit; d._pick = pick;
  $$(".qq", d).forEach(fs => {
    const multi = fs.dataset.multi === "1", inp = $("input", fs);
    $$(".qopt[data-l]", fs).forEach(b => b.onclick = () => pick(fs, b));
    $(".qother", fs).onclick = e => { if (e.target !== inp) inp.focus(); };
    inp.oninput = () => { if (!multi && inp.value.trim()) $$(".qopt[data-l]", fs).forEach(x => x.setAttribute("aria-checked", "false")); sync(); };
  });
  $(".qsend", d).onclick = submit;
  d.addEventListener("keydown", e => qKeys(e, d));
  sync();
  if (!ev.timeout_s) $$("button, input", d).forEach(x => x.disabled = true);   // answered at once (no one to ask)
  scrollConv(true);
  if (ev.timeout_s && document.activeElement === document.body) $(".qopt[aria-checked=true], .qopt", d)?.focus({preventScroll: true});
}
// Keys for the open question: inside the card, and from an empty message box so the reader never has to reach for the mouse.
function qKeys(e, d) {
  if (d.classList.contains("done") || d.classList.contains("sent") || e.isComposing || e.altKey) return;
  const inInput = e.target.tagName === "INPUT", fs = e.target.closest?.(".qq") || $(".qq", d);
  const rows = $$(".qopt[data-l], .qother input", fs), k = e.key;
  if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); const i = rows.indexOf(e.target.closest(".qopt")?.matches(".qother") ? $("input", e.target.closest(".qopt")) : e.target); rows[(i < 0 ? (k === "ArrowDown" ? 0 : rows.length - 1) : (i + (k === "ArrowDown" ? 1 : -1) + rows.length) % rows.length)]?.focus(); return; }
  if (/^[1-9]$/.test(k) && !inInput && !e.metaKey && !e.ctrlKey) { const r = rows[+k - 1]; if (r) { e.preventDefault(); if (r.tagName === "INPUT") r.focus(); else { d._pick(fs, r); r.focus(); } } return; }
  if (k === "Enter" && !e.shiftKey) {
    const onRow = e.target.classList?.contains("qopt");
    if (onRow && e.target.getAttribute("aria-checked") !== "true" && !(e.metaKey || e.ctrlKey)) return;     // the first Enter chooses (native click)
    if (e.target.classList?.contains("qsend")) return;
    e.preventDefault(); d._submit();
  }
}
document.addEventListener("keydown", e => {            // from an empty message box
  if (e.target !== $("#input") || $("#input").value || e.metaKey || e.ctrlKey) return;
  const d = [...document.querySelectorAll(".qcard:not(.done):not(.sent)")].pop(); if (!d || d.dataset.n === undefined || $(".qsend", d)?.disabled && !/^[1-9]$|Arrow/.test(e.key)) return;
  if (!(/^[1-9]$/.test(e.key) || e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter")) return;
  if (e.key === "Enter") { if ($(".qsend", d).disabled) return; e.preventDefault(); e.stopPropagation(); d._submit(); return; }
  qKeys(e, d);
}, true);
function questionDone(ev) {
  const d = document.getElementById(qId(ev)); if (!d || d.classList.contains("done")) return;
  const src = ev.source, ans = ev.answers || [], typed = src === "user" && /^You replied/.test(ev.text || "");
  const qs = $$(".qq", d).map(fs => $(".qtext", fs)?.textContent || "");
  const how = src === "stopped" ? "Stopped" : src === "timeout" ? "No answer in time, went with" : src === "user" || src === "replay" ? "" : "Went with";
  const line = (q, val) => `<div class="qa">${QCHECK}<span class="qtext">${esc(q)}</span><b>${esc(val || "No answer")}</b></div>`;
  const rows = typed ? line("You replied", String(ev.text).replace(/^You replied:?\s*/, "")) : ans.length ? qs.map((q, i) => { const a = ans[i] || {}; const v = [...(a.selected || []), ...(a.other ? [`“${a.other}”`] : [])].join(", "); return line(q, (how ? how + " " : "") + v); }).join("") : line(qs[0] || "Question", (how || "") + (ev.text ? " " + ev.text : ""));
  const h0 = d.offsetHeight; d.style.height = h0 + "px"; d.style.overflow = "hidden";
  d.classList.add("done"); d.innerHTML = `<div class="qin">${rows}</div>`;
  const h1 = $(".qin", d).offsetHeight + parseFloat(getComputedStyle(d).paddingTop) + parseFloat(getComputedStyle(d).paddingBottom);
  requestAnimationFrame(() => { d.style.height = h1 + "px"; setTimeout(() => { d.style.height = ""; d.style.overflow = ""; }, 220); });
}
// A step whose end never arrived (stopped, or a harness that does not report it) must not say "running" forever.
function settleSteps(label) {
  for (const d of Object.values(S.cur?.tools || {})) {
    const st = $(".st.run", d); if (!st) continue;
    const nm = d.dataset.name || "";
    st.className = "st"; st.textContent = label === "stopped" ? "not run" : "";
    $(".tn", d).textContent = label === "stopped" ? (TOOL_BASE[nm] || TOOL_LABEL[nm] || nm) : (TOOL_DONE[nm] || TOOL_LABEL[nm] || nm);
  }
}
function bindFoot(foot) {
  $$("[data-diff]", foot).forEach(b => b.onclick = () => { S.diffTurn = +b.dataset.diff; togglePanel(curMode() === "cowork" ? "outputs" : "diff", true); });
  $$("[data-undo]", foot).forEach(b => b.onclick = () => undoTurn(+b.dataset.undo));
  $$("[data-undomore]", foot).forEach(b => b.onclick = e => ctxMenu(e, [{head: "Undo this reply and the ones after it"},
    {label: "Only the files", desc: "Files go back; the chat stays and Meshpool is told", fn: () => undoPart(+b.dataset.undomore, "code")},
    {label: "Only the conversation", desc: "The chat goes back; files stay as they are", fn: () => undoPart(+b.dataset.undomore, "chat")}]));
}
async function forkChat(n) {                       // a new chat from this one's history up to reply n
  if (!S.cur) return;
  try {
    const v = await post(`/api/agent/${S.cur.id}/fork`, {turn: n});
    await loadThreads(); openThread(v.id);
    toast("Branched into a new chat. The original is unchanged.");
  } catch (e) { toast(e.message, 5000); }
}
/* Settings, Permissions: every saved rule, with Remove, and a way to add one. */
async function drawRules(box, r) {
  try { r = r || await api("/api/rules"); } catch (e) { box.innerHTML = `<div class="res bad">${esc(e.message)}</div>`; return; }
  const NAMES = {allow: ["Always allowed", "Runs without asking."], ask: ["Always asks", "Asks first, even with full access or an allow rule."], deny: ["Never allowed", "Refused, whatever the mode."]};
  const row = (lst, rule, folder) => `<li><span class="tg"><code>${esc(rule)}</code></span><button class="iconbtn" data-rrm="${esc(lst)}" data-rule="${esc(rule)}" ${folder ? `data-folder="${esc(folder)}"` : ""} aria-label="Remove ${esc(rule)}">${ICON.x}</button></li>`;
  box.innerHTML = `<h3 style="font-size:16px;margin:18px 0 4px">Saved rules</h3><p class="pnote">A rule is a tool name (<code>edit</code>), or <code>bash:</code> and a command with * for any text (<code>bash:git push*</code>). Saved in ${esc(r.path)}.</p>
    ${Object.entries(NAMES).map(([k, [h, d]]) => `<h4 style="margin:12px 0 4px">${h}</h4><small class="pnote">${d}</small><ul class="rows2">${(r[k] || []).map(x => row(k, x)).join("") || `<li class="pnote">None.</li>`}</ul>`).join("")}
    ${r.folders.length ? `<h4 style="margin:12px 0 4px">Allowed in one folder</h4>${r.folders.map(f => `<small class="pnote">${esc(f.folder)}</small><ul class="rows2">${f.allow.map(x => row("allow", x, f.folder)).join("")}</ul>`).join("")}` : ""}
    <div class="srow" style="margin-top:10px"><select id="ruleList" aria-label="Kind of rule"><option value="ask">Always ask</option><option value="deny">Never allow</option><option value="allow">Always allow</option></select><input id="ruleText" placeholder="bash:git push*" spellcheck="false"><button class="btn" id="ruleAdd">Add rule</button></div><div class="res" id="ruleRes"></div>`;
  const act = async body => { try { drawRules(box, await post("/api/rules", body)); } catch (e) { const x = $("#ruleRes", box); x.className = "res bad"; x.textContent = e.message; } };
  $$("[data-rrm]", box).forEach(x => x.onclick = () => act({action: "remove", list: x.dataset.rrm, rule: x.dataset.rule, folder: x.dataset.folder || ""}));
  $("#ruleAdd", box).onclick = () => act({action: "add", list: $("#ruleList", box).value, rule: $("#ruleText", box).value});
}
async function undoPart(n, what) {                 // files only, or the conversation only
  if (!S.cur || S.cur.busy) return toast("Wait for it to finish, or stop it first.");
  if (!await ask(what === "code" ? {title: "Undo only the files?", text: "Files Meshpool changed in this reply and the ones after it go back to how they were. The conversation stays.", ok: "Undo files"}
                                 : {title: "Undo only the conversation?", text: "The chat goes back to before this reply. Files stay as they are.", ok: "Undo conversation"})) return;
  const asked = threadLog().filter(e => e.type === "user" && !e.steer)[n - 1]?.text || "";
  try {
    await post(`/api/agent/${S.cur.id}/revert`, {turn: n, what, since: 1e9});
    const inp = $("#input"); if (what === "chat" && asked && !inp.value.trim()) { inp.value = asked; inp.dispatchEvent(new Event("input")); }
    renderBranch(); clearTimeout(S.cur.pollT); resetChat(S.cur); poll(); if (S.panel === "diff") renderPanel();
  } catch (e) { toast(e.message); }
}
async function undoTurn(n) {
  if (!await ask({title: "Undo this reply?", text: "Files Meshpool changed go back to how they were before it, and the conversation goes back to that point. Replies after it are undone too.", ok: "Undo changes", danger: true})) return;
  const asked = threadLog().filter(e => e.type === "user" && !e.steer)[n - 1]?.text || "";
  try { await post(`/api/agent/${S.cur.id}/revert`, {turn: n, since: 1e9}); const inp = $("#input"); if (asked && !inp.value.trim()) { inp.value = asked; inp.dispatchEvent(new Event("input")); } renderBranch(); clearTimeout(S.cur.pollT); resetChat(S.cur); poll(); if (S.panel === "diff") renderPanel(); }
  catch (e) { toast(e.message); }
}

/* ================================== right panel ================================== */
const PTABS_ALL = {session: "Session", progress: "Progress", outputs: "Artifacts", diff: "Changes", files: "Files", term: "Terminal", git: "Git and CI", preview: "Preview", browser: "Browser"};
function togglePanel(name, force) {
  if (!panelSet().includes(name)) name = panelSet()[0];
  if (S.panel === name && !force && curMode() === "cowork") store("cw_panel_closed", true);
  if (force) store("cw_panel_closed", false);
  S.panel = (S.panel === name && !force) ? null : name; store("panel", S.panel);
  renderPanel(); renderTop();
}
function setupPanelResize() {
  const w = store("pw"); if (w) document.documentElement.style.setProperty("--pw", w + "px");
  $("#rz").addEventListener("mousedown", e => {
    e.preventDefault(); const move = ev => { const nw = Math.max(320, Math.min(innerWidth - 520, innerWidth - ev.clientX)); document.documentElement.style.setProperty("--pw", nw + "px"); store("pw", nw); };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", () => document.removeEventListener("mousemove", move), {once: true});
  });
}
function fitPanel() {
  const want = (!!S.panel || (typeof PVX !== "undefined" && PVX.open)) && innerWidth < 1180 && innerWidth > 980 && !S.keepSide;   // only where both cannot fit
  if ($("#app").classList.contains("autoside") !== want) { $("#app").classList.toggle("autoside", want); renderTop(); }
}
function renderPanel() {
  const p = $("#panel");
  clearTimeout(S.spT);
  if (S.panel && !panelSet().includes(S.panel)) { S.panel = panelSet()[0]; store("panel", S.panel); }
  fitPanel(); if (S.panel !== "browser") brwStop();
  if (!S.panel) { p.hidden = true; return; }
  p.hidden = false;
  $("#ptabs").innerHTML = panelSet().map(k => [k, PTABS_ALL[k]]).map(([k, lb]) => `<button role="tab" aria-selected="${S.panel === k}" data-t="${k}">${lb}</button>`).join("") + `<span class="sp"></span><button class="iconbtn" id="pclose" aria-label="Close panel">${ICON.x}</button>`;
  $$("#ptabs [data-t]").forEach(b => b.onclick = () => togglePanel(b.dataset.t, true));
  $("#pclose").onclick = () => togglePanel(S.panel);
  const body = $("#pbody"); body.innerHTML = "";
  if (!S.cur && S.panel !== "session" && S.panel !== "browser") { body.innerHTML = `<div class="pnote">Start a chat and what it makes, changes or runs shows up here.</div>`; return; }
  ({session: panelSession, progress: panelProgress, outputs: panelOutputs, diff: panelDiff, files: panelFiles, term: panelTerm, git: panelGit,
    preview: panelPreview, browser: panelBrowser})[S.panel](body);
}

/* ---------- Cowork: progress, files made, context ---------- */
function threadLog() { return S.cur?.log || []; }
function progressSteps() {
  const log = threadLog();
  const lastUser = log.map(e => e.type).lastIndexOf("user");
  const turn = log.slice(Math.max(0, lastUser));
  const plan = [...turn].reverse().find(e => e.type === "plan");
  if (plan && (plan.entries || []).length) return {kind: "plan", items: plan.entries.map((e, i) => ({key: "p" + i, text: e.content, status: e.status}))};
  const tools = {};
  let turnIds = [];
  for (const e of turn) {
    if (e.type === "tool_start") { const nm = e.mcp ? String(e.name || "").split("__").slice(2).join("__") : e.name; const sm = stepSummary(nm, e); tools[e.id] = {name: nm, sm, text: `${TOOL_RUN[nm] || e.label || nm}${sm ? " " + sm : ""}`, status: "in_progress"}; }
    if (e.type === "tool_end" && tools[e.id] && e.ok) tools[e.id].text = `${TOOL_DONE[tools[e.id].name] || TOOL_LABEL[tools[e.id].name] || tools[e.id].name}${tools[e.id].sm ? " " + tools[e.id].sm : ""}`;
    if (e.type === "tool_end" && tools[e.id]) tools[e.id].status = tools[e.id].status === "stopped" ? "stopped" : e.ok ? "completed" : "failed";
    if (e.type === "user") turnIds = [];
    if (e.type === "tool_start") turnIds.push(e.id);
    if (e.type === "interrupted") for (const id of turnIds) { const t = tools[id]; if (t && (t.status === "in_progress" || t.status === "failed")) { t.status = "stopped"; t.text = `${TOOL_LABEL[t.name] || t.name}${t.sm ? " " + t.sm : ""} (stopped)`; } }
    if (e.type === "turn_end") for (const t of Object.values(tools)) if (t.status === "in_progress") t.status = "completed";
  }
  return {kind: "steps", items: Object.entries(tools).map(([id, t]) => Object.assign({key: "t" + id}, t))};
}
const OUT_RX = /(?:Saved(?: the [\w ]+)?|[Ww]rote|[Cc]reated)[: ]+((?:[A-Za-z]:)?[^\n"'`<>():]+?\.(?:docx|xlsx|pptx|pdf|csv|md|png|jpg|html|txt|json))(?![\w.])/g;
const relPath = p => { const n = String(p).replace(/\\/g, "/"), r = String(curRoot()).replace(/\\/g, "/").replace(/\/$/, ""); return n.startsWith(r + "/") ? n.slice(r.length + 1) : n; };
function outputFiles() {
  const seen = new Map(), rel = relPath;
  for (const t of S.cur?.view?.turns || []) for (const c of t.changed || []) if (c.status !== "deleted") seen.set(c.path, {path: c.path, turn: t.n});
  for (const e of threadLog()) if (e.type === "tool_end" && e.output) for (const m of String(e.output).matchAll(OUT_RX)) { const r = rel(m[1]); if (!r.startsWith("/") && !/^[A-Za-z]:/.test(r)) seen.set(r, {path: r}); }
  return [...seen.values()];
}
function fileIcon(p) { return ICON.files; }
// The Progress tab is built once and then patched by key, so a step finishing changes one row, not the whole panel.
function reconcile(parent, items, keyOf, make, update) {
  const have = new Map([...parent.children].map(el => [el.dataset.k, el]));
  let ref = parent.firstElementChild;
  for (const it of items) {
    const k = keyOf(it); let el = have.get(k);
    if (el) { have.delete(k); update(el, it); if (el === ref) ref = ref.nextElementSibling; else parent.insertBefore(el, ref); }
    else { el = make(it); el.dataset.k = k; parent.insertBefore(el, ref); }
  }
  for (const el of have.values()) el.remove();
}
const setTxt = (el, t) => { if (el && el.textContent !== t) el.textContent = t; };
const setCls = (el, c) => { if (el && el.className !== c) el.className = c; };
function progressSoon() { updateStep(); if (S.panel !== "progress" || S.pgT) return; S.pgT = setTimeout(() => { S.pgT = null; if (S.panel === "progress") progressUpdate(); }, 250); }
function progressUpdate() {
  const body = $("#pbody .cw"); if (!body || !S.cur) return;
  const v = S.cur.view || {}; const pr = progressSteps(); const outs = outputFiles();
  const busy = ["working", "waiting", "starting"].includes(v.state);
  const icon = st => st === "completed" ? `<span class="pi ok">${ICON.check2}</span>` : st === "failed" ? `<span class="pi bad">${ICON.x}</span>` : st === "in_progress" ? `<span class="pi run"><span class="dot run"></span></span>` : `<span class="pi">${ICON.ring}</span>`;
  const hd = $("#pgState", body);
  const stateHTML = busy ? `<span class="cwbusy">${v.state === "waiting" ? "Waiting for you" : "Working"}</span>` : v.state === "idle" && pr.items.length ? (threadLog().slice(-12).some(e => e.type === "interrupted") ? `<span class="cwstop">Stopped</span>` : `<span class="cwdone">Done</span>`) : "";
  if (hd._h !== stateHTML) { hd._h = stateHTML; hd.innerHTML = stateHTML; }
  const box = $("#pgSteps", body); let ol = $("ol", box);
  if (!pr.items.length) { ol?.remove(); setHTML(box, `<div class="pnote" style="padding:4px 0">${busy ? "Getting started..." : "No steps yet for this task."}</div>`); }
  else {
    if (!ol) { box.innerHTML = ""; box._h = null; ol = document.createElement("ol"); ol.className = "stepl"; box.appendChild(ol); }
    reconcile(ol, pr.items, it => it.key, it => { const li = document.createElement("li"); li.className = it.status; li.innerHTML = `${icon(it.status)}<span class="stx"></span>`; $(".stx", li).textContent = it.text; li.dataset.st = it.status; return li; },
      (li, it) => { if (li.dataset.st !== it.status) { li.dataset.st = it.status; li.className = it.status; $(".pi", li).outerHTML = icon(it.status); } setTxt($(".stx", li), it.text); });   // .stx: a running icon has its own inner span
  }
  const fbox = $("#pgFiles", body); let ul = $("ul", fbox);
  if (!outs.length) { ul?.remove(); setHTML(fbox, `<div class="pnote" style="padding:4px 0">Documents, sheets, decks and other files the task creates show up here.</div>`); }
  else {
    if (!ul) { fbox.innerHTML = ""; fbox._h = null; ul = document.createElement("ul"); ul.className = "outs"; fbox.appendChild(ul); }
    reconcile(ul, outs, o => o.path, o => { const li = document.createElement("li"); li.innerHTML = `<button data-open="${esc(o.path)}" title="Open ${esc(o.path)}"><span class="fi">${fileIcon(o.path)}</span><span class="fp">${esc(o.path.split("/").pop())}<small>${esc(o.path)}</small></span></button>`; $("button", li).onclick = () => openPreview(curRoot(), o.path); return li; }, () => {});
  }
  setTxt($("#pgHarness", body), v.harness?.name || ""); setTxt($("#pgModel", body), modelName(v.model || ""));
}
function panelProgress(body) {
  if (!S.cur) { body.innerHTML = `<div class="pnote">Start a task and its progress shows here: the plan, each step as it finishes, the files it makes and what it can reach.</div>`; return; }
  const v = S.cur.view || {};
  const users = threadLog().filter(e => e.type === "user");
  const skills = [...new Set(users.flatMap(u => (String(u.text).match(/\$[\w-]+/g) || [])))].filter(x => S.skills.some(k => "$" + k.name === x));
  body.innerHTML = `<div class="cw">
    <section><h4>Progress <span id="pgState"></span></h4><div id="pgSteps"></div></section>
    <section><h4>Files made</h4><div id="pgFiles"></div></section>
    <section><h4>Context</h4><ul class="ctxl">
      <li>${ICON.dir}<span><b>${esc(projName(curRoot()))}</b><small title="${esc(curRoot())}">${esc(shortPath(curRoot()))}</small></span><button class="linkbtn" id="cwOpen" aria-label="Browse the project's files">Browse files</button></li>
      <li>${ICON.cpu}<span><b id="pgHarness"></b><small id="pgModel"></small></span></li>
      <li>${ICON.plug}<span><b>Connectors</b><small id="cwConn">Loading...</small></span><button class="linkbtn" id="cwConnMgr">Manage</button></li>
      ${skills.length ? `<li>${ICON.skill}<span><b>Skills</b><small>${esc(skills.join(", "))}</small></span></li>` : ""}
    </ul></section></div>`;
  progressUpdate();
  $("#cwOpen").onclick = () => openFolderInApp(curRoot(), "");
  $("#cwConnMgr").onclick = () => openSettings("connectors");
  (S.connCache ? Promise.resolve(S.connCache) : api("/api/connectors").then(r => (S.connCache = r.connectors))).then(cs => {
    const on = cs.filter(c => c.connected && c.enabled).map(c => c.name); const el = $("#cwConn"); if (el) el.textContent = on.length ? on.join(", ") : "None connected";
  }).catch(() => {});
}
function panelOutputs(body) {
  const outs = S.cur ? outputFiles() : [];
  body.innerHTML = `<div class="pbar"><span class="chip">${ICON.dir}${esc(projName(curRoot()))}</span><span style="flex:1"></span><button class="btn" id="oOpen" aria-label="Show every file in the folder">Browse files</button></div>
    ${outs.length ? `<ul class="outs" style="padding:10px">${outs.map(o => `<li><button data-open="${esc(o.path)}"><span class="fi">${fileIcon(o.path)}</span><span class="fp">${esc(o.path.split("/").pop())}<small>${esc(o.path)}</small></span></button></li>`).join("")}</ul>` : `<div class="pnote">Files this chat makes or changes show here. Click one to preview it.</div>`}
    <div style="border-top:1px solid var(--line-2);flex:1;min-height:160px;display:flex;flex-direction:column"><div class="pnote" style="padding:8px 12px 0">All files in the folder</div><div style="flex:1;overflow:auto" id="tree"></div></div></div>`;
  $("#oOpen").onclick = () => $("#tree")?.scrollIntoView({block: "start", behavior: "smooth"});
  $$("[data-open]", body).forEach(b => b.onclick = () => openPreview(curRoot(), b.dataset.open));
  fsTree($("#tree"), curRoot(), {open: S.fsOpen?.root === curRoot() ? S.fsOpen.path : ""}); S.fsOpen = null;
}
async function panelDiff(body) {
  if (!S.cur) { body.innerHTML = `<div class="pnote">This chat hasn't changed any files yet. Each reply's changes show here, and you can undo them.</div>`; return; }
  const turns = S.cur.view?.turns || [];
  const sel = S.diffTurn ?? "";
  body.innerHTML = `<div class="pbar"><select id="dturn" aria-label="Which changes"><option value="">All changes in this chat</option>${turns.slice().reverse().map(t => `<option value="${t.n}" ${+sel === t.n ? "selected" : ""}>Reply ${t.n}${t.title ? ": " + esc(clip(t.title, 50)) : ""}</option>`).join("")}</select>
    <button class="btn" id="dref" title="Refresh">${ICON.refresh}</button><button class="btn" id="dsplit" aria-pressed="${store("diffSplit") === "1"}" title="Show old and new next to each other">${store("diffSplit") === "1" ? "Unified" : "Side by side"}</button>${sel && (turns.find(t => t.n === +sel)?.changed || []).length ? `<button class="btn danger" id="dundo">${ICON.undo} Undo</button>` : ""}</div><div id="dbody" class="pbody"><div class="pnote">Loading...</div></div>`;
  $("#dturn").onchange = () => { S.diffTurn = $("#dturn").value ? +$("#dturn").value : null; renderPanel(); };
  $("#dref").onclick = renderPanel;
  $("#dsplit").onclick = () => { store("diffSplit", store("diffSplit") === "1" ? "0" : "1"); renderPanel(); };
  $("#dundo")?.addEventListener("click", () => undoTurn(+sel));
  try {
    const d = await api(`/api/agent/${S.cur.id}/diff${sel ? "?turn=" + sel : ""}`);
    if (!d.files.length) { $("#dbody").innerHTML = `<div class="pnote">${esc(d.note || S.cur.view?.checkpoints_note || (sel ? "This reply didn't change any files." : "This chat hasn't changed any files yet. Other changes in this folder are under Git and CI."))}</div>`; return; }
    const add = d.files.reduce((a, f) => a + (f.add || 0), 0), del = d.files.reduce((a, f) => a + (f.del || 0), 0);
    $("#dbody").innerHTML = `<div class="pnote" style="padding:8px 12px">${d.files.length} file${d.files.length === 1 ? "" : "s"} · <span style="color:var(--add)">+${add}</span> <span style="color:var(--del)">−${del}</span></div>
      <ul class="flist">${d.files.map((f, i) => `<li data-i="${i}"><span class="badge ${f.status}">${f.status[0].toUpperCase()}</span><span class="p">${esc(String(f.path).replace(/^"|"$/g, ""))}</span><span class="a">+${f.add || 0}</span><span class="r">−${f.del || 0}</span></li>`).join("")}</ul>
      ${store("diffSplit") === "1" ? `<div class="sbs-wrap" id="patch">${splitHTML(d.patch)}</div>` : `<pre class="patch" id="patch">${patchHTML(d.patch)}</pre>`}`;
    $$("#dbody .flist li").forEach(li => li.onclick = () => { const f = d.files[+li.dataset.i]; const el = $(`#patch [data-file="${CSS.escape(f.path)}"]`); el?.scrollIntoView({block: "start"}); });
  } catch (e) { $("#dbody").innerHTML = `<div class="pnote">${esc(e.message)}</div>`; }
}
/* The same patch with old and new next to each other: removed lines on the left, added on the right, paired in
   order within each run of changes; unchanged lines on both sides. */
function splitHTML(p) {
  const rows = []; let o = 0, n = 0, dels = [], adds = [];
  const cell = (num, text, cls) => `<td class="ln">${num ?? ""}</td><td class="tx ${cls}">${text === null ? "" : esc(text) || " "}</td>`;
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) {
      const d = dels[i], a = adds[i];
      rows.push(`<tr>${d ? cell(d[0], d[1], "d") : cell(null, null, "e")}${a ? cell(a[0], a[1], "a") : cell(null, null, "e")}</tr>`);
    }
    dels = []; adds = [];
  };
  for (const l of String(p || "").split("\n")) {
    if (l.startsWith("--- a/") || l.startsWith("--- /dev/null")) continue;
    if (l.startsWith("+++ ") || l.startsWith("diff --meshpool")) {
      flush();
      const f = l.startsWith("+++ ") ? l.slice(4).replace(/^b\//, "") : (l.split(" b/")[1] || l);
      rows.push(`<tr class="f" data-file="${esc(f)}"><td colspan="4">${esc(f)}</td></tr>`); continue;
    }
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(l);
    if (h) { flush(); o = +h[1]; n = +h[2]; rows.push(`<tr class="h"><td colspan="4">${esc(l)}</td></tr>`); continue; }
    if (l.startsWith("-")) { dels.push([o++, l.slice(1)]); continue; }
    if (l.startsWith("+")) { adds.push([n++, l.slice(1)]); continue; }
    flush();
    if (l.startsWith("\\") || !l) continue;
    const t = l.slice(1); rows.push(`<tr>${cell(o++, t, "")}${cell(n++, t, "")}</tr>`);
  }
  flush();
  return `<table class="sbs">${rows.join("")}</table>`;
}
function patchHTML(p) {
  return String(p || "").split("\n").map(l => {
    if (l.startsWith("--- a/")) return "";
    if (l.startsWith("+++ b/")) { const f = l.slice(6); return `<span class="l f" data-file="${esc(f)}">${esc(f)}</span>`; }
    if (l.startsWith("diff --meshpool")) return `<span class="l f">${esc(l.split(" b/")[1] || l)}</span>`;
    const c = l.startsWith("@@") ? "h" : l.startsWith("+") ? "a" : l.startsWith("-") ? "d" : "";
    return `<span class="l ${c}">${esc(l) || " "}</span>`;
  }).join("");
}
async function panelFiles(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  body.innerHTML = `<div class="pbar"><span class="chip" title="${esc(root)}">${ICON.dir}${esc(projName(root))}</span><span style="flex:1"></span><button class="btn" id="fref" aria-label="Refresh the file list" title="Refresh">${ICON.refresh}</button><button class="iconbtn" id="fmore" aria-label="More actions for this folder" title="More actions">${ICON.more}</button></div><div style="flex:1;overflow:auto;min-height:120px" id="tree"></div>`;
  $("#fref").onclick = renderPanel;
  $("#fmore").onclick = e => ctxMenu(e, [{label: "Copy folder path", icon: ICON.copy, fn: () => { try { navigator.clipboard.writeText(root); toast("Path copied", 2000); } catch (err) { toast("Could not copy the path."); } }}, {sep: true}, {label: edRevealLabel(), icon: ICON.ext, fn: () => edReveal(root, "")}]);
  const open = S.fsOpen?.root === root ? S.fsOpen.path : (PVX.open && PVX.root === root ? PVX.path : ""); S.fsOpen = null;
  fsTree($("#tree"), root, {open});
}
function panelTerm(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  S.term = S.term && S.term.root === root ? S.term : {root, html: "", hist: [], hi: 0, id: null};
  body.innerHTML = `<pre class="term-out" id="tout">${S.term.html || `<span class="rc">Commands run in ${esc(shortPath(root))}</span>\n`}</pre><div class="term-in">${termShellPicker()}<input id="tin" placeholder="Run a command" spellcheck="false" autocomplete="off"><button class="btn" id="tstop" hidden>Stop</button></div>`;
  const out = $("#tout"), inp = $("#tin"); out.scrollTop = out.scrollHeight; inp.focus();
  const save = () => { S.term.html = out.innerHTML; };
  inp.onkeydown = async e => {
    if (e.key === "ArrowUp") { e.preventDefault(); if (S.term.hist.length) { S.term.hi = Math.max(0, S.term.hi - 1); inp.value = S.term.hist[S.term.hi] || ""; } return; }
    if (e.key === "ArrowDown") { e.preventDefault(); S.term.hi = Math.min(S.term.hist.length, S.term.hi + 1); inp.value = S.term.hist[S.term.hi] || ""; return; }
    if (e.key !== "Enter") return;
    const cmd = inp.value.trim(); if (!cmd) return;
    if (cmd === "clear") { out.innerHTML = ""; inp.value = ""; save(); return; }
    S.term.hist.push(cmd); S.term.hi = S.term.hist.length; inp.value = "";
    out.insertAdjacentHTML("beforeend", `<span class="cmd">$ ${esc(cmd)}</span>\n`);
    try {
      const t = await post("/api/term", {root, command: cmd, shell: store("term_shell") || S.settings?.shells?.default || ""}); S.term.id = t.id; $("#tstop").hidden = false;
      let since = 0;
      const tick = async () => {
        const r = await api(`/api/term/${t.id}?since=${since}`); since = r.next;
        if (r.lines.length) { out.insertAdjacentText("beforeend", r.lines.join("")); out.scrollTop = out.scrollHeight; }
        if (r.running) setTimeout(tick, 250); else { out.insertAdjacentHTML("beforeend", r.rc ? `<span class="rc">[exit ${r.rc}]</span>\n` : ""); $("#tstop") && ($("#tstop").hidden = true); save(); }
      };
      tick();
    } catch (err) { out.insertAdjacentText("beforeend", err.message + "\n"); save(); }
  };
  $("#tstop").onclick = () => S.term.id && post(`/api/term/${S.term.id}/stop`).catch(() => {});
}
async function panelGit(body) {
  const root = curRoot(); if (!root) { body.innerHTML = `<div class="pnote">Choose a project folder first.</div>`; return; }
  body.innerHTML = `<div class="pnote">Loading...</div>`;
  let g; try { g = await api("/api/git?root=" + encodeURIComponent(root)); } catch (e) { body.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; return; }
  if (!g.git) { body.innerHTML = `<div class="pnote">Git is not installed on this computer.${isMac ? " Open Terminal and run <code>xcode-select --install</code>, then come back." : ""}</div>`; return; }
  if (!g.repo) { body.innerHTML = `<div class="gitbox"><div class="stat">This folder is not a Git repository.</div><div class="row"><button class="btn primary" id="ginit">Start a Git repository here</button></div></div>`; $("#ginit").onclick = () => gitAct({action: "init"}); return; }
  body.innerHTML = `<div class="gitbox">
    <div><h4>Branch</h4><div class="row" style="margin-top:6px"><select id="gbr">${g.branches.map(b => `<option ${b === g.branch ? "selected" : ""}>${esc(b)}</option>`).join("")}</select></div>
      <div class="row" style="margin-top:6px"><input id="gnew" placeholder="New branch name"><button class="btn" id="gcreate">Create</button></div>
      <div class="stat" style="margin-top:6px">${g.upstream ? `${g.ahead} to push, ${g.behind} to pull` : "Not pushed yet"}${g.remote ? ` · ${esc(g.github || g.remote)}` : " · no remote"}</div></div>
    <div><h4>Changes (${g.dirty})${g.staged ? ` · ${g.staged} staged` : ""}</h4><ul class="flist" style="border:1px solid var(--line-2);border-radius:var(--r-s);margin-top:6px;max-height:180px;overflow:auto">${g.files.map((f, i) => `<li><span class="badge modified">${esc(GIT_WORD[String(f.status).trim()] || f.status)}</span><span class="p">${esc(f.path)}</span>${f.unstaged ? `<button class="linkbtn" data-gst="${i}" title="Stage this file for the next commit">Stage</button>` : ""}${f.staged ? `<button class="linkbtn" data-gun="${i}" title="Take it out of the next commit; the change stays">Unstage</button>` : ""}</li>`).join("") || `<li class="pnote" style="padding:6px 10px">Nothing to commit.</li>`}</ul></div>
    <div><h4>Commit</h4><textarea id="gmsg" rows="2" placeholder="What changed and why" style="margin-top:6px"></textarea><div class="row" style="margin-top:6px">${g.staged ? `<button class="btn primary" id="gcommitst">Commit staged (${g.staged})</button>` : ""}<button class="btn ${g.staged ? "" : "primary"}" id="gcommit" ${g.dirty ? "" : "disabled"}>Commit all</button><button class="btn" id="gpush" ${g.remote ? "" : "disabled"}>Push</button><button class="btn" id="gpull" ${g.upstream ? "" : "disabled"}>Pull</button></div></div>
    <div id="chgBox"></div>
    ${g.log?.length ? `<details class="cil"><summary><b>Recent commits</b> <span class="stat">${g.log.length}</span></summary><ul class="flist glog">${g.log.map(c => `<li title="${esc(c.author)}"><code>${esc(c.hash)}</code> <span class="cn">${esc(c.subject)}</span> <span class="stat">${esc(c.when)}</span></li>`).join("")}</ul></details>` : ""}
    <div id="wtBox"></div>
    <div id="ciBox"><h4>Pull request, checks and deployments</h4><div class="pnote" style="padding:6px 0">Loading from GitHub...</div></div>
    <div><h4>Open a pull request</h4><input id="gprt" placeholder="Title (defaults to the last commit)" style="margin-top:6px"><textarea id="gprb" rows="3" placeholder="Description" style="margin-top:6px"></textarea><div class="row" style="margin-top:6px"><button class="btn" id="gpr" ${g.github ? "" : "disabled"}>Open pull request</button>${g.github ? "" : `<span class="stat">Needs a GitHub remote.</span>`}</div><div class="stat" id="gres"></div></div>
  </div>`;
  $("#gbr").onchange = () => gitAct({action: "checkout", branch: $("#gbr").value});
  $("#gcreate").onclick = () => gitAct({action: "checkout", branch: $("#gnew").value, create: true});
  $("#gcommit").onclick = () => gitAct({action: "commit", message: $("#gmsg").value});
  $("#gcommitst")?.addEventListener("click", () => gitAct({action: "commit", message: $("#gmsg").value, staged_only: true}));
  $$("[data-gst]", body).forEach(b => b.onclick = () => gitAct({action: "stage", paths: [g.files[+b.dataset.gst].path]}));
  $$("[data-gun]", body).forEach(b => b.onclick = () => gitAct({action: "unstage", paths: [g.files[+b.dataset.gun].path]}));
  $("#gpush").onclick = () => gitAct({action: "push"});
  $("#gpull").onclick = () => gitAct({action: "pull"});
  $("#gpr").onclick = () => gitAct({action: "pr", title: $("#gprt").value, body: $("#gprb").value});
  api("/api/git/changes?root=" + encodeURIComponent(root)).then(ch => { const b = $("#chgBox"); if (b) b.innerHTML = changesHTML(ch, g.branch); }).catch(() => {});
  drawWorktrees(root);
  if (g.github) drawCI(root); else $("#ciBox").innerHTML = `<h4>Pull request, checks and deployments</h4><div class="stat">Pull requests, checks and deployments show here for repositories on GitHub.</div>`;
}
/* Worktrees meshpool made for this repository: a second checkout on its own branch, so a task never edits this one. */
async function drawWorktrees(root) {
  const box = $("#wtBox"); if (!box) return;
  let r; try { r = await api("/api/worktree?root=" + encodeURIComponent(root)); } catch (e) { box.innerHTML = ""; return; }
  const ws = r.worktrees || [];
  const trim = x => String(x || "").replace(/[\\/]+$/, "");
  const here = w => trim(curRoot()) === trim(w.path);
  const state = w => w.missing ? "folder missing" : [w.dirty ? "uncommitted changes" : "", w.unpushed ? `${w.unpushed} commit${w.unpushed === 1 ? "" : "s"} not on another branch` : ""].filter(Boolean).join(", ") || "no changes";
  box.innerHTML = `<h4>Worktrees (${ws.length})</h4>
    ${ws.length ? `<ul class="flist wtl" style="border:1px solid var(--line-2);border-radius:var(--r-s);margin-top:6px">${ws.map((w, i) => `<li data-wt="${i}"><span class="cn"><b>${esc(w.name)}</b> <span class="dir">${esc(w.branch || "detached")}</span><br><span class="stat">${esc(state(w))}${here(w) ? " · this chat works here" : ""}</span></span>
      <span class="row" style="gap:4px">${w.missing || here(w) ? "" : `<button class="linkbtn" data-wto="${i}">New chat here</button>`}<button class="linkbtn" data-wtr="${i}">Remove</button></span></li>`).join("")}</ul>`
      : `<div class="stat" style="margin-top:6px">None. A worktree is a second checkout of this repository on its own branch, so a task can work there without touching this one.</div>`}
    <div class="row" style="margin-top:6px"><button class="btn" id="wtNew">New worktree</button></div>`;
  $("#wtNew").onclick = async () => { try { const w = await post("/api/worktree", {root, action: "create"}); toast(`Made ${w.name} on branch ${w.branch}`); drawWorktrees(root); } catch (e) { toast(e.message, 5000); } };
  $$("[data-wto]", box).forEach(b => b.onclick = () => newThread(ws[+b.dataset.wto].path));
  $$("[data-wtr]", box).forEach(b => b.onclick = async () => {
    const w = ws[+b.dataset.wtr];
    const lose = w.dirty ? " Its uncommitted changes are lost." : "";
    const keep = w.unpushed ? ` Its branch ${w.branch} stays, with its commits.` : ` The branch ${w.branch} goes too when nothing would be lost.`;
    if (!await ask({title: `Remove the worktree ${w.name}?`, text: `This deletes its folder.${lose}${keep}`, ok: w.dirty ? "Remove and discard changes" : "Remove"})) return;
    try { const x = await post("/api/worktree", {root, action: "remove", path: w.path, force: !!(w.dirty || w.unpushed)}); toast(`Removed ${w.name}` + (x.branch_deleted ? "" : `; branch ${x.branch} kept`)); drawWorktrees(root); }
    catch (e) { toast(e.message, 6000); }
  });
}
const CK = {success: ["ok", "Passed"], failure: ["bad", "Failed"], timed_out: ["bad", "Timed out"], cancelled: ["bad", "Cancelled"],
  action_required: ["bad", "Needs action"], skipped: ["", "Skipped"], neutral: ["", "Neutral"]};
function ckState(c) { if (c.status && c.status !== "completed") return ["run", c.status === "queued" ? "Queued" : "Running"]; return CK[c.conclusion] || ["", c.conclusion || "Done"]; }
/* What the branch (and its pull request) adds and removes, file by file, against the branch it merges into. */
const CH_BADGE = {added: ["added", "Added"], deleted: ["deleted", "Removed"], renamed: ["modified", "Renamed"], copied: ["modified", "Copied"], changed: ["modified", "Changed"]};
function changesHTML(ch, branch) {
  if (!ch || ch.same) return "";                                  // on the base branch: nothing to compare
  const base = String(ch.base || "").replace(/^origin\//, "");
  if (!ch.files?.length) return `<div class="cil"><b>Changes in this branch</b><div class="stat">${esc(ch.note || `${branch} has nothing that ${base || "the base branch"} does not have yet.`)}</div></div>`;
  const n = ch.count || ch.files.length; const by = k => ch.files.filter(f => f.status === k).length;
  const parts = [["added", "added"], ["changed", "changed"], ["deleted", "removed"], ["renamed", "renamed"]].map(([k, w]) => by(k) ? `${by(k)} ${w}` : "").filter(Boolean).join(", ");
  const num = (v, cl, sign) => `<span class="${v ? cl : "zero"}">${sign}${v}</span>`;
  const name = p => { const i = p.lastIndexOf("/"); return i < 0 ? `<b>${esc(p)}</b>` : `<b>${esc(p.slice(i + 1))}</b> <span class="dir">${esc(p.slice(0, i))}</span>`; };
  return `<details class="cil chg" open><summary><b>Changes against ${esc(base)}</b> <span class="stat">${n} file${n === 1 ? "" : "s"} (${parts}) · <span class="nw">${num(ch.added, "plus", "+")} ${num(ch.removed, "minus", "−")}</span></span></summary>
    <ul>${ch.files.map(f => { const [cl, w] = CH_BADGE[f.status] || CH_BADGE.changed; return `<li title="${esc(f.from ? f.from + " → " + f.path : f.path)}"><span class="badge ${cl}">${w}</span><span class="cn">${name(f.path)}${f.from ? ` <span class="dir">from ${esc(f.from)}</span>` : ""}</span><span class="cs nw">${f.binary ? "binary" : `${num(f.added, "plus", "+")} ${num(f.removed, "minus", "−")}`}</span></li>`; }).join("")}</ul>
    ${n > ch.files.length ? `<div class="stat">and ${n - ch.files.length} more</div>` : ""}</details>`;
}
async function drawCI(root, fresh) {
  const box = $("#ciBox"); if (!box) return;
  let ci;
  try { ci = await api("/api/git/ci?root=" + encodeURIComponent(root)); } catch (e) { box.innerHTML = `<h4>Pull request, checks and deployments</h4><div class="stat">${esc(e.message)}</div>`; return; }
  S.ciCache = {...(S.ciCache || {}), [root]: {t: Date.now(), ci}};
  if (!$("#ciBox")) return;
  const pr = ci.pr; const failingRuns = ci.runs.filter(r => r.conclusion === "failure");
  box.innerHTML = `<h4>Pull request, checks and deployments <button class="iconbtn" id="ciRef" title="Refresh" aria-label="Refresh checks">${ICON.refresh}</button> <button class="linkbtn" id="ciView">Open the full view</button></h4>
    ${!ci.connected ? `<div class="stat">Connect GitHub to see private repositories and to rerun or merge. <button class="linkbtn" id="ciConn">Connect GitHub</button></div>` : ""}
    ${ci.error ? `<div class="stat">${esc(ci.error)}</div>` : ""}
    ${pr ? `<div class="prcard"><a href="${esc(pr.url)}" target="_blank" rel="noopener"><b>#${pr.number} ${esc(pr.title)}</b></a><span class="badge ${pr.state === "open" ? "added" : pr.state === "merged" ? "modified" : "deleted"}">${pr.draft ? "Draft" : pr.state === "open" ? "Open" : pr.state === "merged" ? "Merged" : "Closed"}</span><small>${esc(ci.branch)} into ${esc(pr.base)}${pr.author ? " by " + esc(pr.author) : ""}</small>
      ${pr.state === "open" && ci.connected ? `<div class="row" style="margin-top:6px"><button class="btn" id="ciMerge" ${ci.summary === "failing" ? "disabled title='Fix the failing checks first'" : ""}>Squash and merge</button></div>` : ""}</div>`
      : `<div class="stat">No pull request for ${esc(ci.branch)} yet.</div>`}
    <div class="cil"><b>Checks</b> <span class="stat">${esc(ci.sha)}</span>${ci.checks.length ? `<ul>${ci.checks.map(c => { const [cl, tx] = ckState(c); return `<li><span class="cidot ${cl}"></span><span class="cn">${esc(c.name)}</span><span class="cs">${tx}</span>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Details</a>` : ""}</li>`; }).join("")}</ul>` : `<div class="stat">No checks on this commit.</div>`}</div>
    ${ci.runs.length ? `<div class="cil"><b>Workflow runs (CI)</b><ul>${ci.runs.map(r => { const [cl, tx] = ckState(r); return `<li><span class="cidot ${cl}"></span><span class="cn">${esc(r.name)}</span><span class="cs">${tx}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">Logs</a>${r.conclusion === "failure" && ci.connected ? `<button class="linkbtn" data-rerun="${r.id}">Rerun failed</button>` : ""}</li>`; }).join("")}</ul></div>` : ""}
    <div class="cil"><b>Deployments (CD)</b>${ci.deployments.length ? `<ul>${ci.deployments.map(d => `<li><span class="cidot ${d.state === "success" || d.state === "active" ? "ok" : d.state === "failure" || d.state === "error" ? "bad" : d.state === "in_progress" || d.state === "pending" || d.state === "queued" ? "run" : ""}"></span><span class="cn">${esc(d.environment || "deployment")}</span><span class="cs">${esc(d.state)} · ${esc(d.ref || d.sha)}</span>${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">Open</a>` : ""}</li>`).join("")}</ul>` : `<div class="stat">No deployments recorded on GitHub.</div>`}</div>
    ${failingRuns.length ? `<div class="row" style="margin-top:8px"><button class="btn primary" id="ciFix">Ask the agent to fix the failing CI</button></div>` : ""}`;
  $("#ciRef").onclick = () => drawCI(root, true);
  $("#ciView").onclick = () => openPRView(root, pr?.number);
  $("#ciConn")?.addEventListener("click", () => openSettings("connectors"));
  $("#ciMerge")?.addEventListener("click", async () => { if (!await ask({title: `Merge #${pr.number}?`, text: `Squash and merge ${ci.branch} into ${pr.base} on GitHub.`, ok: "Merge"})) return; try { await post("/api/git/ci", {root, action: "merge", pr: pr.number}); toast("Pull request merged"); drawCI(root, true); } catch (e) { toast(e.message, 5000); } });
  $$("[data-rerun]", box).forEach(b => b.onclick = async () => { try { await post("/api/git/ci", {root, action: "rerun", run: b.dataset.rerun}); toast("Rerunning the failed jobs"); setTimeout(() => drawCI(root, true), 3000); } catch (e) { toast(e.message, 5000); } });
  $("#ciFix")?.addEventListener("click", () => { const i = $("#input"); i.value = `CI is failing on ${ci.branch}: ${failingRuns.map(r => r.name + " (" + r.url + ")").join(", ")}. Find out why from the logs (use gh or the GitHub tools), fix it, run the tests, commit and push.`; i.focus(); i.dispatchEvent(new Event("input")); });
}
async function gitAct(b) {
  const root = curRoot();
  try {
    const r = await post("/api/git", {root, ...b});
    if (b.action === "pr" && r.pr) { renderPanel(); setTimeout(() => { const el = $("#gres"); if (el) el.innerHTML = `${esc(r.note || "Pull request ready:")} <a href="${esc(r.pr)}" target="_blank" rel="noopener">${esc(r.pr)}</a>`; }, 300); }
    else { toast({checkout: "Switched branch", commit: "Committed", push: "Pushed", pull: "Pulled", init: "Repository created", stage: "Staged", unstage: "Unstaged"}[b.action] || "Done"); renderPanel(); }
    renderBranch();
  } catch (e) { toast(e.message, 5000); }
}
function panelPreview(body) {
  const url = store("preview") || "http://localhost:3000";
  body.innerHTML = `<div class="pbar"><input id="purl" value="${esc(url)}" aria-label="Address"><button class="btn" id="pgo">Open</button><button class="btn" id="pext" title="Open in your browser">${ICON.globe}</button></div><div class="pnote" id="pempty" style="padding:6px 12px">Start your app first (for example ask Meshpool to run it), then enter its address.</div><iframe class="preview" id="pframe" hidden title="Preview" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`;
  const go = () => {
    let u = $("#purl").value.trim(); if (!/^https?:\/\//.test(u)) u = "http://" + u; store("preview", u);
    const fr = $("#pframe"), msg = $("#pempty");
    fetch(u, {mode: "no-cors"}).then(() => { fr.hidden = false; msg.hidden = true; fr.src = u; })
      .catch(() => { fr.hidden = true; msg.hidden = false; msg.textContent = `Nothing is running at ${u} yet. Start your app (or ask the agent to), then press Open.`; });
  };
  $("#pgo").onclick = go; $("#purl").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#pext").onclick = () => openExternal($("#purl").value);
  if (store("preview")) go();                 // an address the user chose; never probe a default nobody asked for
  else { $("#pframe").hidden = true; const msg = $("#pempty"); msg.hidden = false; msg.textContent = "Type the address of your running app (for example localhost:3000) and press Open."; }
}

/* ================================== session panel + GitHub sessions ================================== */
const isMgd = p => !!S.projects.find(x => x.path === p)?.managed;
const spDur = s => { s = Math.max(0, Math.round(s)); return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`; };
const spOpen = k => { const o = store("sp_open") || {}; return o[k] !== false; };
const spTok = n => n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n);
// ---- the plan: the agent's checklist. In the Session panel, patched by row; in the chat only one quiet line (the status slot).
function planData() {
  const cur = S.cur; if (!cur) return null;
  const e = cur.planEv, sp = S.spPlan && S.spPlan.sid === cur.id ? S.spPlan : null, src = e || sp; if (!src) return null;
  if (cur._pd && cur._pd.src === src) return cur._pd.v;
  // events: items/entries {content, status}; the panel's data: steps {text, state: done|current|pending}
  const raw = src.items || src.entries || (src.steps || []).map(x => ({text: x.text, status: x.state === "done" ? "completed" : x.state === "current" ? "in_progress" : "pending"}));
  const items = raw.map(x => ({text: String(x.content ?? x.text ?? ""), status: x.status === "done" ? "completed" : x.status || "pending"}));
  // the backend's `current` is 1-based (0 = every step done); here it is an index into items
  let c = Number.isInteger(src.current) && src.current > 0 ? src.current - 1 : items.findIndex(x => x.status !== "completed");
  if (c < 0) c = Math.max(0, items.length - 1);
  const v = items.length ? {items, current: c, total: src.total || items.length} : null;
  cur._pd = {src, v}; return v;
}
const planLine = pd => `Step ${Math.min(pd.current + 1, pd.total)} of ${pd.total} · ${clip(String(pd.items[pd.current]?.text || ""), 60)}`;
const planIcon = (st, cur) => st === "completed" ? `<span class="pi ok">${ICON.check2}</span>` : cur || st === "in_progress" ? `<span class="pi run"><span class="dot run"></span></span>` : `<span class="pi">${ICON.ring}</span>`;
function planRows(pd) { return pd.items.map((it, i) => ({key: "s" + i, text: it.text, st: it.status === "completed" ? "completed" : i === pd.current || it.status === "in_progress" ? "current" : "pending"})); }
function planUpdate() {
  updateStep();
  const ol = $("#spPlanList"), pd = planData(); if (!ol || !pd) return;
  reconcile(ol, planRows(pd), r => r.key, r => { const li = document.createElement("li"); li.className = r.st; li.dataset.st = r.st; li.innerHTML = `${planIcon(r.st, r.st === "current")}<span></span>`; li.lastElementChild.textContent = r.text; return li; },
    (li, r) => { if (li.dataset.st !== r.st) { li.dataset.st = r.st; li.className = r.st; $(".pi", li).outerHTML = planIcon(r.st, r.st === "current"); } setTxt(li.lastElementChild, r.text); });
  setTxt($("[data-k=plan] .spc"), `${pd.items.filter(x => x.status === "completed").length}/${pd.items.length}`);
}
function planSoon() { if (S.plT) return; S.plT = setTimeout(() => { S.plT = null; if ($("#spPlanList") || S.panel !== "session") planUpdate(); else sessionSoon(); }, 120); }
function sessionSoon() { if (S.panel !== "session" || S.seT) return; S.seT = setTimeout(() => { S.seT = null; if (S.panel === "session" && S.spLast && S.cur) spDraw(S.cur.id, curRoot(), S.spLast); }, 300); }
function spSec(k, title, count, inner) {
  return `<details class="spsec" data-k="${k}" ${spOpen(k) ? "open" : ""}><summary><span class="chev">${ICON.chev}</span><b>${title}</b>${count ? `<span class="spc">${count}</span>` : ""}</summary><div class="spbody">${inner}</div></details>`;
}
function spHTML(d, pr) {
  const env = d.environment, rp = d.repo, br = d.branch, cx = d.context, cn = d.connectors, bg = d.background, tr = d.tree;
  const buttons = [["term", ICON.term, "Terminal"], ["browser", ICON.globe, "Browser"], ["preview", ICON.dir, "Preview"], ["diff", ICON.diff, "Changes"], [panelSet().includes("files") ? "files" : "outputs", ICON.files, panelSet().includes("files") ? "Files" : "Artifacts"]]
    .filter(([k]) => panelSet().includes(k));
  const own = !!S.cur?.view?.scratch;
  const envHTML = own ? `<div class="sprow">${ICON.folder}<span><b>This chat’s own folder</b><small>Private to this chat. Files it makes show under Artifacts.</small></span></div>` : `<div class="sprow">${env.managed ? ICON.cloud : ICON.cpu}<span><b>${esc(env.label)}</b>${env.managed ? "" : `<small title="${esc(env.folder)}">${esc(shortPath(env.folder))}</small>`}</span></div>
    ${rp ? `<div class="sprow">${ICON.pr}<span><b>${rp.url ? `<a href="${esc(rp.url)}" target="_blank" rel="noopener">${esc(rp.slug)}</a>` : esc(env.name)}</b><small>${rp.slug ? "GitHub repository" : "Git repository without a GitHub remote"}</small></span></div>` : `<div class="sprow">${ICON.dir}<span><b>${esc(env.name)}</b><small>Not a Git repository</small></span></div>`}
    ${br ? `<div class="sprow">${ICON.branch}<span><b>${esc(br.name)}</b><small>${br.upstream ? `${br.ahead} to push, ${br.behind} to pull` : "Not pushed yet"}</small></span></div>
      <div class="row spbr"><select id="spBr" aria-label="Switch branch">${br.branches.map(b => `<option ${b === br.name ? "selected" : ""}>${esc(b)}</option>`).join("")}</select></div>
      <div class="row spbr"><input id="spNew" placeholder="New branch name" aria-label="New branch name"><button class="btn" id="spCreate">Create</button></div>` : ""}`;
  const cls = cx.percent >= 90 ? "bad" : cx.percent >= 70 ? "warn" : "";
  const ctxHTML = `<div class="spctx"><div class="spbar ${cls}" role="progressbar" aria-valuenow="${cx.percent}" aria-valuemin="0" aria-valuemax="100" aria-label="Context used"><i style="width:${cx.percent}%"></i></div>
    <div class="spnum"><b>${cx.percent}%</b><span>${spTok(cx.used)} of ${spTok(cx.size)} tokens${/estimate/.test(cx.source) ? " (window estimated)" : ""}</span></div></div>`;
  const cRows = cn.servers.map(c => `<li><span class="cidot ${c.status === "connected" ? (c.used ? "ok" : "") : c.error || c.status === "error" ? "bad" : "run"}"></span><span class="cn">${esc(c.name)}</span><span class="cs">${c.used ? `used ${c.used}×` : c.status === "connected" ? `${c.tools} tools` : esc(c.status)}</span></li>`).join("");
  const outs = d.outputs;
  const outHTML = outs.length ? `<ul class="outs">${outs.map(o => `<li><button data-open="${esc(o.path)}" title="${esc(o.path)}"><span class="fi">${ICON.file}</span><span class="fp">${esc(o.path.split("/").pop())}<small>${esc(o.path)}</small></span><span class="spc">${esc(o.how)}</span></button></li>`).join("")}</ul>` : `<div class="pnote">Documents, spreadsheets, decks and other files Meshpool makes show up here.</div>`;
  const num = (v, c, sg) => `<span class="${v ? c : "zero"}">${sg}${v}</span>`;
  let chg = `<div class="sprow">${ICON.diff}<span><b>${tr.files ? `${tr.files} file${tr.files === 1 ? "" : "s"} changed` : "Working tree is clean"}</b>${tr.files ? `<small class="nw">${num(tr.added, "plus", "+")} ${num(tr.removed, "minus", "−")} not committed</small>` : ""}</span></div>`;
  if (!pr) chg += `<div class="pnote spmini">Checking GitHub...</div>`;
  else if (!pr.available) chg += `<div class="pnote spmini">${esc(pr.error || "No GitHub remote.")}${/Connect GitHub/.test(pr.error || "") ? ` <button class="linkbtn" id="spConn">Connect GitHub</button>` : ""}</div>`;
  else {
    const p = pr.pr;
    chg += p ? `<div class="prcard"><a href="${esc(p.url)}" target="_blank" rel="noopener"><b>#${p.number} ${esc(p.title)}</b></a><span class="badge ${p.draft ? "modified" : "added"}">${p.draft ? "Draft" : "Open"}</span>
        <span class="nw">${num(p.additions, "plus", "+")} ${num(p.deletions, "minus", "−")}</span>
        ${p.checks !== "none" ? `<span class="chip ci ${p.checks}"><span class="cidot"></span>${esc(CI_TEXT[p.checks] || p.checks)}</span>` : `<span class="stat">No checks</span>`}
        <small>${esc(p.head)} into ${esc(p.base)}</small>
        ${p.failing.length ? `<small class="bad">Failing: ${esc(p.failing.join(", "))}</small>` : ""}
        <div class="row">${p.draft ? `<button class="btn primary" id="spReady" ${p.can_ready ? "" : "disabled title='Connect GitHub first'"}>Mark ready</button>` : ""}<button class="btn" id="spPrView">Open the full view</button></div></div>`
      : `<div class="pnote spmini">No pull request for ${esc(pr.branch)} yet.</div><div class="row"><button class="btn" id="spDraft">Open a draft PR</button><button class="btn" id="spGit">Commit and push</button></div>`;
    if (pr.others.length) chg += `<div class="spsub">Other open pull requests</div><ul class="spl">${pr.others.map(o => `<li><a href="${esc(o.url)}" target="_blank" rel="noopener"><span class="cn">#${o.number} ${esc(o.title)}</span></a>${o.draft ? `<span class="spc">draft</span>` : ""}</li>`).join("")}</ul>`;
  }
  const bgHTML = (bg.running.length ? `<ul class="spl">${bg.running.map(t => `<li><span class="cidot run"></span><span class="cn" title="${esc(t.title)}">${esc(t.title)}</span><span class="cs">${esc({subagent: "Sub-agent", terminal: "Command", scheduled: "Scheduled"}[t.kind])} · ${spDur(t.elapsed)}</span><button class="linkbtn" data-stop="${esc(t.kind)}:${esc(t.id)}">Stop</button></li>`).join("")}</ul>` : `<div class="pnote spmini">Nothing running in the background.</div>`) + (bg.finished ? `<div class="stat">${bg.finished} finished</div>` : "");
  const cur = S.cur, pd = planData(), rc = [...(cur?.log || [])].reverse().find(e => e.type === "receipt"), mn = cur?.mcpNote || "";
  const mcpBad = /Failed|did not start/.test(mn);
  const planHTML = pd ? spSec("plan", "Plan", `${pd.items.filter(x => x.status === "completed").length}/${pd.items.length}`, `<ol class="stepl plan" id="spPlanList">${planRows(pd).map(r => `<li class="${r.st}" data-st="${r.st}" data-k="${r.key}">${planIcon(r.st, r.st === "current")}<span>${esc(r.text)}</span></li>`).join("")}</ol>`) : "";
  const au = cur?.auto, agentHTML = cur?.using ? spSec("agent", "Agent", "", `<div class="sprow">${ICON.cpu}<span><b>${esc(String(cur.using).replace(/^Using /, ""))}</b><small>${au ? `Picked by Auto${au.category ? " for " + esc(String(au.category)) : ""}.${au.reason ? " " + esc(au.reason) : ""}` : "Chosen by you."}</small></span></div>`) : "";
  const routeHTML = agentHTML + (cur?.route ? spSec("route", "Working as", "", `<div class="sprow">${ICON.cpu}<span><b>${esc(cur.route.label)}</b>${cur.route.why ? `<small>${esc(cur.route.why)}</small>` : ""}</span><button class="linkbtn" id="spRoute">Change</button></div>`) : "");
  const reviewHTML = rc ? spSec("review", "Review", "", `<div class="rcbody md">${md(rc.text || "")}</div>`) : "";
  const mcpHTML = mn ? `<div class="pnote spmini ${mcpBad ? "bad" : ""}">${esc(mn.replace(/^MCP: ?/, ""))}</div>` : "";
  return `<div class="spw"><div class="spbtns">${buttons.map(([k, ic, lb]) => `<button class="btn" data-go="${k}">${ic}${lb}</button>`).join("")}</div>
    ${planHTML}${spSec("env", "Environment", "", envHTML)}${routeHTML}${spSec("ctx", "Context", `${cx.percent}%`, ctxHTML)}
    ${spSec("conn", `Connectors${mcpBad ? ` <span class="cidot bad" title="A connector did not start"></span>` : ""}`, cn.used.length ? `${cn.used.length} used` : "", (cRows ? `<ul class="spl">${cRows}</ul>` : `<div class="pnote spmini">No connectors are enabled for this session.</div>`) + mcpHTML)}${reviewHTML}
    ${spSec("out", "Artifacts", outs.length || "", outHTML)}${own ? "" : spSec("chg", "Changes", pr?.pr ? `#${pr.pr.number}` : tr.files || "", chg)}
    ${spSec("bg", "Background tasks", bg.running.length || "", bgHTML)}
    <div class="viewer" id="viewer" hidden></div></div>`;
}
async function panelSession(body) {
  if (!S.cur) { body.innerHTML = `<div class="pnote">Start a session and its environment, context, connectors, files and pull request show here.</div>`; return; }
  const sid = S.cur.id, root = curRoot();
  S.spPr = S.spPr?.sid === sid ? S.spPr : {sid, t: 0, data: null};
  body.innerHTML = `<div class="pnote">Loading...</div>`;
  const tick = async first => {
    if (S.panel !== "session" || S.cur?.id !== sid) return;
    try {
      if (!S.spPr.data || Date.now() - S.spPr.t > 30000) { api(`/api/session/${sid}/pr`).then(r => { S.spPr = {sid, t: Date.now(), data: r}; if (S.panel === "session") spDraw(sid, root, last); }).catch(() => {}); S.spPr.t = Date.now(); }
      const d = await api(`/api/session/${sid}/panel`); last = d; spDraw(sid, root, d);
    } catch (e) { if (first) body.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; }
    clearTimeout(S.spT); S.spT = setTimeout(() => tick(false), 4000);
  };
  let last = null; clearTimeout(S.spT); tick(true);
}
function spDraw(sid, root, d) {
  const body = $("#pbody"); if (!body || !d || S.panel !== "session" || S.cur?.id !== sid) return;
  if (body.contains(document.activeElement) && /INPUT|SELECT/.test(document.activeElement.tagName)) return;   // never rebuild under the user's typing
  S.spLast = d; if (d.plan) S.spPlan = Object.assign({sid}, d.plan);
  const keep = $("#viewer", body); const vh = keep && !keep.hidden ? keep.innerHTML : "";
  const sc = body.scrollTop;
  body.innerHTML = spHTML(d, S.spPr?.data);
  body.scrollTop = sc;
  if (vh) { const v = $("#viewer", body); v.hidden = false; v.innerHTML = vh; }
  $$("details.spsec", body).forEach(el => el.ontoggle = () => { const o = store("sp_open") || {}; o[el.dataset.k] = el.open; store("sp_open", o); });
  $$("[data-go]", body).forEach(b => b.onclick = () => togglePanel(b.dataset.go, true));
  $("#spRoute", body)?.addEventListener("click", e => routeMenu(e.currentTarget.parentElement));
  $$("[data-open]", body).forEach(b => b.onclick = () => openPreview(root, b.dataset.open));
  $("#spBr", body)?.addEventListener("change", e => gitAct({action: "checkout", branch: e.target.value}));
  $("#spCreate", body)?.addEventListener("click", () => gitAct({action: "checkout", branch: $("#spNew").value, create: true}));
  $("#spConn", body)?.addEventListener("click", () => openSettings("connectors"));
  $("#spGit", body)?.addEventListener("click", () => togglePanel("git", true));
  $("#spPrView", body)?.addEventListener("click", () => openPRView(root, S.spPr.data.pr.number));
  $("#spReady", body)?.addEventListener("click", async () => { try { await post("/api/git/pr", {root, action: "ready", pr: S.spPr.data.pr.number}); toast("Marked ready for review"); S.spPr.data = null; S.spPr.t = 0; } catch (e) { toast(e.message, 5000); } });
  $("#spDraft", body)?.addEventListener("click", async () => { const t = await ask({title: "Open a draft pull request", text: "Pushes this branch and opens a draft on GitHub.", value: S.spPr.data.branch, ok: "Open draft"}); if (t) { try { const r = await post("/api/git", {root, action: "pr", title: t, draft: true}); toast("Draft pull request opened"); S.spPr.data = null; S.spPr.t = 0; if (r.pr) window.open?.(r.pr, "_blank", "noopener"); } catch (e) { toast(e.message, 5000); } } });
  $$("[data-stop]", body).forEach(b => b.onclick = async () => { const [kind, ...id] = b.dataset.stop.split(":"); try { await post(`/api/session/${sid}/bg/stop`, {kind, id: id.join(":")}); toast("Stopped"); } catch (e) { toast(e.message); } });
}
function openGithubPicker(opts = {}) {
  const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
  const gtitle = opts.title || "Work on a GitHub repository";
  sc.innerHTML = `<div class="dlg fb ghp" role="dialog" aria-modal="true" aria-label="${esc(gtitle)}"><h3>${esc(gtitle)}</h3>
    <p>Pick a repository. Meshpool keeps its own copy and works there, so it doesn’t need to be on this computer first.</p>
    <input id="ghQ" placeholder="Search your repositories, or type owner/name" spellcheck="false" autocomplete="off" aria-label="Search repositories">
    <ul class="fblist" id="ghList" role="listbox"><li class="pnote">Loading...</li></ul>
    <div id="ghBr" class="ghbr" hidden><label>Branch<select id="ghSel"></select></label><label class="chk"><input type="checkbox" id="ghNewOn"> New branch from it</label><input id="ghNewName" placeholder="New branch name" hidden></div>
    <div class="row"><span class="fbsel" id="ghMsg"></span><button class="btn" data-a="0">Cancel</button><button class="btn primary" id="ghGo" disabled>${esc(opts.cta || "Start chat")}</button></div></div>`;
  document.body.appendChild(sc);
  let repo = null, qT = null;
  const done = () => { sc.remove(); document.removeEventListener("keydown", key, true); };
  const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(); } };
  document.addEventListener("keydown", key, true);
  sc.addEventListener("mousedown", e => { if (e.target === sc) done(); });
  $("[data-a='0']", sc).onclick = done;
  const list = $("#ghList", sc), msg = $("#ghMsg", sc);
  const load = async () => {
    try {
      const r = await api("/api/github/repos?q=" + encodeURIComponent($("#ghQ", sc).value.trim()));
      list.innerHTML = r.repos.length ? r.repos.map((x, i) => `<li><button data-i="${i}" class="${repo?.full_name === x.full_name ? "act" : ""}">${ICON.pr}<span class="ghn"><b>${esc(x.full_name)}</b>${x.private ? `<span class="spc">private</span>` : ""}${x.cloned ? `<span class="spc">ready</span>` : ""}<small>${esc(x.description)}</small></span></button></li>`).join("") : `<li class="pnote">No repositories match.</li>`;
      $$("[data-i]", list).forEach(b => b.onclick = () => pick(r.repos[+b.dataset.i]));
    } catch (e) {
      list.innerHTML = `<li class="pnote">${esc(e.message)} ${/Connect GitHub/.test(e.message) ? `<button class="linkbtn" id="ghConn">Connect GitHub</button>` : ""}</li>`;
      $("#ghConn", sc)?.addEventListener("click", () => { done(); openSettings("connectors"); });
    }
  };
  const pick = async x => {
    repo = x; $$("#ghList button", sc).forEach(b => b.classList.toggle("act", b.textContent.includes(x.full_name))); $("#ghGo", sc).disabled = true; msg.textContent = "Loading branches...";
    try {
      const b = await api("/api/github/branches?repo=" + encodeURIComponent(x.full_name));
      $("#ghSel", sc).innerHTML = b.branches.map(n => `<option ${n === b.default ? "selected" : ""}>${esc(n)}</option>`).join(""); $("#ghBr", sc).hidden = false; $("#ghGo", sc).disabled = false; msg.textContent = x.full_name;
    } catch (e) { msg.textContent = e.message; }
  };
  $("#ghQ", sc).oninput = () => { clearTimeout(qT); qT = setTimeout(load, 250); };
  $("#ghNewOn", sc).onchange = e => { $("#ghNewName", sc).hidden = !e.target.checked; if (e.target.checked) $("#ghNewName", sc).focus(); };
  $("#ghGo", sc).onclick = async () => {
    const mk = $("#ghNewOn", sc).checked, nm = $("#ghNewName", sc).value.trim(); if (mk && !nm) { msg.textContent = "Name the new branch."; return; }
    $("#ghGo", sc).disabled = true; msg.textContent = "Getting the repository ready. A large one can take a minute...";
    try {
      const r = await post("/api/github/workspace", {repo: repo.full_name, branch: mk ? nm : $("#ghSel", sc).value, create: mk, base: $("#ghSel", sc).value});
      done(); await loadProjects();
      if (opts.onReady) return opts.onReady(r);
      renderSide(); newThread(r.path); toast(`${r.name} on ${r.branch} is ready`);
    } catch (e) { msg.textContent = e.message; $("#ghGo", sc).disabled = false; }
  };
  load(); $("#ghQ", sc).focus();
}

/* ================================== settings ================================== */
/* The settings window (openSettings, the seven pages and their tabs) is in settings2.js. */
async function renderSection(sec) {
  const b = $("#mbody"); b.classList.add("loading");
  new MutationObserver((_, o) => { b.classList.remove("loading"); b.scrollTop = 0; o.disconnect(); }).observe(b, {childList: true});
  const close = `<button class="iconbtn mclose" aria-label="Close settings">${ICON.x}</button>`;
  const done = () => $(".mclose", b)?.addEventListener("click", () => $(".scrim").remove());
  await loadSettings();
  const F = S.settings.fields; const get = k => F.find(x => x.key === k) || {};
  if (sec === "models") {
    if (!S.models) await loadModels();
    const M = S.models;
    b.innerHTML = `${close}<h3>Models</h3><p class="lead">Every model your key can use on ${esc(M.base_url)}. Star the ones you use most; they show first in the picker.</p>
      <div style="display:flex;gap:8px;margin-bottom:12px"><input id="smq" class="btn" style="flex:1;text-align:left;font-weight:400" placeholder="Search models"><button class="btn" id="smr">${ICON.refresh} Refresh</button></div>
      ${M.error ? `<div class="res bad">${esc(M.error)}</div>` : ""}<ul class="rows2" id="sml"></ul>`;
    const draw = () => {
      const q = $("#smq").value.trim().toLowerCase();
      $("#sml").innerHTML = M.models.filter(m => !q || m.id.toLowerCase().includes(q)).map(m => `<li><span class="tg"><b title="${esc(m.id)}">${esc(modelLabel(m.id, M.models))}</b><small>${esc(m.provider)}${m.context ? ` · ${fmt(m.context)} context` : ""}${(m.caps || []).length ? " · " + m.caps.join(", ") : ""}</small></span>
        ${m.id === M.default ? `<span class="badge modified">Default</span>` : `<button class="btn" data-def="${esc(m.id)}">Make default</button>`}
        <button class="iconbtn" data-fav="${esc(m.id)}" title="Favorite" style="color:${M.favorites.includes(m.id) ? "var(--warn)" : "var(--ink-3)"}">${ICON.star}</button></li>`).join("") || `<li class="pnote">No models.</li>`;
      $$("[data-def]", b).forEach(x => x.onclick = async () => { S.models = await post("/api/models/default", {id: x.dataset.def}); Object.assign(M, S.models); draw(); renderBar(); });
      $$("[data-fav]", b).forEach(x => x.onclick = async () => { S.models = await post("/api/models/favorite", {id: x.dataset.fav, on: !M.favorites.includes(x.dataset.fav)}); Object.assign(M, S.models); draw(); });
    };
    $("#smq").oninput = draw; $("#smr").onclick = async () => { await loadModels(true); Object.assign(M, S.models); renderSection("models"); }; draw();
  } else if (sec === "personal") {
    const a = await api("/api/assistant");
    b.innerHTML = `${close}<h3>Instructions and memory</h3><p class="lead">Every agent reads these at the start of each new chat, so you do not repeat yourself.</p>
      <label class="field" style="max-width:none"><span>Instructions for every chat</span><textarea id="paIns" rows="6" placeholder="For example: Be direct and concise. No em dashes. I work in strategy and finance in the GCC.">${esc(a.instructions)}</textarea><small>How you want answers: tone, format, what to avoid, context about your work.</small></label>
      <label class="switchrow"><input type="checkbox" id="paInsOn" ${a.settings.instructions_on ? "checked" : ""}> Use these instructions</label>
      <div class="field" style="max-width:none;margin-top:14px"><span>Memory</span><p class="lead" style="margin:4px 0 8px">${a.memory.split("\n").filter(x => x.trim()).length} things remembered.</p><button class="btn" id="paMemPage">Open What Meshpool knows</button></div>
      <label class="switchrow"><input type="checkbox" id="paMemOn" ${a.settings.memory_on ? "checked" : ""}> Let agents use and add to memory</label>
      <label class="switchrow"><input type="checkbox" id="paEdits" ${a.settings.learn_from_edits ? "checked" : ""}> Learn from my edits to files Meshpool makes</label>
      <div style="display:flex;gap:8px;margin-top:12px"><button class="btn primary" id="paSave">Save</button></div><div class="res" id="paRes"></div>
      <p class="lead" style="margin-top:18px">Coming from ChatGPT, Codex or another assistant? <button class="linkbtn" id="paImport">Import your chats and context</button></p>`;
    $("#paImport").onclick = () => renderSection("import");
    $("#paMemPage").onclick = () => { closeSettings?.(); goPage("memory"); };
    $("#paSave").onclick = async () => {
      try {
        await post("/api/assistant", {action: "instructions", text: $("#paIns").value});
        await post("/api/assistant", {action: "settings", memory_on: $("#paMemOn").checked, instructions_on: $("#paInsOn").checked, learn_from_edits: $("#paEdits").checked});
        RES("paRes").className = "res ok"; RES("paRes").textContent = "Saved. New chats use it.";
      } catch (e) { RES("paRes").className = "res bad"; RES("paRes").textContent = e.message; }
    };
  } else if (sec === "import") {
    b.innerHTML = `${close}<div id="impAgents"></div><div id="impBox"></div>`;
    importPanel($("#impAgents", b));
    await contextPanel($("#impBox", b));
  } else if (sec === "scheduled") {
    const draw = a => {
      const proj = S.projects.map(p => `<option value="${esc(p.path)}">${esc(p.name)}</option>`).join("");
      b.innerHTML = `${close}<h3>Scheduled tasks</h3><p class="lead">Work that runs by itself: a morning news brief, a weekly pipeline report, a reminder. Each run opens a new chat and notifies you when it is done. Tasks run while Meshpool is open on this computer. You can also ask any agent to schedule something.</p>
        <ul class="rows2">${a.tasks.map(t => `<li><span class="tg"><b>${esc(t.name)}</b><small>${esc(t.describe)} · ${t.kind === "code" ? "Code" : "Work"}${t.worktree ? " · new worktree each run" : ""}</small><small class="clip2">${esc(t.prompt)}</small></span>
          <label class="switch" title="On or off"><input type="checkbox" data-ton="${esc(t.id)}" ${t.enabled ? "checked" : ""} aria-label="Run ${esc(t.name)}"><span></span></label>
          <button class="btn" data-trun="${esc(t.id)}">Run now</button>${t.last_thread ? `<button class="btn" data-topen="${esc(t.last_thread)}">Last run</button>` : ""}<button class="iconbtn" data-tdel="${esc(t.id)}" aria-label="Delete">${ICON.x}</button></li>`).join("") || `<li class="pnote">No scheduled tasks yet.</li>`}</ul>
        <h3 style="font-size:16px;margin:18px 0 6px">New scheduled task</h3>
        <div class="sform">
          <input id="tsName" placeholder="Name, for example Morning AI news" spellcheck="false">
          <textarea id="tsPrompt" rows="3" placeholder="What should it do? For example: Find the 5 most important AI infrastructure news stories from the last 24 hours and summarize each in two sentences with links."></textarea>
          <div class="srow"><select id="tsWhen" aria-label="How often"><option value="daily">Every day</option><option value="weekdays">Every weekday</option><option value="weekly">Every week</option><option value="hourly">Every hour</option><option value="once">Once</option><option value="cron">Custom (cron)</option></select>
          <input id="tsCron" aria-label="Cron expression" placeholder="30 8 * * 1-5" spellcheck="false" style="max-width:150px">
          <select id="tsDay" aria-label="Day">${["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map(d => `<option value="${d}">${d[0].toUpperCase() + d.slice(1)}</option>`).join("")}</select>
          <input id="tsTime" aria-label="Time" type="time" value="08:30"><input id="tsAt" aria-label="Date and time" type="datetime-local">
          <select id="tsKind" aria-label="Mode"><option value="cowork">Work</option><option value="code">Code</option></select>
          <select id="tsFolder" aria-label="Folder"><option value="">Default folder</option>${proj}</select></div>
          <label class="chk" id="tsWtRow" hidden><input type="checkbox" id="tsWt"> Each run in a new worktree (its own checkout and branch, so runs never change your working copy)</label>
          <button class="btn primary" id="tsAdd">Add task</button></div><div class="res" id="tsRes"></div>`;
      done();
      const sync = () => { const w = $("#tsWhen").value; $("#tsDay").hidden = w !== "weekly"; $("#tsTime").hidden = !["daily", "weekdays", "weekly"].includes(w); $("#tsAt").hidden = w !== "once"; $("#tsCron").hidden = w !== "cron"; $("#tsWtRow").hidden = !$("#tsFolder").value; if ($("#tsWtRow").hidden) $("#tsWt").checked = false; };
      $("#tsWhen").onchange = sync; $("#tsFolder").onchange = sync; sync();
      const act = async body => { try { draw(await post("/api/assistant", body)); } catch (e) { RES("tsRes").className = "res bad"; RES("tsRes").textContent = e.message; } };
      $("#tsAdd").onclick = () => !$("#tsPrompt").value.trim() ? (RES("tsRes").className = "res bad", RES("tsRes").textContent = "Write what the task should do.", $("#tsPrompt").focus()) : act({action: "task_add", name: $("#tsName").value.trim(), prompt: $("#tsPrompt").value.trim(), when: $("#tsWhen").value, day: $("#tsDay").value, time: $("#tsTime").value, at: $("#tsAt").value, kind: $("#tsKind").value, folder: $("#tsFolder").value, worktree: $("#tsWt").checked, cron: $("#tsCron").value.trim()});
      $$("[data-ton]", b).forEach(x => x.onchange = () => act({action: "task_update", id: x.dataset.ton, enabled: x.checked}));
      $$("[data-tdel]", b).forEach(x => x.onclick = async () => { if (await ask({title: "Delete this scheduled task?", text: "It won't run again. Chats from earlier runs stay.", ok: "Delete task", danger: true})) act({action: "task_delete", id: x.dataset.tdel}); });
      $$("[data-trun]", b).forEach(x => x.onclick = async () => { try { const r = await post("/api/assistant", {action: "task_run", id: x.dataset.trun}); $(".scrim")?.remove(); await loadThreads(); openThread(r.thread); } catch (e) { toast(e.message); } });
      $$("[data-topen]", b).forEach(x => x.onclick = () => { $(".scrim")?.remove(); openThread(x.dataset.topen); });
    };
    draw(await api("/api/assistant")); return;
  } else if (sec === "about") {
    const a = await api("/api/about");
    b.innerHTML = `${close}<img class="logo" src="${logoSrc()}" alt="Meshpool" style="height:30px;margin-bottom:12px"><h3>Meshpool ${esc(a.version)}${/^0\./.test(a.version) ? " beta" : ""}</h3>
      <p class="lead">Made by ${esc(a.author)}. <a href="https://meshpool.ai" target="_blank" rel="noopener">meshpool.ai</a> · <a href="https://github.com/hamzaziada4422/meshpool-harness/releases" target="_blank" rel="noopener">All releases</a> · <button class="linkbtn" id="aboutReport">Report a problem</button></p>
      <div class="updbox" id="updBox"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn" id="updCheck">Check for updates</button><span class="res" id="updRes" style="margin:0"></span></div><div id="updMore"></div></div>
      <details style="margin-top:16px"><summary class="linkbtn" style="display:inline">Third-party software</summary><pre class="lic">${esc(a.third_party)}</pre></details>`;
    $("#aboutReport").onclick = () => reportProblem();
    updatePanel(S.upd);
    $("#updCheck").onclick = async () => { RES("updRes").className = "res"; RES("updRes").textContent = "Checking..."; updatePanel(await checkUpdate(true)); };
  } else if (sec === "web") {
    const keys = ["BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "SEARXNG_URL"];
    b.innerHTML = `${close}<h3>Web search</h3><p class="lead">Every agent can search the web, read pages and PDFs, and research a question across many sources at once, without opening a browser. It works with no setup through free search engines. For heavy research, add a key below: searches get faster and are never limited.</p>
      ${keys.map(k => { const f = get(k); return `<label class="field"><span>${esc(f.label)}</span>${f.key === "SEARXNG_URL" ? `<input data-wk="${k}" spellcheck="false" value="${esc(f.value || "")}" placeholder="http://localhost:8888">` : `<input data-wk="${k}" type="password" autocomplete="off" spellcheck="false" placeholder="${f.set ? "Saved, " + esc(f.hint) : "Not set"}">`}<small>${esc(f.help)}</small></label>`; }).join("")}
      <div style="display:flex;gap:8px"><button class="btn primary" id="wSave">Save</button><button class="btn" id="wTest">Test search</button></div><div class="res" id="wRes"></div>`;
    $("#wSave").onclick = async () => { const values = {}; $$("[data-wk]", b).forEach(x => { if (x.value.trim() || x.dataset.wk === "SEARXNG_URL") values[x.dataset.wk] = x.value.trim(); }); try { await post("/api/settings", {values}); RES("wRes").className = "res ok"; RES("wRes").textContent = "Saved."; } catch (e) { RES("wRes").className = "res bad"; RES("wRes").textContent = e.message; } };
    $("#wTest").onclick = async () => { RES("wRes").className = "res"; RES("wRes").textContent = "Searching..."; try { const r = await post("/api/web-test"); RES("wRes").className = "res " + (r.ok ? "ok" : "bad"); RES("wRes").textContent = r.ok ? `Search works: ${r.count} results in ${r.seconds} s, first: ${r.first}` : r.error; } catch (e) { RES("wRes").className = "res bad"; RES("wRes").textContent = e.message; } };
  } else if (sec === "connectors") {
    const cats = ["Connected", "Engineering", "Work and documents", "Sales and marketing", "Finance and data", "Yours"];
    const redirect = `${location.origin}/oauth/callback`;
    const say = (t, cls = "") => { RES("cnRes").className = "res " + cls; RES("cnRes").textContent = t; };
    const clientForm = c => `<span class="ownclient" data-cf="${esc(c.id)}"><small>${esc(c.client_help || "This app only signs in apps it knows. Create an OAuth app in its developer settings with the redirect URL below, then paste its client ID (and secret, if it gives you one).")}</small>
      <small>Redirect URL: <code>${esc(redirect)}</code></small>
      <span class="keyrow" style="margin-top:6px"><input data-cid="${esc(c.id)}" placeholder="Client ID" autocomplete="off" spellcheck="false"><input type="password" data-csec="${esc(c.id)}" placeholder="Client secret (if any)" autocomplete="off" spellcheck="false"><button class="btn primary" data-client="${esc(c.id)}">Connect</button></span></span>`;
    const row = c => {
      const cat = c.connected ? "Connected" : c.custom ? "Yours" : c.category || "Yours";
      const own = c.needs_client && !c.own_client && !c.connected;
      const btn = c.connected ? `<label class="chk" title="Chats can read from it but not change anything: its create, update, delete and send tools are left out"><input type="checkbox" data-ro="${esc(c.id)}" ${c.read_only ? "checked" : ""}> Read only</label><label class="switch" title="Use in new chats"><input type="checkbox" data-on="${esc(c.id)}" ${c.enabled ? "checked" : ""} aria-label="Use ${esc(c.name)}"><span></span></label><button class="btn" data-test="${esc(c.id)}">Test</button><button class="btn" data-off="${esc(c.id)}">${c.auth === "none" ? "Turn off" : "Disconnect"}</button>`
        : c.auth === "none" ? `<button class="btn primary" data-turnon="${esc(c.id)}">Turn on</button>`
        : c.auth === "oauth" && !own ? `<button class="btn primary" data-go="${esc(c.id)}">${c.expired ? "Sign in again" : "Connect"}</button>` : "";
      return `<li data-id="${esc(c.id)}" data-cat="${esc(cat)}" data-text="${esc((c.name + " " + c.description + " " + (c.category || "")).toLowerCase())}"><span class="tg"><b>${esc(c.name)}</b>${c.connected ? ` <span class="badge added">${c.auth === "none" ? "On" : "Connected"}</span>` : c.expired ? ` <span class="badge deleted">Sign in again</span>` : c.auth === "none" ? ` <span class="badge">No sign-in</span>` : ""}<small>${esc(c.description)}</small>
        ${c.id === "github" && !c.connected ? `<span class="row" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">${c.can_sign_in ? `<button class="btn primary" data-ghdev="1">Sign in with GitHub</button>` : ""}<button class="btn ${c.can_sign_in ? "" : "primary"}" data-ghfound="1">Use the GitHub login on this computer</button><a class="btn" href="https://github.com/settings/tokens/new?scopes=repo,workflow,read:org&amp;description=Meshpool" target="_blank" rel="noopener">Create a token</a></span>` : ""}
        ${c.auth === "token" && !c.connected ? `<span class="keyrow" style="margin-top:6px"><input type="password" data-tok="${esc(c.id)}" placeholder="Paste a token" autocomplete="off" spellcheck="false"><button class="btn" data-save="${esc(c.id)}">Save</button></span><small>${esc(c.token_help)}</small>` : ""}
        ${own || S.cnNeedClient === c.id ? clientForm(c) : ""}</span>${btn}</li>`;
    };
    const filter = () => {
      const q = ($("#cnq")?.value || "").trim().toLowerCase(), cat = S.cnCat || "";
      $$("#cnl > li", b).forEach(li => { li.hidden = (q && !li.dataset.text.includes(q)) || (cat && li.dataset.cat !== cat); });
      $$("[data-cncat]", b).forEach(x => x.classList.toggle("primary", x.dataset.cncat === cat));
    };
    const signIn = (r, id) => {                       // poll until the browser sign-in finished
      if (r.needs_client) { S.cnNeedClient = r.needs_client; draw(r.connectors); say(r.message); return; }
      RES("cnRes").className = "res"; RES("cnRes").innerHTML = `Finish signing in in the ${r.inapp ? "Browser panel" : "browser window"}. <a href="${esc(r.url)}" target="_blank" rel="noopener">Open the sign-in page again</a>`;
      const t0 = Date.now();
      const tick = async () => {
        if (!$("#cnl")) return;
        const l = (await api("/api/connectors")).connectors;
        if (l.find(c => c.id === id)?.connected) { S.cnNeedClient = ""; draw(l); say(`${l.find(c => c.id === id).name} is connected. New chats can use it.`, "ok"); return; }
        if (Date.now() - t0 < 300000) setTimeout(tick, 2000);
      };
      setTimeout(tick, 2000);
    };
    const draw = rows => {
      const order = c => (c.connected ? 0 : 1);
      b.innerHTML = `${close}<h3>Connectors</h3><p class="lead">Connect the apps you work in. Every agent can then use them as tools. Sign-in happens between this computer and the app itself: Meshpool keeps the access on this computer only and runs no server in between.</p>
        <div class="cnbar"><input id="cnq" placeholder="Search ${rows.length} apps" spellcheck="false" value="${esc(S.cnQ || "")}"><span class="cnchips"><button class="btn${S.cnCat ? "" : " primary"}" data-cncat="">All</button>${cats.filter(k => rows.some(c => (c.connected ? "Connected" : c.custom ? "Yours" : c.category) === k)).map(k => `<button class="btn" data-cncat="${esc(k)}">${esc(k)}</button>`).join("")}</span></div>
        <div class="res" id="cnRes"></div>
        <ul class="rows2" id="cnl">${[...rows].sort((x, z) => order(x) - order(z)).map(row).join("")}</ul>
        <h3 style="font-size:16px;margin:18px 0 4px">Add another app</h3><p class="lead" style="font-size:13px">Any app with a remote MCP server works. Paste its address; Meshpool works out whether it needs nothing, a token or a browser sign-in. For a key that goes in another header, paste it as <code>Header-Name: value</code>.</p>
        <div class="cform"><input id="cxName" placeholder="Name" spellcheck="false"><input id="cxUrl" placeholder="https://mcp.example.com/mcp" spellcheck="false"><input id="cxTok" type="password" placeholder="Token or API key (optional)" autocomplete="off"><button class="btn primary" id="cxAdd">Add</button></div>
        <details style="margin-top:8px"><summary class="linkbtn" style="display:inline;padding-left:0">More options: your own sign-in client</summary><span class="keyrow" style="margin-top:6px"><input id="cxCid" placeholder="Client ID" autocomplete="off" spellcheck="false"><input id="cxSec" type="password" placeholder="Client secret" autocomplete="off" spellcheck="false"></span><small class="muted">For apps that only sign in apps they know. Redirect URL: <code>${esc(redirect)}</code></small></details>`;
      done();
      $("#cnq").oninput = () => { S.cnQ = $("#cnq").value; filter(); };
      $$("[data-cncat]", b).forEach(x => x.onclick = () => { S.cnCat = x.dataset.cncat; filter(); });
      filter();
      $("#cxAdd").onclick = async () => {
        const url = $("#cxUrl").value.trim(); if (!url) { $("#cxUrl").focus(); return; }
        $("#cxAdd").disabled = true; say("Checking the server...");
        const r = await act({action: "custom", name: $("#cxName").value.trim(), url, token: $("#cxTok").value.trim(), client_id: $("#cxCid").value.trim(), client_secret: $("#cxSec").value.trim()}); $("#cxAdd").disabled = false;
        if (!r) return;
        S.connCache = r.connectors; S.cnCat = ""; draw(r.connectors);
        if (r.url || r.needs_client) signIn(r, r.id); else say(r.message || "Added. New chats can use it.", "ok");
      };
      const act = async body => { try { const r = await post("/api/connectors", body); return r; } catch (e) { say(e.message, "bad"); return null; } };
      $$("[data-go]", b).forEach(x => x.onclick = async () => {
        x.disabled = true; x.textContent = "Opening sign-in...";
        const r = await act({action: "connect", id: x.dataset.go});
        if (!r) { x.disabled = false; x.textContent = "Connect"; return; }
        signIn(r, x.dataset.go);
      });
      $$("[data-client]", b).forEach(x => x.onclick = async () => {
        const id = x.dataset.client, cid = $(`[data-cid="${id}"]`, b).value.trim();
        if (!cid) { $(`[data-cid="${id}"]`, b).focus(); return; }
        const r = await act({action: "client", id, client_id: cid, client_secret: $(`[data-csec="${id}"]`, b).value.trim()});
        if (r) signIn(r, id);
      });
      $$("[data-turnon]", b).forEach(x => x.onclick = async () => {
        x.disabled = true;
        const r = await act({action: "enable", id: x.dataset.turnon, on: true}); if (!r) { x.disabled = false; return; }
        draw(r.connectors); say("Checking it...");
        const t = await act({action: "check", id: x.dataset.turnon}); if (t) say(t.ok ? `On: ${t.tools.length} tools. New chats can use it.` : t.error, t.ok ? "ok" : "bad");
      });
      $$("[data-test]", b).forEach(x => x.onclick = async () => {
        x.disabled = true; say("Testing...");
        const t = await act({action: "check", id: x.dataset.test}); x.disabled = false;
        if (t) say(t.ok ? `Works: ${t.tools.length} tools (${t.tools.slice(0, 6).join(", ")}${t.tools.length > 6 ? ", ..." : ""}).` : t.error, t.ok ? "ok" : "bad");
      });
      $("[data-ghfound]", b)?.addEventListener("click", async e => {
        e.target.disabled = true; RES("cnRes").className = "res"; RES("cnRes").textContent = "Looking for a GitHub login on this computer...";
        const r = await act({action: "github_found", id: "github"}); e.target.disabled = false;
        if (r) { S.connCache = r.connectors; draw(r.connectors); RES("cnRes").className = "res ok"; RES("cnRes").textContent = r.message; }
      });
      $("[data-ghdev]", b)?.addEventListener("click", async () => {
        const r = await act({action: "github_device", id: "github"}); if (!r) return;
        RES("cnRes").className = "res"; RES("cnRes").innerHTML = `Enter this code on GitHub: <b class="ucode">${esc(r.user_code)}</b> <a href="${esc(r.url)}" target="_blank" rel="noopener">Open github.com/login/device</a>`;
        const tick = async () => {
          if (!$("#cnl")) return;
          let q; try { q = await post("/api/connectors", {action: "github_poll", id: "github"}); } catch (e) { return; }
          if (q.status === "done") { S.connCache = q.connectors; draw(q.connectors); RES("cnRes").className = "res ok"; RES("cnRes").textContent = `Connected to GitHub${q.login ? " as " + q.login : ""}.`; return; }
          if (q.status === "failed") { RES("cnRes").className = "res bad"; RES("cnRes").textContent = q.error; return; }
          setTimeout(tick, (r.interval || 5) * 1000);
        };
        setTimeout(tick, (r.interval || 5) * 1000);
      });
      $$("[data-save]", b).forEach(x => x.onclick = async () => { const v = $(`[data-tok="${x.dataset.save}"]`, b).value.trim(); if (!v) return; const r = await act({action: "token", id: x.dataset.save, token: v}); if (r) { S.connCache = r.connectors; draw(r.connectors); const t = await act({action: "check", id: x.dataset.save}); if (t) say(t.ok ? `Connected: ${t.tools.length} tools.` : t.error, t.ok ? "ok" : "bad"); } });
      $$("[data-on]", b).forEach(x => x.onchange = async () => { const r = await act({action: "enable", id: x.dataset.on, on: x.checked}); if (r) draw(r.connectors); });
      $$("[data-ro]", b).forEach(x => x.onchange = async () => { const r = await act({action: "read_only", id: x.dataset.ro, on: x.checked}); if (r) { draw(r.connectors); say(x.checked ? "Read only: new chats get only its read tools." : "New chats get all its tools again.", "ok"); } });
      $$("[data-off]", b).forEach(x => x.onclick = async () => { if (!await ask({title: "Disconnect this app?", text: "Chats stop using it. You can connect it again any time.", ok: "Disconnect", danger: true})) return; const r = await act({action: "disconnect", id: x.dataset.off}); if (r) draw(r.connectors); });
    };
    draw((await api("/api/connectors")).connectors);
    return;
  } else if (sec === "skills") {
    await loadSkills();
    b.innerHTML = `${close}<h3>Skills</h3><p class="lead">Skills are instructions the agent loads when a task matches, or when you type <code>$name</code> in the message box. They use the same SKILL.md format as Codex and Hermes, and Meshpool shares them with every agent.</p>
      <div class="dropzone" id="skDrop" tabindex="0" role="button">Drop a skill folder, a .zip or a SKILL.md here, or click to choose files<input type="file" id="skFile" multiple hidden></div>
      <ul class="rows2" id="skl"></ul>
      <details><summary class="btn" style="display:inline-block">Write a new skill</summary>
        <div style="margin-top:12px"><label class="field"><span>Name</span><input id="skName" placeholder="e.g. release-notes"></label>
        <label class="field"><span>When to use it (one line)</span><input id="skDesc" placeholder="Write release notes from the commits since the last tag."></label>
        <label class="field"><span>Instructions</span><textarea id="skBody" class="code" placeholder="Step-by-step instructions the agent follows."></textarea></label>
        <button class="btn primary" id="skCreate">Create skill</button></div></details>
      <div class="res" id="skRes"></div><div style="display:flex;gap:8px;margin-top:10px"><button class="btn" id="skImp">Import from other apps</button><button class="btn" id="skOpen">Browse the skills folder</button></div><div id="skImpBox" style="margin-top:10px"></div>`;
    $("#skImp").onclick = () => importPanel($("#skImpBox"), r => { if (r) renderSection("skills"); });
    const drawSk = () => {
      $("#skl").innerHTML = S.skills.map(s => `<li><label class="switch"><input type="checkbox" data-id="${esc(s.id)}" ${s.enabled ? "checked" : ""} aria-label="Use ${esc(s.name)}"><span></span></label><span class="tg"><b>$${esc(s.name)}</b><small>${esc(s.description)}</small></span><button class="btn" data-edit="${esc(s.id)}">Edit</button><button class="iconbtn" data-rm="${esc(s.id)}" aria-label="Remove ${esc(s.name)}">${ICON.x}</button></li>`).join("") || `<li class="pnote">No skills yet.</li>`;
      $$("#skl input[data-id]").forEach(i => i.onchange = () => skillAct({action: "toggle", id: i.dataset.id, on: i.checked}));
      $$("#skl [data-rm]").forEach(x => x.onclick = async () => { if (await ask({title: "Remove this skill?", text: "Its folder is deleted from your skills folder. This can't be undone.", ok: "Remove skill", danger: true})) skillAct({action: "remove", id: x.dataset.rm}); });
      $$("#skl [data-edit]").forEach(x => x.onclick = async () => {
        const sk = await api("/api/skill?id=" + encodeURIComponent(x.dataset.edit));
        $("#skl .sked")?.remove();                 // one editor at a time: its Save always saves its own skill
        const li = x.closest("li"); li.insertAdjacentHTML("afterend", `<li class="sked"><div style="flex:1"><textarea class="code">${esc(sk.text)}</textarea><div style="display:flex;gap:6px;margin-top:6px"><button class="btn primary" data-sk="save">Save</button><button class="btn" data-sk="cancel">Cancel</button></div></div></li>`);
        const ed = li.nextElementSibling;
        $("[data-sk=save]", ed).onclick = () => skillAct({action: "update", id: sk.id, text: $("textarea", ed).value});
        $("[data-sk=cancel]", ed).onclick = drawSk;
        $("textarea", ed).focus();
      });
    };
    const skillAct = async body => { try { S.skills = (await post("/api/skills", body)).skills; drawSk(); RES("skRes").className = "res ok"; RES("skRes").textContent = body.action === "upload" ? "Added." : body.action === "create" ? "Created." : body.action === "update" ? "Saved." : ""; } catch (e) { RES("skRes").className = "res bad"; RES("skRes").textContent = e.message; } };
    drawSk();
    const upload = async (files, paths) => {
      const list = [];
      for (let i = 0; i < files.length; i++) { const f = files[i]; const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); }); list.push({name: (paths && paths[i]) || f.webkitRelativePath || f.name, data}); }
      skillAct({action: "upload", files: list});
    };
    const dz = $("#skDrop");
    dz.onclick = () => $("#skFile").click(); $("#skFile").onchange = () => upload([...$("#skFile").files]);
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = async e => {
      e.preventDefault(); dz.classList.remove("over");
      const items = [...(e.dataTransfer.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
      if (items.some(x => x.isDirectory)) {
        const files = [], paths = [];
        const walk = (entry, prefix) => new Promise(res => {
          if (entry.isFile) entry.file(f => { files.push(f); paths.push(prefix + f.name); res(); });
          else { const rd = entry.createReader(); const all = []; const read = () => rd.readEntries(async ents => { if (!ents.length) { for (const en of all) await walk(en, prefix + entry.name + "/"); res(); } else { all.push(...ents); read(); } }); read(); }
        });
        for (const it of items) await walk(it, "");
        upload(files, paths);
      } else upload([...e.dataTransfer.files]);
    };
    $("#skCreate").onclick = () => { if (!$("#skName").value.trim() || !$("#skBody").value.trim()) { RES("skRes").className = "res bad"; RES("skRes").textContent = "Give the skill a name and instructions."; return; } skillAct({action: "create", name: $("#skName").value, description: $("#skDesc").value, body: $("#skBody").value}); };
    $("#skOpen").onclick = () => openHomeFolder("skills");
  } else if (sec === "plugins") { await drawPlugins(b, close);
  } else if (sec === "harnesses") {
    await loadHarnesses();
    const ex = S.hInfo.examples || [];
    b.innerHTML = `${close}<h3>Agents</h3><p class="lead">The agent is who does the work in a chat. What it runs on is your provider (Settings, Account): ${esc((S.settings.providers || {})[S.settings.provider || "meshpool"] || "")}. Meshpool Auto picks a good agent for each task. Each agent runs on the provider of the model you pick in the message box: your API key or your ChatGPT sign-in. Other agents can be added too.</p>
      <div class="dflt">${[["cowork", "Work"], ["code", "Code"]].map(([m, lb]) => `<label class="field"><span>Default for ${lb}</span><select data-dm="${m}">${S.harnesses.filter(h => h.available !== false && !h.error && h.ready !== false).map(h => `<option value="${esc(h.id)}" ${h.id === S.defaults[m] ? "selected" : ""}>${esc(h.name)}${h.mode !== m && h.type !== "auto" ? " · made for " + (h.mode === "code" ? "Code" : "Work") : ""}</option>`).join("")}</select><small>${m === "code" ? "Meshpool Code and Pi both solve most coding tasks; Pi is a little faster and cheaper, Meshpool Code has the full toolset (browser, connectors, skills)." : "Meshpool Work makes Word, Excel and PowerPoint files and does research. Hermes adds long-term memory for ongoing work."}</small></label>`).join("")}</div>
      <ul class="rows2">${S.harnesses.filter(h => !h.hidden).map(h => `<li><span class="tg"><b>${esc(h.name)}</b> <span class="badge modified">${h.type === "auto" ? "Any task" : h.mode === "cowork" ? "Work" : "Code"}</span>${h.available === false ? ` <span class="badge deleted">${h.unsupported ? "Not available" : "Not installed"}</span>` : h.ready === false ? ` <span class="badge deleted">Needs setup</span>` : ""}<small>${esc(h.description || "")}</small>${h.runs_on && h.ready !== false ? `<small>Runs on ${esc(h.runs_on)}.</small>` : ""}${h.ready === false && h.setup ? `<small>${esc(h.setup)}</small>` : ""}${h.install && h.available === false ? `<small>${esc(h.install)}</small>` : ""}</span>${h.login ? (h.signed_in ? `<span class="badge added">Signed in</span><button class="btn" data-login="${esc(h.id)}">Sign in again</button>` : `<button class="btn primary" data-login="${esc(h.id)}" ${h.available === false ? "disabled" : ""}>Sign in</button>`) : h.builtin || h.id === "hermes" ? "" : `<button class="iconbtn" data-rm="${esc(h.id)}" aria-label="Remove ${esc(h.name)}">${ICON.x}</button>`}</li>`).join("")}</ul>
      ${ex.length ? `<h3 style="font-size:16px;margin:18px 0 8px">Add one</h3><div style="display:flex;gap:6px;flex-wrap:wrap">${ex.map(e => `<button class="btn" data-ex="${esc(e.id)}">${esc(e.name)}</button>`).join("")}</div>` : ""}
      <div class="dropzone" id="hDrop" style="margin-top:14px" tabindex="0" role="button">Or drop an agent's .json file here<input type="file" id="hFile" accept=".json" hidden></div><div class="res" id="hRes"></div>`;
    const act = async body => { try { await post("/api/harnesses", body); await loadHarnesses(); renderSection("harnesses"); renderBar(); } catch (e) { RES("hRes").className = "res bad"; RES("hRes").textContent = e.message; } };
    $$("[data-rm]", b).forEach(x => x.onclick = () => act({remove: x.dataset.rm}));
    $$("[data-login]", b).forEach(x => x.onclick = async () => {
      try { const r = await post("/api/harness-login", {id: x.dataset.login}); RES("hRes").className = "res " + (r.ok ? "" : "bad"); RES("hRes").textContent = r.message; }
      catch (e) { RES("hRes").className = "res bad"; RES("hRes").textContent = e.message; return; }
      const id = x.dataset.login, t0 = Date.now();
      const tick = async () => { if (!RES("hRes")) return; await loadHarnesses(); if (harness(id).signed_in) { await loadSettings(); renderSection("harnesses"); renderBar(); toast(harness(id).name + " is signed in."); return; } if (Date.now() - t0 < 600000) setTimeout(tick, 3000); };
      setTimeout(tick, 3000);
    });
    $$("[data-dm]", b).forEach(x => x.onchange = async () => { await act({default_mode: x.dataset.dm, id: x.value}); store("harness_" + x.dataset.dm, null); if (!S.cur) { S.draft.harness = modeHarness(); renderBar(); renderHero(); } });
    $$("[data-ex]", b).forEach(x => x.onclick = () => act({example: x.dataset.ex}));
    const hd = $("#hDrop"); hd.onclick = () => $("#hFile").click();
    $("#hFile").onchange = async () => { for (const f of $("#hFile").files) await act({text: await f.text()}); };
    hd.ondragover = e => { e.preventDefault(); hd.classList.add("over"); }; hd.ondragleave = () => hd.classList.remove("over");
    hd.ondrop = async e => { e.preventDefault(); hd.classList.remove("over"); for (const f of e.dataTransfer.files) await act({text: await f.text()}); };
  } else if (sec === "mcp") {
    const m = await api("/api/mcp");
    b.innerHTML = `${close}<h3>Tools (MCP servers)</h3><p class="lead">Tools every agent can use. Browser use and computer use are built in. New chats pick up changes.</p>
      ${m.error ? `<div class="res bad">${esc(m.error)}</div>` : ""}
      <ul class="rows2">${m.servers.map(s => `<li><label class="switch"><input type="checkbox" data-n="${esc(s.name)}" ${s.enabled ? "checked" : ""} aria-label="Use ${esc(s.name)}"><span></span></label><span class="tg"><b>${esc(s.name)}</b><small>${esc(s.description)}</small><details class="tech"><summary>Technical details</summary><code>${esc(s.target)}</code></details></span></li>`).join("")}</ul>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="mTest">Check servers</button><button class="btn" id="mEdit">Edit the server list</button><button class="btn" id="mImp">Import from other apps</button></div><div class="res" id="mRes"></div><div id="mImpBox" style="margin-top:10px"></div>
      <div id="mEd" hidden style="margin-top:12px"><textarea class="code" id="mText">${esc(m.text)}</textarea><button class="btn primary" id="mSave" style="margin-top:8px">Save mcp.json</button><p class="lead" style="font-size:13px;margin-top:8px">The common mcpServers format most MCP apps use. Write \${NAME} to use a value from the settings file, such as \${GITHUB_TOKEN}.</p></div>`;
    $$("input[data-n]", b).forEach(i => i.onchange = () => post("/api/mcp", {name: i.dataset.n, enabled: i.checked}).catch(e => toast(e.message)));
    $("#mEdit").onclick = () => $("#mEd").hidden = !$("#mEd").hidden;
    $("#mImp").onclick = () => importPanel($("#mImpBox"), r => { if (r) setTimeout(() => renderSection("mcp"), 1500); });
    $("#mSave").onclick = async () => { try { await post("/api/mcp", {text: $("#mText").value}); renderSection("mcp"); } catch (e) { RES("mRes").className = "res bad"; RES("mRes").textContent = e.message; } };
    $("#mTest").onclick = async () => { RES("mRes").className = "res"; RES("mRes").textContent = "Starting each server..."; try { const r = await post("/api/mcp/test", {workdir: curRoot()}); const rows = r.status.filter(s => s.status !== "disabled"); RES("mRes").className = "res " + (rows.some(s => s.status === "error") ? "bad" : "ok"); RES("mRes").innerHTML = rows.map(s => `${esc(s.name)}: ${s.status === "connected" ? `connected, ${s.tools.length} tools` : esc(s.error)}`).join("<br>") || "No servers are on."; } catch (e) { RES("mRes").className = "res bad"; RES("mRes").textContent = e.message; } };
    drawMcpLibrary(b);
  } else if (sec === "permissions") {
    const draw = async body => {
      const r = await post("/api/permissions", body || {});
      b.innerHTML = `${close}<h3>Permissions</h3><p class="lead">${esc(r.note || "")} Meshpool works only in the folders you add.${r.platform === "darwin" ? " When an agent opens a protected folder such as Desktop, Documents or Downloads for the first time, your Mac asks you to allow it." : ""}</p>
        ${r.platform === "darwin" ? `<button class="btn primary" id="permAll" style="margin-bottom:10px">Allow all</button>` : ""}
        <ul class="rows2">${r.rows.map(x => `<li><span class="dot ${x.ok === true ? "" : "wait"}" style="margin-top:7px;${x.ok === true ? "background:var(--add)" : ""}"></span><span class="tg"><b>${esc(x.name)}</b><small>${x.ok === true ? "Allowed. " : x.ok === false ? "Not allowed yet. " : ""}${esc(x.why)}</small></span>${x.ok === true || r.platform !== "darwin" ? "" : `<button class="btn" data-perm="${esc(x.id)}">${x.ok === false ? "Allow" : "Open settings"}</button>`}</li>`).join("")}</ul>
        <h3 style="font-size:16px;margin:18px 0 8px">Folders Meshpool works in</h3>
        <ul class="rows2">${S.projects.map(p => `<li><span class="tg"><b>${esc(p.name)}</b><small>${esc(p.managed ? "GitHub" : p.path)}</small></span><button class="iconbtn" data-rmp="${esc(p.path)}" aria-label="Remove">${ICON.x}</button></li>`).join("") || `<li class="pnote">No folders yet.</li>`}</ul>
        <button class="btn" id="addP">${ICON.folder} Add a folder</button>`;
      done();
      $$("[data-perm]", b).forEach(x => x.onclick = () => draw({id: x.dataset.perm}));
      $("#permAll")?.addEventListener("click", () => draw({id: "all"}));
      $$("[data-rmp]", b).forEach(x => x.onclick = async () => { await post("/api/projects", {action: "remove", path: x.dataset.rmp}); await loadProjects(); renderSide(); draw(); });
      const rb = document.createElement("div"); rb.id = "rulesBox"; b.appendChild(rb); drawRules(rb);
      $("#addP").onclick = async () => { await pickProject(); await loadProjects(); draw(); };
    };
    await draw(); return;
  } else if (sec === "usage") {
    const u = await api("/api/usage"); const t = u.total;
    b.innerHTML = `${close}<h3>Usage</h3><p class="lead">How much Meshpool's own agents used on this computer. What it cost is on your <a href="https://meshpool.ai" target="_blank" rel="noopener">Meshpool account</a>.</p>
      <div class="statgrid"><div><b>${fmt(t.in)}</b><span>Input tokens</span></div><div><b>${t.in ? Math.round(100 * t.cached / t.in) : 0}%</b><span>Served from cache</span></div><div><b>${fmt(t.out)}</b><span>Output tokens</span></div><div><b>${fmt(t.calls)}</b><span>Model calls</span></div></div>
      <h3 style="font-size:16px;margin:0 0 8px">By model</h3><table class="t"><tr><th>Model</th><th>Chats</th><th>In</th><th>Cached</th><th>Out</th></tr>${Object.entries(u.by_model).map(([m, r]) => `<tr><td title="${esc(m)}">${esc(modelName(m))}</td><td class="n">${r.threads}</td><td class="n">${fmt(r.in)}</td><td class="n">${fmt(r.cached)}</td><td class="n">${fmt(r.out)}</td></tr>`).join("")}</table>`;
  } else if (sec === "terminal") {
    const c = S.settings.cli || {};
    b.innerHTML = `${close}<h3>Terminal command</h3><p class="lead">The same agent works in any terminal. Go to a project folder and type <code>meshpool</code>. Use <code>meshpool -p "..."</code> for one answer, and <code>meshpool acp</code> to plug Meshpool into Zed, JetBrains or other ACP apps.</p>
      <div class="res ${c.installed ? "ok" : ""}">${c.available ? (c.installed ? (c.new_window ? "Installed. Open a new terminal window and type meshpool." : "Installed. In any terminal window, type meshpool.") : "Not on your PATH yet.") : "The meshpool command comes with the Meshpool app for Mac and Windows. On other computers, install it with the one-line installer on the Meshpool download page."}</div>
      ${c.available && !c.installed ? `<button class="btn primary" id="cliBtn" style="margin-top:8px">Install the meshpool command</button>` : ""}`;
    $("#cliBtn")?.addEventListener("click", async () => { try { const r = await post("/api/install-cli"); $(".res", b).textContent = r.message; } catch (e) { $(".res", b).textContent = e.message; } });
  } else if (sec === "office") {
    b.innerHTML = `${close}<h3>Excel, PowerPoint and Word</h3><p class="lead">Add a meshpool panel to the Home tab of Excel, PowerPoint and Word. Ask about what you selected, then insert the answer into your sheet, slide or document. The panel talks only to meshpool on this computer, and nothing changes in your file until you click Insert.</p>
      <div class="res" id="ofRes">Checking…</div>
      <p class="pnote" id="ofNote" hidden></p>
      <div style="display:flex;gap:8px;margin-top:8px"><button class="btn primary" id="ofInstall" hidden>Add meshpool to Office</button><button class="btn" id="ofRemove" hidden>Remove from Office</button></div>
      <pre class="pnote" id="ofLines" hidden style="white-space:pre-wrap"></pre>`;
    const show = r => {
      const on = !!(r.manifest && r.certificate) && !r.needs_trust && !(r.days_left !== null && r.days_left < 30);
      $("#ofRes").className = "res" + (on && r.running ? " ok" : "");
      $("#ofInstall").textContent = r.manifest && r.needs_trust ? "Trust the certificate" : r.manifest ? "Renew the certificate" : "Add meshpool to Office";
      $("#ofRes").textContent = r.manifest && r.needs_trust ? "Added, but the certificate is not trusted yet, so Office cannot load the panel." :
        r.manifest && r.days_left !== null && r.days_left < 30 ? `The certificate expires in ${Math.max(0, r.days_left)} days.` : !on ? "Not added yet." : r.running ? `Added${r.sideloaded.length ? " to " + r.sideloaded.map(x => ({excel: "Excel", powerpoint: "PowerPoint", word: "Word"})[x]).join(", ") : ""}. Restart those apps, then click meshpool on the Home tab.` : "Added, but the panel is not being served. Quit and reopen meshpool.";
      $("#ofNote").hidden = on || (!!r.manifest && !r.needs_trust); $("#ofNote").textContent = r.trust_note + (r.desktop_office ? " Your system asks you to confirm." : " There is no desktop Office here: use Office on the web and upload the manifest (Home, Add-ins, Upload My Add-in).");
      $("#ofInstall").hidden = on; $("#ofRemove").hidden = !r.manifest;
    };
    const act = async (action, btn) => { btn.disabled = true; try { const r = await post("/api/office", {action}); show(r); $("#ofLines").hidden = false; $("#ofLines").textContent = (r.lines || []).join("\n"); } catch (e) { $("#ofRes").className = "res bad"; $("#ofRes").textContent = e.message; } btn.disabled = false; };
    $("#ofInstall").onclick = e => act("install", e.currentTarget);
    $("#ofRemove").onclick = e => act("uninstall", e.currentTarget);
    api("/api/office").then(show).catch(e => { $("#ofRes").className = "res bad"; $("#ofRes").textContent = e.message; });
  } else if (sec === "advanced") {
    const CH = {MH_AGENT_PERMISSIONS: [["ask", "Supervised"], ["edits", "Auto-accept edits"], ["auto", "Full access"]], MH_AUTO_SUBSCRIPTIONS: [["0", "Off"], ["1", "On"]], MH_SUBAGENTS: [["1", "On"], ["0", "Off"]], MH_SUBAGENT_ACCESS: [["inherit", "Same as the chat"], ["edit", "Read and edit files, no commands"], ["read", "Read and research"]], MH_SUBAGENT_AUTO_APPROVE: [["0", "Off"], ["1", "On"]], MH_SUBAGENT_DEPTH: [["1", "1"], ["2", "2"], ["3", "3"]]};
    // Search keys and the GitHub token have their own pages (Web search, Connectors).
    const keys = F.filter(x => !["MESHPOOL_API_KEY", "MESHPOOL_BASE_URL", "MH_MODEL", "MH_PROVIDER", "GITHUB_TOKEN", "MH_ONBOARDED", "BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "SEARXNG_URL", "INSTANTLY_API_KEY"].includes(x.key));
    b.innerHTML = `${close}<h3>Advanced</h3><p class="lead">Most people never need to change these.</p>
      ${keys.map(x => CH[x.key] ? `<label class="field"><span>${esc(x.label)}</span><select id="ad_${x.key}">${CH[x.key].map(([v, l]) => `<option value="${esc(v)}" ${(x.value || x.default) === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select><small>${esc(x.help)}</small></label>`
        : `<label class="field"><span>${esc(x.label)}</span><input id="ad_${x.key}" ${x.key === "GITHUB_TOKEN" ? `type="password" placeholder="${x.set ? "Saved, " + esc(x.hint) : "ghp_... or github_pat_..."}"` : `value="${esc(x.value)}" placeholder="${esc(x.default)}"`} spellcheck="false"><small>${esc(x.help)}</small></label>`).join("")}
      <div style="display:flex;gap:8px"><button class="btn primary" id="adSave">Save</button><button class="btn" id="adData">Browse the data folder</button></div><div class="res" id="adRes"></div>`;
    $("#adSave").onclick = async () => { const values = {}; keys.forEach(x => { values[x.key] = $("#ad_" + x.key).value; }); try { await post("/api/settings", {values}); RES("adRes").className = "res ok"; RES("adRes").textContent = "Saved."; } catch (e) { RES("adRes").className = "res bad"; RES("adRes").textContent = e.message; } };
    $("#adData").onclick = () => openHomeFolder("");
  }
  done();
}

/* ================================== first run ================================== */
const onbSteps = n => `<div class="onb-steps" aria-label="Step ${n} of 3">${[1, 2, 3].map(i => `<span class="${i <= n ? "on" : ""}"></span>`).join("")}<small>Step ${n} of 3</small></div>`;
// Providers: what the models run on. Agents (harnesses) are picked separately.
const PROVIDERS = [["meshpool", "Meshpool API key", "Recommended. Every model on one key, 20% cheaper."],
  ["custom", "Another API key", "OpenAI, OpenRouter or any OpenAI-compatible provider."],
  ["chatgpt", "ChatGPT subscription", "Your ChatGPT Plus, Pro or Business plan, for Meshpool's agents, Pi, Hermes and Codex. No API key needed."]];
const SIGNIN = {chatgpt: {name: "ChatGPT", harness: "codex"}};
function showOnboarding() {
  if ($("#onb")) return;
  const o = document.createElement("div"); o.className = "onb"; o.id = "onb";
  o.innerHTML = `<div class="onb-card" id="onbKey">
    <img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(1)}<h1>Welcome to Meshpool</h1><p>Choose what Meshpool runs on. It stays on this computer, and you can change it any time in Settings.</p>
    <div class="scope prov" role="radiogroup" aria-label="What Meshpool runs on">${PROVIDERS.filter(p => p[0] !== "chatgpt" || !chatgptSignInGone()).map(([id, name, desc], i) => `<button class="scopeopt" data-prov="${id}" role="radio" aria-checked="${i === 0}"><b>${name}</b><small>${desc}</small></button>`).join("")}</div>
    <div id="oKeyBox">
      <p class="credit" id="oCredit">No key yet? <a href="https://meshpool.ai" target="_blank" rel="noopener">Get one at meshpool.ai</a> and run every model 20% cheaper.</p>
      <label class="field" id="oBaseF" style="max-width:none" hidden><span>Provider address (base URL)</span><input id="oBase" spellcheck="false" placeholder="https://api.openai.com/v1"></label>
      <label class="field" id="oModelF" style="max-width:none" hidden><span>Model</span><input id="oModel" spellcheck="false" placeholder="gpt-4.1-mini"></label>
      <label class="field" style="max-width:none"><span>API key</span><span class="keyrow"><input id="oKey" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your key"><button class="btn" id="oShow" type="button">Show</button></span></label>
    </div>
    <div id="oSubBox" hidden><p class="credit" id="oSubTxt"></p><button class="btn primary" id="oSign">Sign in</button></div>
    <div class="res" id="oRes"></div>
    <div class="onb-go"><button class="bigbtn" id="oGo">Continue</button></div></div>
    <div class="onb-card" id="onbPerm" hidden></div>`;
  document.body.appendChild(o);
  let prov = "meshpool";
  const res = RES("oRes");
  const sub = () => SIGNIN[prov] && (S.harnesses || []).find(h => h.id === SIGNIN[prov].harness);
  const drawSub = () => {
    const h = sub(); const name = SIGNIN[prov]?.name || "";
    const what = " Meshpool Code, Meshpool Work, Pi, Hermes and Codex all run on it.";
    $("#oSign").hidden = !h || h.signed_in || h.available === false;
    $("#oSubTxt").textContent = !h ? "Loading…" : h.available === false ? (h.unsupported ? `Signing in with ${name} needs Codex, which is not part of the Meshpool app. Use a Meshpool API key or another API key.` : `Signing in to ${name} needs Node.js. The desktop app includes it; otherwise install it from nodejs.org.`)
      : (h.signed_in ? `You are signed in to ${name} on this computer. Usage comes out of your plan.` : `Sign in with your ${name} account. Usage comes out of your plan, not an API key.`) + what;
    $("#oGo").disabled = !(h && h.signed_in);
  };
  const pick = id => {
    prov = id; res.className = "res"; res.textContent = "";
    $$("[data-prov]", o).forEach(x => x.setAttribute("aria-checked", x.dataset.prov === id));
    const isSub = !!SIGNIN[id];
    $("#oKeyBox").hidden = isSub; $("#oSubBox").hidden = !isSub;
    $("#oBaseF").hidden = $("#oModelF").hidden = id !== "custom"; $("#oCredit").hidden = id !== "meshpool";
    $("#oGo").disabled = false;
    if (isSub) { drawSub(); loadHarnesses().then(drawSub).catch(() => {}); } else $(id === "custom" ? "#oBase" : "#oKey").focus();
  };
  $$("[data-prov]", o).forEach(x => x.onclick = () => pick(x.dataset.prov));
  $("#oKey").focus();
  $("#oShow").onclick = () => { const i = $("#oKey"); i.type = i.type === "password" ? "text" : "password"; $("#oShow").textContent = i.type === "password" ? "Show" : "Hide"; };
  $("#oKey").onkeydown = e => { if (e.key === "Enter") $("#oGo").click(); };
  $("#oKey").oninput = () => { res.textContent = ""; };
  $("#oSign").onclick = async () => {
    const id = SIGNIN[prov].harness;
    try { const r = await post("/api/harness-login", {id}); res.className = "res " + (r.ok ? "" : "bad"); res.textContent = r.message; } catch (e) { res.className = "res bad"; res.textContent = e.message; return; }
    const t0 = Date.now();
    const tick = async () => { if (!$("#oSubBox") || SIGNIN[prov]?.harness !== id) return; await loadHarnesses(); drawSub(); if (sub()?.signed_in) { res.className = "res ok"; res.textContent = "Signed in."; return; } if (Date.now() - t0 < 600000) setTimeout(tick, 3000); };
    setTimeout(tick, 3000);
  };
  const finish = async () => { await loadSettings(); await loadHarnesses(); await loadModels(true); renderBar(); renderHero?.(); $("#onbKey").remove(); permStep(o); };
  $("#oGo").onclick = async () => {
    if (SIGNIN[prov]) {
      if (!sub()?.signed_in) { res.className = "res bad"; res.textContent = "Sign in first."; return; }
      $("#oGo").disabled = true;
      try { await post("/api/provider", {provider: prov}); store("harness_code", null); store("harness_cowork", null); await finish(); } catch (e) { res.className = "res bad"; res.textContent = e.message; $("#oGo").disabled = false; }
      return;
    }
    const key = $("#oKey").value.trim();
    if (!key) { res.className = "res bad"; res.textContent = "Paste your API key first."; $("#oKey").focus(); return; }
    const values = {MESHPOOL_API_KEY: key, MESHPOOL_BASE_URL: ""};
    if (prov === "custom") {
      const base = $("#oBase").value.trim(), model = $("#oModel").value.trim();
      if (!base || !model) { res.className = "res bad"; res.textContent = "Fill in the provider address and a model it serves."; (base ? $("#oModel") : $("#oBase")).focus(); return; }
      values.MESHPOOL_BASE_URL = base; values.MH_MODEL = model;
    }
    $("#oGo").disabled = true; res.className = "res"; res.textContent = "Checking your key…";
    try {
      const r = await post("/api/test", {values: prov === "custom" ? values : {MESHPOOL_API_KEY: key, MESHPOOL_BASE_URL: "https://llm.meshpool.ai/v1"}});   // check first; save only a key that works
      if (!r.ok) { res.className = "res bad"; res.textContent = connMsg(r.detail); $("#oGo").disabled = false; return; }
      await post("/api/settings", {values}); await post("/api/provider", {provider: prov});
      store("harness_code", null); store("harness_cowork", null);
      await finish();
    } catch (e) { res.className = "res bad"; res.textContent = e.message; $("#oGo").disabled = false; }
  };
}

/* Bring your context: what ChatGPT, Claude or Gemini already knows about you, by prompt or by data export, and
   custom GPT / project instructions as skills. */
async function contextPanel(box) {
  if (!box) return;
  let prompt = "", local = {}, links = [];
  try { const ic = await api("/api/import-context"); prompt = ic.prompt; links = ic.links || []; } catch (e) { /* the server is older: hide the panel */ return; }
  try { local = await api("/api/import-chats"); } catch (e) { local = {}; }
  const srcs = Object.entries(local).filter(([, v]) => v.found);
  const n = x => Number(x || 0).toLocaleString();
  box.innerHTML = `<h3 class="onb-h">Bring your chats and memory</h3>
    <p class="muted">It all stays on this computer.</p>
    <ol class="ctx-steps">
      <li><b>Chats from another assistant</b>
        <div class="ctx-acts">${links.map(l => `<button class="btn" data-ext="${esc(l.url)}">1. Get your ${esc(l.name)} export</button>`).join("")}</div>
        <div class="dropzone" id="ctxDrop" tabindex="0" role="button">2. Drop the .zip here<input type="file" id="ctxZip" accept=".zip" hidden></div></li>
      ${srcs.length ? `<li><b>On this computer</b>
        ${srcs.map(([k, v]) => `<span class="ctx-src"><span>${esc(v.name)} <small class="muted">${n(v.found)} session${v.found === 1 ? "" : "s"}</small></span><button class="btn" data-local="${esc(k)}" ${v.imported >= v.found ? "disabled" : ""}>${v.imported >= v.found ? "Imported" : "Import"}</button></span>`).join("")}</li>` : ""}
      <li><b>What an assistant knows about you</b>
        <div class="ctx-acts"><button class="btn" id="ctxCopy">1. Copy this prompt</button></div>
        <textarea id="ctxText" rows="3" placeholder="2. Paste its answer here"></textarea>
        <div class="ctx-acts end"><button class="btn primary" id="ctxGo">3. Add to memory</button></div></li>
      <li><details><summary><b>A custom GPT's instructions</b></summary>
        <input id="ctxName" placeholder="Name" spellcheck="false">
        <textarea id="ctxIns" rows="4" placeholder="Paste the instructions"></textarea>
        <div class="ctx-acts end"><button class="btn primary" id="ctxSkill">Save as a skill</button></div></details></li>
    </ol><div class="res" id="ctxRes"></div>`;
  $$("[data-ext]", box).forEach(x => { x.onclick = () => openExternal(x.dataset.ext); });
  const res = (t, bad, ok) => { const r = $("#ctxRes", box); r.className = "res" + (bad ? " bad" : ok ? " ok" : ""); r.textContent = t; };
  const refresh = async () => { try { if (typeof loadThreads === "function") await loadThreads(); } catch (e) { /* the list refreshes later */ } };
  $("#ctxCopy", box).onclick = async () => { try { await navigator.clipboard.writeText(prompt); res("Copied. Paste it into your assistant, then paste the answer here."); } catch (e) { $("#ctxText", box).value = prompt; res("Copy the text in the box, then replace it with the answer."); } };
  $("#ctxGo", box).onclick = async () => {
    const text = $("#ctxText", box).value.trim(); if (!text) return res("Paste the answer first.", true);
    try { const r = await post("/api/import-context", {text}); res(`${r.memory} to review in Memory, ${r.instructions} instructions saved.` + (r.skipped?.length ? ` ${r.skipped.length} skipped.` : ""), false, true); if (r.memory) toast("Review what it found.", 9000, {label: "Open Memory", fn: () => { try { closeSettings(); } catch (e) { /* onboarding */ } goPage("memory"); }}); } catch (e) { res(e.message, true); }
  };
  const upload = async f => {
    if (!f) return;
    if (!/\.zip$/i.test(f.name)) return res("Choose the .zip file from ChatGPT or another assistant, without unzipping it.", true);
    res(`Importing ${f.name}…`);
    try {
      const r = await api("/api/import-file?chats=1&context=1", {method: "POST", body: f, headers: {"Content-Type": "application/zip"}});
      await refresh();
      res(`${r.kind === "claude" ? "Assistant" : "ChatGPT"} export: ${n(r.imported)} chat${r.imported === 1 ? "" : "s"} imported${r.skipped ? ` (${n(r.skipped)} already here)` : ""}.` + (r.context_pending ? " Facts about you will appear in Memory to review." : ""), false, true);
    } catch (e) { res(e.message, true); }
  };
  const dz = $("#ctxDrop", box);
  dz.onclick = () => $("#ctxZip", box).click(); dz.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#ctxZip", box).click(); } };
  $("#ctxZip", box).onchange = ev => upload(ev.target.files[0]);
  dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); };
  $$("[data-local]", box).forEach(x => x.onclick = async () => {
    x.disabled = true; res(`Importing ${local[x.dataset.local].name} sessions…`);
    try {
      const r = await post("/api/import-chats", {sources: [x.dataset.local]}); await refresh();
      res(`Imported ${n(r.imported)} ${local[x.dataset.local].name} session${r.imported === 1 ? "" : "s"} as chats${r.skipped ? ` (${n(r.skipped)} already here)` : ""}.`, false, true);
      x.textContent = "Imported";
    } catch (e) { x.disabled = false; res(e.message, true); }
  });
  $("#ctxSkill", box).onclick = async () => {
    const instructions = $("#ctxIns", box).value.trim(); if (!instructions) return res("Paste the instructions first.", true);
    try { const r = await post("/api/import-context", {name: $("#ctxName", box).value.trim(), instructions}); res(`Saved the skill ${r.skill}.`, false, true); } catch (e) { res(e.message, true); }
  };
}

/* Settings, Plugins: built-in plugin groups (one switch each) and installed plugins in the Claude Code format.
   Nothing in a plugin runs at install; its MCP servers stay off until the user turns each one on. */
async function drawPlugins(b, close) {
  let d;
  try { d = await api("/api/plugins"); } catch (e) { b.innerHTML = `${close}<h3>Plugins</h3><div class="res bad">${esc(e.message)}</div>`; return; }
  const say = (text, bad) => { RES("plRes").className = "res " + (bad ? "bad" : "ok"); RES("plRes").textContent = text; };
  const act = async body => { try { const r = await post("/api/plugins", body); await loadSkills(); render(r); return r; } catch (e) { say(e.message, true); return null; } };
  const installed = r => { if (!r?.installed?.length) return; const p = r.installed; const sv = p.reduce((n, x) => n + x.servers.length, 0); say(`Installed ${p.map(x => x.name).join(", ")}: ${p.reduce((n, x) => n + x.skills.length, 0)} skills${sv ? `, ${sv} MCP server${sv === 1 ? "" : "s"} (off until you turn them on)` : ""}.` + p.flatMap(x => x.notes).map(n => " " + n).join("")); };
  const group = g => `<li><label class="switch"><input type="checkbox" data-pb="${esc(g.id)}" ${g.enabled ? "checked" : ""} aria-label="Use ${esc(g.name)}"><span></span></label><span class="tg"><b>${esc(g.name)}</b> <span class="badge modified">${g.skills.length} skills</span>${g.partly ? ` <span class="badge modified">Some on</span>` : ""}<small>${esc(g.description)}</small><small>${esc(g.source)}</small><details><summary style="cursor:pointer;font-size:12px;color:var(--ink-3)">Skills it turns on</summary><small>${g.skills.map(s => "$" + esc(s.id)).join(", ")}</small></details></span></li>`;
  const server = s => `<span style="display:flex;gap:8px;align-items:center;margin-top:6px"><label class="switch" title="Run this server"><input type="checkbox" data-ps="${esc(s.name)}" data-cmd="${esc(s.target)}" ${s.enabled ? "checked" : ""} ${s.present ? "" : "disabled"} aria-label="Turn on ${esc(s.name)}"><span></span></label><code>${esc(s.target)}</code></span>`;
  const plugin = p => `<li><label class="switch"><input type="checkbox" data-pi="${esc(p.name)}" ${p.enabled ? "checked" : ""} aria-label="Use ${esc(p.name)}"><span></span></label><span class="tg"><b>${esc(p.name)}</b>${p.version ? ` <span class="badge modified">${esc(p.version)}</span>` : ""}<small>${esc(p.description)}</small>${p.skills.length ? `<small>Skills: ${p.skills.map(s => "$" + esc(s)).join(", ")}</small>` : ""}${(p.server_status || []).map(server).join("")}<small>From ${esc(p.source)}</small></span><button class="iconbtn" data-pr="${esc(p.name)}" aria-label="Remove ${esc(p.name)}">${ICON.x}</button></li>`;
  const render = v => {
    d = v;
    b.innerHTML = `${close}<h3>Plugins</h3><p class="lead">A plugin bundles skills, MCP servers and instructions for one kind of work, in the plugin format coding agents share. Nothing in a plugin runs when you install it, and its MCP servers stay off until you turn them on.</p>
      <h3 style="font-size:16px;margin:14px 0 6px">Built in</h3><ul class="rows2">${d.catalog.map(group).join("")}</ul>
      <h3 style="font-size:16px;margin:14px 0 6px">Installed</h3><ul class="rows2">${d.plugins.map(plugin).join("") || `<li class="pnote">No plugins installed yet.</li>`}</ul>
      <div class="keyrow"><input id="plSrc" placeholder="Folder, .zip, git URL or marketplace.json" spellcheck="false"><button class="btn primary" id="plAdd">Install</button></div>
      <div class="dropzone" id="plDrop" style="margin-top:8px" tabindex="0" role="button">Or drop a plugin .zip or a marketplace.json here<input type="file" id="plFile" accept=".zip,.json" hidden></div><div class="res" id="plRes"></div>`;
    $(".mclose", b)?.addEventListener("click", () => $(".scrim").remove());
    $$("[data-pb]", b).forEach(x => x.onchange = () => act({action: "enable", name: x.dataset.pb, on: x.checked}));
    $$("[data-pi]", b).forEach(x => x.onchange = () => act({action: "enable", name: x.dataset.pi, on: x.checked}));
    $$("[data-pr]", b).forEach(x => x.onclick = async () => { if (await ask({title: "Remove this plugin?", text: "Its skills, MCP servers and files are removed. Skills you had before stay.", ok: "Remove plugin", danger: true})) act({action: "remove", name: x.dataset.pr}); });
    $$("[data-ps]", b).forEach(x => x.onchange = async () => {
      if (x.checked && !await ask({title: "Turn on this server?", text: `It runs on this computer: ${x.dataset.cmd}`, ok: "Turn on"})) { x.checked = false; return; }
      try { await post("/api/mcp", {name: x.dataset.ps, enabled: x.checked}); } catch (e) { say(e.message, true); x.checked = !x.checked; }
    });
    $("#plAdd").onclick = async () => { const src = $("#plSrc").value.trim(); if (!src) { $("#plSrc").focus(); return; } say("Installing..."); installed(await act({action: "install", source: src})); };
    const upload = async f => { if (!f) return; const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); }); say("Installing..."); installed(await act({action: "upload", file: {name: f.name, data}})); };
    const dz = $("#plDrop"); dz.onclick = () => $("#plFile").click(); $("#plFile").onchange = () => upload($("#plFile").files[0]);
    dz.ondragover = e => { e.preventDefault(); dz.classList.add("over"); }; dz.ondragleave = () => dz.classList.remove("over");
    dz.ondrop = e => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files[0]); };
  };
  render(d);
}

/* Settings, MCP servers: a library of well-known servers. Adding one puts it in the list switched off; the user fills
   its settings (saved in meshpool.config on this computer) and turns it on. OAuth ones sign in through Connectors. */
async function drawMcpLibrary(b) {
  const box = document.createElement("div"); b.appendChild(box);
  let d;
  try { d = await api("/api/mcp-library"); } catch (e) { box.innerHTML = `<div class="res bad">${esc(e.message)}</div>`; return; }
  const say = (text, bad) => { RES("mlRes").className = "res " + (bad ? "bad" : "ok"); RES("mlRes").textContent = text; };
  const secret = n => /KEY|TOKEN|SECRET|PASSWORD|URI|STRING/.test(n);
  const fields = e => e.settings.map(s => `<label class="field" style="margin-top:6px"><span>${esc(s.name)}</span><input data-ms="${esc(s.name)}" type="${secret(s.name) ? "password" : "text"}" placeholder="${esc(s.hint)}" autocomplete="off" spellcheck="false"></label>`).join("");
  const button = e => e.transport === "connector" || e.transport === "oauth" ? (e.enabled ? "" : `<button class="btn" data-ma="${esc(e.id)}">Connect</button>`)
    : !e.added ? `<button class="btn" data-ma="${esc(e.id)}">Add</button>` : e.enabled ? "" : `<button class="btn primary" data-mo="${esc(e.id)}">${e.settings.length ? "Save and turn on" : "Turn on"}</button>`;
  const row = e => `<li data-me="${esc(e.id)}"><span class="tg"><b>${esc(e.name)}</b> ${e.enabled ? `<span class="badge added">On</span>` : e.added ? `<span class="badge modified">Added, off</span>` : ""}<small>${esc(e.description)}</small>${e.target ? `<code>${esc(e.target)}</code>` : ""}<small>${esc(e.license)} · <a href="${esc(e.docs)}" target="_blank" rel="noopener">Docs</a>${e.settings.length ? ` · Needs ${e.settings.map(s => esc(s.name)).join(", ")}` : ""}</small>${e.added && !e.enabled && e.settings.length && e.transport !== "connector" ? fields(e) : ""}</span>${button(e)}</li>`;
  const draw = () => {
    const cat = S.mcpLibCat || "";
    box.innerHTML = `<h3 style="font-size:16px;margin:18px 0 4px">Library</h3><p class="lead" style="font-size:13px">Well-known MCP servers, checked to exist. Adding one puts it in the list above, switched off. Fill in its settings, then turn it on. Keys stay on this computer.</p>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">${["", ...d.categories].map(c => `<button class="btn${c === cat ? " primary" : ""}" data-mc="${c}">${c ? c[0].toUpperCase() + c.slice(1) : "All"}</button>`).join("")}</div>
      <ul class="rows2">${d.library.filter(e => !cat || e.category === cat).map(row).join("")}</ul><div class="res" id="mlRes"></div>`;
    $$("[data-mc]", box).forEach(x => x.onclick = () => { S.mcpLibCat = x.dataset.mc; draw(); });
    $$("[data-ma]", box).forEach(x => x.onclick = async () => {
      try {
        const r = await post("/api/mcp-library", {action: "add", id: x.dataset.ma});
        if (r.result.connector) { await post("/api/connectors", {action: "connect", id: r.result.connector}); S.connCache = null; d = r; draw(); say(`Finish signing in in the ${r.inapp === false ? "browser window" : "Browser panel"}. New chats can then use it.`); return; }
        renderSection("mcp");
      } catch (e) { say(e.message, true); }
    });
    $$("[data-mo]", box).forEach(x => x.onclick = async () => {
      const values = {}; $$(`[data-me="${x.dataset.mo}"] [data-ms]`, box).forEach(i => { if (i.value.trim()) values[i.dataset.ms] = i.value.trim(); });
      try { await post("/api/mcp-library", {action: "configure", id: x.dataset.mo, values}); renderSection("mcp"); } catch (e) { say(e.message, true); }
    });
  };
  draw();
}

/* Bring your setup: skills and MCP servers from Claude, Codex, Cursor and other apps on this computer. */
async function importPanel(box, onDone) {
  box.innerHTML = `<div class="res">Looking…</div>`;
  let f;
  try { f = await api("/api/import-agents"); } catch (e) { box.innerHTML = `<div class="res bad">${esc(e.message)}</div>`; return; }
  const agents = f.agents.filter(a => a.items.length);
  if (!agents.length) { box.innerHTML = `<span></span>`; onDone?.(null); return; }
  const KIND = {skill: "Skill", mcp: "Tool", memory: "Memory", instructions: "Instructions"};
  const item = it => `<label class="imp ${it.exists ? "have" : ""}"><input type="checkbox" data-key="${esc(it.key)}" ${it.exists ? "disabled" : it.kind === "instructions" ? "" : "checked"}><span class="tg"><b>${esc(it.name)}</b> <span class="badge modified">${KIND[it.kind]}</span>${it.exists ? ` <span class="badge">Already here</span>` : ""}${it.kind === "memory" ? "" : `<small>${esc((it.preview || "").slice(0, 140))}</small>`}${(it.stripped || []).length ? `<small>Left out: ${esc(it.stripped.join(", "))}</small>` : ""}</span></label>`;
  const sum = a => ["skill", "mcp", "memory", "instructions"].filter(k => a.counts[k]).map(k => `${a.counts[k]} ${({skill: "skill", mcp: "tool", memory: "memor", instructions: "instruction file"})[k]}${k === "memory" ? (a.counts[k] === 1 ? "y" : "ies") : a.counts[k] === 1 ? "" : "s"}`).join(", ");
  box.innerHTML = `<h3 class="onb-h">Agents on this computer</h3>
    ${agents.map(a => `<details class="impag" open><summary><b>${esc(a.name)}</b> <small class="muted">${esc(sum(a))}</small></summary><div class="impl">${a.items.map(item).join("")}</div></details>`).join("")}${agents.some(a => a.credentials_not_read.length) ? `<small class="muted">Logins are never read.</small>` : ""}
    <div style="display:flex;gap:8px;align-items:center;margin-top:10px"><button class="btn primary" id="impGo">Import selected</button><button class="linkbtn" id="impDry">Preview only</button></div><div class="res" id="impRes"></div>`;
  const keys = () => $$("[data-key]", box).filter(x => x.checked && !x.disabled).map(x => x.dataset.key);
  const say = (t, cls) => { const r = $("#impRes", box); r.className = "res " + (cls || ""); r.textContent = t; };
  const text = (r, dry) => `${dry ? "Would import" : "Imported"} ${r.skills.length} skill${r.skills.length === 1 ? "" : "s"}, ${r.mcp.length} tool${r.mcp.length === 1 ? "" : "s"}, ${r.memory} to review in Memory, ${r.instructions} instruction file${r.instructions === 1 ? "" : "s"}.`
    + (r.stripped.length ? ` Keys left out of ${r.stripped.map(x => x.server).join(", ")}: add them in Settings.` : "") + (r.errors.length ? " Problems: " + r.errors.join("; ") : "");
  $("#impDry", box).onclick = async () => { const k = keys(); if (!k.length) return say("Nothing selected."); try { say(text(await post("/api/import-agents", {keys: k, dry_run: true}), true)); } catch (e) { say(e.message, "bad"); } };
  $("#impGo", box).onclick = box._import = async () => {
    const k = keys(); if (!k.length) return say("Nothing selected.");
    $("#impGo", box).disabled = true;
    try {
      const r = await post("/api/import-agents", {keys: k});
      await loadSkills(); S.connCache = null;
      say(text(r, false), r.errors.length ? "bad" : "ok"); box.dataset.done = "1"; onDone?.(r);
      if (r.memory) toast("Review what it found.", 9000, {label: "Open Memory", fn: () => { try { closeSettings(); } catch (e) { /* onboarding */ } goPage("memory"); }});
    } catch (e) { say(e.message, "bad"); }
    $("#impGo", box).disabled = false;
  };
}

/* Permissions up front: one step on first launch (and once after an update) that asks for everything at once. */
function permStep(host) {
  const o = host || Object.assign(document.createElement("div"), {className: "onb", id: "onb"});
  if (!host) { if ($("#onb")) return; document.body.appendChild(o); }
  store("permsAsked", 1); store("onbStep", 2);
  const mac = S.settings.platform === "darwin";
  let card = $("#onbPerm", o);
  if (!card) { card = document.createElement("div"); card.className = "onb-card"; card.id = "onbPerm"; o.appendChild(card); }
  card.hidden = false;
  card.innerHTML = `<img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(2)}<h1>Choose where Meshpool can work</h1><p>Meshpool only works in the places you allow. You can change this any time in Settings → Permissions.</p>
    <div class="scope" role="radiogroup" aria-label="Where Meshpool can work">
      <button class="scopeopt" data-scope="pick" role="radio"><b>Folders I choose</b><small>Add one or more folders now.</small></button>
      <button class="scopeopt" data-scope="home" role="radio"><b>My whole home folder</b><small>Documents, Desktop, Downloads and everything else in your user folder.</small></button>
      <button class="scopeopt" data-scope="later" role="radio"><b>Start in Meshpool Projects</b><small>A folder Meshpool made for you. Add your own folders any time.</small></button>
    </div><div class="res" id="oScope"></div>
    <div id="oPermH"><h3 class="onb-h">What Meshpool can do</h3>
    <ul class="rows2" id="oPerms"></ul></div>
    <div class="onb-go"><button class="bigbtn" id="oAll">Allow all</button><button class="btn" id="oDone">Continue</button></div>`;
  const draw = async body => {
    try {
      const r = await post("/api/permissions", body || {});
      const rows = r.rows.filter(x => x.id !== "automation");
      $("#oPerms", card).innerHTML = rows.map(x => `<li><span class="tg"><b>${esc(x.name)}</b><small>${esc(x.why)}</small></span>${x.ok ? `<span class="badge added">Allowed</span>` : mac || x.id === "notifications" ? `<button class="btn" data-p="${esc(x.id)}">${x.id === "disk" ? "Open settings" : "Allow"}</button>` : `<span class="badge">Not found</span>`}</li>`).join("") || `<li class="pnote">${esc(r.note)}</li>`;
      $$("[data-p]", card).forEach(x => x.onclick = () => draw({id: x.dataset.p}));
      const needs = rows.some(x => !x.ok && (mac || x.id === "notifications"));
      $("#oAll", card).hidden = !needs; $("#oPermH", card).hidden = !rows.length;
      $("#oDone", card).className = needs ? "btn" : "bigbtn";
    } catch (e) { /* the step still works without the list */ }
  };
  $$("[data-scope]", card).forEach(x => x.onclick = async () => {
    $$("[data-scope]", card).forEach(y => y.setAttribute("aria-checked", y === x));
    const res = $("#oScope", card);
    try {
      if (x.dataset.scope === "pick") { await pickProject(); res.className = S.projects.length ? "res ok" : "res"; res.textContent = S.projects.length ? `Added ${S.projects.map(x => x.name || projName(x.path)).join(", ")}. Add more any time from the sidebar.` : "No folder added yet."; }
      else if (x.dataset.scope === "home") { const r = await post("/api/projects", {action: "home"}); S.projects = r.projects; renderSide(); newThread(r.picked); res.className = "res ok"; res.textContent = "Meshpool can work anywhere in your home folder. It still asks before changing files unless you allow edits."; }
      else { res.className = "res"; res.textContent = "Tasks start in your Meshpool Projects folder. Add your own folders any time from the sidebar."; }
      if (mac && x.dataset.scope !== "later") draw({id: "folders"});
    } catch (e) { res.className = "res bad"; res.textContent = e.message; }
  });
  $("#oAll", card).onclick = async () => { $("#oAll", card).disabled = true; await draw({id: "all"}); $("#oAll", card).disabled = false; $("#oAll", card).textContent = "Ask again"; };
  document.addEventListener("visibilitychange", () => { if (!document.hidden && $("#onbPerm")) draw(); });
  $("#oDone", card).onclick = () => setupStep(o, card);
  if (!S.projects.length) { const later = $("[data-scope=later]", card); later.setAttribute("aria-checked", true); $("#oScope", card).className = "res"; $("#oScope", card).textContent = "Tasks start in your Meshpool Projects folder. Add your own folders any time from the sidebar."; }
  draw();
}

function setupStep(o, prev) {
  prev?.remove();
  const card = document.createElement("div"); card.className = "onb-card"; card.id = "onbSetup"; o.appendChild(card);
  card.innerHTML = `<img class="logo mlogo" src="${logoSrc()}" alt="Meshpool">
    ${onbSteps(3)}<h1>Bring your setup</h1><p>Optional. You can skip this.</p>
    <div id="oImport"></div>
    <div id="oContext"></div>
    <div class="onb-go"><button class="bigbtn" id="oFinish">Start using Meshpool</button></div>`;
  importPanel($("#oImport", card)).then(() => { const b = $("#impGo", card); if (b) b.hidden = true; });
  contextPanel($("#oContext", card));
  store("onbStep", 3);
  $("#oFinish", card).onclick = async () => {
    store("onbDone", 1); post("/api/settings", {values: {MH_ONBOARDED: "1"}}).catch(() => {});        // ticked items are imported on the way out; no extra click
    const box = $("#oImport", card);
    if (box?._import && !box.dataset.done && $$("[data-key]", box).some(x => x.checked)) { $("#oFinish", card).disabled = true; $("#oFinish", card).textContent = "Importing…"; await box._import(); }
    o.remove(); renderBar(); renderHero();
  };
}

/* ================================== updates ================================== */
async function checkUpdate(force) {
  try { S.upd = await post("/api/update/check", {force: !!force}); } catch (e) { S.upd = {ok: false, error: e.message}; }
  await updStatus();
  return S.upd;
}
/* The background updater's state: downloading, ready ("Restart to update"), or an error. Polled quickly while it works. */
async function updStatus() {
  try { S.ust = await post("/api/update/status", {}); } catch (e) { S.ust = null; }
  renderUpdBar(); if ($("#updMore")) updatePanel(S.upd);
  const r = S.ust?.last_result;
  if (r) toast(r.message || (r.ok ? "Meshpool was updated." : "The update did not finish."), r.ok ? 5000 : 12000);
  clearTimeout(S.ustT);
  S.ustT = setTimeout(updStatus, ["downloading", "verifying", "staging", "checking"].includes(S.ust?.state) ? 3000 : 60000);
  return S.ust;
}
function updPct(s) { return s && s.total ? Math.min(99, Math.floor(100 * s.done / s.total)) : 0; }
function renderUpdBar() {
  const bar = $("#updBar"); if (!bar) return;
  const s = S.ust, u = S.upd;
  if (s?.state === "ready") { bar.hidden = false; bar.dataset.act = "apply"; bar.innerHTML = `<span class="dot"></span>Update ready<span class="go">Restart</span>`; }
  else if (["downloading", "verifying", "staging"].includes(s?.state)) { bar.hidden = false; bar.dataset.act = "about"; bar.innerHTML = `<span class="dot"></span>Downloading update${s.state === "downloading" && s.total ? " " + updPct(s) + "%" : "..."}`; }
  else if (u?.ok && u.newer) { bar.hidden = false; bar.dataset.act = "about"; bar.innerHTML = `<span class="dot"></span>Meshpool ${esc(u.latest || "")} is available<span class="go">Update</span>`; }
  else bar.hidden = true;
}
async function applyUpdate() {
  let r; try { r = await post("/api/update/apply", {}); } catch (e) { r = {ok: false, error: e.message}; }
  if (!r.ok) { toast(r.error || "Could not restart to update.", 8000); return; }
  if (r.mode === "code") { restartApp(); return; }
  toast("Updating Meshpool. It reopens in a moment.", 60000);
}
function updatePanel(u) {
  const res = RES("updRes"), more = $("#updMore"); if (!res || !more) return;
  const s = S.ust || {}, st = s.settings || {};
  const opts = `<div style="margin-top:12px"><label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="updAuto" ${st.auto_download === false ? "" : "checked"}> Download updates automatically</label><small style="color:var(--ink-3)">An update that is ready installs when you quit Meshpool, or when you press Restart.</small></div>`;
  const bindOpts = () => { const c = $("#updAuto"); if (c) c.onchange = () => post("/api/update/settings", {auto_download: c.checked}).catch(() => {}); };
  if (s.state === "ready") { res.className = "res ok"; res.textContent = `Meshpool ${s.latest} is ready to install.`;
    more.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-top:10px"><button class="btn primary" id="updGo">Restart to update</button><a class="btn" href="${esc(s.url || "")}" target="_blank" rel="noopener">What's new</a></div>${opts}`;
    $("#updGo").onclick = applyUpdate; bindOpts(); return; }
  if (["downloading", "verifying", "staging"].includes(s.state)) { res.className = "res"; res.textContent = s.state === "downloading" ? `Downloading Meshpool ${s.latest}${s.total ? " (" + updPct(s) + "%)" : ""}. You can keep working.` : `Checking Meshpool ${s.latest}...`; more.innerHTML = opts; bindOpts(); return; }
  if (s.state === "error" && s.error && u?.newer !== false) { res.className = "res bad"; res.textContent = s.error;
    more.innerHTML = `<div style="display:flex;gap:8px;margin-top:10px"><button class="btn" id="updGo">Try again</button>${s.url ? `<a class="btn" href="${esc(s.url)}" target="_blank" rel="noopener">Release page</a>` : ""}</div>${opts}`;
    $("#updGo").onclick = async () => { await post("/api/update/download", {}).catch(() => {}); setTimeout(updStatus, 800); }; bindOpts(); return; }
  if (!u) { res.textContent = ""; more.innerHTML = ""; return; }
  if (!u.ok) { res.className = "res bad"; res.textContent = u.error; more.innerHTML = ""; return; }
  if (!u.newer) { res.className = "res ok"; res.textContent = `You have the latest version (${u.current}).`; more.innerHTML = opts; bindOpts(); return; }
  res.className = "res"; res.textContent = `Meshpool ${u.latest} is available. You have ${u.current}.`;
  const how = ["mac-app", "win-app"].includes(u.kind) ? "Downloads in the background; installs when you restart." : u.kind === "dev" ? "This is a git checkout: run git pull." : "Installs in place in under a minute, then restarts.";
  more.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-top:10px">${u.kind === "dev" ? "" : `<button class="btn primary" id="updGo">${["mac-app", "win-app"].includes(u.kind) ? "Download update" : "Update now"}</button>`}<a class="btn" href="${esc(u.url)}" target="_blank" rel="noopener">What's new</a><small style="color:var(--ink-3)">${esc(how)}</small></div><div class="res" id="updOut"></div>${["mac-app", "win-app"].includes(u.kind) ? opts : ""}`;
  bindOpts();
  const btn = $("#updGo"); if (!btn) return;
  btn.onclick = async () => {
    const out = RES("updOut"); btn.disabled = true; btn.textContent = "Updating...";
    out.className = "res"; out.textContent = "Starting the update.";
    let r; try { r = await post("/api/update/install", {}); } catch (err) { r = {ok: false, error: err.message}; }
    btn.disabled = false; btn.textContent = "Update now";
    if (!r.ok) { out.className = "res bad"; out.textContent = r.error; return; }
    out.className = "res ok"; out.textContent = r.message;
    if (r.action === "downloading" || r.action === "ready") { btn.remove(); updStatus(); }
    else if (r.action === "restart") { btn.textContent = "Restart now"; btn.onclick = restartApp; }
    else if (r.action !== "installer") btn.remove();
  };
}
async function restartApp() {
  const out = RES("updOut"); if (out) { out.className = "res"; out.textContent = "Restarting Meshpool..."; }
  try { await post("/api/update/restart", {}); } catch (e) {}
  const t0 = Date.now();
  const wait = async () => { try { const r = await fetch("/healthz", {cache: "no-store"}); if (r.ok && Date.now() - t0 > 2500) { location.reload(); return; } } catch (e) {} if (Date.now() - t0 < 90000) setTimeout(wait, 1000); else if (out) out.textContent = "Meshpool closed. Open it again to use the new version."; };
  setTimeout(wait, 1500);
}

/* ================================== boot ================================== */
(async () => {
  shell();
  document.addEventListener("click", e => {          // the buttons that appear on hover under a message of yours
    const ma = e.target.closest("[data-ma]"); const host = ma?.closest(".msg-user"); if (!host) return;   // other pages use data-ma too
    const text = $(".b", host)?.innerText.replace(/^Plan mode\n?/, "").trim() || "";
    if (ma.dataset.ma === "copy") navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Could not copy."));
    else { const i = $("#input"); i.value = text; i.focus(); i.dispatchEvent(new Event("input")); toast("Edit it and send to ask again.", 3500); }
  });
  document.addEventListener("click", e => { const b = e.target.closest(".flink, .outs [data-open]"); if (!b || !b.classList.contains("flink")) return;
    openPreview(curRoot(), b.dataset.open); });
  // New items in an open thread ease in; a thread being opened shows at once (no wave of animations).
  new MutationObserver(ms => { if (!S.cur?.ready) return; for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1 && n.id !== "working") n.classList.add("enter"); })
    .observe($("#col"), {childList: true});
  try { await Promise.all([loadSettings(), loadProjects(), loadHarnesses(), loadSkills(), loadPj()]); }
  catch (e) { $("#col").innerHTML = `<div class="hero"><h1>Meshpool could not start</h1><p class="sub">${esc(e.message)}</p></div>`; return; }
  await loadThreads();
  await loadPj();
  loadAccount(); loadCaps(); loadConnectors(); loadStyles().then(() => renderBar());
  newThread();
  if (S.panel) renderPanel();
  if (!S.settings.ready) showOnboarding();
  else {
    loadModels().then(renderBar);
    if (!store("onbDone") && get0("MH_ONBOARDED") !== "1" && !S.threads.length) { if (store("onbStep") === 3 || store("onbStep") === 2) { if (store("onbStep") === 2) { permStep(); } else { const o = Object.assign(document.createElement("div"), {className: "onb", id: "onb"}); document.body.appendChild(o); setupStep(o); } } else if (!store("permsAsked")) permStep(); }
    // 21. a thread waiting for the user's OK opens straight away after a restart
    const waiting = S.threads.find(t => t.state === "waiting" && !t.archived);
    const last = store("last_chat");
    if (waiting) openThread(waiting.id);
    else if (last && S.threads.some(t => t.id === last && !t.archived)) openThread(last);
  }   // set up but onboarding left halfway
  if (MP.desktop) { const ping = () => post("/api/ping").catch(() => {}); ping(); setInterval(ping, 30000); }
  setTimeout(() => checkUpdate(false), 3000); setTimeout(updStatus, 1000);
  window.addEventListener("resize", () => { clearTimeout(S.rzT); S.rzT = setTimeout(fitPanel, 120); });
  window.addEventListener("focus", () => { loadPj().then(renderSide); });
  setupScroll();
  { const h = /^#settings\/([\w-]+)/.exec(location.hash); if (h && S.settings.ready) openSettings(h[1]); }      // a link to a settings page opens it
  setInterval(() => { if (S.threads.some(t => ["working", "waiting"].includes(t.state))) loadThreads(); }, 4000);
})();

/* ---------- pull requests: the repository's pull requests, one in full with its diff, checks, runs and deployments ---------- */
const PRV = {root: "", n: null, timer: null, list: null};
function prVerdictChip(v) {
  const m = {passing: ["ok", "Checks passed"], failing: ["bad", "Checks failing"], running: ["run", "Checks running"], none: ["", "No checks"]}[v] || ["", ""];
  return `<span class="prv-chk ${v}"><span class="cidot ${m[0]}"></span>${m[1]}</span>`;
}
function prStateBadge(p) {
  const [cl, w] = p.draft && p.state === "open" ? ["draft", "Draft"] : ({open: ["open", "Open"], merged: ["merged", "Merged"], closed: ["closed", "Closed"]}[p.state] || ["", p.state]);
  return `<span class="prv-state ${cl}">${w}</span>`;
}
// Pull requests, comments: ISO dates from GitHub. (Named apart from ago(): two functions of one name meant the
// later won, and every chat in the sidebar read "NaN d ago".)
function agoIso(t) { const ms = Date.parse(t); if (!t || isNaN(ms)) return ""; const s = (Date.now() - ms) / 1000; return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; }
function diffHTML(patch) {
  // a GitHub patch: hunks of "@@ -a,b +c,d @@", then lines starting with space, + or -
  let o = 0, n = 0; const rows = [];
  for (const ln of String(patch || "").split("\n")) {
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/.exec(ln);
    if (h) { o = +h[1]; n = +h[2]; rows.push(`<tr class="hk"><td></td><td></td><td>${esc(ln)}</td></tr>`); continue; }
    const c = ln[0];
    if (c === "+") rows.push(`<tr class="ad"><td></td><td>${n++}</td><td>${esc(ln.slice(1)) || " "}</td></tr>`);
    else if (c === "-") rows.push(`<tr class="dl"><td>${o++}</td><td></td><td>${esc(ln.slice(1)) || " "}</td></tr>`);
    else if (c === "\\") rows.push(`<tr class="nt"><td></td><td></td><td>${esc(ln)}</td></tr>`);
    else rows.push(`<tr><td>${o++}</td><td>${n++}</td><td>${esc(ln.slice(1)) || " "}</td></tr>`);
  }
  return `<table class="prv-diff">${rows.join("")}</table>`;
}
async function openPRView(root, n) {
  root = root || curRoot();
  if (!root) { toast("Open a project first."); return; }
  $(".scrim")?.remove();
  PRV.root = root; PRV.n = n || null;
  const sc = document.createElement("div"); sc.className = "scrim prv-scrim";
  sc.innerHTML = `<div class="prv" role="dialog" aria-label="Pull requests"><div class="prv-top"><b>Pull requests</b><span class="stat" id="prvRepo"></span><span class="sp"></span>
      <button class="btn" id="prvNew">New pull request</button><button class="iconbtn" id="prvRef" title="Refresh" aria-label="Refresh">${ICON.refresh}</button><button class="iconbtn" id="prvX" aria-label="Close">${ICON.x}</button></div>
    <div class="prv-body"><aside class="prv-list" id="prvList"><div class="pnote">Loading pull requests...</div></aside><main class="prv-main" id="prvMain"><div class="pnote">Loading...</div></main></div></div>`;
  document.body.appendChild(sc);
  sc.onclick = e => { if (e.target === sc) closePRView(); };
  $("#prvX").onclick = closePRView;
  $("#prvRef").onclick = () => prLoad(true);
  $("#prvNew").onclick = async () => {                   // a pull request for the current branch, opened on GitHub
    const title = await ask({title: "New pull request", text: `Pushes ${PRV.list?.branch || "the current branch"} and opens a pull request for it on GitHub. Title:`, value: PRV.list?.branch || "", ok: "Open"});
    if (!title) return;
    try {
      const r = await post("/api/git", {root: PRV.root, action: "pr", title: typeof title === "string" ? title : "", body: ""});
      if (r.note) { toast(r.note, 6000); window.open(r.pr, "_blank", "noopener"); return; }
      toast("Pull request opened"); PRV.n = null; prLoad(true);
    } catch (e) { toast(e.message, 6000); }
  };
  await prLoad();
}
function closePRView() { clearTimeout(PRV.timer); $(".prv-scrim")?.remove(); }
async function prLoad(fresh) {
  const L = $("#prvList"); if (!L) return;
  try { PRV.list = await api("/api/git/prs?root=" + encodeURIComponent(PRV.root)); }
  catch (e) { L.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; $("#prvMain").innerHTML = prEmpty(e.message); return; }
  const d = PRV.list; $("#prvRepo").textContent = d.slug;
  if (!d.prs.length) { L.innerHTML = `<div class="pnote">No pull requests yet.</div>`; $("#prvMain").innerHTML = prEmpty(""); return; }
  if (!PRV.n || !d.prs.some(p => p.number === PRV.n)) PRV.n = d.current || d.prs[0].number;
  const group = (t, rows) => rows.length ? `<div class="prv-grp">${t}</div>` + rows.map(p => `<button class="prv-item${p.number === PRV.n ? " on" : ""}" data-n="${p.number}">
      <span class="prv-it-top">${prStateBadge(p)}<span class="stat">#${p.number}</span>${p.checks ? `<span class="cidot ${({passing: "ok", failing: "bad", running: "run"})[p.checks] || ""}" title="${esc(p.checks)}"></span>` : ""}</span>
      <span class="prv-it-t">${esc(p.title)}</span><span class="stat">${esc(p.head)} · ${agoIso(p.updated)}</span></button>`).join("") : "";
  L.innerHTML = (d.connected ? "" : `<div class="pnote">Connect GitHub to see private repositories and to merge. <button class="linkbtn" id="prvConn">Connect</button></div>`)
    + group("Open", d.prs.filter(p => p.state === "open")) + group("Recently closed", d.prs.filter(p => p.state !== "open"));
  $("#prvConn")?.addEventListener("click", () => { closePRView(); openSettings("connectors"); });
  $$(".prv-item", L).forEach(b => b.onclick = () => { PRV.n = +b.dataset.n; $$(".prv-item", L).forEach(x => x.classList.toggle("on", x === b)); prDetail(); });
  await prDetail();
}
function prEmpty(msg) { return `<div class="prv-empty"><h3>Pull requests</h3><p>${esc(msg || "Pull requests Meshpool or you open for this repository show here, with what they change and how their checks are doing.")}</p></div>`; }
async function prDetail() {
  clearTimeout(PRV.timer);
  const M = $("#prvMain"); if (!M) return; const n = PRV.n;
  let p; try { p = await api(`/api/git/pr?root=${encodeURIComponent(PRV.root)}&n=${n}`); } catch (e) { M.innerHTML = prEmpty(e.message); return; }
  if (PRV.n !== n || !$("#prvMain")) return;
  const fails = p.checks.filter(c => ckState(c)[0] === "bad");
  const ok = p.checks.filter(c => ckState(c)[0] === "ok").length, run = p.checks.filter(c => ckState(c)[0] === "run").length;
  const why = p.can_merge ? "" : p.state !== "open" ? "" : !p.connected ? "Connect GitHub to merge" : p.draft ? "Mark it ready for review first"
    : p.verdict === "failing" ? "Fix the failing checks first" : p.verdict === "running" ? "Wait for the checks to finish" : p.mergeable === false ? "It has merge conflicts" : "GitHub is still working out if it can merge";
  const tot = Math.max(1, p.additions + p.deletions);
  M.innerHTML = `<header class="prv-head">
      <div class="prv-title">${prStateBadge(p)}<h2>${esc(p.title)} <span class="stat">#${p.number}</span></h2></div>
      <div class="stat">${esc(p.author)} wants to merge <code>${esc(p.head)}</code> into <code>${esc(p.base)}</code> · ${p.commits} commit${p.commits === 1 ? "" : "s"} · updated ${agoIso(p.updated)}</div>
      <div class="prv-acts"><a class="btn" href="${esc(p.url)}" target="_blank" rel="noopener">Open on GitHub</a>
        ${p.preview_url ? `<button class="btn" id="prvPrev" title="${esc(p.preview_url)}">Preview</button>` : ""}
        <button class="btn" id="prvRev">Review with the agent</button>
        ${p.state === "open" && p.draft && p.connected ? `<button class="btn" id="prvReady">Ready for review</button>` : ""}
        ${p.state === "open" ? `<button class="btn primary" id="prvMerge" ${p.can_merge ? "" : `disabled title="${esc(why)}"`}>Squash and merge</button>${why ? `<span class="stat">${esc(why)}</span>` : ""}` : ""}</div>
    </header>
    <section class="prv-sum">
      <div class="prv-card"><div class="k">Changes</div><div class="v"><span class="plus">+${p.additions}</span> <span class="minus">−${p.deletions}</span></div><div class="stat">${p.changed_files} file${p.changed_files === 1 ? "" : "s"}</div>
        <div class="prv-bar"><i class="a" style="width:${100 * p.additions / tot}%"></i><i class="d" style="width:${100 * p.deletions / tot}%"></i></div></div>
      <div class="prv-card"><div class="k">CI checks</div><div class="v">${prVerdictChip(p.verdict)}</div><div class="stat">${ok} passed${fails.length ? `, ${fails.length} failing` : ""}${run ? `, ${run} running` : ""}</div></div>
      <div class="prv-card"><div class="k">Reviews</div><div class="v">${p.reviews.length ? p.reviews.map(r => `<span class="prv-rv ${r.state}">${esc(r.user)}: ${r.state === "approved" ? "approved" : r.state === "changes_requested" ? "changes requested" : "commented"}</span>`).join(" ") : `<span class="stat">None yet</span>`}</div></div>
      <div class="prv-card"><div class="k">Deployments (CD)</div><div class="v">${p.deployments.length ? p.deployments.map(d => `<span class="prv-dep"><span class="cidot ${d.state === "success" || d.state === "active" ? "ok" : d.state === "failure" || d.state === "error" ? "bad" : d.state === "in_progress" || d.state === "pending" || d.state === "queued" ? "run" : ""}"></span>${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.environment)}</a>` : esc(d.environment)}</span>`).join(" ") : `<span class="stat">None for this commit</span>`}</div></div>
    </section>
    <section class="prv-sec"><h4>Checks <span class="stat">${esc(p.sha.slice(0, 7))}</span></h4>${p.checks.length ? `<ul class="prv-ck">${p.checks.map(c => { const [cl, tx] = ckState(c); return `<li class="${cl}"><span class="cidot ${cl}"></span><span class="cn">${esc(c.name)}${c.summary ? ` <span class="stat">${esc(c.summary)}</span>` : ""}</span><span class="cs">${tx}${c.seconds != null ? ` · ${c.seconds < 90 ? c.seconds + " s" : Math.round(c.seconds / 60) + " min"}` : ""}</span>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Details</a>` : ""}</li>`; }).join("")}</ul>` : `<div class="stat">No checks have run on this commit.</div>`}
      ${p.runs.length ? `<h4 style="margin-top:14px">Workflow runs</h4><ul class="prv-ck">${p.runs.map(r => { const [cl, tx] = ckState(r); return `<li class="${cl}"><span class="cidot ${cl}"></span><span class="cn">${esc(r.name)} <span class="stat">${esc(r.event || "")}${r.attempt > 1 ? ` · attempt ${r.attempt}` : ""}</span></span><span class="cs">${tx}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">Logs</a>${r.conclusion === "failure" && p.connected ? `<button class="linkbtn" data-rerun="${r.id}">Rerun failed jobs</button>` : ""}</li>`; }).join("")}</ul>` : ""}
      ${fails.length ? `<div class="row" style="margin-top:10px"><button class="btn" id="prvFix">Ask the agent to fix the failing checks</button></div>` : ""}</section>
    <section class="prv-sec"><h4>Conversation <span class="stat">${p.comments.length}</span></h4>
      ${p.body ? `<div class="prv-cm"><div class="stat"><b>${esc(p.author)}</b> opened it</div><div class="prv-cmb">${esc(p.body).slice(0, 4000)}</div></div>` : ""}
      ${p.comments.map(c => `<div class="prv-cm"><div class="stat"><b>${esc(c.user)}</b> · ${agoIso(c.created)}</div><div class="prv-cmb">${esc(c.body)}</div></div>`).join("")}
      ${p.connected ? `<div class="prv-cmnew"><textarea id="prvCm" rows="3" placeholder="Comment on GitHub"></textarea><button class="btn" id="prvCmGo">Comment</button></div>` : `<div class="stat">Connect GitHub to comment.</div>`}</section>
    <section class="prv-sec"><h4>Files changed <span class="stat">${p.files.length}${p.files.length < p.changed_files ? ` of ${p.changed_files}` : ""}</span><span class="sp"></span><button class="linkbtn" id="prvAll">Expand all</button></h4>
      ${p.files.map((f, i) => { const [cl, w] = CH_BADGE[f.status === "removed" ? "deleted" : f.status === "modified" ? "changed" : f.status] || CH_BADGE.changed;
        const pv = PRV_KIND(f.path) && f.status !== "removed";
        return `<details class="prv-file" data-i="${i}"><summary><span class="badge ${cl}">${w}</span><span class="cn">${esc(f.from ? f.from + " → " + f.path : f.path)}</span>${pv ? `<button class="linkbtn prv-pvb" data-pv="${i}">Preview</button>` : ""}<span class="nw"><span class="plus">+${f.added}</span> <span class="minus">−${f.removed}</span></span></summary><div class="prv-fb"></div></details>`; }).join("")}
    </section>`;
  $$(".prv-file", M).forEach(d => d.addEventListener("toggle", () => {   // a file's diff is drawn when it is opened
    const fb = $(".prv-fb", d); if (!d.open || fb.dataset.done) return; fb.dataset.done = 1;
    const f = p.files[+d.dataset.i];
    fb.innerHTML = f.patch ? diffHTML(f.patch) : `<div class="stat" style="padding:10px">${f.binary ? "A binary file: no text to show." : "Too large to show here. Open it on GitHub."}</div>`;
  }));
  if (p.files.length && p.files.length <= 4) $$(".prv-file", M).forEach(d => d.open = true);
  $$("[data-pv]", M).forEach(b => b.onclick = e => { e.preventDefault(); e.stopPropagation(); prPreview(p, +b.dataset.pv, b.closest(".prv-file")); });
  $("#prvPrev")?.addEventListener("click", () => { store("preview", p.preview_url); closePRView(); togglePanel("preview", true); });
  $("#prvRev").onclick = () => { closePRView(); sendCmd(`Review pull request #${p.number} (${p.url}): ${p.head} into ${p.base}. Read its diff (git fetch, then git diff origin/${p.base}...origin/${p.head}, or the GitHub tools) and the changed files in full. Look for correctness bugs, security problems, missing tests and anything that would break on Mac or Windows. Report findings most severe first, each with file:line, what goes wrong and the fix. Do not change any files.`); };
  $("#prvAll").onclick = () => $$(".prv-file", M).forEach(d => d.open = true);
  $("#prvMerge")?.addEventListener("click", async () => {
    if (!await ask({title: `Merge #${p.number}?`, text: `Squash and merge ${p.head} into ${p.base} on GitHub. Its checks passed.`, ok: "Merge"})) return;
    try { await post("/api/git/pr", {root: PRV.root, action: "merge", pr: p.number}); toast("Pull request merged"); prLoad(true); } catch (e) { toast(e.message, 6000); }
  });
  $("#prvCmGo")?.addEventListener("click", async () => {
    const t = $("#prvCm").value.trim(); if (!t) return;
    try { await post("/api/git/pr", {root: PRV.root, action: "comment", pr: p.number, body: t}); toast("Comment posted on GitHub"); prDetail(); } catch (e) { toast(e.message, 6000); }
  });
  $("#prvReady")?.addEventListener("click", async () => { try { await post("/api/git/pr", {root: PRV.root, action: "ready", pr: p.number}); toast("Marked ready for review"); prDetail(); } catch (e) { toast(e.message, 6000); } });
  $$("[data-rerun]", M).forEach(b => b.onclick = async () => { try { await post("/api/git/pr", {root: PRV.root, action: "rerun", pr: p.number, run: b.dataset.rerun}); toast("Rerunning the failed jobs"); setTimeout(prDetail, 3000); } catch (e) { toast(e.message, 6000); } });
  $("#prvFix")?.addEventListener("click", () => {
    closePRView(); const i = $("#input");
    i.value = `The checks on pull request #${p.number} (${p.url}) are failing: ${fails.map(c => c.name + (c.url ? " (" + c.url + ")" : "")).join(", ")}. Read the logs, find the root cause, fix it, run the tests and push to ${p.head}.`;
    i.dispatchEvent(new Event("input")); i.focus();
  });
  // keep it live while anything is still running
  if (p.verdict === "running" || p.runs.some(r => r.status !== "completed")) PRV.timer = setTimeout(() => { if ($(".prv-scrim")) prDetail(); }, 20000);
}

/* File previews in a pull request: images before and after, Markdown rendered, HTML in a sandbox (no scripts). */
const PRV_KIND = path => /\.(png|jpe?g|gif|webp|svg)$/i.test(path) ? "image" : /\.(md|markdown)$/i.test(path) ? "md" : /\.html?$/i.test(path) ? "html" : "";
async function prFile(p, path, side) {
  const r = await fetch(`/api/git/pr/file?root=${encodeURIComponent(PRV.root)}&n=${p.number}&side=${side}&path=${encodeURIComponent(path)}`, {headers: {"X-Meshpool-Token": MP.token}});
  if (!r.ok) { let m = `Could not load ${side === "head" ? "the new" : "the old"} version.`; try { m = (await r.json()).error || m; } catch (e) {} throw new Error(m); }
  return r;
}
async function prPreview(p, i, det) {
  const f = p.files[i], kind = PRV_KIND(f.path), fb = $(".prv-fb", det);
  det.open = true; fb.dataset.done = 1;
  fb.innerHTML = `<div class="stat" style="padding:10px">Loading the preview...</div>`;
  try {
    if (kind === "image") {
      const sides = [["base", "Before"], ["head", "After"]].filter(([sd]) => !(sd === "base" && f.status === "added"));
      const urls = await Promise.all(sides.map(async ([sd]) => URL.createObjectURL(await (await prFile(p, sd === "base" && f.from ? f.from : f.path, sd)).blob())));
      fb.innerHTML = `<div class="prv-img">${sides.map(([, lb], k) => `<figure><figcaption>${lb}</figcaption><img src="${urls[k]}" alt="${esc(lb + " " + f.path)}"></figure>`).join("")}</div>`;
    } else {
      const text = await (await prFile(p, f.path, "head")).text();
      if (kind === "md") fb.innerHTML = `<div class="prv-md md">${md(text)}</div>`;
      else { fb.innerHTML = `<iframe class="prv-html" sandbox title="${esc(f.path)}"></iframe>`; $(".prv-html", fb).srcdoc = text; }
    }
    fb.insertAdjacentHTML("beforeend", `<div class="prv-pvbar"><button class="linkbtn" data-diff="1">Show the diff</button></div>`);
    $("[data-diff]", fb).onclick = () => { fb.innerHTML = f.patch ? diffHTML(f.patch) : `<div class="stat" style="padding:10px">No text diff.</div>`; };
  } catch (e) { fb.innerHTML = `<div class="stat" style="padding:10px">${esc(e.message)}</div>`; }
}
