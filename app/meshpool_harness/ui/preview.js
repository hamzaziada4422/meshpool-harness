/* In-app previews: the viewer pane beside the chat, the file cards under a reply, and the Preview button on code blocks.
   Server side: ui/previews.py (GET /api/preview). Loaded before app.js (its helpers are only called later); uses app.js helpers ($, esc, md, api, post, toast, ICON).
   Page images and tables come from the server; HTML runs in a sandboxed frame (scripts and forms yes, same origin no);
   SVG is only ever an <img> from a blob, never markup in the page. */
const PVX = {open: false, id: 0, root: "", path: "", m: null, page: 0, code: false, sheet: 0, urls: [], opener: null, snippet: null, poll: 0, back: null, backNext: null};

const PV_GROUPS = [
  [/^(pptx|ppt|pptm|ppsx|pps|potx|odp|key)$/, "Deck", "PowerPoint"],
  [/^(docx|doc|docm|dotx|odt|rtf|pages)$/, "Document", "Word"],
  [/^(xlsx|xlsm|xls|xlsb|ods|numbers|csv|tsv)$/, "Spreadsheet", "Excel"],
  [/^pdf$/, "PDF", null],
  [/^html?$/, "Web page", "browser"],
  [/^(md|markdown)$/, "Markdown", "editor"],
  [/^(png|jpe?g|gif|webp|svg|bmp)$/, "Image", null],
];
const pvExt = p => (String(p).match(/\.([^./\\]+)$/) || [, ""])[1].toLowerCase();
// The icon of a kind of file (a spreadsheet looks like a sheet, a deck like a screen), not one page for everything.
function pvIcon(ext) {
  return /^html?$/.test(ext) ? ICON.globe : PV_GROUPS[2][0].test(ext) ? ICON.fsheet : PV_GROUPS[1][0].test(ext) ? ICON.fdoc
    : PV_GROUPS[0][0].test(ext) ? ICON.fdeck : PV_GROUPS[6][0].test(ext) ? ICON.fimg : ICON.files;
}
// A long name is kept in two lines and ends in an ellipsis, with the end of the name and its extension still showing:
// "Quarterly bouquet price list for the sp…List.xlsx".
function pvShortName(nm, max = 40) {
  if (nm.length <= max) return nm;
  const m = nm.match(/^(.*?)(\.[^.]{1,5})?$/), stem = m[1], ext = m[2] || "";
  const tail = Math.min(8, Math.max(0, stem.length - 4)), head = max - ext.length - tail - 1;
  return stem.slice(0, Math.max(8, head)).trimEnd() + "\u2026" + stem.slice(stem.length - tail) + ext;
}
function pvType(path) {
  const ext = pvExt(path), g = PV_GROUPS.find(x => x[0].test(ext));
  return {label: g ? g[1] : (ext ? ext.toUpperCase() : "File"), ext};
}
const pvQuery = (root, path) => `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`;
const pvPage = (root, path, n, w, v) => `/api/preview?${pvQuery(root, path)}&page=${n}&w=${w}&v=${v || 0}&t=${encodeURIComponent(MP.token)}`;

function pvEnsure() {
  let el = $("#pvp"); if (el) return el;
  el = document.createElement("aside"); el.id = "pvp"; el.className = "pvp"; el.hidden = true; el.tabIndex = -1;
  el.setAttribute("aria-label", "Preview");
  el.innerHTML = `<div class="pv-bar"><button class="iconbtn pv-back" id="pvBack" aria-label="Back to the folder" title="Back to the folder" hidden>${ICON.chev}</button><span class="pv-ic">${ICON.files}</span><b class="pv-name" id="pvName"></b><span class="pv-type" id="pvType"></span><span class="sp"></span>
    <span class="pv-seg" id="pvSeg" role="group" aria-label="View" hidden><button data-v="view" aria-pressed="true">Preview</button><button data-v="code" aria-pressed="false">Code</button></span>
    <button class="btn" id="pvAlt" hidden></button><button class="iconbtn" id="pvDl" aria-label="Download this file" title="Download" hidden>${ICON.dl}</button><button class="iconbtn" id="pvMore" aria-label="More actions for this file" title="More actions">${ICON.more}</button><button class="iconbtn" id="pvClose" aria-label="Close preview" title="Close (Esc)">${ICON.x}</button></div>
    <div class="pv-tabs" id="pvTabs" role="tablist" hidden></div>
    <div class="pv-stage" id="pvStage"></div>
    <div class="pv-strip" id="pvStrip" hidden><button class="iconbtn" id="pvPrev" aria-label="Previous page">${ICON.chev}</button><div class="pv-thumbs" id="pvThumbs"></div><button class="iconbtn" id="pvNext" aria-label="Next page">${ICON.chev}</button><span class="pv-count" id="pvCount" aria-live="polite"></span></div>`;
  $("#app").appendChild(el);
  $("#pvClose").onclick = () => closePreview();
  $("#pvDl").onclick = () => { if (PVX.root && PVX.path) pvDownload(PVX.root, PVX.path); };
  $("#pvPrev").onclick = () => pvGo(PVX.page - 1);
  $("#pvNext").onclick = () => pvGo(PVX.page + 1);
  $$("#pvSeg button").forEach(b => b.onclick = () => { if (PVX.m?.kind === "editor") return edMode(b.dataset.v === "code" ? "src" : "view"); PVX.code = b.dataset.v === "code"; pvBody(); });
  $("#pvBack").onclick = async () => { const b = PVX.back; if (b && await edGuard()) edFolderPane(b.root, b.rel, b.file); };
  // The only route to the system file manager: a menu item a person picks on purpose, never a click on the file itself.
  $("#pvMore").onclick = e => {
    if (!PVX.root || (PVX.m?.kind !== "folder" && !PVX.path)) return;
    const root = PVX.root, path = PVX.path;
    ctxMenu(e, [...(PVX.m?.kind !== "folder" && path ? [{label: "Download", icon: ICON.dl, fn: () => pvDownload(root, path)}] : []),
      {label: "Copy path", icon: ICON.copy, fn: () => { try { navigator.clipboard.writeText(root.replace(/[\\/]$/, "") + (path ? "/" + path : "")); toast("Path copied", 2000); } catch (err) { toast("Could not copy the path."); } }},
      {sep: true}, {label: edRevealLabel(), icon: ICON.ext, fn: () => edReveal(root, path)}]);
  };
  el.addEventListener("keydown", e => {
    if (PVX.m?.kind !== "pages" || e.altKey || e.ctrlKey || e.metaKey || e.target.closest?.("input, textarea, select")) return;
    const k = e.key;
    if (k === "ArrowLeft" || k === "PageUp") { e.preventDefault(); pvGo(PVX.page - 1); }
    else if (k === "ArrowRight" || k === "PageDown") { e.preventDefault(); pvGo(PVX.page + 1); }
    else if (k === "Home") { e.preventDefault(); pvGo(0); } else if (k === "End") { e.preventDefault(); pvGo(1e9); }
  });
  return el;
}
// Download: the browser's own download of the file, through the app's checked file route (/raw with dl=1, which sends
// it as an attachment). Nothing else is opened or started.
function pvDownload(root, path) {
  const a = document.createElement("a");
  a.href = `/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(root, path)}&dl=1`; a.download = String(path).split("/").pop();
  a.rel = "noopener"; a.hidden = true; document.body.appendChild(a); a.click(); a.remove();
  if (typeof toast === "function") toast(`Downloaded ${a.download}`, 3000);   // the browser saves it quietly: say so
}
function pvFree() { PVX.urls.forEach(u => { try { URL.revokeObjectURL(u); } catch (e) {} }); PVX.urls = []; }
function pvShell(name, type, altLabel) {
  const el = pvEnsure(), app = $("#app");
  if (typeof edStop === "function") edStop();
  PVX.opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : PVX.opener;
  pvFree(); PVX.open = true; PVX.id++; PVX.m = null; PVX.page = 0; PVX.code = false; PVX.sheet = 0;
  PVX.back = PVX.backNext; PVX.backNext = null; $("#pvBack").hidden = !PVX.back;
  const sb = $$("#pvSeg button"); sb[0].textContent = "Preview"; sb[1].textContent = "Code";
  // the chat glides to its new place, as it does for the side panel (a measured one-frame snap of 300 to 460 px before)
  const show = () => { el.hidden = false; app.classList.add("pvopen"); if (typeof fitPanel === "function") fitPanel(); };
  if (el.hidden && typeof flipLayout === "function") flipLayout(show);
  else { const keep = readerAnchor(); show(); keep(); }     // the reader keeps their place when the panel takes room
  $("#pvName").textContent = name; $("#pvName").title = PVX.path || name; $("#pvType").textContent = type;
  $("#pvDl").hidden = !PVX.root || !PVX.path || type === "Folder";
  const ob = $("#pvAlt"); ob.hidden = !altLabel; ob.textContent = altLabel || "";
  $("#pvSeg").hidden = true; $("#pvTabs").hidden = true; $("#pvStrip").hidden = true;
  if (typeof renderTop === "function") renderTop();
  el.focus({preventScroll: true});
  return PVX.id;
}
function pvNote(text, busy) {
  $("#pvStage").innerHTML = `<div class="pv-note${busy ? " busy" : ""}">${busy ? `<span class="pv-spin" aria-hidden="true"></span>` : ""}<span></span></div>`;
  $("#pvStage .pv-note span:last-child").textContent = text;
}
function closePreview(quiet) {
  if (!PVX.open) return;
  if (typeof EDX !== "undefined" && EDX.dirty) { edGuard().then(ok => ok && closePreview(quiet)); return; }
  if (typeof edStop === "function") edStop();
  PVX.open = false; PVX.id++; pvFree();
  const hide = () => {
    if (PVX.open) return;             // a preview opened again before this close was drawn: it stays
    const el = $("#pvp"); if (el) { el.hidden = true; $("#pvStage").innerHTML = ""; $("#pvThumbs").innerHTML = ""; }
    $("#app")?.classList.remove("pvopen");
    if (typeof fitPanel === "function") fitPanel();
  };
  if (typeof flipLayout === "function") flipLayout(hide);       // a glide back, not a snap
  else { const keep = readerAnchor(); hide(); keep(); }
  if (typeof renderTop === "function") renderTop();
  if (!quiet && PVX.opener?.isConnected) try { PVX.opener.focus({preventScroll: true}); } catch (e) {}
  PVX.opener = null;
}

// A file: ask the server what it is, then draw it.
async function openPreview(root, path) {
  root = root || (typeof curRoot === "function" ? curRoot() : "");
  if (!root || !path) return;
  if (typeof edGuard === "function" && !await edGuard()) return;
  PVX.root = root; PVX.path = path; PVX.snippet = null;
  const t = pvType(path), name = path.split("/").pop();
  const id = pvShell(name, t.label, null);
  pvNote("Loading the preview...", true);
  let tries = 0;
  const load = async () => {
    let m;
    try { m = await api(`/api/preview?${pvQuery(root, path)}`); }
    catch (e) { if (id === PVX.id) pvNote(e.message); return; }
    if (id !== PVX.id) return;
    if (m.state === "working") {
      if (++tries > 150) { pvNote("Preparing the preview is taking too long. Close it and try again in a moment."); return; }
      pvNote(tries > 3 ? "Preparing the pages. The first time takes a few seconds..." : "Loading the preview...", true);
      PVX.poll = setTimeout(load, 1000); return;
    }
    if (m.kind === "text" || m.kind === "md") return edLoad(root, path, id);      // text and code open in the editor
    PVX.m = m; PVX.page = 0; pvBody();
  };
  load();
}
// A file a turn changed while its preview is open: its pages are read again, on the same page (a deck that grew from
// 6 to 7 slides kept "2 / 6" until it was reopened: Daniel, 0.0.19). app.js calls this with the turn's changed files.
async function pvRefresh(root, paths) {
  if (!PVX.open || PVX.snippet || !PVX.path || PVX.root !== root || !(paths || []).includes(PVX.path)) return;
  if (!PVX.m) return;
  const sheet = PVX.m.kind === "sheet" || !!PVX.m.sheets;
  if (PVX.m.kind !== "pages" && !sheet && !["html", "md"].includes(PVX.m.kind)) return;
  const id = PVX.id, keep = PVX.page || 0, was = PVX.m.kind;
  const sc = $(".pv-sheet"), top = sc ? sc.scrollTop : 0, left = sc ? sc.scrollLeft : 0, tab = PVX.sheet;
  let m; try { m = await api(`/api/preview?${pvQuery(root, PVX.path)}&fresh=${Date.now()}`); } catch (e) { return; }
  if (id !== PVX.id) return;
  if (m.state === "working") { clearTimeout(PVX.poll); PVX.poll = setTimeout(() => pvRefresh(root, paths), 1000); return; }
  if (m.kind === "pages" && was === "pages") { PVX.m = m; pvPages(m); pvGo(keep); return; }
  if (sheet && (m.kind === "sheet" || m.sheets)) {              // a spreadsheet: the same sheet tab and the same place on it (the sort and filter stay in PVX.view)
    PVX.m = m; PVX.sheet = Math.min(tab, (m.sheets || []).length - 1); pvBody();
    const w = $(".pv-sheet"); if (w) { w.scrollTop = top; w.scrollLeft = left; }
    return;
  }
  if (m.kind === was) { PVX.m = m; pvBody(); }                  // a page or a note: drawn again
}
// Code from a reply (no file): html or svg, shown the same way.
async function openPreviewCode(lang, code) {
  if (typeof edGuard === "function" && !await edGuard()) return;
  PVX.root = ""; PVX.path = ""; PVX.snippet = code;
  pvShell(lang === "svg" ? "SVG preview" : "HTML preview", lang.toUpperCase(), null);
  PVX.m = {kind: lang === "svg" ? "image" : "html", ext: lang, name: "snippet", text: code, state: "ready", snippet: true};
  pvBody();
}

function pvBody() {
  const m = PVX.m, st = $("#pvStage"), id = PVX.id;
  if (!m) return;
  $("#pvTabs").hidden = true; $("#pvStrip").hidden = true;
  const textual = m.kind === "html" || m.kind === "md" || (m.kind === "image" && m.snippet);
  $("#pvSeg").hidden = !textual;
  const alt = $("#pvAlt"), editable = !m.snippet && /^(html?|svg|csv|tsv)$/.test(m.ext || "");   // these open as a preview; Edit shows their text
  alt.hidden = !editable; alt.textContent = "Edit"; alt.onclick = () => edLoad(PVX.root, PVX.path, PVX.id, "src");
  if (textual) { $$("#pvSeg button").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.v === "code") === PVX.code))); $("#pvSeg [data-v=code]").textContent = m.kind === "md" ? "Source" : "Code"; }
  st.innerHTML = "";
  if (m.kind === "pages") return pvPages(m);
  if (m.kind === "sheet" || m.sheets) return pvSheets(m);
  if (m.kind === "html") {
    if (PVX.code) return pvCode(m.text);
    const f = document.createElement("iframe");
    f.className = "pv-frame"; f.title = m.name || "HTML preview";
    f.setAttribute("sandbox", "allow-scripts allow-forms");   // scripts and forms work (a sign-up form can be tried); never allow-same-origin with scripts; no popups, no top navigation
    f.srcdoc = m.text; st.appendChild(f); return;
  }
  if (m.kind === "md") {
    if (PVX.code) return pvCode(m.text);
    st.innerHTML = `<div class="pv-md md"></div>`; $(".pv-md", st).innerHTML = md(m.text); return;
  }
  if (m.kind === "image") {
    if (m.snippet && PVX.code) return pvCode(m.text);
    const wrap = document.createElement("div"); wrap.className = "pv-img"; st.appendChild(wrap);
    const show = blob => {
      if (id !== PVX.id) return;
      const url = URL.createObjectURL(blob); PVX.urls.push(url);
      const img = new Image(); img.alt = m.name || "Image"; img.src = url; img.onerror = () => { if (id === PVX.id) pvNote("This image could not be drawn."); };
      wrap.appendChild(img);
    };
    if (m.snippet) { show(new Blob([m.text], {type: "image/svg+xml"})); return; }
    pvNote("Loading the image...", true);
    fetch(`/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(PVX.root, PVX.path)}`).then(async r => {
      if (!r.ok) { let m = ""; try { m = (await r.json()).error || ""; } catch (e) { /* not JSON */ } throw new Error(m || "Could not load the image: meshpool could not read the file. Check that it still exists, then open it again."); }
      const b = await r.blob(); if (id !== PVX.id) return;
      st.innerHTML = ""; st.appendChild(wrap);
      show(m.ext === "svg" ? new Blob([b], {type: "image/svg+xml"}) : b);
    }).catch(e => id === PVX.id && pvNote(e.message));
    return;
  }
  if (m.kind === "text") return pvCode(m.text);
  if (m.kind === "audio" || m.kind === "video") {           // B3: played straight from /raw; the file manager is one button away
    const wrap = document.createElement("div"); wrap.className = "pv-img pv-media";
    const el = document.createElement(m.kind); el.controls = true; el.preload = "metadata";
    el.setAttribute("aria-label", m.name || m.kind);
    el.src = `/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(PVX.root, PVX.path)}`;
    el.onerror = () => { if (id === PVX.id) { const n = document.createElement("div"); n.className = "pv-more"; n.textContent = "This file could not be played here. Open it in its own app."; wrap.appendChild(n); } };
    wrap.appendChild(el);
    const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = "Show in folder";
    b.setAttribute("data-reveal", "1"); b.onclick = () => edReveal(PVX.root, PVX.path);
    wrap.appendChild(b); st.appendChild(wrap); return;
  }
  pvNote(m.note || "meshpool cannot show this kind of file yet.");
}
function pvCode(text) {
  const pre = document.createElement("pre"); pre.className = "pv-code"; pre.tabIndex = 0; pre.textContent = text;
  $("#pvStage").innerHTML = ""; $("#pvStage").appendChild(pre);
}

// ---- pages (decks, documents, PDFs)
function pvPages(m) {
  const n = m.pages || 0, st = $("#pvStage");
  if (!n) return pvNote("This file has no pages to show.");
  const deck = /^(pptx|ppt|pptm|ppsx|pps|potx|odp|key)$/.test(m.ext);
  st.innerHTML = `<div class="pv-pagewrap"><img class="pv-pg" alt=""></div>${m.total > n ? `<div class="pv-more">Showing the first ${n} of ${m.total} pages.</div>` : ""}`;
  const img = $(".pv-pg", st);
  img.onload = () => img.classList.remove("loading");
  img.onerror = () => { img.classList.remove("loading"); };
  const strip = $("#pvStrip"); strip.hidden = n < 2; strip.dataset.deck = deck ? "1" : "";
  const th = $("#pvThumbs"); th.innerHTML = "";
  for (let i = 0; i < n; i++) {
    const b = document.createElement("button"); b.className = "pv-th"; b.dataset.i = i; b.setAttribute("aria-label", `${deck ? "Slide" : "Page"} ${i + 1}`);
    const im = new Image(); im.loading = "lazy"; im.alt = ""; im.src = pvPage(PVX.root, PVX.path, i, 200, m.mtime);
    b.appendChild(im); const s = document.createElement("span"); s.textContent = i + 1; b.appendChild(s);
    b.onclick = () => pvGo(i); th.appendChild(b);
  }
  pvGo(0);
}
function pvGo(i) {
  const m = PVX.m; if (!m || m.kind !== "pages") return;
  const n = m.pages; i = Math.max(0, Math.min(n - 1, i)); PVX.page = i;
  const img = $("#pvStage .pv-pg"); if (!img) return;
  const src = pvPage(PVX.root, PVX.path, i, 1600, m.mtime);
  img.classList.add("loading"); img.alt = `Page ${i + 1} of ${n}`; img.src = src;
  $("#pvStage .pv-pagewrap").scrollTop = 0;
  $$("#pvThumbs .pv-th").forEach(b => { const on = +b.dataset.i === i; b.classList.toggle("on", on); b.setAttribute("aria-current", on ? "true" : "false"); if (on) b.scrollIntoView({block: "nearest", inline: "center"}); });
  $("#pvPrev").disabled = i === 0; $("#pvNext").disabled = i === n - 1;
  $("#pvCount").textContent = `${i + 1} / ${n}`;
}

// ---- sheets (tables are built with textContent: file text is never HTML)
function pvSheets(m) {
  const sheets = m.sheets || [], st = $("#pvStage"), tabs = $("#pvTabs");
  if (!sheets.length) return pvNote("This file is empty.");
  tabs.hidden = sheets.length < 2;
  if (sheets.length > 1) {
    tabs.innerHTML = "";
    sheets.forEach((s, i) => { const b = document.createElement("button"); b.setAttribute("role", "tab"); b.setAttribute("aria-selected", String(i === PVX.sheet)); b.textContent = s.hidden ? `${s.name} (hidden)` : s.name; b.title = s.hidden ? `${s.name}: a hidden sheet` : s.name; if (s.hidden) b.classList.add("pv-hid"); b.onclick = () => { PVX.sheet = i; pvSheets(m); }; tabs.appendChild(b); });
  }
  const s = sheets[Math.min(PVX.sheet, sheets.length - 1)];
  st.innerHTML = "";
  if (!s.rows.length) return pvNote("This sheet is empty.");
  const key = PVX.path + "#" + PVX.sheet;
  if (!PVX.view || PVX.view.key !== key) PVX.view = {key, col: -1, dir: 0, q: ""};
  const V = PVX.view;
  // numbers as the cell's format shows them: 1,234  -1,234.50  (1,234)  $1,234  3.1%
  const isNum = v => v !== "" && /^\(?-?[$\u20ac\u00a3\u00a5]?-?[\d,]*\.?\d+%?\)?$/.test(String(v).trim());
  const num = v => { const t = String(v).trim(), n = parseFloat(t.replace(/[^\d.-]/g, "")); return /^\(.*\)$/.test(t) ? -n : n; };
  const fx = s.formulas || {};
  // a first row of labels stays on top when sorting or filtering
  const headRow = s.rows.length > 1 && s.rows[0].some(v => v !== "") && s.rows[0].every(v => v === "" || !isNum(v));
  const bar = document.createElement("div"); bar.className = "pv-tools";
  const q = document.createElement("input"); q.type = "search"; q.className = "pv-filter"; q.placeholder = "Filter rows";
  q.setAttribute("aria-label", "Filter rows"); q.value = V.q;
  const count = document.createElement("span"); count.className = "pv-count";
  bar.append(q, count);
  const wrap = document.createElement("div"); wrap.className = "pv-sheet"; wrap.tabIndex = 0;
  const t = document.createElement("table"), tb = document.createElement("tbody");
  const colName = i => { let n = "", k = i + 1; while (k > 0) { n = String.fromCharCode(65 + (k - 1) % 26) + n; k = Math.floor((k - 1) / 26); } return n; };
  const head = document.createElement("thead"), hr = document.createElement("tr");
  hr.appendChild(document.createElement("th")).className = "rn";
  const ths = [];
  for (let c = 0; c < s.cols; c++) {
    const th = document.createElement("th"); th.className = "sortable"; th.tabIndex = 0;
    th.title = "Sort by this column"; ths.push(th); hr.appendChild(th);
    const go = () => { if (V.col !== c) { V.col = c; V.dir = 1; } else { V.dir = V.dir === 1 ? -1 : V.dir === -1 ? 0 : 1; } draw(); };
    th.onclick = go; th.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } };
  }
  head.appendChild(hr); t.appendChild(head);
  const draw = () => {
    ths.forEach((th, c) => { const on = V.col === c && V.dir; th.textContent = colName(c) + (on ? (V.dir === 1 ? " \u25b2" : " \u25bc") : "");
      th.setAttribute("aria-sort", on ? (V.dir === 1 ? "ascending" : "descending") : "none"); });
    let idx = s.rows.map((_, i) => i).filter(i => !(headRow && i === 0));
    const needle = V.q.trim().toLowerCase();
    if (needle) idx = idx.filter(i => s.rows[i].some(v => String(v).toLowerCase().includes(needle)));
    if (V.col >= 0 && V.dir) {
      const c = V.col;
      idx.sort((a, b) => { const x = s.rows[a][c] ?? "", y = s.rows[b][c] ?? "";
        if (x === "" || y === "") return x === y ? a - b : x === "" ? 1 : -1;                       // blanks last
        const r = isNum(x) && isNum(y) ? num(x) - num(y) : String(x).localeCompare(String(y), undefined, {numeric: true, sensitivity: "base"});
        return (r * V.dir) || a - b; });
    }
    if (headRow) idx.unshift(0);
    tb.innerHTML = "";
    idx.forEach(ri => {
      const r = s.rows[ri];
      const tr = document.createElement("tr"); if (headRow && ri === 0) tr.className = "hd";
      const rn = document.createElement("th"); rn.className = "rn"; rn.textContent = ri + 1; tr.appendChild(rn);
      r.forEach((v, ci) => { const td = document.createElement("td"); td.textContent = v; if (isNum(v)) td.className = "num";
        const f = fx[ri + ":" + ci]; if (f) { td.title = f; td.classList.add("fx"); } else if (String(v).length > 12) td.title = v;
        tr.appendChild(td); });
      tb.appendChild(tr);
    });
    const shown = idx.length - (headRow ? 1 : 0), all = s.rows.length - (headRow ? 1 : 0);
    count.textContent = needle ? `${shown} of ${all} rows` : "";
  };
  let qt = 0;
  q.oninput = () => { clearTimeout(qt); qt = setTimeout(() => { V.q = q.value; draw(); }, 120); };
  draw();
  t.appendChild(tb); wrap.appendChild(t); st.appendChild(bar); st.appendChild(wrap);
  pvFitCols(wrap, t, s.cols);
  const notes = [s.more ? `Showing the first ${s.rows.length} rows.` : "", s.wide ? "Showing the first 50 columns." : ""].filter(Boolean).join(" ");
  if (notes) st.insertAdjacentHTML("beforeend", `<div class="pv-more">${esc(notes)} Use Open to see the rest.</div>`);
}

// Columns narrowed so a sheet fits the pane: a long title in A1 made column A 300 px wide, and at 1024 or 1366 px only
// column B showed. The widest columns give way first, none below 72 px; a sheet that still does not fit scrolls sideways.
// Cut text ends in an ellipsis and shows in full on hover. Measured once per sheet; a resize of the pane fits it again.
function pvFitCols(wrap, t, cols) {
  if (!cols) return;
  const ths = [...t.querySelectorAll("thead th")];
  const nat = ths.slice(1).map(th => th.getBoundingClientRect().width), rn = ths[0]?.getBoundingClientRect().width || 0;
  if (!nat.length || nat.some(w => !w)) return;
  const fit = () => {
    const room = wrap.clientWidth - rn - 1;
    if (room <= 0) return;
    let cap = Math.max(...nat);
    if (nat.reduce((a, w) => a + w, 0) > room) {
      const ws = [...nat].sort((a, b) => a - b);
      let left = room, n = ws.length;
      cap = 72;
      for (const w of ws) { if (w * n <= left) { left -= w; n--; } else { cap = Math.max(72, Math.floor(left / n)); break; } }
    }
    let cg = t.querySelector("colgroup");
    if (!cg) { cg = document.createElement("colgroup"); t.prepend(cg); }
    // whole pixels, rounded up, can add up to a pixel or two more than the pane: that was a horizontal scrollbar on a sheet that
    // fits. The widest columns give the extra pixels back (never below 72) so the sheet is never wider than its pane.
    const ws = [rn, ...nat.map(w => Math.min(w, cap))].map(Math.ceil);
    let over = ws.reduce((a, w) => a + w, 0) - (wrap.clientWidth - 1);
    while (over > 0 && ws.slice(1).some(w => w > 72)) { const k = ws.indexOf(Math.max(...ws.slice(1)), 1); ws[k]--; over--; }
    cg.replaceChildren(...ws.map(w => { const c = document.createElement("col"); c.style.width = w + "px"; return c; }));
    const total = ws.reduce((a, w) => a + w, 0);
    t.style.tableLayout = "fixed"; t.style.width = total + "px";
  };
  fit();
  if (typeof ResizeObserver === "function") {
    let last = wrap.clientWidth;
    const ro = new ResizeObserver(() => { if (!wrap.isConnected) return ro.disconnect(); if (wrap.clientWidth !== last) { last = wrap.clientWidth; fit(); } });
    ro.observe(wrap);
  }
}

// ---- cards under a reply
const PV_CARD_RX = /\.(pptx?|pptm|ppsx|odp|docx?|odt|rtf|xlsx?|xlsm|ods|csv|tsv|pdf|html?|md|markdown|svg|png|jpe?g|gif|webp)$/i;
const PV_THUMB_RX = /\.(pptx?|pptm|ppsx|odp|docx?|odt|rtf|pdf)$/i;
// One card per file for the turn, newest version (the thumbnail carries the file's modified time). Idempotent: called again
// when the turn's file list arrives (checkpoint), it fills the same row.
// A file an engine keeps for its build and checks (qa.json, slides.json, deck.provenance.json...), not the user's
// deliverable: left out of the cards and the "files changed" count (server side: workbench.is_build_file).
const pvBuildFile = p => { p = String(p || "").replace(/\\/g, "/"); return /\.provenance\.json$/i.test(p) || /(^|\/)Meshpool outputs\/(.+\/)?[^/]+\.(json|jsonl|log|run|py)$/i.test(p); };
// A line that arrives under the reply (the receipt, the checks, next-step chips: .after010) goes below the file cards, never
// between the footer and them: it pushed the card away from under the pointer. Moved in the same task it is added, so no
// frame is drawn with it above the cards.
function pvKeepCardsFirst(col) {
  if (col._pvKeep || typeof MutationObserver !== "function") return;
  col._pvKeep = true;
  new MutationObserver(list => { for (const m of list) for (const n of m.addedNodes) {
    if (n.nodeType !== 1 || !n.classList.contains("after010")) continue;
    let last = null; for (let x = n.nextElementSibling; x && /\b(pvcards|pvbigs)\b/.test(x.className); x = x.nextElementSibling) last = x;
    if (last && n.previousElementSibling?.classList.contains("turnfoot")) last.after(n);
  } }).observe(col, {childList: true, subtree: true});
}
function pvTurnCards(cur, foot, turn) {
  if (!foot || !cur) return;
  { const col = foot.closest("#col"); if (col) pvKeepCardsFirst(col); }
  const root = typeof curRoot === "function" ? curRoot() : "";
  const seen = new Set(), base = String(root).replace(/\\/g, "/").replace(/\/+$/, "") + "/";
  const add = p => { p = String(p || "").replace(/\\/g, "/");
    if (base.length > 1 && p.startsWith(base)) p = p.slice(base.length);   // a full path inside the chat's folder is a file of this chat
    if (p && PV_CARD_RX.test(p) && !pvBuildFile(p) && !p.startsWith("/") && !/^[A-Za-z]:/.test(p) && !p.split("/").some(x => x.startsWith("."))) seen.add(p); };
  (cur.tpaths || []).forEach(add);
  const t = (cur.view?.turns || []).find(x => x.n === (turn ?? cur.turn));
  for (const c of t?.changed || []) if (c.status !== "deleted") add(c.path);
  const paths = [...seen];
  // The turn's row, wherever it is under its footer: the receipt line and the chips (.after010) go in right after the
  // footer, so the row drawn at turn end was no longer next to it when the checkpoint came, and a second row was
  // added (Sara: the PDF card twice). One turn, one row.
  let row = null;
  for (let n = foot.nextElementSibling; n && !n.classList.contains("turnfoot"); n = n.nextElementSibling) if (n.classList.contains("pvcards")) { row = n; break; }
  if (!paths.length || !root) { if (row?.nextElementSibling?.classList.contains("pvbigs")) row.nextElementSibling.remove(); row?.remove(); return; }
  if (!row) { row = document.createElement("div"); row.className = "pvcards"; foot.after(row); }
  // A picture shown large in the reply is not also a card in the row under it (the chart was there twice).
  const bigs = pvBigImages(row, root, paths);
  const shown = paths.filter(p => !bigs.has(p)).slice(0, 6), key = shown.join("|") + "#" + [...bigs].join("|");
  row.hidden = !shown.length;
  if (row.dataset.k === key) return;
  row.dataset.k = key; row.dataset.root = root;
  // In a chat with its own folder, every file is in that folder, and a file you attached is a copy of yours there:
  // the card says so, so "I fixed Budget.xlsx" is not read as a change to your own file. Download gets it back.
  const own = !!cur.view?.scratch, att = pvAttached(cur);
  row.innerHTML = shown.map(p => { const ty = pvType(p), thumb = PV_THUMB_RX.test(p), img = /\.(png|jpe?g|gif|webp|svg)$/i.test(p), nm = p.split("/").pop();
    const where = own ? (att.has(p) || att.has(nm) ? "Copy of your file, in this chat" : "In this chat") : "";
    return `<span class="pvwrap"><button class="pvcard" data-pv="${esc(p)}" title="${esc(p)}"><span class="pvth${thumb ? " wait" : ""}">${img ? `<img alt="" loading="lazy" src="/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(root, p)}">` : `<span class="pvi ${ty.ext && /^html?$/.test(ty.ext) ? "html" : ""}">${pvIcon(ty.ext)}</span>`}</span><span class="pvcm"><b title="${esc(nm)}">${esc(pvShortName(nm))}</b><small>${esc(ty.label)}${ty.ext ? " · ." + esc(ty.ext) : ""}</small>${where ? `<small class="pvwhere">${esc(where)}</small>` : ""}</span></button><button class="iconbtn pvdl" data-pvdl="${esc(p)}" aria-label="Download ${esc(nm)}" data-tip="Download" data-tip-pos="above">${ICON.dl}</button></span>`; }).join("")
    + (paths.length - bigs.size > shown.length ? `<span class="pvmore">+${paths.length - bigs.size - shown.length} more under Artifacts</span>` : "");
  $$(".pvcard", row).forEach(b => {
    // opening is delegated (the click listener below), so a card whose handlers were lost in a rebuild still opens
    if (PV_THUMB_RX.test(b.dataset.pv)) pvThumb(root, b.dataset.pv, $(".pvth", b));
  });
  $$("[data-pvdl]", row).forEach(b => b.onclick = e => { e.stopPropagation(); pvDownload(root, b.dataset.pvdl); });
}
// A picture the turn made, shown large in the reply when the person asked to see it ("show me the chart here") or when
// pictures are all the turn made. The file card stays; the picture is capped at the column's width, loads lazily and
// opens the preview on a click. A picture the reply text already shows (linkFiles) is not drawn twice.
const PV_IMG_RX = /\.(png|jpe?g|gif|webp|svg)$/i;
const PV_ASKED_RX = /\b(show|display|see|view|put|include|embed|attach|let me see)\b[^.?!]*\b(chat|here|inline|reply|below|me|this)\b|\b(in|into|inside) (the |this )?(chat|reply|conversation)\b|\bright here\b|\binline\b/i;
function pvBigImages(row, root, paths) {
  const old = row.nextElementSibling?.classList.contains("pvbigs") ? row.nextElementSibling : null;
  const imgs = paths.filter(p => PV_IMG_RX.test(p));
  let mu = row.previousElementSibling; while (mu && !mu.classList?.contains("msg-user")) mu = mu.previousElementSibling;
  const asked = mu && typeof userText === "function" ? PV_ASKED_RX.test(userText(mu)) : false;
  const main = imgs.length > 0 && imgs.length === paths.length;
  const named = new Set($$(".rimg", row.parentElement || document).map(i => i.getAttribute("alt")));
  const list = imgs.filter(p => !named.has(p)).slice(0, 3);
  const big = new Set(imgs.filter(p => named.has(p)));                    // drawn large by the reply's own text
  if (!(asked || main) || !list.length) { if (old) old.remove(); return big; }
  list.forEach(p => big.add(p));
  const k = list.join("|");
  if (old && old.dataset.k === k) return big;                             // already drawn: leave the loaded pictures alone
  const box = old || document.createElement("div"); box.className = "pvbigs"; box.dataset.root = root; box.dataset.k = k;
  box.innerHTML = list.map(p => `<button type="button" class="pvbig wait" data-pvbig="${esc(p)}" title="Open ${esc(p.split("/").pop())}"><img alt="${esc(p.split("/").pop())}" loading="lazy" decoding="async" src="/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(root, p)}"></button>`).join("");
  $$("img", box).forEach(im => { const b = im.parentNode; im.onload = () => b.classList.remove("wait"); im.onerror = () => b.remove(); });
  if (!old) row.after(box);
  return big;
}
document.addEventListener("click", e => {
  const b = e.target.closest?.(".pvbig[data-pvbig]"); if (!b) return;
  openPreview(b.closest(".pvbigs")?.dataset.root || (typeof curRoot === "function" ? curRoot() : ""), b.dataset.pvbig);
});
// The files the person attached in this chat (the server adds "Attached files (in ...): a, b" to the message).
function pvAttached(cur) {
  const out = new Set();
  for (const e of cur?.log || []) if (e.type === "user") for (const m of String(e.text || "").matchAll(/Attached files \(in [^)]*\): ([^\n]+)/g)) m[1].split(", ").forEach(x => out.add(x.trim()));
  return out;
}
async function pvThumb(root, path, box) {
  // A chat is rebuilt off-screen and swapped in whole (Undo, a reload, switching chats): wait a moment for the card to
  // be on the page instead of giving up at once, which left every card without its thumbnail after Undo.
  for (let w = 0; w < 50 && !box.isConnected; w++) await new Promise(r => setTimeout(r, 100));
  for (let i = 0; i < 90 && box.isConnected; i++) {
    let m; try { m = await api(`/api/preview?${pvQuery(root, path)}`); } catch (e) { box.classList.remove("wait"); return; }
    if (m.state === "ready" && m.pages) {
      const im = new Image(); im.alt = ""; im.onload = () => { if (box.isConnected) { box.innerHTML = ""; box.appendChild(im); box.classList.remove("wait"); box.classList.add("page"); } };
      im.src = pvPage(root, path, 0, 320, m.mtime); return;
    }
    if (m.state !== "working") { box.classList.remove("wait"); return; }
    await new Promise(r => setTimeout(r, 1500));
  }
  box.classList.remove("wait");
}

// ---- global hooks
// A file card opens its file whatever happened to the row that holds it (priya018: the "Open house.html" card did
// nothing when clicked, three times; cards of a chat rebuilt off-screen lose handlers set on the buttons).
document.addEventListener("click", e => {
  const b = e.target.closest?.(".pvcards .pvcard"); if (!b || e.target.closest("[data-pvdl]")) return;
  const row = b.closest(".pvcards");
  openPreview(row?.dataset.root || (typeof curRoot === "function" ? curRoot() : ""), b.dataset.pv);
});
document.addEventListener("click", e => {
  const b = e.target.closest?.(".pvbtn[data-pvcode]"); if (!b) return;
  e.preventDefault();
  const code = $("pre code", b.closest(".codewrap"))?.textContent || "";
  PVX.opener = b; openPreviewCode(b.dataset.pvcode, code);
});
window.addEventListener("keydown", e => {            // Esc closes the pane first, unless a menu or dialog is open over it
  if (e.key !== "Escape" || !PVX.open || $("#pop, #ctx, .scrim, #srch, #onb") || e.target.closest?.("#edTa, #edFq, .fs-q")) return;
  e.preventDefault(); e.stopPropagation(); closePreview();
}, true);

/* ---------- The Browser panel: what the agent's browser shows, inside Meshpool ----------
   The browser itself is hidden (agent/browser_mcp_server.py). This panel shows it as pictures (a JPEG about once a second,
   faster for a moment after you act, and not at all while the window is hidden), and passes your clicks, scrolls and typing
   on, so you can sign in or solve a captcha without any other window. Server side: ui/browser_api.py.
   src "chat": the browser of the open chat. src "signin": the app's own browser for sign-in pages. */
const BRW = {back: "", src: "chat", tok: 0, timer: 0, known: "", info: null, fast: 0, opened: {}, img: "", buf: "", bufT: 0, wheel: 0, wheelT: 0, wp: null};
function brwQuery() { return `src=${BRW.src}&chat=${encodeURIComponent(S.cur?.id || "")}`; }
function brwStop() { BRW.tok++; clearTimeout(BRW.timer); BRW.timer = 0; if (BRW.img) { try { URL.revokeObjectURL(BRW.img); } catch (e) {} BRW.img = ""; } }
// A sign-in page opened inside Meshpool. Settings covers the panel, so it steps aside and comes back on Done.
function showSignIn() {
  const settings = $(".scrim:not(.dlg-scrim):not(.srch-scrim)");
  if (settings) { BRW.back = $("#cnl") ? "connectors" : ""; closeSettings(); }
  BRW.src = "signin"; BRW.known = ""; togglePanel("browser", true);
}
// The agent opened its first page in this chat: show the panel, once, without a toast or taking the keyboard.
function browserAutoOpen(chat) {
  if (!chat || BRW.opened[chat.id]) return;
  BRW.opened[chat.id] = true;
  if (S.cur !== chat || S.panel === "browser") return;
  BRW.src = "chat"; BRW.known = ""; togglePanel("browser", true);
}
async function brwSend(body) {
  BRW.fast = Date.now() + 4000;
  try { await post("/api/browser/input", {src: BRW.src, chat: S.cur?.id || "", ...body}); }
  catch (e) { toast(e.message, 3500); }
  clearTimeout(BRW.timer); BRW.timer = setTimeout(() => brwTick(BRW.tok), 120);
}
async function brwTick(id) {
  if (id !== BRW.tok || !$("#bImg")) return;
  const again = ms => { if (id === BRW.tok) BRW.timer = setTimeout(() => brwTick(id), ms); };
  if (document.hidden) return again(2000);
  const note = $("#bNote"), img = $("#bImg");
  try {
    const r = await fetch(`/api/browser/frame?${brwQuery()}&known=${encodeURIComponent(BRW.known)}`, {headers: {"X-Meshpool-Token": MP.token}});
    if (id !== BRW.tok) return;
    const why = r.headers.get("X-Frame-State") || "";
    if (why === "busy") return again(700);                // busy with the agent's own step
    if (r.status === 200 || (r.status === 204 && why === "same")) {
      let info = {}; try { info = JSON.parse(r.headers.get("X-Frame-Info") || "{}"); } catch (e) {}
      if (r.status === 200) {
        const url = URL.createObjectURL(await r.blob()); if (id !== BRW.tok) { URL.revokeObjectURL(url); return; }
        const old = BRW.img; BRW.img = url; img.src = url; if (old) URL.revokeObjectURL(old);
      }
      BRW.known = info.id || BRW.known; BRW.info = info; img.hidden = false; note.hidden = true;
      const u = $("#bUrl"); if (u && document.activeElement !== u && info.url !== undefined) u.value = info.url || "";
      const t = $("#bTitle"); if (t) t.textContent = info.title || "";
      return again(BRW.fast > Date.now() ? 250 : S.cur?.state === "working" ? 600 : 1000);
    }
    img.hidden = true; note.hidden = false; BRW.known = "";
    let msg = BRW.src === "signin" ? "Loading the page..." : "Nothing is open yet. When the agent opens a web page, it shows here. You can click and type in it.";
    try { const st = await api(`/api/browser/state?${brwQuery()}`); if (st.error) msg = st.error; } catch (e) {}
    note.textContent = msg;
  } catch (e) { /* the app is busy: try again */ }
  again(1500);
}
function brwPoint(e) {
  const img = $("#bImg"), r = img.getBoundingClientRect(), w = BRW.info?.width || 1280, h = BRW.info?.height || 720;
  return {x: Math.round((e.clientX - r.left) / Math.max(1, r.width) * w), y: Math.round((e.clientY - r.top) / Math.max(1, r.height) * h)};
}
const BRW_KEYS = {Enter: 1, Tab: 1, Escape: 1, Backspace: 1, Delete: 1, ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1, Home: 1, End: 1, PageUp: 1, PageDown: 1};
function brwFlush() { clearTimeout(BRW.bufT); if (BRW.buf) { const t = BRW.buf; BRW.buf = ""; brwSend({type: "text", text: t}); } }
function panelBrowser(body) {
  brwStop(); const id = BRW.tok;
  const signin = BRW.src === "signin";
  body.innerHTML = `<div class="pbar brw-bar"><button class="btn" id="bBack" aria-label="Back">Back</button><button class="btn" id="bReload" aria-label="Reload">Reload</button><input id="bUrl" aria-label="Address" placeholder="Address" spellcheck="false" autocomplete="off"><button class="btn" id="bGo">Go</button>${signin ? `<button class="btn primary" id="bDone">Done</button>` : ""}</div>
    <div class="brw-title" id="bTitle"></div>
    <div class="brw-stage" id="bStage" tabindex="0" role="application" aria-label="Browser view. Click it, then type to enter text."><img id="bImg" alt="The page the browser shows" draggable="false" hidden><div class="pnote" id="bNote">Loading...</div></div>
    <div class="brw-foot">${signin ? "Sign in here, then press Done." : "This is the browser the agent uses. Sign in here, or solve a check, and the agent carries on."}${signin ? ` <button class="linkbtn" id="bSystem" title="Opens this page in your own browser, for sites that will not sign you in here">This site refuses? Open it in my browser</button>` : ""}${signin && S.cur ? ` <button class="linkbtn" id="bAgent">Show the agent's browser</button>` : ""}</div>`;
  const stage = $("#bStage"), img = $("#bImg");
  $("#bBack").onclick = () => brwSend({type: "back"});
  $("#bReload").onclick = () => brwSend({type: "reload"});
  const go = () => { const v = $("#bUrl").value.trim(); if (v) brwSend({type: "goto", url: v}); };
  $("#bGo").onclick = go; $("#bUrl").onkeydown = e => { if (e.key === "Enter") go(); };
  if ($("#bDone")) $("#bDone").onclick = () => { post("/api/browser/close", {src: "signin"}).catch(() => {}); BRW.src = "chat"; brwStop(); S.connCache = null; togglePanel("browser"); if (BRW.back) { const s = BRW.back; BRW.back = ""; openSettings(s); } else toast("Done. If you signed in to an app, check it under Settings."); };
  $("#bSystem")?.addEventListener("click", () => post("/api/browser/system-open", {}).then(() => toast("Opened in your browser. Come back and press Done."), e => toast(e.message)));
  $("#bAgent")?.addEventListener("click", () => { BRW.src = "chat"; BRW.known = ""; renderPanel(); });
  img.addEventListener("click", e => { stage.focus({preventScroll: true}); brwSend({type: "click", count: Math.min(3, e.detail || 1), ...brwPoint(e)}); });
  img.addEventListener("contextmenu", e => { e.preventDefault(); brwSend({type: "click", button: "right", ...brwPoint(e)}); });
  stage.addEventListener("wheel", e => {
    if (e.target !== img) return; e.preventDefault();
    BRW.wheel += e.deltaY * (e.deltaMode === 1 ? 16 : 1); BRW.wp = brwPoint(e);
    if (!BRW.wheelT) BRW.wheelT = setTimeout(() => { const dy = Math.round(BRW.wheel); BRW.wheel = 0; BRW.wheelT = 0; if (dy) brwSend({type: "scroll", dx: 0, dy, ...BRW.wp}); }, 90);
  }, {passive: false});
  stage.addEventListener("keydown", e => {
    if (e.isComposing) return;
    const mod = (e.ctrlKey ? "Control+" : "") + (e.altKey ? "Alt+" : "") + (e.metaKey ? "Meta+" : "");
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); BRW.buf += e.key; clearTimeout(BRW.bufT); BRW.bufT = setTimeout(brwFlush, 60); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return;           // the paste event carries the text
    if (BRW_KEYS[e.key] || ((e.ctrlKey || e.metaKey) && /^[a-z]$/i.test(e.key))) { e.preventDefault(); brwFlush(); brwSend({type: "key", key: mod + (e.key.length === 1 ? e.key.toUpperCase() : e.key)}); }
  });
  stage.addEventListener("paste", e => { const t = e.clipboardData?.getData("text") || ""; if (t) { e.preventDefault(); brwFlush(); brwSend({type: "text", text: t}); } });
  BRW.known = ""; BRW.info = null; BRW.timer = setTimeout(() => brwTick(id), 0);
}
