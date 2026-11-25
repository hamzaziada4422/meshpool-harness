"""H-28: on Windows every process Meshpool starts gets CREATE_NO_WINDOW, so no console window flashes.

subprocess.run / check_output / call all go through Popen, so one wrapper on Popen.__init__ covers every spawn site
(ours and our libraries') instead of 90 call sites each remembering the flag. A site that asks for its own console
(CREATE_NEW_CONSOLE) or a detached process keeps what it asked for.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010
_DETACHED_PROCESS = 0x00000008


def install(popen=None, name: str | None = None) -> bool:
    """Wrap `popen.__init__` (default subprocess.Popen) once. Returns True when installed (only on Windows)."""
    if (name or os.name) != "nt":
        return False
    popen = popen or subprocess.Popen
    if getattr(popen.__init__, "_mh_nowindow", False):
        return True
    orig = popen.__init__

    def init(self, *a, **kw):
        fl = kw.get("creationflags", 0) or 0
        if not fl & (_CREATE_NEW_CONSOLE | _DETACHED_PROCESS):
            kw["creationflags"] = fl | CREATE_NO_WINDOW
        orig(self, *a, **kw)

    init._mh_nowindow = True
    popen.__init__ = init
    return True
