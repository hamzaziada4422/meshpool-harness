"""Hides keys, tokens and passwords in what the chat shows: tool output, tool cards (command, diff) and approval
details, on screen and in the saved transcript the app shows. What the model reads (and the agent's own session
file, which holds it) is not changed: an agent that has to fix a broken .env still sees it. The model's own words
are not filtered. MH_REDACT=0 turns this off."""
from __future__ import annotations

import os
import re

HIDDEN = "[hidden]"

_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"),
    re.compile(r"\b(?:sk|rk|pk)-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{20,}"),        # OpenAI, Anthropic, Stripe
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}"),        # GitHub
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                                         # AWS access key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),                                        # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),                                             # Google API key
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),                                            # GitLab
    re.compile(r"\bey[A-Za-z0-9_-]{15,}\.ey[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),     # a JWT
]
# key = value where the key names a secret: the key stays, the value goes
_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_.-]*(?:api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|pwd|private[_-]?key|"
                     r"client[_-]?secret|auth)[A-Z0-9_.-]*[\"']?\s*[=:]\s*[\"']?)(?!(?:bearer|token|basic)\b)([^\s\"',;]{6,})")
_BEARER = re.compile(r"(?i)\b((?:authorization|proxy-authorization)\s*:\s*(?:bearer|token|basic)?\s*)([^\s\"']{8,})")
_URL_PASS = re.compile(r"(://[^/\s:@]+:)([^@\s/]{3,})(@)")


def redact(text: str) -> str:
    """text with secrets replaced by [hidden]."""
    if not text or os.environ.get("MH_REDACT") == "0":
        return text
    s = str(text)
    for p in _PATTERNS:
        s = p.sub(HIDDEN, s)
    s = _BEARER.sub(lambda m: m.group(1) + HIDDEN, s)
    s = _ASSIGN.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) == HIDDEN else HIDDEN), s)
    s = _URL_PASS.sub(lambda m: m.group(1) + HIDDEN + m.group(3), s)
    return s
