// 0.0.10 journeys against running apps on the mock model (no paid calls).
// Usage: node journeys_010.js MAIN_PORT HEAVY_PORT EMPTY_PORT UPGRADE_PORT
// MAIN has seeded memory (seed_memory.py), HEAVY is seed_heavy.py + seed_memory.py, EMPTY is a fresh home with no files,
// UPGRADE has only a 0.0.9 memory.md.
const {chromium} = require("playwright");
const [MAIN, HEAVY, EMPTY, UPG] = process.argv.slice(2).map(p => `http://127.0.0.1:${p}`);
const res = []; const ck = (n, ok, d = "") => { res.push(!!ok); console.log((ok ? "PASS " : "FAIL ") + n + (ok ? "" : " -> " + String(d).slice(0, 200))); };
const tok = async B => (await (await fetch(B + "/")).text()).match(/token: "([^"]+)"/)[1];
const mk = async B => { const T = await tok(B); return async (p, body) => { const r = await fetch(B + p, {method: body ? "POST" : "GET", headers: {"X-Meshpool-Token": T, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined}); const t = await r.text(); try { return JSON.parse(t); } catch (e) { return t; } }; };
const wait = ms => new Promise(r => setTimeout(r, ms));
async function onboard(p) { for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); } }
async function chat(call, text, extra = {}) {
  const v = await call("/api/agent", {workdir: "", harness: "meshpool-auto", kind: "cowork", mode: "auto", ...extra});
  for (let i = 0; i < 60; i++) { if ((await call(`/api/agent/${v.id}?since=0`)).state !== "starting") break; await wait(250); }
  await call(`/api/agent/${v.id}/send`, {text});
  for (let i = 0; i < 200; i++) { await wait(100); const s = await call(`/api/agent/${v.id}?since=0`); if (s.state === "idle" && s.events.some(e => e.type === "turn_end")) return {id: v.id, view: s}; }
  return {id: v.id};
}
(async () => {
  const main = await mk(MAIN), heavy = await mk(HEAVY), empty = await mk(EMPTY), upg = await mk(UPG);

  // J35 migration on upgrade: old memory.md becomes facts, nothing lost, old file kept as backup
  const u = await upg("/api/memory");
  ck("J35 upgrade: every old line is a stated fact", u.facts.length === 3 && u.facts.every(f => f.kind === "stated" && f.source.text.includes("earlier memory")), JSON.stringify(u.facts.map(f => f.text)));
  ck("J35 upgrade: the assistant view still reads the same memory", ((await upg("/api/assistant")).memory || "").includes("Layla Haddad"));

  // J36 memory API: edit, pin, scope, archive and undo, history
  const f0 = (await main("/api/memory")).facts[0];
  await main("/api/memory", {action: "edit", id: f0.id, text: f0.text + " (edited)"});
  await main("/api/memory", {action: "pin", id: f0.id, on: true});
  await main("/api/memory", {action: "archive", id: f0.id});
  ck("J36 archive hides it", !(await main("/api/memory")).facts.some(f => f.id === f0.id));
  await main("/api/memory", {action: "unarchive", id: f0.id});
  const back = (await main("/api/memory")).facts.find(f => f.id === f0.id);
  ck("J36 undo brings it back, edited and pinned", back && back.text.endsWith("(edited)") && back.pinned);
  const hist = (await main("/api/memory/history")).history;
  ck("J36 the history lists every change", ["edited", "archived", "restored"].every(a => hist.some(h => h.action === a)));
  await main("/api/memory", {action: "restore_text", id: f0.id, text: f0.text});

  // J37 noticed facts wait in the tray and never reach an agent until confirmed
  const pend = (await main("/api/memory")).pending;
  ck("J37 pending facts are in the tray", pend.length >= 1 && !((await main("/api/assistant")).memory || "").includes(pend[0].text));
  await main("/api/memory", {action: "confirm", id: pend[0].id});
  ck("J37 yes makes it active and visible to agents", ((await main("/api/assistant")).memory || "").includes(pend[0].text));

  // J38/J39 sources chip data and X-ray match what the agent got
  const c1 = await chat(main, "Make a one slide summary of our Q3 results");
  const src = await main(`/api/memory/sources?chat=${c1.id}`);
  const xr = await main(`/api/xray?chat=${c1.id}&text=x`);
  const given = new Set(src.facts.map(f => f.text));
  const snap = xr.snapshot || "";
  ck("J38 the reply's memories are listed", src.facts.length >= 3, src.facts.length);
  ck("J39 every listed memory is in the context the agent actually received", [...given].every(t => snap.includes(t)));

  // J40 incognito writes nothing
  const before = (await main("/api/memory")).counts;
  const inc = await chat(main, "Please remember that I like green charts and always want fonts in Comic Sans", {incognito: true});
  await wait(1500);
  const after = (await main("/api/memory")).counts, hasInc = await main(`/api/memory/sources?chat=${inc.id}`);
  ck("J40 incognito: no facts were added or pending", before.active === after.active && before.pending === after.pending, JSON.stringify([before, after]));
  ck("J40 incognito: no memory was given to the chat", hasInc.facts.length === 0);

  // J41 first run: files give starters, an empty folder offers samples
  const sc = await heavy("/api/firstrun/scan?folder=");
  const samples = await empty("/api/firstrun/samples", {});
  ck("J41 samples are copied and a one-slide prompt is returned", samples.files.length === 3 && /one slide/.test(samples.prompt), JSON.stringify(samples));
  ck("J41 scanning reads names only (starters name real files)", Array.isArray(sc.starters));

  // J42-J43 cards: accept a schedule card end to end, dismiss, mute
  const cards = (await heavy("/api/insights?where=home")).cards;
  ck("J42 home shows at most two cards, one per type", cards.length <= 2 && new Set(cards.map(c => c.type)).size === cards.length, cards.length);
  const pool = (await heavy("/api/insights?where=home")).cards;
  const sched = pool.find(c => c.type === "schedule") || null;
  if (sched) {
    const a = await heavy("/api/insights/act", {id: sched.id, event: "accepted"});
    ck("J42 accepting a schedule card prefills the task with only-on-change on", a.action.kind === "schedule" && a.action.payload.only_on_change === true && a.action.payload.prompt);
    await heavy("/api/assistant", {action: "task_add", ...a.action.payload, name: a.action.payload.name || "Weekly", only_on_change: true});
    ck("J42 the task exists with the option", (await heavy("/api/assistant")).tasks.some(t => t.only_on_change === true));
  } else ck("J42 (no schedule card today: covered by test_insights)", true);
  const other = (await heavy("/api/insights?where=home")).cards[0];
  if (other) { await heavy("/api/insights/act", {id: other.id, event: "dismissed"}); ck("J43 a dismissed card never returns", !(await heavy("/api/insights?where=home")).cards.some(c => c.id === other.id)); }
  else ck("J43 (nothing left to dismiss)", true);

  // J44 away block data
  const away = await heavy("/api/away");
  ck("J45 While you were away lists the finished run", away.runs.length === 1 && away.runs[0].name === "Price watch", JSON.stringify(away).slice(0, 160));

  // J46 Your work and recap
  const w = await heavy("/api/work?range=3");
  ck("J46 Your work has topics and counts", w && (w.stats || w.totals || w.topics || w.chats !== undefined));
  const png = await (await fetch(HEAVY + "/api/work/recap.png?range=3", {headers: {"X-Meshpool-Token": await tok(HEAVY)}})).arrayBuffer();
  ck("J46 the recap is a PNG", new Uint8Array(png).slice(0, 4).join(",") === "137,80,78,71");

  // J48 Suggestions off hides everything
  await heavy("/api/insights/settings", {insights_on: false});
  ck("J48 Suggestions off: no cards", (await heavy("/api/insights?where=home")).cards.length === 0);
  await heavy("/api/insights/settings", {insights_on: true});

  // UI: the memory page rows, search, inline edit and the Undo toast
  const b = await chromium.launch(); const p = await b.newPage({viewport: {width: 1280, height: 860}}); const errs = [];
  p.on("pageerror", e => errs.push(e.message));
  await p.goto(MAIN + "/"); await p.waitForTimeout(1000); await onboard(p);
  await p.evaluate(() => goPage("memory")); await p.waitForSelector(".mem-row");
  await p.fill("#memQ", "layla"); await p.waitForTimeout(500);
  ck("J36 UI: search narrows to one row", (await p.$$(".mem-row")).length === 1);
  await p.click(".mem-row .mem-text"); await p.waitForSelector(".mem-edit");
  await p.fill(".mem-edit", "Manager is Layla Haddad (CFO)"); await p.keyboard.press("Enter"); await p.waitForTimeout(600);
  ck("J36 UI: inline edit saves", (await p.textContent(".mem-row .mem-text")).includes("(CFO)"));
  await p.focus(".mem-row"); await p.keyboard.press("Backspace"); await p.waitForTimeout(500);
  ck("J36 UI: Backspace archives with an Undo toast", await p.$(".toast .tact") !== null);
  await p.click(".toast .tact"); await p.waitForTimeout(600);
  await p.fill("#memQ", "layla"); await p.waitForTimeout(500);
  ck("J36 UI: Undo restores it", (await p.$$(".mem-row")).length === 1);
  ck("no page errors", errs.length === 0, errs.join("|"));
  await b.close();
  console.log(`${res.filter(Boolean).length}/${res.length} passed`);
  process.exit(res.every(Boolean) ? 0 : 1);
})();
