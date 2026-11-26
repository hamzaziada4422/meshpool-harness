"""Office work an outside engine (Codex, other ACP agents) does not do well on its own, done or guarded by meshpool.

Sara, round 3:
- "Make a PDF of Board_Update_Q3.pptx" took 9 steps on Codex and ended on a raw approval card. Codex's sandbox forbids
  the local socket LibreOffice needs (bind on AF_UNIX fails with EPERM), so `soffice --convert-to pdf` exits 1 there
  even on a plain text file. A plain conversion request now runs on meshpool's own converter, in one step, with a card
  that says "Convert Board_Update_Q3.pptx to PDF" (conversion_request, convert).
- A workbook Excel held open (its ~$ lock file next to it) was saved over with no question (open_files, guard_text).
- A reply pointed to a backup in a temporary folder outside the project, and its link opened "That folder is not one
  of your projects." (dead_paths, unlink_dead_paths).
- An approval card showed "cd /tmp/lout && HOME=... soffice -env:UserInstallation=..." and a JSON blob as its body
  (plain_command_title)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- "make a PDF of X"
_DOC = r"[\w][\w .()&'+,-]*?\.(?:pptx?|pptm|ppsx|docx?|docm|odt|odp|ods|rtf|xlsx?|xlsm)"
_DOC_RX = re.compile(r"(?<![\w~$.])(" + _DOC + r")(?![\w])", re.I)
_TO_PDF = re.compile(
    r"\b(?:make|create|export|save|convert|turn|produce|generate|render|print|give me|get me|i need|send me|want)\b"
    r"[^.?!\n]{0,80}?\bpdf\b|\bpdf (?:version|copy|file|export) of\b|\b(?:to|into|as) (?:a |an )?pdf\b", re.I)
# a message that asks for more than the conversion: the engine does that work, not this shortcut
_MORE = re.compile(r"\b(?:fix|change|update|edit|add|remove|delete|rewrite|shorten|translate|correct|replace|insert|"
                   r"format|summari[sz]e|redesign|restyle|rename|merge|combine|split|compress|password|sign|"
                   r"and then|then|after that|also|first)\b", re.I)


def _names(text: str, base: Path) -> list[str]:
    """The office file names a message mentions. A name may hold spaces ("Q3 Board Update.pptx"), so of the words
    before an extension the longest run that names a file in the folder wins, else the last word."""
    out = []
    for m in _DOC_RX.finditer(text):
        words = m.group(1).strip().split(" ")
        tails = [" ".join(words[i:]).strip(" ,'(") for i in range(len(words))]
        out.append(next((x for x in tails if x and (base / x).is_file()), tails[-1]))
    return list(dict.fromkeys(out))


def conversion_request(text: str, root) -> tuple[Path, str] | None:
    """(the file, "pdf") when the message only asks for a PDF of one office file that is in the folder, else None.
    "Make a PDF of Board_Update_Q3.pptx that I can email to the board." is one; "fix the typo and make a PDF" is not."""
    t = str(text or "").split("\n\n​")[0].strip()
    if not t or len(t) > 400 or not _TO_PDF.search(t) or _MORE.search(t):
        return None
    base = Path(str(root)).resolve()
    names = _names(t, base)
    if len(names) != 1:
        return None
    p = (base / names[0]).resolve()
    if base not in p.parents or not p.is_file():
        return None
    return p, "pdf"


def convert_title(src: Path, fmt: str = "pdf") -> str:
    return f"Convert {Path(src).name} to {fmt.upper()}"


def _soffice() -> str | None:
    try:
        from meshpool_harness import office_runtime
        return office_runtime.find_soffice()
    except Exception:  # noqa: BLE001
        return None


def can_convert() -> bool:
    return bool(_soffice())


def convert(src: Path, fmt: str = "pdf", timeout: float = 240) -> tuple[Path | None, str]:
    """(the file made next to src, "") or (None, a plain reason). Runs meshpool's own LibreOffice outside any command
    sandbox, with a throwaway profile, so it works on the first try. An older file of the same name is replaced:
    Undo keeps it."""
    exe = _soffice()
    if not exe:
        return None, "LibreOffice is not available here"
    src = Path(src)
    with tempfile.TemporaryDirectory(prefix="mh_conv_") as tmp, tempfile.TemporaryDirectory(prefix="mh_lo_") as prof:
        made = None
        try:
            from meshpool_harness import lo_session
            made = lo_session.convert(exe, str(src), fmt, tmp, timeout)
        except Exception:  # noqa: BLE001 - the kept LibreOffice is an optimisation: a one-off run below
            made = None
        if made is None or not Path(made).is_file():
            kw = {"start_new_session": True} if os.name != "nt" else {}
            try:
                r = subprocess.run([exe, f"-env:UserInstallation={Path(prof).as_uri()}", "--headless", "--norestore",
                                    "--convert-to", fmt, "--outdir", tmp, str(src)], capture_output=True, text=True,
                                   timeout=timeout, **kw)
            except subprocess.TimeoutExpired:
                return None, f"converting {src.name} took longer than {int(timeout)} seconds"
            except OSError as e:
                return None, f"LibreOffice could not start ({e})"
            made = Path(tmp) / (src.stem + "." + fmt)
            if r.returncode != 0 or not made.is_file():
                return None, f"LibreOffice could not convert {src.name}"
        dst = src.with_suffix("." + fmt)
        shutil.move(str(made), str(dst))
    return dst, ""


def page_count(pdf: Path) -> int:
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf))
        try:
            return len(doc)
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------- a file open in Office
_EDIT = re.compile(r"\b(?:fix|change|update|edit|add|insert|remove|delete|rewrite|shorten|correct|replace|rename|"
                   r"set|put|move|sort|format|extend|recalculate|fill|write|save|make|include|merge)\b", re.I)
_CLOSED = re.compile(r"\b(?:i(?:'ve| have)? closed|closed (?:it|excel|word|powerpoint|the file))\b|"
                     r"\b(?:remove|delete)\b[^.?!\n]{0,40}\block(?: file)?\b", re.I)


def _lock_of(p: Path) -> tuple[str, str]:
    """(app, lock file name) when another app has p open, else ("", "")."""
    lo = p.with_name(f".~lock.{p.name}#")
    if lo.exists():
        return "LibreOffice", lo.name
    for n in (p.name, p.name[1:], p.name[2:]):
        if len(n) > len(p.suffix) and p.with_name("~$" + n).exists():
            ext = p.suffix.lower()
            app = "Excel" if ext.startswith((".xls", ".csv")) else "PowerPoint" if ext.startswith(".pp") else "Word"
            return app, "~$" + n
    return "", ""


def open_files(text: str, root) -> list[tuple[str, str, str]]:
    """[(file name, app, lock file name)] for the office files the message asks to change that are open in Office
    right now. A message that only reads, or says the file was closed, names none."""
    t = str(text or "").split("\n\n​")[0]
    if not _EDIT.search(t) or _CLOSED.search(t):
        return []
    base = Path(str(root)).resolve()
    out = []
    for name in _names(t, base):
        p = (base / name).resolve()
        if base not in p.parents or not p.is_file():
            continue
        app, lock = _lock_of(p)
        if app:
            out.append((p.name, app, lock))
    return out


def guard_card(name: str, app: str, lock: str) -> tuple[str, str]:
    """(title, body) of the card asked before an outside engine may change a file open in Office. The body's plain
    lines come first; the lock file sits in the Details fold."""
    title = f"{name} is open in {app}. Change it anyway?"
    body = (f"{app} has it open right now, maybe on your computer.\n"
            f"If {app} saves its copy later, the change made here is lost.\n\nDetails:\nLock file: {lock}")
    return title, body


def keep_note(names: list[str]) -> str:
    """The hidden note an engine gets when the user said not to change an open file."""
    files = ", ".join(names)
    return (f"\n\n​{files} {'is' if len(names) == 1 else 'are'} open in Office on the user's computer and the user "
            "chose not to change it now. Do not change, replace or delete it in this turn; reading it is fine. If the "
            "request needs a change to it, say so in one sentence and stop.")


# ---------------------------------------------------------------- plain approval titles for commands
_SOFFICE = re.compile(r"(?:^|[\s;&|(/])(?:soffice|libreoffice|soffice\.bin)\b[^;&|]*?--convert-to\s+([\w:]+)"
                      r"[^;&|]*?(?:^|\s)['\"]?([^\s'\";&|]+\.\w{2,5})['\"]?(?=\s*(?:$|[;&|)]|2>|>|--))", re.I)


_HELPERS = {"cd", "mkdir", "ls", "echo", "cp", "timeout", "soffice", "libreoffice", "soffice.bin", "pdfinfo", "export",
            "true", "env"}


def plain_command_title(cmd: str) -> str:
    """A command's approval card title in plain words, for the kinds people meet in office work, else "". The raw
    command goes in the card's Details fold."""
    c = str(cmd or "")
    m = _SOFFICE.search(c)
    # never milder than the command: only when every other step is a harmless helper (cd, mkdir, ls, echo, a copy
    # of the input, pdfinfo); "soffice ...; rm -rf x" keeps the ordinary command card
    for step in re.split(r"&&|\|\||[;|\n]", c):
        words = [w for w in step.split() if not re.match(r"[A-Za-z_]\w*=", w)]
        if words and os.path.basename(words[0]) not in _HELPERS and not re.match(r"\d*>|2>&1", words[0]):
            return ""
        if words and words[0] == "cp" and not words[-1].startswith(("/tmp/", "$TMPDIR", "${TMPDIR")):
            return ""                                  # a copy over a file of the user's is not a conversion
        if re.search(r"(?<![2&])>{1,2}\s*(?!&1|/dev/null|/tmp/)\S", step):
            return ""                                  # a redirect that writes somewhere
    if m:
        fmt = m.group(1).split(":")[0]
        name = os.path.basename(m.group(2))
        cp = re.search(r"\bcp\s+(?:-\w+\s+)*['\"]?([^\s'\"]+)['\"]?\s+['\"]?([^\s'\";&|]+)", c)
        if cp and os.path.basename(cp.group(2)) == name:
            name = os.path.basename(cp.group(1))      # a scratch copy of the user's file: name the user's file
        return f"Convert {name} to {fmt.upper()}"
    return ""


# ---------------------------------------------------------------- links to files nobody can open here
_TEMP = re.compile(r"(?:/private)?/(?:tmp|var/tmp|var/folders)/")      # the page's TEMP_PATH_RX (app.js)
_ABS = re.compile(r"(`?)((?:/[^\s`'\"<>()\[\]]+)+/([^\s`'\"<>()\[\]/]+\.\w{1,6}))\1(?![\w/])")


def dead_paths(text: str, workdir: str, can_open=None) -> list[str]:
    """Absolute file paths in a reply that the app cannot open: outside the chat's folder, in a folder that is not
    one of the user's projects (a model's own scratch folder, say)."""
    root = Path(str(workdir or "")).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    out = []
    for m in _ABS.finditer(str(text or "")):
        path = m.group(2)
        if not _TEMP.match(path):
            continue                       # the page links only paths in the chat's folder and temporary folders
        try:
            p = Path(path).resolve()
        except (OSError, ValueError):
            continue
        if root and (p == root or root in p.parents):
            continue
        ok = False
        if can_open is not None:
            try:
                ok = bool(can_open(str(p.parent)))
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            out.append(path)
    return list(dict.fromkeys(out))


def unlink_dead_paths(text: str, workdir: str, can_open=None) -> str:
    """The reply with each dead path (dead_paths) said as a plain name that is not a link, so no button leads to
    "That folder is not one of your projects"."""
    dead = set(dead_paths(text, workdir, can_open))
    if not dead:
        return text

    def sub(m):
        if m.group(2) not in dead:
            return m.group(0)
        return f"{m.group(3)} (a temporary copy outside this project, which cannot be opened here; Undo takes a change back)"
    return _ABS.sub(sub, text)
