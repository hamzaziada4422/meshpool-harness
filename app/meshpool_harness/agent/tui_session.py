"""The terminal as a front end of the app's chat engine (ui/agent_api.Session, reached through meshpool_harness.chats).

The terminal draws; the Session thinks. A chat started here is the chat the app shows (same transcript, checkpoints,
checks, receipts, memory and routing), and every slash command here calls the Session method the app's button calls.
Nothing in this file decides what a chat does.

  TermFrontend    attaches to a Session: draws its events, answers its approvals, waits for a turn to end
  SharedCommands  the slash commands that map one-to-one onto Session methods (mixed into tui.Session)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from types import SimpleNamespace

# What the model is told about a refused action: only that it was refused, and why. The flags that would allow it are
# for the user (denial_note), and depend on what was refused and the mode the run already has (0.0.19: the note told
# the model to "re-run with --permission-mode edits" in a run that had it, and the model told the user the same).
DENIED_NOTE = ("Not run: this needs a person's yes, and in -p mode nobody can answer. Do not try it another way; "
               "say in your reply what was not done.")
# Full access (--auto) still asks before a command that deletes recursively, rewrites git history or uses sudo
# (core._destructive): "re-run with --auto" would be wrong advice for those, to the model and to the user alike.
DESTRUCTIVE_NOTE = ("Not run: this command deletes or rewrites things (rm -r/-f, git reset --hard, sudo...), which "
                    "always needs a person's yes, even in full access, and nobody can answer in -p mode. Do not retry "
                    "it: get the result another way (a new folder or file name instead of deleting one, for example).")
DESTRUCTIVE_HINT = ("commands that delete recursively, rewrite git history or use sudo always need a yes, even with "
                    "--auto: allow a specific one with --allowed-tools \"bash:rm -rf build*\", or run it yourself.")


def _denied(ev: dict) -> dict:
    """What a -p run reports about a refused action: a command by its text, not the bare "Run this command?"."""
    if ev.get("title") == "Run this destructive command?":     # the card shows the command itself
        return {"kind": "bash", "title": ev.get("title"), "destructive": True,
                "command": " ".join(str(ev.get("detail") or "").split())[:300]}
    if ev.get("title") != "destructive":
        d = {"kind": ev.get("kind"), "title": ev.get("title")}
        if ev.get("kind") == "bash" and str(ev.get("detail") or "").strip():
            d["command"] = str(ev.get("detail")).strip()[:300]
        return d
    cmd = ""
    try:
        args = json.loads(ev.get("detail") or "{}")
        c = args.get("command") or args.get("cmd") or args.get("commands") or ""
        if isinstance(c, list):                          # shell: ["bash", "-lc", script]; bash_many: [cmd, cmd...]
            c = c[-1] if args.get("command") else "; ".join(map(str, c))
        cmd = c
    except (ValueError, TypeError, AttributeError):
        pass
    return {"kind": "bash", "title": "Run this destructive command?", "destructive": True,
            "command": " ".join(str(cmd).split())[:300]}
_QUIET = ("Starting MCP servers", "No MCP servers enabled")          # status lines the terminal shows its own way
_NO_STREAM = ("text_delta", "token", "delta", "thinking")


class TermFrontend:
    """One terminal attached to one Session."""

    def __init__(self, s, term, headless: bool = False, stream: bool = False):
        self.term, self.headless, self.stream = term, headless, stream
        self.denied: list[dict] = []
        self.s = None
        self.attach(s)

    # -- wiring
    def attach(self, s):
        self.s = s
        s.listeners.append(self.on_event)
        self.term.agent = s.agent
        if s.harness["type"] not in ("acp", "pi"):
            if not self.headless:
                s.agent.ask = self.term.ask_questions        # ask_user questions are answered in the terminal
            else:
                s.agent.headless = True                      # one request, no one to answer questions
        if self.headless:
            s.auto_resume = False                            # a script gets the failure now, not after a 12 minute wait
            s.agent.recover_min = 0                          # ... nor after 30 minutes of probing a dead endpoint
        self.sub = s.harness["id"] if s.harness["type"] == "acp" else None

    def detach(self):
        try:
            self.s.listeners.remove(self.on_event)
        except ValueError:
            pass

    def rebind(self, new):
        """Move the terminal to another chat (/clear, /resume): the old one is closed (its transcript stays)."""
        old = self.s
        self.detach()
        self.attach(new)
        try:
            old.close()
        except Exception:  # noqa: BLE001
            pass
        self.term.refresh_facts(new.agent)
        if hasattr(self.term, "follow_history"):
            self.term.follow_history()                       # FL-L11: history follows the chat's folder

    @property
    def can_ask(self) -> bool:
        return bool(sys.stdin and sys.stdin.isatty()) or bool(self.term.keys_factory)

    # -- events
    def on_event(self, ev: dict):
        t = ev.get("type")
        if t == "approval":
            return self._approval(ev)
        if self.stream and t not in _NO_STREAM:
            sys.__stdout__.write(json.dumps({k: v for k, v in ev.items() if k not in ("i", "t")},
                                            ensure_ascii=False, default=str) + "\n")
            sys.__stdout__.flush()
        if t == "status" and str(ev.get("text", "")).startswith(_QUIET):
            return
        if t == "status" and str(ev.get("text", "")).startswith("MCP: ") and "Failed" not in ev["text"]:
            return
        if t == "status" and not ev.get("text"):
            return
        self.term.emit(ev)

    def _approval(self, ev: dict):
        s, term = self.s, self.term
        aid = ev["id"]
        if self.headless and not self.can_ask:
            d = _denied(ev)
            self.denied.append(d)
            return s.answer(aid, "no", DESTRUCTIVE_NOTE if d.get("destructive") else DENIED_NOTE)
        # scope: what "don't ask again" would cover ("`npm test` commands", or "once" for a chained command, which is
        # never remembered): without it the menu offered a session grant that the agent then did not keep
        d, note = term.approve(ev.get("kind", ""), ev.get("title", ""), ev.get("detail", ""), ev.get("options"),
                               scope=ev.get("scope") or "")
        if self.headless and d == "no" and not note and not term.stopping:
            self.denied.append(_denied(ev))
        try:
            s.answer(aid, d, note)
        except ValueError:                                   # stopped meanwhile: the approval is gone
            pass

    # -- turns
    def wait_ready(self):
        """The first message waits for the MCP servers (with a spinner while they start)."""
        s = self.s
        if s._mcp_ready.is_set():
            return
        if not self.headless:
            self.term.spin.start("Starting MCP servers")
        try:
            s._mcp_ready.wait(float(os.environ.get("MH_MCP_START_S", "60")) + 5)
        finally:
            self.term.spin.stop()

    def _wait(self):
        s = self.s
        with s.changed:
            while s.state in ("working", "waiting"):
                s.changed.wait(0.25)

    def send(self, text: str, images: list | None = None):
        s, term = self.s, self.term
        term.stopping = term.failed = False
        term.started = True
        self.wait_ready()
        a = s.agent                                          # where this run starts: --json reports this run only,
        self._u0 = dict(getattr(a, "usage", {}) or {})       # not the totals of a resumed chat (-c, --resume)
        self._steps0 = self._tool_steps(a)
        self._ev0 = len(getattr(s, "events", []) or [])
        term.turn_begin()
        try:
            s.send(text, images)
            self._wait()
        except KeyboardInterrupt:
            term.turn_finish()
            term.p(term.st.amber("\n  Stopped."))
            term.failed = True
            self.stop()
        except ValueError as e:                              # an update is installing, an empty message...
            term.emit({"type": "error", "text": str(e)})
        finally:
            term.turn_finish()
            for th in threading.enumerate():                 # file registration and insights run just after the turn
                if th.name == "meshpool-after-turn":
                    th.join(3.0)
            term.refresh_facts(s.agent)

    def run(self, fn):
        """Run a Session action that starts a turn on its own (/grade) and wait for it to end."""
        term = self.term
        term.stopping = term.failed = False
        term.turn_begin()
        try:
            fn()
            self._wait()
        except KeyboardInterrupt:
            term.turn_finish()
            self.stop()
        finally:
            term.turn_finish()
            term.refresh_facts(self.s.agent)

    def stop(self):
        from meshpool_harness.agent.tui import _stop_turn
        term, s = self.term, self.s
        term.stopping = True
        term.spin.stop()
        try:
            s.stop()
        except Exception:  # noqa: BLE001
            pass
        _stop_turn(term, s.agent)
        end = time.time() + 4
        while s.state in ("working", "waiting") and time.time() < end:
            time.sleep(0.1)

    # -- the result of a headless run
    def reply(self) -> str:
        a = self.s.agent
        for m in reversed(getattr(a, "messages", None) or []):
            if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
                return m["content"]
        with self.s.lock:
            for e in reversed(self.s.events):
                if e.get("type") == "assistant" and str(e.get("text") or "").strip():
                    return e["text"]
        return ""

    def checked(self) -> str:
        """The "Checked:" line of this run's last turn (agent/look.py), or ""."""
        lk = getattr(self.s.agent, "last_look", None) or {}
        return str(lk.get("line") or "") if lk.get("turn") == getattr(self.s.agent, "_turn_no", 0) else ""

    def answer(self) -> str:
        """The last answer without the "Checked:" line under it: -p reports that line on its own."""
        from meshpool_harness.agent import look as L
        return L.strip_checked(self.reply(), self.checked())

    @staticmethod
    def _tool_steps(a) -> int:
        return len([m for m in getattr(a, "messages", []) or [] if m.get("role") == "tool"])

    @staticmethod
    def _usage(a, base: dict | None = None) -> dict:
        """The old keys stay; cost_usd is None when no call had a price (core/usage.py has the rest). With `base` (the
        agent's usage when this run started) the counts are this run's alone, as by_model already is."""
        u = dict(getattr(a, "usage", {}) or {})
        if base:
            for k, v in list(u.items()):
                b = base.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(b, (int, float)):
                    u[k] = max(0, v - b) if isinstance(v, int) and isinstance(b, int) else max(0.0, v - b)
        try:
            from meshpool_harness.api import usage_dict
            ud = usage_dict(SimpleNamespace(usage=u, _task=getattr(a, "_task", None)) if base else a)
            u.update({k: ud[k] for k in ("cost_usd", "unpriced_calls", "unreported_calls", "cache_write_tokens",
                                         "reasoning_tokens", "by_model") if k in ud})
        except Exception:  # noqa: BLE001
            pass
        return u

    def _error(self) -> str:
        """The last error this run showed (an unreachable endpoint, a rejected key), for a script reading --json."""
        with self.s.lock:
            evs = list(self.s.events[getattr(self, "_ev0", 0):])
        from meshpool_harness.core import errtext as ET
        return ET.for_terminal(next((str(e.get("text") or "") for e in reversed(evs) if e.get("type") == "error"), ""),
                               self.headless)

    def result(self, failed: bool) -> dict:
        a = self.s.agent
        out = {"type": "result", "ok": not failed, "result": self.answer(), "session_id": self.s.id,
               "chat_id": self.s.id, "usage": self._usage(a, getattr(self, "_u0", None)),
               "steps": max(0, self._tool_steps(a) - getattr(self, "_steps0", 0)),
               "denied": list(self.denied)}
        err = self._error() if failed else ""
        if err:
            out["error"] = err
        if self.checked():                                   # the look at what it made (agent/look.py)
            out["checked"] = self.checked()
        rv = getattr(a, "last_review", None)
        if rv:                                               # the independent check of a big task (agent/grader.py)
            out["review"] = {k: v for k, v in rv.items() if k not in ("type", "turn")}
        return out

    def denial_note(self) -> str:
        """What -p prints after a run that refused actions: each one by name, with the flag or rule that allows it,
        then a re-run that works for all of them. "--permission-mode edits" is offered only for file edits: it never
        lets a command run (OM-01)."""
        if not self.denied:
            return ""
        rows, kinds = [], set()
        for d in self.denied[:8]:
            what, how, k = _allow_hint(d, getattr(self.s.agent, "mode", "ask"))
            kinds.add(k)
            rows.append(f"  {what}: {how}")
        more = [f"  (and {len(self.denied) - 8} more)"] if len(self.denied) > 8 else []
        if kinds <= {"edit"}:
            hint = "re-run with --permission-mode edits (or --auto for full access)."
        elif kinds <= {"outside"}:
            hint = "re-run with --dir set to a folder that holds those files: edits stay inside the project folder."
        elif kinds <= {"destructive"}:
            hint = DESTRUCTIVE_HINT
        else:
            hint = ("re-run with --permission-mode auto (full access, the same as --auto), or allow each one as "
                    "shown above.")
            if "destructive" in kinds:
                hint += " " + DESTRUCTIVE_HINT
        if "command" in kinds and getattr(self.s.agent, "mode", "ask") in ("ask", "edits"):
            try:
                from meshpool_harness.core import sandbox as SBX
                if not SBX.available():
                    hint += (" Ordinary commands in the folder run without a question only inside the command sandbox "
                             "(bubblewrap on Linux, sandbox-exec on macOS), which this computer does not have.")
            except Exception:  # noqa: BLE001
                pass
        return "\n".join([f"{len(self.denied)} action(s) were not allowed. In -p mode nobody can approve them:",
                          *rows, *more, "To allow them, " + hint])


def _allow_hint(d: dict, mode: str) -> tuple[str, str, str]:
    """(what was refused, what allows it, its kind) for one refused action of a -p run. A rule offered here is checked
    against the permission code itself, so it is one that really lets the command through."""
    from meshpool_harness.agent import core as K
    from meshpool_harness.core import permrules as PR
    title = str(d.get("title") or d.get("kind") or "an action").rstrip("?")
    cmd = str(d.get("command") or "")
    if d.get("destructive"):
        rule = f"bash:{cmd}"
        ok = cmd and K._allow_hit([rule], "bash", {"command": cmd})
        return (f"`{cmd[:120]}`", (f'--auto --allowed-tools "{rule}"' if ok else "run it yourself")
                + " (deletes or rewrites things)", "destructive")
    if d.get("kind") == "bash" and cmd:
        shown = f"`{' '.join(cmd.split())[:120]}`"
        if title.startswith("Your rule"):
            return shown, "your saved ask rule covers it; change the rule in permissions.json", "rule"
        if title.startswith("Change "):
            return shown, "it changes meshpool's own rule files; run it yourself", "rule"
        for p in PR.grant_patterns(cmd)[:1]:
            rule = f"bash:{p}*"
            if K._allow_hit([rule], "bash", {"command": cmd}):
                return shown, f'--allowed-tools "{rule}"', "command"
        if K._allow_hit(["bash"], "bash", {"command": cmd}):
            return shown, "--allowed-tools bash (every command that does not delete things)", "command"
        return shown, "--permission-mode auto", "command"
    if d.get("kind") == "edit":
        if title.startswith("Your rule"):
            return title, "your saved ask rule covers it; change the rule in permissions.json", "rule"
        if title.startswith("Change ") and "outside the project folder" not in title:
            return title, "it changes meshpool's own rule files; make that change yourself", "rule"
        if "outside the project folder" in title:
            # no mode lets an edit leave the folder (Workspace refuses it): name the run that can make it
            return title, "--dir with a folder that holds the file (edits stay inside the project folder)", "outside"
        if mode in ("edits", "auto"):
            # the run already accepts edits: it was refused for another reason (a file open in Office, a saved rule)
            return title, "close the file in Office or change your saved rule, then run again", "other"
        return title, "--permission-mode edits", "edit"
    if d.get("kind") == "read":
        return title, "--permission-mode auto (it reaches outside the folder)", "other"
    return title, "--permission-mode auto", "other"


_IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def collect_attachments(paths: list, workdir: str) -> tuple[str, list]:
    """(text to add to the message, images) for files the user attached with /attach or --attach. Pictures go as
    images (the same {name, mime, data} the app sends); other files are named with @ (copied into the folder first
    when they live elsewhere, the way the app's attach button does)."""
    import base64
    from pathlib import Path
    root = Path(workdir).resolve()
    names, images = [], []
    for raw in paths:
        p = Path(os.path.expanduser(raw))
        if not p.is_file():
            raise ValueError(f"Attachment not found: {raw}")
        if p.suffix.lower() in _IMAGES:
            images.append({"name": p.name, "mime": _IMAGES[p.suffix.lower()],
                           "data": base64.b64encode(p.read_bytes()).decode()})
        elif root in p.resolve().parents:
            names.append("@" + p.resolve().relative_to(root).as_posix())
        else:
            from meshpool_harness.ui import workbench as W
            names.append("@" + W.attach(str(root), p.name, p.read_bytes())["path"])
    return ("Attached: " + " ".join(names)) if names else "", images


def split_args(arg: str) -> list:
    """The words of a slash command's argument, with "quoted words" kept together (/schedule add --name "Daily news").
    A backslash is kept as it is, so a Windows path stays whole. Unbalanced quotes fall back to plain words."""
    import shlex
    try:
        lex = shlex.shlex(arg or "", posix=True)
        lex.whitespace_split, lex.escape, lex.commenters = True, "", ""
        return list(lex)
    except ValueError:
        return (arg or "").split()


# ---------- slash commands that call the Session ---------------------------------------------------------------

class SharedCommands:
    """Mixed into tui.Session. Needs self.s (the shared Session), self.front, self.term and self.agent."""

    def _need_shared(self) -> bool:
        if getattr(self, "s", None) is None:
            self.term.p(self.term.st.dim("  Not available in this session."))
            return False
        return True

    def _rebind(self):
        self.agent = self.s.agent
        self.sub = self.front.sub
        self.term.agent = self.agent
        self.term.refresh_facts(self.agent)

    def cmd_plan(self, arg):
        if not self._need_shared():
            return
        t, st = self.term, self.term.st
        on = (not self.s.plan) if arg == "" else arg.lower() in ("on", "1", "yes", "true")
        self.s.set_plan(on)
        t.p(st.dim("  Plan first: the agent investigates and replies with a plan before changing anything."
                   if on else "  Plan mode is off."))

    def cmd_goal(self, arg):
        if not self._need_shared():
            return
        if not arg:
            return self.term.p(self.term.st.dim(f"  Goal: {self.s.goal or 'none'}. /goal <text> sets one, /goal off clears it."))
        self.s.set_goal("" if arg.lower() in ("off", "clear", "none") else arg)
        if self.s.goal:
            self.send(self.s.goal)                           # the goal is the first request; the Session keeps going

    def cmd_style(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import styles as ST
        t, st = self.term, self.term.st
        rows = ST.listing()["styles"]
        ids = [r["id"] for r in rows]
        if not arg:
            cur = self.s.style_id()
            i = t.choose("Writing style", [], [f"{r.get('name', r['id'])}" + ("  (current)" if r["id"] == cur else "")
                                               for r in rows])
            if i is None:
                return
            arg = ids[i]
        self.s.set_style(arg)
        t.p(st.dim(f"  Style: {arg}."))

    def cmd_agent(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import chats
        t, st = self.term, self.term.st
        hs = [h for h in chats.harness_info()["harnesses"] if h.get("ready", True) and h.get("available", True)
              and not h.get("unsupported") and h["id"] != "meshpool-auto"]
        if not arg:
            cur = self.s.harness["id"]
            i = t.choose("Switch agent", [], [h["name"] + ("  (current)" if h["id"] == cur else "") for h in hs])
            if i is None:
                return
            arg = hs[i]["id"]
        self.s.switch_harness(arg)
        self._rebind()
        t.p(st.dim(f"  Now using {self.s.harness['name']}."))

    def cmd_mode(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import harnesses as HR
        kinds = {"code": "code", "work": "cowork", "cowork": "cowork"}
        if arg not in kinds:
            return self.term.p(self.term.st.dim("  /mode code | work. Auto picks the agent when a chat starts: "
                                                "meshpool --mode auto. (/auto sets what the agent may do without asking.)"))
        self.s.switch_harness(HR.own_for(kinds[arg]))
        self._rebind()
        self.term.p(self.term.st.dim(f"  Now using {self.s.harness['name']}."))

    def _turn_pick(self, arg: str, title: str):
        t = self.term
        turns = self.s.cp.turns() if self.s.cp else []
        if not turns:
            t.p(t.st.dim("  No turns are recorded yet" + (f" ({self.s.cp.disabled})." if self.s.cp and self.s.cp.disabled else ".")))
            return None
        if arg.isdigit():
            return int(arg)
        # the menu numbers its rows itself: a row that began with the turn's number read "1. 1. fix the test" (OM-08)
        i = t.choose(title, [], [(x.get('title') or f"Turn {x['n']}") + ("  (" + str(len(x['changed'])) + " files)"
                                                                         if x.get('changed') else "") for x in turns],
                     sel=len(turns) - 1)
        return None if i is None else turns[i]["n"]

    def _show_patch(self, patch: str):
        t, st = self.term, self.term.st
        for ln in patch.splitlines()[:400]:
            if ln.startswith("+") and not ln.startswith("+++"):
                t.p("  " + st.green(ln))
            elif ln.startswith("-") and not ln.startswith("---"):
                t.p("  " + st.red(ln))
            else:
                t.p("  " + st.dim(ln))

    def cmd_diff(self, arg):
        if not self._need_shared():
            return
        n = int(arg) if arg.isdigit() else None
        d = self.s.cp.diff(n) if self.s.cp else {"patch": "", "note": "No checkpoints for this chat."}
        if not d.get("patch"):
            return self.term.p(self.term.st.dim("  " + (d.get("note") or "No changes recorded.")))
        self._show_patch(d["patch"])

    def cmd_undo(self, arg):
        if not self._need_shared():
            return
        t = self.term
        n = self._turn_pick(arg, "Undo from which turn?")
        if n is None:
            return
        turns_before = self.s.cp.turns() if self.s.cp else []
        d = self.s.cp.diff(n)
        if d.get("patch"):
            self._show_patch(d["patch"])
        i = t.choose(f"Undo turn {n} and everything after it?", [], ["Undo", "Cancel"], sel=1)
        if i == 0:
            r = self.s.revert(n)
            self._rebind()
            if r.get("conversation"):            # the status line names the files; say what left the conversation
                title = next((x.get("title") for x in turns_before if x.get("n") == n), "") or ""
                t.p(t.st.dim(f"  The conversation is back to before turn {n}" + (f" ({title})" if title else "")
                             + "."))

    def cmd_grade(self, arg):
        if not self._need_shared():
            return
        t, st = self.term, self.term.st
        t.p(st.dim("  A separate reviewer is checking the last piece of work..."))
        try:
            self.front.run(self.s.grade)
        except ValueError as e:
            t.p(st.amber(f"  {e}"))

    def cmd_attach(self, arg):
        if not self._need_shared():
            return
        text, images = collect_attachments([a for a in arg.split() if a], self.s.workdir)
        if not text and not images:
            return self.term.p(self.term.st.dim("  /attach PATH [PATH...] then your message follows."))
        self.pending_attach = (text, images)
        self.term.p(self.term.st.dim(f"  {len(images)} image(s) and {text.count('@') if text else 0} file(s) "
                                     "will go with your next message."))

    def cmd_clear(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import chats
        t = self.term
        v = chats.create(dict(self.front.body, task=""))
        new = chats.get(v["id"], no_mcp=bool(self.front.body.get("no_mcp")))
        self.front.rebind(new)
        self._rebind()
        t.usage = {"in": 0, "cached": 0, "out": 0, "calls": 0}
        t.ctx_tokens, t.started, t.last_block = 0, False, None
        if t.interactive:
            t._write(t._clear_seq() + "\x1b[2J\x1b[H")
        t.startup()

    def cmd_resume(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import chats
        t, st = self.term, self.term.st
        rows = chats.search(arg) if arg else chats.listing()
        rows = [r for r in rows if r.get("id") != self.s.id][:30]
        if not rows:
            return t.p(st.dim("  No other chats."))
        i = t.choose("Resume which chat?", [], [f"{r.get('title') or 'Chat'}  {st.dim(str(r.get('workdir') or ''))}"
                                                for r in rows])
        if i is None:
            return
        new = chats.get(rows[i]["id"], no_mcp=bool(self.front.body.get("no_mcp")))
        self.front.rebind(new)
        self._rebind()
        t.p(st.dim(f"  Resumed: {new.agent.title or 'chat'} ({len(getattr(new.agent, 'messages', [])) - 1} messages)."))

    # -- the app's features, through the same handlers `meshpool chats|projects|...` use (cli_app) ---------------------
    def _cli(self, argv: list):
        from meshpool_harness import cli_app
        rc, text = cli_app.run_text(argv)
        st = self.term.st
        self.term.scroll([""] + [("  " + x) if rc == 0 else "  " + st.amber(x) for x in (text or "(nothing)").splitlines()])

    def cmd_chats(self, arg):
        self._cli(["chats", "search", arg] if arg else ["chats", "list"])

    def cmd_rename(self, arg):
        if not self._need_shared():
            return
        if not arg:
            return self.term.p(self.term.st.dim("  /rename NEW TITLE"))
        self._cli(["chats", "rename", self.s.id, arg])

    def cmd_fork(self, arg):
        """A new chat from this one, up to a turn (default: all of it); /resume ID goes there."""
        if not self._need_shared():
            return
        self._cli(["chats", "fork", self.s.id] + (["--turn", arg] if arg.isdigit() else []))

    def cmd_delete(self, arg):
        if not self._need_shared():
            return
        t = self.term
        if t.choose("Delete this chat?", [], ["Keep it", "Delete it"]) != 1:
            return t.p(t.st.dim("  Kept."))
        self._cli(["chats", "delete", self.s.id])

    def cmd_receipt(self, arg):
        if self._need_shared():
            self._cli(["chats", "receipt", self.s.id])

    def cmd_usage(self, arg):
        self._cli(["usage"] + (["--days", arg] if arg.isdigit() else []))
        if getattr(self, "s", None) is not None:
            self._cli(["chats", "receipt", self.s.id])

    def cmd_project(self, arg):
        if arg.startswith("use ") and self._need_shared():          # move this chat into a project
            return self._cli(["chats", "move", self.s.id, arg[4:].strip()])
        self._cli(["projects"] + (split_args(arg) if arg else ["list"]))

    def cmd_memory(self, arg):
        self._cli(["memory"] + (arg.split(None, 1) if arg else ["list"]))

    def cmd_skills(self, arg):
        self._cli(["skills"] + (split_args(arg) if arg else ["list"]))

    def cmd_schedule(self, arg):
        self._cli(["schedule"] + (split_args(arg) if arg else ["list"]))

    def cmd_settings(self, arg):
        parts = arg.split(None, 1)
        if len(parts) == 2:
            self._cli(["settings", "set", f"{parts[0]}={parts[1]}"])
        else:
            self._cli(["settings", "get"] + parts)

    def cmd_checks(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import checks
        run = f"{self.s.id}:{int(self.s.t_turn)}" if getattr(self.s, "t_turn", None) else ""
        r = checks.summary(run) if run else {}
        self.term.p(self.term.st.dim("  " + (str(r.get("line") or r.get("text") or "") or "No checks ran in this chat.")))

    def cmd_worktree(self, arg):
        import io
        from meshpool_harness import worktrees as WT
        buf = io.StringIO()
        WT.cli(arg.split() or ["list"], out=buf)
        self.term.scroll([""] + ["  " + x for x in buf.getvalue().rstrip().splitlines()])

    def cmd_effort(self, arg):
        if not self._need_shared():
            return
        from meshpool_harness import chats
        v = chats.session_action(self.s.id, "effort", {"effort": arg}) if arg else chats.view(self.s.id, 10 ** 9)
        self.term.effort = v.get("effort") or ""
        self.term.p(self.term.st.dim(f"  Effort: {v.get('effort') or 'the model default'}. "
                                     "/effort low|medium|high|xhigh|off sets it for this chat."))

    def cmd_rewind(self, arg):
        """Back to a turn: the files and the conversation (chat_rewind, the app's Rewind), and that turn's message
        comes back in the box to edit and send again."""
        if not self._need_shared():
            return
        from meshpool_harness import chat_rewind as CR
        n = self._turn_pick(arg, "Rewind to which turn?")
        if n is None:
            return
        p = next((x for x in CR.points(self.s.id) if x.get("turn") == n), None)
        if not p:
            return self.term.p(self.term.st.dim(f"  No turn {n} in this chat."))
        r = CR.rewind(self.s.id, p["message_index"], session=self.s)
        if r.get("forked"):
            return self.term.p(self.term.st.dim(f"  This agent cannot rewind in place; the earlier state is chat {r['forked']} "
                                                "(/resume it)."))
        self._rebind()
        self.term.prefill = r.get("text", "")
