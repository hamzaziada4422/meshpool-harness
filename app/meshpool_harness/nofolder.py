"""A chat with no folder of its own choosing, asked for a file (Daniel, round 3: "a deck from this PDF").

The chat has a private folder, so the file goes there and shows in the chat's files. When the agent cannot write it, the
reply says so in plain words and offers "Add a folder"; it never sends the user to "a session that has a shell".

note(root, asked)   the hidden note for the model, or "" when this is not that case
plain(text)         the reply with a "run this in a session that has a shell" sentence turned into the plain offer
wants_folder(text)  True when a reply offers a folder (the app then shows an "Add a folder" button)
"""
from __future__ import annotations

import re

OFFER = ("I can save this file once the chat has a folder. Choose Add a folder in the sidebar, then send the request "
         "again.")

_ELSEWHERE = re.compile(
    r"[^.\n]*\b(?:run|use|try|do|open|paste)\b[^.\n]*\b(?:session|environment|setup|machine|terminal|tool)s?\b[^.\n]*"
    r"\b(?:shell|terminal|command line|file writing|write files|file access)\b[^.\n]*\.?"
    r"|[^.\n]*\b(?:session|environment|setup)\b[^.\n]*\b(?:has|have|with)\s+(?:a\s+|an\s+)?(?:shell|terminal|command line|"
    r"file writing)\b[^.\n]*\.?", re.I)
_FOLDER = re.compile(r"\badd a folder\b", re.I)


def asks_for_file(text: str) -> bool:
    from meshpool_harness import office_intent as OI
    own = str(text or "").split("\n\n​")[0]
    return OI.styled_deliverable(own) or OI.names_output(own)


def note(root, asked: str) -> str:
    """The note for the model: where the file goes, and what to say when it cannot be written."""
    from meshpool_harness import project_store as PS
    if not root or not PS.is_scratch(str(root)) or not asks_for_file(asked):
        return ""
    return ("[Meshpool] This chat has no folder of the user's choosing. It has its own folder, " + str(root) + ": save "
            "the file there and name it in your reply; the user finds it in the chat's files. If your tools cannot "
            "write the file, say so in one plain sentence and offer: \"" + OFFER + "\" Never tell the user to run "
            "it elsewhere, in another session, or in a place that has a shell or file writing.")


def plain(text: str) -> str:
    """A sentence that sends the user to "a session that has a shell" becomes the plain offer."""
    t = str(text or "")
    if not _ELSEWHERE.search(t):
        return t
    out = _ELSEWHERE.sub(OFFER, t, count=1)
    return _ELSEWHERE.sub("", out).replace("  ", " ")


def wants_folder(text: str) -> bool:
    t = str(text or "")
    return bool(_FOLDER.search(t) or _ELSEWHERE.search(t))
