"""Try a web page the way a user would, so the agent can check the UX of a page it built before it says the task is done.

exercise(target, request, out_dir) opens the page in headless Chromium at each width, finds the parts a person would
use (buttons, links, forms, menus, dialogs, tabs), ranks them by how closely the user's request names them, and tries
them: it clicks, fills and submits forms, submits them empty, opens and closes menus and dialogs, tabs through the
page, hovers the main buttons, and opens the menu at phone width. It writes what went wrong in plain words a model can
act on, for example "The 'Order now' button does nothing when clicked".

It is deterministic and makes no model calls. Only local pages are opened (a file, or http on 127.0.0.1 or localhost),
every request to another host is blocked, external links are never followed and forms never post to another host.
It never raises: a page it cannot open gives the finding "could not exercise the page: <reason>". When it launches its
own browser, it closes it before returning and kills it if the time limit passes, so no process is left behind.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_SHOTS = 6
TAB_STOPS = 15
CHANGE_WAIT_MS = 400          # how long a click has to show any effect before it counts as doing nothing

_STOP = set("""a an the and or but nor to of for with on in at by from as is are be been was were it its this that these
those i me my we our you your they them their he she his her page pages site website web webpage app application make
build create add want need please should would could can will just also some any all each every into onto using use
used like so very more most less than then there here what which who when where how why not no yes do does done get
got one two new nice good great simple basic sure let lets have has had show shows showing look looks able when
include includes including give gives now today really""".split())

SUCCESS_RE = re.compile(r"\b(thank|thanks|success|successful|successfully|sent|received|confirmed|done|added|saved|booked|"
                        r"subscribed|welcome|submitted|we.ll be in touch|order placed|registered)\b", re.I)
ERROR_RE = re.compile(r"\b(required|invalid|please|must|error|missing|enter|cannot be|can.t be|should be|not valid|"
                      r"is empty|too short|fill|incorrect|try again)\b", re.I)

HELPER_JS = r"""() => {
if (window.__uxr) return true;
const U = {};
U.text = s => (s || "").replace(/\s+/g, " ").trim();
U.vis = e => {
  if (!e || !e.isConnected || e.nodeType !== 1) return false;
  if (e.checkVisibility && !e.checkVisibility({opacityProperty: true, visibilityProperty: true})) return false;
  const s = getComputedStyle(e);
  if (s.visibility === "hidden" || s.display === "none") return false;
  const r = e.getBoundingClientRect();
  return r.width > 1 && r.height > 1 && r.right > 0 && r.left < document.documentElement.clientWidth && r.bottom + scrollY > 0;
};
U.cls = e => { const c = e.className; return ((c && c.baseVal !== undefined ? c.baseVal : c) || "") + " " + (e.id || ""); };
U.label = e => {
  let t = "";
  if (e.tagName === "INPUT" || e.tagName === "TEXTAREA" || e.tagName === "SELECT") {
    if (["submit", "button", "reset"].includes(e.type)) t = e.value;
    const L = e.labels && e.labels[0];
    const lt = L ? U.text([...L.childNodes].filter(n => n.nodeType === 3 || (n.nodeType === 1 && !n.contains(e) &&
                   !["SELECT", "TEXTAREA", "INPUT"].includes(n.tagName))).map(n => n.textContent).join(" ")) : "";
    t = t || lt || e.getAttribute("aria-label") || e.placeholder || e.name || e.id;
  } else if (e.tagName === "FORM") {
    const h = e.querySelector("legend,h1,h2,h3,h4");
    let prev = null;
    for (let n = e; n && !prev && n !== document.body; n = n.parentElement) {
      for (let s = n.previousElementSibling; s; s = s.previousElementSibling) {
        if (/^H[1-4]$/.test(s.tagName)) { prev = s; break; }
        const inner = s.querySelector && s.querySelector("h1,h2,h3,h4"); if (inner) { prev = inner; break; }
      }
    }
    t = e.getAttribute("aria-label") || (h && U.text(h.innerText)) || (prev && U.text(prev.innerText)) || e.getAttribute("name") || e.id;
  } else {
    const own = U.text(e.innerText), al = U.text(e.getAttribute("aria-label"));
    t = (/[\p{L}\p{N}]/u.test(own) ? own : "") || al || e.title || (e.querySelector("img[alt]") || {}).alt || own || e.value || e.id || "";
  }
  return U.text(t).slice(0, 40);
};
U.words = e => {
  let w = U.label(e) + " " + (e.getAttribute("aria-label") || "") + " " + (e.title || "") + " " + U.cls(e);
  if (e.tagName === "FORM") {
    w += " form " + [...e.querySelectorAll("label,legend,button,[placeholder],input[type=submit]")].map(x => U.text(x.innerText || x.value || "") + " " + (x.placeholder || "")).join(" ");
    w += " " + [...e.elements].map(x => (x.name || "") + " " + (x.type || "")).join(" ");
  }
  if (e.tagName === "A") w += " " + (e.getAttribute("href") || "").replace(/[^a-z0-9]+/gi, " ");
  const sec = e.parentElement && e.parentElement.closest("section,article,aside,[id]");
  if (sec && sec !== document.body) { const h = sec.querySelector("h1,h2,h3"); w += " " + (sec.id || "") + " " + (h ? U.text(h.innerText).slice(0, 60) : ""); }
  return w.slice(0, 700);
};
U.path = e => {
  const parts = [];
  for (let n = e; n && n.nodeType === 1 && n !== document.documentElement; n = n.parentElement) {
    if (n.id && /^[A-Za-z][\w-]*$/.test(n.id) && document.querySelectorAll("#" + n.id).length === 1) { parts.unshift("#" + n.id); return parts.join(" > "); }
    let i = 1; for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
    parts.unshift(n.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
  }
  return "html > " + parts.join(" > ");
};
U.isLocalUrl = u => {
  if (["file:", "data:", "blob:", "about:", "javascript:"].includes(u.protocol)) return true;
  return ["127.0.0.1", "localhost", "[::1]"].includes(u.hostname);
};
U.sameSite = u => location.protocol === "file:" ? u.protocol === "file:" : u.origin === location.origin;
U.bodyBg = () => {
  for (const e of [document.body, document.documentElement]) { const b = e && getComputedStyle(e).backgroundColor; if (b && !/rgba\(0, 0, 0, 0\)|transparent/.test(b)) return b; }
  return "rgb(255, 255, 255)";
};
U.uaBg = () => {
  if (U._ua) return U._ua;
  const h = document.createElement("div"), b = document.createElement("button");
  try { h.attachShadow({mode: "open"}).appendChild(b); document.body.appendChild(h); U._ua = getComputedStyle(b).backgroundColor; h.remove(); }
  catch (x) { U._ua = "rgb(239, 239, 239)"; }
  return U._ua;
};
U.primary = (e, bg) => {
  const tag = e.tagName, c = U.cls(e), s = getComputedStyle(e);
  const solid = s.backgroundColor && !/rgba\(0, 0, 0, 0\)|transparent/.test(s.backgroundColor) && s.backgroundColor !== bg &&
                s.backgroundColor !== U.uaBg();
  const btn = tag === "BUTTON" || e.getAttribute("role") === "button" || tag === "INPUT" || (tag === "A" && (solid || /btn|button|cta/i.test(c)));
  if (!btn || e.closest("nav,[role=navigation],footer")) return false;
  if (/(^|[\s_-])(primary|cta|hero)([\s_-]|$)|call-to-action/i.test(c)) return true;
  const r = e.getBoundingClientRect();
  return !!solid && r.top + scrollY < innerHeight * 1.2 && r.width >= 70;
};
U.navInfo = () => {
  let pool = [...document.querySelectorAll("nav a[href], [role=navigation] a[href]")];
  if (!pool.length) pool = [...document.querySelectorAll("header a[href]")];
  return {total: pool.length, visible: pool.filter(U.vis).length};
};
U.dialogs = () => {
  const sel = "dialog, [role=dialog], [role=alertdialog], [aria-modal=true], .modal, .dialog, .popup, .lightbox, [class*=modal], [class*=Modal], [class*=dialog], [class*=popup]";
  let d = [...document.querySelectorAll(sel)].filter(U.vis);
  for (const e of [...document.querySelectorAll("body > *, body > * > *")]) {
    if (d.includes(e)) continue;
    const s = getComputedStyle(e);
    if (s.position !== "fixed") continue;
    const r = e.getBoundingClientRect();
    if (r.width * r.height > innerWidth * innerHeight * 0.15 && U.vis(e) && e.tagName !== "HEADER" && e.tagName !== "NAV") d.push(e);
  }
  return d.filter(e => !d.some(o => o !== e && o.contains(e)));
};
U.dlabel = d => { const h = d.querySelector("h1,h2,h3,h4,[role=heading]"); return U.text((h && h.innerText) || d.getAttribute("aria-label") || (d.innerText || "").split("\n")[0]).slice(0, 40); };
U.lines = () => document.body ? (document.body.innerText || "").slice(0, 30000).split("\n").map(U.text).filter(Boolean) : [];
U.invalidCount = () => [...document.querySelectorAll("[aria-invalid=true], .error, .invalid, .is-invalid, .has-error, [class*=error]")].filter(U.vis).length;

U.inventory = () => {
  const bg = U.bodyBg(), out = [], seen = new Set();
  let externals = 0;
  const add = (e, kind, extra) => {
    if (seen.has(e)) return; seen.add(e);
    const r = e.getBoundingClientRect();
    out.push(Object.assign({kind, sel: U.path(e), label: U.label(e), words: U.words(e), y: Math.round(r.top + scrollY), x: Math.round(r.left),
      fold: r.top + scrollY < innerHeight, primary: U.primary(e, bg), on: false}, extra || {}));
  };
  const fillable = f => [...f.elements].filter(x => !x.disabled && !["submit", "button", "reset", "hidden", "image", "file"].includes(x.type) && !["BUTTON", "FIELDSET", "OUTPUT", "OBJECT"].includes(x.tagName));
  for (const f of document.forms) {
    if (!U.vis(f)) continue;
    const fl = fillable(f).filter(x => U.vis(x) || x.type === "checkbox" || x.type === "radio");
    if (!fl.length) continue;
    const textish = fl.filter(x => x.tagName === "TEXTAREA" || (x.tagName === "INPUT" && !["checkbox", "radio", "range", "color"].includes(x.type))).length;
    add(f, "form", {fields: fl.length, textish});
  }
  const btnSel = "button, [role=button], input[type=button], input[type=submit], summary, [role=tab], [aria-haspopup], [aria-expanded]";
  for (const e of document.querySelectorAll(btnSel)) {
    if (!U.vis(e) || e.disabled || e.getAttribute("aria-disabled") === "true") continue;
    if (e.form && (e.type === "submit" || (e.tagName === "BUTTON" && !e.getAttribute("type")))) continue;
    if (e.tagName === "A" && e.getAttribute("href") && !/^(#|javascript:)/.test(e.getAttribute("href"))) continue;
    const role = e.getAttribute("role"), pop = e.getAttribute("aria-haspopup");
    const ctl = e.getAttribute("aria-controls"), target = ctl && document.getElementById(ctl);
    let kind = "button";
    if (role === "tab") kind = "tab";
    else if (e.tagName === "SUMMARY") kind = "details";
    else if (pop === "dialog" || /modal|dialog/i.test(Object.keys(e.dataset || {}).join(" ") + " " + Object.values(e.dataset || {}).join(" ")) ||
             (target && target.matches("dialog,[role=dialog],[aria-modal=true],[class*=modal]"))) kind = "dialog";
    else if ((pop && pop !== "false") || e.hasAttribute("aria-expanded")) kind = "menu";
    const on = e.getAttribute("aria-selected") === "true" || e.getAttribute("aria-pressed") === "true" || !!e.getAttribute("aria-current") ||
               /(^|\s)(active|selected|current|is-active)(\s|$)/.test(U.cls(e));
    add(e, kind, {on: kind === "tab" || kind === "button" ? on : false});
  }
  const here = location.href.split("#")[0];
  for (const a of document.querySelectorAll("a[href]")) {
    if (!U.vis(a) || a.hasAttribute("download")) continue;
    const h = a.getAttribute("href").trim();
    if (h === "#" || h === "" || /^javascript:/i.test(h)) { add(a, "button"); continue; }
    if (/^(mailto|tel|sms):/i.test(h)) continue;
    let u; try { u = new URL(h, location.href); } catch (e) { continue; }
    if (!U.sameSite(u)) { if (!/^(data|blob|about):/.test(u.protocol)) externals++; continue; }
    if (u.href.split("#")[0] === here) { if (u.hash) add(a, "anchor", {hash: u.hash}); continue; }
    if (/\.(zip|pdf|png|jpe?g|gif|svg|webp|mp4|mp3|docx?|xlsx?|pptx?|csv|exe|dmg|gz|tar)$/i.test(u.pathname)) continue;
    add(a, "link", {href: u.href, newtab: a.target === "_blank"});
  }
  for (const e of document.querySelectorAll("input, textarea, select")) {
    if (e.form || !U.vis(e) || e.disabled || e.readOnly) continue;
    if (e.tagName === "SELECT") { if (e.options.length > 1) add(e, "select"); continue; }
    if (["submit", "button", "reset", "hidden", "image", "file", "checkbox", "radio", "range", "color"].includes(e.type)) continue;
    add(e, "input");
  }
  const fz = [...document.querySelectorAll("a[href], button, input, select, textarea, summary, [tabindex]")].filter(e => !e.disabled && e.tabIndex >= 0 && U.vis(e)).length;
  return {items: out, externals, nav: U.navInfo(), focusables: fz, forms: document.forms.length,
          fields: document.querySelectorAll("input:not([type=hidden]), textarea, select").length};
};

U.broken = () => [...document.images].filter(i => {
  if (!i.complete || i.naturalWidth > 0) return false;
  const s = i.currentSrc || i.src; if (!s) return false;
  try { return U.isLocalUrl(new URL(s, location.href)); } catch (e) { return false; }
}).slice(0, 6).map(i => ({src: (i.getAttribute("src") || "").slice(0, 120), alt: i.alt || ""}));

U.baseline = ms => new Promise(res => {
  const noise = new Set();
  const mo = new MutationObserver(recs => recs.forEach(r => noise.add(r.target)));
  mo.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  setTimeout(() => { mo.disconnect(); U.noise = noise; res(noise.size); }, ms);
});
U.noise = new Set();
U.arm = sel => {
  const el = sel ? document.querySelector(sel) : null;
  if (U.mo) U.mo.disconnect();
  U.st = {href: location.href, sy: scrollY, sx: scrollX, active: document.activeElement, el, muts: 0, dialogs: U.dialogs(),
          text: U.lines(), inv: U.invalidCount(), submit: null, nav: U.navInfo().visible,
          expanded: el ? el.getAttribute("aria-expanded") : null};
  U.mo = new MutationObserver(recs => {
    for (const r of recs) {
      if (U.noise.has(r.target)) continue;
      if (r.type === "attributes" && /^data-uxr/.test(r.attributeName || "")) continue;
      U.st.muts++;
    }
  });
  U.mo.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  return true;
};
U.focusMoved = () => {
  const s = U.st, a = document.activeElement;
  return a !== s.active && a !== document.body && a !== document.documentElement && !(s.el && s.el.contains(a));
};
U.changed = () => {
  const s = U.st; if (!s) return false;
  return s.muts > 0 || location.href !== s.href || Math.abs(scrollY - s.sy) > 4 || Math.abs(scrollX - s.sx) > 4 || U.focusMoved() ||
         (s.submit && s.submit.fired);
};
U.report = () => {
  const s = U.st; if (!s) return null;
  const now = U.dialogs(), opened = now.filter(d => !s.dialogs.includes(d));
  const before = new Set(s.text), lines = U.lines();
  const added = lines.filter(l => !before.has(l)).slice(0, 12);
  const old = new URL(s.href);
  return {muts: s.muts, url: location.href, hash: location.hash !== old.hash, moved: location.href.split("#")[0] !== s.href.split("#")[0],
          scrolled: Math.abs(scrollY - s.sy) > 4, focus: U.focusMoved(), added, inv: U.invalidCount() - s.inv, submit: s.submit,
          dialogs: opened.map(d => ({sel: U.path(d), label: U.dlabel(d)})), nav: U.navInfo().visible - s.nav,
          expanded: s.el && s.el.isConnected ? s.el.getAttribute("aria-expanded") : null,
          focusIn: opened.some(d => d.contains(document.activeElement))};
};
U.watchSubmit = () => {
  U.st.submit = {fired: false, prevented: false, blocked: "", action: ""};
  if (U.subHooked) return true;
  U.subHooked = true;
  window.addEventListener("submit", ev => {
    const st = U.st && U.st.submit; if (!st) return;
    st.fired = true; st.prevented = ev.defaultPrevented;
    let u = null; try { u = new URL(ev.target.action || location.href, location.href); } catch (e) {}
    st.action = u ? u.href : "";
    if (!ev.defaultPrevented && u && !U.sameSite(u)) { ev.preventDefault(); st.blocked = u.host || u.protocol; }
  });
  return true;
};
U.fill = sel => {
  const f = document.querySelector(sel); if (!f) return 0;
  const iso = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
  const radios = new Set(); let n = 0;
  const set = (e, v) => {
    const proto = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : e.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const d = Object.getOwnPropertyDescriptor(proto, "value");
    try { d && d.set ? d.set.call(e, v) : (e.value = v); } catch (x) { return; }
    e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); n++;
  };
  for (const e of f.elements) {
    if (e.disabled || e.readOnly) continue;
    const t = (e.type || "").toLowerCase();
    if (["submit", "button", "reset", "hidden", "image", "file"].includes(t) || ["BUTTON", "FIELDSET", "OUTPUT", "OBJECT"].includes(e.tagName)) continue;
    const nm = ((e.name || "") + " " + (e.id || "") + " " + (e.placeholder || "") + " " + (e.getAttribute("autocomplete") || "") + " " +
                ((e.labels && e.labels[0] && e.labels[0].innerText) || "")).toLowerCase();
    if (e.tagName === "SELECT") { const o = [...e.options].find(o => !o.disabled && o.value && o.index > 0) || [...e.options].find(o => !o.disabled && o.value); if (o) set(e, o.value); continue; }
    if (t === "checkbox") { if (e.required || /agree|terms|consent|accept|privacy/.test(nm)) { e.checked = true; e.dispatchEvent(new Event("change", {bubbles: true})); n++; } continue; }
    if (t === "radio") { if (!radios.has(e.name)) { radios.add(e.name); e.checked = true; e.dispatchEvent(new Event("change", {bubbles: true})); n++; } continue; }
    let v;
    if (t === "email" || /e-?mail/.test(nm)) v = "alex@example.com";
    else if (t === "tel" || /phone|mobile|\btel\b/.test(nm)) v = "5551234567";
    else if (t === "url" || /website|\burl\b/.test(nm)) v = "https://example.com";
    else if (t === "number" || t === "range") {
      const mn = parseFloat(e.min), mx = parseFloat(e.max), st = parseFloat(e.step) || 1;
      let x = isNaN(mn) ? 2 : mn + st; if (!isNaN(mx) && x > mx) x = isNaN(mn) ? mx : mn; v = String(x);
    }
    else if (t === "date") v = e.min && e.min > iso ? e.min : iso;
    else if (t === "datetime-local") v = iso + "T12:00";
    else if (t === "time") v = "12:00";
    else if (t === "month") v = iso.slice(0, 7);
    else if (t === "week") continue;
    else if (t === "color") v = "#3366cc";
    else if (t === "password") v = "Review-pass-123";
    else if (e.tagName === "TEXTAREA") v = "Hello, this is a test message from the review.";
    else if (/first/.test(nm)) v = "Alex";
    else if (/last|surname/.test(nm)) v = "Example";
    else if (/name/.test(nm)) v = "Alex Example";
    else if (/zip|postal/.test(nm)) v = "10001";
    else if (/city|town/.test(nm)) v = "Springfield";
    else if (/address|street/.test(nm)) v = "12 Main Street";
    else if (/company|organi[sz]ation/.test(nm)) v = "Example Ltd";
    else if (/age|qty|quantity|count|guests|people/.test(nm)) v = "2";
    else v = "Test";
    if (e.maxLength > 0 && v.length > e.maxLength) v = v.slice(0, e.maxLength);
    if (e.minLength > 0 && v.length < e.minLength) v = v + "x".repeat(e.minLength - v.length);
    set(e, v);
  }
  return n;
};
U.fillNear = sel => {
  const el = document.querySelector(sel); if (!el) return 0;
  let box = el.parentElement;
  for (let i = 0; i < 2 && box && box.parentElement && !box.querySelector("input,textarea"); i++) box = box.parentElement;
  if (!box) return 0;
  let n = 0;
  for (const e of box.querySelectorAll("input, textarea")) {
    if (e.form || e.disabled || e.readOnly || e.value || !U.vis(e)) continue;
    if (!["text", "search", "email", "number", "tel", "url", ""].includes(e.type) && e.tagName !== "TEXTAREA") continue;
    const v = e.type === "number" ? "2" : e.type === "email" ? "alex@example.com" : "Test item";
    const proto = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(e, v);
    e.dispatchEvent(new Event("input", {bubbles: true})); n++;
  }
  return n;
};
U.submitter = sel => {
  const f = document.querySelector(sel); if (!f) return null;
  let b = f.querySelector("button[type=submit], input[type=submit], input[type=image], button:not([type])");
  if (!b && f.id) b = document.querySelector('[form="' + f.id + '"][type=submit], button[form="' + f.id + '"]:not([type])');
  if (b && U.vis(b)) return {sel: U.path(b), label: U.label(b)};
  const t = [...f.elements].find(e => U.vis(e) && (e.tagName === "TEXTAREA" ? false : e.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "hidden"].includes(e.type)));
  return t ? {sel: U.path(t), label: "", enter: true} : {sel: "", label: "", js: true};
};
U.firstInvalid = sel => {
  const f = document.querySelector(sel); if (!f) return null;
  const e = f.querySelector(":invalid:not(fieldset):not(form)");
  return e ? {label: U.label(e), msg: (e.validationMessage || "").slice(0, 120)} : null;
};
U.closer = sel => {
  const d = document.querySelector(sel); if (!d) return null;
  const re = /^(close|×|✕|✖|x|cancel|dismiss|got it|ok|okay|no thanks|done)$/i;
  for (const e of d.querySelectorAll("button, [role=button], a, [aria-label], .close, [class*=close]")) {
    if (!U.vis(e)) continue;
    const t = U.text(e.innerText), al = e.getAttribute("aria-label") || "";
    if (re.test(t) || /close|dismiss|cancel/i.test(al) || /(^|[\s_-])close([\s_-]|$)/i.test(U.cls(e))) return U.path(e);
  }
  return null;
};
U.openState = sels => sels.some(s => { const e = document.querySelector(s); return e && U.vis(e); });

U.focusStyle = e => { const s = getComputedStyle(e); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.outlineOffset, s.boxShadow, s.borderTopColor,
  s.borderBottomColor, s.borderBottomWidth, s.backgroundColor, s.color, s.textDecorationLine, s.transform, s.opacity, s.filter].join("|"); };
U.calm = on => {
  let s = document.getElementById("__uxr_calm");
  if (on && !s) { s = document.createElement("style"); s.id = "__uxr_calm"; s.textContent = "*,*::before,*::after{transition:none!important;animation:none!important}"; document.head.appendChild(s); }
  if (!on && s) s.remove();
  return true;
};
U.prepTab = () => {
  U.calm(true);
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  scrollTo(0, 0);
  U.base = new Map();
  const f = [...document.querySelectorAll("a[href], button, input, select, textarea, summary, [tabindex], [contenteditable=true], iframe")].filter(e => !e.disabled && e.tabIndex >= 0).slice(0, 80);
  for (const e of f) U.base.set(e, U.focusStyle(e));
  return f.length;
};
U.focusInfo = () => {
  const e = document.activeElement;
  if (!e || e === document.body || e === document.documentElement) return null;
  const r = e.getBoundingClientRect();
  let fixed = false;
  for (let n = e; n && n !== document.body; n = n.parentElement) { const p = getComputedStyle(n).position; if (p === "fixed" || p === "sticky") { fixed = true; break; } }
  const base = U.base ? U.base.get(e) : undefined, now = U.focusStyle(e);
  let shown = base === undefined ? null : base !== now;
  if (shown) {
    const a = base.split("|"), b = now.split("|"), diff = a.map((v, i) => v !== b[i] ? i : -1).filter(i => i >= 0);
    if (diff.every(i => i <= 3) && (b[0] === "none" || parseFloat(b[1]) === 0 || /rgba\([^)]*,\s*0\)$/.test(b[2]))) shown = false;
  }
  return {sel: U.path(e), label: U.label(e) || e.tagName.toLowerCase(), y: Math.round(r.top + scrollY), fixed, shown, visible: U.vis(e),
          nav: !!e.closest("nav,[role=navigation]"), ti: e.tabIndex};
};
U.hoverStyle = sel => { const e = document.querySelector(sel); if (!e) return ""; const s = getComputedStyle(e);
  return [s.backgroundColor, s.backgroundImage, s.color, s.borderTopColor, s.boxShadow, s.transform, s.opacity, s.filter, s.textDecorationLine, s.outlineStyle, s.scale, s.translate].join("|"); };
U.navToggle = () => {
  const re = /menu|nav|burger|hamburger|toggle|☰|≡|open/i;
  const c = [...document.querySelectorAll("button, [role=button], a[href], label, summary, [aria-expanded], [aria-controls]")];
  for (const e of c) {
    if (!U.vis(e)) continue;
    const r = e.getBoundingClientRect();
    const zone = e.closest("header,nav,[role=navigation],[role=banner]") || r.top + scrollY < 140;
    if (!zone) continue;
    const hint = (e.getAttribute("aria-label") || "") + " " + U.cls(e) + " " + U.text(e.innerText) + " " + (e.title || "");
    const ctl = e.getAttribute("aria-controls");
    const ctlNav = ctl && document.getElementById(ctl) && document.getElementById(ctl).querySelector("a[href]");
    if (ctlNav || (e.hasAttribute("aria-expanded") && e.tagName !== "A") || re.test(hint) && e.tagName !== "A" || (e.tagName === "A" && /^#/.test(e.getAttribute("href") || "") && re.test(hint)))
      return {sel: U.path(e), label: U.label(e) || "menu button"};
  }
  return null;
};
U.overflow = () => {
  const W = document.documentElement.clientWidth, sw = document.documentElement.scrollWidth;
  if (sw <= W + 2) return null;
  const ox = getComputedStyle(document.documentElement).overflowX + getComputedStyle(document.body).overflowX;
  if (/hidden|clip/.test(ox)) return null;
  let n = 0;
  for (const e of document.body.querySelectorAll("*")) {
    if (++n > 4000) break;
    const r = e.getBoundingClientRect();
    if (r.right <= W + 2 || r.width === 0 || getComputedStyle(e).position === "fixed") continue;
    const p = e.parentElement && e.parentElement.getBoundingClientRect();
    if (!p || p.right <= W + 2 || e.parentElement === document.body) {
      const c = U.cls(e).trim().split(/\s+/)[0];
      return {width: sw, what: e.tagName.toLowerCase() + (c ? "." + c : "") + (U.label(e) ? " '" + U.label(e).slice(0, 24) + "'" : "")};
    }
  }
  return {width: sw, what: ""};
};
window.__uxr = U;
return true;
}"""


class _Stop(Exception):
    """The time limit is near: stop trying more things and report what was found."""


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _words(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _STOP}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "step"


def _q(label: str) -> str:
    return f"'{label}'" if label else "an unnamed control"


def _target_url(target: str) -> str:
    """A file path or file:// URL, or an http URL on 127.0.0.1 or localhost. Anything else is refused."""
    t = (target or "").strip()
    if not t:
        raise ValueError("no page was given")
    if re.match(r"(?i)^https?://", t):
        host = (urlparse(t).hostname or "").lower()
        if host not in LOCAL_HOSTS:
            raise ValueError(f"only local pages can be tried (127.0.0.1 or localhost), not {host or t[:60]}")
        return t
    if t.lower().startswith("file://"):
        p = Path(_file_path(t))
    elif re.match(r"(?i)^[a-z][a-z0-9+.-]*://", t):
        raise ValueError(f"only a file or a local http page can be tried, not {t[:60]}")
    else:
        p = Path(t).expanduser()
    p = p.resolve()
    if p.is_dir() and (p / "index.html").is_file():
        p = p / "index.html"
    if not p.is_file():
        raise ValueError(f"the file {p} does not exist")
    return p.as_uri()


def origin(url: str) -> str:
    """scheme://host:port of an http(s) or ws(s) URL, with the default port filled in ("" for anything else)."""
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        return ""
    scheme = {"ws": "http", "wss": "https"}.get(u.scheme.lower(), u.scheme.lower())
    if scheme not in ("http", "https") or not u.hostname:
        return ""
    host = u.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{host}:{port or (443 if scheme == 'https' else 80)}"


def _inside(path: str, root: str) -> bool:
    try:
        a, b = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([a, b]) == b
    except (OSError, ValueError):
        return False


def _is_local(url: str, root: str | None = None, allow=None) -> bool:
    """May the page load this URL? With `allow` (a set of origins, see origin()): only those origins and in-memory
    data (data:, blob:, about:). Without it: a file inside `root`, or http on 127.0.0.1 or localhost. A file outside
    `root`, or any file when no root is given, is never local."""
    u = urlparse(url)
    if u.scheme in ("data", "blob", "about", "chrome-error"):
        return True
    if allow is not None:
        return bool(origin(url)) and origin(url) in allow
    if u.scheme == "file":
        return bool(root) and _inside(_file_path(url), root)
    return (u.hostname or "").lower() in LOCAL_HOSTS


def _file_path(url: str) -> str:
    """The path of a file:// URL. On Windows "file:///C:/x" is C:/x, not /C:/x."""
    path = unquote(urlparse(url).path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


NO_RTC_JS = ("(() => { for (const k of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel']) "
             "{ try { delete window[k]; window[k] = undefined; } catch (e) {} } })()")


def guard(ctx, allowed, root: str | None = None, blocked: list | None = None) -> None:
    """Block every request of this context that is not to an allowed origin (or, with no `allowed`, not local): page
    loads, frames, scripts, fetch, forms, popups and web sockets. WebRTC, which goes around the request routing, is
    removed from the pages."""
    def route(r):
        try:
            if _is_local(r.request.url, root, allowed):
                r.continue_()
            else:
                if blocked is not None:
                    host = urlparse(r.request.url).hostname or r.request.url[:40]
                    if host not in blocked:
                        blocked.append(host)
                r.abort("blockedbyclient")
        except Exception:  # noqa: BLE001
            pass
    ctx.route("**/*", route)
    if hasattr(ctx, "route_web_socket"):
        def ws_route(ws):
            try:
                if _is_local(re.sub(r"^ws", "http", ws.url), root, allowed):
                    ws.connect_to_server()
                else:
                    ws.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            ctx.route_web_socket(re.compile(".*"), ws_route)
        except Exception:  # noqa: BLE001
            pass
    try:
        ctx.add_init_script(NO_RTC_JS)
    except Exception:  # noqa: BLE001
        pass


# Chromium resolves no host name or address but this computer's own: a <link rel=dns-prefetch> or preconnect, which
# skip the request routing, cannot carry data out in a name lookup or open a connection to another host.
LAUNCH_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost , EXCLUDE 127.0.0.1 , EXCLUDE [::1]"]


def launch_browser(pw):
    """Headless Chromium the way meshpool opens it for previews (ui_audit.launcher): Playwright's own build first, else
    MH_TEST_CHROMIUM, else any Chromium a Playwright install left on this computer (PLAYWRIGHT_BROWSERS_PATH first)."""
    try:
        return pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
    except Exception as first:  # noqa: BLE001
        cands = [os.environ.get("MH_TEST_CHROMIUM") or ""]
        try:
            from meshpool_harness import browser_use as BU
            cands.append(BU.find_playwright_chromium())
        except Exception:  # noqa: BLE001
            pass
        for exe in dict.fromkeys(c for c in cands if c):
            try:
                return pw.chromium.launch(headless=True, executable_path=exe, args=LAUNCH_ARGS)
            except Exception:  # noqa: BLE001
                continue
        raise first


def _tree(pids) -> list:
    """The processes with these ids and everything they started, children first."""
    import psutil
    out = []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            out += p.children(recursive=True) + [p]
        except Exception:  # noqa: BLE001
            continue
    return out


def _kill(procs, wait: float = 3.0) -> None:
    """Kill these processes, then wait up to `wait` s for them to be gone."""
    import psutil
    for p in procs:
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass
    try:
        psutil.wait_procs(procs, timeout=wait)
    except Exception:  # noqa: BLE001
        pass


def _exact_driver_pid(manager, sp) -> int:
    """The pid of the Playwright driver behind this sync_playwright() call, from Playwright's internals (0 if unknown)."""
    for get in (lambda: manager._client._transport._proc.pid, lambda: sp._impl_obj._connection._transport._proc.pid):
        try:
            pid = int(get())
            if pid > 0:
                return pid
        except Exception:  # noqa: BLE001
            continue
    return 0


def _driver_pids(before: set) -> list:
    """The Playwright driver this call started: a new child of this process running Playwright's run-driver."""
    import psutil
    out = []
    try:
        for c in psutil.Process().children():
            if c.pid in before:
                continue
            try:
                cmd = " ".join(c.cmdline())
            except Exception:  # noqa: BLE001
                cmd = ""
            if "run-driver" in cmd or "playwright" in cmd.lower():
                out.append(c.pid)
    except Exception:  # noqa: BLE001
        pass
    return out


class _Run:
    """One try-out of one page. Holds the result as it grows, so a run cut short still returns what it found."""

    def __init__(self, url: str, request: str, out_dir: str, widths, max_steps: int, deadline: float,
                 root: str | None = None, allow=None):
        self.url = url
        self.root, self.allow = root, (set(allow) if allow is not None else None)
        self.request = request or ""
        self.req_words = _words(request)
        self.out = Path(out_dir)
        self.widths = [int(w) for w in (widths or (1280,))] or [1280]
        self.max_steps = max(0, int(max_steps))
        self.deadline = deadline
        self.total = max(1.0, deadline - time.monotonic())
        self.steps: list = []
        self.findings: list = []
        self.shots: list = []
        self.console: list = []
        self.errs: list = []          # (kind, text) as they happen, so a step can see the ones it caused
        self.failed: dict = {}        # url -> reason
        self.blocked: list = []
        self.events = 0               # popups, downloads, alerts
        self.nav_resp: list = []      # main-frame responses: (url, status)
        self.nav_fail: list = []      # main-frame navigations that failed: (url, reason)
        self.load_mode = "load"
        self.noisy = False
        self.desktop_nav = None
        self.opened_dialog = False
        self.tried = 0
        self.skipped = 0

    # ---------- small helpers --------------------------------------------------------------------------------------------
    def left(self) -> float:
        return self.deadline - time.monotonic()

    def guard(self, need: float = 1.5) -> None:
        if self.left() < need:
            raise _Stop()

    def ms(self, cap: float) -> int:
        """A Playwright timeout in ms: at most cap seconds and never past the deadline."""
        return int(max(0.2, min(cap, self.left() - 0.5)) * 1000)

    def find(self, text: str) -> None:
        text = text.strip()
        if text and text not in self.findings and len(self.findings) < 30:
            self.findings.append(text)

    def step(self, action: str, ok: bool, note: str = "", shot=None) -> dict:
        s = {"action": action, "ok": bool(ok), "note": note, "shot": shot}
        self.steps.append(s)
        return s

    def shot(self, page, name: str, failure: bool = False):
        """A screenshot, at most MAX_SHOTS. The last two are kept for failures."""
        if len(self.shots) >= (MAX_SHOTS if failure else MAX_SHOTS - 2) or self.left() < 2:
            return None
        try:
            self.out.mkdir(parents=True, exist_ok=True)
            f = self.out / f"uxr-{len(self.shots) + 1:02d}-{_slug(name)}.png"
            page.screenshot(path=str(f), timeout=self.ms(3))
            self.shots.append(str(f))
            return str(f)
        except Exception:  # noqa: BLE001
            return None

    def alive(self, page, cap: float = 2.0) -> bool:
        """True when the page still runs scripts. A page stuck in an endless script would hang every later call."""
        try:
            page.wait_for_function("() => true", timeout=self.ms(cap))
            return True
        except Exception:  # noqa: BLE001
            return False

    def js(self, page, expr: str, arg=None):
        return page.evaluate(f"(a) => window.__uxr && ({expr})", arg)

    def install(self, page) -> None:
        page.evaluate(HELPER_JS)
        if self.noisy:
            page.evaluate("() => window.__uxr.baseline(150)")

    # ---------- page set-up ----------------------------------------------------------------------------------------------
    def new_page(self, browser, width: int):
        phone = width < 700
        ctx = browser.new_context(viewport={"width": width, "height": 844 if phone else 800}, is_mobile=phone, has_touch=phone,
                                  device_scale_factor=1, accept_downloads=False, service_workers="block", bypass_csp=True)
        ctx.set_default_timeout(self.ms(5))
        ctx.set_default_navigation_timeout(self.ms(10))

        guard(ctx, self.allow, self.root, self.blocked)

        def on_page(p):
            self.events += 1
            try:
                p.close()
            except Exception:  # noqa: BLE001
                pass
        page = ctx.new_page()
        ctx.on("page", on_page)
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self._err("page", f"{getattr(e, 'name', '') or 'Error'}: {getattr(e, 'message', '') or e}"))
        page.on("requestfailed", self._on_failed)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)
        page.on("download", lambda d: self._bump())
        return ctx, page

    def _bump(self):
        self.events += 1

    def _on_dialog(self, d):
        self.events += 1
        try:
            d.accept()
        except Exception:  # noqa: BLE001
            pass

    def _err(self, kind: str, text: str):
        text = re.sub(r"\s+", " ", str(text)).strip()[:300]
        self.errs.append((kind, text))
        if text not in self.console and len(self.console) < 40:
            self.console.append(text)

    def _on_console(self, m):
        try:
            if m.type != "error":
                return
            t = m.text or ""
            if "ERR_BLOCKED_BY_CLIENT" in t or "favicon.ico" in t or t.startswith("Failed to load resource"):
                return              # blocked on purpose, or already reported as a failed file
            self._err("console", t)
        except Exception:  # noqa: BLE001
            pass

    def _ours(self, r) -> bool:
        """True for a request made by the page under review (not by a page a link led to)."""
        try:
            return r.frame.url.split("#")[0].split("?")[0] == self.url.split("#")[0].split("?")[0] or r.frame.url in ("", "about:blank")
        except Exception:  # noqa: BLE001
            return True

    def _on_failed(self, r):
        try:
            why = r.failure or ""
            if ("ERR_BLOCKED_BY_CLIENT" in why or "ERR_ABORTED" in why or r.url.endswith("favicon.ico")
                    or not _is_local(r.url, self.root, self.allow)):
                return
            if r.is_navigation_request() and r.frame.parent_frame is None:
                self.nav_fail.append((r.url, why))
                return
            if self._ours(r):
                self.failed.setdefault(r.url, why.replace("net::", "") or "failed")
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, r):
        try:
            req = r.request
            if req.is_navigation_request() and req.frame.parent_frame is None:
                self.nav_resp.append((r.url, r.status))
            elif r.status >= 400 and not r.url.endswith("favicon.ico") and self._ours(req):
                self.failed.setdefault(r.url, f"HTTP {r.status}")
        except Exception:  # noqa: BLE001
            pass

    def settle_cap(self, first: bool) -> float:
        return max(1.0, min(8.0, self.total * 0.2)) if first else max(1.0, min(2.5, self.total * 0.08))

    def load(self, page, width: int, first: bool) -> bool:
        """Open the page and wait for it to settle, within a bound. Returns False when it cannot be used at all."""
        cap = self.settle_cap(first)
        end = time.monotonic() + cap
        stage = "commit"
        try:
            page.goto(self.url, wait_until="commit", timeout=self.ms(max(cap, 5)))
            for stage in ("domcontentloaded", "load", "networkidle"):
                page.wait_for_load_state(stage, timeout=int(max(0.2, min(end - time.monotonic(), self.left() - 1)) * 1000))
            stage = "settled"
        except Exception as e:  # noqa: BLE001
            if stage == "commit":
                self.find(f"could not exercise the page: it did not open ({str(e).splitlines()[0][:160]})")
                self.step(f"load at {width}px", False, str(e).splitlines()[0][:200])
                return False
        if self.nav_fail and not self.nav_resp:
            self.find(f"could not exercise the page: it did not open ({self.nav_fail[-1][1]})")
            self.step(f"load at {width}px", False, self.nav_fail[-1][1])
            return False
        if self.nav_resp and self.nav_resp[-1][1] >= 400:
            self.find(f"could not exercise the page: the server answered HTTP {self.nav_resp[-1][1]}")
            self.step(f"load at {width}px", False, f"HTTP {self.nav_resp[-1][1]}")
            return False
        if not self.alive(page):
            self.find("The page freezes: a script on it never stops running, so nothing on the page responds")
            self.step(f"load at {width}px", False, "the page stopped responding", self.shot(page, f"frozen-{width}", True))
            return False
        note = ""
        if stage in ("domcontentloaded", "load"):
            note = f"the page was still loading after {cap:.0f} s"
            if first:
                self.load_mode = "commit" if stage == "domcontentloaded" else "domcontentloaded"
                self.find(f"The page did not finish loading within {cap:.0f} s: something it loads never arrives")
        elif stage == "networkidle":
            note = f"network requests were still going after {cap:.0f} s"
            if first:
                self.find(f"The page never settles: it was still making network requests after {cap:.0f} s")
        self.install(page)
        if first:
            try:
                self.noisy = bool(page.evaluate("() => window.__uxr.baseline(150)"))
            except Exception:  # noqa: BLE001
                self.noisy = False
        self.step(f"load at {width}px", not note, note or "loaded")
        return True

    def reset(self, page) -> bool:
        """Back to a fresh copy of the page before the next step."""
        self.guard()
        try:
            page.goto(self.url, wait_until=self.load_mode if self.load_mode != "commit" else "commit", timeout=self.ms(5))
        except Exception:  # noqa: BLE001
            return False
        if not self.alive(page):
            return False
        try:
            self.install(page)
        except Exception:  # noqa: BLE001
            return False
        return True

    def load_problems(self, page, width: int):
        try:
            broken = self.js(page, "window.__uxr.broken()") or []
        except Exception:  # noqa: BLE001
            broken = []
        for b in broken:
            name = os.path.basename(urlparse(b["src"]).path) or b["src"]
            self.find(f"The image '{name}'" + (f" (alt '{b['alt'][:30]}')" if b["alt"] else "") + " is broken: it does not load")
        for u, why in list(self.failed.items())[:6]:
            name = os.path.basename(urlparse(u).path) or u
            if any(name in f for f in self.findings):
                continue
            self.find(f"The file '{name}' could not be loaded ({why})")
        for kind, text in self.errs[:3]:
            self.find(("A script on the page crashes: " if kind == "page" else "The page logs an error in the console: ") + text[:200])

    # ---------- watching one action -------------------------------------------------------------------------------------
    def arm(self, page, sel: str) -> tuple:
        self.js(page, "window.__uxr.arm(a)", sel)
        return len(self.errs), self.events, len(self.nav_resp), len(self.nav_fail)

    def watch(self, page, mark: tuple, wait_ms: int = CHANGE_WAIT_MS, extra_ms: int = 200) -> dict:
        """Wait for any visible effect, then describe it. A navigation away counts as an effect."""
        errs0, ev0, resp0, fail0 = mark
        try:
            page.wait_for_function("() => window.__uxr && window.__uxr.changed()", timeout=self.ms(wait_ms / 1000), polling=50)
            page.wait_for_timeout(extra_ms)
        except Exception:  # noqa: BLE001
            pass
        rep = None
        navigated = len(self.nav_resp) > resp0 or len(self.nav_fail) > fail0
        if navigated:
            try:
                page.wait_for_load_state("load", timeout=self.ms(3))
            except Exception:  # noqa: BLE001
                pass
        if self.alive(page, 1.5):
            try:
                rep = self.js(page, "window.__uxr.report()")
            except Exception:  # noqa: BLE001
                rep = None
        else:
            rep = {"frozen": True}
        if rep is None:             # the helper is gone: the page navigated
            rep = {"muts": 0, "moved": True, "added": [], "dialogs": [], "inv": 0, "submit": None, "url": page.url}
        rep["navigated"] = navigated or bool(rep.get("moved"))
        rep["events"] = self.events - ev0
        rep["errs"] = self.errs[errs0:]
        rep["any"] = bool(rep.get("muts") or rep.get("moved") or rep.get("hash") or rep.get("scrolled") or rep.get("focus")
                          or rep.get("dialogs") or rep["events"] or navigated)
        return rep

    def after(self, page, rep: dict, what: str) -> bool:
        """Report script errors the action caused. Returns False when the page froze."""
        for kind, text in rep.get("errs") or []:
            self.find(f"{what} " + ("causes a script error: " if kind == "page" else "logs a console error: ") + text[:200])
        if rep.get("frozen"):
            self.find(f"{what} freezes the page: a script never stops running")
            return False
        return True

    def click(self, page, sel: str, label: str):
        """Click, or say why it could not be clicked. Returns an error text or ''."""
        try:
            page.locator(sel).first.click(timeout=self.ms(1.5))
            return ""
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            m = re.search(r"<(\w+)[^>]*>.*?intercepts pointer events", msg)
            if m:
                return f"covered by another element (<{m.group(1)}>)"
            return msg.splitlines()[0][:160]

    # ---------- ranking -------------------------------------------------------------------------------------------------
    def rank(self, items: list) -> list:
        base = {"form": 2.5, "dialog": 2.5, "button": 2.0, "menu": 1.8, "tab": 1.5, "details": 1.2, "anchor": 1.0, "link": 0.6,
                "input": 0.8, "select": 0.4}
        caps = {"link": 2, "anchor": 3, "input": 2, "select": 1}
        for i, it in enumerate(items):
            hits = sorted(self.req_words & _words(it.get("words", "") + " " + it.get("label", "")))
            it["hits"] = hits
            it["score"] = 3 * len(hits) + base.get(it["kind"], 1) + (1 if it.get("fold") else 0) - i * 0.001
        prim = sorted([it for it in items if it.get("primary") and it["kind"] in ("button", "dialog", "link", "anchor", "menu")],
                      key=lambda it: -it["score"])[:3]
        rest = sorted([it for it in items if it not in prim], key=lambda it: -it["score"])
        out, count, hrefs, alike = [], {}, set(), {}
        for it in prim + rest:
            k = it["kind"]
            key = it.get("href") or it.get("hash")
            if key and key in hrefs:
                continue
            if count.get(k, 0) >= caps.get(k, 99):
                continue
            if it.get("on") and k in ("tab", "button"):
                continue            # the selected tab or a pressed toggle may rightly do nothing
            if k == "button" and re.match(r"(?i)^\W*(close|cancel|dismiss|reset|clear|back|undo|print|copy|share|download|"
                                          r"previous|prev|no thanks)\b", it.get("label") or ""):
                continue            # these do nothing on a fresh page, and that is fine
            group = (k, re.sub(r"\d+", "#", (it.get("label") or "").lower()).strip())
            if group[1] and alike.get(group, 0) >= 2:
                continue            # "Choose plan 1..4": two of a kind say enough, the step budget goes to other parts
            alike[group] = alike.get(group, 0) + 1
            if key:
                hrefs.add(key)
            count[k] = count.get(k, 0) + 1
            out.append(it)
        return out

    # ---------- the steps -----------------------------------------------------------------------------------------------
    def do_hover(self, page, items: list) -> None:
        prim = [it for it in items if it.get("primary") and it["kind"] in ("button", "dialog", "link", "menu")][:2]
        if not prim:
            return
        self.js(page, "window.__uxr.calm(true)")
        flat = []
        for it in prim:
            try:
                page.mouse.move(0, 0)
                s0 = self.js(page, "window.__uxr.hoverStyle(a)", it["sel"])
                page.locator(it["sel"]).first.hover(timeout=self.ms(1))
                s1 = self.js(page, "window.__uxr.hoverStyle(a)", it["sel"])
                if s0 == s1:
                    flat.append(it["label"])
            except Exception:  # noqa: BLE001
                continue
        try:
            page.mouse.move(0, 0)
            self.js(page, "window.__uxr.calm(false)")
        except Exception:  # noqa: BLE001
            pass
        for lab in flat:
            self.find(f"The {_q(lab)} button does not change when the mouse is over it (add a hover style)")
        names = ", ".join(_q(it["label"]) for it in prim)
        self.step(f"hover {names}", not flat, "no visible hover change on " + ", ".join(_q(x) for x in flat) if flat else "changes on hover")

    def do_tab(self, page, inv: dict) -> None:
        self.js(page, "window.__uxr.prepTab()")
        seen = []
        try:
            for _ in range(TAB_STOPS):
                self.guard()
                page.keyboard.press("Tab")
                info = self.js(page, "window.__uxr.focusInfo()")
                if not info or (seen and info["sel"] == seen[0]["sel"]):
                    break
                seen.append(info)
        finally:
            try:
                self.js(page, "window.__uxr.calm(false)")
                page.evaluate("() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); scrollTo(0, 0); }")
            except Exception:  # noqa: BLE001
                pass
        if not seen:
            if (inv.get("focusables") or 0) >= 2:
                self.find("Pressing Tab does not reach any control on the page, so it cannot be used with a keyboard")
                self.step("Tab through the page", False, "focus reached nothing")
            return
        notes = []
        hidden = [s for s in seen if not s["visible"]]
        flat = [s for s in seen if s["visible"] and s["shown"] is False]
        if flat:
            if all(s["nav"] for s in flat) and len(flat) > 1:
                where = "the nav links"
            else:
                where = ", ".join(_q(s["label"]) for s in flat[:4]) + (f" and {len(flat) - 4} more" if len(flat) > 4 else "")
            self.find(f"Keyboard focus is invisible on {where} ({len(flat)} of {len(seen)} elements checked): "
                      "add a :focus-visible outline")
            notes.append(f"focus invisible on {len(flat)}")
        if hidden:
            self.find("Keyboard focus lands on things that cannot be seen: " + ", ".join(_q(s["label"]) for s in hidden[:4]) +
                      ". Hide them from the keyboard too while they are closed (display:none, visibility:hidden or inert)")
            notes.append(f"focus on {len(hidden)} hidden elements")
        vh = 800
        for a, b in zip(seen, seen[1:]):
            if not (a["fixed"] or b["fixed"]) and a["visible"] and b["visible"] and a["y"] - b["y"] > max(300, vh // 2):
                hint = f" (it has tabindex={b['ti']}; remove positive tabindex values)" if b["ti"] > 0 else " (the order in the code does not match the page)"
                self.find(f"Keyboard focus jumps back up the page from {_q(a['label'])} to {_q(b['label'])}{hint}")
                notes.append("order jumps back")
                break
        self.step(f"Tab through the first {len(seen)} focusable elements", not notes, "; ".join(notes) or "focus is visible and in order")

    def do_item(self, page, it: dict, phase: str = "") -> bool:
        """Try one control. Returns True when the page changed and needs a reset before the next step."""
        k, lab, sel = it["kind"], it.get("label") or "", it["sel"]
        hint = f" (the request mentions {', '.join(it['hits'][:3])})" if it.get("hits") else ""
        if k == "form":
            return self.do_form(page, it, phase, hint)
        if k == "select":
            try:
                opts = page.locator(sel).first.evaluate("e => [...e.options].filter(o => !o.disabled).map(o => o.value)")
                cur = page.locator(sel).first.input_value(timeout=self.ms(1))
                other = next((o for o in opts if o != cur), None)
                if other is None:
                    return False
                mark = self.arm(page, sel)
                page.locator(sel).first.select_option(other, timeout=self.ms(1.5))
                rep = self.watch(page, mark)
                self.after(page, rep, f"Choosing an option in {_q(lab)}")
                self.step(f"choose an option in {_q(lab)}", True, ("the page updated" if rep["any"] else "no other change on the page") + hint)
                return rep["any"]
            except Exception as e:  # noqa: BLE001
                self.step(f"choose an option in {_q(lab)}", False, str(e).splitlines()[0][:160])
                return False
        if k == "input":
            try:
                loc = page.locator(sel).first
                mark = self.arm(page, sel)
                loc.fill("alex@example.com" if "mail" in (it.get("words") or "").lower() else "Test item", timeout=self.ms(1.5))
                loc.press("Enter", timeout=self.ms(1))
                rep = self.watch(page, mark)
                self.after(page, rep, f"Typing into {_q(lab)}")
                self.step(f"type into {_q(lab)} and press Enter", True, ("the page updated" if rep["any"] else "nothing else changed") + hint)
                return True
            except Exception as e:  # noqa: BLE001
                self.step(f"type into {_q(lab)}", False, str(e).splitlines()[0][:160])
                return False

        noun = "link" if k in ("anchor", "link") else "tab" if k == "tab" else "button"
        what = f"Clicking the {_q(lab)} {noun}"
        try:
            page.locator(sel).first.scroll_into_view_if_needed(timeout=self.ms(1.5))
            if k in ("button", "dialog"):
                self.js(page, "window.__uxr.fillNear(a)", sel)
        except Exception:  # noqa: BLE001
            pass
        mark = self.arm(page, sel)
        err = self.click(page, sel, lab)
        if err:
            if err.startswith("covered"):
                self.find(f"The {_q(lab)} {noun} cannot be clicked: it is {err}")
                self.step(f"click {_q(lab)}", False, err, self.shot(page, f"covered-{lab}", True))
                return False
            self.step(f"click {_q(lab)}", False, err)
            return False
        rep = self.watch(page, mark)
        if not self.after(page, rep, what):
            self.step(f"click {_q(lab)}", False, "the page froze")
            raise _Stop()

        if k == "link":
            fail = self.nav_fail[mark[3]:]
            bad = [r for r in self.nav_resp[mark[2]:] if r[1] >= 400]
            name = os.path.basename(urlparse(it.get("href") or "").path) or it.get("href", "")
            if rep["navigated"] and (fail or bad or page.url.startswith("chrome-error")):
                why = bad[0][1] if bad else (fail[-1][1] if fail else "error page")
                self.find(f"The {_q(lab)} link leads to a page that does not load ({name}: {why})")
                self.step(f"follow link {_q(lab)}", False, f"{name}: {why}", self.shot(page, f"link-{lab}", True))
            elif rep["navigated"] or rep["events"]:
                self.step(f"follow link {_q(lab)}", True, f"opens {name}" + hint)
            else:
                self.find(f"The {_q(lab)} link does nothing when clicked")
                self.step(f"follow link {_q(lab)}", False, "nothing happened", self.shot(page, f"dead-{lab}", True))
            return True

        if k == "anchor":
            target = (it.get("hash") or "#")[1:]
            exists = self.js(page, "!!(document.getElementById(decodeURIComponent(a)) || document.getElementsByName(decodeURIComponent(a)).length)", target)
            if not exists:
                self.find(f"The {_q(lab)} link points to '#{target}', but there is no such section on the page")
                self.step(f"click in-page link {_q(lab)}", False, f"no element with id '{target}'")
            else:
                self.step(f"click in-page link {_q(lab)}", True, ("moves to #" + target if rep.get("scrolled") or rep.get("hash") else "section already in view") + hint)
            return bool(rep["any"])

        if rep.get("dialogs"):
            return self.do_dialog(page, it, rep, hint)

        if k == "menu":
            return self.do_menu(page, it, rep, hint)

        if not rep["any"]:
            self.find(f"The {_q(lab)} {noun} does nothing when clicked")
            self.step(f"click {_q(lab)}", False, "nothing visible changed" + hint, self.shot(page, f"dead-{lab}", True))
            return False
        if rep.get("navigated"):
            bad = [r for r in self.nav_resp[mark[2]:] if r[1] >= 400]
            if bad or page.url.startswith("chrome-error"):
                self.find(f"{what} leads to an error page" + (f" (HTTP {bad[0][1]})" if bad else ""))
                self.step(f"click {_q(lab)}", False, "error page", self.shot(page, f"error-{lab}", True))
                return True
        added = "; ".join(rep.get("added") or [])[:120]
        shot = self.shot(page, f"after-{lab}") if (rep.get("added") or rep.get("navigated")) else None
        self.step(f"click {_q(lab)}", True, (f"shows: {added}" if added else "the page changed") + hint, shot)
        return True

    def do_dialog(self, page, it: dict, rep: dict, hint: str) -> bool:
        self.opened_dialog = True
        lab = it.get("label") or ""
        d = rep["dialogs"][0]
        dname = _q(d.get("label") or lab)
        sels = [x["sel"] for x in rep["dialogs"]]
        shot = self.shot(page, f"dialog-{lab}")
        problems = []
        if not rep.get("focusIn"):
            problems.append("focus stays behind it")
            self.find(f"Opening the {dname} dialog leaves keyboard focus behind it: move focus into the dialog when it opens")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            still = self.js(page, "window.__uxr.openState(a)", sels)
        except Exception:  # noqa: BLE001
            still = True
        if still:
            problems.append("Escape does not close it")
            self.find(f"The {dname} dialog does not close when Escape is pressed")
            closed = False
            try:
                csel = self.js(page, "window.__uxr.closer(a)", sels[0])
                if csel and not self.click(page, csel, "close"):
                    page.wait_for_timeout(250)
                    closed = not self.js(page, "window.__uxr.openState(a)", sels)
            except Exception:  # noqa: BLE001
                closed = False
            if not closed:
                problems.append("no close button works")
                self.find(f"The {dname} dialog has no working way to close it (no close button that works, and Escape does nothing)")
        self.step(f"open dialog with {_q(lab)}", not problems, ("opens " + dname + (": " + ", ".join(problems) if problems else ", Escape closes it")) + hint, shot)
        return True

    def do_menu(self, page, it: dict, rep: dict, hint: str) -> bool:
        lab = it.get("label") or ""
        if not rep["any"]:
            self.find(f"The {_q(lab)} menu button does nothing when clicked")
            self.step(f"open menu {_q(lab)}", False, "nothing visible changed" + hint, self.shot(page, f"dead-{lab}", True))
            return False
        shot = self.shot(page, f"menu-{lab}")
        closed = False
        how = ""
        try:
            mark = self.arm(page, it["sel"])
            page.keyboard.press("Escape")
            r2 = self.watch(page, mark, 300, 50)
            closed = bool(r2.get("muts")) and r2.get("expanded") != "true"
            how = "Escape" if closed else ""
            if not closed:
                mark = self.arm(page, it["sel"])
                if not self.click(page, it["sel"], lab):
                    r3 = self.watch(page, mark, 300, 50)
                    closed = bool(r3.get("muts") or r3.get("navigated"))
                    how = "clicking it again" if closed else ""
        except Exception:  # noqa: BLE001
            closed = False
        if not closed:
            self.find(f"The {_q(lab)} menu cannot be closed again: clicking it a second time and pressing Escape both leave it open")
        self.step(f"open and close menu {_q(lab)}", closed, (f"opens; closes with {how}" if closed else "opens but does not close") + hint, shot)
        return True

    def do_form(self, page, it: dict, phase: str, hint: str) -> bool:
        lab = it.get("label") or ""
        fname = f"{_q(lab)} form" if lab else "form"
        sel = it["sel"]
        sub = self.js(page, "window.__uxr.submitter(a)", sel) or {}
        empty = phase == "empty"
        if empty and not it.get("textish"):
            return False
        if not empty:
            self.js(page, "window.__uxr.fill(a)", sel)
        mark = self.arm(page, sub.get("sel") or sel)
        self.js(page, "window.__uxr.watchSubmit()")
        try:
            if sub.get("enter"):
                page.locator(sub["sel"]).first.press("Enter", timeout=self.ms(1.5))
            elif sub.get("sel"):
                err = self.click(page, sub["sel"], sub.get("label") or "")
                if err:
                    if err.startswith("covered"):
                        self.find(f"The send button of the {fname} cannot be clicked: it is {err}")
                    self.step(f"submit the {fname}" + (" empty" if empty else ""), False, err)
                    return False
            else:
                page.locator(sel).first.evaluate("f => f.requestSubmit ? f.requestSubmit() : f.submit()")
        except Exception as e:  # noqa: BLE001
            self.step(f"submit the {fname}" + (" empty" if empty else ""), False, str(e).splitlines()[0][:160])
            return False
        rep = self.watch(page, mark, 600, 350)
        action = f"submit the {fname} empty" if empty else f"fill and submit the {fname}"
        if not self.after(page, rep, f"Submitting the {fname}" + (" empty" if empty else "")):
            self.step(action, False, "the page froze")
            raise _Stop()
        subm = rep.get("submit") or {}
        added = [x for x in rep.get("added") or [] if x]
        text = " ".join(added)
        errorish = bool(ERROR_RE.search(text)) or (rep.get("inv") or 0) > 0
        successish = bool(SUCCESS_RE.search(text))
        invalid = None
        if not rep.get("navigated"):
            try:
                invalid = self.js(page, "window.__uxr.firstInvalid(a)", sel)
            except Exception:  # noqa: BLE001
                invalid = None
        bad = [r for r in self.nav_resp[mark[2]:] if r[1] >= 400]
        said = (added[0] if added else "")[:100]

        if empty:
            if not subm.get("fired") and invalid:
                self.step(action, True, f"the browser blocks it and marks {_q(invalid['label'])} as required")
                return True
            if errorish and not successish:
                self.step(action, True, f"shows: {said}" if said else "marks the fields as invalid", self.shot(page, f"empty-{lab}"))
                return True
            if successish:
                self.find(f"The {fname} accepts empty fields and says '{said}'. Check the fields and show what is missing")
                self.step(action, False, f"accepted empty: {said}", self.shot(page, f"empty-{lab}", True))
                return True
            if rep.get("navigated"):
                self.find(f"The {fname} submits with empty fields: the page " +
                          ("shows an error" + (f" (HTTP {bad[0][1]})" if bad else "") if bad or self.nav_fail[mark[3]:] or page.url.startswith("chrome-error")
                           else "reloads or moves on") +
                          " with no message about the missing fields")
                self.step(action, False, "sent with empty fields", self.shot(page, f"empty-{lab}", True))
                return True
            if not rep["any"]:
                if subm.get("fired"):
                    self.find(f"The {fname} submits with empty fields and shows no message")
                else:
                    self.find(f"Sending the {fname} with empty fields does nothing and shows no message about what is missing")
                self.step(action, False, "no message", self.shot(page, f"empty-{lab}", True))
                return True
            self.step(action, True, ("the page changed: " + "; ".join(added)[:120]) if added else "the page changed")
            return True

        # filled with plausible values
        if subm.get("blocked"):
            self.step(action, True, f"it would send the form to {subm['blocked']}; that was not allowed during the review" + hint)
            return True
        if not subm.get("fired") and invalid:
            self.find(f"The {fname} would not accept plausible test values: {_q(invalid['label'])} says '{invalid['msg']}'")
            self.step(action, False, f"{invalid['label']}: {invalid['msg']}", self.shot(page, f"form-{lab}", True))
            return True
        if rep.get("navigated"):
            if bad or self.nav_fail[mark[3]:] or page.url.startswith("chrome-error"):
                self.find(f"Sending the {fname} leads to an error page" + (f" (HTTP {bad[0][1]})" if bad else ""))
                self.step(action, False, "error page", self.shot(page, f"form-{lab}", True))
                return True
            same = page.url.split("?")[0].split("#")[0] == self.url.split("?")[0].split("#")[0]
            if same and not successish:
                self.find(f"Sending the {fname} just reloads the page and shows no confirmation")
                self.step(action, False, "page reloaded, no message", self.shot(page, f"form-{lab}", True))
                return True
            self.step(action, True, f"goes to {os.path.basename(urlparse(page.url).path) or page.url}" + hint, self.shot(page, f"form-{lab}"))
            return True
        if successish:
            self.step(action, True, f"shows: {said}" + hint, self.shot(page, f"form-{lab}"))
            return True
        if errorish and added:
            self.find(f"The {fname} shows an error after it is filled with plausible values and sent: '{said}'")
            self.step(action, False, f"error: {said}", self.shot(page, f"form-{lab}", True))
            return True
        if not rep["any"]:
            self.find(f"The {fname} shows nothing after it is sent with plausible values (no success or error message)")
            self.step(action, False, "no visible result", self.shot(page, f"form-{lab}", True))
            return True
        self.step(action, True, (("shows: " + "; ".join(added)[:120]) if added else "the page changed") + hint, self.shot(page, f"form-{lab}"))
        return True

    def do_phone(self, page, width: int) -> None:
        try:
            ov = self.js(page, "window.__uxr.overflow()")
        except Exception:  # noqa: BLE001
            ov = None
        if ov:
            self.find(f"At {width} px wide the page scrolls sideways (content is {ov['width']} px wide)" +
                      (f"; {ov['what']} is wider than the screen" if ov.get("what") else ""))
            self.step(f"check width at {width}px", False, f"content {ov['width']} px wide", self.shot(page, f"overflow-{width}", True))
        if width >= 700 or not self.desktop_nav:
            return
        nav = self.js(page, "window.__uxr.navInfo()") or {}
        if self.desktop_nav.get("visible", 0) < 2 or nav.get("visible", 0) >= max(2, self.desktop_nav["visible"] // 2 + 1):
            return
        self.guard()
        tog = self.js(page, "window.__uxr.navToggle()")
        if not tog:
            self.find(f"At {width} px wide the navigation links are hidden and there is no menu button to show them")
            self.step(f"find the menu button at {width}px", False, "no menu button", self.shot(page, f"nav-{width}", True))
            return
        mark = self.arm(page, tog["sel"])
        err = self.click(page, tog["sel"], tog["label"])
        if err:
            self.find(f"At {width} px wide the menu button {_q(tog['label'])} cannot be pressed ({err})")
            self.step(f"open the menu at {width}px", False, err)
            return
        rep = self.watch(page, mark, 500, 300)
        self.after(page, rep, f"Opening the menu at {width} px")
        after = self.js(page, "window.__uxr.navInfo()") or {}
        if after.get("visible", 0) > nav.get("visible", 0):
            self.step(f"open the menu at {width}px", True, f"{_q(tog['label'])} shows {after['visible']} links", self.shot(page, f"nav-open-{width}", True))
        else:
            self.find(f"At {width} px wide the menu button {_q(tog['label'])} does not show the navigation links when pressed")
            self.step(f"open the menu at {width}px", False, "links stay hidden", self.shot(page, f"nav-{width}", True))

    # ---------- the whole run -------------------------------------------------------------------------------------------
    def run(self, browser) -> None:
        for i, width in enumerate(self.widths):
            if self.left() < 2:
                self.step(f"load at {width}px", False, "skipped: out of time")
                continue
            ctx = None
            try:
                ctx, page = self.new_page(browser, width)
                if not self.load(page, width, first=i == 0):
                    if i == 0:
                        return
                    continue
                if i == 0:
                    self.main_width(page, width)
                else:
                    self.load_problems(page, width)
                    self.do_phone(page, width)
            except _Stop:
                self.step("stop", False, "the time limit was reached; the rest was not tried")
                return
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:  # noqa: BLE001
                        pass

    def main_width(self, page, width: int) -> None:
        inv = self.js(page, "window.__uxr.inventory()") or {"items": []}
        self.desktop_nav = inv.get("nav")
        self.load_problems(page, width)
        items = inv.get("items") or []
        plan = self.rank(items)
        budget = self.max_steps
        if budget and any(it.get("primary") for it in items):
            self.guard()
            self.do_hover(page, items)
            budget -= 1
        self.guard()
        self.do_tab(page, inv)
        dirty = True                # the hover and Tab checks moved the mouse and focus
        queue = []
        for it in plan:
            if it["kind"] == "form":
                if it.get("textish"):
                    queue.append((it, "empty"))
                queue.append((it, "filled"))
            else:
                queue.append((it, ""))
        for it, phase in queue:
            if budget <= 0:
                self.skipped += 1
                continue
            self.guard(2.5)
            if dirty and not self.reset(page):
                self.step("reload the page", False, "the page could not be loaded again")
                return
            try:
                page.locator(it["sel"]).first.wait_for(state="visible", timeout=self.ms(1))
                exists = True
            except Exception:  # noqa: BLE001
                exists = False
            if not exists:
                dirty = False
                continue
            dirty = self.do_item(page, it, phase)
            budget -= 1
            self.tried += 1
        if self.skipped:
            self.step("more controls", True, f"{self.skipped} more steps were not tried (max_steps)")
        self.request_checks(inv)

    def request_checks(self, inv: dict) -> None:
        """Things the request names that the page does not have at all."""
        rw = self.req_words
        if rw & {"form", "login", "signup", "register", "subscribe", "newsletter", "checkout"} and not inv.get("fields"):
            self.find("The request asks for a form, but the page has no form fields at all")
        if rw & {"modal", "dialog", "popup", "lightbox"} and not self.opened_dialog:
            self.find("The request mentions a modal or dialog, but no control on the page opened one"
                      + (" (not every control was tried)" if self.skipped else ""))
        if rw & {"tab", "tabs", "tabbed"} and not any(it["kind"] == "tab" for it in inv.get("items") or []) and not rw & {"key", "keyboard", "focus"}:
            self.find("The request mentions tabs, but the page has no tabs (no elements with role=tab)")

    def result(self) -> dict:
        return {"steps": list(self.steps), "findings": list(self.findings), "shots": list(self.shots),
                "console_errors": list(self.console)}


def exercise(target: str, request: str, out_dir: str, *, browser=None, widths=(1280, 390), max_steps: int = 12,
             timeout_s: float = 45, allow=None, root: str | None = None) -> dict:
    """Try the page at `target` the way a user would and say what went wrong. Never raises.

    target: a file path (opened as file://) or an http URL on 127.0.0.1 or localhost. request: the user's original
    request, used to decide what to try first. browser: an already-launched Playwright sync Browser, left open; when
    None, a headless Chromium is launched and closed (killed at timeout_s if it hangs). widths: the first width is where
    controls are tried; the others are checked for sideways scrolling and, under 700 px, for a working menu button.
    max_steps bounds the controls tried at the first width; the Tab walk and the phone checks always run.
    allow: the origins the page may load from (the look's own server, a dev server the chat started); everything else
    is blocked, files included. Without it, only files inside `root` (default: the page's folder) and http on
    127.0.0.1 or localhost load.

    Returns {"steps": [{"action", "ok", "note", "shot"}], "findings": [str], "shots": [str], "console_errors": [str], "ms": int}.
    """
    t0 = time.monotonic()
    try:
        timeout_s = float(timeout_s) if timeout_s and float(timeout_s) > 0 else 45.0
    except (TypeError, ValueError):
        timeout_s = 45.0
    deadline = t0 + timeout_s
    res = {"steps": [], "findings": [], "shots": [], "console_errors": []}
    run = None
    try:
        url = _target_url(target)
        if allow is not None and not _is_local(url, None, allow):
            raise ValueError("the page is not on an address this check may open")
        if root is None and url.startswith("file:"):
            root = os.path.dirname(_file_path(url))
        run = _Run(url, request, out_dir, widths, max_steps, deadline, root=root, allow=allow)
        if browser is not None:
            try:
                run.run(browser)
            except _Stop:
                pass
        else:
            _run_own(run, deadline)
        res = run.result()
    except Exception as e:  # noqa: BLE001
        if run is not None:
            res = run.result()
        msg = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        res["findings"].append(f"could not exercise the page: {msg}")
    res["ms"] = int((time.monotonic() - t0) * 1000)
    return res


class Worker:
    """Playwright's sync API on a thread of its own, with the processes it starts known by pid: the Playwright driver
    and every Chromium under it. kill() ends them all, from any thread, so a page stuck in an endless script or a
    browser that stops answering never keeps the caller waiting or leaves a process behind. Only processes this worker
    started are touched; nothing is ever killed by name."""

    def __init__(self, name: str = "meshpool-browser"):
        import psutil
        try:
            self.before = {c.pid for c in psutil.Process().children()}
        except Exception:  # noqa: BLE001
            self.before = set()
        self.name = name
        self.pids: list = []
        self.procs: list = []          # psutil.Process objects noted while they ran (a pid reused later is not hit)
        self.exact = False
        self.killed = False
        self.error = None
        self.thread: threading.Thread | None = None

    def start(self, work) -> "Worker":
        """Run work(playwright) on the worker's thread. An exception is kept in .error (not after a kill)."""
        def body():
            sp = None
            try:
                from playwright.sync_api import sync_playwright
                manager = sync_playwright()
                sp = manager.start()
                exact = _exact_driver_pid(manager, sp)
                self.exact = bool(exact)
                self.pids = [exact] if exact else _driver_pids(self.before)
                if self.killed:
                    return
                work(sp)
            except _Stop:
                pass
            except Exception as e:  # noqa: BLE001
                if not self.killed:
                    self.error = e
            finally:
                if sp is not None:
                    try:
                        sp.stop()
                    except Exception:  # noqa: BLE001
                        pass
        self.thread = threading.Thread(target=body, name=self.name, daemon=True)
        self.thread.start()
        return self

    def note(self) -> None:
        """Remember the processes under the driver now (call it once the browser is up)."""
        try:
            have = {p.pid for p in self.procs}
            self.procs += [p for p in _tree(self.pids) if p.pid not in have]
        except Exception:  # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def join(self, seconds: float) -> None:
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(max(0.0, seconds))

    def kill(self, wait: float = 5.0) -> None:
        """End the driver and every browser process under it, then wait up to `wait` s for them and the thread."""
        self.killed = True
        procs = _tree(self.pids or _driver_pids(self.before))
        have = {p.pid for p in procs}
        procs += [p for p in self.procs if p.pid not in have]
        t_end = time.monotonic() + wait
        _kill(procs, min(3.0, wait))
        self.join(t_end - time.monotonic())

    def finish(self) -> None:
        """After the thread is done: end anything it left (only when its processes are known exactly)."""
        if self.exact or self.killed:
            left = [p for p in _tree(self.pids) + self.procs if _running(p)]
            if left:
                _kill(left)


def _running(p) -> bool:
    try:
        import psutil
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001
        return False


def _run_own(run: _Run, deadline: float) -> None:
    """Launch a browser in a worker thread, so the caller's thread (which may run an asyncio loop) is never blocked
    past the deadline, and kill the browser and the Playwright driver if the deadline passes."""
    w = Worker("review-interact")

    def work(sp):
        b = launch_browser(sp)
        try:
            w.note()
            if not w.killed:
                run.run(b)
        finally:
            if not w.killed:
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
    w.start(work)
    w.join(max(0.5, deadline - time.monotonic()))
    if w.alive():
        w.kill()
        run.find(f"could not exercise the page: it did not finish within {run.total:.0f} s "
                 "(the page or the browser stopped responding); the steps above are what was done")
    w.finish()
    if w.error is not None:
        raise w.error
