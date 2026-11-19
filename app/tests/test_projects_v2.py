"""Projects (name, instructions, knowledge, memory, optional linked folder), chats that need no folder, moving chats,
the context a project adds to a chat, and the migration of the folders the old app listed. No network, no model."""
import base64
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness import assistant as AS
from meshpool_harness import config as C
from meshpool_harness import project_store as PS
from meshpool_harness.ui import agent_api as A
from meshpool_harness.ui import workbench as W


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))
    monkeypatch.setattr(A, "SESSIONS", tmp_path / "sessions")
    A.SESSIONS.mkdir()
    monkeypatch.setattr(A, "LIVE", {})
    monkeypatch.setattr(A, "_METAS", {})
    monkeypatch.setattr(A, "_ROWS", {})
    C.ensure_home()
    return tmp_path


# ---------- store ------------------------------------------------------------------------------------------------------
def test_create_update_delete(home):
    p = PS.create("  Q3   report ", "Numbers for the board", "Always answer in bullet points.")
    assert p["name"] == "Q3 report" and PS.ID_RX.fullmatch(p["id"]) and p["instructions"].startswith("Always")
    assert [x["id"] for x in PS.list_projects()] == [p["id"]]
    q = PS.update(p["id"], name="Q4 report", instructions="Use tables.")
    assert q["name"] == "Q4 report" and q["instructions"] == "Use tables."
    PS.delete(p["id"])
    assert PS.list_projects() == []
    with pytest.raises(KeyError):
        PS.detail(p["id"])


def test_names_and_ids_are_checked(home):
    with pytest.raises(ValueError):
        PS.create("   ")
    for bad in ("../x", "p_zz", "p_1234567890/../..", ""):
        with pytest.raises(ValueError):
            PS.detail(bad)


def test_link_needs_an_existing_folder(home, tmp_path):
    with pytest.raises(ValueError):
        PS.create("Site", link={"type": "folder", "path": str(tmp_path / "nope")})
    d = tmp_path / "site"
    d.mkdir()
    p = PS.create("Site", link={"type": "folder", "path": str(d)})
    assert p["link"]["path"] == str(d.resolve())
    assert PS.update(p["id"], link=None)["link"] is None


def test_knowledge_files(home):
    p = PS.create("Docs")
    pid = p["id"]
    r = PS.knowledge_add(pid, "../../evil/notes.md", b"# Pricing\nThe Pro plan costs 40 dollars a month.\n")
    assert r["name"] == "notes.md" and r["readable"]
    PS.knowledge_add(pid, "blob.bin", b"\x00\x01\x02\x03")
    names = [k["name"] for k in PS.knowledge_list(pid)]
    assert names == ["blob.bin", "notes.md"]
    assert not [k for k in PS.knowledge_list(pid) if k["name"] == "blob.bin"][0]["readable"]
    assert PS.knowledge_path(pid, "notes.md").read_bytes().startswith(b"# Pricing")
    PS.knowledge_remove(pid, "notes.md")
    assert [k["name"] for k in PS.knowledge_list(pid)] == ["blob.bin"]
    with pytest.raises(KeyError):
        PS.knowledge_path(pid, "notes.md")
    with pytest.raises(ValueError):
        PS.knowledge_add(pid, "empty.txt", b"")


def test_memory_edit_and_clear(home):
    pid = PS.create("Shop")["id"]
    assert PS.memory_add(pid, "Tests run with npm test") == "Saved to this project's memory."
    assert PS.memory_add(pid, "tests run with NPM TEST").startswith("Already")
    with pytest.raises(ValueError):
        PS.memory_add(pid, "my password is hunter2 and api_key=sk-abcdefghijklmnop")
    PS.memory_set(pid, "One fact\n\nAnother fact  with   spaces")
    assert PS.memory(pid).splitlines() == ["One fact", "Another fact with spaces"]
    PS.memory_clear(pid)
    assert PS.memory(pid) == ""


# ---------- what a chat is told ---------------------------------------------------------------------------------------
def test_context_has_the_project_and_only_that_project(home):
    a = PS.create("Alpha", "The alpha launch", "ALPHA-RULE: reply in French.")["id"]
    b = PS.create("Beta", "", "BETA-RULE: reply in German.")["id"]
    PS.memory_add(a, "The alpha codename is Falcon")
    PS.knowledge_add(a, "brief.txt", b"The launch date is the first of March.")
    big = ("Budget section. " * 200 + "\n\nThe vendor shortlist is Acme and Globex.\n\n" + "Filler text. " * 300).encode()
    PS.knowledge_add(a, "big.txt", big)
    ctx = AS.context_block("when is the launch?", "", a)
    assert "ALPHA-RULE" in ctx and "Falcon" in ctx and "first of March" in ctx and "brief.txt" in ctx
    assert "BETA-RULE" not in ctx
    assert "BETA-RULE" not in AS.context_block("hello", "", "") and "ALPHA-RULE" not in AS.context_block("hello", "", "")
    # a long file contributes the passage that matches the question, not everything
    assert "Acme and Globex" in AS.context_block("which vendor shortlist?", "", a)
    assert "Acme and Globex" not in AS.context_block("when is the launch?", "", a)
    # project memory and knowledge are fenced background; instructions are the user's directions
    fenced = ctx.split(AS.FENCE_OPEN)[1]
    assert "Falcon" in fenced and "ALPHA-RULE" not in fenced.split(AS.FENCE_CLOSE)[0]
    # deterministic: the same question gives the same text (a stable prompt prefix)
    assert AS.context_block("when is the launch?", "", a).split("Today is")[1][60:] == ctx.split("Today is")[1][60:]


def test_switches_turn_project_parts_off(home):
    pid = PS.create("Gamma", "", "GAMMA-RULE")["id"]
    PS.memory_add(pid, "gamma fact one")
    AS.save_settings(instructions_on=False, memory_on=False)
    ctx = AS.context_block("hi", "", pid)
    assert "GAMMA-RULE" not in ctx and "gamma fact one" not in ctx
    AS.save_settings(instructions_on=True, memory_on=True)
    assert "GAMMA-RULE" in AS.context_block("hi", "", pid)


def test_project_note_goes_to_the_project_not_the_folder(home, tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    linked = PS.create("Repo", link={"type": "folder", "path": str(proj)})["id"]
    other = PS.create("Other")["id"]
    AS.remember_project(str(proj), "Run pytest -q for the tests")
    assert "pytest" in PS.memory(linked) and PS.memory(other) == ""
    assert AS.project_memory(str(proj)) == ""                        # nothing written to the old per-folder file
    scratch = PS.new_scratch(other)
    AS.remember_project(scratch, "Deploys go through make ship")
    assert "make ship" in PS.memory(other) and "make ship" not in PS.memory(linked)
    plain = PS.new_scratch("")
    AS.remember_project(plain, "A note from a chat that is in no project")     # old behaviour: a note for that folder
    assert "no project" in AS.project_memory(plain) and "no project" not in PS.memory(other)


def test_a_chat_folder_for_a_project_gets_its_knowledge_files(home):
    pid = PS.create("Docs")["id"]
    PS.knowledge_add(pid, "spec.md", b"hello spec")
    d = Path(PS.new_scratch(pid))
    assert PS.is_scratch(str(d)) and (d / "knowledge" / "spec.md").read_text() == "hello spec"
    assert PS.for_root(str(d)) == pid
    with open(d / "knowledge" / "spec.md", "w", encoding="utf-8") as f:       # the agent editing its copy in place
        f.write("changed by a chat")
    assert (PS._kdir(pid) / "spec.md").read_text() == "hello spec"             # the project's own file is untouched
    PS.remove_scratch(str(d))
    assert not d.exists()
    PS.remove_scratch(str(home))                                       # never removes anything that is not a chat folder
    assert home.exists()


# ---------- migration -------------------------------------------------------------------------------------------------
def test_migration_keeps_old_folders_chats_and_notes(home, tmp_path):
    f1, f2 = tmp_path / "shop", tmp_path / "blog"
    f1.mkdir()
    f2.mkdir()
    W.add_project(str(f1), "Shop")
    W.add_project(str(f2))
    AS.remember_project(str(f1), "Shop uses pnpm")                     # an old per-folder note (no projects exist yet)
    old_files = {p: p.read_bytes() for p in [W._projects_file(), AS._project_file(str(f1))]}
    made = PS.migrate([str(f1), str(tmp_path / "gone")])
    assert sorted(m["name"] for m in made) == ["Shop", "blog"]
    shop = next(m for m in made if m["name"] == "Shop")
    assert shop["link"]["path"] == str(f1.resolve()) and "pnpm" in PS.memory(shop["id"])
    assert PS.migrate([str(f1)]) == []                                  # idempotent
    assert {p: p.read_bytes() for p in old_files} == old_files          # the old data is untouched
    # an old chat (no project_id in its meta) is found through its folder
    assert A._project_of({}, str(f1.resolve())) == shop["id"]
    assert A._project_of({"project_id": ""}, str(f1.resolve())) == ""   # taken out of the project on purpose
    # rollback: delete projects/, the old behaviour is all that is left and nothing is lost
    import shutil
    shutil.rmtree(PS.root())
    assert A._project_of({}, str(f1.resolve())) == "" and PS.list_projects() == []
    assert "pnpm" in AS.project_memory(str(f1))
    assert sorted(r["name"] for r in W.projects()) == ["Shop", "blog"]


def test_migration_skips_the_default_and_home_folders(home, tmp_path):
    from meshpool_harness.ui.desktop import default_project
    d = default_project()
    d.mkdir(parents=True, exist_ok=True)
    assert PS.migrate([str(d), str(Path.home())]) == []


# ---------- chats -----------------------------------------------------------------------------------------------------
class FakeSession:
    made = []

    def __init__(self, workdir=None, mode=None, harness=None, kind="code", model=None, plan=False, **kw):
        import uuid
        self.id = uuid.uuid4().hex[:8]
        (A.SESSIONS / self.id).mkdir()
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


def test_a_chat_needs_no_folder(home, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    added = []
    monkeypatch.setattr(W, "add_project", lambda *a, **k: added.append(a))
    v = A.create({"harness": "meshpool-auto", "task": "hello"})
    wd = Path(v["workdir"])
    assert wd.parent == PS.chats_root().resolve() and wd.is_dir() and not added
    assert A.read_meta(v["id"])["scratch"] is True and A.read_meta(v["id"])["project_id"] == ""
    # the private folder goes with the chat; a chat in a folder the user chose does not remove it
    A._saved = lambda sid: {"workdir": str(wd)}
    A.thread_action(v["id"], {"action": "delete"})
    assert not wd.exists()


def test_plan_first_setting_makes_new_chats_start_with_a_plan(home, monkeypatch):
    """Settings, Plan first: saved in the profile, applied by the server to every new chat (the page's draft flag is
    reset after each send, so it cannot carry the setting)."""
    from meshpool_harness.ui import settings_v2 as SV
    seen = []
    class Spy(FakeSession):
        def __init__(self, *a, plan=False, **kw):
            seen.append(plan)
            super().__init__(*a, plan=plan, **kw)
    monkeypatch.setattr(A, "Session", Spy)
    A.create({"harness": "meshpool-auto", "task": "one", "plan": False})
    SV.save_profile(plan_first=True)
    A.create({"harness": "meshpool-auto", "task": "two", "plan": False})
    assert seen == [False, True]


def test_a_chat_in_a_linked_project_works_in_the_folder(home, tmp_path, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    site = tmp_path / "site"
    site.mkdir()
    pid = PS.create("Site", link={"type": "folder", "path": str(site)})["id"]
    v = A.create({"project_id": pid, "task": "fix the page"})
    assert Path(v["workdir"]).resolve() == site.resolve()
    assert A.read_meta(v["id"])["project_id"] == pid and not A.read_meta(v["id"]).get("scratch")
    with pytest.raises(ValueError):
        A.create({"project_id": "p_0000000000"})
    plain = PS.create("Notes")["id"]
    w = A.create({"project_id": plain, "task": "x"})
    assert PS.for_root(w["workdir"]) == plain


def test_listing_groups_and_move(home, tmp_path, monkeypatch):
    monkeypatch.setattr(A, "Session", FakeSession)
    a = PS.create("A")["id"]
    v1 = A.create({"project_id": a, "task": "one"})
    v2 = A.create({"task": "two"})
    for v in (v1, v2):                                                   # a saved chat row, as the real session writes it
        (A.SESSIONS / v["id"] / "session.json").write_text(json.dumps({"id": v["id"], "title": "T", "workdir": v["workdir"],
                                                                      "updated": 1}))
    rows = {r["id"]: r for r in A.listing()}
    assert rows[v1["id"]]["project_id"] == a and rows[v2["id"]]["project_id"] == ""
    A.thread_action(v2["id"], {"action": "move", "project_id": a})
    assert {r["id"]: r["project_id"] for r in A.listing()}[v2["id"]] == a
    A.thread_action(v2["id"], {"action": "move", "project_id": ""})
    assert {r["id"]: r["project_id"] for r in A.listing()}[v2["id"]] == ""
    with pytest.raises(ValueError):
        A.thread_action(v2["id"], {"action": "move", "project_id": "p_ffffffffff"})
    A.thread_action(v2["id"], {"action": "rename", "title": "Renamed chat"})
    A.thread_action(v2["id"], {"action": "pin"})
    row = {r["id"]: r for r in A.listing()}[v2["id"]]
    assert row["title"] == "Renamed chat" and row["pinned"] is True
    PS.delete(a)                                                         # a deleted project: its chats become plain chats
    assert {r["id"]: r["project_id"] for r in A.listing()}[v1["id"]] == ""


def test_a_chat_moved_into_a_project_gets_it_once(home, monkeypatch):
    pid = PS.create("Zed", "", "ZED-RULE")["id"]
    monkeypatch.setattr(A, "Session", FakeSession)
    v = A.create({"task": "x"})
    A.thread_action(v["id"], {"action": "move", "project_id": pid})
    m = A.read_meta(v["id"])
    assert m["project_id"] == pid and m.get("project_ctx") != pid       # next turn adds the project, then marks it sent
    assert "ZED-RULE" in AS.project_block(pid, "hi")


# ---------- HTTP routes -----------------------------------------------------------------------------------------------
@pytest.fixture
def web(home):
    from meshpool_harness.ui import server as SV
    httpd = SV._Server(("127.0.0.1", 0), SV.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Meshpool-Token": SV.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers["Content-Type"].startswith("application/json") else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    httpd.shutdown()


def test_project_routes(web):
    st, p = web("/api/pj", {"name": "Web project", "instructions": "Be brief."})
    assert st == 200 and p["name"] == "Web project"
    pid = p["id"]
    assert web("/api/pj")[1]["projects"][0]["id"] == pid
    st, d = web(f"/api/pj/{pid}", {"action": "knowledge_add", "name": "a.md",
                                   "data": base64.b64encode(b"hello knowledge").decode()})
    assert st == 200 and d["knowledge"][0]["name"] == "a.md"
    st, raw = web(f"/api/pj/{pid}/file?name=a.md")
    assert st == 200 and raw == b"hello knowledge"
    assert web(f"/api/pj/{pid}", {"action": "memory_add", "text": "uses tabs"})[1]["memory"] == "uses tabs"
    assert web(f"/api/pj/{pid}", {"action": "memory_set", "text": "one\ntwo"})[1]["memory_count"] == 2
    assert web(f"/api/pj/{pid}", {"action": "memory_clear"})[1]["memory"] == ""
    assert web(f"/api/pj/{pid}", {"action": "update", "name": "Renamed", "description": "d"})[1]["name"] == "Renamed"
    assert web(f"/api/pj/{pid}", {"action": "knowledge_remove", "name": "a.md"})[1]["knowledge"] == []
    assert web(f"/api/pj/{pid}", {"action": "bogus"})[0] == 400
    assert web("/api/pj/p_0000000000")[0] == 404
    assert web("/api/pj", {"name": ""})[0] == 400
    assert web(f"/api/pj/{pid}", {"action": "delete"})[0] == 200
    assert web("/api/pj")[1]["projects"] == []
