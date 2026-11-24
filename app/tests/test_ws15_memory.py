"""Audit ws15 (memory, projects, skills, import): M17-01..03, M19-01..04, M20-01, M20-03..07, M21-01..04, F-07, F-17,
F-32, F-33."""
import json
import multiprocessing as mp
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import chat_import as CI
from meshpool_harness import config as C
from meshpool_harness import importer as IM
from meshpool_harness import learn as LRN
from meshpool_harness import memstore as MS
from meshpool_harness import project_store as PS
from meshpool_harness import skills as SK
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MH_RUNS_DIR", str(tmp_path / "home" / "runs"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "home" / "runs" / "sessions")
    A.SESSIONS.mkdir(parents=True)
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    monkeypatch.setattr(SK, "_DEFAULTS_SEEN", set(), raising=False)
    monkeypatch.setattr(MS, "_ENSURED", set())
    monkeypatch.setattr(MS, "_CACHE", {})
    C.ensure_home()
    return tmp_path


# ---- M17-01, M17-02, M17-03: skills ------------------------------------------------------------------------------------------
def _skill(name):
    d = SK.folder() / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\nbody\n")


def test_M17_01_toggles_do_not_lose_each_other(home, monkeypatch):
    names = [f"s{i}" for i in range(8)]
    for n in names:
        _skill(n)
    real = Path.read_text

    def slow(self, *a, **k):                         # a reader that is slow between its read and its write
        v = real(self, *a, **k)
        if self.name == ".disabled.json":
            time.sleep(0.05)
        return v
    monkeypatch.setattr(Path, "read_text", slow)
    ts = [threading.Thread(target=SK.toggle, args=(n, False)) for n in names]
    [t.start() for t in ts]
    [t.join() for t in ts]
    monkeypatch.setattr(Path, "read_text", real)
    assert SK._disabled() == set(names)
    assert not list(SK.folder().glob(".disabled.json.*.tmp"))


def test_M17_01_damaged_disabled_list_is_set_aside(home):
    _skill("a")
    SK.folder().mkdir(parents=True, exist_ok=True)
    (SK.folder() / ".disabled.json").write_text("{not json")
    assert SK._disabled() == set()
    SK.toggle("a", False)
    assert SK._disabled() == {"a"} and list(SK.folder().glob(".disabled.json.corrupt-*"))


def test_M17_02_removing_a_skill_prunes_the_disabled_list(home):
    _skill("gone")
    _skill("kept")
    SK.toggle("gone", False)
    SK.toggle("kept", False)
    SK.remove("gone")
    assert SK._disabled() == {"kept"}
    _skill("gone")                                    # a new skill with the same name starts enabled
    assert next(s for s in SK.load_all() if s["id"] == "gone")["enabled"] is True


def test_M17_03_default_install_is_whole_or_absent(home, monkeypatch):
    shutil.rmtree(SK.folder(), ignore_errors=True)
    real = shutil.copyfile
    n = []

    def dying_copy(src, dst, *a, **k):
        n.append(1)
        if len(n) == 2:                               # the disk goes away in the middle of the install
            raise OSError("disk went away")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(shutil, "copyfile", dying_copy)
    with pytest.raises(OSError):
        SK.ensure_defaults()
    monkeypatch.setattr(shutil, "copyfile", real)
    SK._DEFAULTS_SEEN.clear()
    SK.ensure_defaults()
    shipped = [d.name for d in (C.APP / "defaults" / "skills").iterdir() if d.is_dir()]
    assert shipped
    for n in shipped:
        src = C.APP / "defaults" / "skills" / n
        want = sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file())
        have = sorted(str(p.relative_to(SK.folder() / n)) for p in (SK.folder() / n).rglob("*") if p.is_file())
        assert have == want, f"{n} is missing or half installed"
    assert not [p for p in SK.folder().iterdir() if p.name.startswith(".") and p.is_dir()]
    marker = json.loads((SK.folder() / ".defaults-installed").read_text())
    assert set(shipped) <= set(marker)


def test_M17_03_reading_the_list_does_not_reinstall(home, monkeypatch):
    SK.load_all()
    n = []
    real = SK.ensure_defaults.__globals__["fsio"].file_lock
    monkeypatch.setattr(SK.fsio, "file_lock", lambda *a, **k: (n.append(1), real(*a, **k))[1])
    for _ in range(5):
        SK.load_all()
    assert n == []                                    # settled once; reading never takes the install lock again


# ---- M19-01: a Settings save changes only what the person changed -----------------------------------------------------------------
def test_M19_01_saving_memory_text_keeps_facts_added_meanwhile(home):
    MS.add("I work in finance", scope="everywhere")
    MS.add("I like tables", scope="everywhere")
    base = AS.memory()                                # the page loads this
    MS.add("My manager is Sam", scope="everywhere")   # an agent saves a fact while the page is open
    edited = base.replace("- I like tables", "- I like bullet points") + "\n- I live in Dubai"
    AS.set_memory(edited, base=base)
    now = AS.memory()
    assert "My manager is Sam" in now and "I like bullet points" in now and "I live in Dubai" in now
    assert "I like tables" not in now and "I work in finance" in now
    assert any(f["text"] == "I like tables" and f["status"] == "archived" for f in MS.load())


def test_M19_01_instructions_unchanged_or_changed_elsewhere(home):
    AS.set_instructions("Be brief.")
    AS.set_instructions("Be brief.", base="Be brief.")
    AS.set_instructions("Different elsewhere.")                            # another window
    AS.set_instructions("Be brief.", base="Be brief.")                     # this page changed nothing: writes nothing
    assert AS.instructions() == "Different elsewhere."
    with pytest.raises(ValueError, match="changed somewhere else"):
        AS.set_instructions("Mine.", base="Be brief.")
    assert AS.instructions() == "Different elsewhere."
    AS.set_instructions("Mine.", base="Different elsewhere.")
    assert AS.instructions() == "Mine."


def test_M19_01_settings_saves_do_not_overwrite_each_other(home, monkeypatch):
    real = AS._read

    def slow(p):
        v = real(p)
        if p.name == "settings.json":
            time.sleep(0.05)
        return v
    monkeypatch.setattr(AS, "_read", slow)
    keys = ["memory_on", "instructions_on", "learn_from_edits", "insights_on"]
    ts = [threading.Thread(target=AS.save_settings, kwargs={k: False}) for k in keys]
    [t.start() for t in ts]
    [t.join() for t in ts]
    monkeypatch.setattr(AS, "_read", real)
    assert all(AS.settings()[k] is False for k in keys)


# ---- M19-02, M19-04: learned preferences ----------------------------------------------------------------------------------------------
class _Client:
    def __init__(self, prefs):
        self.prefs = prefs

    def chat(self, **kw):
        return {"choices": [{"message": {"content": json.dumps({"preferences": self.prefs})}}]}


def _learn(home, monkeypatch, sid="c1", prefs=("Prefers short answers always",)):
    monkeypatch.setenv("MH_LEARN", "1")
    (A.SESSIONS / sid).mkdir(exist_ok=True)
    (A.SESSIONS / sid / "meta.json").write_text("{}")
    LRN._PENDING.clear()
    return LRN.after_turn(sid, "from now on always keep it short please", "ok", client=_Client(list(prefs)), model="m")


def test_M19_02_deleted_preference_is_not_injected(home, monkeypatch):
    assert _learn(home, monkeypatch)
    fid = next(f["id"] for f in MS.load() if f["text"].startswith("Prefers short"))
    MS.delete(fid)
    assert LRN.peek("c1") == [] and LRN.pending("c1") == ""


def test_M19_02_memory_off_injects_nothing(home, monkeypatch):
    assert _learn(home, monkeypatch)
    AS.save_settings(memory_on=False)
    assert LRN.peek("c1") == [] and LRN.pending("c1") == ""
    AS.save_settings(memory_on=True)
    assert LRN.peek("c1") == []                                       # dropped when memory was off, not held back


def test_M19_04_pending_learning_survives_a_restart(home, monkeypatch):
    assert _learn(home, monkeypatch)
    assert A.read_meta("c1")["learn_pending"] == ["Prefers short answers always"]
    LRN._PENDING.clear()                                              # the app restarted, or the chat was reaped
    assert LRN.peek("c1") == ["Prefers short answers always"]
    LRN.commit("c1", ["Prefers short answers always"])
    LRN._PENDING.clear()
    assert LRN.peek("c1") == [] and A.read_meta("c1")["learn_pending"] == []


# ---- M19-03: not reproducible - the read-side migration already holds the lock --------------------------------------------------------
def _reader(home, out):
    os.environ["MESHPOOL_HOME"] = home
    from meshpool_harness import assistant as A2
    out.put(A2.memory())


def test_M19_03_concurrent_first_reads_agree(home):
    d = Path(os.environ["MESHPOOL_HOME"]) / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.md").write_text("\n".join(f"- fact number {i} about work" for i in range(6)) + "\n")
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    ps = [ctx.Process(target=_reader, args=(os.environ["MESHPOOL_HOME"], out)) for _ in range(4)]
    [p.start() for p in ps]
    got = [out.get(timeout=90) for _ in ps]
    [p.join(30) for p in ps]
    assert all(g == got[0] for g in got) and len(got[0].splitlines()) == 6
    assert len([f for f in MS.load() if f["scope"] == "everywhere"]) == 6


# ---- M20-01, M20-03, M20-04, M20-05, M20-06, M20-07 ----------------------------------------------------------------------------------------
class FakeSession:
    made: list = []

    def __init__(self, workdir=None, resume_id=None, mode=None, harness=None, kind="code", model=None, plan=False, **_kw):
        self.id = f"fs{len(FakeSession.made)}"
        (A.SESSIONS / self.id).mkdir(exist_ok=True)
        self.workdir, self.kind, self.state, self.harness = workdir, kind, "idle", {"id": harness or "meshpool-work"}
        self.agent = type("G", (), {"title": "", "deleted": False})()
        self.events = []
        FakeSession.made.append(self)

    def view(self, since):
        return {"id": self.id, "workdir": self.workdir}

    def emit(self, ev):
        self.events.append(ev)

    def close(self):
        pass


def test_M20_01_moving_a_chat_rebinds_its_folder(home, tmp_path, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    a = PS.create("A")
    b = PS.create("B")
    PS.knowledge_add(a["id"], "a.md", b"alpha notes")
    PS.knowledge_add(b["id"], "b.md", b"beta notes")
    v = A.create({"project_id": a["id"], "task": "x"})
    wd = Path(v["workdir"])
    assert PS.for_root(str(wd)) == a["id"] and (wd / "knowledge" / "a.md").exists()
    monkeypatch.setattr(A, "_saved", lambda sid: {"workdir": str(wd)})
    A.thread_action(v["id"], {"action": "move", "project_id": b["id"]})
    assert PS.for_root(str(wd)) == b["id"]
    assert not (wd / "knowledge" / "a.md").exists() and (wd / "knowledge" / "b.md").exists()
    A.thread_action(v["id"], {"action": "move", "project_id": ""})
    assert PS.for_root(str(wd)) == "" and not (wd / "knowledge" / "b.md").exists()
    assert A.read_meta(v["id"])["project_unbound"] is True


def test_M20_01_a_chat_moved_out_of_a_linked_project_stops_getting_it(home, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    p = PS.create("Site", instructions="SITE-RULE", link={"type": "folder", "path": str(site)})
    assert "SITE-RULE" in AS.context_block("hi", str(site), "", record=False)
    assert "SITE-RULE" not in AS.context_block("hi", str(site), "", record=False, lookup=False)
    assert p["id"]


def test_M20_04_failed_create_leaves_no_private_folder(home, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)

    def boom(*a, **k):
        raise ValueError("the worktree could not be made")
    monkeypatch.setattr(A, "_chat_worktree", boom)
    PS.chats_root().mkdir(parents=True, exist_ok=True)
    before = set(PS.chats_root().iterdir())
    with pytest.raises(ValueError):
        A.create({"task": "hello"})
    assert set(PS.chats_root().iterdir()) == before


def _knowledge_worker(home, pid, prefix, n):
    os.environ["MESHPOOL_HOME"] = home
    from meshpool_harness import project_store as PS2
    for i in range(n):
        PS2.knowledge_add(pid, f"{prefix}{i}.md", f"text {prefix}{i}".encode())


def test_M20_05_project_store_is_locked_across_processes(home):
    pid = PS.create("P")["id"]
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_knowledge_worker, args=(os.environ["MESHPOOL_HOME"], pid, f"w{k}_", 6)) for k in range(4)]
    [p.start() for p in ps]
    [p.join(120) for p in ps]
    assert len(PS.knowledge_list(pid)) == 24
    assert json.loads((PS.root() / pid / "project.json").read_text())["id"] == pid


def test_M20_05_damaged_project_file_is_not_replaced(home):
    pid = PS.create("P")["id"]
    f = PS.root() / pid / "project.json"
    f.write_text("{broken")
    with pytest.raises(ValueError, match="damaged"):
        PS.update(pid, description="new")
    assert f.read_text() == "{broken"


def test_M20_07_plain_folder_chats_do_not_become_projects(home, tmp_path, monkeypatch):
    plain, listed = tmp_path / "plain", tmp_path / "listed"
    plain.mkdir()
    (listed / "sub").mkdir(parents=True)
    monkeypatch.setattr(W, "projects", lambda: [{"path": str(listed), "name": "Listed"}])
    made = PS.migrate([str(plain), str(listed / "sub")])
    assert [Path(p["link"]["path"]).name for p in made] == ["listed"]


# the session tests: a real Session with the model loop replaced
class _Agent:
    pass


@pytest.fixture
def real_session(home, tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    from meshpool_harness.agent.core import Agent
    monkeypatch.setattr(core, "SESSIONS", A.SESSIONS)

    class FA(Agent):
        sent: list = []

        def send(self, text, images=None):
            self._stop = False
            FA.sent.append(text)
            self.messages.append({"role": "user", "content": text})
            self.messages.append({"role": "assistant", "content": "ok"})
            self.emit({"type": "assistant", "text": "ok"})
            self.save()
            self.emit({"type": "turn_end"})
            return "ok"
    FA.sent = []
    monkeypatch.setattr(A, "Agent", FA)
    monkeypatch.setenv("MH_CHECKS", "0")
    proj = tmp_path / "proj"
    proj.mkdir()
    s = A.Session(workdir=str(proj), harness="meshpool-code", kind="code")
    s._mcp_ready.set()
    return s, FA


def _wait(s, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if s.state == "idle":
            return
        time.sleep(0.05)
    pytest.fail("the turn did not finish")


def test_M20_06_first_message_context_is_kept_in_the_chat(real_session):
    s, FA = real_session
    assert A.read_meta(s.id)["ctx_pending"] is True
    s.send("hello")
    time.sleep(0.2)
    _wait(s)
    assert "Today is" in FA.sent[0] and A.read_meta(s.id)["ctx_pending"] is False and s.ctx_pending is False


def test_M20_03_stop_before_the_turn_starts_keeps_the_context_for_the_next(real_session, monkeypatch):
    s, FA = real_session
    s.agent.messages  # the agent exists
    orig = s.cp.begin_turn if s.cp else None
    if s.cp:
        def stop_then_begin(*a, **k):
            s._stop_req = True                        # Stop pressed while the checkpoint was being taken
            return orig(*a, **k)
        monkeypatch.setattr(s.cp, "begin_turn", stop_then_begin)
    else:
        pytest.skip("no checkpoint in this mode")
    monkeypatch.setenv("MH_LEARN", "1")
    (A.SESSIONS / s.id).mkdir(exist_ok=True)
    LRN._PENDING.clear()
    LRN._PENDING[s.id] = ["Prefers short answers always"]
    MS.add("Prefers short answers always", kind="learned", scope="everywhere")
    s.send("first try")
    time.sleep(0.3)
    _wait(s)
    assert FA.sent == []                              # it never started
    assert s.ctx_pending is True and A.read_meta(s.id)["ctx_pending"] is True
    assert LRN.peek(s.id) == ["Prefers short answers always"]
    monkeypatch.setattr(s.cp, "begin_turn", orig)
    s.send("second try")
    time.sleep(0.3)
    _wait(s)
    assert "Today is" in FA.sent[0] and "Prefers short answers always" in FA.sent[0]
    assert s.ctx_pending is False and LRN.peek(s.id) == []


# ---- M21-01 .. M21-04, F-33 -------------------------------------------------------------------------------------------------------------------------
def _cv(i, title="T"):
    return {"source": "chatgpt", "id": f"c{i}", "title": f"{title}{i}", "created": 1.0, "updated": 2.0, "kind": "chat",
            "workdir": "", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}


def test_M21_01_half_written_import_is_redone(home):
    tid = CI._tid("chatgpt", "c1")
    half = CI._sessions() / tid
    half.mkdir(parents=True)
    (half / "meta.json").write_text("{}")                       # a crash left only this
    r = CI.write_threads([_cv(1), _cv(2)])
    assert r["imported"] == 2 and r["skipped"] == 0
    assert json.loads((half / "session.json").read_text())["id"] == tid
    assert not [p for p in CI._sessions().iterdir() if p.name.startswith(".")]
    assert CI.write_threads([_cv(1), _cv(2)])["skipped"] == 2


def test_M21_01_a_failed_write_leaves_nothing_behind(home, monkeypatch):
    def boom(*a, **k):
        raise OSError("full disk")
    monkeypatch.setattr(CI.SS, "write_snapshot", boom)
    with pytest.raises(OSError):
        CI.write_threads([_cv(1)])
    assert not list(CI._sessions().glob("*"))
    monkeypatch.undo()


def test_M21_02_deleted_imported_chat_stays_deleted(home, monkeypatch):
    monkeypatch.setenv("MH_DELETE_GRACE_S", "0")
    r = CI.write_threads([_cv(1), _cv(2)])
    gone = r["ids"][0]
    A.thread_action(gone, {"action": "delete"})
    again = CI.write_threads([_cv(1), _cv(2)])
    assert again["imported"] == 0 and again["removed_earlier"] == 1 and again["skipped"] == 2
    assert not (CI._sessions() / gone).exists()
    assert "you deleted earlier" in again["summary"]


def test_F_33_reimport_reports_new_and_already_present(home):
    first = CI.write_threads([_cv(1), _cv(2)])
    assert first["summary"] == "2 new chats imported."
    second = CI.write_threads([_cv(1), _cv(2)])
    assert second["imported"] == 0 and second["summary"] == "Nothing new to import; 2 were already here."
    assert CI.write_threads([_cv(1), _cv(2), _cv(3)])["summary"] == "1 new chat imported; 2 were already here."


@pytest.fixture
def web(home):
    from meshpool_harness.ui import server as SV
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None, raw=None):
        req = urllib.request.Request(base + path, data=raw if raw is not None else (None if body is None else json.dumps(body).encode()),
                                     headers={"X-Meshpool-Token": SV.TOKEN,
                                              "Content-Type": "application/zip" if raw is not None else "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    httpd.shutdown()


def _export_zip(path):
    conv = [{"id": "x1", "title": "Plan", "create_time": 1.0, "update_time": 2.0, "current_node": "n2", "mapping": {
        "n1": {"id": "n1", "parent": None, "children": ["n2"], "message": {"author": {"role": "user"}, "create_time": 1.0,
               "content": {"content_type": "text", "parts": ["hello there"]}, "recipient": "all"}},
        "n2": {"id": "n2", "parent": "n1", "children": [], "message": {"author": {"role": "assistant"}, "create_time": 2.0,
               "content": {"content_type": "text", "parts": ["hi"]}, "recipient": "all"}}}}]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("conversations.json", json.dumps(conv))
    return path.read_bytes()


def test_M21_03_uploaded_zip_is_removed(home, web, tmp_path, monkeypatch):
    """Already fixed: the temporary copy of an upload never stays behind, on success or on a bad file."""
    import tempfile
    tmp = tmp_path / "tmpdir"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    st, r = web("/api/import-file?chats=1&context=0", raw=_export_zip(tmp_path / "e.zip"))
    assert st == 200 and r["imported"] == 1 and r["summary"] == "1 new chat imported." and r["ids"]
    assert not list(tmp.glob("*.zip"))
    st, _ = web("/api/import-file?chats=1&context=0", raw=b"this is not a zip")
    assert st >= 400 and not list(tmp.glob("*.zip"))


def test_M21_04_background_context_failure_is_recorded(home, web, tmp_path, monkeypatch):
    monkeypatch.setattr(IM, "import_export", lambda path, client=None: {
        "kind": "chatgpt", "skills": [], "memory": 0, "instructions": 0,
        "skipped": ["context from conversations: ConnectionError: the model could not be reached"]})
    st, r = web("/api/import-file?chats=1&context=1", raw=_export_zip(tmp_path / "e.zip"))
    assert st == 200 and r["context_pending"]
    for _ in range(100):
        st, s = web("/api/import-status")
        if s.get("state") in ("failed", "done"):
            break
        time.sleep(0.1)
    assert s["state"] == "failed" and "could not be reached" in s["error"]


# ---- F-07: search finds the name the person gave a chat ---------------------------------------------------------------------------------------------
def test_F_07_search_finds_a_custom_title(home):
    sid = "chat-7"
    d = A.SESSIONS / sid
    d.mkdir()
    (d / "session.json").write_text(json.dumps({"id": sid, "title": "Untitled", "workdir": str(home), "updated": 1,
                                                  "messages": [{"role": "user", "content": "numbers for the quarter"}]}))
    A.thread_action(sid, {"action": "rename", "title": "Zanzibar board pack"})
    assert [h["id"] for h in A.search("zanzibar")] == [sid]
    assert [h["id"] for h in A.search("quarter")] == [sid]


# ---- F-17: the first-run import offers sensible defaults and nothing starts broken ------------------------------------------------------------------
def test_F_17_defaults_and_servers_stay_off_until_tested(home, tmp_path, monkeypatch):
    spec_ok = {"command": shutil.which("python3") or "python3", "args": ["x.py"]}
    spec_bad = {"command": "definitely-not-installed-xyz", "args": []}
    monkeypatch.setattr(IM, "mcp_sources", lambda: [("Claude", {"ok": spec_ok, "bad": spec_bad})])
    rows = {r["name"]: r for r in IM.find_mcp()}
    assert rows["ok"]["default"] is True and rows["bad"]["default"] is False and rows["bad"]["broken"] is True
    IM.import_mcp("bad", rows["bad"]["spec"])
    from meshpool_harness.agent import mcp as M
    srv = M.load_config()["bad"]
    assert srv["disabled"] is True and srv["needs_test"] is True
    with pytest.raises(ValueError, match="did not start"):                 # switching it on starts it first
        A.mcp_save({"name": "bad", "enabled": True})
    assert M.load_config()["bad"]["disabled"] is True
    monkeypatch.setattr(CI, "claude_code_dir", lambda: home / "cc")
    (home / "cc" / "p").mkdir(parents=True)
    (home / "cc" / "p" / "s1.jsonl").write_text("{}\n")
    monkeypatch.setattr(CI, "codex_dir", lambda: home / "nocodex")
    scan = CI.scan_local()
    assert scan["claude-code"]["default"] is True and scan["codex"]["default"] is False
    js = (Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'id="oLocal" checked' in js and "it.default === false" in js     # ticked unless known not to work


# ---- F-32: the export's context uses the configured model and a checked answer -------------------------------------------------------------------------
def test_F_32_configured_model_and_validated_answer(home, tmp_path, monkeypatch):
    for k in ("MH_CONTEXT_MODEL", "MH_MODEL"):
        monkeypatch.delenv(k, raising=False)
    C.write({"MH_MODEL": "my-configured-model"})
    assert IM.context_model() == "my-configured-model"
    monkeypatch.setenv("MH_CONTEXT_MODEL", "ctx-model")
    assert IM.context_model() == "ctx-model"
    assert IM.check_context_answer("## About me\n- Works in finance\n- Based in Dubai") == ""
    assert IM.check_context_answer("I'm sorry, I can't help with that request.")
    assert IM.check_context_answer("Sure! Here you go:\n\n- one\n- two")                    # lines but no headings
    seen = {}

    class Client:
        def chat(self, **kw):
            seen["model"] = kw["model"]
            return {"choices": [{"message": {"content": "I'm sorry, I can't do that."}}]}
    _export_zip(tmp_path / "e.zip")
    r = IM.import_export(str(tmp_path / "e.zip"), client=Client())
    assert seen["model"] == "ctx-model" and r["memory"] == 0 and any("format" in x for x in r["skipped"])
    assert not [f for f in MS.load() if "sorry" in f["text"].lower()]
