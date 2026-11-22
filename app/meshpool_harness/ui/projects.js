/* Projects: a name, instructions, knowledge files and a memory of their own, an optional linked folder or GitHub
   repository, and the chats that belong to it. Loaded after app.js; everything here runs on a click, never at load. */
"use strict";
(() => {
  const pjOf = id => S.pj.find(p => p.id === id);
  const fmtSize = n => n < 1024 ? n + " B" : n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB";

  // A larger text box than ask() offers, for instructions and memory.
  window.editText = function ({title, text = "", value = "", placeholder = "", ok = "Save", rows = 9, max = 0}) {
    return new Promise(res => {
      const sc = document.createElement("div"); sc.className = "scrim dlg-scrim";
      sc.innerHTML = `<div class="dlg wide" role="dialog" aria-modal="true" aria-label="${esc(title)}"><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}<textarea id="etIn" rows="${rows}" placeholder="${esc(placeholder)}" spellcheck="true" ${max ? `maxlength="${max}"` : ""}>${esc(value)}</textarea><div class="row"><button class="btn" data-a="0">Cancel</button><button class="btn primary" data-a="1">${esc(ok)}</button></div></div>`;
      document.body.appendChild(sc);
      const inp = $("#etIn", sc);
      const done = v => { sc.remove(); document.removeEventListener("keydown", key, true); res(v); };
      const key = e => { if (e.key === "Escape") { e.stopPropagation(); done(null); } if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); done(inp.value); } };
      document.addEventListener("keydown", key, true);
      sc.addEventListener("mousedown", e => { if (e.target === sc) done(null); });
      $$("[data-a]", sc).forEach(b => b.onclick = () => done(b.dataset.a === "1" ? inp.value : null));
      inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
    });
  };

  // Create a project: a name and what it is for. Resolves with the new project's id, or "" when cancelled.
  window.newProjectFlow = async function ({stay = false} = {}) {
    const name = await ask({title: "New project", text: "A project keeps related chats together, with its own instructions, files and memory.", value: "", ok: "Create project"});
    if (!name) return "";
    try {
      const p = await post("/api/pj", {name});
      await post(`/api/pj/${p.id}`, {action: "update", pinned: true});          // a new project shows in the sidebar's Pinned list
      await loadPj(); renderSide(); if (S.view === "page" && S.page === "projects") pageProjects();
      if (!stay) openProject(p.id);
      return p.id;
    } catch (e) { toast(e.message); return ""; }
  };
  window.renameProject = async function (pid) {
    const p = pjOf(pid); if (!p) return;
    const name = await ask({title: "Rename project", value: p.name, ok: "Rename"});
    if (!name || name === p.name) return;
    try { await post(`/api/pj/${pid}`, {action: "update", name}); await loadPj(); renderSide(); renderTop(); if (S.view === "project" && S.pjId === pid) drawProject(pid); } catch (e) { toast(e.message); }
  };
  window.deleteProject = async function (pid) {
    const p = pjOf(pid); if (!p) return;
    const n = S.threads.filter(t => t.project_id === pid && !t.archived).length;
    if (!await ask({title: `Delete “${clip(p.name, 40)}”?`, text: `Its instructions, files and memory are removed. ${n ? `Its ${n} chat${n === 1 ? "" : "s"} stay${n === 1 ? "s" : ""}, outside any project. ` : ""}${p.link ? "The linked folder is not touched." : ""}`.trim(), ok: "Delete project", cancel: "Keep project", danger: true})) return;
    try { await post(`/api/pj/${pid}`, {action: "delete"}); } catch (e) { return toast(e.message); }
    await loadPj(); await loadThreads();
    if (S.view === "project" && S.pjId === pid) newThread(); else { renderSide(); renderTop(); }
    toast("Project deleted");
  };

  // Show a project's page in the main area. The message box at the bottom starts a chat in this project.
  window.openProject = async function (pid, {focus = false} = {}) {
    if (!pjOf(pid)) { await loadPj(); if (!pjOf(pid)) return toast("That project is gone."); }
    leaveChat(S.cur); S.cur = null; setWorking(null);
    S.view = "project"; S.pjId = pid; S.draft.pid = pid; S.draft.project = "";
    S.attachments = []; renderAttach();
    $("#app").classList.remove("sideopen");
    renderTop(); renderBar(); renderSide(); renderPanel();
    $("#col").classList.remove("loading");
    await drawProject(pid);
    if (window.MeshBrief && $("#pjBrief")) { window.MeshBrief.render($("#pjBrief"), pid).then(() => post("/api/summaries/opened", {id: pid}).catch(() => {})); }
    restoreDraft();
    if (focus) $("#input").focus();
  };

  window.drawProject = async function (pid) {
    let d;
    try { d = await api(`/api/pj/${pid}`); }
    catch (e) { if (S.view === "project" && S.pjId === pid) $("#col").innerHTML = `<div class="hero"><h1>This project can’t be opened</h1><p class="sub">${esc(e.message)}</p></div>`; return; }
    if (S.view !== "project" || S.pjId !== pid) return;
    const idx = S.pj.findIndex(p => p.id === pid); if (idx >= 0) S.pj[idx] = Object.assign(S.pj[idx], d);
    const chats = S.threads.filter(t => t.project_id === pid && !t.archived).sort((a, z) => (Number(z.updated) || 0) - (Number(a.updated) || 0));
    const link = d.link;
    const ico = {pdf: ICON.file, default: ICON.file};
    $("#col").innerHTML = `<div class="pjpage">
      <header class="pjh">
        <div class="pjt"><span class="pjico">${link ? ICON.branch : ICON.folder}</span><h1 id="pjName">${esc(d.name)}</h1><button class="iconbtn" id="pjRename" data-tip="Rename project" aria-label="Rename project">${ICON.pencil}</button><span class="sp"></span><button class="iconbtn" id="pjMore" aria-label="Project options" aria-haspopup="menu" data-tip="Project options">${ICON.more}</button></div>
        <button class="pjdesc ${d.description ? "" : "empty"}" id="pjDesc" data-tip="Edit description">${esc(d.description || "Add a short description")}</button>
      </header>
      <div class="pjgrid">
        <section class="pjmain" aria-label="Chats in this project">
          <h2>Chats${chats.length ? `<span class="cnt">${chats.length}</span>` : ""}</h2>
          ${chats.length ? `<ul class="threads pjchats">${chats.map(t => thHTML(t)).join("")}</ul>` : `<div class="pjempty"><b>No chats yet.</b><span>Type a message below to start the first chat in this project. It will follow the instructions and use the files and memory on the right.</span></div>`}
        </section>
        <aside class="pjside">
          <section class="card" id="pjBrief" aria-label="Project brief"></section>
          <section class="card" aria-label="Instructions"><div class="chd"><span class="ci">${ICON.pencil}</span><h3>Instructions</h3><button class="linkbtn" id="pjInsEdit">${d.instructions ? "Edit" : "Add"}</button></div>
            ${d.instructions ? `<p class="clamp">${esc(d.instructions)}</p>` : `<p class="muted">Tell Meshpool how to work here: the tone, the format, what to avoid. Every chat in this project follows it.</p>`}</section>
          <section class="card" aria-label="Files"><div class="chd"><span class="ci">${ICON.book}</span><h3>Files</h3><span class="cnt">${d.knowledge.length || ""}</span><button class="linkbtn" id="pjAddFiles">Add files</button></div>
            <input type="file" id="pjFileIn" multiple hidden>
            ${d.knowledge.length ? `<ul class="klist">${d.knowledge.map(k => `<li><span class="ki">${ICON.file}</span><span class="kn" title="${esc(k.name)}">${esc(k.name)}<small>${fmtSize(k.size)}${k.readable ? "" : " · Meshpool can’t read the text in this file"}</small></span><button class="iconbtn" data-kdel="${esc(k.name)}" data-tip="Remove" aria-label="Remove ${esc(k.name)}">${ICON.x}</button></li>`).join("")}</ul>`
              : `<p class="muted">Add briefs, style guides or data that every chat here should know. You can also drop files on this page.</p>`}</section>
          <section class="card" aria-label="Memory"><div class="chd"><span class="ci">${ICON.brain}</span><h3>Memory</h3>${d.memory ? `<button class="linkbtn" id="pjMemEdit">Edit</button>` : ""}</div>
            ${d.memory ? `<ul class="mem">${d.memory.split("\n").map(l => `<li>${esc(l)}</li>`).join("")}</ul><button class="linkbtn danger" id="pjMemClear">Clear memory</button>`
              : `<p class="muted">Nothing yet. Meshpool saves lasting facts about this project here as you work. It is separate from your general memory.</p>`}</section>
          <section class="card" aria-label="Folder"><div class="chd"><span class="ci">${ICON.link}</span><h3>${link?.type === "github" ? "GitHub repository" : "Folder"}</h3></div>
            ${link ? `<p class="linked"><b title="${esc(link.path)}">${esc(link.type === "github" && link.repo ? link.repo : projName(link.path))}</b>${link.branch ? `<small>${esc(link.branch)}</small>` : ""}${link.missing ? `<small class="bad">This folder was moved or deleted.</small>` : `<small>${esc(shortPath(link.path))}</small>`}</p><p class="muted">Chats here read and change these files. The side panel shows the branch and changes.</p><div class="row"><button class="btn" id="pjLinkFolder">Change folder</button><button class="btn" id="pjUnlink">Unlink</button></div>`
              : `<p class="muted">Optional. Link a folder or a GitHub repository so chats here can work on your files and code.</p><div class="row"><button class="btn" id="pjLinkFolder">${ICON.dir} Link a folder</button><button class="btn" id="pjLinkGh">${ICON.pr} Link a repository</button></div>`}</section>
        </aside>
      </div></div>`;
    $("#conv").scrollTop = 0; $(".jumpdown")?.setAttribute("hidden", "");
    bindProject(pid, d);
  };

  function bindProject(pid, d) {
    const refresh = async () => { await loadPj(); renderSide(); renderTop(); await drawProject(pid); };
    $$(".pjchats .th").forEach(li => {
      li.onclick = e => { const m = e.target.closest("[data-tmenu]"); if (m) { e.stopPropagation(); return threadMenu({type: "click", currentTarget: m}, m.dataset.tmenu); } openThread(li.dataset.id); };
      li.oncontextmenu = e => { e.preventDefault(); threadMenu(e, li.dataset.id); };
      li.onkeydown = e => { if (e.key === "Enter" && e.target === li) openThread(li.dataset.id); };
    });
    $("#pjRename").onclick = () => renameProject(pid);
    $("#pjMore").onclick = e => projMenu(e, pid);
    $("#pjDesc").onclick = async () => {
      const v = await editText({title: "Project description", text: "A line or two about what this project is for.", value: d.description, rows: 3, max: 400, placeholder: "For example: Planning and copy for the spring product launch."});
      if (v === null) return;
      try { await post(`/api/pj/${pid}`, {action: "update", description: v}); await refresh(); } catch (e) { toast(e.message); }
    };
    $("#pjInsEdit").onclick = async () => {
      const v = await editText({title: "Project instructions", text: "Every new chat in this project follows these. They take priority over your general instructions. Chats that are already open keep what they started with.", value: d.instructions, rows: 11, max: 8000, placeholder: "For example: Write in British English. Keep answers under 200 words. Always put numbers in a table."});
      if (v === null) return;
      try { await post(`/api/pj/${pid}`, {action: "update", instructions: v}); await refresh(); toast("Instructions saved. New chats in this project use them."); } catch (e) { toast(e.message); }
    };
    // knowledge files: pick or drop
    const upload = async files => {
      let ok = 0;
      for (const f of files) {
        if (f.size > 25 * 1024 * 1024) { toast(`${f.name} is over 25 MB. Add a smaller file, or link its folder instead.`, 5000); continue; }
        const data = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); });
        try { await post(`/api/pj/${pid}`, {action: "knowledge_add", name: f.name, data}); ok++; } catch (e) { toast(e.message, 5000); }
      }
      if (ok) { await refresh(); toast(ok === 1 ? "File added" : `${ok} files added`); }
    };
    $("#pjAddFiles").onclick = () => $("#pjFileIn").click();
    $("#pjFileIn").onchange = () => { upload([...$("#pjFileIn").files]); $("#pjFileIn").value = ""; };
    const col = $("#col");
    col.ondragover = e => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); col.classList.add("drag"); } };
    col.ondragleave = e => { if (e.target === col || !col.contains(e.relatedTarget)) col.classList.remove("drag"); };
    col.ondrop = e => { col.classList.remove("drag"); if (e.dataTransfer?.files?.length) { e.preventDefault(); e.stopPropagation(); upload([...e.dataTransfer.files]); } };
    $$("[data-kdel]").forEach(b => b.onclick = async () => {
      try { await post(`/api/pj/${pid}`, {action: "knowledge_remove", name: b.dataset.kdel}); await refresh(); toast("File removed"); } catch (e) { toast(e.message); }
    });
    $("#pjMemEdit")?.addEventListener("click", async () => {
      const v = await editText({title: "Project memory", text: "One fact per line. Meshpool reads these at the start of every new chat in this project.", value: d.memory, rows: 11, max: 6000});
      if (v === null) return;
      try { await post(`/api/pj/${pid}`, {action: "memory_set", text: v}); await refresh(); } catch (e) { toast(e.message); }
    });
    $("#pjMemClear")?.addEventListener("click", async () => {
      if (!await ask({title: "Clear this project’s memory?", text: "Meshpool forgets everything it learned in this project. Your general memory is not affected.", ok: "Clear memory", cancel: "Keep it", danger: true})) return;
      try { await post(`/api/pj/${pid}`, {action: "memory_clear"}); await refresh(); toast("Memory cleared"); } catch (e) { toast(e.message); }
    });
    // link a folder or repository
    const link = async l => { try { await post(`/api/pj/${pid}`, {action: "update", link: l}); await refresh(); renderPanel(); } catch (e) { toast(e.message, 5000); } };
    $("#pjLinkFolder")?.addEventListener("click", async () => { const path = await browseFolder(d.link?.path || ""); if (path) { await link({type: "folder", path}); toast("Folder linked"); } });
    $("#pjLinkGh")?.addEventListener("click", () => openGithubPicker({title: "Link a GitHub repository", cta: "Link repository", onReady: async r => { await loadProjects(); await link({type: "github", path: r.path, repo: r.name || "", branch: r.branch || ""}); toast(`${r.name || "Repository"} linked`); }}));
    $("#pjUnlink")?.addEventListener("click", async () => { await link(null); toast("Unlinked. The files stay where they are."); });
  }
})();
