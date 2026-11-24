/* Isolated copies (worktrees, worktrees.py): the "Work in an isolated copy" option for new Code chats, the suggestion
   when another chat already works in the chosen folder, and the "Isolated copy" panel: the diff, Merge and Discard. */
const WTX = {seq: 0, folder: "", users: []};

function wtDraftOn() { return S.view === "chat" && S.mode === "code" && !S.cur && !!S.draft.project; }

async function wtSuggest() {
  if (!wtDraftOn()) return;
  const f = S.draft.project, n = ++WTX.seq;
  try {
    const r = await api("/api/worktree?workdir=" + encodeURIComponent(f));
    if (n !== WTX.seq) return;                       // a newer check (another folder) is under way
    WTX.folder = f; WTX.users = r.users || [];
  } catch (e) { WTX.folder = f; WTX.users = []; }
  wtBanner();
}

function wtBanner() {
  $("#wtSug")?.remove();
  if (!wtDraftOn()) return;
  const hero = $("#col .hero.code"); if (!hero) return;
  const name = esc(projName(S.draft.project));
  if (S.draft.worktree) {
    hero.insertAdjacentHTML("beforeend", `<div class="tipbar" id="wtSug" role="status">${ICON.branch}<span>This chat works in an isolated copy of <b>${name}</b>: other chats don't see its edits until you merge them.</span><button class="btn" id="wtOff">Work in the folder</button></div>`);
    $("#wtOff").onclick = () => { S.draft.worktree = false; wtBanner(); };
    return;
  }
  const others = WTX.folder === S.draft.project ? WTX.users : [];
  if (!others.length) return;
  const who = others.length === 1 ? `“${clip(others[0].title || "Another chat", 50)}” is` : `${others.length} other chats are`;
  hero.insertAdjacentHTML("beforeend", `<div class="tipbar" id="wtSug" role="status">${ICON.branch}<span>${esc(who)} already working in <b>${name}</b>. Work in an isolated copy so the two chats don't change each other's files?</span><button class="btn" id="wtOn">Use an isolated copy</button></div>`);
  $("#wtOn").onclick = () => { S.draft.worktree = true; wtBanner(); };
}

(() => {                                             // after every hero render: check the chosen folder
  const orig = renderHero;
  renderHero = function () { const r = orig.apply(this, arguments); $("#wtSug")?.remove(); wtSuggest(); return r; };
})();

function wtMenuItem() {
  if (S.cur || S.mode !== "code" || !S.draft.project || S.view === "project") return [];
  return [{label: "Work in an isolated copy (worktree)", icon: ICON.branch, checked: !!S.draft.worktree,
           desc: "Its own copy of the folder; merge the changes when done",
           fn: () => { S.draft.worktree = !S.draft.worktree; wtBanner(); }}];
}

async function panelWorktree(body) {
  const wt = S.cur?.view?.worktree;
  if (wt?.removed) { body.innerHTML = `<div class="pnote">This chat's isolated copy was discarded${wt.kept_branch ? `; its commits are kept on git branch <b>${esc(wt.kept_branch)}</b>` : ""}. Start a new chat to keep working.</div>`; return; }
  if (!wt) { body.innerHTML = `<div class="pnote">This chat works in the folder itself.</div>`; return; }
  const id = S.cur.id, src = projName(wt.source);
  body.innerHTML = `<div class="pbar"><span class="chip" title="${esc(wt.workdir || "")}">${ICON.branch}${esc(wt.branch || "Isolated copy")}</span><span style="flex:1"></span>
    <button class="btn" id="wtRef" aria-label="Refresh" title="Refresh">${ICON.refresh}</button><button class="btn" id="wtReset" title="Throw away the changes and start the copy again from ${esc(src)} as it is now">Reset to folder</button><button class="btn danger" id="wtDiscard" title="Delete the changes and remove this copy">Discard</button><button class="btn pri" id="wtMerge" title="Bring these changes into ${esc(src)}">Merge</button></div>
    <div class="pnote" id="wtNote" style="padding:8px 12px">Changes in this chat's isolated copy that <b>${esc(src)}</b> doesn't have yet.${wt.base_dirty ? " The folder had uncommitted changes when the copy was made; they are not in the copy." : ""}</div>
    <div id="wtBody" class="pbody"><div class="pnote">Loading...</div></div>`;
  $("#wtRef").onclick = renderPanel;
  const busy = () => ["working", "waiting"].includes(S.cur?.view?.state);
  const show = d => {
    const el = $("#wtBody"); if (!el || S.cur?.id !== id) return;
    if (!d) { el.innerHTML = `<div class="pnote">The copy could not be read.</div>`; return; }
    $("#wtMerge").disabled = $("#wtDiscard").disabled = $("#wtReset").disabled = !d.files.length;
    if (!d.files.length) { el.innerHTML = `<div class="pnote">No changes waiting: everything here is in ${esc(src)}.</div>`; return; }
    el.innerHTML = `<div class="pnote" style="padding:8px 12px">${esc(d.stat || d.files.length + " files")}</div>
      <ul class="flist">${d.files.map(f => `<li><span class="badge ${f.status === "A" ? "added" : f.status === "D" ? "deleted" : "modified"}">${esc(f.status)}</span><span class="p">${esc(f.path)}</span></li>`).join("")}</ul>
      <pre class="patch" id="wtPatch">${patchHTML(String(d.patch || "").replace(/^--- \/dev\/null$/gm, "--- a/").replace(/^\+\+\+ \/dev\/null$/gm, ""))}</pre>`;
  };
  const note = (html, bad) => { const n = $("#wtNote"); if (n) { n.innerHTML = html; n.classList.toggle("err", !!bad); } };
  $("#wtMerge").onclick = async () => {
    if (busy()) return toast("Wait for the chat to finish (or stop it) first.");
    $("#wtMerge").disabled = true;
    try {
      const r = await post(`/api/worktree/${id}`, {action: "merge"});
      if (r.ok) { toast(r.files?.length ? `Merged ${r.files.length} file${r.files.length === 1 ? "" : "s"} into ${src}` : (r.note || "Nothing to merge")); show(r.diff); note(`Merged into <b>${esc(src)}</b>. Keep working here and merge again later.`); }
      else {
        const list = (r.conflicts || []).concat(r.blocked || []);
        note(`<b>Not merged.</b> ${esc(r.reason || "")}${list.length ? `<ul class="wtlist">${list.slice(0, 20).map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}`, true);
        $("#wtMerge").disabled = false;
      }
    } catch (e) { toast(e.message); $("#wtMerge").disabled = false; }
  };
  $("#wtReset").onclick = async () => {
    if (busy()) return toast("Wait for the chat to finish (or stop it) first.");
    if (!await ask({title: "Reset to the folder?", text: `The changes in the isolated copy are deleted and the copy starts again from how ${src} is now. The copy stays. ${src} itself is not touched.`, ok: "Reset copy", cancel: "Keep them", danger: true})) return;
    try { const r = await post(`/api/worktree/${id}`, {action: "reset"}); show(r.diff); note("Changes deleted. The copy now matches the folder."); }
    catch (e) { toast(e.message); }
  };
  $("#wtDiscard").onclick = async () => {
    if (busy()) return toast("Wait for the chat to finish (or stop it) first.");
    if (!await ask({title: "Discard this isolated copy?", text: `The changes are deleted and the copy is removed, so this chat cannot go on editing in it. ${src} itself is not touched. Its git branch is kept only if it holds commits ${src} does not have.`, ok: "Discard copy", cancel: "Keep it", danger: true})) return;
    try { const r = await post(`/api/worktree/${id}`, {action: "discard"}); toast(r.note || "Discarded."); await renderPanel(); }
    catch (e) { toast(e.message); }
  };
  try { const r = await api(`/api/worktree/${id}`); show(r.diff); if (r.error) note(esc(r.error), true); }
  catch (e) { const el = $("#wtBody"); if (el) el.innerHTML = `<div class="pnote">${esc(e.message)}</div>`; }
}
