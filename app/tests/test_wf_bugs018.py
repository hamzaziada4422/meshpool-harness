"""Bugs found by the 0.0.18 persona testers (Daniel, consultant; Lina, data analyst; Sara), server and engine side
(IDs WF-nn). The browser side is in test_wf_ui018.py. No model is called: the deck build runs on a stub client."""
from __future__ import annotations

import base64
import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

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


class FakeSession:
    """A chat without an agent behind it (as in test_projects_v2.py)."""

    def __init__(self, workdir=None, mode=None, harness=None, kind="code", model=None, plan=False, **kw):
        self.id = uuid.uuid4().hex[:8]
        (A.SESSIONS / self.id).mkdir()
        self.workdir, self.kind, self.state, self.harness = workdir, kind, "idle", {"id": harness or "meshpool-work"}
        self.agent = type("G", (), {"title": "", "deleted": False})()
        self.events = []

    def view(self, since):
        return {"id": self.id, "workdir": self.workdir}

    def emit(self, ev):
        self.events.append(ev)

    def close(self):
        pass


# ---------------------------------------------------------------------------------------------------- WF-01
def test_a_folder_picked_in_onboarding_becomes_a_pinned_project_and_new_chats_work_in_it(home, tmp_path, monkeypatch):
    """WF-01 / WF-A: "Folders I choose" only added the folder to the permission list. It was in no sidebar, no project
    picker, and the chat worked in a hidden app-data folder (Lina's agent then searched / for her folder). The folder
    now becomes a pinned project linked to it, and a chat in that project works in the folder itself."""
    from meshpool_harness.ui import server as SV
    monkeypatch.setattr(A, "Session", FakeSession)
    folder = tmp_path / "userhome" / "ClientWork"
    folder.mkdir(parents=True)
    got = SV._as_project({"project": True}, str(folder))
    pid = got["pid"]
    p = PS.detail(pid)
    assert p["name"] == "ClientWork" and p["pinned"] and p["link"]["path"] == str(folder.resolve())
    assert SV._as_project({"project": True}, str(folder))["pid"] == pid          # the same folder: the same project
    assert SV._as_project({}, str(folder)) == {}                                  # Settings > Permissions: no project
    v = A.create({"project_id": pid, "task": "Summarise the brief"})
    assert Path(v["workdir"]).resolve() == folder.resolve()                      # the chat's folder is hers
    assert not A.read_meta(v["id"]).get("scratch")


# ---------------------------------------------------------------------------------------------------- WF-02
def _bare_session(workdir: str, monkeypatch):
    s = A.Session.__new__(A.Session)
    s.workdir, s.state, s.queue, s.events = workdir, "idle", [], []
    s.lock = threading.RLock()
    s.agent = object()
    s.harness = {"type": "x", "id": "x"}
    started = []
    monkeypatch.setattr(A.Session, "_start_turn", lambda self, t, i, *a: started.append(t))
    s.emit = lambda e: None
    return s, started


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_a_dropped_file_already_in_the_folder_is_used_where_it_is(tmp_path, monkeypatch):
    """WF-02: dropping a file that already lived in the chat's folder made a second copy at the top level, without a
    word. It is now used where it is; a file that is not there is copied, and the app is told where the copy went."""
    work = tmp_path / "ClientWork"
    (work / "briefs").mkdir(parents=True)
    (work / "briefs" / "brief.pdf").write_bytes(b"%PDF the real brief")
    (work / "notes.txt").write_bytes(b"same name, same bytes")
    s, started = _bare_session(str(work), monkeypatch)
    s.send("read these", files=[{"name": "brief.pdf", "data": _b64(b"%PDF the real brief")},
                                {"name": "notes.txt", "data": _b64(b"same name, same bytes")},
                                {"name": "new.csv", "data": _b64(b"a,b\n1,2\n")}])
    assert sorted(p.name for p in work.iterdir()) == ["briefs", "new.csv", "notes.txt"]   # no "brief.pdf" or "(2)" copy
    assert "briefs/brief.pdf" in started[0] and "notes.txt" in started[0]
    notes = {a["name"]: a for a in s._attached}
    assert notes["brief.pdf"] == {"name": "brief.pdf", "path": "briefs/brief.pdf", "reused": True}
    assert notes["notes.txt"]["reused"] and notes["new.csv"] == {"name": "new.csv", "path": "new.csv", "copy": True}
    s2, _ = _bare_session(str(work), monkeypatch)
    s2.send("again", files=[{"name": "notes.txt", "data": _b64(b"other bytes")}])
    assert s2._attached == [{"name": "notes.txt", "path": "notes (2).txt", "copy": True}]   # a different file: a named copy


# ---------------------------------------------------------------------------------------------------- WF-03
def test_a_failed_slide_writes_no_model_error_into_the_speaker_notes(tmp_path, monkeypatch):
    """WF-03: slide 1's speaker notes said "content failed: claude-sonnet returned no JSON (finish_reason 'stop', 988
    chars of content)". The error goes to the log and the engine's answer, never into the user's file."""
    from engines.forge import build as B
    from pptx import Presentation

    class Stub:
        def chat(self, *, model, messages, **kw):
            sys_, user = messages[0]["content"], messages[-1]["content"]
            if "engagement manager" in sys_:
                body = {"slides": [{"node": "0", "title": "Northwind plan", "archetype": "cover"},
                                   {"node": "1.1", "title": "North revenue grew 18% to $120m",
                                    "archetype": "bar_takeaway", "evidence": ["F1"]}]}
            elif "ONE consulting slide" in sys_:
                x = json.loads(user.split("This slide: ", 1)[1].split("\n\nFacts", 1)[0])
                if x["archetype"] == "cover":                     # the model answers in prose: no JSON at all
                    return {"choices": [{"message": {"content": "Here is the cover slide you asked for."},
                                         "finish_reason": "stop"}]}
                body = dict(x, subtitle="Revenue by region, $m, 2025", takeaway={"text": "North leads", "prov": ["F1"]},
                            chart={"kind": "bar", "categories": ["North", "South", "East"],
                                   "series": [{"name": "Revenue", "values": [120, 90, 60], "prov": ["F1"]}], "unit": "$m"},
                            bullets=[{"text": "North revenue was $120m", "prov": ["F1"]}],
                            source={"text": "Company data", "prov": ["F1"]})
            else:
                body = {"edits": []}
            return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(B, "_review_on", lambda client, rules: False)
    monkeypatch.setenv("MH_PERSONAS", "0")
    res = B.build("Facts\n- North revenue $120m, South $90m, East $60m in 2025; North grew 18%", str(tmp_path),
                  slides_wanted=2, client=Stub(), task_id="t")
    notes = " ".join(s.notes_slide.notes_text_frame.text for s in Presentation(res["pptx"]).slides if s.has_notes_slide)
    assert "content failed" not in notes and "returned no JSON" not in notes and "finish_reason" not in notes, notes
    assert res["content_failed"] and "slide 0" in res["content_failed"][0]


def test_a_deck_without_a_brand_has_no_product_name_in_its_footer(tmp_path):
    """WF-03: every slide's footer said "Meshpool". With no brand or company profile the footer is the page number."""
    from engines.forge import brand as BR
    from engines.forge.render import render
    from pptx import Presentation
    out = tmp_path / "plain.pptx"
    render(copy.deepcopy(BR.SAMPLE), str(out), fix_labels=False)
    for s in Presentation(str(out)).slides:
        for sh in s.shapes:                                    # the footer label (the sample's own text is content)
            if sh.has_text_frame and sh.name in ("Firm", "Footer", "PageNumber"):
                assert "meshpool" not in sh.text_frame.text.lower(), (sh.name, sh.text_frame.text)


# ---------------------------------------------------------------------------------------------------- WF-04
def test_a_deck_saved_under_the_users_name_is_moved_not_copied(tmp_path, monkeypatch):
    """WF-04: the deck showed up as deck.pptx and "ViDA ... .pptx", identical until an edit, then deck.pptx was
    stale. The job's file is now moved to the name the user asked for, and the job lists that file."""
    from meshpool_harness.agent import office_jobs as OJ
    from meshpool_harness.agent import office_mcp_server as OS
    proj = tmp_path / "ClientWork"
    out = proj / "Meshpool outputs" / "20261126-slides-abcd"
    out.mkdir(parents=True)
    (out / "deck.pptx").write_bytes(b"PK deck")
    (out / "qa.json").write_text("{}")
    monkeypatch.setattr(OS, "_project", lambda: proj)
    jid = "wf04job"
    monkeypatch.setitem(OJ.JOBS, jid, {"engine": "slides", "out": out, "done": time.time(), "inputs": set(),
                                       "result": {"ok": True, "steps": [{"artifacts": [str(out / "deck.pptx")]}]}})
    msg = OS._save_copy(jid, "ViDA e-invoicing small consulting firms.pptx")
    named = proj / "ViDA e-invoicing small consulting firms.pptx"
    assert named.read_bytes() == b"PK deck" and not (out / "deck.pptx").exists(), msg
    assert OJ._made(OJ.JOBS[jid]) == [named.resolve()]                  # not the job folder's qa.json
    assert OS._save_copy(jid, "ViDA e-invoicing small consulting firms.pptx") and named.exists()   # once only


def test_build_and_check_files_are_not_the_users_files(tmp_path):
    """WF-04: Artifacts, Your work and the outputs list showed qa.json, clarity.json, facts.json, slides.json,
    frame.json and deck.provenance.json next to the deck."""
    job = tmp_path / "Meshpool outputs" / "job1"
    job.mkdir(parents=True)
    names = ["deck.pptx", "qa.json", "clarity.json", "facts.json", "slides.json", "frame.json", "deck.provenance.json",
             "dossier.md"]
    for n in names:
        (job / n).write_text("x")
    (tmp_path / "data.json").write_text("{}")                          # the user's own JSON at the top stays listed
    turns = [{"n": 1, "changed": [{"path": f"Meshpool outputs/job1/{n}", "status": "added"} for n in names]
              + [{"path": "data.json", "status": "added"}]}]
    got = sorted(o["path"] for o in W.outputs_from([], turns, tmp_path.resolve()))
    assert got == ["Meshpool outputs/job1/deck.pptx", "Meshpool outputs/job1/dossier.md", "data.json"], got


# ---------------------------------------------------------------------------------------------------- WF-05
def test_your_work_topics_are_real_words_not_summary_filler(monkeypatch):
    """WF-05: "What you worked on" showed "user", "number" and "wanted" (drawn with their counts: "user2, number1,
    wanted1"), the filler of the stored summaries. Topics are now words, in two chats or more."""
    from meshpool_harness import summaries as SM
    from meshpool_harness import yourwork as YW
    lines = {"c1": "The user wanted a deck on ViDA e-invoicing for small consulting firms.",
             "c2": "The user asked for a number check on the ViDA e-invoicing deadlines.",
             "c3": "The user wanted a summary of report2 with 3 numbers."}
    monkeypatch.setattr(SM, "summary_for", lambda sid: lines[sid])
    chats = [{"id": k, "title": "", "users": ["x"]} for k in lines]
    topics = [t["topic"] for t in YW._topics(chats)[0]]
    assert topics and set(topics) <= {"vida", "invoicing", "deck"}, topics
    assert not any(ch.isdigit() for t in topics for ch in t)


# ---------------------------------------------------------------------------------------------------- WF-07 / WF-C
def test_undo_says_what_it_restored_and_keeps_the_backup_path_in_the_details():
    """WF-07: the Undo line printed a full /tmp/.../checkpoints/undone/... path. WF-C: it said "Files are back to how
    they were" while a file outside the chat's folder kept the agent's change."""
    msg, detail = A.undo_message({"restored": ["sales_clean.csv", "out/chart.png"], "deleted": ["monthly_revenue.png"],
                                  "outside": ["/home/lina/LinaData/sales.csv"],
                                  "backup": "/tmp/x/sessions/s1/checkpoints/undone/20261126-103934"})
    assert msg.startswith("Undone. Restored 2 files (sales_clean.csv, chart.png) and removed 1 file "
                          "(monthly_revenue.png)."), msg
    assert "/tmp" not in msg and "checkpoints" not in msg
    assert "sales.csv" in msg and "Undo only covers this chat's folder" in msg
    assert "checkpoints/undone/20261126-103934" in detail and "/home/lina/LinaData/sales.csv" in detail
    assert A.undo_message({})[0] == "Undone. No files in this chat's folder had changed."


def test_undo_restores_the_project_folder_and_names_writes_outside_it(tmp_path):
    """WF-C: with the project folder as the chat's folder, Undo puts the user's own file back; a write outside the
    folder cannot be undone and is named."""
    from meshpool_harness.checkpoints import Checkpoints
    root = tmp_path / "LinaData"
    root.mkdir()
    (root / "sales_clean.csv").write_text("units,unit_price\n2,3\n")
    elsewhere = tmp_path / "Downloads" / "copy.csv"
    elsewhere.parent.mkdir()
    cp = Checkpoints(str(root), tmp_path / "store", mode="journal")
    cp.begin_turn("add a revenue column", 0, 0)
    with cp.tool("bash", {"command": "python add_column.py"}):
        (root / "sales_clean.csv").write_text("units,unit_price,revenue\n2,3,6\n")
        (root / "monthly_revenue.png").write_bytes(b"\x89PNG chart")
    with cp.tool("write_file", {"path": str(elsewhere), "content": "x"}):
        elsewhere.write_text("x")
    cp.end_turn()
    r = cp.revert_to_before(1)
    assert (root / "sales_clean.csv").read_text() == "units,unit_price\n2,3\n"
    assert not (root / "monthly_revenue.png").exists()
    assert r["outside"] == [str(elsewhere)]
    assert "copy.csv" in A.undo_message(r)[0]


# ---------------------------------------------------------------------------------------------------- WF-09
def test_engine_stages_reach_the_chat_as_job_stage_events_not_checks(home, monkeypatch):
    """WF-09: a deck took 4 min 51 s and the chat showed only "2 steps". The engines' stages now travel with the
    checks record the turn already watches, as job_stage events; they are never counted as checks."""
    from meshpool_harness import checks as CK
    from meshpool_harness.core import loop as LP
    from engines.forge import build as B
    run = "chatwf09:1"
    CK.begin(run)
    try:
        B._stage("writing 6 slides")
        CK.passed("deck_rules", "design rules", 37)
        monkeypatch.setenv("MH_STAGE_LABEL", "Workbook")
        LP._stage_step(4, [{"function": {"name": "write_file"}}, {"function": {"name": "bash"}}])
        monkeypatch.delenv("MH_STAGE_LABEL")
        LP._stage_step(5, [{"function": {"name": "bash"}}])               # outside an office job: nothing
        evs = CK.events_for(run)
    finally:
        CK.end(run)
    assert [e["type"] for e in evs] == ["job_stage", "check", "job_stage"], evs
    assert evs[0]["text"] == "Deck: writing 6 slides"
    assert evs[2]["text"] == "Workbook: step 4, writing a file, running a step"
    assert CK.final_line(run) == "Checked the deck: all 37 checks passed."


# ---------------------------------------------------------------------------------------------------- WF-I
def test_a_code_session_in_a_projects_folder_is_listed_under_the_project(home, tmp_path, monkeypatch):
    """WF-I: Lina's data question became a Code session and was gone from her project's list. A Code session whose
    folder is a project's linked folder is listed under the project; one taken out on purpose stays out."""
    monkeypatch.setattr(A, "Session", FakeSession)
    site = tmp_path / "LinaData"
    site.mkdir()
    pid = PS.create("LinaData", link={"type": "folder", "path": str(site)})["id"]
    v = A.create({"workdir": str(site.resolve()), "kind": "code", "task": "add a revenue column"})
    w = A.create({"workdir": str(site.resolve()), "kind": "code", "task": "second"})
    for x in (v, w):
        (A.SESSIONS / x["id"] / "session.json").write_text(json.dumps({"id": x["id"], "title": "T", "updated": 1,
                                                                      "workdir": x["workdir"]}))
    A.write_meta(w["id"], project_unbound=True)
    A.list_dirty()
    rows = {r["id"]: r for r in A.listing()}
    assert rows[v["id"]]["project_id"] == pid
    assert rows[w["id"]]["project_id"] == ""
    assert A.read_meta(v["id"]).get("project_id", "") == ""            # listing only: the stored chat is unchanged


# ---------------------------------------------------------------------------------------------------- WF-H
def test_artifacts_follow_an_undo_even_when_the_transcript_file_did_not_change(home, tmp_path):
    """WF-H: Artifacts went stale after Undo: its cache followed session.json only, and Undo changes the
    checkpoints. A file the chat no longer made (Undo put the older version back) stays listed no more."""
    from meshpool_harness.ui import hub as H
    root = tmp_path / "work"
    root.mkdir()
    (root / "deck.pptx").write_bytes(b"PK")
    sid = "wfh1"
    d = A.SESSIONS / sid
    (d / "checkpoints").mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({"id": sid, "workdir": str(root), "messages": []}))
    tj = d / "checkpoints" / "turns.json"
    tj.write_text(json.dumps({"seq": 1, "turns": [{"n": 1, "done": True, "changed": [{"path": "deck.pptx",
                                                                                       "status": "added"}]}]}))
    row = {"id": sid, "workdir": str(root), "kind": "cowork", "state": "idle"}
    assert [f["path"] for f in H._session_files(row)] == ["deck.pptx"]
    time.sleep(0.02)
    tj.write_text(json.dumps({"seq": 1, "turns": []}))                   # Undo: the turn is gone, the file stays
    os.utime(tj, (time.time() + 5, time.time() + 5))
    assert H._session_files(row) == []
