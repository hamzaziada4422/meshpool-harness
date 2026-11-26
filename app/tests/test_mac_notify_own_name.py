"""macOS notifications are posted in Meshpool.app's own name, never through osascript (which files them under another
app: users saw meshpool's replies arrive as TextEdit / Script Editor notifications, and clicking opened that app)."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.ui import desktop as D  # noqa: E402


def _fake_foundation(monkeypatch, bundle_id):
    sent = []

    class Note:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            self.t = self.b = None
            return self

        def setTitle_(self, t):
            self.t = t

        def setInformativeText_(self, b):
            self.b = b

    class Center:
        @staticmethod
        def defaultUserNotificationCenter():
            return Center()

        def deliverNotification_(self, n):
            sent.append((n.t, n.b))

    class Bundle:
        @staticmethod
        def mainBundle():
            return types.SimpleNamespace(bundleIdentifier=lambda: bundle_id)

    mod = types.ModuleType("Foundation")
    mod.NSBundle, mod.NSUserNotification, mod.NSUserNotificationCenter = Bundle, Note, Center
    monkeypatch.setitem(sys.modules, "Foundation", mod)
    return sent


def _no_subprocess(monkeypatch):
    ran = []
    monkeypatch.setattr(D.subprocess, "Popen", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: ran.append(a))
    return ran


def test_inside_meshpool_app_the_notification_is_meshpools_own(monkeypatch):
    sent = _fake_foundation(monkeypatch, "ai.meshpool.desktop")
    ran = _no_subprocess(monkeypatch)
    monkeypatch.setattr(D.sys, "platform", "darwin")
    D.notify("Done: Q3 report", "The report is saved as q3.docx.")
    assert sent == [("Done: Q3 report", "The report is saved as q3.docx.")]
    assert ran == []                                   # no osascript, no other program


def test_outside_meshpool_app_nothing_is_shown_in_another_apps_name(monkeypatch):
    sent = _fake_foundation(monkeypatch, "org.python.python")      # a source checkout or a helper process
    ran = _no_subprocess(monkeypatch)
    monkeypatch.setattr(D.sys, "platform", "darwin")
    D.notify("Done: task", "reply")
    assert sent == [] and ran == []
    monkeypatch.setitem(sys.modules, "Foundation", None)           # no PyObjC at all
    D.notify("Done: task", "reply")
    assert ran == []


def test_no_osascript_notification_is_left_in_the_app():
    src = Path(D.__file__).read_text()
    assert src.count("display notification") == 1          # only the docstring that says why it was removed
