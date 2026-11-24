/* meshpool in Excel, PowerPoint and Word: the side panel. It reads the selection (only when "Include the selection"
   is ticked), sends it with your request to a meshpool chat on this computer, shows the answer as it streams, and
   puts the answer back into the document when you click Insert. Nothing is written to the document without a click. */
"use strict";

// ------------------------------------------------------------------------------------------- pure helpers (tested)
const MAX_CELLS = 4000, MAX_TEXT = 60000;

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out = []; let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; continue; }
    if (s[i] === "|") { out.push(cur.trim()); cur = ""; continue; }
    cur += s[i];
  }
  out.push(cur.trim());
  return out;
}

const SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

/** The first markdown table in a reply, as rows of cells (header first), or null. */
function parseTable(md) {
  const lines = String(md || "").split(/\r?\n/);
  for (let i = 0; i + 1 < lines.length; i++) {
    if (lines[i].includes("|") && SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const rows = [splitRow(lines[i])];
      for (let j = i + 2; j < lines.length && lines[j].includes("|") && lines[j].trim(); j++) rows.push(splitRow(lines[j]));
      return rows.map(r => r.map(c => c.replace(/^\*\*(.*)\*\*$/, "$1").replace(/^`(.*)`$/, "$1")));
    }
  }
  return null;
}

/** The first fenced code block's text, or null. */
function firstCode(md) {
  const m = /```[^\n]*\n([\s\S]*?)```/.exec(String(md || ""));
  return m ? m[1].replace(/\n$/, "") : null;
}

/** Markdown to plain text: for slides, and for pasting as text. */
function plain(md) {
  const t = parseTable(md);
  let s = String(md || "");
  if (t) s = s.replace(/(^|\n)([^\n]*\|[^\n]*\n\s*\|?\s*:?-{2,}[^\n]*(\n[^\n]*\|[^\n]*)*)/, "\n" + t.map(r => r.join("\t")).join("\n"));
  return s.replace(/```[^\n]*\n?/g, "").replace(/^#{1,6}\s+/gm, "").replace(/^\s*[-*+]\s+/gm, "• ")
    .replace(/\*\*(.+?)\*\*/g, "$1").replace(/__(.+?)__/g, "$1").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1").replace(/\n{3,}/g, "\n\n").trim();
}

/** A reply as cells for Excel: its table; else its code block (one formula or row per line, tabs or commas split
    columns); else its text, one line per row. Every row padded to the same width. */
function toMatrix(md) {
  let rows = parseTable(md);
  if (!rows) {
    const code = firstCode(md);
    const src = (code != null ? code : plain(md)).split(/\r?\n/).filter(l => l.trim() !== "");
    const tabs = src.some(l => l.includes("\t"));
    const formulas = src.every(l => l.trim().startsWith("="));
    const commas = !tabs && !formulas && code != null && src.length > 1 &&
      src.every(l => l.split(",").length === src[0].split(",").length && l.includes(","));
    rows = src.map(l => tabs ? l.split("\t") : commas ? l.split(",").map(c => c.trim()) : [formulas ? l.trim() : l]);
  }
  if (!rows.length) return [[""]];
  const w = Math.max(...rows.map(r => r.length));
  return rows.map(r => r.concat(Array(w - r.length).fill("")));
}

function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<em>$2</em>");
}

/** A small, safe markdown renderer: everything is escaped first; only these tags are produced. */
function mdToHtml(md) {
  const lines = String(md || "").split(/\r?\n/);
  let out = "", list = null, para = [];
  const flushP = () => { if (para.length) { out += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
  const flushL = () => { if (list) { out += `<${list.t}>` + list.items.map(x => `<li>${inline(x)}</li>`).join("") + `</${list.t}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^```/.test(l)) {
      flushP(); flushL();
      const body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      out += "<pre><code>" + esc(body.join("\n")) + "</code></pre>";
      continue;
    }
    if (l.includes("|") && i + 1 < lines.length && SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      flushP(); flushL();
      const head = splitRow(l), rows = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(splitRow(lines[i]));
      i--;
      out += "<table><thead><tr>" + head.map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
        rows.map(r => "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
      continue;
    }
    const h = /^(#{1,3})\s+(.*)$/.exec(l);
    if (h) { flushP(); flushL(); out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    const b = /^\s*(?:[-*+]|(\d+)[.)])\s+(.*)$/.exec(l);
    if (b) {
      flushP();
      const t = b[1] ? "ol" : "ul";
      if (!list || list.t !== t) { flushL(); list = {t, items: []}; }
      list.items.push(b[2]);
      continue;
    }
    if (!l.trim()) { flushP(); flushL(); continue; }
    flushL();
    para.push(l);
  }
  flushP(); flushL();
  return out;
}

const GUIDE = {
  Excel: "When the answer is data or formulas for the sheet, give it as one markdown table (a formula cell starts " +
    "with =) or, for a single formula, just the formula in a code block. The panel inserts it at the active cell.",
  Word: "Write the answer as the text to put in the document, in markdown (headings, lists, bold and tables are " +
    "kept). No preamble.",
  PowerPoint: "For slide text, give a short title on the first line, then a few bullet points. No preamble.",
};

/** The message sent to meshpool: where the request comes from, the selection, the request. */
function buildMessage(host, ctx, ask, first) {
  const parts = [];
  if (first) parts.push(`[I am in ${host || "Office"}, using the meshpool panel. Answer in this chat; do not create ` +
    `files unless I ask. ${GUIDE[host] || ""}]`);
  if (ctx && ctx.text) {
    parts.push(`${ctx.label}${ctx.cut ? " (cut to fit)" : ""}:\n<selection>\n${ctx.text}\n</selection>`);
  } else if (ctx && ctx.label) {
    parts.push(ctx.label + ".");
  }
  parts.push(ask);
  return parts.join("\n\n");
}

/** Excel values (and formulas where a cell has one) as tab-separated lines. */
function cellsText(values, formulas) {
  return values.map((r, i) => r.map((v, j) => {
    const f = formulas && formulas[i] && formulas[i][j];
    return typeof f === "string" && f.startsWith("=") ? `${f} [${v}]` : String(v == null ? "" : v);
  }).join("\t")).join("\n");
}

function splitAddress(addr) {           // "'My sheet'!A1:B2" -> ["My sheet", "A1:B2"]
  const i = String(addr).lastIndexOf("!");
  return [String(addr).slice(0, i).replace(/^'(.*)'$/, "$1").replace(/''/g, "'"), String(addr).slice(i + 1)];
}

if (typeof module !== "undefined") {
  module.exports = {esc, parseTable, firstCode, plain, toMatrix, mdToHtml, buildMessage, cellsText, splitAddress};
}

// ------------------------------------------------------------------------------------------- the panel
if (typeof document !== "undefined") (function () {
  const $ = id => document.getElementById(id);
  const S = {id: null, since: 0, host: "", busy: false, bot: null, live: "", final: "", ctx: null, undo: null, polls: 0};
  const TOKEN = window.MESHPOOL_TOKEN;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function api(method, path, body) {
    let r;
    try {
      r = await fetch(path, {method, headers: {"X-Meshpool-Token": TOKEN, "Content-Type": "application/json"},
                             body: body ? JSON.stringify(body) : undefined, cache: "no-store"});
    } catch (e) {
      throw new Error("meshpool is not answering. Start the meshpool app on this computer, then try again.");
    }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `meshpool answered ${r.status}.`);
    return j;
  }

  // ---- reading the document
  async function readExcel() {
    return Excel.run(async c => {
      const r = c.workbook.getSelectedRange();
      r.load("address,rowCount,columnCount");
      await c.sync();
      const cols = Math.min(r.columnCount, 60), rows = Math.min(r.rowCount, Math.max(1, Math.floor(MAX_CELLS / cols)));
      const cut = cols < r.columnCount || rows < r.rowCount;
      const part = cut ? r.getCell(0, 0).getResizedRange(rows - 1, cols - 1) : r;
      part.load("values,formulas");
      await c.sync();
      const text = cellsText(part.values, part.formulas);
      const empty = !text.replace(/\s/g, "");
      const n = r.rowCount * r.columnCount;
      return {label: empty ? `The active cell is ${r.address}, and it is empty` :
                `Selected range ${r.address} (${n} cell${n === 1 ? "" : "s"}; tab-separated; a formula is shown with its value in brackets)`,
              text: empty ? "" : text, cut, short: empty ? `${r.address} (empty)` : `${r.address}, ${n} cell${n === 1 ? "" : "s"}`};
    });
  }

  async function readWord() {
    return Word.run(async c => {
      const s = c.document.getSelection();
      s.load("text");
      await c.sync();
      let text = s.text || "", label = "Selected text";
      if (!text.trim()) {
        const b = c.document.body;
        b.load("text");
        await c.sync();
        text = b.text || "";
        label = "Nothing is selected; this is the whole document";
      }
      const cut = text.length > MAX_TEXT;
      return {label, text: text.slice(0, MAX_TEXT), cut,
              short: label === "Selected text" ? `${text.trim().split(/\s+/).length} words selected` : "the whole document"};
    });
  }

  function selectedText() {
    return new Promise(res => {
      try {
        Office.context.document.getSelectedDataAsync(Office.CoercionType.Text, r =>
          res(r.status === Office.AsyncResultStatus.Succeeded ? String(r.value || "") : ""));
      } catch (e) { res(""); }
    });
  }

  async function readPowerPoint() {
    const sel = await selectedText();
    if (sel.trim()) return {label: "Selected text on the slide", text: sel.slice(0, MAX_TEXT), cut: sel.length > MAX_TEXT, short: "selected text"};
    if (!(window.PowerPoint && Office.context.requirements.isSetSupported("PowerPointApi", "1.4"))) {
      return {label: "Nothing is selected", text: "", short: "nothing selected"};
    }
    return PowerPoint.run(async c => {
      const slides = c.presentation.slides;
      slides.load("items");
      await c.sync();
      const out = [];
      for (const [i, s] of slides.items.slice(0, 60).entries()) {
        s.shapes.load("items");
        await c.sync();
        const texts = [];
        for (const sh of s.shapes.items) {
          try { sh.textFrame.textRange.load("text"); await c.sync(); if (sh.textFrame.textRange.text.trim()) texts.push(sh.textFrame.textRange.text.trim()); }
          catch (e) { /* a picture or chart: no text */ }
        }
        out.push(`Slide ${i + 1}:\n${texts.join("\n")}`);
      }
      const text = out.join("\n\n");
      return {label: `Nothing is selected; this is the text of the deck (${slides.items.length} slides)`,
              text: text.slice(0, MAX_TEXT), cut: text.length > MAX_TEXT || slides.items.length > 60, short: "the whole deck"};
    });
  }

  async function readContext() {
    try {
      if (S.host === "Excel") return await readExcel();
      if (S.host === "Word") return await readWord();
      if (S.host === "PowerPoint") return await readPowerPoint();
    } catch (e) {
      return {label: "", text: "", short: "could not read the selection: " + (e.message || e)};
    }
    return null;
  }

  async function showContext() {
    if (!$("useSel").checked) { $("ctxText").textContent = "Include the selection"; return; }
    S.ctx = await readContext();
    $("ctxText").textContent = S.ctx ? "Include the selection: " + S.ctx.short : "Include the selection";
  }

  // ---- writing to the document
  async function excelInsert(md, newSheet) {
    const m = toMatrix(md), rows = m.length, cols = m[0].length;
    return Excel.run(async c => {
      let target, sheetName;
      if (newSheet) {
        const all = c.workbook.worksheets;
        all.load("items/name");
        await c.sync();
        const names = new Set(all.items.map(w => w.name));
        sheetName = "meshpool";
        for (let k = 2; names.has(sheetName); k++) sheetName = `meshpool ${k}`;
        const ws = all.add(sheetName);
        target = ws.getRange("A1").getResizedRange(rows - 1, cols - 1);
        ws.activate();
      } else {
        target = c.workbook.getSelectedRange().getCell(0, 0).getResizedRange(rows - 1, cols - 1);
      }
      target.load("address,formulas");
      await c.sync();
      S.undo = newSheet ? {sheet: sheetName} : {address: target.address, formulas: target.formulas};
      target.formulas = m;
      target.format.autofitColumns();
      await c.sync();
      return newSheet ? `Added the sheet ${sheetName}.` : `Inserted ${rows} × ${cols} at ${target.address}.`;
    });
  }

  async function excelUndo() {
    const u = S.undo;
    if (!u) return "";
    await Excel.run(async c => {
      if (u.sheet) {
        c.workbook.worksheets.getItem(u.sheet).delete();
      } else {
        const [sheet, addr] = splitAddress(u.address);
        c.workbook.worksheets.getItem(sheet).getRange(addr).formulas = u.formulas;
      }
      await c.sync();
    });
    S.undo = null;
    return u.sheet ? `Removed the sheet ${u.sheet}.` : `Put back what was in ${u.address}.`;
  }

  async function wordInsert(md, where) {
    return Word.run(async c => {
      c.document.getSelection().insertHtml(mdToHtml(md), where);
      await c.sync();
      return where === "Replace" ? "Replaced the selection." : "Inserted after the selection.";
    });
  }

  function pptInsert(md) {
    return new Promise((res, rej) => Office.context.document.setSelectedDataAsync(plain(md),
      {coercionType: Office.CoercionType.Text},
      r => r.status === Office.AsyncResultStatus.Succeeded ? res("Inserted on the slide.") : rej(new Error(r.error && r.error.message || "PowerPoint did not take the text."))));
  }

  async function pptNewSlide(md) {
    if (!(window.PowerPoint && Office.context.requirements.isSetSupported("PowerPointApi", "1.4"))) {
      throw new Error("Adding a slide needs a newer PowerPoint. Use Insert on the slide instead.");
    }
    const lines = plain(md).split("\n").filter(l => l.trim());
    const title = (lines.shift() || "").replace(/^•\s*/, "");
    return PowerPoint.run(async c => {
      c.presentation.slides.add();
      await c.sync();
      const slides = c.presentation.slides;
      slides.load("items");
      await c.sync();
      const s = slides.items[slides.items.length - 1];
      s.shapes.addTextBox(title, {left: 40, top: 28, width: 640, height: 70});
      if (lines.length) s.shapes.addTextBox(lines.join("\n"), {left: 40, top: 110, width: 640, height: 380});
      await c.sync();
      return `Added slide ${slides.items.length}.`;
    });
  }

  // ---- the conversation
  function add(cls, html) {
    $("empty")?.remove();
    const d = document.createElement("div");
    d.className = "msg " + cls;
    d.innerHTML = html;
    $("log").appendChild(d);
    d.scrollIntoView({block: "end"});
    return d;
  }
  const note = (text, bad) => add("note" + (bad ? " bad" : ""), esc(text));

  function setBusy(on) {
    S.busy = on;
    $("send").disabled = on;
    $("stop").hidden = !on;
    $("status").hidden = !on;
    if (on) $("status").textContent = "Working…";
  }

  function actions(div, md) {
    const acts = document.createElement("div");
    acts.className = "acts";
    const btn = (label, fn, title) => {
      const b = document.createElement("button");
      b.className = "ghost";
      b.textContent = label;
      if (title) b.title = title;
      b.onclick = async () => {
        b.disabled = true;
        try { const t = await fn(); if (t) note(t); } catch (e) { note(e.message || String(e), true); }
        b.disabled = false;
      };
      acts.appendChild(b);
      return b;
    };
    if (S.host === "Excel") {
      btn("Insert at selection", async () => { const t = await excelInsert(md, false); undo.hidden = false; return t; },
          "Write the table or formula into the sheet, starting at the active cell");
      btn("Insert in a new sheet", async () => { const t = await excelInsert(md, true); undo.hidden = false; return t; });
      var undo = btn("Undo insert", async () => { const t = await excelUndo(); undo.hidden = true; return t; });
      undo.hidden = true;
    } else if (S.host === "Word") {
      btn("Replace selection", () => wordInsert(md, "Replace"));
      btn("Insert after", () => wordInsert(md, "After"));
    } else if (S.host === "PowerPoint") {
      btn("Insert on slide", () => pptInsert(md), "Into the selected text box, or a new one on this slide");
      btn("New slide", () => pptNewSlide(md), "A new slide: the first line is its title");
    }
    btn("Copy", async () => { await navigator.clipboard.writeText(plain(md)); return "Copied."; });
    div.appendChild(acts);
  }

  function liveRender() {
    if (!S.bot) S.bot = add("bot", "");
    S.bot.innerHTML = mdToHtml(S.live);
    S.bot.scrollIntoView({block: "end"});
  }

  function onEvent(ev) {
    switch (ev.type) {
      case "text_delta": S.live += ev.text || ""; liveRender(); break;
      case "stream_reset": S.live = ""; if (S.bot) S.bot.innerHTML = ""; break;
      case "assistant":
        if (ev.has_tools) {                        // words before a tool call: kept short, as a note
          if (S.bot) { S.bot.remove(); S.bot = null; }
          if ((ev.text || "").trim()) note(ev.text.trim().slice(0, 300));
          S.live = "";
        } else {
          S.final = ev.text || S.live;
          S.live = S.final;
          liveRender();
        }
        break;
      case "tool_start": $("status").textContent = "Working: " + String(ev.name || "").replace(/^mcp__\w+__/, "").replace(/_/g, " ") + "…"; break;
      case "approval": askCard(ev); break;
      case "question": {
        const qs = (ev.questions || []).map(q => q.question || q.text || "").filter(Boolean);
        add("ask", `<div class="t">meshpool asks</div>${qs.map(q => `<p>${esc(q)}</p>`).join("")}<p class="note">Type your answer below and press Send.</p>`);
        $("send").disabled = false;
        S.asking = true;
        break;
      }
      case "error": note(ev.text || "Something went wrong.", true); break;
      case "turn_end": endTurn(); break;
    }
  }

  function askCard(ev) {
    const d = add("ask", `<div class="t">${esc(ev.title || "Allow this?")}</div>${ev.detail ? `<pre>${esc(String(ev.detail).slice(0, 1500))}</pre>` : ""}`);
    const row = document.createElement("div");
    row.className = "acts";
    for (const [label, decision] of [["Allow once", "yes"], ["Deny", "no"]]) {
      const b = document.createElement("button");
      b.textContent = label;
      if (decision === "no") b.className = "ghost";
      b.onclick = async () => {
        row.querySelectorAll("button").forEach(x => { x.disabled = true; });
        try { await api("POST", `/api/agent/${S.id}/answer`, {id: ev.id, decision, since: 1e9}); d.querySelector(".t").textContent += decision === "yes" ? " (allowed)" : " (denied)"; }
        catch (e) { note(e.message, true); }
      };
      row.appendChild(b);
    }
    d.appendChild(row);
  }

  function endTurn() {
    if (S.bot && S.final.trim()) actions(S.bot, S.final);
    S.bot = null; S.live = ""; S.final = "";
    setBusy(false);
    $("inp").focus();
  }

  function apply(v) {
    if (typeof v.total === "number") S.since = v.total;
    for (const ev of v.events || []) onEvent(ev);
  }

  async function poll() {
    let quiet = 0;
    while (S.id && S.busy) {
      await sleep(350);
      let v;
      try { v = await api("GET", `/api/agent/${S.id}?since=${S.since}`); }
      catch (e) { note(e.message, true); setBusy(false); return; }
      apply(v);
      if (!S.busy) return;
      quiet = ["working", "waiting", "starting"].includes(v.state) ? 0 : quiet + 1;
      if (quiet > 8) { if (S.bot || S.final) endTurn(); else setBusy(false); return; }   // ended without a turn_end
    }
  }

  async function send(text) {
    text = String(text || "").trim();
    if (!text) return;
    const answering = S.busy && S.asking;           // the answer to meshpool's question, while the turn waits
    if (S.busy && !answering) return;
    const ctx = !answering && $("useSel").checked ? await readContext() : null;
    add("user", esc(text) + (ctx && ctx.short ? `<div class="note">with ${esc(ctx.short)}</div>` : ""));
    $("inp").value = "";
    if (answering) {
      S.asking = false;
      $("send").disabled = true;
      try { apply(await api("POST", `/api/agent/${S.id}/send`, {text, since: S.since})); } catch (e) { note(e.message, true); }
      return;
    }
    const first = !S.id;
    setBusy(true);
    try {
      if (!S.id) {
        const v = await api("POST", "/api/agent", {kind: "cowork", task: text});
        S.id = v.id;
        S.since = v.total || 0;
      }
      apply(await api("POST", `/api/agent/${S.id}/send`, {text: buildMessage(S.host, ctx, text, first), since: S.since}));
      poll();
    } catch (e) {
      note(e.message, true);
      setBusy(false);
    }
  }

  const QUICK = {
    Excel: [["Explain this", "Explain what this data or formula does, briefly."],
            ["Write a formula", "Write a formula for this: "],
            ["Clean this up", "Clean up this data (trim spaces, fix case, consistent dates and numbers) and give it back as a table."],
            ["Summarize", "Summarize this data: totals, trends and anything unusual."]],
    Word: [["Rewrite", "Rewrite this to be clearer and shorter, same meaning."],
           ["Summarize", "Summarize this in a few bullet points."],
           ["Proofread", "Fix spelling and grammar. Give back the corrected text only."],
           ["Draft", "Draft: "]],
    PowerPoint: [["Slide from this", "Turn this into one slide: a title and up to five short bullets."],
                 ["Speaker notes", "Write short speaker notes for this."],
                 ["Tighten", "Make this slide text shorter and punchier."],
                 ["Outline a deck", "Outline a short deck about: "]],
  };

  function boot(host) {
    S.host = host;
    $("host").textContent = host ? "in " + host : "";
    const q = $("quick");
    for (const [label, prompt] of QUICK[host] || []) {
      const b = document.createElement("button");
      b.textContent = label;
      b.onclick = () => { if (prompt.endsWith(": ")) { $("inp").value = prompt; $("inp").focus(); } else send(prompt); };
      q.appendChild(b);
    }
    $("composer").onsubmit = e => { e.preventDefault(); send($("inp").value); };
    $("inp").onkeydown = e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("inp").value); } };
    $("stop").onclick = async () => { try { await api("POST", `/api/agent/${S.id}/stop`, {since: 1e9}); } catch (e) { note(e.message, true); } };
    $("newChat").onclick = () => {
      S.id = null; S.since = 0; S.undo = null; S.bot = null; S.live = ""; S.final = ""; setBusy(false);
      $("log").innerHTML = '<div id="empty" class="empty">New chat. Ask about what you selected, or ask for something new.</div>';
    };
    $("useSel").onchange = showContext;
    $("refreshSel").onclick = showContext;
    try {
      Office.context.document.addHandlerAsync(Office.EventType.DocumentSelectionChanged, () => { clearTimeout(S.t); S.t = setTimeout(showContext, 300); });
    } catch (e) { /* not every host reports selection changes */ }
    showContext();
    api("GET", "/api/settings").then(s => {
      if (!s.ready) $("note").textContent = "Set up a model in the meshpool app first.";
    }).catch(e => { $("note").textContent = e.message; });
  }

  if (window.Office && Office.onReady) {
    Office.onReady(info => boot(info && info.host ? String(info.host) : ""));
  } else {
    document.body.innerHTML = '<div class="off"><h2>Open this from Office</h2><p>This panel runs inside Excel, ' +
      "PowerPoint or Word. Install it with <code>meshpool office install</code>, then click meshpool on the Home tab.</p></div>";
  }
})();
