"""Desktop notifications are posted in Meshpool's own name.
macOS: from Meshpool.app, never through osascript (which files them under another app: users saw meshpool's replies
arrive as TextEdit / Script Editor notifications, and clicking opened that app).
Windows: with Meshpool's registered AppUserModelID, not PowerShell's (they showed as "Windows PowerShell")."""
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


# ---------- Windows: toasts in Meshpool's own AppUserModelID, not PowerShell's ----------------------------------------
def _fake_winreg(monkeypatch, fail=False):
    keys = {}

    class Key(dict):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def create(root, path):
        if fail:
            raise PermissionError("denied")
        return keys.setdefault(path, Key())

    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER, mod.REG_SZ = "HKCU", 1
    mod.CreateKey = create
    mod.SetValueEx = lambda k, name, _r, _t, value: k.__setitem__(name, value)
    monkeypatch.setitem(sys.modules, "winreg", mod)
    return keys


def _windows(monkeypatch):
    ran = []
    monkeypatch.setattr(D.sys, "platform", "win32")
    monkeypatch.setattr(D.os, "name", "nt")
    monkeypatch.setattr(D.subprocess, "Popen", lambda args, **k: ran.append((args, k.get("env", {}))))
    return ran


def test_windows_toast_is_sent_as_meshpool_and_the_id_is_registered(monkeypatch):
    keys = _fake_winreg(monkeypatch)
    ran = _windows(monkeypatch)
    D.notify("Done: Q3 report", "Saved q3.docx")
    assert keys[r"Software\Classes\AppUserModelId\Meshpool.Desktop"]["DisplayName"] == "Meshpool"
    (args, env), = ran
    assert env["MP_ID"] == "Meshpool.Desktop" and env["MP_T"] == "Done: Q3 report" and env["MP_B"] == "Saved q3.docx"
    assert "CreateToastNotifier($env:MP_ID)" in args[-1] and "WindowsPowerShell" not in args[-1]


def test_windows_falls_back_to_powershells_id_when_the_id_cannot_be_registered(monkeypatch):
    _fake_winreg(monkeypatch, fail=True)              # an unregistered ID makes Windows drop the toast silently
    ran = _windows(monkeypatch)
    D.notify("Done: task", "reply")
    (args, env), = ran
    assert env["MP_ID"].endswith(r"\WindowsPowerShell\v1.0\powershell.exe")


def test_installer_registers_and_removes_the_same_id():
    nsi = (Path(__file__).resolve().parents[2] / "packaging" / "nsis" / "installer.nsi").read_text()
    key = D.WIN_AUMID_KEY
    assert f'WriteRegStr HKCU "{key}" "DisplayName" "Meshpool"' in nsi
    assert f'DeleteRegKey HKCU "{key}"' in nsi


def test_the_signed_uninstaller_removes_the_id_too():
    # release builds with signing ship the uninstaller built from uninstaller.nsi, not installer.nsi's section
    nsi = (Path(__file__).resolve().parents[2] / "packaging" / "nsis" / "uninstaller.nsi").read_text()
    assert f'DeleteRegKey HKCU "{D.WIN_AUMID_KEY}"' in nsi
