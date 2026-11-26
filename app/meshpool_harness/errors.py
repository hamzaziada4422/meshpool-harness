"""One plain sentence for an exception, shared by the app's routes and the terminal (so they say the same thing)."""
from __future__ import annotations

import errno as _errno
import os
import re
import zipfile


def _name(path) -> str:
    """A file's name for a message: the last part of its path, not the whole path."""
    p = str(path or "").rstrip("/\\")
    return os.path.basename(p.replace("\\", "/")) or p


def os_error(e: BaseException) -> str:
    """An OSError in plain words with what to do, or "" when it is not one this knows."""
    if not isinstance(e, OSError):
        return ""
    name = _name(getattr(e, "filename", "") or "")
    what = f"{name}" if name else "the file"
    win = getattr(e, "winerror", None)
    if win in (32, 33):                              # Windows: another program has the file open
        return f"{what} is open in another program (for example Word or Excel). Close it there, then try again."
    if isinstance(e, FileNotFoundError):
        return f"No file or folder named {what} was found. Check the name and the folder, then try again."
    if isinstance(e, IsADirectoryError):
        return f"{what} is a folder, not a file. Pick a file inside it."
    if isinstance(e, NotADirectoryError):
        return f"{what} is a file, not a folder. Pick its folder instead."
    if isinstance(e, PermissionError):
        return (f"meshpool is not allowed to open {what}. Check its permissions, or close the program that has it "
                "open, then try again.")
    if isinstance(e, FileExistsError):
        return f"{what} already exists. Pick another name."
    if getattr(e, "errno", None) == _errno.ENOSPC:
        return "The disk is full. Free some space, then try again."
    if getattr(e, "errno", None) == _errno.EROFS:
        return f"{what} is on a read-only disk. Save it somewhere else."
    if isinstance(e, TimeoutError):
        return "It took too long and was stopped. Try again in a moment."
    if getattr(e, "strerror", None):
        return f"{e.strerror} ({what})." if name else f"{e.strerror}."
    return ""


def plain(e: BaseException) -> str:
    if isinstance(e, zipfile.BadZipFile):
        return "That file is not a valid zip file."
    if isinstance(e, (TypeError, AttributeError)):
        return f"The request has a missing or wrong value ({e})."
    if isinstance(e, UnicodeDecodeError):
        return "That file is not text in an encoding meshpool can read (it may be a binary file)."
    said = os_error(e)
    if said:
        return said
    return str(e).strip("'\"") or type(e).__name__


def unexpected(e: BaseException) -> str:
    """The message for an error a route did not expect (a 500): plain words when the error is a known kind (a file
    that is locked, missing or not allowed), else what it said and what to do, never the exception class alone."""
    said = os_error(e) or (plain(e) if isinstance(e, (zipfile.BadZipFile, UnicodeDecodeError)) else "")
    if said:
        return said
    text = str(e).strip("'\"")
    return (f"meshpool could not finish this: {text or 'an internal error with no message'} ({type(e).__name__}). "
            "Try again; if it keeps happening, restart meshpool and report it.")


# a tool's failure as the agent and its tool card see it: "ERROR: PermissionError: [Errno 13] Permission denied: 'x'"
# or docread's "Could not read x.docx: PackageNotFoundError: ..."
_TOOL_EXC = re.compile(r"^(ERROR: |Could not read (?P<file>[^\n:]+): )(?P<type>[A-Z]\w*(?:Error|Exception|Exit))"
                       r"(?::\s*(?P<msg>[^\n]*))?")
_ERRNO = re.compile(r"\[(?:Errno|WinError) (-?\d+)\]\s*([^:]+)(?::\s*'([^']*)')?")


def tool_card(out: str) -> str:
    """A tool output for its card in the chat: a raw Python exception at its start becomes plain words. What the model
    reads is not changed (the agent keeps its own text); only the card shows this."""
    s = str(out or "")
    m = _TOOL_EXC.match(s)
    if not m:
        return s
    typ, msg, file = m.group("type"), (m.group("msg") or "").strip(), m.group("file")
    rest = s[m.end():]
    head = "ERROR: " if m.group(1) == "ERROR: " else ""
    e = _ERRNO.search(msg)
    if e:
        code, path = int(e.group(1)), e.group(3) or file or ""
        name = _name(path) or "the file"
        if code in (32, 33) and "WinError" in msg:
            text = f"{name} is open in another program (for example Word or Excel). Close it there, then try again."
        elif code == _errno.EACCES or code == _errno.EPERM or typ == "PermissionError":
            text = (f"meshpool is not allowed to open {name}. Check its permissions, or close the program that has it "
                    "open, then try again.")
        elif code == _errno.ENOENT or typ == "FileNotFoundError":
            text = f"No file or folder named {name} was found."
        elif code == _errno.ENOSPC:
            text = "The disk is full. Free some space, then try again."
        else:
            text = f"{e.group(2).strip()} ({name})."
        return head + text + rest
    if file:
        kind = {".docx": "Word document", ".doc": "Word document", ".xlsx": "Excel workbook", ".xlsm": "Excel workbook",
                ".xls": "Excel workbook", ".pptx": "PowerPoint deck", ".ppt": "PowerPoint deck", ".pdf": "PDF"}.get(
            os.path.splitext(file)[1].lower(), "file")
        return (f"Could not read {file}: this {kind} is damaged, empty or not what its name says "
                f"({typ}{': ' + msg[:120] if msg else ''}). Open it in its app to check it." + rest)
    return s
