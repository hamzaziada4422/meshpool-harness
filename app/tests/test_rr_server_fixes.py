"""Release review fixes (meshpool 0.0.18): sheet previews, folder projects, undo refs, project ticks, login reaping,
attach copies. No network, no model."""
import pytest

from meshpool_harness import project_store as PS
from meshpool_harness.ui import previews as P
from meshpool_harness.ui import workbench as W


@pytest.fixture
def proj(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(home))
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(W, "known_roots", lambda: [root])
    P._JOBS.clear()
    return root


def _cache_value(path, old, new):
    import zipfile
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i, data in items:
            if i.filename == "xl/worksheets/sheet1.xml":
                assert old.encode() in data
                data = data.replace(old.encode(), new.encode())
            z.writestr(i.filename, data)


# ---- item 11: text labels are not formulas
def test_text_labels_stay_text_and_formulas_keep_their_hover(proj):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Region", "Q1", "Q2", "Total"])
    ws.append(["North", 100, 20, "=B2+C2"])
    wb.save(proj / "m.xlsx")
    _cache_value(proj / "m.xlsx", "<f>B2+C2</f><v></v>", "<f>B2+C2</f><v>120</v>")      # as Excel saves it
    sheet = P.meta(str(proj), "m.xlsx")["sheets"][0]
    assert sheet["rows"][0] == ["Region", "Q1", "Q2", "Total"]
    assert sheet["rows"][1] == ["North", "100", "20", "120"]
    forms = sheet.get("formulas") or sheet.get("forms") or {}
    assert forms == {"1:3": "=B2+C2"}


# ---- item 12: number formats
@pytest.mark.parametrize("v,fmt,want", [
    (5, 'General" units"', "5 units"),
    (5, '"$"General', "$5"),
    (2139, "00000", "02139"),
    (42, "000", "042"),
    (1234, "#,##0", "1,234"),
    (7, "0.00", "7.00"),
])
def test_number_formats_with_general_and_leading_zeros(v, fmt, want):
    assert P._number(v, fmt) == want


# ---- item 13: an existing folder project is pinned
def test_for_folder_pins_an_existing_project(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    from meshpool_harness import config as C
    C.ensure_home()
    folder = tmp_path / "work"
    folder.mkdir()
    made = PS.create("work", link={"type": "folder", "path": str(folder)})
    assert not made.get("pinned")
    got = PS.for_folder(str(folder))
    assert got["id"] == made["id"] and got["pinned"] is True
    assert PS.detail(made["id"])["pinned"] is True


# ---- item 16: an owner-less sign-in record is reaped only when it is older than the timeout
import os  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from meshpool_harness import config as C  # noqa: E402
from meshpool_harness import fsio  # noqa: E402
from meshpool_harness import harnesses as H  # noqa: E402
from test_login_ws12 import alive, fake_npx, wait_dead  # noqa: E402,F401

posix = pytest.mark.skipif(os.name == "nt", reason="fake npx is a shell script")


@posix
def test_an_ownerless_login_is_reaped_only_when_older_than_the_timeout(fake_npx):  # noqa: F811
    left = subprocess.Popen([H.npx(), "-y", H.CODEX_CLI, "login"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    f = C.home() / "codex-login.json"
    try:
        time.sleep(0.3)
        fsio.atomic_write_json(f, {"pid": left.pid, "started": time.time()})            # no owner, just started
        assert H.reap_stale_login() is False and alive(left.pid) and f.exists()
        fsio.atomic_write_json(f, {"pid": left.pid, "started": time.time() - H.LOGIN_TIMEOUT_S - 5})
        assert H.reap_stale_login() is True
        assert wait_dead(left.pid) and not f.exists()
    finally:
        left.kill()
        left.wait()


# ---- items 14 and 17: attached copies, Undo, Rewind and take_back
import base64  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

from meshpool_harness import chats  # noqa: E402
from meshpool_harness.ui import agent_api as A  # noqa: E402
from test_om_bugs018 import _idle, home, proj as om_proj, script  # noqa: E402,F401

DROPPED = b"date,payee,amount\n2026-02-01,blue cafe,13\n"


def _refs(user_text):
    m = re.search(r"Attached files \(in [^)]*\): (.*)$", user_text)
    return [x.strip() for x in m.group(1).split(", ")] if m else []


def _chat_with_a_copy(home, om_proj, monkeypatch):
    script(monkeypatch, "Done.", "Done again.")
    (om_proj / "data.csv").write_text("date,payee,amount\n2026-01-01,rent co,1200\n")
    v = chats.create({"workdir": str(om_proj), "no_mcp": True})
    s = chats.get(v["id"])
    s.send("look at this", files=[{"name": "data.csv", "data": base64.b64encode(DROPPED).decode()}])
    _idle(s)
    assert (om_proj / "data (2).csv").read_bytes() == DROPPED
    return s


def test_item14_undo_names_the_deleted_copy_as_the_message_names_it(home, om_proj, monkeypatch):
    """The page drops the chip for any ref in undone.deleted. So the paths must match what the message says."""
    s = _chat_with_a_copy(home, om_proj, monkeypatch)
    sent = [e for e in s.events if e.get("type") == "user"][0]["text"]
    assert _refs(sent) == ["data (2).csv"]
    out = A.session_action(s.id, "revert", {"turn": s.cp.turns()[-1]["n"]})
    assert out["undone"]["deleted"] == _refs(sent)
    assert not (om_proj / "data (2).csv").exists()


def test_item14_rewind_with_files_names_the_deleted_copy_as_the_message_names_it(home, om_proj, monkeypatch):
    s = _chat_with_a_copy(home, om_proj, monkeypatch)
    ei = [e for e in s.events if e.get("type") == "user"][0]["i"]
    r = s.rewind(ei, files=True)
    assert r["undo"]["deleted"] == ["data (2).csv"] and "data (2).csv" in r["text"]
    assert not (om_proj / "data (2).csv").exists()


# ---- item 15: a Code session listed under a project by its folder is not ticked as linked
class _Fake:
    def __init__(self, workdir=None, kind="code", **kw):
        import uuid
        self.id = uuid.uuid4().hex[:8]
        (A.SESSIONS / self.id).mkdir()
        self.workdir, self.kind, self.state, self.harness = workdir, kind, "idle", {"id": "meshpool-work"}
        self.agent = type("G", (), {"title": "", "deleted": False})()
        self.events = []

    def view(self, since):
        return {"id": self.id, "workdir": self.workdir}

    def emit(self, ev):
        self.events.append(ev)

    def close(self):
        pass


def test_item15_a_session_listed_under_a_project_by_its_folder_is_not_linked_until_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(A, "Session", _Fake)
    C.ensure_home()
    site = tmp_path / "Data"
    site.mkdir()
    pid = PS.create("Data", link={"type": "folder", "path": str(site)})["id"]
    v = A.create({"workdir": str(site.resolve()), "kind": "code", "task": "x"})
    (A.SESSIONS / v["id"] / "session.json").write_text(json.dumps({"id": v["id"], "title": "T", "updated": 1,
                                                                  "workdir": v["workdir"]}))
    A.list_dirty()
    row = {r["id"]: r for r in A.listing()}[v["id"]]
    assert row["project_id"] == pid and row["project_linked"] is False      # listed there, not stored there
    A.thread_action(v["id"], {"action": "move", "project_id": pid})          # what a click on the ticked item now does
    A.list_dirty()
    row = {r["id"]: r for r in A.listing()}[v["id"]]
    assert row["project_id"] == pid and row["project_linked"] is True
    assert A.read_meta(v["id"])["project_id"] == pid


# ---- item 17: take_back clears the copies made for the message it takes back
def test_item17_a_later_turns_undo_does_not_remove_a_taken_back_messages_copy(home, om_proj, monkeypatch):
    script(monkeypatch, "Hello.", "Hello again.")
    v = chats.create({"workdir": str(om_proj), "no_mcp": True})
    s = chats.get(v["id"])
    s._save_files("check this", [{"name": "late.csv", "data": base64.b64encode(DROPPED).decode()}])
    assert (om_proj / "late.csv").exists()
    s.take_back()                                                             # the message never started a turn
    assert "_attach_made" not in s.__dict__
    s.send("say hello")
    _idle(s)
    r = s.revert(s.cp.turns()[-1]["n"])
    assert (om_proj / "late.csv").exists() and "late.csv" not in r["deleted"]
