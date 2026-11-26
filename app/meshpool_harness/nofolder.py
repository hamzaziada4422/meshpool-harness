"""A chat with no folder of its own choosing, asked for a file (Daniel, round 3: "a deck from this PDF").

The chat has a private folder, so the file goes there and shows in the chat's files. When the agent cannot write it, the
reply says so in plain words and offers "Add a folder"; it never sends the user to "a session that has a shell".

note(root, asked)   the hidden note for the model, or "" when this is not that case
plain(text, made_file)
                    the reply with a "run this in a session that has a shell" sentence turned into the plain offer;
                    left alone when the turn made a file
wants_folder(text, made_file)
                    True when a reply offers a folder (the app then shows an "Add a folder" button)
"""
from __future__ import annotations

import re

OFFER = ("I can save this file once the chat has a folder. Choose Add a folder in the sidebar, then send the request "
         "again.")

# A deflection is one sentence that names a place to go (a session, an environment, a setup, a machine) and what that
# place has (a shell, a terminal, file writing), and sends the user away ("another session", "a session that has",
# "an environment with a shell"). Each is a flat keyword search run on one sentence at a time: no nested wildcards, so a long reply
# costs linear time (B2: the old single pattern took 36 s on 2 KB). "a terminal" alone is not a place: "Run the tests
# in a terminal with the shell command `pytest -q`" is a correct reply, not a deflection.
_PLACE = re.compile(r"\b(?:session|environment|setup|machine|computer)s?\b", re.I)
_HAS = re.compile(r"\b(?:shell|terminal|command line|file writing|write files|file access)\b", re.I)
_AWAY = re.compile(r"\b(?:another|a different|a separate|elsewhere|one that|that has|which has|with (?:a |an )?"
                   r"(?:shell|terminal|command line|file writing))\b", re.I)
# Sentence ends: . ! ? followed by white space or the end, or a line break. "plot.py" and "v1.2" are never split.
_SENTENCE = re.compile(r"[^\n]*?(?:[.!?](?=\s|$)|\n|$)")
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


def _sentences(text: str) -> list:
    out, pos, n = [], 0, len(text)
    while pos < n:
        m = _SENTENCE.match(text, pos)
        end = m.end() if m and m.end() > pos else n
        out.append(text[pos:end])
        pos = end
    return out


def _deflects(sentence: str) -> bool:
    s = sentence[:2000]
    return bool(_PLACE.search(s) and _HAS.search(s) and _AWAY.search(s))


def plain(text: str, made_file: bool = False) -> str:
    """A sentence that sends the user to "a session that has a shell" becomes the plain offer. Only whole sentences
    are replaced, so a word or a file name is never cut; a reply whose turn made a file is left as it is."""
    t = str(text or "")
    if made_file or not wants_folder(t):
        return t
    out, offered = [], False
    for part in _sentences(t):
        if not _deflects(part):
            out.append(part)
            continue
        lead = part[:len(part) - len(part.lstrip())]
        tail = part[len(part.rstrip()):]
        if not offered:
            out.append(lead + OFFER + tail)
            offered = True
        elif tail and "\n" in tail:
            out.append(tail)
    return "".join(out)


def wants_folder(text: str, made_file: bool = False) -> bool:
    t = str(text or "")
    if _FOLDER.search(t):
        return True
    return not made_file and any(_deflects(p) for p in _sentences(t))
