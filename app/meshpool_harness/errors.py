"""One plain sentence for an exception, shared by the app's routes and the terminal (so they say the same thing)."""
from __future__ import annotations

import zipfile


def plain(e: BaseException) -> str:
    if isinstance(e, zipfile.BadZipFile):
        return "That file is not a valid zip file."
    if isinstance(e, (TypeError, AttributeError)):
        return f"The request has a missing or wrong value ({e})."
    return str(e).strip("'\"") or type(e).__name__
