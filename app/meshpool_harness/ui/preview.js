/* In-app previews: the viewer pane beside the chat, the file cards under a reply, and the Preview button on code blocks.
   Server side: ui/previews.py (GET /api/preview). Loaded after app.js; uses its helpers ($, esc, md, api, post, toast, ICON).
   Page images and tables come from the server; HTML runs in a sandboxed frame (scripts yes, same origin no);
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
    <button class="btn" id="pvAlt" hidden></button><button class="iconbtn" id="pvMore" aria-label="More actions for this file" title="More actions">${ICON.more}</button><button class="iconbtn" id="pvClose" aria-label="Close preview" title="Close (Esc)">${ICON.x}</button></div>
    <div class="pv-tabs" id="pvTabs" role="tablist" hidden></div>
    <div class="pv-stage" id="pvStage"></div>
    <div class="pv-strip" id="pvStrip" hidden><button class="iconbtn" id="pvPrev" aria-label="Previous page">${ICON.chev}</button><div class="pv-thumbs" id="pvThumbs"></div><button class="iconbtn" id="pvNext" aria-label="Next page">${ICON.chev}</button><span class="pv-count" id="pvCount" aria-live="polite"></span></div>`;
  $("#app").appendChild(el);
  $("#pvClose").onclick = () => closePreview();
  $("#pvPrev").onclick = () => pvGo(PVX.page - 1);
  $("#pvNext").onclick = () => pvGo(PVX.page + 1);
  $$("#pvSeg button").forEach(b => b.onclick = () => { if (PVX.m?.kind === "editor") return edMode(b.dataset.v === "code" ? "src" : "view"); PVX.code = b.dataset.v === "code"; pvBody(); });
  $("#pvBack").onclick = async () => { const b = PVX.back; if (b && await edGuard()) edFolderPane(b.root, b.rel, b.file); };
  // The only route to the system file manager: a menu item a person picks on purpose, never a click on the file itself.
  $("#pvMore").onclick = e => {
    if (!PVX.root || (PVX.m?.kind !== "folder" && !PVX.path)) return;
    const root = PVX.root, path = PVX.path;
    ctxMenu(e, [{label: "Copy path", icon: ICON.copy, fn: () => { try { navigator.clipboard.writeText(root.replace(/[\\/]$/, "") + (path ? "/" + path : "")); toast("Path copied", 2000); } catch (err) { toast("Could not copy the path."); } }},
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
function pvFree() { PVX.urls.forEach(u => { try { URL.revokeObjectURL(u); } catch (e) {} }); PVX.urls = []; }
function pvShell(name, type, altLabel) {
  const el = pvEnsure(), app = $("#app");
  if (typeof edStop === "function") edStop();
  PVX.opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : PVX.opener;
  pvFree(); PVX.open = true; PVX.id++; PVX.m = null; PVX.page = 0; PVX.code = false; PVX.sheet = 0;
  PVX.back = PVX.backNext; PVX.backNext = null; $("#pvBack").hidden = !PVX.back;
  const sb = $$("#pvSeg button"); sb[0].textContent = "Preview"; sb[1].textContent = "Code";
  el.hidden = false; app.classList.add("pvopen");
  if (typeof fitPanel === "function") fitPanel();
  $("#pvName").textContent = name; $("#pvName").title = PVX.path || name; $("#pvType").textContent = type;
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
  const el = $("#pvp"); if (el) { el.hidden = true; $("#pvStage").innerHTML = ""; $("#pvThumbs").innerHTML = ""; }
  $("#app")?.classList.remove("pvopen");
  if (typeof fitPanel === "function") fitPanel();
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
    f.setAttribute("sandbox", "allow-scripts");               // scripts run; no same-origin, forms, popups or navigation
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
      if (!r.ok) throw new Error("Could not load the image.");
      const b = await r.blob(); if (id !== PVX.id) return;
      st.innerHTML = ""; st.appendChild(wrap);
      show(m.ext === "svg" ? new Blob([b], {type: "image/svg+xml"}) : b);
    }).catch(e => id === PVX.id && pvNote(e.message));
    return;
  }
  if (m.kind === "text") return pvCode(m.text);
  pvNote(m.note || "Meshpool cannot show this kind of file yet.");
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
    sheets.forEach((s, i) => { const b = document.createElement("button"); b.setAttribute("role", "tab"); b.setAttribute("aria-selected", String(i === PVX.sheet)); b.textContent = s.name; b.title = s.name; b.onclick = () => { PVX.sheet = i; pvSheets(m); }; tabs.appendChild(b); });
  }
  const s = sheets[Math.min(PVX.sheet, sheets.length - 1)];
  st.innerHTML = "";
  if (!s.rows.length) return pvNote("This sheet is empty.");
  const wrap = document.createElement("div"); wrap.className = "pv-sheet"; wrap.tabIndex = 0;
  const t = document.createElement("table"), tb = document.createElement("tbody");
  const colName = i => { let n = "", k = i + 1; while (k > 0) { n = String.fromCharCode(65 + (k - 1) % 26) + n; k = Math.floor((k - 1) / 26); } return n; };
  const head = document.createElement("thead"), hr = document.createElement("tr");
  hr.appendChild(document.createElement("th")).className = "rn";
  for (let c = 0; c < s.cols; c++) { const th = document.createElement("th"); th.textContent = colName(c); hr.appendChild(th); }
  head.appendChild(hr); t.appendChild(head);
  s.rows.forEach((r, ri) => {
    const tr = document.createElement("tr"); const rn = document.createElement("th"); rn.className = "rn"; rn.textContent = ri + 1; tr.appendChild(rn);
    r.forEach(v => { const td = document.createElement("td"); td.textContent = v; if (v !== "" && /^-?[\d,]*\.?\d+%?$/.test(v)) td.className = "num"; tr.appendChild(td); });
    tb.appendChild(tr);
  });
  t.appendChild(tb); wrap.appendChild(t); st.appendChild(wrap);
  const notes = [s.more ? `Showing the first ${s.rows.length} rows.` : "", s.wide ? "Showing the first 50 columns." : ""].filter(Boolean).join(" ");
  if (notes) st.insertAdjacentHTML("beforeend", `<div class="pv-more">${esc(notes)} Use Open to see the rest.</div>`);
}

// ---- cards under a reply
const PV_CARD_RX = /\.(pptx?|pptm|ppsx|odp|docx?|odt|rtf|xlsx?|xlsm|ods|csv|tsv|pdf|html?|md|markdown|svg|png|jpe?g|gif|webp)$/i;
const PV_THUMB_RX = /\.(pptx?|pptm|ppsx|odp|docx?|odt|rtf|pdf)$/i;
// One card per file for the turn, newest version (the thumbnail carries the file's modified time). Idempotent: called again
// when the turn's file list arrives (checkpoint), it fills the same row.
function pvTurnCards(cur, foot, turn) {
  if (!foot || !cur) return;
  const root = typeof curRoot === "function" ? curRoot() : "";
  const seen = new Set();
  const add = p => { p = String(p || "").replace(/\\/g, "/"); if (p && PV_CARD_RX.test(p) && !p.startsWith("/") && !/^[A-Za-z]:/.test(p) && !p.split("/").some(x => x.startsWith("."))) seen.add(p); };
  (cur.tpaths || []).forEach(add);
  const t = (cur.view?.turns || []).find(x => x.n === (turn ?? cur.turn));
  for (const c of t?.changed || []) if (c.status !== "deleted") add(c.path);
  const paths = [...seen];
  let row = foot.nextElementSibling; if (!row?.classList.contains("pvcards")) row = null;
  if (!paths.length || !root) { row?.remove(); return; }
  const shown = paths.slice(0, 6);
  if (row && row.dataset.k === shown.join("|")) return;
  if (!row) { row = document.createElement("div"); row.className = "pvcards"; foot.after(row); }
  row.dataset.k = shown.join("|");
  row.innerHTML = shown.map(p => { const ty = pvType(p), thumb = PV_THUMB_RX.test(p), img = /\.(png|jpe?g|gif|webp|svg)$/i.test(p);
    return `<button class="pvcard" data-pv="${esc(p)}" title="${esc(p)}"><span class="pvth${thumb ? " wait" : ""}">${img ? `<img alt="" loading="lazy" src="/raw?t=${encodeURIComponent(MP.token)}&${pvQuery(root, p)}">` : `<span class="pvi">${ICON.files}</span>`}</span><span class="pvcm"><b>${esc(p.split("/").pop())}</b><small>${esc(ty.label)}${ty.ext ? " · ." + esc(ty.ext) : ""}</small></span></button>`; }).join("")
    + (paths.length > shown.length ? `<span class="pvmore">+${paths.length - shown.length} more under Artifacts</span>` : "");
  $$(".pvcard", row).forEach(b => {
    b.onclick = () => openPreview(root, b.dataset.pv);
    if (PV_THUMB_RX.test(b.dataset.pv)) pvThumb(root, b.dataset.pv, $(".pvth", b));
  });
}
async function pvThumb(root, path, box) {
  for (let i = 0; i < 90 && box.isConnected; i++) {
    let m; try { m = await api(`/api/preview?${pvQuery(root, path)}`); } catch (e) { box.classList.remove("wait"); return; }
    if (m.state === "ready" && m.pages) {
      const im = new Image(); im.alt = ""; im.onload = () => { if (box.isConnected) { box.innerHTML = ""; box.appendChild(im); box.classList.remove("wait"); } };
      im.src = pvPage(root, path, 0, 320, m.mtime); return;
    }
    if (m.state !== "working") { box.classList.remove("wait"); return; }
    await new Promise(r => setTimeout(r, 1500));
  }
  box.classList.remove("wait");
}

// ---- global hooks
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
