"""Terminal front end for the MeshPool coding agent.

  meshpool                       start a session in the current folder
  meshpool --dir PATH            start in another folder
  meshpool --continue            resume the last session in this folder
  meshpool -p "request"          answer one request and exit (prints only the reply)
  meshpool --auto                allow edits and commands without asking

One scrolling column: a header, the conversation (assistant text, shell calls as filled blocks, other tools as
one-line entries), then a live area at the bottom (a spinner, or the input box between two rules with the footer
hint, the project line and the counters line). Everything printed above the live area is append-only; only the live
area is erased and redrawn. Rendering lives in tui_render.py and keys in tui_input.py. Without a terminal (a pipe)
the same events come out as plain lines; NO_COLOR keeps the layout and drops the colors.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from meshpool_harness import config as C
from meshpool_harness.agent import brand as B
from meshpool_harness.agent import tui_input as K
from meshpool_harness.agent import tui_render as R
from meshpool_harness.agent.tui_render import Style

# (name, argument hint, description)
COMMANDS = [
    ("help", "", "show the commands"),
    ("model", "[id]", "pick the model"),
    ("login", "", "sign in: MeshPool key, another provider, or ChatGPT"),
    ("clear", "", "start a new conversation in this folder"),
    ("compact", "", "shorten old tool output to free context"),
    ("grade", "", "have an independent reviewer check the last piece of work"),
    ("status", "", "model, folder, access mode, usage"),
    ("auto", "[ask|edits|auto]", "set what the agent may do without asking"),
    ("mcp", "", "MCP servers and their tools"),
    ("connect", "[app]", "connect Notion, Gmail, Slack... or an MCP server"),
    ("tokens", "", "tokens and cost of this session"),
    ("context", "", "what fills the context window"),
    ("resume", "[id]", "open an earlier session"),
    ("export", "[file]", "save this conversation as Markdown"),
    ("copy", "[n]", "copy the last reply (or the nth from the end)"),
    ("effort", "[auto|low|medium|high]", "how hard the model thinks"),
    ("plan", "[on|off]", "plan mode: read and plan, change nothing"),
    ("permissions", "", "saved allow and deny rules, and folder trust"),
    ("diff", "", "uncommitted changes in this folder (git diff)"),
    ("init", "", "have the agent write MESHPOOL.md (project notes)"),
    ("import", "", "bring in old chats, Claude Code and Codex sessions"),
    ("setup", "", "first-run walkthrough: key, folder, skills"),
    ("exit", "", "quit"),
]
ALIASES = {"quit": "exit", "q": "exit", "mode": "auto", "cost": "tokens"}
MODES = ["ask", "edits", "auto"]
PLACEHOLDERS = ["fix the failing test in tests/", "explain how this project is organised",
                "add a --verbose flag to the CLI", "find where errors are logged", "write tests for <filepath>"]

HELP_TIPS = ["End a line with \\ (or press Alt+Enter) for a new line. Esc stops the agent while it works.",
             "Shift+Tab cycles access: ask, accept edits, full access.",
             "Ctrl+O prints the last collapsed output in full. Ctrl+C stops a step; twice at the prompt quits."]


def _enable_ansi() -> int:
    return B.depth(sys.stdout)


def _git_branch(cwd: str) -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True,
                           timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


class Terminal:
    """Draws the session. `emit` takes the agent's events; `approve` and `ask_questions` take its questions.
    out/err/width/depth/interactive/clock/keys_factory can be set for tests and screenshots."""

    def __init__(self, print_mode: bool = False, out=None, err=None, width: int | None = None,
                 depth: int | None = None, interactive: bool | None = None, clock=None, keys_factory=None):
        self.print_mode = print_mode
        self.out = out or sys.stdout
        self.info = err or (sys.stderr if print_mode else self.out)
        tty = bool(getattr(self.out, "isatty", lambda: False)()) and os.environ.get("TERM") != "dumb"
        self.interactive = (tty and not print_mode) if interactive is None else interactive
        if depth is None:
            depth = B.depth(self.out) if tty else 0
            if tty and os.environ.get("NO_COLOR"):
                depth = 0
        self.st = Style(depth)
        self._width = width
        self.clock = clock or time.monotonic
        self.keys_factory = keys_factory
        self.lock = threading.RLock()
        self.agent = None
        self.failed = False
        self.stopping = False
        self.idle = threading.Event()
        self.idle.set()
        self.keys_free = threading.Event()      # set while the Esc watcher does not hold the keyboard
        self.keys_free.set()
        self.on_esc = None                      # what Esc does while the agent works (stop the turn)
        self.typeahead: list = []               # keys typed while the agent worked, for the next prompt
        # screen state
        self._live_fn = None
        self._rows = 0
        self._cur_row = 0
        self._partial = ""
        self._md = R.MdState()
        self._text_open = False
        self._frame = 0
        self._tools: dict = {}
        self._label = ""
        self._busy_t0: float | None = None
        self._ticker: threading.Thread | None = None
        self._turn = False
        self.last_block: dict | None = None
        self.output_limit = int(os.environ.get("MH_TUI_OUTPUT_LINES", "10"))
        # session facts shown on screen
        self.version = ""
        self.cwd = os.getcwd()
        self.cwd_label = ""                     # shown instead of the real folder (screenshots)
        self.branch: str | None = None          # None: ask git
        self._branch_t = 0.0
        self._branch_v = ""
        self.model = ""
        self.provider = ""
        self.effort = os.environ.get("MH_EFFORT") or os.environ.get("MH_REASONING_EFFORT") or ""
        self.mode = "ask"
        self.modes = MODES
        self.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        self.ctx_tokens = 0
        self.windows: dict = {}
        self.cost: float | None = None
        self.agents = 0
        self.status_fn = None                   # () -> [(kind, text)] shown right-aligned above the box
        self.notices: list[str] = []
        self.started = False                    # the conversation has begun (changes the rule color)
        self.history: list[str] = []
        self.on_mode = None                     # called with the new mode after Shift+Tab

    # ---------- basics ----------
    @property
    def width(self) -> int:
        if self._width:
            return self._width
        return max(20, shutil.get_terminal_size((100, 24)).columns)

    def _write(self, s: str, to=None):
        (to or self.out).write(s)
        (to or self.out).flush()

    def p(self, s="", end="\n", to=None):
        """Print scrollback text (the old API; sub-agent progress and status lines use it)."""
        if to is not None and to is not self.out:
            self._write(s + end, to)
        elif self.interactive:
            self.scroll((s + end).rstrip("\n").split("\n"))
        else:
            self._write(s + end, to or self.info)

    # ---------- the live area ----------
    def _clear_seq(self) -> str:
        if not self._rows:
            return ""
        up = f"\x1b[{self._cur_row}A" if self._cur_row else ""
        self._rows = self._cur_row = 0
        return up + "\r\x1b[J"

    def _paint_seq(self, redraw: bool = False) -> str:
        """Escape sequence that draws the live area at the cursor (or, with redraw, over the old one)."""
        if not self.interactive or not self._live_fn:
            return ""
        up_prev = f"\x1b[{self._cur_row}A" if (redraw and self._cur_row) else ""
        lines, (row, col) = self._live_fn()
        lines = lines or [""]
        body = "\n".join(ln + ("\x1b[K" if R.vlen(ln) < self.width else "") for ln in lines) + "\x1b[J"
        back = len(lines) - 1 - row
        body += (f"\x1b[{back}A" if back > 0 else "") + "\r" + (f"\x1b[{col}C" if col else "")
        self._rows, self._cur_row = len(lines), row
        return up_prev + ("\r" if redraw else "") + body

    def refresh(self):
        """Redraw the live area in place: lines are overwritten, then what is left below is cleared (no flicker)."""
        if not self.interactive:
            return
        with self.lock:
            self._write(self._paint_seq(redraw=True) if self._live_fn else self._clear_seq())

    def set_live(self, fn):
        with self.lock:
            self._live_fn = fn
            if self.interactive:
                self._write(self._clear_seq() + self._paint_seq())

    def scroll(self, lines: list[str]):
        """Append lines to the scrollback above the live area."""
        if not lines:
            return
        with self.lock:
            if not self.interactive:
                self._write("\n".join(lines if self.st.on else [R.strip(x) for x in lines]) + "\n", self.info)
                return
            self._write(self._clear_seq() + "\n".join(lines) + "\n" + self._paint_seq())

    # ---------- spinner ----------
    def _turn_live(self):
        lines = []
        if self._partial:
            tail = self._partial.replace("\t", "    ")
            w = self.width - 4
            lines.append("  " + (tail if R.vlen(tail) <= w else "…" + tail[-(w - 1):]))
        secs = int(self.clock() - self._busy_t0) if self._busy_t0 is not None else 0
        lines.append(" " + R.spinner_line(self.st, self._frame, self._label or "Working", secs))
        lines = [R.clip(x, self.width - 1) for x in lines]
        return lines, (len(lines) - 1, 0)

    def turn_begin(self):
        self._turn = True
        self.stopping = False
        self._busy_t0 = self.clock()
        self._label = "Thinking"
        if self.interactive:
            self.set_live(self._turn_live)
            if self._ticker is None or not self._ticker.is_alive():
                self._ticker = threading.Thread(target=self._tick, daemon=True)
                self._ticker.start()
            if self.on_esc and self.keys_factory is None and K.raw_available() and \
                    (getattr(self, "_watcher", None) is None or not self._watcher.is_alive()):
                self._watcher = threading.Thread(target=self._watch_esc, daemon=True)
                self._watcher.start()

    def _watch_esc(self):
        """While the agent works, Esc stops the turn (Ctrl+C still does too). The keyboard is let go whenever a
        question or approval needs it (idle cleared), and taken back after."""
        while self._turn:
            if not self.idle.is_set():
                time.sleep(0.05)
                continue
            self.keys_free.clear()
            try:
                if os.name == "nt":
                    import msvcrt
                    while self._turn and self.idle.is_set():
                        if msvcrt.kbhit():
                            ch = msvcrt.getwch()
                            if ch == "\x1b":
                                self._esc_pressed()
                                return
                            self.typeahead.extend(K.parse_keys(ch))
                        else:
                            time.sleep(0.05)
                else:
                    with K.RawKeys() as keys:
                        while self._turn and self.idle.is_set():
                            k = keys.read(timeout=0.05)
                            if k == "esc":
                                self._esc_pressed()
                                return
                            if k is not None:             # typed ahead: kept for the next prompt
                                self.typeahead.append(k)
                        self.typeahead.extend(keys.pending)    # keys read together with the last one
                        keys.pending = []
            except Exception:  # noqa: BLE001 - no usable terminal: Ctrl+C still stops
                return
            finally:
                self.keys_free.set()

    def _esc_pressed(self):
        self.stopping = True
        self._label = "Stopping"
        try:
            self.on_esc()
        except Exception:  # noqa: BLE001
            pass

    def _tick(self):
        while self._turn:
            time.sleep(0.15)
            self._frame += 1
            if self._turn and self._live_fn == self._turn_live:
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001
                    pass

    def turn_finish(self):
        self._turn = False
        self._flush_text()
        self._tools.clear()
        if self.interactive:
            self.set_live(None)
            self.scroll([""])

    class _Spin:                                 # the old Spinner API: start(label) / stop()
        def __init__(self, t):
            self.t = t

        def start(self, label="Thinking"):
            if self.t.interactive and not self.t._turn:
                self.t.turn_begin()
            self.t._label = label

        def stop(self):
            if self.t._turn:
                self.t.turn_finish()

    @property
    def spin(self):
        return Terminal._Spin(self)

    # ---------- streaming text ----------
    def _text(self, chunk: str):
        if not self.interactive:
            if not self._text_open and not self.print_mode:
                self._write("\n", self.out)
            self._text_open = True
            self._write(chunk, self.out)
            return
        self._text_open = True
        self._partial += chunk
        done = []
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            done += R.markdown(line, self.width - 2, self.st, self._md)
        if done:
            self.scroll(["  " + x if x else "" for x in done])
        elif self._live_fn:
            self.refresh()

    def _flush_text(self):
        if self.interactive:
            if self._partial:
                rest, self._partial = self._partial, ""
                self.scroll(["  " + x if x else "" for x in R.markdown(rest, self.width - 2, self.st, self._md)])
        elif self._text_open:
            self._write("\n", self.out)
        self._text_open = False
        self._md = R.MdState()

    # ---------- agent events ----------
    def emit(self, ev: dict):
        t, st = ev.get("type"), self.st
        if t == "model_start":
            if self.interactive and not self._turn:
                self.turn_begin()
            self._label = "Thinking"
            self._thought = ""
        elif t == "text_delta":
            if not self._text_open and self.interactive:
                self.scroll([""])
            self._text(ev["text"])
        elif t == "thought":                             # the model's reasoning: one dim line while it thinks
            self._thought = (getattr(self, "_thought", "") + str(ev.get("text") or ""))[-4000:]
            last = [x for x in self._thought.splitlines() if x.strip()]
            self._label = "Thinking: " + R.clip(last[-1].strip(), max(20, self.width - 30)) if last else "Thinking"
        elif t == "stream_reset":
            if self._text_open:
                self._partial = ""
                self._flush_text()
                self.p(st.dim("[connection dropped; the reply restarts below]"))
        elif t == "assistant":
            if not ev.get("streamed") and ev.get("text"):
                if self.interactive:
                    self.scroll([""])
                self._text(ev["text"] + "\n")
            self._flush_text()
        elif t == "tool_start":
            self._flush_text()
            self._tools[ev.get("id") or ev["name"]] = {"name": ev["name"], "summary": ev.get("summary", ""),
                                                      "t0": self.clock(), "diff": ev.get("diff", ""),
                                                      "mcp": ev.get("mcp"), "timeout": ev.get("timeout_s")}
            self._label = self._tool_label(ev["name"], ev.get("summary", ""), ev.get("mcp"))
        elif t == "tool_end":
            self._tool_end(ev)
        elif t == "subagent":
            tag = f"  ↳ [{ev.get('index', 0) + 1}/{ev.get('count', 1)}] {str(ev.get('title') or '')[:48]}"
            if ev.get("kind") == "start":
                self.agents += 1
                self.p(st.dim(f"{tag}: started"))
            elif ev.get("kind") == "tool":
                self.p(st.dim(f"{tag}: {ev.get('tool', '')} {str(ev.get('text') or '')[:70]}"))
            elif ev.get("kind") == "done":
                self.agents = max(0, self.agents - 1)
                self.p(st.dim(f"{tag}: {ev.get('text') or ev.get('status', '')}"))
        elif t == "status":
            self.p(st.dim("  " + ev["text"]))
        elif t == "plan":
            self._plan(ev.get("entries") or [])
        elif t == "usage":
            self._usage(ev)
        elif t == "receipt":
            self._flush_text()
            lines = [ln for ln in str(ev.get("text", "")).splitlines() if ln.strip()]
            self.scroll([""] + ["  " + st.dim(R.clip(ln, self.width - 4)) for ln in lines])
        elif t == "review":
            self._flush_text()
            self.scroll([""] + self._review_lines(ev))
        elif t in ("error", "interrupted"):
            self.failed = True
            self._flush_text()
            paint = st.red if t == "error" else st.amber
            self.scroll([""] + ["  " + paint(x) for x in R.wrap_inline(ev["text"], self.width - 4, Style(0))])
        elif t == "turn_end":
            self.turn_finish()

    @staticmethod
    def _tool_label(name, summary, mcp):
        first = summary.splitlines()[0][:50] if summary else ""
        return ("Running " + (first or name)) if name in R.SHELL_TOOLS else f"{R.tool_label(name, bool(mcp))} {first}".strip()

    def _usage(self, ev: dict):
        for k in ("in", "cached", "out", "calls"):
            self.usage[k] += int(ev.get(k, 0) or 0)
        if not ev.get("aux"):
            self.ctx_tokens = int(ev.get("in", 0) or 0)
        if isinstance(ev.get("cost"), (int, float)):     # the provider's own figure for this call
            self.measured = getattr(self, "measured", 0.0) + float(ev["cost"])
        self.cost = getattr(self, "measured", None) if getattr(self, "measured", None) is not None else self._price()

    def _price(self) -> float | None:
        """Session cost from MH_PRICE_IN / MH_PRICE_OUT / MH_PRICE_CACHED (USD per million tokens). MeshPool has no
        price list, so without those variables no dollar figure is shown."""
        try:
            pi, po = float(os.environ["MH_PRICE_IN"]), float(os.environ["MH_PRICE_OUT"])
            pc = float(os.environ.get("MH_PRICE_CACHED") or pi)
        except (KeyError, ValueError):
            return None
        u = self.usage
        return ((u["in"] - u["cached"]) * pi + u["cached"] * pc + u["out"] * po) / 1e6

    def _review_lines(self, ev) -> list:
        """The independent check as a compact block: a headline, then what is open (or every requirement when it fits)."""
        st, w = self.st, self.width - 6
        paint = {"pass": st.green, "partial": st.amber, "fail": st.red}.get(ev.get("verdict"), st.dim)
        lines = ["  " + paint(R.clip(str(ev.get("text", "")).splitlines()[0] if ev.get("text") else "Independent check", w + 4))]
        if ev.get("rework"):
            lines.append("  " + st.dim("Sending these back to the agent to fix."))
        items = ev.get("items") or []
        shown = [i for i in items if i.get("status") != "met"] or (items if len(items) <= 6 else [])
        for i in shown[:8]:
            tag = {"met": st.green("met      "), "not_met": st.red("not met  ")}.get(i.get("status"), st.amber("unchecked"))
            lines.append("    " + tag + " " + R.clip(str(i.get("requirement", "")), w - 12))
        if ev.get("acceptance") and ev.get("verdict") != "pass":
            lines.append("  " + st.dim(R.clip({"accept_with_notes": "The reviewer would accept it with notes.",
                                              "would_send_back": "The reviewer would send it back.",
                                              "accept": "The reviewer would accept it."}.get(ev["acceptance"], ""), w + 4)))
        return lines

    def _plan(self, entries):
        st = self.st
        mark = {"completed": st.green("✔"), "in_progress": st.accent("◼")}
        rows = [""]
        for e in entries:
            txt = e.get("content", "")
            rows.append("  " + mark.get(e.get("status"), st.dim("◻")) + " " +
                        (st.dim(txt) if e.get("status") == "completed" else txt))
        self.scroll([R.clip(r, self.width) for r in rows])

    def _tool_end(self, ev: dict):
        st = self.st
        info = self._tools.pop(ev.get("id") or ev["name"], None) or {"name": ev["name"], "summary": "", "t0": None,
                                                                    "diff": "", "mcp": None, "timeout": None}
        name = ev["name"]
        took = (self.clock() - info["t0"]) if info["t0"] is not None else None
        if not self._tools:
            self._label = "Thinking"
        if name in ("todo_write", "ask_user"):
            return
        self._flush_text()
        out = (ev.get("output") or "").rstrip()
        if ev.get("denied"):
            self.scroll([R.clip("  " + st.amber("✗ ") + R.tool_label(name, bool(info["mcp"])) + " " +
                                st.dim("denied"), self.width)])
            return
        ok = bool(ev.get("ok"))
        if name in R.SHELL_TOOLS:
            meta = f"(timeout {info['timeout']}s)" if info.get("timeout") else ""
            lines, hidden = R.shell_block(info["summary"], out, took, st, self.width, meta=meta,
                                          limit=self.output_limit, ok=ok)
            self.last_block = {"command": info["summary"], "output": out, "took": took, "meta": meta, "ok": ok,
                               "hidden": hidden}
            self.scroll(lines)
        else:
            self.scroll(["  " + x for x in R.tool_line(name, info["summary"], out, ok, took, st, self.width - 4,
                                                       info["diff"], bool(info["mcp"]))])

    def expand_last(self):
        """Ctrl+O: print the last shell output in full (the screen above is append-only, so it is printed again)."""
        b = self.last_block
        if not b:
            return self.scroll(["  " + self.st.dim("Nothing to expand.")])
        lines, _ = R.shell_block(b["command"], b["output"], b["took"], self.st, self.width, meta=b["meta"], limit=0,
                                 ok=b["ok"])
        self.scroll(lines)

    # ---------- facts for the screen ----------
    def refresh_facts(self, agent):
        from meshpool_harness import __version__
        from meshpool_harness import providers as PV
        self.version = __version__
        model = agent.current_model() if hasattr(agent, "current_model") else ""
        prov, bare = PV.split(model)
        self.model = bare or model
        try:
            label = PV.name(prov)
        except Exception:  # noqa: BLE001
            label = prov
        self.provider = "ChatGPT subscription" if prov == "chatgpt" else (
            "MeshPool API" if label.lower() == "meshpool" else f"{label} API")
        self.mode = getattr(agent, "mode", self.mode) or "ask"
        self.modes = ["ask", "auto"] if hasattr(agent, "manifest") else MODES
        self.cwd = self.cwd_label or str(getattr(agent, "root", self.cwd))
        u = getattr(agent, "usage", None)
        if isinstance(u, dict) and not self.usage["calls"] and u.get("calls"):
            self.usage = {k: int(u.get(k, 0)) for k in ("in", "cached", "out", "calls")}

    def _branch(self) -> str:
        if self.branch is not None:
            return self.branch
        if self.clock() - self._branch_t > 5 or not self._branch_v:
            self._branch_v, self._branch_t = _git_branch(self.cwd), self.clock()
        return self._branch_v

    def status_items(self) -> list:
        if self.status_fn:
            return self.status_fn()
        items = []
        try:
            from meshpool_harness import providers as PV
            if not (PV.key() or os.environ.get("MH_PROVIDER") == "chatgpt" or PV.ready("chatgpt")) \
                    and not hasattr(self.agent, "manifest"):
                items += [("err", "Not logged in"), ("err", "Run /login")]
        except Exception:  # noqa: BLE001
            pass
        try:
            from meshpool_harness import autoupdate as AU
            s = AU._ST.get("state")
            if s == "ready":
                items += [("ok", "✔ Update installed"), ("ok", "Restart to update")]
            elif s == "available":
                items += [("warn", " ".join(f"Update {AU._ST.get('latest') or ''} available".split()))]
        except Exception:  # noqa: BLE001
            pass
        return items

    # ---------- screens ----------
    def startup(self):
        rows = [""] + R.header(self.st, self.width, self.version, self.model, self.provider, self.cwd) + [""]
        rows += R.notice_lines(self.st, self.width, self.notices) + [""]
        self.scroll(rows)

    def project_line(self) -> str:
        b = self._branch()
        return " " + self.st.dim(_tilde(self.cwd) + (f" ({b})" if b else ""))

    def counters_line(self) -> str:
        window = R.context_window(self.model, self.windows)
        return " " + R.counters(self.st, self.width - 2, self.usage, self.ctx_tokens, window, self.mode, self.model,
                                self.effort, self.cost)

    def footer_line(self) -> str:
        return R.mode_hint(self.st, self.mode, self.agents)

    def rule_line(self) -> str:
        return R.rule(self.st, self.width, R.POOL if self.started else None)

    # ---------- input ----------
    def _keys(self):
        return self.keys_factory() if self.keys_factory else K.RawKeys()

    def raw_ok(self) -> bool:
        return self.keys_factory is not None or (self.interactive and K.raw_available())

    @staticmethod
    def menu_rows(buf: str) -> list:
        """The slash commands that match what is typed: names starting with it, else names containing it."""
        if not buf.startswith("/") or " " in buf or "\n" in buf:
            return []
        q = buf[1:].lower()
        return ([c for c in COMMANDS if c[0].startswith(q)] or [c for c in COMMANDS if q in c[0]])[:8]

    def _box(self, s: dict):
        """The live area while waiting for input: right-aligned status line, rule, the input rows, rule, then the
        command list (when typing `/`) or the footer hint, project line and counters line."""
        st, w = self.st, self.width
        lines = []
        stat = R.status_right(st, w, self.status_items())
        if stat:
            lines.append(stat)
        lines.append(self.rule_line())
        buf = s["buf"]
        before = buf[:s["cur"]]
        crow, ctext = before.count("\n"), before.split("\n")[-1]
        first_row = len(lines)
        ccol = 2
        for i, row in enumerate(buf.split("\n")):
            lead = st.accent("❯") + " " if i == 0 else "  "
            avail = w - 3
            if i == crow:
                idx = max(0, R.vlen(ctext) - (avail - 1))
                row = row[idx:] if idx else row
                ccol = 2 + R.vlen(ctext[idx:])
            if not buf and i == 0:
                lines.append(lead + st.dim(f'Try "{s["hint"]}"'))
            else:
                lines.append(lead + R.clip(row, avail, ""))
        lines.append(self.rule_line())
        menu = self.menu_rows(buf)
        if menu:
            lines += R.slash_menu(st, w, menu, min(s["sel"], len(menu) - 1))
        else:
            lines.append(R.clip(self.footer_line(), w - 1) if not s.get("note") else " " + st.dim(s["note"]))
            lines.append(R.clip(self.project_line(), w - 1))
            lines.append(R.clip(self.counters_line(), w - 1))
        return lines, (first_row + crow, ccol)

    def cycle_mode(self):
        i = self.modes.index(self.mode) if self.mode in self.modes else -1
        self.mode = self.modes[(i + 1) % len(self.modes)]
        if self.on_mode:
            self.on_mode(self.mode)

    def echo_user(self, text: str):
        rows = [seg for r in text.split("\n") for seg in R.hard_wrap(r, max(10, self.width - 4))]
        self.scroll([""] + [f" {self.st.accent('❯') if i == 0 else ' '} {r}" for i, r in enumerate(rows)])

    def read_input(self) -> str | None:
        """One message or /command from the input box. None: the user is leaving (Ctrl+D, or Ctrl+C twice)."""
        self.keys_free.wait(0.5)                # the Esc watcher of the last turn lets go first
        if not self.raw_ok():
            return self._read_plain()
        s = {"buf": "", "cur": 0, "sel": 0, "note": "", "hint": PLACEHOLDERS[len(self.history) % len(PLACEHOLDERS)]}
        h = len(self.history)
        draft = ""
        last_c = 0.0
        self.set_live(lambda: self._box(s))
        result: str | None = None
        try:
            with self._keys() as keys:
                ahead, self.typeahead = getattr(self, "typeahead", []), []
                if ahead and isinstance(getattr(keys, "pending", None), list):
                    keys.pending[:0] = ahead              # what was typed while the agent worked
                while True:
                    try:
                        k = keys.read()
                    except KeyboardInterrupt:
                        k = "ctrl_c"
                    except EOFError:
                        break
                    if k is None:
                        continue
                    buf, cur = s["buf"], s["cur"]
                    menu = self.menu_rows(buf)
                    s["note"] = ""
                    if isinstance(k, tuple):
                        s["buf"], s["cur"] = buf[:cur] + k[1] + buf[cur:], cur + len(k[1])
                    elif k == "enter":
                        if menu:
                            name, hint, _ = menu[min(s["sel"], len(menu) - 1)]
                            if buf != "/" + name and hint:
                                s["buf"] = "/" + name + " "
                                s["cur"] = len(s["buf"])
                                self.refresh()
                                continue
                            buf = "/" + name
                        if buf.endswith("\\"):
                            s["buf"], s["cur"] = buf[:-1] + "\n", len(buf)
                        elif buf.strip():
                            result = buf.strip()
                            break
                    elif k == "alt_enter":
                        s["buf"], s["cur"] = buf[:cur] + "\n" + buf[cur:], cur + 1
                    elif len(k) == 1:
                        s["buf"], s["cur"], s["sel"] = buf[:cur] + k + buf[cur:], cur + 1, 0
                    elif k == "backspace":
                        if cur:
                            s["buf"], s["cur"], s["sel"] = buf[:cur - 1] + buf[cur:], cur - 1, 0
                    elif k == "delete":
                        s["buf"] = buf[:cur] + buf[cur + 1:]
                    elif k == "left":
                        s["cur"] = max(0, cur - 1)
                    elif k == "right":
                        s["cur"] = min(len(buf), cur + 1)
                    elif k in ("home", "ctrl_a"):
                        s["cur"] = buf.rfind("\n", 0, cur) + 1
                    elif k in ("end", "ctrl_e"):
                        j = buf.find("\n", cur)
                        s["cur"] = len(buf) if j < 0 else j
                    elif k == "ctrl_k":
                        j = buf.find("\n", cur)
                        s["buf"] = buf[:cur] + (buf[j:] if j >= 0 else "")
                    elif k == "ctrl_u":
                        s["buf"], s["cur"] = buf[cur:], 0
                    elif k == "ctrl_w":
                        j = len(buf[:cur].rstrip(" "))
                        j = buf.rfind(" ", 0, j) + 1
                        s["buf"], s["cur"] = buf[:j] + buf[cur:], j
                    elif k in ("up", "ctrl_p"):
                        recalled = h < len(self.history) and buf == self.history[h]   # walking history, not the menu
                        if menu and not recalled:
                            s["sel"] = (s["sel"] - 1) % len(menu)
                        elif "\n" in buf[:cur]:
                            s["cur"] = self._vmove(buf, cur, -1)
                        elif self.history and h > 0:
                            if h == len(self.history):
                                draft = buf
                            h -= 1
                            s["buf"] = self.history[h]
                            s["cur"] = len(s["buf"])
                    elif k in ("down", "ctrl_n"):
                        recalled = h < len(self.history) and buf == self.history[h]
                        if menu and not recalled:
                            s["sel"] = (s["sel"] + 1) % len(menu)
                        elif "\n" in buf[cur:]:
                            s["cur"] = self._vmove(buf, cur, 1)
                        elif h < len(self.history):
                            h += 1
                            s["buf"] = self.history[h] if h < len(self.history) else draft
                            s["cur"] = len(s["buf"])
                    elif k == "tab":
                        if menu:
                            s["buf"] = "/" + menu[min(s["sel"], len(menu) - 1)][0] + " "
                            s["cur"] = len(s["buf"])
                    elif k == "shift_tab":
                        self.cycle_mode()
                    elif k == "esc":
                        s["buf"], s["cur"] = "", 0
                    elif k == "ctrl_o":
                        self.expand_last()
                    elif k == "ctrl_l":
                        self._write("\x1b[2J\x1b[H")
                        self._rows = self._cur_row = 0
                        self.refresh()
                    elif k == "ctrl_d":
                        if not buf:
                            break
                    elif k == "ctrl_c":
                        if buf:
                            s["buf"], s["cur"] = "", 0
                        elif self.clock() - last_c < 2:
                            break
                        else:
                            last_c = self.clock()
                            s["note"] = "Press Ctrl+C again to exit"
                    s["cur"] = min(s["cur"], len(s["buf"]))
                    self.refresh()
        finally:
            self.set_live(None)
        if result is not None:
            self.history.append(result)
            if self.keys_factory is None and self.interactive:   # a real terminal, not a scripted test
                from meshpool_harness.agent import tui_more as TM
                TM.add_history(self.cwd, result)
            self.started = True
            self.echo_user(result)
        return result

    @staticmethod
    def _vmove(buf: str, cur: int, d: int) -> int:
        start = buf.rfind("\n", 0, cur) + 1
        col = cur - start
        if d < 0:
            pstart = buf.rfind("\n", 0, max(0, start - 1)) + 1
            return min(pstart + col, start - 1)
        nl = buf.find("\n", cur)
        nend = buf.find("\n", nl + 1)
        nend = len(buf) if nend < 0 else nend
        return min(nl + 1 + col, nend)

    def _read_plain(self) -> str | None:
        lines = []
        while True:
            try:
                line = input("❯ " if sys.stdin.isatty() and not lines else "")
            except EOFError:
                return "\n".join(lines).strip() if lines else None
            if line.endswith("\\"):
                lines.append(line[:-1])
                continue
            lines.append(line)
            return "\n".join(lines).strip()

    # ---------- questions ----------
    def choose(self, title: str, detail: list[str], options: list[str], sel: int = 0, hotkeys: dict | None = None,
               hint: str = "↑↓ to move · enter to select · esc to cancel") -> int | None:
        """A compact inline choice. Returns the index picked, or None for Esc / Ctrl+C. Without raw keys it asks for a
        number with input()."""
        if not self.raw_ok():
            self.p("")
            self.p(self.st.amber(title))
            for d in detail:
                self.p("  " + d)
            for i, o in enumerate(options, 1):
                self.p(f"  {self.st.bold(str(i))} {o}")
            try:
                a = input(f"  Choose 1-{len(options)} (Enter: {sel + 1}) > ").strip()
            except EOFError:
                return None
            return sel if not a else (int(a) - 1 if a.isdigit() and 1 <= int(a) <= len(options) else None)
        hotkeys = hotkeys or {}
        state = {"sel": sel}
        win = 10

        def live():
            s0 = max(0, min(state["sel"] - win // 2, len(options) - win)) if len(options) > win else 0
            shown = options[s0:s0 + win]
            lines = R.choice_lines(self.st, self.width, title, detail, shown, state["sel"] - s0, hint)
            if len(options) > win:                       # numbers stay those of the full list
                head = 1 + len(detail)
                for i in range(len(shown)):
                    lines[head + i] = lines[head + i].replace(f"{i + 1}. ", f"{s0 + i + 1}. ", 1)
            return lines, (len(lines) - 1, 0)
        prev = self._live_fn
        self.set_live(live)
        try:
            with self._keys() as keys:
                while True:
                    try:
                        k = keys.read()
                    except KeyboardInterrupt:
                        return None
                    except EOFError:
                        return None
                    if k in ("up", "ctrl_p"):
                        state["sel"] = (state["sel"] - 1) % len(options)
                    elif k in ("down", "ctrl_n", "tab"):
                        state["sel"] = (state["sel"] + 1) % len(options)
                    elif k == "enter":
                        return state["sel"]
                    elif k in ("esc", "ctrl_c"):
                        return None
                    elif isinstance(k, str) and len(k) == 1:
                        if k in hotkeys:
                            return hotkeys[k]
                        if k.isdigit() and 1 <= int(k) <= min(9, len(options)):
                            return int(k) - 1
                    self.refresh()
        finally:
            self.set_live(prev)

    def approve(self, kind: str, title: str, detail: str, options=None):
        if not (sys.stdin.isatty() or self.keys_factory):
            return "no", "No terminal to ask in. Run with --auto."
        if self.stopping:
            return "no", ""
        self.idle.clear()
        self.keys_free.wait(1.0)                # the Esc watcher lets go of the keyboard first
        try:
            choice, note = self._approve_raw(kind, title, detail) if self.raw_ok() else self._approve(kind, title, detail)
        finally:
            self.idle.set()
        if self.stopping:                 # Ctrl+C while the question was open: the turn is over
            return "no", ""
        if choice == "always" and options:
            # an ACP harness (options from its permission request): "always" is its own allow-always option
            return next((o["id"] for o in options if o.get("kind") == "allow_always"), "yes"), note
        return choice, note

    def _approve_raw(self, kind: str, title: str, detail: str):
        st = self.st
        rows = detail.splitlines()

        def style(ln):
            if kind == "edit" and ln.startswith("+") and not ln.startswith("+++"):
                return st.green(ln)
            if kind == "edit" and ln.startswith("-") and not ln.startswith("---"):
                return st.red(ln)
            return st.bold(ln) if kind == "bash" else st.dim(ln)
        if len(rows) > 14:
            # a yes must never cover lines the user did not see: the whole text goes to the scrollback, above the menu
            self.p("")
            for ln in rows[:200]:
                self.p("  " + style(ln))
            if len(rows) > 200:
                self.p("  " + st.dim(f"… {len(rows) - 200} more lines (not shown; answer No and ask for smaller steps)"))
            shown = [st.dim(f"The full text ({len(rows)} lines) is above.")]
        else:
            shown = [style(ln) for ln in rows]
        what = {"edit": "file edits", "bash": "commands", "mcp": "this MCP tool"}.get(kind, "this")
        opts = ["Yes", f"Yes, and don't ask again for {what} in this session"]
        if kind in ("bash", "edit", "mcp"):
            opts.append("Yes, and always allow this in this folder")
        i = self.choose(title, shown, opts + ["No"], hotkeys={"y": 0, "a": 1, "n": len(opts), **({"f": 2} if len(opts) > 2 else {})},
                        hint=f"↑↓ or 1-{len(opts) + 1} · enter to confirm · esc: no")
        return {0: "yes", 1: "always", 2: "save" if len(opts) > 2 else "no"}.get(i, "no"), ""

    def _approve(self, kind: str, title: str, detail: str):
        """The same question with plain input(): no raw keys (a pipe, a test)."""
        st = self.st
        self.p("")
        self.p(st.amber(title))
        for line in detail.splitlines()[:80]:
            if kind == "edit" and line.startswith("+") and not line.startswith("+++"):
                self.p("  " + st.green(line))
            elif kind == "edit" and line.startswith("-") and not line.startswith("---"):
                self.p("  " + st.red(line))
            else:
                self.p("  " + (line if kind == "bash" else st.dim(line)))
        what = {"edit": "file edits", "bash": "commands", "mcp": "this MCP tool"}.get(kind, "this")
        while True:
            try:
                a = input(B.prompt(f"  {st.bold('y')} yes   {st.bold('a')} yes, and allow {what} for this session   "
                                   f"{st.bold('n')} no (Enter: no) > ")).strip().lower()
            except EOFError:
                return "no", ""
            if self.stopping:
                return "no", ""
            if a == "":                   # Enter alone never approves a change
                return "no", ""
            if a in ("y", "yes"):
                return "yes", ""
            if a in ("a", "always"):
                return "always", ""
            if a in ("n", "no"):
                try:
                    note = input("  What should MeshPool do instead? (Enter to skip) > ").strip()
                except EOFError:
                    note = ""
                return "no", note

    def ask_questions(self, qid: str, questions: list):
        """ask_user in the terminal: arrow-key choices (numbers in a pipe), Enter takes the recommended one, any other
        text is the user's own answer. No terminal: None (the agent goes on with its recommendations)."""
        st = self.st
        if not (sys.stdin.isatty() or self.keys_factory):
            return None
        self.idle.clear()
        self.keys_free.wait(1.0)
        try:
            return self._ask_questions(questions)
        finally:
            self.idle.set()

    def _ask_questions(self, questions: list):
        st = self.st
        answers = []
        for q in questions:
            opts = q["options"]
            rec = [str(i) for i, o in enumerate(opts, 1) if o.get("recommended")]
            if self.raw_ok() and not q.get("multi"):
                labels = [o["label"] + (" (recommended)" if o.get("recommended") else "") +
                          (f" · {o['description']}" if o.get("description") else "") for o in opts] + \
                    ["Type my own answer"]
                i = self.choose(f"{q['header']}: {q['question']}", [], labels, sel=int(rec[0]) - 1 if rec else 0)
                if i is None:
                    return None
                if i == len(opts):
                    try:
                        own = input("  Your answer > ").strip()
                    except EOFError:
                        return None
                    answers.append({"selected": [], "other": own})
                else:
                    answers.append({"selected": [opts[i]["label"]], "other": ""})
                continue
            self.p("")
            self.p(st.amber(f"{q['header']}: {q['question']}") + (st.dim("  (several: 1,3)") if q.get("multi") else ""))
            for i, o in enumerate(opts, 1):
                self.p(f"  {st.bold(str(i))} {o['label']}" + (st.green("  (recommended)") if o.get("recommended") else "")
                       + (st.dim("  " + o["description"]) if o.get("description") else ""))
            try:
                a = input(f"  Pick{' (Enter: ' + ','.join(rec) + ')' if rec else ''}, or type your own answer > ").strip()
            except EOFError:
                return None
            nums = [int(x) for x in re.split(r"[ ,]+", a or ",".join(rec)) if x.isdigit()]
            if a and not nums or any(not 1 <= n <= len(opts) for n in nums):
                answers.append({"selected": [], "other": a})
            else:
                answers.append({"selected": [opts[n - 1]["label"] for n in nums], "other": ""})
        return answers


def ask_key(force: bool = False) -> bool:
    """Ask for the API key in the terminal, check it, and save it to meshpool.config (meshpool login)."""
    C.ensure_home() if os.environ.get("MESHPOOL_HOME") else None
    C.load_into_env()
    from meshpool_harness import providers as _PV
    if not force and (os.environ.get("MESHPOOL_API_KEY") or os.environ.get("OPENAI_API_KEY") or _PV.key()):
        return True
    if not sys.stdin.isatty():
        print(f"No API key. Run: meshpool login   (or add MESHPOOL_API_KEY=... to {C.config_path()})", file=sys.stderr)
        return False
    from meshpool_harness.agent import onboard
    return onboard.key_step(Style(_enable_ansi()), header=True)


def logo_lines(color: bool) -> list[str]:
    """The MeshPool mark, for terminals."""
    return R.logo(B.depth() if color else 0)


def _context() -> str:
    """The user's instructions and memory (shared with the app), sent out of sight with a session's first message.
    The same block the app sends (assistant.context_block), so the two never drift apart."""
    try:
        from meshpool_harness import assistant as AS
        return AS.context_block() or ""
    except Exception:  # noqa: BLE001
        return ""


def _stop_turn(term: Terminal, agent, wait: float = 4.0):
    """Ctrl+C during a turn: stop the agent itself (an ACP harness keeps running otherwise, and its permission
    questions would race the prompt for input), let an open question finish, and wait briefly for the turn to end."""
    term.stopping = True
    term.spin.stop()
    try:
        agent.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        if not term.idle.is_set():        # a question is waiting for a line in another thread: let it take one
            term.p(term.st.dim("  Press Enter to dismiss the open question."))
            term.idle.wait()
        # an ACP harness answers its open session/prompt request once it has cancelled
        cv, pending, rid = (getattr(agent, a, None) for a in ("_cv", "_pending", "_id"))
        if cv is not None and isinstance(pending, dict) and rid:
            end = time.time() + wait
            with cv:
                while rid not in pending and getattr(agent, "_alive", False) and time.time() < end:
                    cv.wait(0.2)
                answered = pending.pop(rid, None) is not None
            if not answered and getattr(agent, "_alive", False) and hasattr(agent, "_hard_restart"):
                agent._hard_restart()     # it ignored the cancel: end it; the next message restarts it
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        pass


def _mcp_status(term: Terminal, mcp):
    st = term.st
    if not mcp:
        term.p("MCP is off.")
        return
    from meshpool_harness.agent.mcp import config_file
    term.p(f"Config: {config_file()}")
    if mcp.config_error:
        term.p(st.red(mcp.config_error))
    for s in mcp.status():
        mark = {"connected": st.green("connected"), "error": st.red("failed"),
                "disabled": st.dim("disabled")}.get(s["status"], s["status"])
        term.p(f"  {st.bold(s['name'])}  {mark}  {st.dim(s['transport'])} {st.dim(s['server'])}")
        if s["error"]:
            term.p("    " + st.red(s["error"]))
        if s["tools"]:
            term.p("    " + st.dim(", ".join(s["tools"])))


# ---------- the session: commands and the turn loop --------------------------------------------------------------

class Session:
    """One interactive session: the input loop, the slash commands and sending a message to the agent."""

    def __init__(self, term: Terminal, agent, mcp=None, sub: str | None = None):
        self.term, self.agent, self.mcp, self.sub = term, agent, mcp, sub
        self.ctx = True                                    # your instructions and memory go with the first message
        term.agent = agent
        term.on_mode = self.set_mode
        term.on_esc = agent.stop if hasattr(agent, "stop") else None
        term.refresh_facts(agent)

    # -- sending
    def send(self, text: str):
        term, agent, st = self.term, self.agent, self.term.st
        if self.ctx:
            self.ctx = False
            block = _context()
            if block:
                text += "\n\n\u200b" + block
        term.stopping = term.failed = False
        term.started = True
        term.turn_begin()
        try:
            return agent.send(text)
        except KeyboardInterrupt:
            term.turn_finish()
            term.p(st.amber("\n  Stopped."))
            term.failed = True
            _stop_turn(term, agent)
        finally:
            term.turn_finish()
            term.refresh_facts(agent)

    def set_mode(self, mode: str):
        self.agent.mode = mode
        self.term.mode = mode
        if hasattr(self.agent, "apply_mode"):             # Codex & co. have their own modes: switch them too
            try:
                self.agent.apply_mode()
            except Exception as e:  # noqa: BLE001
                self.term.p(self.term.st.red(f"  Couldn't switch the harness's own mode: {e}"))

    # -- loop
    def loop(self, first: str | None = None):
        term = self.term
        if first:
            term.echo_user(first)
            self.send(first)
        while True:
            text = term.read_input()
            if text is None:
                break
            if not text:
                continue
            if text.startswith("/"):
                if self.command(text) == "exit":
                    break
                continue
            self.send(text)

    # -- commands
    def command(self, text: str):
        term, st = self.term, self.term.st
        cmd, _, arg = text[1:].partition(" ")
        cmd = ALIASES.get(cmd.lower(), cmd.lower())
        arg = arg.strip()
        names = [c[0] for c in COMMANDS]
        if cmd not in names:
            from meshpool_harness.agent import extras as EX
            if cmd in EX.commands(self.agent.root):          # a command file in .meshpool/commands
                return self.send(text)
            near = [n for n in names if n.startswith(cmd)] if cmd else []
            if len(near) == 1:
                cmd = near[0]
            else:
                return term.p(st.amber(f"  Unknown command /{cmd}. Type /help."))
        fn = getattr(self, "cmd_" + cmd)
        try:
            return fn(arg)
        except KeyboardInterrupt:
            term.p(st.amber("\n  Cancelled."))
        except Exception as e:  # noqa: BLE001  (a command must never end the session)
            term.p(st.red(f"  /{cmd} failed: {e}"))

    def cmd_exit(self, arg):
        return "exit"

    def cmd_help(self, arg):
        t, st = self.term, self.term.st
        labels = [f"/{n}" + (f" {a}" if a else "") for n, a, _ in COMMANDS]
        w = max(len(x) for x in labels) + 3
        rows = [""] + ["  " + st.cmd(lab) + " " * (w - len(lab)) + st.dim(d) for lab, (_, _, d) in zip(labels, COMMANDS)]
        t.scroll(rows + [""] + ["  " + st.dim(x) for x in HELP_TIPS])

    def cmd_clear(self, arg):
        t = self.term
        self.agent.reset()
        self.ctx = True
        t.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        t.measured, t.cost = None, None
        t.ctx_tokens, t.started, t.last_block = 0, False, None
        if t.interactive:
            t._write(self.term._clear_seq() + "\x1b[2J\x1b[H")
        t.refresh_facts(self.agent)
        t.startup()

    def cmd_auto(self, arg):
        t, st = self.term, self.term.st
        if arg and arg not in t.modes:
            return t.p(st.amber(f"  Modes: {', '.join(t.modes)}"))
        mode = arg or ("ask" if t.mode == "auto" else "auto")
        self.set_mode(mode)
        t.p(st.dim("  " + {"ask": "Edits and commands are asked first.",
                            "edits": "File edits are allowed; commands are still asked.",
                            "auto": "Edits and commands are allowed without asking."}[mode]))

    def cmd_tokens(self, arg):
        u = self.agent.usage
        pct = round(100 * u["cached"] / u["in"]) if u["in"] else 0
        cost = u.get("cost") if isinstance(u.get("cost"), (int, float)) else self.term.cost
        self.term.p(f"  {u['calls']} model calls, {u['in']:,} input tokens ({pct}% from cache), "
                    f"{u['out']:,} output tokens" + (f", ${cost:.4f}." if cost is not None else
                                                     ". Cost not measured: the endpoint sends no price; set "
                                                     "MH_PRICE_IN and MH_PRICE_OUT (USD per million tokens) to see one."))

    def cmd_context(self, arg):
        from meshpool_harness.agent import tui_more as TM
        t, st = self.term, self.term.st
        if not hasattr(self.agent, "messages"):
            return t.p(st.dim("  /context is not available for this agent."))
        rows = TM.context_breakdown(self.agent)
        total = sum(n for _, n in rows)
        window = R.context_window(t.model, t.windows)
        w = max(len(k) for k, _ in rows) + 2
        bar = lambda n: "█" * max(0, round(30 * n / window)) if window else ""
        lines = [""] + [f"  {R.pad(k, w)}{n:>9,}  {st.dim(bar(n))}" for k, n in rows if n]
        lines += ["", f"  {R.pad('About', w)}{total:>9,} of {window:,} tokens ({round(100 * total / window) if window else 0}%)",
                  st.dim("  Estimates, at four characters a token. /compact makes room.")]
        t.scroll(lines)

    def cmd_resume(self, arg):
        from meshpool_harness.agent import tui_more as TM
        from meshpool_harness.agent.core import Agent
        t, st = self.term, self.term.st
        if self.sub or not isinstance(self.agent, Agent):
            return t.p(st.dim("  /resume is not available for this agent."))
        rows = [r for r in TM.sessions(None, 40) if r["id"] != self.agent.id]
        if arg:
            rows = [r for r in rows if r["id"].startswith(arg) or arg.lower() in r["title"].lower()]
        if not rows:
            return t.p(st.dim("  No earlier sessions."))
        here = os.path.realpath(str(self.agent.root))
        rows.sort(key=lambda r: (os.path.realpath(r["workdir"]) != here, -r["updated"]))    # this folder first
        if len(rows) == 1 and arg:
            pick = 0
        else:
            labels = [f"{R.clip(r['title'] or '(untitled)', 44)}  {st.dim(TM.ago(r['updated']) + ' · ' + _tilde(r['workdir']))}"
                      for r in rows]
            pick = t.choose("Resume which session?", [], labels)
        if pick is None:
            return
        r = rows[pick]
        a = self.agent
        new = Agent.resume(r["workdir"], r["id"], mcp=self.mcp, emit=a.emit, approve=a.approve, mode=a.mode,
                           profile=getattr(a, "profile", "code"))
        if new is None:
            return t.p(st.red("  That session could not be opened."))
        new.ask = getattr(a, "ask", None)
        self.agent = t.agent = new
        t.on_esc = new.stop
        self.ctx = False
        t.refresh_facts(new)
        t.p(st.dim(f"  Resumed \"{r['title'] or r['id']}\" ({r['messages']} messages) in {_tilde(r['workdir'])}."))

    def cmd_export(self, arg):
        from meshpool_harness.agent import tui_more as TM
        t, st = self.term, self.term.st
        if not hasattr(self.agent, "messages"):
            return t.p(st.dim("  /export is not available for this agent."))
        name = arg or f"meshpool-session-{time.strftime('%Y%m%d-%H%M')}.md"
        path = Path(name).expanduser()
        if not path.is_absolute():
            path = Path(str(getattr(self.agent, "root", os.getcwd()))) / path
        if path.exists() and not arg:
            return t.p(st.amber(f"  {path} exists. Give a file name: /export notes.md"))
        path.write_text(TM.transcript(self.agent), encoding="utf-8")
        t.p(st.dim(f"  Saved the conversation to {_tilde(str(path))}"))

    def cmd_copy(self, arg):
        from meshpool_harness.agent import tui_more as TM
        t, st = self.term, self.term.st
        reps = TM.replies(self.agent)
        n = int(arg) if arg.isdigit() and int(arg) > 0 else 1
        if len(reps) < n:
            return t.p(st.dim("  No reply to copy yet." if not reps else f"  There are only {len(reps)} replies."))
        how = TM.copy_to_clipboard(reps[-n], out=t.out)
        t.p(st.dim("  Copied." if how else "  Could not reach the clipboard."))

    def cmd_effort(self, arg):
        from meshpool_harness.agent.core import EFFORTS, effort_choice
        t, st = self.term, self.term.st
        if not hasattr(self.agent, "effort"):
            return t.p(st.dim("  This agent chooses its own effort."))
        if not arg:
            return t.p(f"  Effort: {self.agent.effort}. Choose one of: {', '.join(EFFORTS)}.")
        if arg.lower() not in EFFORTS:
            return t.p(st.amber(f"  Effort is one of: {', '.join(EFFORTS)}."))
        self.agent.effort = t.effort = effort_choice(arg)
        t.p(st.dim("  " + ({"auto": "Meshpool sizes the effort to each request."}.get(self.agent.effort)
                           or f"The model is asked for {self.agent.effort} reasoning effort. Models without effort levels ignore it.")))

    def cmd_plan(self, arg):
        t, st = self.term, self.term.st
        if not hasattr(self.agent, "plan"):
            return t.p(st.dim("  /plan is not available for this agent."))
        on = {"on": True, "off": False}.get(arg.lower(), not self.agent.plan) if arg else not self.agent.plan
        self.agent.plan = on
        t.p(st.dim("  Plan mode is on: Meshpool reads and plans, and changes nothing until you say go." if on
                   else "  Plan mode is off."))

    def cmd_permissions(self, arg):
        from meshpool_harness import trust as TR
        from meshpool_harness.agent import core as A
        t, st = self.term, self.term.st
        root = str(getattr(self.agent, "root", os.getcwd()))
        rules = A._rules(root)
        rows = [""]
        for k in ("allow", "deny"):
            rows.append(f"  {st.bold(k.capitalize())}: " + (", ".join(rules[k]) if rules[k] else st.dim("none")))
        rows.append(f"  {st.bold('This session')}: " + (", ".join(sorted(getattr(self.agent, 'always', set()) or []))
                                                        or st.dim("nothing allowed for the rest of the session")))
        risky = TR.risky_files(root)
        if risky:
            rows.append(f"  {st.bold('Folder')}: " + ("trusted, so its " + " and ".join(risky) + " apply" if TR.is_trusted(root)
                                                     else "not trusted: its " + " and ".join(risky) + " are ignored (meshpool trust)"))
        rows += ["", st.dim(f"  Rules live in {_tilde(str(C.home() / 'permissions.json'))} and .meshpool/permissions.json "
                            "in the project.")]
        t.scroll(rows)

    def cmd_diff(self, arg):
        t, st = self.term, self.term.st
        root = str(getattr(self.agent, "root", os.getcwd()))
        try:
            stat = subprocess.run(["git", "-C", root, "diff", "--stat", "HEAD"], capture_output=True, text=True, timeout=20)
            body = subprocess.run(["git", "-C", root, "diff", "HEAD"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:
            return t.p(st.red(f"  git diff failed: {e}"))
        if stat.returncode:
            return t.p(st.dim("  Not a git repository, or it has no commits yet."))
        if not stat.stdout.strip():
            return t.p(st.dim("  No uncommitted changes."))
        lines = body.stdout.splitlines()
        paint = lambda ln: st.green(ln) if ln.startswith("+") and not ln.startswith("+++") else \
            st.red(ln) if ln.startswith("-") and not ln.startswith("---") else st.dim(ln) if ln.startswith("@@") else ln
        shown = [paint(R.clip(ln, t.width - 2)) for ln in lines[:400]]
        t.scroll([""] + ["  " + x for x in stat.stdout.rstrip().splitlines()] + [""] + shown +
                 ([st.dim(f"  ... {len(lines) - 400} more lines (git diff HEAD)")] if len(lines) > 400 else []))

    def cmd_mcp(self, arg):
        _mcp_status(self.term, self.mcp)

    def cmd_init(self, arg):
        self.send("Look at this project (layout, languages, how to install, build, run and test it, and its "
                  "conventions). Then write MESHPOOL.md in the project root with short notes a new engineer would "
                  "need: the exact commands, the main folders, and the rules to follow. Keep it under 60 lines.")

    def cmd_import(self, arg):
        from meshpool_harness.agent import onboard
        onboard.context_step(self.term.st)
        self.ctx = True                                   # the next message carries the new context

    def cmd_setup(self, arg):
        from meshpool_harness.agent import onboard
        if onboard.provider_step(self.term.st, 1):
            self.term.refresh_facts(self.agent)
            self.term.p(self.term.st.dim("  Saved. Restart meshpool to use it (type /exit, then meshpool)."))

    def cmd_connect(self, arg):
        from meshpool_harness.agent import connect_cli
        t, st = self.term, self.term.st
        if not arg:
            return connect_cli.listing(st)
        ok, cid = connect_cli.connect(arg, st=st)
        if ok and self.mcp:                               # its tools work in this session at once
            from meshpool_harness import mcp_apps as CN
            spec = CN.servers().get(cid)
            if spec:
                self.mcp.add(cid, spec)
        elif ok:
            t.p(st.dim("  New sessions use it."))

    def cmd_grade(self, arg):
        t, st = self.term, self.term.st
        if self.sub or not hasattr(self.agent, "grade_now"):
            return t.p(st.dim("  /grade is not available for this agent."))
        t.p(st.dim("  A separate reviewer is checking the last piece of work..."))
        try:
            self.agent.grade_now()
        except KeyboardInterrupt:
            t.p(st.amber("  Stopped."))

    def cmd_compact(self, arg):
        t, st = self.term, self.term.st
        if self.sub or not hasattr(self.agent, "messages"):
            return t.p(st.dim("  /compact is not available for this agent."))
        from meshpool_harness.agent import core as A
        before = A._context_size(self.agent.messages)
        done = self.agent.compact_now() if hasattr(self.agent, "compact_now") else None
        A._shrink_context(self.agent.messages, max(1, int(before * 0.4)), hard=True)
        after = A._context_size(self.agent.messages)
        if before:
            t.ctx_tokens = int(t.ctx_tokens * after / before)
        t.p(st.dim(f"  {'Summarized earlier steps and shortened' if done else 'Shortened'} old tool output: "
                   f"{before // 1000}k to {after // 1000}k characters of context."))

    def cmd_status(self, arg):
        t, st, a = self.term, self.term.st, self.agent
        from meshpool_harness import providers as PV
        t.refresh_facts(a)
        key = PV.key()
        mode = {"ask": "ask before changes", "edits": "accept edits", "auto": "full access"}.get(t.mode, t.mode)
        window = R.context_window(t.model, t.windows)
        rows = [("Version", t.version), ("Model", f"{t.model} · {t.provider}"),
                ("Endpoint", PV.host(PV.key_base()) or "-"),
                ("Key", f"set (ends {key[-4:]})" if len(key) >= 8 else ("set" if key else "not set (run /login)")),
                ("Folder", t.cwd), ("Access", mode),
                ("Session", f"{getattr(a, 'title', '') or 'new'} ({max(0, len(getattr(a, 'messages', [])) - 1)} messages)"),
                ("Context", f"{t.ctx_tokens:,} of {window:,} tokens"),
                ("Usage", f"{t.usage['calls']} calls, {t.usage['in']:,} in, {t.usage['cached']:,} cached, "
                          f"{t.usage['out']:,} out")]
        if self.mcp:
            ok = [s for s in self.mcp.status() if s["status"] == "connected"]
            rows.append(("MCP", f"{len(ok)} server{'s' if len(ok) != 1 else ''} connected"))
        t.scroll([""] + [f"  {st.dim(R.pad(k, 9))}{v}" for k, v in rows])

    def _model_options(self) -> tuple[list[str], str]:
        """(model ids to pick from, note). Asks the endpoint for its list (with the saved key); ChatGPT models are
        listed with their chatgpt: prefix when that sign-in exists."""
        from meshpool_harness import provider_setup as PS
        from meshpool_harness import providers as PV
        ids, note = [], ""
        if PV.key() or not PV.ready("chatgpt"):
            self.term.p(self.term.st.dim("  Loading models..."))
            r = PS.test(PV.key_base(), PV.key())
            ids = [m["id"] for m in r.get("models") or []]
            for m in r.get("models") or []:
                if m.get("context"):
                    self.term.windows[m["id"]] = m["context"]
            note = "" if ids else (r.get("error") or "")
        try:
            if PV.ready("chatgpt"):
                ids += ["chatgpt:" + m for m in PV.list_models("chatgpt")[0]]
        except Exception:  # noqa: BLE001
            pass
        return ids, note

    def set_model(self, mid: str):
        if self.sub:
            self.agent.set_model(mid)
        else:
            self.agent.model = mid
        self.term.refresh_facts(self.agent)
        self.term.p(self.term.st.dim(f"  Model: {self.term.model} on {self.term.provider}"))

    def cmd_model(self, arg):
        t, st = self.term, self.term.st
        if arg:
            return self.set_model(arg)
        if self.sub:
            return t.p(st.dim(f"  Model: {self.agent.current_model()}. Use /model <id> to change it."))
        ids, note = self._model_options()
        cur = self.agent.current_model()
        if not ids:
            return t.p(st.amber(f"  Could not list models. {note}") + st.dim(f" Current: {cur}. /model <id> sets one."))
        i = self.term.choose("Select model", [], [m + ("  (current)" if m == cur or m == t.model else "") for m in ids],
                             sel=next((n for n, m in enumerate(ids) if m == cur or m == t.model), 0))
        if i is not None:
            self.set_model(ids[i])

    def cmd_login(self, arg):
        import getpass

        from meshpool_harness import provider_setup as PS
        from meshpool_harness import providers as PV
        t, st = self.term, self.term.st
        kinds = ["MeshPool API key", "Another provider (OpenAI-compatible URL and key)", "ChatGPT subscription"]
        i = t.choose("Sign in", [], kinds)
        if i is None:
            return
        if i == 2:
            from meshpool_harness.agent import onboard
            ok = onboard.sub_step(st, "chatgpt")
            t.refresh_facts(self.agent)
            return t.p(st.green("  Signed in.") if ok else st.dim("  Not signed in."))
        base, kind, ver, dep = "https://llm.meshpool.ai/v1", "", "", ""
        if i == 1:
            base = input(f"  Endpoint URL [{PV.key_base()}] > ").strip() or PV.key_base()
            try:
                pp = PS.parse(base)
            except PS.SetupError as e:
                return t.p(st.red(f"  {e}"))
            base, kind, ver, dep = pp.base, pp.kind, pp.api_version, pp.deployment
            t.p(st.dim(f"  Recognised: {PS.EP.caps(kind)['label']} at {base}"))
            if PS.EP.is_azure(kind) or not PS.EP.caps(kind)["lists"]:
                what = "Deployment name (as named in Azure)" if PS.EP.is_azure(kind) else "Model name"
                dep = input(f"  {what}{' [' + dep + ']' if dep else ''} > ").strip() or dep
                if kind in ("azure-classic", "azure-foundry"):
                    d = ver or PS.EP.caps(kind)["version"]
                    ver = input(f"  API version [{d}] > ").strip() or d
        key = getpass.getpass("  API key (hidden) > ")
        t.p(st.dim("  Testing the endpoint..."))
        res = PS.test(base, key, model=dep, api_version=ver, kind=kind) if kind else PS.test(base, key)
        if not res["ok"]:
            return t.p(st.red("  " + (res["error"] or "The endpoint did not answer.")))
        t.p(st.green(f"  Connected to {PV.host(res['base_url'])}") +
            st.dim(f" ({res['wire']} wire, {len(res['models'])} models, {res['latency_ms']} ms)"))
        ids = [m["id"] for m in res["models"]]
        if ids:
            j = t.choose("Default model", [], ids, sel=max(0, ids.index(os.environ.get("MH_MODEL", "")) if os.environ.get("MH_MODEL") in ids else 0))
            if j is None:
                return
            model = ids[j]
            for m in res["models"]:
                if m.get("context"):
                    t.windows[m["id"]] = m["context"]
        else:
            model = input("  Model name > ").strip()
        try:
            PS.save(res["base_url"], key, res["wire"], model, allow_custom=not ids, api_version=ver, kind=kind)
        except PS.SetupError as e:
            return t.p(st.red(f"  {e}"))
        if not self.sub:
            self.agent.model = None                       # the saved default now applies
        t.refresh_facts(self.agent)
        t.p(st.green("  Saved.") + st.dim(f" Using {t.model} on {t.provider}."))


def _notices(agent, mcp) -> list[str]:
    out = ["Describe a task, or type /help for the commands. Switch models with /model."]
    if len(getattr(agent, "messages", [])) > 1:
        out.insert(0, f"Resumed: {getattr(agent, 'title', '') or 'last session'} ({len(agent.messages) - 1} messages).")
    if mcp:
        bad = [s["name"] for s in mcp.status() if s["status"] == "error"]
        if bad:
            out.append(f"MCP server{'s' if len(bad) > 1 else ''} {', '.join(bad)} failed to start. See /mcp.")
        elif any(s["status"] == "connected" for s in mcp.status()):
            out.append("MCP servers are connected. See /mcp for their tools.")
    out.append("Shift+Tab cycles access: ask, accept edits, full access.")
    return out


def _trust_step(term: "Terminal", wd: str, interactive: bool) -> None:
    """A folder with its own hooks or allow rules is asked about once; until it is trusted they are ignored."""
    from meshpool_harness import trust as TR
    files = TR.needs_prompt(wd)
    if not files:
        return
    st = term.st
    if not interactive:
        print(f"meshpool: {TR.message(wd)} Run `meshpool trust` in this folder, or set MH_TRUST_PROJECT=1 for this run.",
              file=sys.stderr)
        return
    pick = term.choose("Do you trust the files in this folder?",
                       [_tilde(str(Path(wd).resolve())), f"It has {' and '.join(files)}, which can run commands on this",
                        "computer or skip approval questions. Only trust folders you made or know."],
                       ["Yes, trust this folder", "No, ignore its hooks and rules for now"], sel=1)
    if pick == 0:
        TR.trust(wd)
        term.p(st.dim("  Trusted. Its hooks and rules are on. Undo with: meshpool trust --revoke"))
    else:
        term.p(st.dim("  Its hooks and allow rules are off. Trust it later with: meshpool trust"))


def main(workdir: str | None = None, prompt: str | None = None, auto: bool = False, resume: bool = False,
         session: str | None = None, no_mcp: bool = False, first: str | None = None, onboarded: bool = False,
         json_out: bool = False) -> int:
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.agent.mcp import Manager
    C.load_into_env()
    term = Terminal(print_mode=prompt is not None)
    if json_out:
        term.out = sys.stderr                    # headless JSON: only the final object reaches stdout
    st = term.st
    wd = workdir or os.getcwd()
    from meshpool_harness import harnesses as HR
    if not HR.ready():
        if prompt is None and sys.stdin.isatty():
            from meshpool_harness.agent import onboard
            folder = onboard.run(wd, st)          # first run: key, folder, setup, then a short tour
            if folder is None:
                return 1
            wd = str(folder)
        elif not ask_key():
            term.p(st.red(f"No API key yet. Run meshpool login, or add MESHPOOL_API_KEY=... to {C.config_path()}"))
            return 1
    _trust_step(term, wd, interactive=prompt is None and sys.stdin.isatty())
    # The terminal runs MeshPool Code, or the official Codex when it is set as the default.
    hid = HR.default_for("code")
    try:
        h = HR.get(hid)
    except (KeyError, ValueError) as e:
        term.p(st.red(str(e)))
        return 1
    if not h.get("ready", True):
        term.p(st.red(f"{h['name']} cannot run yet. {h.get('setup') or ''} Run: meshpool setup"))
        return 1
    sub = h["id"] if h.get("type") == "acp" else None
    mcp = None
    if not no_mcp and not sub:                   # an ACP agent gets the MCP servers through ACP itself
        if prompt is None:
            term.spin.start("Starting MCP servers")
        try:
            from meshpool_harness.harnesses.acp import office_extra
            extra = office_extra(wd)                 # the same office tools the app's agents have
        except Exception:  # noqa: BLE001
            extra = None
        mcp = Manager(roots=[wd], extra=extra).start(timeout=float(os.environ.get("MH_MCP_START_S", "60")))
        term.spin.stop()
    pm = os.environ.get("MH_PERMISSION_MODE", "")
    mode = "auto" if auto else (pm if pm in ("ask", "edits") else None)
    kw = dict(mcp=mcp, emit=term.emit, approve=term.approve, mode=mode,
              profile="work" if os.environ.get("MH_AGENT_PROFILE") == "work" else "code")   # --profile work: Cowork
    if sub:
        from meshpool_harness.harnesses.acp import ACPAgent
        if resume or session:
            term.p(st.amber(f"Resuming is not available with {h['name']} in the terminal yet. Starting a new session."))
        try:
            agent = ACPAgent(h, wd, emit=term.emit, approve=term.approve, mode=mode)
        except (ValueError, KeyError) as e:
            term.p(st.red(str(e)))
            return 1
    else:
        agent = (Agent.resume(wd, session, **kw) if (resume or session) else None)
        if (resume or session) and agent is None:
            term.p(st.amber("No earlier session for this folder. Starting a new one."))
        try:
            agent = agent or Agent(wd, **kw)
        except ValueError as e:
            term.p(st.red(str(e)))
            return 1
    term.agent = agent
    agent.headless = prompt is not None and not sub     # one request, no one to answer questions
    if pm == "plan" and hasattr(agent, "plan"):
        agent.plan = True                        # read only: investigate, then reply with a plan
    agent.ask = term.ask_questions               # ask_user questions are answered in the terminal
    sess = Session(term, agent, mcp, sub)
    if prompt is None:
        from meshpool_harness.agent import tui_more as TM
        term.history = TM.load_history(wd)       # Up arrow reaches prompts from earlier sessions in this folder
    try:
        if prompt is not None:
            if json_out:                          # headless: progress goes to stderr, one JSON object to stdout
                import contextlib
                import json as _json
                if json_out == "stream-json":     # one event per line while it works, then the final object
                    _emit = agent.emit

                    def _stream(ev, _emit=_emit):
                        _emit(ev)
                        if isinstance(ev, dict) and ev.get("type") not in ("token", "delta", "thinking"):
                            sys.__stdout__.write(_json.dumps(ev, ensure_ascii=False, default=str) + "\n")
                            sys.__stdout__.flush()
                    agent.emit = _stream
                with contextlib.redirect_stdout(sys.stderr):
                    sess.send(prompt)
                reply = next((m.get("content") for m in reversed(agent.messages) if m.get("role") == "assistant"
                              and isinstance(m.get("content"), str) and m.get("content").strip()), "")
                out = {"type": "result", "ok": not term.failed, "result": reply, "session_id": agent.id,
                       "usage": dict(agent.usage), "steps": len([m for m in agent.messages if m.get("role") == "tool"])}
                rv = getattr(agent, "last_review", None)
                if rv:                                # the independent check of a big task (agent/grader.py)
                    out["review"] = {k: v for k, v in rv.items() if k not in ("type", "turn")}
                sys.__stdout__.write(_json.dumps(out, ensure_ascii=False, default=str) + "\n")
                sys.__stdout__.flush()            # a piped stdout is block buffered: the caller must see the result now
                return 1 if term.failed else 0
            sess.send(prompt)
            return 1 if term.failed else 0
        term.notices = _notices(agent, mcp)
        term.startup()
        sess.loop(first)
    finally:
        term.spin.stop()
        if mcp:
            mcp.close()
        if sub:
            agent.close()
    if prompt is None and not sub:
        term.p(st.dim("  Session saved. Resume it with: meshpool --continue"))
    return 0

