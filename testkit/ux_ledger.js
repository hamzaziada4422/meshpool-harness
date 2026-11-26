// Meshpool UI test for the feature-ledger additions: code blocks with colours and a Copy button, the Thinking block
// for the built-in agent, the Effort menu, the @ file picker, Up arrow recall of sent messages, search inside files
// and the folder trust note. Runs against an app on MAIN_PORT backed by the mock model (testkit/mock_llm.py).
// Usage: node ux_ledger.js OUT_DIR MAIN_PORT PROJECT_DIR
const {chromium} = require("playwright");
const fs = require("fs"), path = require("path");
const [out, port, proj] = process.argv.slice(2);
const results = []; const check = (name, ok, detail = "") => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  -> " + String(detail).slice(0, 300))); };

(async () => {
  fs.mkdirSync(path.join(proj, ".meshpool"), {recursive: true});
  fs.writeFileSync(path.join(proj, ".meshpool", "hooks.json"), JSON.stringify({PreToolUse: [{matcher: "bash", command: "true"}]}));
  fs.writeFileSync(path.join(proj, "notes.md"), "# Notes\nthe needle is here\n");
  const b = await chromium.launch();
  const ctx = await b.newContext({viewport: {width: 1360, height: 880}, permissions: ["clipboard-read", "clipboard-write"]});
  const p = await ctx.newPage(); const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(() => { try { localStorage.setItem("mp_permsAsked", "1"); localStorage.removeItem("mp_sent"); } catch (e) {} });
  await p.goto(`http://127.0.0.1:${port}/`); await p.waitForSelector(".hero", {timeout: 30000});
  const ends = () => p.evaluate(() => (S.cur?.log || []).filter(e => e.type === "turn_end").length);
  const send = async text => {                       // send, then wait for this message's own turn to end
    const n = await ends(); await p.fill("#input", text); await p.keyboard.press("Enter");
    await p.waitForFunction(k => S.cur?.view?.state === "idle" && (S.cur.log || []).filter(e => e.type === "turn_end").length > k, n, {timeout: 60000}).catch(() => {});
  };
  // a new chat in a git folder can start in its own worktree (the plus menu)
  require("child_process").execSync("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", {cwd: proj});
  await p.evaluate(dir => { S.draft.project = dir; renderBar(); }, proj); await p.waitForTimeout(300);
  await p.click("#attBtn"); await p.waitForTimeout(300);
  let wt = false; for (const x of await p.$$("#ctx .mi")) if (/Work in a new worktree/.test(await x.textContent())) { await x.click(); wt = true; break; }
  await p.waitForTimeout(1200);
  const wdir = await p.evaluate(() => S.draft.project);
  check("Work in a new worktree moves the new chat to its own checkout", wt && /\.meshpool[\\/]worktrees[\\/]task-1$/.test(wdir) && fs.existsSync(wdir), wdir);
  await p.evaluate(dir => { S.draft.project = dir; renderBar(); }, proj);
  // a chat in the project: the trust note, then a code block
  await p.evaluate(dir => newThread(dir), proj); await p.waitForTimeout(800);
  await send("show python code");
  check("trust note shows for a folder with its own hooks", await p.waitForSelector("#trustBar", {timeout: 10000}).then(() => true, () => false));
  check("code block has a language label and a Copy button", !!(await p.$(".msg-ai .codewrap .codehd [data-copycode]")) &&
        /python/.test(await p.textContent(".msg-ai .codewrap .codehd span")));
  check("code block is coloured", (await p.$$(".msg-ai .codewrap pre .tk-k")).length > 0 && (await p.$$(".msg-ai .codewrap pre .tk-c")).length > 0);
  await p.click(".msg-ai .codewrap [data-copycode]"); await p.waitForTimeout(300);
  const clip = await p.evaluate(() => navigator.clipboard.readText()).catch(e => "ERR " + e.message);
  check("Copy puts the code on the clipboard", /def add\(a, b\):/.test(clip) && !/```/.test(clip), clip);
  await p.screenshot({path: out + "/code-block.png"});
  // trust from the note
  await p.click("#trustYes"); await p.waitForSelector(".dlg, .scrim", {timeout: 5000}).catch(() => {});
  const okBtn = await p.$(".dlg button.primary, .dlg .btn.primary"); if (okBtn) await okBtn.click(); await p.waitForTimeout(800);
  const t = await p.evaluate(dir => api("/api/trust?root=" + encodeURIComponent(dir)), proj);
  check("Trust this folder trusts it", t.trusted === true && !(await p.$("#trustBar")), JSON.stringify(t));
  // thinking
  await send("think out loud"); await p.waitForTimeout(500);
  check("the built-in agent's reasoning shows in a Thinking block", /briefly/.test(await p.evaluate(() => [...document.querySelectorAll(".think")].map(x => x.textContent).join(" "))));
  // effort menu
  await p.click("#mPill"); await p.waitForTimeout(300);
  const eff = await p.$$eval("#ctx .mi", xs => xs.map(x => x.textContent));     // menus are #ctx
  const effBtn = await p.$$("#ctx .mi"); let opened = false;
  for (const x of effBtn) if (/Effort/.test(await x.textContent())) { await x.click(); opened = true; break; }
  await p.waitForTimeout(300);
  const high = await p.$$("#ctx .mi");
  let picked = false;
  for (const x of high) if (/^High/.test((await x.textContent()).trim())) { await x.click(); picked = true; break; }
  await p.waitForTimeout(500);
  check("Effort menu sets High for this chat", opened && picked && await p.evaluate(() => S.cur.view.effort === "high"), eff.join(" | "));
  await p.keyboard.press("Escape");
  // @ picker
  await p.click("#input"); await p.fill("#input", ""); await p.type("#input", "look at @note"); await p.waitForTimeout(900);
  const opts = await p.$$eval("#pop.inline .opt, #pop.inline button", xs => xs.map(x => x.textContent));
  check("@ opens a file picker with matching files", opts.some(o => /@notes\.md/.test(o)), opts.join(" | "));
  await p.keyboard.press("Enter"); await p.waitForTimeout(300);
  check("picking a file puts @path in the message", /look at @notes\.md /.test(await p.inputValue("#input")), await p.inputValue("#input"));
  // Up arrow recall
  await p.fill("#input", ""); await p.keyboard.press("ArrowUp"); await p.waitForTimeout(200);
  check("Up arrow in an empty box recalls the last sent message", (await p.inputValue("#input")) === "think out loud", await p.inputValue("#input"));
  await p.keyboard.press("ArrowUp"); await p.waitForTimeout(200);
  check("Up again goes further back", (await p.inputValue("#input")) === "show python code", await p.inputValue("#input"));
  await p.keyboard.press("ArrowDown"); await p.keyboard.press("ArrowDown"); await p.waitForTimeout(200);
  check("Down past the newest clears the box", (await p.inputValue("#input")) === "", await p.inputValue("#input"));
  // search inside files
  const r = await p.evaluate(dir => api(`/api/fs/search?root=${encodeURIComponent(dir)}&q=needle&content=1`), proj);
  check("search inside files finds the line", (r.lines || []).some(x => x.path === "notes.md" && x.line === 2), JSON.stringify(r).slice(0, 200));
  // a picture a reply names is shown in the reply
  fs.writeFileSync(path.join(proj, "pic.png"), Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DwnwEIGBgY/v//DwAh+gP9hW5j3QAAAABJRU5ErkJggg==", "base64"));
  const loaded = await p.evaluate(async () => {
    const d = document.createElement("div"); d.className = "msg-ai"; d.innerHTML = linkFiles(`Saved ${curRoot()}/pic.png for you.`); $("#col").appendChild(d);
    const im = d.querySelector("img.rimg"); if (!im) return "no img";
    await new Promise(r => { if (im.complete) r(); else { im.onload = r; im.onerror = r; } });
    return im.naturalWidth;
  });
  check("an image named in a reply is shown inline", loaded === 2, loaded);
  // the Git panel lists meshpool's worktrees, opens a chat in one, and takes one away after asking
  await p.evaluate(async () => { const d = document.createElement("div"); d.id = "wtPanelTest"; document.body.appendChild(d); await panelGit(d); });
  const wtRow = await p.waitForSelector("#wtBox li[data-wt]", {timeout: 15000}).then(() => true, () => false);
  check("Git panel lists the worktree with its branch and state", wtRow && /task-1[\s\S]*meshpool\/task-1[\s\S]*no changes/.test(await p.textContent("#wtBox")),
        wtRow ? await p.textContent("#wtBox") : "no rows");
  check("Git panel offers Stage per file and lists recent commits", !!(await p.$("#wtPanelTest [data-gst]")) &&
        /Recent commits/.test(await p.textContent("#wtPanelTest")));
  await p.click("#wtBox [data-wto]"); await p.waitForTimeout(400);
  check("New chat here starts a chat in the worktree", /\.meshpool[\\/]worktrees[\\/]task-1$/.test(await p.evaluate(() => S.draft.project)));
    await p.click("#wtBox [data-wtr]");
  await p.waitForSelector(".dlg [data-a='1']"); await p.click(".dlg [data-a='1']");
  await p.waitForFunction(() => /Worktrees \(0\)/.test(document.querySelector("#wtBox")?.textContent || ""), null, {timeout: 15000}).catch(() => {});
  check("Remove asks, then deletes the worktree folder", /Worktrees \(0\)/.test(await p.textContent("#wtBox")) && !fs.existsSync(wdir));
  await p.evaluate(() => document.getElementById("wtPanelTest")?.remove());
  // the chat menu hands an open chat to a new worktree
  await p.evaluate(dir => newThread(dir), proj); await p.waitForTimeout(500);
  await send("show python code");
  await p.waitForFunction(() => S.cur?.view?.state === "idle", null, {timeout: 60000}).catch(() => {});
  await p.evaluate(() => chatMenu({type: "click", clientX: 200, clientY: 200, currentTarget: document.querySelector("#ttl") || document.body, preventDefault() {}, stopPropagation() {}}));
  await p.waitForTimeout(300);
  const items = await p.$$eval("#ctx .mi", xs => xs.map(x => x.textContent));
  check("the chat menu offers Move to a new worktree", items.some(t => /Move to a new worktree/.test(t)), items.join(" | "));
  await p.keyboard.press("Escape");
  await p.evaluate(() => moveCheckout(S.cur.view.id, "worktree", false)); await p.waitForTimeout(1500);
  check("Move to a new worktree moves the open chat", /\.meshpool[\\/]worktrees[\\/]task-\d+$/.test(await p.evaluate(() => S.cur?.view?.workdir || "")),
        await p.evaluate(() => S.cur?.view?.workdir));
  // Branch under a reply opens a new chat with the history up to there
  const before = await p.evaluate(() => S.cur?.id);
  const fork = await p.$(".turnfoot [data-fork]");
  check("each reply has a Branch button", !!fork);
  if (fork) { await fork.click(); await p.waitForTimeout(1500); }
  check("Branch opens a new chat with the conversation so far", await p.evaluate(b4 => !!S.cur && S.cur.id !== b4 &&
        (S.cur.log || []).some(e => e.type === "user") && /\(branch\)/.test(S.cur.view?.title || ""), before),
        await p.evaluate(() => S.cur?.view?.title));
  // Settings, Permissions: saved rules with Remove, and Add
  await p.evaluate(() => openSettings("permissions")); await p.waitForSelector("#rulesBox #ruleAdd", {timeout: 15000});
  await p.selectOption("#ruleList", "ask"); await p.fill("#ruleText", "bash:git push*"); await p.click("#ruleAdd");
  await p.waitForSelector("#rulesBox [data-rule='bash:git push*']", {timeout: 10000}).catch(() => {});
  check("a rule can be added from Settings", !!(await p.$("#rulesBox [data-rrm='ask'][data-rule='bash:git push*']")));
  await p.click("#rulesBox [data-rule='bash:git push*']"); await p.waitForTimeout(600);
  check("and removed again", !(await p.$("#rulesBox [data-rule='bash:git push*']")));
  await p.keyboard.press("Escape");
  // a long paste becomes a chip; it goes with the message as a block
  const chip = await p.evaluate(() => {
    const inp = document.querySelector("#input"); inp.value = "";
    const dt = new DataTransfer(); dt.setData("text/plain", Array.from({length: 80}, (_, i) => "line " + i).join("\n"));
    inp.dispatchEvent(new ClipboardEvent("paste", {clipboardData: dt, bubbles: true, cancelable: true}));
    return [inp.value, document.querySelector("#attachRow")?.textContent || "", S.attachments.length];
  });
  check("a long paste becomes a chip, not a wall of text", chip[0] === "" && /Pasted text 1 \(80 lines\)/.test(chip[1]) && chip[2] === 1, chip.join(" | "));
  await p.evaluate(() => { S.attachments = []; renderAttach(); });
  // side by side: removed on the left, added on the right, unchanged on both
  const sbs = await p.evaluate(() => {
    const d = document.createElement("div");
    d.innerHTML = splitHTML("diff --meshpool a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,3 @@\n keep\n-old line\n+new line\n tail");
    const rows = [...d.querySelectorAll("tr")].map(r => [...r.querySelectorAll("td.tx")].map(c => c.className.replace("tx", "").trim() + ":" + c.textContent));
    return JSON.stringify(rows.filter(r => r.length));
  });
  check("side by side pairs old and new lines", sbs === JSON.stringify([[":keep", ":keep"], ["d:old line", "a:new line"], [":tail", ":tail"]]), sbs);
  // "# a fact" in the box saves it to memory instead of sending it
  await p.fill("#input", "# I deploy on Fridays"); await p.keyboard.press("Enter"); await p.waitForTimeout(800);
  const mem = await p.evaluate(async () => (await api("/api/memory")).facts?.map(f => f.text) || []);
  check("# in the message box saves to memory", mem.some(t => /deploy on Fridays/i.test(t)) && !(await p.inputValue("#input")), mem.slice(0, 5).join(" | "));
  // Try again keeps the earlier answer: 2/2 under the new reply, and the arrow goes back to 1/2
  await p.evaluate(dir => newThread(dir), proj); await p.waitForTimeout(500);
  await send("show python code");
  await p.waitForFunction(() => S.cur?.view?.state === "idle", null, {timeout: 60000}).catch(() => {});
  const ends0 = await p.evaluate(() => (S.cur.log || []).filter(e => e.type === "turn_end").length);
  await p.click(".racts010 [data-ra=retry]");
  await p.waitForFunction(n => S.cur?.view?.state === "idle" && (S.cur.log || []).filter(e => e.type === "turn_end").length >= n, ends0, {timeout: 60000}).catch(() => {});
  await p.waitForSelector(".ver010 .vn", {timeout: 15000}).catch(() => {});
  const v1 = await p.textContent(".ver010 .vn").catch(() => "");
  await p.click(".ver010 [data-vv='-1']").catch(() => {});
  const v2 = await p.textContent(".ver010 .vn").catch(() => "");
  check("Try again keeps the earlier answer, and the arrows switch versions", v1 === "2/2" && v2 === "1/2", `${v1} -> ${v2}`);
  // a CSV preview sorts by a column and filters rows; the label row stays on top
  fs.writeFileSync(path.join(proj, "people.csv"), "name,age\nzoe,31\nadam,9\nmia,120\n");
  await p.evaluate(dir => openPreview(dir, "people.csv"), proj);
  await p.waitForSelector(".pv-sheet table tbody tr", {timeout: 15000}).catch(() => {});
  const col = async () => p.$$eval(".pv-sheet tbody tr td:nth-child(3)", tds => tds.map(t => t.textContent).join(","));
  await p.click(".pv-sheet thead th.sortable:nth-child(3)"); await p.waitForTimeout(150);
  const up = await col();
  await p.click(".pv-sheet thead th.sortable:nth-child(3)"); await p.waitForTimeout(150);
  const down = await col();
  await p.fill(".pv-filter", "mi"); await p.waitForTimeout(400);
  const filtered = await col(), cnt = await p.textContent(".pv-count");
  check("a table preview sorts by a column (numbers as numbers) and filters rows", up === "age,9,31,120" &&
        down === "age,120,31,9" && filtered === "age,120" && /1 of 3/.test(cnt), `${up} | ${down} | ${filtered} | ${cnt}`);
  // a message added while the agent works can be taken back before it is read
  await p.evaluate(dir => newThread(dir), proj); await p.waitForTimeout(500);
  await p.fill("#input", "run the long task please"); await p.keyboard.press("Enter");
  await p.waitForFunction(() => ["working", "waiting"].includes(S.cur?.view?.state), null, {timeout: 20000}).catch(() => {});
  await p.waitForTimeout(1500);
  await p.fill("#input", "also update the changelog"); await p.keyboard.press("Enter");
  await p.waitForSelector(".msg-user.steer [data-takeback]", {timeout: 15000}).catch(() => {});
  await p.click(".msg-user.steer [data-takeback]").catch(() => {});
  await p.waitForSelector(".msg-user.steer.taken", {timeout: 15000}).catch(() => {});
  const tbv = await p.inputValue("#input");
  check("a message added while working can be taken back into the box", tbv === "also update the changelog" &&
        !!(await p.$(".msg-user.steer.taken")), tbv);
  await p.evaluate(() => post(`/api/agent/${S.cur.id}/stop`, {since: 1e9})).catch(() => {});
  await p.fill("#input", "");
  // Appearance: reasoning can be hidden and code colours turned off
  const ap = await p.evaluate(() => {
    store("thinking", "hide"); store("codecolor", "off"); applyAppearance();
    const col = document.createElement("div"); col.innerHTML = '<div class="think"></div><pre><code><span class="tk-k">def</span></code></pre>';
    document.body.appendChild(col);
    const r = [getComputedStyle(col.querySelector(".think")).display === "none",
               getComputedStyle(col.querySelector(".tk-k")).color === getComputedStyle(col.querySelector("code")).color];
    store("thinking", "show"); store("codecolor", "on"); applyAppearance(); col.remove(); return r;
  });
  check("reasoning can be hidden and code colours turned off", ap[0] && ap[1], JSON.stringify(ap));
  const rv = await p.evaluate(() => [reviewPrompt("deep --max 3 the parser"), reviewPrompt(""), simplifyPrompt("")]);
  check("/review takes a depth and a findings cap; /simplify exists", /At most 3 findings/.test(rv[0]) && /tests/.test(rv[0]) &&
        /Focus on: the parser/.test(rv[0]) && /At most 10 findings/.test(rv[1]) && /without changing what it does/.test(rv[2]), rv[0]);
  // a running command's output shows under its step as it comes, and folds away when it ends
  await p.evaluate(dir => newThread(dir), proj); await p.waitForTimeout(500);
  await p.fill("#input", "count slowly please"); await p.keyboard.press("Enter");
  const al = await p.waitForSelector("text=Allow all commands in this chat", {timeout: 10000}).catch(() => null);
  if (al) await al.click();
  const lv = await p.waitForSelector("pre.live", {timeout: 15000}).then(() => true, () => false);
  const lvText = lv ? await p.textContent("pre.live") : "";
  await p.waitForFunction(() => S.cur?.view?.state === "idle", null, {timeout: 30000}).catch(() => {});
  check("a running command's output shows live under its step", lv && /tick 1/.test(lvText) && !(await p.$("pre.live")), lvText);
  check("no page errors", !errs.length, errs.join(" | "));
  await b.close();
  const ok = results.filter(r => r[1]).length; console.log(`\n${ok}/${results.length} passed`);
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.error("CRASH", e.message); process.exit(2); });
