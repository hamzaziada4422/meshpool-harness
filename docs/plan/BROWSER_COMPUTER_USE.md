# Browser use and computer use

Owner requirement: MeshPool drives a browser and a computer (screen, mouse, keyboard), on top of files and running code, and all of it works. This page says what was verified, where, what each setting does, what is not possible, and what has to be checked by hand on a real Mac and a real Windows PC before the release is called done.

Code: `app/meshpool_harness/agent/browser_mcp_server.py` (browser tools), `app/meshpool_harness/browser_use.py` (which browser), `app/meshpool_harness/agent/computer_mcp_server.py` (computer tools and the self-check). The agent starts both as MCP servers from `mcp.json` (`browser` on by default, `computer-use` off by default).

## What works, and where it was verified

| Part | Verified how | Where | Not verified |
|---|---|---|---|
| Browser tools: open, snapshot (text and numbered elements), click, double click, type, `submit`, select, check box (click), press keys and shortcuts, scroll up and down, back, tabs (list, switch, close, new tab from a click), screenshot (PNG saved in the project), wait, close and start again | `app/tests/test_browser_e2e.py`: starts the server exactly as the agent does (`agent/mcp.py` `Server`, command `python`) against a local multi-page site (login form that sets a cookie, controls page, late-loading page) in headless Chromium | Linux, Playwright 1.63 with Chromium 1243 (Chrome for Testing 153) | headed windows; macOS and Windows |
| Sign-in persists | same file: sign in, close the server the way the agent does (stdin closed, then terminate) and with SIGTERM, start a new server on the same profile folder, still signed in. A different profile folder is signed out | Linux | macOS, Windows (the agent's close on Windows is `TerminateProcess`; see below) |
| "installed" mode | `test_browser_modes.py`: detection with fake executables for macOS, Windows and Linux paths; a real run through `MH_BROWSER_EXE` with the Chromium here; the profile folder is Meshpool's | Linux | real Chrome, Edge, Brave on macOS and Windows |
| "attach" mode | `test_browser_modes.py`: starts Chromium with `--remote-debugging-port`, connects, drives it, `browser_close` only disconnects (the browser keeps running), nothing listening gives a clear error in under 20 s | Linux | Chrome 136+ on the everyday profile (refuses, see below) |
| Packaged build finds its bundled Chromium | `test_browser_modes.py::test_packaged_build_finds_its_bundled_chromium` (in-process, `bundled.packaged` forced to true) | Linux | the real Mac and Windows installers |
| Computer tools on the macOS and Windows backends | `test_computer_backends.py`: a fake Quartz module that records the events and a fake `ctypes.windll` check every call for screenshot, click (left, right, middle, double), move, drag, scroll (both directions), type text (chunking, accented characters, surrogate pairs), press key (single, combo, several, minus key, unknown key and modifier), open app (name, URL, file, refusals), screen info, coordinate scaling | mocked, on Linux | the real operating systems. Nothing here proves that macOS accepts the events or that the permissions dialogs behave: use the manual check list below |
| Computer use self-check | `computer_check` tool, `GET /api/computer-status`, the "Computer use" row of the doctor (`config.system_check`), `capabilities[computer].computer_status`; unit tests with mocked probes for macOS, Windows, Linux | mocked | the real probes (`CGPreflightScreenCaptureAccess`, `AXIsProcessTrusted`, `OpenInputDesktop`) on real machines |

Local verification used Playwright 1.63 with the Chromium that was pre-installed here (revision 1243); the release builds with Playwright 1.65 (see `docs/plan/BUNDLED_TOOLS.md`). The venv at `~/.venvs/meshpool` was changed to Playwright 1.63.0 to match the browser. `docs/plan/ci_local.sh` installs `playwright` unpinned, which would pull 1.65 again and stop the pre-installed Chromium from launching (the runtime asks for revision 1255).

On Linux, computer use is not supported and says so plainly (`computer_check`, the doctor row, and the error when a computer tool is called).

## Bugs found and fixed

1. A launch failure of any kind (including "the profile folder is in use") triggered a Chromium download. Now the download happens only when Chromium is really missing ("Executable doesn't exist"), and never in the packaged app.
2. When `PLAYWRIGHT_BROWSERS_PATH` was not set (the source-checkout terminal agent; the desktop shell sets it), the download went to `<data folder>/browsers` while the Playwright driver looks in its default cache, so the "download once" fallback would download on every start and still fail. The download now goes where the driver looks. (`meshpool install-browser` already installs into the process's environment, i.e. the default cache, so the two now agree.)
3. In the packaged app `PLAYWRIGHT_BROWSERS_PATH` was set after the driver had started, and the driver reads it once, so outside the desktop shell (which sets it earlier) the bundled Chromium would not be found. Set before the driver starts now; `test_packaged_build_finds_its_bundled_chromium` fails with the old order (checked) and passes now.
4. The browser server was killed while Chromium was still running (stdin closed, then terminate), so Chromium never got a clean close. The server now closes the browser when stdin ends and on SIGTERM, and `agent/mcp.py` gives it up to 6 s to do so before terminating it. A sign-in made seconds before did survive a plain kill in the one try made here (Chromium also flushes on its own signal handler), so this is protection, not a measured fix.
5. macOS: without the Screen Recording permission `screencapture` still succeeds and returns only the wallpaper, so the old return-code check never fired and the agent got a useless picture. `Mac.grab` now asks `CGPreflightScreenCaptureAccess` and, when it says no, adds a warning with the exact System Settings path to the screenshot text. It only warns (the picture is still returned) because the probe's answer inside the app's helper process has not been seen on a real Mac: see the manual list.
6. macOS: `open_app` used `open -a` for everything, so a web address or a file (which the tool advertises) failed. Web addresses and existing files now use plain `open --`; names still use `open -a`; a name that starts with a dash is refused.
7. `press_key` could not press the minus key (`cmd+-` is zoom out): `-` was treated as a separator. Fixed.
8. Windows: screenshots needed the `mss` package, which is not in the app's requirements or in `RUNTIME_EXTRAS` (`pywebview playwright`); unless a build dependency such as Hermes' generated requirements pulls it in, every Windows screenshot would have failed. It falls back to Pillow's `ImageGrab`. (Adding `mss` to `RUNTIME_EXTRAS` would also work; the fallback means the release does not depend on it.)
9. macOS: when the accessibility probe itself failed, control was reported as allowed. The self-check now reports "could not be checked" instead of "ok", while the tools still try (the click reports the refusal).
10. The "allow Accessibility" and "allow Screen Recording" messages pointed to a "Meshpool Settings > Permissions" page that does not exist. They now give the System Settings path.

## Which browser (setting `MH_BROWSER_USE`)

| Value | What it launches | Profile | Falls back? |
|---|---|---|---|
| `meshpool` (default) | packaged app: the Chromium inside the app; source checkout: Chrome or Edge when installed, else a Chromium fetched once | Meshpool's folder `browser-profile/` in the data folder | n/a |
| `installed` | the program of Google Chrome, Microsoft Edge or Brave on this computer (`MH_BROWSER_APP` picks one; empty: the first found; `MH_BROWSER_EXE` overrides the path), launched by Playwright with `executable_path` | Meshpool's folder `browser-profile/installed-<name>/` | never, in any build: when it is not found the error names what was looked for and says to choose Meshpool's browser under Settings > Capabilities > Browser for browser use |
| `attach` | nothing is launched; connects over CDP to the browser listening at `MH_BROWSER_CDP_URL` (default `http://127.0.0.1:9222`) | the browser's own | never; nothing listening gives the command to start it, and the same offer |

Search paths for `installed`: macOS `/Applications` and `~/Applications` (`Google Chrome.app`, `Microsoft Edge.app`, `Brave Browser.app`); Windows `%ProgramFiles%`, `%ProgramFiles(x86)%` and `%LOCALAPPDATA%` under `Google\Chrome\Application\chrome.exe`, `Microsoft\Edge\Application\msedge.exe`, `BraveSoftware\Brave-Browser\Application\brave.exe`; Linux `google-chrome(-stable)`, `microsoft-edge(-stable)`, `brave-browser(-stable)` on `PATH`.

The setting is read when the browser starts (environment first, then the settings file), so a change applies to the next chat that starts the browser. In the packaged app `installed` and `attach` are opt-in only: the default stays Meshpool's bundled Chromium, nothing of the customer's browser is touched unless they choose it, and `installed` on a Mac older than 13 works (it does not need the bundled Chromium).

### Limits (stated in the setting's description)

* `installed` is the user's browser engine with Meshpool's own profile. It does not see the sign-ins of the everyday profile. Chrome does not allow automation of the everyday profile: Chrome 136 and later ignore `--remote-debugging-port` when the profile folder is the default one (Google's change, not re-tested here: no everyday Chrome profile exists on the build machine), and a second browser cannot open a profile that the first one has open. So the user signs in once in the Meshpool window; the sign-in is then kept.
* `attach` needs the user to start the browser themselves with `--remote-debugging-port=9222 --user-data-dir=<a folder>`; the folder must not be the everyday one (Chrome 136+). So it cannot reach the everyday profile either. It is for people who already run a debugging browser, for a window they want to watch and steer, or for a browser on a remote desktop. The debugging port gives full control of the browser to any local program: bind it to `127.0.0.1` only (the default) and never expose it.
* None of the three reads the passwords, history or extensions of the everyday profile.

### Settings API (backend added)

* `GET /api/settings` carries the three fields (`MH_BROWSER_USE`, `MH_BROWSER_CDP_URL`, `MH_BROWSER_APP`) and `POST /api/settings` validates them.
* `GET /api/browser-use` returns `{mode, modes[{id, name, desc, available, detail}], cdp_url, installed[{key, name, path}], default_cdp_url}`; `POST /api/browser-use` with `{mode, cdp_url?, app?}` saves and returns the same.
* `GET /api/capabilities` now adds `browser_use` to the `browser` row and `computer_status` to the `computer` row.
* `GET /api/computer-status` returns the self-check.

UI (applied, small): `app/meshpool_harness/ui/settings2.js`, Settings > Capabilities (section `caps`), a select "Browser for browser use" and, for the attach choice, a "Debugging address" field, written in the same style as the neighbouring "Editing files and running commands" select. The whole change is these three pieces:

```js
// 1. after `const mode = ...`, in the "caps" section
const bu = (feat.features.find(f => f.id === "browser") || {}).browser_use;
// 2. in the template, before the "Advanced" heading
${bu ? `<label class="field"><span>Browser for browser use</span><select id="capBrowser">${bu.modes.map(m => `<option value="${m.id}" ${m.id === bu.mode ? "selected" : ""} ${m.available ? "" : "disabled"}>${esc(m.name)}${m.available ? "" : " (not found)"}</option>`).join("")}</select><small id="capBrowserD"></small></label>
<label class="field" id="capCdpW"><span>Debugging address</span><input id="capCdp" value="${esc(bu.cdp_url)}" placeholder="${esc(bu.default_cdp_url)}"><small>...</small></label>` : ""}
// 3. after the capMode handler
if (bu) {
  const showBu = () => { const m = bu.modes.find(x => x.id === $("#capBrowser").value) || {}; $("#capBrowserD").textContent = `${m.desc || ""} (${m.detail || ""})`; $("#capCdpW").hidden = m.id !== "attach"; };
  $("#capBrowser").onchange = $("#capCdp").onchange = async () => { showBu(); try { await post("/api/browser-use", {mode: $("#capBrowser").value, cdp_url: $("#capCdp").value}); res("capRes", "ok", "Saved. New chats use it."); } catch (e) { res("capRes", "bad", e.message); } };
  showBu();
}
```

It uses only helpers that file already uses (`$`, `esc`, `post`, `res`) and `node --check` accepts the file; it has NOT been seen in a rendered window here, so look at Settings > Capabilities once on a real build.

For Computer use the row can show `f.computer_status.summary` and, when `ready` is false, the `fix` of each item whose `ok` is not true.

## Computer use self-check

`computer_status()` (in `computer_mcp_server.py`) never prompts and never takes a screenshot. Per platform:

* macOS: Screen Recording (`CGPreflightScreenCaptureAccess`), Accessibility (`AXIsProcessTrusted`), the Quartz bridge and Pillow. The fix text is `System Settings > Privacy & Security > Screen Recording` (macOS 15: Screen & System Audio Recording; macOS 12 or older: System Preferences > Security & Privacy > Privacy) and `... > Accessibility`, then quit and reopen Meshpool. A probe that cannot answer is reported as "unknown", never as ok.
* Windows: no permission to ask for. It checks that the desktop that receives input can be opened (`OpenInputDesktop`): a locked screen, the sign-in screen and UAC's secure desktop fail it. It notes that programs running as administrator ignore input from a normal program.
* Linux: "not supported on Linux yet", with what to use instead (browser tools, the shell).

Exposed as the MCP tool `computer_check`, `GET /api/computer-status`, the doctor row "Computer use" (optional; part of `GET /api/settings` as `system`, and `meshpool check`), and `capabilities[computer].computer_status`.

## Proposal for 0.0.8: use the everyday browser profile (extension)

The only supported way to act inside the user's everyday Chrome, Edge or Brave profile (their sign-ins, cookies, extensions, open tabs) is an extension that runs inside that browser. Design:

1. **Meshpool Bridge**, a Manifest V3 extension (Chrome Web Store, Edge Add-ons; Brave installs from the Chrome store). Permissions: `activeTab`, `scripting`, `tabs`, `tabGroups`, `debugger` (for trusted clicks, typing and screenshots through CDP on a tab), `nativeMessaging`; host permissions requested per site at run time (`optional_host_permissions`), never `<all_urls>` up front.
2. **Local socket.** The Meshpool app (or the browser MCP server) listens on `127.0.0.1` on a random port with a random token written to a file in the data folder. The extension cannot read that file, so the installer registers a tiny **native messaging host** (a manifest in the browser's `NativeMessagingHosts` folder; `allowed_origins` is the extension's id) that answers one message with the port and the token. The extension then opens a WebSocket to `ws://127.0.0.1:<port>` with the token; the server checks the token and the `Origin` (`chrome-extension://<id>`), accepts one connection, and drops it when the app quits.
3. **Protocol.** JSON-RPC over that socket with the same tool surface as today (`open`, `snapshot`, `click ref`, `type ref`, `select`, `press`, `scroll`, `back`, `tabs`, `screenshot`, `wait`). The snapshot and the `[eN]` numbering are the same JavaScript (`SNAPSHOT_JS`), injected with `chrome.scripting.executeScript`; trusted input and screenshots go through `chrome.debugger` on that tab; screenshots can also use `chrome.tabs.captureVisibleTab`. `MH_BROWSER_USE=extension` then makes `Browser` talk to the socket instead of Playwright; the tool functions do not change.
4. **Consent and safety.** Meshpool works only in tabs in a tab group named "Meshpool" that it opens itself (or that the user adds), with the usual "Meshpool started debugging this browser" bar visible; the toolbar button shows a badge while it is connected and has a stop switch; per-site permission is asked at first use; no cookie or password export call exists in the protocol; the socket accepts only loopback and one client; navigation to the browser's own pages (`chrome://`, the extension store, password manager) is refused.
5. **Distribution and limits.** Store review takes days to weeks, so submit early; enterprise-managed browsers may block it; Firefox and Safari are out of scope; the debugging bar cannot be hidden in Chrome. Estimated effort: extension and host about 5 days, the Meshpool side and settings 2 days, installer changes on both OSes 2 days, tests (headless Chromium can load the unpacked extension with `--load-extension` on Linux, as in the existing e2e) 2 days.

## Manual check list on a real Mac and a real Windows PC

Nothing below can be done here. Run it on each installer build before the release is announced; tick each line.

### macOS (Apple silicon on macOS 14 or 15; Intel on macOS 11 or 12 if one exists)

Browser
- [ ] Browser use opens a visible Chromium window (macOS 13 and later); on macOS 11 or 12 the tool says it needs macOS 13 and offers search and fetch instead.
- [ ] Sign in to any site in that window, quit Meshpool from the menu, reopen, ask the agent to open the site: still signed in.
- [ ] Settings: switch Which browser to "My installed Chrome, Edge or Brave" (with each installed): the window is that browser, a first run has no everyday sign-ins, Meshpool's profile folder is under the data folder. With none installed the option is disabled or the tool says so; nothing opens.
- [ ] Attach: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir=/tmp/mp-debug`, then choose "A browser I started myself": the agent drives that window; "close" leaves it open. Stop Chrome: the error says what to run.

Computer use (turn the Computer use switch on; start a new chat)
- [ ] `computer_check` before granting anything lists Screen Recording and Accessibility as MISSING with the System Settings path; the doctor row agrees.
- [ ] Grant both (System Settings > Privacy & Security), quit and reopen Meshpool, run `computer_check`: ready.
- [ ] Screenshot of a Retina display: a click at a button in the screenshot lands on that button (check at the far corners as well). Repeat with a non-default scaled resolution and with an external display as the main display.
- [ ] Single click, double click (select a word in Notes), right click (context menu), drag (move a file in Finder), vertical scroll in a long page (down is down), horizontal scroll (right moves the content the expected way), move for a hover menu.
- [ ] Type text: ASCII, accented characters, an emoji, a 200-character sentence; into Notes and into a browser field.
- [ ] Keys: enter, tab, escape, `cmd+c` / `cmd+v`, `cmd+shift+4`, `cmd+-` and `cmd+=` (zoom), arrows, F5; several separated by spaces.
- [ ] `open_app`: `Safari`, `Notes`, `https://example.com`, a file path, a name that does not exist (clean error).
- [ ] Revoke Screen Recording while Meshpool runs: the next screenshot text carries the warning and the path. Also confirm the opposite: with the permission granted there is NO warning (the probe runs in the helper process, not in the app, and was never seen on a real Mac; if it wrongly says no, drop the warning).
- [ ] Stop in the middle of a long action: the mouse is not left held down.
- [ ] With the model text-only: `open_app`, keys and typing still work and the agent does not loop on screenshots.

### Windows (10 and 11; 100%, 125% and 150% scaling; one and two monitors)

Browser
- [ ] Browser use opens the bundled Chromium (no Chrome or Edge needed), a sign-in survives closing and reopening Meshpool (the agent closes the server with TerminateProcess here, so this line matters most on Windows).
- [ ] "My installed" with Edge (always present) and with Chrome and Brave when installed: right window, profile folder under the data folder.
- [ ] Attach to `msedge.exe --remote-debugging-port=9222 --user-data-dir=%TEMP%\mp-debug`.

Computer use
- [ ] `computer_check` is ready on an unlocked desktop; with the screen locked (Win+L, then a scheduled task or a second session asks) it reports the locked desktop; a UAC prompt on screen reports the secure desktop.
- [ ] Screenshot at 125% and 150%: click accuracy at the corners (the process is DPI aware; check that coordinates match). With two monitors: the screenshot is the primary monitor and clicks land on it.
- [ ] Click, double click, right click, drag (a file in Explorer), scroll both directions, hover.
- [ ] Type: ASCII, accents, emoji; keys: `ctrl+c`, `ctrl+v`, `alt+tab`, `alt+f4`, `win+d` (`cmd` is the Windows key), `ctrl+shift+t`, F-keys, `ctrl+-`.
- [ ] `open_app`: `notepad`, `calc`, `Excel` (if installed), a file path, a web address, `x&calc` is refused; a program that needs a console shows one.
- [ ] A program running as administrator ignores the input (documented limit); the message in `computer_check` says so.
- [ ] A screenshot works although `mss` is not installed (the app does not ship it).
