// UX jitter audit: how steady the chat screen is while it opens, streams, switches chats and opens panels.
// A regression check for docs/plan/CHAT_UX.md (phase acceptance numbers). Read-only for app code.
//
// Usage:
//   python3 testkit/mock_llm_long.py 8911 /tmp/mock.jsonl &      # mock model with a "stream long" rule (tools + long Markdown)
//   (cd app && MESHPOOL_HOME=/tmp/mp-ux HOME=/tmp/mp-fakehome python -m meshpool_harness ui --port 8799 --no-browser &)
//   node testkit/ux_jitter_audit.js 8799 /path/to/a/project/folder [--json out.json] [--strict]
//
// The app's home needs MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1 and MESHPOOL_API_KEY=test-key-1234 in
// <home>/meshpool.config (see run_all.sh). Env: VP=1440x900, WORD_MS (informational), KEEP=1 keeps the setup chats.
//
// Prints, per scenario: CLS (layout-shift sum, and the worst shifting elements), long tasks (count / total / max ms),
// DOM mutations per animation frame (mean / p95 / max), scroll jumps and shrinks of the conversation, how often the
// whole message list was rebuilt, how many times landmarks (composer, top bar, sidebar, column width) changed
// geometry, and how often the transcript grew (growth events per second, a smoothness figure). Then PASS/FAIL against
// THRESHOLDS. Exit code 1 with --strict when any check fails. Written against the DOM of app.js at the time of the
// audit (ids #conv #col #composer #topbar #sideList #working; S, setAppMode, newThread, openThread are globals).
const {chromium} = require("playwright");
const fs = require("fs");
const args = process.argv.slice(2);
const flag = n => args.includes(n);
const val = n => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : null; };
const pos = args.filter((a, i) => !a.startsWith("--") && args[i - 1] !== "--json");
const [port, proj] = pos;
if (!port || !proj) { console.error("usage: node ux_jitter_audit.js PORT PROJECT_DIR [--json FILE] [--strict]"); process.exit(2); }
const BASE = `http://127.0.0.1:${port}`;

// Targets for the finished design (CHAT_UX.md phase acceptance). "open" is the first paint of the app.
const THRESHOLDS = {
  open:      {cls: 0.02, longMaxMs: 200},
  stream:    {cls: 0.02, backJumps: 0, fwdJumps: 0, shrinks: 0, listRebuilds: 0, maxMut: 60, minGrowthPerSec: 20, landmarkMoves: 0, longMaxMs: 100},
  switch:    {cls: 0.02, blankFrames: 2, settleLayouts: 2, landmarkMoves: 0, longMaxMs: 150},
  panels:    {cls: 0.02, landmarkMoves: 2, longMaxMs: 150},
};

const INIT = () => {
  const W = window;
  W.__m = {cls: 0, shifts: [], long: [], muts: [], pending: 0, listRebuilds: 0, indicatorMoves: 0, liveRewrites: 0,
           frames: [], boxes: {}, regions: {}, reqs: {list: 0, thread: 0, other: 0}};
  try { localStorage.setItem("mp_permsAsked", "1"); } catch (e) {}
  const name = n => !n ? "?" : (n.id ? "#" + n.id : n.className && typeof n.className === "string" ? n.nodeName.toLowerCase() + "." + n.className.split(" ")[0] : n.nodeName.toLowerCase());
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) { W.__m.cls += e.value; W.__m.shifts.push([Math.round(e.startTime), +e.value.toFixed(4), (e.sources || []).map(s => name(s.node)).join(",")]); } }).observe({type: "layout-shift", buffered: true}); } catch (e) {}
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) W.__m.long.push(Math.round(e.duration)); }).observe({type: "longtask", buffered: true}); } catch (e) {}
  // landmarks sampled every frame once the app is up; the mutation observer counts records and list rebuilds
  const LM = ["#composer", "#topbar", "#sideList", ".side", "#conv", "#colw", "#panel"];
  const rect = s => { if (s === "#colw") { const c = document.querySelector("#col"); if (!c) return "-"; const b = c.getBoundingClientRect(); return [b.x, b.width].map(Math.round).join(","); } const e = document.querySelector(s); if (!e || e.hidden) return "-"; const b = e.getBoundingClientRect(); return [b.x, b.y, b.width, b.height].map(Math.round).join(","); };
  W.__m.sig = () => Object.fromEntries(LM.map(s => [s, rect(s)]));
  let last = 0, prevSt = null, prevSh = null;
  const tick = t => {
    const m = W.__m;
    if (m.run) {
      const conv = document.querySelector("#conv");
      const st = conv ? conv.scrollTop : 0, sh = conv ? conv.scrollHeight : 0, ch = conv ? conv.clientHeight : 0;
      const col = document.querySelector("#col"); m.frames.push({t: Math.round(t), muts: m.pending, st, sh, gap: sh - st - ch, kids: col ? col.children.length : -1});
      m.pending = 0;
      const sig = m.sig();
      for (const k in sig) (m.boxes[k] = m.boxes[k] || new Set()).add(sig[k]);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  document.addEventListener("DOMContentLoaded", () => {
    new MutationObserver(recs => {
      const m = W.__m; if (!m.run) return;
      for (const r of recs) {
        m.pending += 1 + r.addedNodes.length + r.removedNodes.length;
        if (r.type === "characterData") m.pending += 0;
        const col = document.querySelector("#col");
        for (const id of ["cbar", "topbar", "sideList", "panel", "hintline", "attachRow"]) { const el = document.getElementById(id); if (el && (r.target === el || el.contains(r.target)) && [...r.removedNodes, ...r.addedNodes].some(n => n.nodeType === 1)) m.regions[id] = (m.regions[id] || 0) + 1; }
        if (r.target === col) {
          const rem = [...r.removedNodes].filter(n => n.nodeType === 1 && n.id !== "working");
          if (rem.length >= 2) m.listRebuilds++;          // several messages removed at once = the list was rebuilt
          if ([...r.removedNodes, ...r.addedNodes].some(n => n.nodeType === 1 && n.id === "working")) m.indicatorMoves++;
        }
        if (r.target.nodeType === 1 && r.target.classList && r.target.classList.contains("msg-ai") && r.removedNodes.length) m.liveRewrites++;
      }
    }).observe(document.body, {childList: true, subtree: true, characterData: true});
  });
};

const stats = a => { if (!a.length) return {mean: 0, p95: 0, max: 0}; const s = [...a].sort((x, y) => x - y); return {mean: +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(1), p95: s[Math.floor(s.length * 0.95)], max: s[s.length - 1]}; };

(async () => {
  const b = await chromium.launch();
  const [vw, vh] = (process.env.VP || "1440x900").split("x").map(Number);
  const p = await b.newPage({viewport: {width: vw, height: vh}});
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.addInitScript(INIT);
  const api = (path, body) => p.evaluate(([a, c]) => c ? post(a, c) : api(a), [path, body]);
  const reqs = {list: 0, thread: 0, other: 0, bytes: 0};
  p.on("response", async r => { const u = r.url().replace(BASE, ""); if (!u.startsWith("/api/")) return; const k = /^\/api\/agent\/[\w-]+\?/.test(u) ? "thread" : /^\/api\/agent($|\?)/.test(u) ? "list" : "other"; reqs[k]++; });
  const results = [], report = {};
  const check = (name, ok, detail) => { results.push([name, ok]); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? "  " + detail : "")); };

  // ------------------------------------------------------------------ (a) open the app
  const tOpen = Date.now();
  await p.evaluate(() => { window.__m && (window.__m.run = true); });   // before navigation: no-op, the init script re-creates it
  await p.goto(BASE + "/"); await p.evaluate(() => { window.__m.run = true; });
  await p.waitForSelector("#input", {timeout: 30000});
  await p.waitForTimeout(2500);                                           // fonts, first loads and the sidebar settle
  const grab = async (label, ms) => {
    const m = await p.evaluate(() => { const m = window.__m; m.run = false; return {cls: m.cls, shifts: m.shifts, long: m.long, frames: m.frames, boxes: Object.fromEntries(Object.entries(m.boxes).map(([k, v]) => [k, [...v].length])), listRebuilds: m.listRebuilds, indicatorMoves: m.indicatorMoves, regionRebuilds: m.regions, liveRewrites: m.liveRewrites, regions: m.regions}; });
    const muts = m.frames.map(f => f.muts), mm = stats(muts);
    // scroll: jumps forward bigger than a normal chunk, any step back, shrink of the transcript, lag behind the bottom
    let fwd = 0, back = 0, shr = 0, maxGap = 0, grow = 0, growPx = [], jumps = [], blank = 0, active = 0; const heights = new Set();
    for (let i = 1; i < m.frames.length; i++) {
      const a = m.frames[i - 1], c = m.frames[i];
      const dst = c.st - a.st, dsh = c.sh - a.sh;
      if (dst > 150) { fwd++; jumps.push([c.t, "scroll +" + dst, "height " + (dsh >= 0 ? "+" : "") + dsh]); } if (dst < -4) { back++; jumps.push([c.t, "scroll " + dst, "height " + (dsh >= 0 ? "+" : "") + dsh]); }
      if (dsh < -20) shr++;
      if (c.kids === 0) blank++; if (c.muts > 0) active++; heights.add(c.sh);
      if (dsh > 0) { grow++; growPx.push(dsh); }
      if (c.gap > maxGap) maxGap = c.gap;
    }
    const secs = Math.max(0.001, (m.frames.length ? (m.frames[m.frames.length - 1].t - m.frames[0].t) : ms) / 1000);
    const med = growPx.length ? growPx.sort((x, y) => x - y)[Math.floor(growPx.length / 2)] : 0;
    const out = {cls: +m.cls.toFixed(4), topShifts: m.shifts.sort((x, y) => y[1] - x[1]).slice(0, 4), longTasks: {n: m.long.length, totalMs: m.long.reduce((x, y) => x + y, 0), maxMs: Math.max(0, ...m.long)},
      mutPerFrame: mm, activeFramesPerSec: +(active / secs).toFixed(1), blankFrames: blank, distinctHeights: heights.size, jumps: jumps.slice(0, 8), fwdJumps: fwd, backJumps: back, shrinks: shr, maxGapPx: maxGap, growthPerSec: +(grow / secs).toFixed(1), medianGrowthPx: med,
      listRebuilds: m.listRebuilds, indicatorMoves: m.indicatorMoves, regionRebuilds: m.regions, liveRewrites: m.liveRewrites, landmarkGeometries: m.boxes, frames: m.frames.length, seconds: +secs.toFixed(1)};
    report[label] = out;
    console.log(`\n== ${label} (${out.seconds}s, ${out.frames} frames)`);
    console.log(`   CLS ${out.cls}  top shifts: ${JSON.stringify(out.topShifts)}`);
    console.log(`   long tasks: ${out.longTasks.n} (total ${out.longTasks.totalMs} ms, max ${out.longTasks.maxMs} ms)`);
    console.log(`   DOM mutations/frame: mean ${mm.mean}  p95 ${mm.p95}  max ${mm.max};  message-list rebuilds ${out.listRebuilds};  #working moves ${out.indicatorMoves};  live-message rewrites ${out.liveRewrites}`);
    console.log(`   scroll: forward jumps>150px ${fwd}, backward steps ${back}, transcript shrinks>20px ${shr}, max gap below bottom ${maxGap}px;  growth events/s ${out.growthPerSec} (median ${med}px)`);
    console.log(`   element rebuilds by region (header, composer bar, sidebar, panel...): ${JSON.stringify(m.regions)}`);
    if (jumps.length) console.log(`   scroll events [t ms, scroll, height]: ${JSON.stringify(jumps.slice(0, 8))}`);
    console.log(`   landmark geometries seen (1 = never moved): ${JSON.stringify(out.landmarkGeometries)}`);
    return out;
  };
  const open = await grab("a. open the app", 2500);
  const T = THRESHOLDS;
  check("open: CLS", open.cls <= T.open.cls, `${open.cls} <= ${T.open.cls}`);
  check("open: no long task over " + T.open.longMaxMs + " ms", open.longTasks.maxMs <= T.open.longMaxMs, `max ${open.longTasks.maxMs}`);

  console.log("setup chats..."); // setup: a project and finished chats (not measured)
  await api("/api/projects", {action: "add", path: proj});
  await p.evaluate(() => loadProjects());
  const ids = [];
  for (let i = 0; i < 3; i++) {
    const v = await api("/api/agent", {workdir: proj, harness: "meshpool-work", kind: "cowork", mode: "auto", task: "stream long setup " + i});
    ids.push(v.id); await api(`/api/agent/${v.id}/send`, {text: "stream long setup " + i});
  }
  await p.waitForFunction(ids => Promise.all(ids.map(id => api("/api/agent/" + id + "?since=0").then(v => v.state))).then(s => s.every(x => x === "idle")), ids, {timeout: 120000, polling: 2000}).catch(() => {});
  await p.evaluate(() => loadThreads()); await p.waitForTimeout(800);

  console.log("stream scenario..."); // (b) send a message, stream a long reply with tool calls
  await p.evaluate(path => { setAppMode("cowork"); newThread(); S.draft.project = path; S.draft.mode = "auto"; renderBar(); }, proj);   // full access: no approval card stops the run
  await p.waitForTimeout(600);
  await p.fill("#input", "stream long please");
  await p.evaluate(() => { const m = window.__m; m.cls = 0; m.shifts = []; m.long = []; m.frames = []; m.boxes = {}; m.listRebuilds = m.indicatorMoves = m.liveRewrites = 0; m.regions = {}; m.run = true; });
  const rq0 = {...reqs}; const tS = Date.now();
  await p.keyboard.press("Enter");
  await p.waitForFunction(() => S.cur?.view && ["idle", "done", "saved"].includes(S.cur.view.state) && S.cur.view.events?.length !== undefined && !document.querySelector("#working") && document.querySelector(".msg-ai"), null, {timeout: 120000, polling: 250}).catch(() => {});
  await p.waitForTimeout(1500);
  const stream = await grab("b. send + stream a long reply with 3 tool calls", Date.now() - tS);
  const dur = (Date.now() - tS) / 1000;
  const polls = {thread: reqs.thread - rq0.thread, list: reqs.list - rq0.list, other: reqs.other - rq0.other};
  report.requestsDuringStream = {seconds: +dur.toFixed(1), ...polls, threadPollsPerSec: +(polls.thread / dur).toFixed(2)};
  console.log(`   requests: ${JSON.stringify(report.requestsDuringStream)}`);
  const lm = Object.entries(stream.landmarkGeometries).filter(([k]) => ["#composer", "#topbar", "#sideList", ".side"].includes(k)).reduce((n, [, v]) => n + (v - 1), 0);
  const colW = (await p.evaluate(() => window.__m.boxes["#colw"] ? [...window.__m.boxes["#colw"]].map(s => s.split(",")[1]) : [])).filter((x, i, a) => a.indexOf(x) === i);
  report.colWidths = colW;
  check("stream: CLS", stream.cls <= T.stream.cls, `${stream.cls} <= ${T.stream.cls}`);
  check("stream: no backward scroll steps", stream.backJumps <= T.stream.backJumps, `${stream.backJumps}`);
  check("stream: no forward scroll jumps > 150px", stream.fwdJumps <= T.stream.fwdJumps, `${stream.fwdJumps}`);
  check("stream: transcript never shrinks (no raw-to-Markdown swap)", stream.shrinks <= T.stream.shrinks, `${stream.shrinks}`);
  check("stream: message list not rebuilt", stream.listRebuilds <= T.stream.listRebuilds, `${stream.listRebuilds}`);
  check("stream: no frame with more than " + T.stream.maxMut + " DOM mutations (updates are paced, not dumped)", stream.mutPerFrame.max <= T.stream.maxMut, `max ${stream.mutPerFrame.max}, active frames ${stream.activeFramesPerSec}/s`);
  check("stream: smooth growth (>= " + T.stream.minGrowthPerSec + " growth events/s)", stream.growthPerSec >= T.stream.minGrowthPerSec, `${stream.growthPerSec}/s, median ${stream.medianGrowthPx}px`);
  check("stream: composer, top bar and sidebar do not move", lm <= T.stream.landmarkMoves, `${lm} geometry changes`);
  check("stream: no long task over " + T.stream.longMaxMs + " ms", stream.longTasks.maxMs <= T.stream.longMaxMs, `max ${stream.longTasks.maxMs}`);

  // ------------------------------------------------------------------ (c) switch chats
  await p.evaluate(() => loadThreads()); await p.waitForTimeout(600);
  const tids = await p.evaluate(() => S.threads.filter(t => !t.archived).slice(0, 4).map(t => t.id));
  await p.evaluate(() => { const m = window.__m; m.cls = 0; m.shifts = []; m.long = []; m.frames = []; m.boxes = {}; m.listRebuilds = m.indicatorMoves = m.liveRewrites = 0; m.regions = {}; m.run = true; });
  const tSw = Date.now();
  for (let r = 0; r < 2; r++) for (const id of tids) { await p.click(`.th[data-id="${id}"]`); await p.waitForTimeout(900); }
  const sw = await grab("c. switch between chats (8 switches)", Date.now() - tSw);
  sw.blankFramesPerSwitch = +(sw.blankFrames / 8).toFixed(1); sw.layoutsPerSwitch = +(sw.distinctHeights / 8).toFixed(1);
  console.log(`   blank frames/switch ${sw.blankFramesPerSwitch}, distinct transcript heights/switch ${sw.layoutsPerSwitch}`);
  const swLm = Object.entries(sw.landmarkGeometries).filter(([k]) => ["#composer", "#topbar", ".side"].includes(k)).reduce((n, [, v]) => n + (v - 1), 0);
  check("switch: CLS", sw.cls <= T.switch.cls, `${sw.cls}`);
  check("switch: no blank frames while a chat loads (<= " + T.switch.blankFrames + " per switch)", sw.blankFramesPerSwitch <= T.switch.blankFrames, `${sw.blankFramesPerSwitch} per switch`);
  check("switch: a chat appears in one layout (<= " + T.switch.settleLayouts + " distinct heights per switch)", sw.layoutsPerSwitch <= T.switch.settleLayouts, `${sw.layoutsPerSwitch} per switch`);
  check("switch: composer, top bar and sidebar do not move", swLm <= T.switch.landmarkMoves, `${swLm}`);
  check("switch: no long task over " + T.switch.longMaxMs + " ms", sw.longTasks.maxMs <= T.switch.longMaxMs, `max ${sw.longTasks.maxMs}`);

  // ------------------------------------------------------------------ (d) sidebar and panels
  await p.evaluate(() => { const m = window.__m; m.cls = 0; m.shifts = []; m.long = []; m.frames = []; m.boxes = {}; m.listRebuilds = m.indicatorMoves = m.liveRewrites = 0; m.regions = {}; m.run = true; });
  const tP = Date.now();
  const clickIf = async sel => { const e = await p.$(sel); if (e && await e.isVisible()) { await e.click(); await p.waitForTimeout(700); } };
  await clickIf("#hideSide"); await clickIf("#showSide");
  for (const k of ["progress", "outputs", "session"]) { await clickIf(`[data-panel="${k}"]`); await clickIf(`[data-panel="${k}"]`); }
  await clickIf("#openSettings"); await p.keyboard.press("Escape"); await p.waitForTimeout(500);
  await clickIf("#mPill"); await p.keyboard.press("Escape"); await p.waitForTimeout(300);
  await clickIf("#hPill"); await p.keyboard.press("Escape"); await p.waitForTimeout(300);
  const pn = await grab("d. sidebar toggle, side panels, settings, pickers", Date.now() - tP);
  check("panels: CLS", pn.cls <= T.panels.cls, `${pn.cls}`);
  check("panels: no long task over " + T.panels.longMaxMs + " ms", pn.longTasks.maxMs <= T.panels.longMaxMs, `max ${pn.longTasks.maxMs}`);

  check("no page errors", !errs.length, errs.join(" | "));
  report.checks = results.map(([n, ok]) => ({name: n, ok}));
  if (val("--json")) fs.writeFileSync(val("--json"), JSON.stringify(report, null, 1));
  const bad = results.filter(r => !r[1]).length;
  console.log(`\n${results.length - bad}/${results.length} checks pass`);
  await b.close();
  if (flag("--strict") && bad) process.exit(1);
})();
