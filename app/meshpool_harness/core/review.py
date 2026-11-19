"""Independent review of a code change against its request (WP3). Pure helpers: gathering the material, the
reviewer's prompt, and reading its reply. The model call itself lives in Agent (it reuses the agent's transport)."""
from __future__ import annotations

import os
import re
import subprocess

REVIEW_SPECS = ("code.build", "code.debug", "code.refactor", "code.test")
CAP = 12000

SYSTEM = ("You check a code change against the request that asked for it. You cannot run code. Report only concrete, "
          "checkable problems: a rule, case or requirement in the request that the change does not implement or gets "
          "wrong. For each, give a specific input or scenario and the result the request requires, so it can be "
          "tested. Do not report style, naming, missing comments, or anything the request does not ask for. Do not "
          "guess about code you cannot see. If you find nothing concrete, reply exactly LGTM. Otherwise reply with at "
          "most 5 numbered findings, one or two lines each.")

INJECT = ("[Meshpool] A second reviewer read your change against the request and raised these points. They may be "
          "wrong. Test all of them together in ONE script (the case each gives); fix only those the script shows "
          "are real, then rerun your checks once. If none are real, say so in one line.\n")

_TAGS = re.compile(r"\n*<(reproduction|environment_snapshot|repo_map|repo-map)\b.*", re.S)


def enabled(spec: str) -> bool:
    v = os.environ.get("MH_REVIEW", "1")
    return v != "0" and spec in REVIEW_SPECS


def clean_request(text: str) -> str:
    """The user's own words: no appended reproduction, snapshot or repo map."""
    t = (text or "").split("\n\n​")[0]
    return _TAGS.sub("", t).strip()


def _git(root: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=20)
    except Exception:  # noqa: BLE001
        return None
    return r.stdout if r.returncode == 0 else None


def gather_change(root: str, turn_files, cap: int = CAP) -> str:
    """The change as text: git diff vs HEAD plus the full content of new untracked files; without git, the files."""
    parts: list[str] = []
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside is not None and inside.strip() == "true" and _git(root, "rev-parse", "HEAD") is not None:
        diff = _git(root, "diff", "HEAD") or ""
        if diff.strip():
            parts.append(diff)
        for rel in (_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines():
            p = os.path.join(root, rel)
            if rel.endswith((".pyc",)) or not os.path.isfile(p):
                continue
            try:
                body = open(p, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            parts.append(f"--- new file: {rel}\n{body}")
    else:
        for f in sorted(turn_files or ()):
            try:
                body = open(f, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            parts.append(f"--- file: {os.path.relpath(f, root)}\n{body}")
    out = "\n".join(parts)
    return out[:cap] + ("\n[...change truncated]" if len(out) > cap else "")


def build_user(request: str, change: str, last_cmd: dict | None) -> str:
    s = f"REQUEST:\n{request[:6000]}\n\nCHANGE:\n{change}\n"
    if last_cmd:
        s += f"\nLAST CHECK RUN: `{str(last_cmd.get('cmd', ''))[:200]}` exited with {last_cmd.get('code')}\n"
    return s


def findings(reply: str) -> str:
    """The reviewer's findings, or '' when it found nothing (LGTM, empty)."""
    r = (reply or "").strip()
    if not r or r.upper().startswith("LGTM"):
        return ""
    return r[:2500]
