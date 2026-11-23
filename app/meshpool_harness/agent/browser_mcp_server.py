"""Meshpool Browser: browser use for any harness, as an MCP server. Built in; nothing to install.

The agent drives a real browser that stays out of the way: hidden, and shown inside the app's Browser panel
(agent/browser_live.py), where the user can click and type too. MH_BROWSER_HEADLESS=0 gives it a window of its own.
Which browser is the MH_BROWSER_USE setting (browser_use.py):
meshpool (default: the app's bundled Chromium; in a source checkout a Chromium fetched once; a system Chrome or Edge
only as a last resort, when no Chromium can be found or installed), installed (the Chrome, Edge or Brave program on this computer, with a Meshpool-owned profile) or attach
(connect to a browser started with --remote-debugging-port). Meshpool's profile lives in the Meshpool data folder, so
sign-ins persist between sessions and never touch your everyday browser profile.

Pages are described as text: title, address, the visible text, and numbered interactive elements ([e12] button
"Sign in"). The agent acts on those numbers, so it works with any model, including ones that cannot see images.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from meshpool_harness import browser_use as BU
from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, image_png, serve, text

SNAPSHOT_JS = r"""() => {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link],[role=tab],' +
              '[role=menuitem],[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],' +
              '[role=textbox],[contenteditable=""],[contenteditable=true],[onclick]';
  document.querySelectorAll('[data-mp-ref]').forEach(e => e.removeAttribute('data-mp-ref'));
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    if (r.bottom < -innerHeight || r.top > innerHeight * 2.5) continue;
    const ref = 'e' + (++i); el.setAttribute('data-mp-ref', ref);
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    const lab = (el.labels && el.labels.length) ? el.labels[0].innerText : '';
    let name = el.getAttribute('aria-label') || lab || el.getAttribute('title') || (tag === 'select' ? '' : el.innerText) || el.getAttribute('placeholder')
      || el.getAttribute('alt') || el.getAttribute('name') || (tag === 'input' && ['submit','button'].includes(type) ? el.value : '') || '';
    name = String(name).trim().replace(/\s+/g, ' ').slice(0, 90);
    let line = `[${ref}] ${el.getAttribute('role') || tag}${type ? ':' + type : ''} "${name}"`;
    if (tag === 'a' && el.getAttribute('href')) line += ' -> ' + el.getAttribute('href').slice(0, 90);
    if ((tag === 'input' || tag === 'textarea') && !['checkbox','radio','submit','button','password'].includes(type) && el.value)
      line += ` value="${String(el.value).slice(0, 60)}"`;
    if (type === 'checkbox' || type === 'radio') line += el.checked ? ' (checked)' : ' (unchecked)';
    if (tag === 'select') line += ` selected="${(el.options[el.selectedIndex] || {}).text || ''}" options=[${[...el.options].slice(0, 15).map(o => o.text.trim()).join(' | ')}]`;
    if (window.__mpStates) line += __STATES__(el).replace(' (disabled)', '');   // MH_BROWSER_STATES
    if (el.disabled) line += ' (disabled)';
    if (r.top > innerHeight || r.bottom < 0) line += ' (scroll to see)';
    out.push(line);
    if (out.length >= 300) break;
  }
  const body = document.body ? document.body.innerText : '';
  return {title: document.title, url: location.href, elements: out, text: body.replace(/\n{3,}/g, '\n\n').slice(0, 7000),
          scroll: Math.round(scrollY), height: document.documentElement.scrollHeight, vh: innerHeight};
}"""
from meshpool_harness.core.webfix import STATES_JS  # noqa: E402
# The app's browser runs with the fixes measured on MiniWoB++: read-only picker fields,
# late menus, invalid/disabled/read-only markers, key-driven widgets. An explicit "0" in the environment turns one off.
for _flag in ("MH_BROWSER_READONLY", "MH_BROWSER_SETTLE", "MH_BROWSER_STATES", "MH_BROWSER_KEYTYPE"):
    os.environ.setdefault(_flag, "1")
SNAPSHOT_JS = SNAPSHOT_JS.replace("__STATES__", STATES_JS)


def load_playwright():
    """The Playwright API. When the package is missing (a source or terminal install from before it was a dependency),
    it is installed once, here, instead of failing: browser use works the first time it is asked for."""
    import importlib
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        pass
    from meshpool_harness import bundled
    if bundled.packaged():
        raise ToolFailed("Browser use is missing from this install. Reinstall Meshpool.")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "playwright>=1.40"],
                       capture_output=True, text=True, timeout=600)
    importlib.invalidate_caches()
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        raise ToolFailed("Browser use needs the playwright package and it could not be installed "
                         f"({(r.stderr or r.stdout or 'no output')[-200:].strip()}). Run: meshpool install-browser")


def _home() -> Path:
    return Path(os.environ.get("MESHPOOL_HOME") or Path.home() / ".meshpool")


class Browser:
    """One Playwright session on a dedicated thread (Playwright's sync API is bound to the thread that started it)."""

    def __init__(self, profile: str = ""):
        self.q: queue.Queue = queue.Queue()
        self.pw = self.ctx = self.page = self.cdp = None
        self.mode = ""
        self.channel = ""
        self.profile = profile              # a profile folder name of its own (the in-app sign-in browser)
        self.notice = ""                    # said once, in the next page description (a last-resort browser was used)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            fn, box, done = self.q.get()
            try:
                box["r"] = fn()
            except BaseException as e:  # noqa: BLE001
                box["e"] = e
            done.set()

    def run(self, fn, timeout: float = 240):
        box, done = {}, threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            raise ToolFailed("The browser did not respond in time.")
        if "e" in box:
            raise box["e"]
        return box["r"]

    # ---------- start ----------------------------------------------------------------------------------------------
    def _use_cached_chromium(self, headless: bool) -> bool:
        """A Chromium already on this computer (any revision): used instead of downloading a second copy."""
        path = BU.find_playwright_chromium()
        if not path:
            return False
        try:
            self.ctx = self._launch(_home() / "browser-profile" / "chromium-cached", headless=headless, executable_path=path)
        except Exception:  # noqa: BLE001 - it may be unusable (missing system libraries): fall through to the download
            self.ctx = None
            return False
        self.channel = "chromium:cached"
        return True

    def _install_chromium(self):
        # Installs where the Playwright driver looks (the process's PLAYWRIGHT_BROWSERS_PATH, else its default
        # cache): the driver reads that variable once, at start.
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        if sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(4, "--with-deps")                    # a server or container: the system libraries too
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ), timeout=900)
        if r.returncode != 0:
            raise ToolFailed("Could not download the browser engine: " + (r.stderr or r.stdout)[-400:])

    def _alive(self) -> bool:
        """Whether the browser we started (or attached to) is still there; resets state when the user closed it."""
        try:
            if self.cdp is not None and not self.cdp.is_connected():
                raise RuntimeError("detached")
            if self.page is None or self.page.is_closed():
                self.page = (self.ctx.pages[-1] if self.ctx.pages and self.cdp is None else self.ctx.new_page())
            return True
        except Exception:  # noqa: BLE001  the window was closed by the user: start again
            self.ctx = self.page = self.cdp = None
            return False

    def ensure(self):
        from meshpool_harness import bundled
        mode = "meshpool" if self.profile == "signin" else BU.mode()   # the sign-in browser is never the user's own
        if mode == "meshpool":
            why = bundled.browser_needs_newer_macos()   # the app opens on macOS 11, its Chromium needs 13
            if why:
                raise ToolFailed(why)
        if self.ctx is not None and self._alive():
            return
        own = mode == "meshpool" and bundled.packaged()     # the app's own Chromium (app/vendor/browsers)
        if own:
            # before the driver starts: it reads this once, so setting it later would never reach it
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled.browsers_dir())
        sync_playwright = load_playwright()
        if self.pw is None:
            self.pw = sync_playwright().start()
        if mode == "meshpool":
            headless = BU.headless()                        # the default: hidden, shown in the app's Browser panel
        else:
            headless = os.environ.get("MH_BROWSER_HEADLESS") == "1"   # installed: opt-in, its window as before
        if mode == "attach":
            self._attach()
        elif mode == "installed":
            self._launch_installed(headless)
        else:
            self._launch_meshpool(headless, own)
        self.mode = mode
        self.ctx.set_default_timeout(20000)
        if self.page is None or self.page.is_closed():
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _launch(self, prof: Path, **kw):
        prof.mkdir(parents=True, exist_ok=True)
        return self.pw.chromium.launch_persistent_context(
            str(prof), headless=kw.pop("headless"), no_viewport=True,
            args=["--no-first-run", "--no-default-browser-check", "--window-size=1280,900"], **kw)

    def _launch_meshpool(self, headless: bool, own: bool):
        explicit = os.environ.get("MH_BROWSER_CHANNEL") if not own else None
        channels = [explicit] if explicit is not None else [""]     # Meshpool's Chromium: never a system browser first
        last = None
        for attempt in range(2):
            for ch in channels:
                try:
                    self.ctx = self._launch(_home() / "browser-profile" / (self.profile or ch or "chromium"),
                                            headless=headless, channel=ch or None)
                    self.channel = ch or "chromium"
                    return
                except Exception as e:  # noqa: BLE001
                    last = e
            # Download Chromium only when it is missing (never when the profile is merely in use), and never in the
            # packaged app: Chromium ships inside it.
            if attempt == 0 and not own and "xecutable doesn't exist" in str(last):
                if self._use_cached_chromium(headless):     # one is already here: no download
                    return
                try:
                    self._install_chromium()
                except ToolFailed as e:
                    last = e
                    break
                channels = [""]
            else:
                break
        if not own and explicit is None and self._launch_system_fallback(headless):
            return
        raise ToolFailed(f"Could not start a browser: {last}")

    def _launch_system_fallback(self, headless: bool) -> bool:
        """The last resort, source and terminal installs only: no Chromium could be found or installed, so a Chrome or
        Edge that is already on this computer runs hidden, and the agent is told."""
        names = {"chrome": "Google Chrome", "msedge": "Microsoft Edge"}
        for ch in (["chrome"] if sys.platform == "linux" else ["chrome", "msedge"]):
            try:
                self.ctx = self._launch(_home() / "browser-profile" / (self.profile or ch), headless=headless, channel=ch)
            except Exception:  # noqa: BLE001
                continue
            self.channel = ch
            self.notice = (f"Note: Meshpool's own browser could not be found or installed, so this page runs in "
                           f"{names[ch]} from this computer instead.")
            return True
        return False

    def _launch_installed(self, headless: bool):
        found = BU.pick_installed()
        if not found:
            raise ToolFailed(BU.installed_missing_message())
        try:
            self.ctx = self._launch(_home() / "browser-profile" / f"installed-{found['key']}", headless=headless,
                                    executable_path=found["path"])
        except Exception as e:  # noqa: BLE001
            raise ToolFailed(f"Could not start {found['name']} ({found['path']}): {e}. Close any window that is "
                             "using Meshpool's profile folder, or choose Meshpool's browser under Settings > Capabilities > Browser for browser use.")
        self.channel = "installed:" + found["key"]

    def _attach(self):
        url = BU.cdp_url()
        err = BU.probe_cdp(url)
        if err:
            raise ToolFailed(err)
        try:
            self.cdp = self.pw.chromium.connect_over_cdp(url, timeout=15000)
        except Exception as e:  # noqa: BLE001
            raise ToolFailed(f"Could not connect to the browser at {url}: {e}. " + BU.attach_hint(url))
        self.ctx = self.cdp.contexts[0] if self.cdp.contexts else self.cdp.new_context()
        self.page = self.ctx.new_page()             # a tab of our own: the user's open tab is never navigated away
        self.channel = "attached"

    def shutdown(self):
        """Close the browser cleanly (so cookies and local storage reach the profile folder) or, when attached, only
        disconnect. Called when the agent closes the server."""
        try:
            if self.cdp is not None:
                self.cdp.close()                    # a CDP connection: leaves the user's browser running
            elif self.ctx is not None:
                self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        self.ctx = self.page = self.cdp = None
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self.pw = None

    # ---------- helpers ------------------------------------------------------------------------------------------
    def settle(self):
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001
            pass
        from meshpool_harness.core.webfix import settle as _settle   # MH_BROWSER_SETTLE: late menus and pop-ups
        _settle(self.page)

    def snapshot(self, note: str = "") -> str:
        self.settle()
        if os.environ.get("MH_BROWSER_STATES") == "1":
            self.page.evaluate("() => { window.__mpStates = true; }")
        s = self.page.evaluate(SNAPSHOT_JS)
        tabs = len(self.ctx.pages)
        head = [note] if note else []
        if self.notice:
            head.append(self.notice)
            self.notice = ""
        head += [f"Page: {s['title'] or '(no title)'}", f"Address: {s['url']}",
                 f"Scroll: {s['scroll']} of {max(0, s['height'] - s['vh'])} px" + (f" | Tabs open: {tabs}" if tabs > 1 else "")]
        body = s["text"].strip()
        return ("\n".join(head) + "\n\nInteractive elements (use the [eN] number):\n" + ("\n".join(s["elements"]) or "(none)")
                + "\n\nVisible text:\n" + (body or "(empty)"))

    def el(self, ref: str):
        ref = str(ref).strip().strip("[]")
        loc = self.page.locator(f'[data-mp-ref="{ref}"]')
        if loc.count() == 0:
            raise ToolFailed(f"No element {ref} on the page now. Call browser_snapshot to get fresh numbers.")
        return loc.first


B = Browser()


def browser_open(url: str) -> str:
    def go():
        B.ensure()
        u = url.strip()
        if "://" not in u and not u.startswith(("about:", "file:")):
            u = "https://" + u
        B.page.goto(u, wait_until="domcontentloaded", timeout=45000)
        return B.snapshot()
    return B.run(go)


def browser_snapshot() -> str:
    def go():
        B.ensure()
        return B.snapshot()
    return B.run(go)


def browser_click(ref: str, double: bool = False) -> str:
    def go():
        B.ensure()
        pages = len(B.ctx.pages)
        e = B.el(ref)
        e.scroll_into_view_if_needed(timeout=5000)
        e.dblclick() if double else e.click()
        note = ""
        for _ in range(8):                                  # a click that opens a new tab does so a moment later
            B.page.wait_for_timeout(250)
            if len(B.ctx.pages) > pages:
                break
        if len(B.ctx.pages) > pages:                        # the click opened a new tab: follow it
            B.page = B.ctx.pages[-1]
            note = "The click opened a new tab; now on that tab."
        return B.snapshot(note)
    return B.run(go)


def browser_type(ref: str, text_: str = "", submit: bool = False, **kw) -> str:
    value = text_ or kw.get("text", "")

    def go():
        B.ensure()
        e = B.el(ref)
        note = ""
        try:
            from meshpool_harness.core.webfix import smart_fill   # read-only and key-driven fields (MH_BROWSER_*)
            note = smart_fill(e, value, 30000) or ""
        except Exception:  # noqa: BLE001  not a plain field (rich editors): type key by key
            e.click()
            B.page.keyboard.type(value, delay=15)
        if submit:
            e.press("Enter")
            B.page.wait_for_timeout(500)
        return B.snapshot(note)
    return B.run(go)


def browser_select(ref: str, option: str) -> str:
    def go():
        B.ensure()
        e = B.el(ref)
        try:
            e.select_option(label=option)
        except Exception:  # noqa: BLE001
            e.select_option(option)
        return B.snapshot()
    return B.run(go)


def browser_press(key: str) -> str:
    def go():
        B.ensure()
        B.page.keyboard.press(key)
        B.page.wait_for_timeout(400)
        return B.snapshot()
    return B.run(go)


def browser_scroll(direction: str = "down", pages: float = 1) -> str:
    def go():
        B.ensure()
        h = B.page.evaluate("innerHeight")
        dy = int(h * 0.85 * float(pages or 1)) * (-1 if str(direction).lower() in ("up", "top") else 1)
        B.page.mouse.wheel(0, dy)
        B.page.wait_for_timeout(400)
        return B.snapshot()
    return B.run(go)


def browser_back() -> str:
    def go():
        B.ensure()
        B.page.go_back(wait_until="domcontentloaded")
        return B.snapshot()
    return B.run(go)


def browser_tabs(switch_to: int | None = None, close: int | None = None) -> str:
    def go():
        B.ensure()
        pages = B.ctx.pages
        if close is not None and 0 <= int(close) < len(pages) and len(pages) > 1:
            pages[int(close)].close()
            pages = B.ctx.pages
            B.page = pages[-1]
        if switch_to is not None and 0 <= int(switch_to) < len(pages):
            B.page = pages[int(switch_to)]
            B.page.bring_to_front()
            return B.snapshot(f"Switched to tab {int(switch_to)}.")
        return "Tabs:\n" + "\n".join(f"{i}: {p.title() or '(no title)'} - {p.url}" + (" (current)" if p == B.page else "")
                                    for i, p in enumerate(pages))
    return B.run(go)


def browser_screenshot(full_page: bool = False) -> list:
    def go():
        B.ensure()
        B.settle()
        png = B.page.screenshot(full_page=bool(full_page), type="png")
        out = Path(os.environ.get("MESHPOOL_PROJECT") or os.getcwd()) / "Meshpool outputs" / "screenshots"
        try:
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"browser-{time.strftime('%Y%m%d-%H%M%S')}.png"
            f.write_bytes(png)
            where = f"Saved to {f}."
        except OSError:
            where = ""
        return [text(f"Screenshot of {B.page.url}. {where}"), image_png(png)]
    return B.run(go)


def browser_wait(seconds: float = 2) -> str:
    def go():
        B.ensure()
        B.page.wait_for_timeout(1000 * min(30.0, max(0.0, float(seconds))))
        return B.snapshot()
    return B.run(go, timeout=60)


def browser_close() -> str:
    def go():
        was = B.cdp is not None
        B.shutdown()
        return "Disconnected from your browser; it stays open." if was else "Browser closed."
    return B.run(go)


_SHOTS_KEEP_SECONDS = 24 * 3600
_pruned = False


def _outputs_dir(sub: str) -> Path:
    """Where the audit and view pictures go: a temp folder outside the user's project (one per project and server
    process), never "Meshpool outputs", so no ui-audit or views folder is left behind in their work. Folders older than
    a day are removed. MH_SHOTS_DIR overrides the base folder. The tools print the path they used."""
    global _pruned
    import hashlib
    import shutil
    import tempfile
    import time
    project = str(Path(os.environ.get("MESHPOOL_PROJECT") or os.getcwd()).resolve())
    base = Path(os.environ.get("MH_SHOTS_DIR") or Path(tempfile.gettempdir()) / "meshpool-shots")
    if not _pruned:
        _pruned = True
        try:
            for d in base.glob("*/*"):
                if d.is_dir() and time.time() - d.stat().st_mtime > _SHOTS_KEEP_SECONDS:
                    shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    out = base / hashlib.sha1(project.encode()).hexdigest()[:10] / str(os.getpid()) / sub
    out.mkdir(parents=True, exist_ok=True)
    return out


def _launcher():
    """A separate headless Chromium for audits and renders (fresh viewports, never the user's window)."""
    from meshpool_harness import ui_audit as UA
    return UA.launcher(B.pw)


def _images(shots, limit: int = 4) -> list:
    out = []
    for s in list(shots)[:limit]:
        try:
            out.append(image_png(Path(s).read_bytes()))
        except OSError:
            pass
    return out


def ui_audit(url: str, views: str = "") -> list:
    from meshpool_harness import ui_audit as UA

    def go():
        B.ensure()
        v = tuple(x.strip() for x in views.split(",") if x.strip() in UA.VIEWS) or tuple(UA.VIEWS)
        res = UA.audit(B.pw, url, views=v, shots_dir=_outputs_dir("ui-audit"), launch=_launcher())
        where = ("\nPictures saved (temporary, outside your project): " + ", ".join(str(x) for x in list(res["shots"])[:6])
                 if res["shots"] else "")
        return [text(res["text"] + where)] + _images(res["shots"])
    return B.run(go, timeout=300)


def ui_smoke(url: str) -> list:
    from meshpool_harness import ui_smoke as US

    def go():
        B.ensure()
        res = US.smoke(B.pw, url, launch=_launcher())
        return [text(res["text"])]
    return B.run(go, timeout=300)


def view_output(path: str, pages: str = "") -> list:
    from meshpool_harness import view as V

    def go():
        k = V.kind(path)
        if k in ("web", "table"):
            B.ensure()
        try:
            res = V.render(path, pages or None, _outputs_dir("views"), launch=_launcher() if k in ("web", "table") else None)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            raise ToolFailed(str(e))
        files = [str(f) for _, f in res["shots"]]
        head = f"{Path(res['file']).name}: " + " ".join(res["notes"]) + "\nPictures saved: " + ", ".join(files)
        ocr_text = V.reading(res["shots"])
        return [text(head + ("\n" + ocr_text if ocr_text else ""))] + _images(files)
    return B.run(go, timeout=300)


S = {"type": "string"}
TOOLS = {
    "browser_open": Tool(browser_open, "Open a web address in the browser window and describe the page.", {"url": S}, ["url"]),
    "browser_snapshot": Tool(browser_snapshot, "Describe the current page: address, visible text and numbered "
                             "interactive elements. Call it again after the page changes."),
    "browser_click": Tool(browser_click, "Click an element by its number from the latest snapshot, for example e12.",
                          {"ref": S, "double": {"type": "boolean"}}, ["ref"]),
    "browser_type": Tool(browser_type, "Type into a field by its number. Replaces what is there. submit=true presses Enter.",
                         {"ref": S, "text": S, "submit": {"type": "boolean"}}, ["ref", "text"]),
    "browser_select": Tool(browser_select, "Choose an option in a drop-down by its number and the option's text.",
                           {"ref": S, "option": S}, ["ref", "option"]),
    "browser_press": Tool(browser_press, "Press a key or shortcut on the page, such as Enter, Escape, Tab, "
                          "ArrowDown or Control+A.", {"key": S}, ["key"]),
    "browser_scroll": Tool(browser_scroll, "Scroll the page up or down by screens.",
                           {"direction": {"type": "string", "enum": ["down", "up"]}, "pages": {"type": "number"}}),
    "browser_back": Tool(browser_back, "Go back to the previous page."),
    "browser_tabs": Tool(browser_tabs, "List open tabs, switch to one by index, or close one by index.",
                         {"switch_to": {"type": "integer"}, "close": {"type": "integer"}}),
    "browser_screenshot": Tool(browser_screenshot, "Take a screenshot of the page (saved in the project's "
                               "Meshpool outputs folder). Useful to check layout or for models that can see images.",
                               {"full_page": {"type": "boolean"}}),
    "browser_wait": Tool(browser_wait, "Wait a few seconds for the page to finish something, then describe it.",
                         {"seconds": {"type": "number"}}),
    "browser_close": Tool(browser_close, "Close the browser window."),
    "ui_audit": Tool(ui_audit, "Check a web page you built (a file path or address) on a wide screen, a tablet and a "
                     "phone, in light and dark. Lists what is broken in plain words (sideways scroll, text too faint, "
                     "tiny buttons, unnamed controls, broken images, console errors), prints the layout, saves "
                     "screenshots and returns them. Run it after every change to a page; it is done at 'No problems found'.",
                     {"url": S, "views": S}, ["url"]),
    "ui_smoke": Tool(ui_smoke, "Use a page you built the way a person would: clicks every button, follows the links, "
                     "submits each form empty and with nonsense, presses keys in a game, tabs through the controls and "
                     "reloads to see if typed data is kept. Lists buttons that do nothing, forms that accept bad input, "
                     "console errors and lost data in plain words. Run it after ui_audit; it is done at 'No problems found'.",
                     {"url": S}, ["url"]),
    "view_output": Tool(view_output, "Look at any visual file you made: a web page, SVG, image, PDF, slides, document, "
                        "workbook or CSV. Returns pictures of the pages, plus the text read from them for models "
                        "that cannot see images. Use pages to pick some, like \"1,3\" or \"2-4\".",
                        {"path": S, "pages": S}, ["path"]),
}


def _fix_type_args():
    # MCP clients send {"ref":..,"text":..}; the function's parameter is text_ to avoid shadowing text()
    t = TOOLS["browser_type"]
    t.fn = lambda ref, text="", submit=False: browser_type(ref, text_=text, submit=submit)


LIVE = None


def _start_live():
    """The control channel the app's Browser panel uses (agent/browser_live.py). Failing to start it costs the live
    view only, never the browser tools."""
    global LIVE
    try:
        from meshpool_harness.agent import browser_live
        LIVE = browser_live.LiveServer(B, os.environ.get("MH_CHAT_ID", "")).start()
    except Exception:  # noqa: BLE001
        LIVE = None


def _close_for_exit(timeout: float = 8):
    """Close the browser on the way out: Chromium writes cookies to disk in batches, so a process that is simply
    killed can lose a sign-in made seconds before."""
    if LIVE is not None:
        LIVE.stop()
    try:
        B.run(B.shutdown, timeout=timeout)
    except BaseException:  # noqa: BLE001
        pass


def _exit_cleanly():
    import signal

    def bye(*_):
        _close_for_exit(5)
        os._exit(0)
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, bye)
            except (ValueError, OSError):
                pass


def _prewarm():
    """Source and terminal installs: fetch Chromium in the background as the server starts, so the first page the agent
    opens does not wait on a download (which would run past the call's time limit on a slow connection)."""
    try:
        from meshpool_harness import bundled
        if bundled.packaged() or BU.mode() != "meshpool" or BU.find_playwright_chromium():
            return
        load_playwright()
        B._install_chromium()
    except Exception:  # noqa: BLE001 - the first real call reports a failure in plain words
        pass


def main():
    _fix_type_args()
    _exit_cleanly()
    _start_live()
    threading.Thread(target=_prewarm, daemon=True).start()
    serve("meshpool-browser", TOOLS, instructions="Use browser_open, then act on the [eN] numbers from the snapshot.")
    _close_for_exit()                       # stdin closed: the agent is done with us


if __name__ == "__main__":
    main()
