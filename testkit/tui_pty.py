"""Terminal UI check in a real pseudo-terminal against the mock model (testkit/mock_llm.py on port 8911).

Covers what the unit tests cannot: Esc stops a running turn, prompt history survives a restart (Up arrow), and the
slash commands /effort, /plan, /context, /permissions, /tokens, /copy, /export, /diff and /resume answer.
Usage: python testkit/tui_pty.py WORKDIR   (exits 1 when a check fails). Linux and macOS only.
"""
import os
import pty
import re
import select
import subprocess
import sys
import tempfile
import time

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {str(detail)[-400:]}"), flush=True)


class Tui:
    def __init__(self, cwd, home):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            env = {k: v for k, v in os.environ.items() if not re.match(r"(OPENAI|ANTHROPIC|CODEX|CLAUDE)_", k)}
            env.update(MESHPOOL_HOME=home, PYTHONPATH=APP, TERM="xterm-256color", COLUMNS="120", LINES="40",
                       MH_MCP_START_S="1", MESHPOOL_BASE_URL="http://127.0.0.1:8911/v1", MESHPOOL_API_KEY="test-key-1234")
            os.execvpe(sys.executable, [sys.executable, "-m", "meshpool_harness", "--no-mcp", "--auto"], env)
        self.buf = ""

    def read(self, secs=0.5):
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.1)
            if r:
                try:
                    self.buf += os.read(self.fd, 65536).decode("utf-8", "replace")
                except OSError:
                    break
        return self.buf

    def plain(self):
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07", "", self.buf)

    def wait(self, pattern, secs=30):
        end = time.time() + secs
        while time.time() < end:
            self.read(0.3)
            if re.search(pattern, self.plain()):
                return True
        return False

    def send(self, s):
        os.write(self.fd, s.encode())

    def line(self, s):
        self.send(s)
        time.sleep(0.2)
        self.send("\r")

    def close(self):
        try:
            self.line("/exit")
            self.read(2)
        finally:
            try:
                os.kill(self.pid, 9)
            except OSError:
                pass
            os.waitpid(self.pid, 0)


def main():
    work = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    home = tempfile.mkdtemp()
    open(os.path.join(home, "meshpool.config"), "w").write(
        "MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1\nMESHPOOL_API_KEY=test-key-1234\n")
    os.makedirs(work, exist_ok=True)
    subprocess.run("git init -q && echo 'x = 1' > a.py && git add -A && git -c user.email=t@t -c user.name=t commit -qm i "
                   "&& echo 'x = 2' > a.py", shell=True, cwd=work)
    t = Tui(work, home)
    check("terminal starts", t.wait(r"Esc stops|/help|ask before|meshpool", 40), t.plain()[-600:])
    t.line("/effort high")
    check("/effort sets the level", t.wait(r"high reasoning effort"), t.plain()[-300:])
    t.line("/plan on")
    check("/plan turns plan mode on", t.wait(r"Plan mode is on"), t.plain()[-300:])
    t.line("/plan off")
    t.wait(r"Plan mode is off")
    t.line("/context")
    check("/context shows a breakdown", t.wait(r"Tool definitions.*\n(.|\n)*of [\d,]+ tokens"), t.plain()[-600:])
    t.line("/permissions")
    check("/permissions lists rules", t.wait(r"Allow:(.|\n)*Deny:"), t.plain()[-400:])
    t.line("/diff")
    check("/diff shows the change", t.wait(r"a\.py(.|\n)*\+x = 2"), t.plain()[-400:])
    t.line("say hello")
    check("a turn gets a reply", t.wait(r"[Hh]ello", 60), t.plain()[-400:])
    t.line("/tokens")                                    # typed while the turn may still run: kept for the prompt
    check("/tokens reports usage", t.wait(r"model calls"), t.plain()[-300:])
    t.line("/copy")
    check("/copy copies the reply", t.wait(r"Copied\.|Could not reach the clipboard"), t.plain()[-300:])
    t.line("/export out.md")
    check("/export writes the transcript", t.wait(r"Saved the conversation") and
          "## You" in open(os.path.join(work, "out.md")).read(), t.plain()[-300:])
    t.line("think slowly about this")                    # the mock takes 30 s to answer this
    t.wait(r"Thinking", 15)
    time.sleep(2)
    t0 = time.time()
    t.send("\x1b")                                       # Esc
    stopped = t.wait(r"Stopped", 15)
    check("Esc stops a running turn within 15 s", stopped and time.time() - t0 < 15, t.plain()[-500:])
    t.line("/exit")
    check("leaving prints what the session took", t.wait(r"This session: \d+ model calls?, [\d,]+ tokens in", 10),
          t.plain()[-400:])
    t.close()
    # a second session in the same folder: history and /resume
    t2 = Tui(work, home)
    t2.wait(r"Esc stops|/help|meshpool", 40)
    t2.send("\x1b[A")                                    # Up arrow: /exit, then the prompt before it
    t2.read(0.3)
    t2.send("\x1b[A")
    check("Up arrow recalls a prompt from the last session", t2.wait(r"think slowly about this", 5), t2.plain()[-300:])
    t2.send("\x15")                                      # Ctrl+U clears the line
    t2.read(0.5)
    t2.line("/resume")
    picked = t2.wait(r"Resume which session|say hello", 10)
    t2.send("\r")
    check("/resume opens the earlier session", picked and t2.wait(r"Resumed", 10), t2.plain()[-500:])
    t2.close()
    ok = sum(1 for _, r in RESULTS if r)
    print(f"\n{ok}/{len(RESULTS)} passed")
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
