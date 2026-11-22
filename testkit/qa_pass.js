// A scripted /qa pass: every screen at desktop and phone width, light and dark. Reports page errors, failed requests,
// horizontal overflow, elements outside the screen, tiny tap targets and controls with no name. Screenshots go to OUTDIR.
// Usage: node qa_pass.js PORT OUTDIR [--shots]
const {chromium} = require("playwright");
const [port, out, shots] = process.argv.slice(2);
const VIEWS = [["desktop", 1280, 860], ["phone", 390, 844]];
const found = [];
const note = (view, theme, state, kind, detail) => found.push({view, theme, state, kind, detail: String(detail).slice(0, 220)});

async function audit(p, view, theme, state) {
  const r = await p.evaluate(() => {
    const vw = innerWidth, o = {};
    o.overflowX = document.scrollingElement.scrollWidth - vw;
    const vis = e => { const s = getComputedStyle(e), b = e.getBoundingClientRect(); return s.visibility !== "hidden" && s.display !== "none" && b.width > 0 && b.height > 0 && +s.opacity > 0; };
    const clipped = e => { for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) { const s = getComputedStyle(a); if (/(hidden|auto|scroll)/.test(s.overflowX) && a.getBoundingClientRect().right <= vw + 1) return true; } return false; };
    o.outside = [...document.querySelectorAll("body *")].filter(e => vis(e) && e.getBoundingClientRect().right > vw + 2 && !clipped(e) && !e.closest("[hidden],.toasts")).slice(0, 4)
      .map(e => (e.id ? "#" + e.id : e.className ? "." + String(e.className).split(" ")[0] : e.tagName) + " " + Math.round(e.getBoundingClientRect().right));
    const tiny = [...document.querySelectorAll("button, a[href], input, select, textarea, [role=button], [role=tab]")].filter(e => vis(e) && !e.disabled && e.type !== "hidden" && e.type !== "checkbox" && e.type !== "radio" && !e.closest("label") && !(e.tagName === "A" && getComputedStyle(e).display === "inline") && (e.getBoundingClientRect().width < 24 || e.getBoundingClientRect().height < 24));
    o.tiny = tiny.length; o.tinySample = tiny.slice(0, 3).map(e => (e.id ? "#" + e.id : e.className ? "." + String(e.className).split(" ")[0] : e.tagName) + " " + Math.round(e.getBoundingClientRect().width) + "x" + Math.round(e.getBoundingClientRect().height));
    const nameless = [...document.querySelectorAll("button, [role=button], input:not([type=hidden]), select, textarea, a[href]")].filter(e => vis(e) && !(e.textContent || "").trim() && !e.getAttribute("aria-label") && !e.getAttribute("title") && !e.getAttribute("aria-labelledby") && !e.getAttribute("placeholder") && !(e.labels && e.labels.length) && !e.closest("label") && !e.querySelector("img[alt]"));
    o.nameless = nameless.length; o.namelessSample = nameless.slice(0, 3).map(e => (e.id ? "#" + e.id : e.className ? "." + String(e.className).split(" ")[0] : e.tagName));
    o.noAlt = [...document.querySelectorAll("img:not([alt])")].filter(vis).length;
    return o;
  });
  if (r.overflowX > 1) note(view, theme, state, "page scrolls sideways", r.overflowX + "px");
  if (r.outside.length) note(view, theme, state, "element past the screen edge", r.outside.join("; "));
  if (r.tiny) note(view, theme, state, "tap target under 24px", r.tiny + ": " + r.tinySample.join("; "));
  if (r.nameless) note(view, theme, state, "control with no name", r.nameless + ": " + r.namelessSample.join("; "));
  if (r.noAlt) note(view, theme, state, "image without alt", r.noAlt);
}

(async () => {
  const b = await chromium.launch();
  const errs = [];
  for (const [view, w, h] of VIEWS) for (const theme of ["light", "dark"]) {
    const ctx = await b.newContext({viewport: {width: w, height: h}, deviceScaleFactor: 1, hasTouch: view === "phone"});
    const p = await ctx.newPage();
    p.on("pageerror", e => errs.push(`${view}/${theme} pageerror: ${e.message}`));
    p.on("console", m => { if (m.type() === "error" && !/favicon|404/.test(m.text())) errs.push(`${view}/${theme} console: ${m.text().slice(0, 160)}`); });
    p.on("response", r => { if (r.url().includes("/api/") && r.status() >= 500) errs.push(`${view}/${theme} HTTP ${r.status()} ${r.url().split("?")[0]}`); });
    await p.goto(`http://127.0.0.1:${port}/`); await p.waitForTimeout(1200);
    for (let i = 0; i < 5; i++) { const c = await p.$('button:has-text("Continue"), button:has-text("Start using Meshpool")'); if (!c) break; await c.click(); await p.waitForTimeout(500); }
    if (theme === "dark") await p.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    const states = [
      ["home", () => p.evaluate(() => newThread())],
      ["memory", () => p.evaluate(() => { MEM.tab = "memory"; goPage("memory"); })],
      ["memory-projects", () => p.evaluate(() => { MEM.tab = "projects"; goPage("memory"); })],
      ["memory-history", () => p.evaluate(() => { MEM.tab = "history"; goPage("memory"); })],
      ["memory-review", () => p.evaluate(() => { MEM.tab = "review"; goPage("memory"); })],
      ["your-work", () => p.evaluate(() => goPage("work"))],
      ["scheduled", () => p.evaluate(() => goPage("scheduled"))],
      ["projects", () => p.evaluate(() => goPage("projects"))],
      ["artifacts", () => p.evaluate(() => goPage("artifacts"))],
      ["plugins", () => p.evaluate(() => goPage("plugins"))],
      ["code-home", async () => { await p.evaluate(() => setAppMode3 ? setAppMode3("code") : 0); }],
      ["chat-mode-again", async () => { await p.evaluate(() => setAppMode3("chat")); await p.evaluate(() => newThread()); }],
      ...["profile", "appearance", "personal", "caps", "suggest", "notify", "connectors", "skills", "mcp", "general", "models", "harnesses", "scheduled", "company", "about", "data", "permissions", "import"].map(s => ["settings-" + s, async () => { await p.evaluate(() => closeSettings && closeSettings()); await p.evaluate(sec => openSettings(sec), s); }]),
    ];
    for (const [name, go] of states) {
      try { await go(); await p.waitForTimeout(700); } catch (e) { note(view, theme, name, "could not open", e.message); continue; }
      if (name.startsWith("settings-")) await p.waitForSelector(".modal", {timeout: 4000}).catch(() => {});
      await audit(p, view, theme, name);
      if (shots) await p.screenshot({path: `${out}/qa-${view}-${theme}-${name}.png`});
    }
    // a finished chat on the phone and desktop, with the new popovers
    await p.evaluate(() => { try { closeSettings(); } catch (e) {} });
    await p.evaluate(() => newThread()); await p.waitForTimeout(500);
    await p.fill("#input", "Make a one slide summary of our Q3 results"); await p.keyboard.press("Enter");
    await p.waitForSelector(".turnfoot", {state: "attached", timeout: 40000}).catch(() => note(view, theme, "chat", "turn did not finish", ""));
    await p.waitForTimeout(1500); await audit(p, view, theme, "chat-finished");
    if (shots) await p.screenshot({path: `${out}/qa-${view}-${theme}-chat-finished.png`});
    if (await p.$("#xrayBtn")) { await p.click("#xrayBtn"); await p.waitForTimeout(500); await audit(p, view, theme, "xray-open"); if (shots) await p.screenshot({path: `${out}/qa-${view}-${theme}-xray.png`}); await p.keyboard.press("Escape"); }
    await ctx.close();
  }
  await b.close();
  const by = {};
  for (const f of found) { const k = `${f.kind}`; (by[k] = by[k] || []).push(f); }
  console.log(JSON.stringify({page_errors: [...new Set(errs)], problems: Object.fromEntries(Object.entries(by).map(([k, v]) => [k, v.length])), samples: found}, null, 1));
})();
