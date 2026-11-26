/* In-app files: the file tree and the text and code editor. Nothing here asks the computer to open another program.
   Server side: ui/files_api.py (GET /api/fs/list, search, read, stat; POST /api/fs/save, reveal).
   Loaded after preview.js, which shows the pane (#pvp) this file draws into. Uses the helpers from app.js ($, esc, api,
   post, toast, store, ctxMenu, ask, md, ICON, isMac) and PVX from preview.js. File text is only ever set with textContent
   or escaped, never as markup. */
const EDX = {f: null, base: "", dirty: false, mode: "src", wrap: false, find: null, poll: 0, saving: false, tick: 0};

// ---- small helpers
const edQ = (root, path) => `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`;
const edRevealLabel = () => /Mac/i.test(navigator.platform || "") ? "Show in Finder" : /Win/i.test(navigator.platform || "") ? "Show in File Explorer" : "Show in file manager";
async function edFetch(method, url, body) {         // like api(), but keeps the status so a save conflict can be told apart
  let r;
  try { r = await fetch(url, {method, headers: {"X-Meshpool-Token": MP.token, ...(body ? {"Content-Type": "application/json"} : {})}, body: body ? JSON.stringify(body) : undefined}); }
  catch (e) { throw new Error("meshpool isn't responding, so nothing was saved. Your text is still here: try again, and if it keeps happening, quit and reopen meshpool."); }
  let j = {}; try { j = await r.json(); } catch (e) { /* not JSON */ }
  return {ok: r.ok, status: r.status, json: j};
}
// The one place the app asks the system file manager for anything: a context-menu item a person picked on purpose.
function edReveal(root, path) {
  post("/api/fs/reveal", {root, path}).catch(e => toast(e.message));
}
function edCtx(e, root, path, isDir) {
  e.preventDefault(); e.stopPropagation();
  const items = [
    {label: isDir ? "Open folder" : "Open in Meshpool", icon: isDir ? ICON.folderopen : ICON.files, fn: () => isDir ? openFolderInApp(root, path) : openPreview(root, path)},
    ...(isDir ? [] : [{label: "Download", icon: ICON.dl, fn: () => pvDownload(root, path)}]),
    {label: "Copy path", icon: ICON.copy, fn: () => { try { navigator.clipboard.writeText(root.replace(/[\\/]$/, "") + (path ? "/" + path : "")); toast("Path copied", 2000); } catch (err) { toast("Could not copy the path."); } }},
    {sep: true},
    {label: edRevealLabel(), icon: ICON.ext, fn: () => edReveal(root, path)},
  ];
  ctxMenu(e, items);
}

// ---- the folder tree (lazy, with a search box)
function fsTree(host, root, opts = {}) {
  host.innerHTML = `<div class="fs-search"><input type="search" class="fs-q" aria-label="Search files by name" placeholder="Search files" autocomplete="off" spellcheck="false"></div>
    <ul class="fs-tree" role="tree" aria-label="Files and folders"></ul><ul class="fs-res" role="list" aria-label="Search results" hidden></ul><div class="fs-note" role="status" aria-live="polite" hidden></div>`;
  const tree = $(".fs-tree", host), res = $(".fs-res", host), q = $(".fs-q", host), note = $(".fs-note", host);
  const pick = (p, el) => {
    $$(".fsr.on", host).forEach(x => x.classList.remove("on")); el?.classList.add("on");
    PVX.backNext = {root, rel: p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "", file: p};   // the viewer's back arrow returns to this folder
    openPreview(root, p);
  };
  const row = (e, flat) => {
    const li = document.createElement("li"); li.setAttribute("role", e.dir ? "treeitem" : "none"); if (e.dir) li.setAttribute("aria-expanded", "false");
    const b = document.createElement("button"); b.type = "button"; b.className = "fsr" + (e.dir ? " dir" : ""); b.dataset.path = e.path; b.dataset.dir = e.dir ? "1" : "";
    if (!e.dir) b.setAttribute("role", "treeitem");
    const dirPart = flat && e.path.includes("/") ? e.path.slice(0, e.path.lastIndexOf("/")) : "";
    b.innerHTML = `<span class="fsc">${e.dir ? ICON.chev : ""}</span><span class="fsi">${e.dir ? ICON.dir : ICON.file}</span><span class="fsn"></span>${dirPart ? `<small class="fsd"></small>` : ""}`;
    $(".fsn", b).textContent = e.name; if (dirPart) $(".fsd", b).textContent = dirPart;
    b.title = e.path;
    b.oncontextmenu = ev => edCtx(ev, root, e.path, !!e.dir);
    li.appendChild(b);
    if (e.dir) { const sub = document.createElement("ul"); sub.hidden = true; sub.setAttribute("role", "group"); li.appendChild(sub); b.onclick = () => toggle(li, b, sub, e.path); }
    else b.onclick = () => pick(e.path, b);
    return li;
  };
  const load = async (ul, rel) => {
    ul.innerHTML = `<li class="fs-note-i">Loading...</li>`;
    try {
      const r = await api(`/api/fs/list?${edQ(root, rel)}`);
      ul.innerHTML = "";
      r.entries.forEach(e => ul.appendChild(row(e)));
      if (!r.entries.length) ul.innerHTML = `<li class="fs-note-i">Empty folder</li>`;
      if (r.truncated) ul.insertAdjacentHTML("beforeend", `<li class="fs-note-i">Only the first 2,000 items are shown. Use the search box to find the rest.</li>`);
    } catch (e) { ul.innerHTML = `<li class="fs-note-i"></li>`; ul.firstChild.textContent = e.message; }
  };
  const toggle = async (li, b, sub, rel, force) => {
    const open = force !== undefined ? force : sub.hidden;
    sub.hidden = !open; li.setAttribute("aria-expanded", String(open)); b.classList.toggle("open", open);
    if (open && !sub.dataset.loaded) { sub.dataset.loaded = "1"; await load(sub, rel); }
  };
  host._expandTo = async rel => {                   // open every folder on the way to rel (a folder or a file) and mark it
    const parts = String(rel || "").split("/").filter(Boolean); let acc = "", ul = tree;
    for (let i = 0; i < parts.length; i++) {
      acc += (acc ? "/" : "") + parts[i];
      const b = $$(`:scope > li > .fsr`, ul).find(x => x.dataset.path === acc); if (!b) return;
      if (b.dataset.dir) { const li = b.parentElement, sub = $(":scope > ul", li); await toggle(li, b, sub, acc, true); ul = sub; }
      else { b.classList.add("on"); b.scrollIntoView({block: "nearest"}); }
    }
  };
  host._reload = () => load(tree, "").then(() => opts.open && host._expandTo(opts.open));
  let timer = 0;
  q.oninput = () => {
    clearTimeout(timer);
    const v = q.value.trim();
    if (!v) { res.hidden = true; tree.hidden = false; note.hidden = true; return; }
    timer = setTimeout(async () => {
      try {
        const r = await api(`/api/fs/search?${edQ(root, "")}&q=${encodeURIComponent(v)}${v.length >= 2 ? "&content=1" : ""}`);
        if (q.value.trim() !== v) return;
        res.innerHTML = ""; r.results.forEach(e => res.appendChild(row(e, true)));
        const lines = r.lines || [];
        if (lines.length) {                                // the text search: each line that contains it, opened at that line
          const h = document.createElement("li"); h.className = "fshd"; h.textContent = "Inside files"; res.appendChild(h);
          lines.forEach(e => { const li = row(e, true); const b = $(".fsr", li); b.dataset.line = e.line; $(".fsn", b).textContent = `${e.name}:${e.line}`; const sm = document.createElement("small"); sm.className = "fsd fsl"; sm.textContent = e.text; b.appendChild(sm); b.title = `${e.path}:${e.line}`; res.appendChild(li); });
        }
        tree.hidden = true; res.hidden = false;
        const n = r.results.length, m = lines.length;
        note.hidden = false; note.textContent = n || m ? `${n} file${n === 1 ? "" : "s"} by name${m ? `, ${m}${m >= 200 ? "+" : ""} line${m === 1 ? "" : "s"} inside files` : ""}` + (r.truncated ? ". Type more to narrow it." : "") : "Nothing matches.";
      } catch (e) { note.hidden = false; note.textContent = e.message; }
    }, 180);
  };
  q.onkeydown = e => { if (e.key === "Escape" && q.value) { e.stopPropagation(); q.value = ""; q.oninput(); } };
  host._reload();
  return host;
}

// Folders open in the app's own tree: in the Files panel when this chat has one, else in the viewer pane.
function openFolderInApp(root, rel = "") {
  root = root || (typeof curRoot === "function" ? curRoot() : ""); if (!root) return;
  const here = typeof curRoot === "function" && curRoot() && root === curRoot();
  if (here && !PVX.open && typeof panelSet === "function") {
    S.fsOpen = {root, path: rel};
    togglePanel(panelSet().includes("files") ? "files" : "outputs", true);
    return;
  }
  edFolderPane(root, rel);
}
async function edFolderPane(root, rel, file) {
  if (!await edGuard()) return;
  PVX.root = root; PVX.path = rel || ""; PVX.snippet = null;
  const name = rel ? rel.split("/").pop() : (root.split(/[\\/]/).filter(Boolean).pop() || "Folder");
  pvShell(name, "Folder", null);
  PVX.m = {kind: "folder"};
  $("#pvStage").innerHTML = `<div class="fs-host"></div>`;
  fsTree($(".fs-host"), root, {open: file || rel});
}
// A file or a folder, from anywhere in the app.
function openInApp(root, path, isDir) { return isDir ? openFolderInApp(root, path) : openPreview(root, path); }
// Settings: the skills folder and the data folder, shown in the app's own tree.
async function openHomeFolder(sub) {
  try { const h = await api("/api/fs/home"); edFolderPane(h.home, sub ? h[sub] || sub : ""); } catch (e) { toast(e.message); }
}

// ---- syntax colours (a small hand-written tokenizer: comments, strings, numbers, keywords, types, keys, tags)
const edSet = s => new Set(s.split(" "));
const ED_KW = {
  js: edSet("async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield interface type enum implements public private protected readonly abstract as namespace declare"),
  py: edSet("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self cls"),
  c: edSet("auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class namespace template typename using new delete public private protected virtual override final this try catch throw bool"),
  java: edSet("abstract boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static super switch synchronized this throw throws try void volatile while var record fun val when object"),
  go: edSet("break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var"),
  rs: edSet("as async await break const continue crate dyn else enum extern fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait type unsafe use where while"),
  rb: edSet("alias and begin break case class def defined do else elsif end ensure for if in module next nil not or redo rescue retry return self super then undef unless until when while yield require include"),
  sh: edSet("if then else elif fi for while until do done case esac in function select time export local readonly return exit echo cd set unset source alias"),
  sql: edSet("select from where and or not insert into values update set delete create table alter drop index view join left right inner outer full on as group by order having limit offset union all distinct null is in like between case when then else end primary key foreign references default unique count sum avg min max"),
  php: edSet("abstract and array as break case catch class clone const continue declare default do echo else elseif empty endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global if implements include instanceof interface isset list namespace new or print private protected public require return static switch throw trait try unset use var while yield"),
  yaml: edSet(""), none: edSet(""),
};
const ED_LIT = edSet("true false null undefined None True False nil NULL TRUE FALSE NaN Infinity");
const ED_LANGS = {   // ext -> [label, kind, keywords, line comment, block comment, strings]
  js: ["JavaScript", "code", "js", "//", true, "\"'`"], jsx: ["JavaScript", "code", "js", "//", true, "\"'`"], mjs: ["JavaScript", "code", "js", "//", true, "\"'`"], cjs: ["JavaScript", "code", "js", "//", true, "\"'`"],
  ts: ["TypeScript", "code", "js", "//", true, "\"'`"], tsx: ["TypeScript", "code", "js", "//", true, "\"'`"],
  py: ["Python", "code", "py", "#", false, "\"'", true], pyw: ["Python", "code", "py", "#", false, "\"'", true],
  c: ["C", "code", "c", "//", true, "\"'"], h: ["C", "code", "c", "//", true, "\"'"], cpp: ["C++", "code", "c", "//", true, "\"'"], cc: ["C++", "code", "c", "//", true, "\"'"], hpp: ["C++", "code", "c", "//", true, "\"'"],
  cs: ["C#", "code", "java", "//", true, "\"'"], java: ["Java", "code", "java", "//", true, "\"'"], kt: ["Kotlin", "code", "java", "//", true, "\"'"], swift: ["Swift", "code", "java", "//", true, "\""],
  go: ["Go", "code", "go", "//", true, "\"'`"], rs: ["Rust", "code", "rs", "//", true, "\""], rb: ["Ruby", "code", "rb", "#", false, "\"'"],
  php: ["PHP", "code", "php", "//", true, "\"'"], sh: ["Shell", "code", "sh", "#", false, "\"'"], bash: ["Shell", "code", "sh", "#", false, "\"'"], zsh: ["Shell", "code", "sh", "#", false, "\"'"],
  ps1: ["PowerShell", "code", "sh", "#", false, "\"'"], sql: ["SQL", "code", "sql", "--", true, "\"'"],
  yml: ["YAML", "yaml", "yaml", "#", false, "\"'"], yaml: ["YAML", "yaml", "yaml", "#", false, "\"'"], toml: ["TOML", "yaml", "none", "#", false, "\"'"], ini: ["Config", "yaml", "none", "#", false, "\"'"],
  cfg: ["Config", "yaml", "none", "#", false, "\"'"], env: ["Config", "yaml", "none", "#", false, "\"'"], conf: ["Config", "yaml", "none", "#", false, "\"'"],
  json: ["JSON", "json", "none", "", false, "\""], jsonc: ["JSON", "json", "none", "//", true, "\""], ipynb: ["Notebook", "json", "none", "", false, "\""],
  html: ["HTML", "markup"], htm: ["HTML", "markup"], xml: ["XML", "markup"], svg: ["SVG", "markup"], vue: ["Vue", "markup"],
  css: ["CSS", "css"], scss: ["CSS", "css"], less: ["CSS", "css"],
  md: ["Markdown", "md"], markdown: ["Markdown", "md"],
};
const edLangOf = path => { const n = String(path).split("/").pop().toLowerCase(); const ext = n.includes(".") ? n.split(".").pop() : ""; return ED_LANGS[ext] || (n === "dockerfile" || n === "makefile" ? ["Text", "code", "sh", "#", false, "\"'"] : ["Plain text", "plain"]); };
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&");
const ED_RE = {};
function edTokenRe(lang) {
  const key = lang.join("|"); if (ED_RE[key]) return ED_RE[key];
  const [, kind, kwk, lc, bc, qs, tri] = lang; let parts = [], names = [];
  const add = (n, src) => { names.push(n); parts.push("(" + src + ")"); };
  if (kind === "markup") {
    add("c", "<!--[\\s\\S]*?(?:-->|$)"); add("tag", "<\\/?[A-Za-z!?][^>]*>?");
  } else if (kind === "css") {
    add("c", "\\/\\*[\\s\\S]*?(?:\\*\\/|$)"); add("s", "\"(?:\\\\.|[^\"\\\\\\n])*\"?|'(?:\\\\.|[^'\\\\\\n])*'?"); add("k", "@[\\w-]+"); add("n", "#[0-9a-fA-F]{3,8}\\b|-?\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|s|ms|deg|fr)?\\b"); add("a", "[\\w-]+(?=\\s*:)");
  } else if (kind === "md") {
    add("s", "```[\\s\\S]*?(?:```|$)|`[^`\\n]+`"); add("k", "^#{1,6}[^\\n]*"); add("t", "\\*\\*[^*\\n]+\\*\\*|__[^_\\n]+__"); add("a", "\\[[^\\]\\n]*\\]\\([^)\\n]*\\)"); add("n", "^[ \\t]*(?:[-*+]|\\d+\\.)(?= )"); add("c", "^>[^\\n]*");
  } else if (kind === "plain") {
    return (ED_RE[key] = null);
  } else {
    if (bc) add("c", "\\/\\*[\\s\\S]*?(?:\\*\\/|$)");
    if (lc) add("c", reEsc(lc) + "[^\\n]*");
    const q = [];
    if (tri) q.push("\"\"\"[\\s\\S]*?(?:\"\"\"|$)", "'''[\\s\\S]*?(?:'''|$)");
    for (const ch of qs || "") q.push(ch === "`" ? "`(?:\\\\[\\s\\S]|[^`\\\\])*`?" : `${ch}(?:\\\\.|[^${ch}\\\\\\n])*${ch}?`);
    if (q.length) add("s", q.join("|"));
    if (kind === "yaml") add("a", "^[ \\t-]*[\\w.\\/-]+(?=[ \\t]*[:=])");
    add("n", "\\b(?:0x[\\da-fA-F_]+|\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)\\b");
    add("w", "[A-Za-z_$][\\w$]*");
  }
  const re = new RegExp(parts.join("|"), "gm"); re.names = names;
  return (ED_RE[key] = re);
}
function edTokens(text, lang) {                      // [[class, text], ...] covering the whole text
  const re = edTokenRe(lang), out = [];
  if (!re) return [["", text]];
  const [, kind, kwk] = lang, kw = ED_KW[kwk] || ED_KW.none, ci = kwk === "sql";
  let last = 0, m; re.lastIndex = 0;
  while ((m = re.exec(text))) {
    if (!m[0]) { re.lastIndex++; continue; }
    if (m.index > last) out.push(["", text.slice(last, m.index)]);
    let cls = re.names[m.slice(1).findIndex(x => x !== undefined)], s = m[0];
    if (cls === "w") cls = kw.has(ci ? s.toLowerCase() : s) ? "k" : ED_LIT.has(s) ? "l" : /^[A-Z][A-Za-z0-9]/.test(s) && kind === "code" ? "t" : "";
    else if (cls === "s" && kind === "json" && /^\s*:/.test(text.slice(m.index + s.length, m.index + s.length + 12))) cls = "a";
    else if (cls === "tag") { edTag(s, out); last = m.index + s.length; continue; }
    out.push([cls, s]); last = m.index + s.length;
  }
  if (last < text.length) out.push(["", text.slice(last)]);
  return out;
}
function edTag(s, out) {                              // <name attr="value"> split into the name, attribute names and values
  const m = /^(<\/?[\w:!?-]*)([\s\S]*)$/.exec(s); out.push(["t", m[1]]);
  const rest = m[2], re = /("[^"]*"?|'[^']*'?)|([\w:@.-]+)(?==)/g; let last = 0, x;
  while ((x = re.exec(rest))) { if (x.index > last) out.push(["", rest.slice(last, x.index)]); out.push([x[1] ? "s" : "a", x[0]]); last = x.index + x[0].length; }
  if (last < rest.length) out.push(["", rest.slice(last)]);
}
const edLines = (text, lang) => {                      // the highlighted text as one block per line (so line numbers can sit beside wrapped lines)
  const lines = [[]]; let cur = lines[0];
  for (const [cls, s] of edTokens(text, lang)) {
    const parts = s.split("\n");
    parts.forEach((p, i) => { if (i) { cur = []; lines.push(cur); } if (p) cur.push([cls, p]); });
  }
  return lines.map(l => `<span class="ln">${l.length ? l.map(([c, s]) => c ? `<span class="tk-${c}">${esc(s)}</span>` : esc(s)).join("") : "​"}</span>`).join("");
};

// ---- the editor
const EDX_BIG = 400000;
async function edGuard() {                            // true when it is fine to leave this file
  if (!EDX.dirty) return true;
  const ok = await ask({title: "Leave without saving?", text: `${EDX.f?.name || "This file"} has changes that are not saved.`, ok: "Leave without saving", cancel: "Keep editing", danger: true});
  if (ok) { EDX.dirty = false; edStop(); }
  return !!ok;
}
function edStop() { clearInterval(EDX.poll); EDX.poll = 0; EDX.f = null; EDX.find = null; EDX.dirty = false; EDX.saving = false; }
window.addEventListener("beforeunload", e => { if (EDX.dirty) { e.preventDefault(); e.returnValue = ""; } });

async function edLoad(root, path, id, mode) {
  let f;
  try { f = await api(`/api/fs/read?${edQ(root, path)}`); } catch (e) { if (id === PVX.id) pvNote(e.message); return; }
  if (id !== PVX.id) return;
  if (f.kind !== "text") { pvNote(f.note || "Meshpool cannot show this kind of file yet."); return; }
  edStop();
  const lang = edLangOf(path);
  EDX.f = {root, path, name: f.name, sha: f.sha, mtime_ns: f.mtime_ns, size: f.size, readonly: !!f.readonly, note: f.note || "", lang, ext: f.ext};
  EDX.base = f.text; EDX.dirty = false; EDX.find = null;
  EDX.mode = mode || ((lang[1] === "md" || lang[1] === "json") && !f.readonly ? "view" : (lang[1] === "md" || lang[1] === "json") ? "view" : "src");
  EDX.wrap = !!store("ed_wrap");
  PVX.m = {kind: "editor"};
  $("#pvType").textContent = lang[0];
  const seg = $("#pvSeg");
  const two = lang[1] === "md" || lang[1] === "json";
  seg.hidden = !two;
  if (two) { const [a, b] = $$("button", seg); a.textContent = lang[1] === "md" ? "Preview" : "Formatted"; b.textContent = lang[1] === "md" ? "Source" : "Source"; }
  const alt = $("#pvAlt"); const prev = ["html", "htm", "svg", "csv", "tsv"].includes(f.ext);
  alt.hidden = !prev; alt.textContent = "Preview"; alt.onclick = async () => { if (await edGuard()) openPreview(root, path); };
  edDraw(f.text);
  EDX.poll = setInterval(edCheckDisk, 4000);
}
function edDraw(text) {
  const st = $("#pvStage"), lang = EDX.f.lang;
  st.innerHTML = `<div class="ed">
    <div class="ed-bar"><button class="btn primary" id="edSave" type="button" aria-label="Save the file">Save</button><span class="ed-st" id="edSt" role="status" aria-live="polite"></span><span class="sp"></span>
      <button class="btn" id="edFindBtn" type="button" aria-label="Find in this file" title="Find (${MOD}F)">Find</button>
      <button class="btn" id="edWrap" type="button" aria-pressed="false" aria-label="Wrap long lines" title="Wrap long lines">Wrap</button></div>
    <div class="ed-find" id="edFind" hidden role="search"><input id="edFq" type="search" aria-label="Find in this file" placeholder="Find" autocomplete="off" spellcheck="false"><span class="ed-fc" id="edFc" aria-live="polite"></span>
      <button class="iconbtn" id="edFp" type="button" aria-label="Previous match" title="Previous (Shift+Enter)">${ICON.chev}</button><button class="iconbtn" id="edFn" type="button" aria-label="Next match" title="Next (Enter)">${ICON.chev}</button>
      <button class="btn" id="edFcase" type="button" aria-pressed="false" aria-label="Match case">Aa</button><button class="iconbtn" id="edFx" type="button" aria-label="Close find" title="Close (Esc)">${ICON.x}</button></div>
    <div class="ed-warn" id="edWarn" role="alert" hidden></div>
    <div class="ed-view" id="edView" hidden></div>
    <div class="ed-box" id="edBox"><div class="ed-layer" id="edLayer"><pre class="ed-pre ed-hl" id="edHl" aria-hidden="true"></pre><pre class="ed-pre ed-fm" id="edFm" aria-hidden="true"></pre></div>
      <textarea class="ed-pre ed-ta" id="edTa" aria-label="${esc(EDX.f.name)}" spellcheck="false" autocapitalize="off" autocomplete="off" wrap="off"></textarea></div></div>`;
  const ta = $("#edTa"); ta.value = text; ta.readOnly = EDX.f.readonly;
  edWrapSet(EDX.wrap);
  $("#edWrap").onclick = () => { EDX.wrap = !EDX.wrap; store("ed_wrap", EDX.wrap); edWrapSet(EDX.wrap); };
  $("#edSave").onclick = () => edSave();
  ta.addEventListener("input", () => { EDX.dirty = ta.value !== EDX.base; edStatus(); edPaint(); if (EDX.find) edFindRun(false); });
  ta.addEventListener("scroll", edSync);
  ta.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.stopPropagation(); ta.blur(); return; }
    if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !ta.readOnly) { e.preventDefault(); document.execCommand("insertText", false, "  "); }
  });
  new ResizeObserver(() => { edWidth(); edSync(); }).observe($("#edBox"));
  $("#edFindBtn").onclick = () => edFindOpen();
  $("#edFq").oninput = () => edFindRun(true);
  $("#edFq").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); edFindStep(e.shiftKey ? -1 : 1); } else if (e.key === "Escape") { e.stopPropagation(); edFindClose(); } };
  $("#edFn").onclick = () => edFindStep(1); $("#edFp").onclick = () => edFindStep(-1);
  $("#edFcase").onclick = e => { const b = e.currentTarget; b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); edFindRun(true); };
  $("#edFx").onclick = () => edFindClose();
  edMode(EDX.mode, true);
  edStatus();
}
function edMode(m, first) {
  EDX.mode = m;
  const two = EDX.f.lang[1] === "md" || EDX.f.lang[1] === "json";
  $$("#pvSeg button").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.v === "code") === (m === "src"))));
  const view = $("#edView"), box = $("#edBox"), ta = $("#edTa");
  const showView = two && m === "view";
  view.hidden = !showView; box.hidden = showView; $("#edFindBtn").hidden = showView; $("#edWrap").hidden = showView; $("#edFind").hidden = showView || !EDX.find;
  if (showView) {
    view.innerHTML = "";
    if (EDX.f.lang[1] === "md") { const d = document.createElement("div"); d.className = "pv-md md"; d.innerHTML = md(ta.value); view.appendChild(d); }
    else edJson(view, ta.value);
  } else { edPaint(); edWidth(); edSync(); if (!first) ta.focus({preventScroll: true}); }
}
function edWrapSet(on) {
  const ta = $("#edTa"), hl = $("#edHl"), fm = $("#edFm"), b = $("#edWrap"); if (!ta) return;
  for (const el of [ta, hl, fm]) el.classList.toggle("wrap", on);
  ta.wrap = on ? "soft" : "off"; b.setAttribute("aria-pressed", String(on)); b.classList.toggle("on", on);
  edWidth(); edSync();
}
function edWidth() {                                   // the colour layer must wrap at exactly the width the textarea does
  const ta = $("#edTa"), layer = $("#edLayer"); if (!ta || !layer) return;
  if (EDX.wrap) { layer.style.width = ta.clientWidth + "px"; layer.style.minWidth = ""; } else { layer.style.width = "max-content"; layer.style.minWidth = ta.clientWidth + "px"; }
}
function edSync() { const ta = $("#edTa"), layer = $("#edLayer"); if (ta && layer) layer.style.transform = `translate(${-ta.scrollLeft}px,${-ta.scrollTop}px)`; }
let edRaf = 0;
function edPaint() {
  cancelAnimationFrame(edRaf);
  edRaf = requestAnimationFrame(() => {
    const ta = $("#edTa"), hl = $("#edHl"); if (!ta || !hl || !EDX.f) return;
    const text = ta.value, big = text.length > EDX_BIG;
    hl.innerHTML = edLines(text, big ? ["Plain text", "plain"] : EDX.f.lang);
    edWidth(); edSync();
  });
}
function edStatus() {
  const s = $("#edSt"), sv = $("#edSave"); if (!s || !EDX.f) return;
  const f = EDX.f;
  sv.disabled = f.readonly || !EDX.dirty || EDX.saving;
  s.textContent = f.readonly ? (f.note || "Read only") : EDX.saving ? "Saving..." : EDX.dirty ? "Unsaved changes" : (EDX.savedAt ? "Saved" : "");
  s.className = "ed-st" + (EDX.dirty ? " dirty" : "");
  const nm = $("#pvName"); if (nm) nm.textContent = (EDX.dirty ? "● " : "") + f.name;
}
function edWarn(html, actions) {
  const w = $("#edWarn"); if (!w) return;
  if (!html) { w.hidden = true; w.innerHTML = ""; return; }
  w.hidden = false; w.innerHTML = `<span class="ed-wt"></span><span class="sp"></span>`; $(".ed-wt", w).textContent = html;
  actions.forEach(([label, fn, cls]) => { const b = document.createElement("button"); b.type = "button"; b.className = "btn " + (cls || ""); b.textContent = label; b.onclick = fn; w.appendChild(b); });
}
async function edSave(force) {
  const f = EDX.f, ta = $("#edTa"); if (!f || f.readonly || EDX.saving || !ta) return;
  if (!EDX.dirty && !force) return;
  EDX.saving = true; edStatus();
  const text = ta.value;
  try {
    const r = await edFetch("POST", "/api/fs/save", {root: f.root, path: f.path, text, base_sha: f.sha, overwrite: !!force});
    if (r.status === 409 && r.json.conflict) { EDX.saving = false; edStatus(); edConflict(r.json.error); return; }
    if (!r.ok) throw new Error(r.json.error || `Could not save this file: meshpool gave no reason (status ${r.status}). Your text is still here; try again.`);
    f.sha = r.json.sha; f.mtime_ns = r.json.mtime_ns; EDX.base = text; EDX.dirty = ta.value !== text; EDX.savedAt = Date.now();
    edWarn(""); toast("File saved", 1800);
  } catch (e) { toast(e.message, 6000); }
  EDX.saving = false; edStatus();
}
function edConflict(msg) {
  edWarn(msg || "This file changed on disk since you opened it. Nothing was saved.", [
    ["Reload from disk", () => edReload(), "primary"],
    ["Save mine anyway", () => edSave(true), "danger"],
  ]);
  $("#edWarn .btn")?.focus();
}
async function edReload() {
  const f = EDX.f; if (!f) return;
  try {
    const n = await api(`/api/fs/read?${edQ(f.root, f.path)}`);
    f.sha = n.sha; f.mtime_ns = n.mtime_ns; f.readonly = !!n.readonly; EDX.base = n.text; EDX.dirty = false;
    const ta = $("#edTa"); if (ta) { const t = ta.scrollTop; ta.value = n.text; ta.scrollTop = t; }
    edWarn(""); edPaint(); if (EDX.mode === "view") edMode("view"); if (EDX.find) edFindRun(false); edStatus();
  } catch (e) { toast(e.message); }
}
async function edCheckDisk() {                        // the agent or another tool may change the file while it is open
  const f = EDX.f; if (!f || EDX.saving || document.hidden) return;
  let s; try { s = await api(`/api/fs/stat?${edQ(f.root, f.path)}`); } catch (e) { return; }
  if (EDX.f !== f) return;
  if (s.gone) { edWarn("This file is gone from the folder.", []); return; }
  if (s.sha && s.sha !== f.sha) {
    if (!EDX.dirty) { await edReload(); toast("The file changed on disk, so Meshpool reloaded it.", 3000); }
    else if ($("#edWarn")?.hidden) edConflict("This file changed on disk while you were editing it.");
  }
}

// ---- find in file
function edFindOpen() {
  if (!$("#edFind")) return;
  EDX.find = EDX.find || {i: -1, n: 0, ranges: []};
  $("#edFind").hidden = false;
  const ta = $("#edTa"), sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
  const q = $("#edFq"); if (sel && !sel.includes("\n")) q.value = sel;
  q.focus(); q.select(); edFindRun(true);
}
function edFindClose() { EDX.find = null; const b = $("#edFind"); if (b) b.hidden = true; const fm = $("#edFm"); if (fm) fm.innerHTML = ""; $("#edTa")?.focus({preventScroll: true}); }
function edFindRun(jump) {
  const f = EDX.find, ta = $("#edTa"), fm = $("#edFm"); if (!f || !ta) return;
  const q = $("#edFq").value, cs = $("#edFcase").getAttribute("aria-pressed") === "true", text = ta.value;
  f.ranges = [];
  if (q) {
    const hay = cs ? text : text.toLowerCase(), needle = cs ? q : q.toLowerCase(); let i = -1;
    while ((i = hay.indexOf(needle, i + 1)) !== -1 && f.ranges.length < 5000) f.ranges.push(i);
  }
  f.n = f.ranges.length; f.len = q.length;
  if (jump || f.i >= f.n) f.i = f.n ? Math.max(0, f.ranges.findIndex(x => x >= ta.selectionStart)) : -1;
  if (f.n && f.i < 0) f.i = 0;
  edFindPaint(jump);
}
function edFindPaint(scroll) {
  const f = EDX.find, ta = $("#edTa"), fm = $("#edFm"); if (!f) return;
  let html = "", at = 0;
  f.ranges.forEach((s, k) => { html += esc(ta.value.slice(at, s)) + `<mark${k === f.i ? ' class="cur"' : ""}>${esc(ta.value.slice(s, s + f.len))}</mark>`; at = s + f.len; });
  fm.innerHTML = html + esc(ta.value.slice(at)) + "\n";
  $("#edFc").textContent = $("#edFq").value ? (f.n ? `${f.i + 1} of ${f.n}` : "No matches") : "";
  if (scroll && f.i >= 0) {
    const m = $("mark.cur", fm); edWidth();
    if (m) { const top = m.offsetTop, h = ta.clientHeight; if (top < ta.scrollTop + 20 || top > ta.scrollTop + h - 40) ta.scrollTop = Math.max(0, top - h / 3);
      const left = m.offsetLeft; if (!EDX.wrap && (left < ta.scrollLeft || left > ta.scrollLeft + ta.clientWidth - 80)) ta.scrollLeft = Math.max(0, left - 80); }
    edSync();
  }
}
function edFindStep(d) {
  const f = EDX.find; if (!f || !f.n) return;
  f.i = (f.i + d + f.n) % f.n; edFindPaint(true);
}

// ---- JSON, formatted: folds objects and lists (built with textContent)
function edJson(host, text) {
  let v; try { v = JSON.parse(text); } catch (e) { host.innerHTML = `<div class="pv-note"><span></span></div>`; $("span", host).textContent = "This is not valid JSON yet: " + e.message + " Switch to Source to fix it."; return; }
  const root = document.createElement("div"); root.className = "ed-json"; let count = 0;
  const leaf = (x) => { const s = document.createElement("span"); s.className = "tk-" + (typeof x === "string" ? "s" : x === null || typeof x === "boolean" ? "l" : "n"); s.textContent = typeof x === "string" ? JSON.stringify(x) : String(x); return s; };
  const node = (k, x, open) => {
    const row = document.createElement(x && typeof x === "object" ? "details" : "div"); row.className = "jn";
    const key = () => { const s = document.createElement("span"); s.className = "tk-a"; s.textContent = JSON.stringify(String(k)); return s; };
    if (x && typeof x === "object" && ++count < 4000) {
      const keys = Array.isArray(x) ? x.map((_, i) => i) : Object.keys(x);
      row.open = !!open; const sm = document.createElement("summary");
      if (k !== null) { sm.appendChild(key()); sm.append(": "); }
      sm.append(Array.isArray(x) ? `[ ${keys.length} ]` : `{ ${keys.length} }`); row.appendChild(sm);
      let built = false;
      const fill = () => { if (built) return; built = true; keys.forEach(kk => row.appendChild(node(kk, x[kk], false))); };
      if (open) fill(); else row.addEventListener("toggle", fill);
    } else { if (k !== null) { row.appendChild(key()); row.append(": "); } row.appendChild(x && typeof x === "object" ? document.createTextNode("...") : leaf(x)); }
    return row;
  };
  root.appendChild(node(null, v, true)); host.appendChild(root);
}

// ---- keys: Save and Find work anywhere in the viewer pane
document.addEventListener("keydown", e => {
  if (!EDX.f || !PVX.open || !(isMac ? e.metaKey : e.ctrlKey) || e.altKey) return;
  if (!e.target.closest?.("#pvp")) return;
  const k = e.key.toLowerCase();
  if (k === "s") { e.preventDefault(); edSave(); }
  else if (k === "f" && EDX.mode === "src") { e.preventDefault(); edFindOpen(); }
}, true);
