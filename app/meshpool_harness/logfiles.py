"""Log files that rotate by size while the app runs (H-23): the app log, each MCP server's log and Pi's log.

RotatingFile is a small text file object backed by logging.handlers.RotatingFileHandler (path, path.1, path.2, ...).
PipedLog gives a child process a pipe for its stderr and copies what arrives into a RotatingFile, because a child that
writes straight into a file keeps writing to the old, renamed file after a rotation."""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
from pathlib import Path

MAX_BYTES = 5_000_000
BACKUPS = 3


class RotatingFile:
    encoding = "utf-8"

    def __init__(self, path, max_bytes: int = MAX_BYTES, backups: int = BACKUPS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._h = logging.handlers.RotatingFileHandler(self.path, maxBytes=max_bytes, backupCount=backups,
                                                       encoding="utf-8", errors="replace")
        self._h.terminator = ""
        self._lock = threading.Lock()
        self.closed = False

    def write(self, s) -> int:
        if not s or self.closed:
            return 0
        if isinstance(s, bytes):
            s = s.decode("utf-8", "replace")
        rec = logging.LogRecord("meshpool", logging.INFO, "", 0, s, None, None)
        with self._lock:
            try:
                self._h.emit(rec)
            except Exception:  # noqa: BLE001 - a full disk must not crash the app
                return 0
        return len(s)

    def flush(self) -> None:
        with self._lock:
            try:
                self._h.flush()
            except Exception:  # noqa: BLE001
                pass

    def tell(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def isatty(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True
        with self._lock:
            try:
                self._h.close()
            except Exception:  # noqa: BLE001
                pass


class PipedLog:
    """stderr=PipedLog(path) for subprocess.Popen: call release() once the child has started, close() when it is done."""

    def __init__(self, path, max_bytes: int = MAX_BYTES, backups: int = BACKUPS):
        self.sink = RotatingFile(path, max_bytes, backups)
        self.path = self.sink.path
        self._r, self._w = os.pipe()
        self._wopen = True
        self._t = threading.Thread(target=self._pump, daemon=True, name="log-pump")
        self._t.start()

    def fileno(self) -> int:
        return self._w

    def _pump(self) -> None:
        try:
            with os.fdopen(self._r, "rb", closefd=True) as f:
                while True:
                    chunk = f.read1(65536) if hasattr(f, "read1") else f.read(65536)
                    if not chunk:
                        break
                    self.sink.write(chunk.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass

    def release(self) -> None:
        """Close this process's copy of the write end: the pump ends when the child (and what it started) exits."""
        if self._wopen:
            self._wopen = False
            try:
                os.close(self._w)
            except OSError:
                pass

    def tell(self) -> int:
        return self.sink.tell()

    def flush(self) -> None:
        self._t.join(0.3)                 # whatever the child printed before it exited has been copied
        self.sink.flush()

    def close(self) -> None:
        self.release()
        self._t.join(1.0)
        self.sink.close()
