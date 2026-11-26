"""ws20 (K-01/K-03): what Meshpool's layer adds to Pi and Codex per request. Each test fails on the code before the cut.

Sizes are measured with the profiler (testkit/bench3/profile); these tests pin the mechanism and a char budget.
"""
import json
import os
from pathlib import Path

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import capabilities as CAP
from meshpool_harness.agent import mcp_assist as MA
from meshpool_harness.agent import office_mcp_server as OM
from meshpool_harness.harnesses import acp as ACP, pi_rpc as PI

EDIT = ("In inventory.xlsx, add a Value column (qty times unit cost) as live formulas. Raise the unit cost of every "
        "item in the Cables category by 10%. Save over the same file.")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(h))
    for k in ("MH_PI_SKILLS", "MH_PI_MCP", "MH_ACP_MCP", "MH_ACP_TITLE", "MH_MCP_LAZY_WORK"):
        monkeypatch.delenv(k, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    return h


# ---- 1. Pi: no skills catalog ------------------------------------------------------------------------------------
def test_pi_has_no_skills_catalog_and_the_matched_skill_is_reachable(home, tmp_path):
    from meshpool_harness import skills as SK
    assert len(SK.enabled()) > 50                                 # the full catalog exists (defaults)
    root = tmp_path / "proj"
    h = PI.prepare(root, "chat1", False, "Build an Excel model of unit economics with live formulas")
    staged = [d.name for d in (h / "skills" / "meshpool").iterdir() if d.is_dir()]
    assert 1 <= len(staged) <= 3 and "excel-model" in staged     # only what matches the task
    args = PI.skill_args(h)
    assert "--no-skills" in args                                  # Pi lists nothing it discovers
    loaded = [args[i + 1] for i, a in enumerate(args) if a == "--skill"]
    assert any(p.endswith("excel-model") for p in loaded) and len(loaded) <= 3
    settings = json.loads((h / "settings.json").read_text())
    assert "skills" not in settings                               # no configured skills folder either
    index = (h / "skills" / "meshpool" / "INDEX.md").read_text()  # everything else stays reachable by name
    assert "pdf-tools" in index and "excel-model" not in index
    assert "INDEX.md" in " ".join(args)                           # one line in the system prompt points to it


def test_pi_user_installed_skill_stays_reachable(home, tmp_path):
    from meshpool_harness import skills as SK
    SK.create("zebra-grooming", "Groom zebras on request.", "Brush them.")
    h = PI.prepare(tmp_path / "proj", "c2", False, "hello")
    assert "zebra-grooming" in (h / "skills" / "meshpool" / "INDEX.md").read_text()
    h2 = PI.prepare(tmp_path / "proj", "c3", False, "please groom zebras")
    assert (h2 / "skills" / "meshpool" / "zebra-grooming" / "SKILL.md").is_file()
    h3 = PI.prepare(tmp_path / "proj", "c4", False, "use $zebra-grooming now")
    assert (h3 / "skills" / "meshpool" / "zebra-grooming" / "SKILL.md").is_file()


# ---- 2. Pi: deferred MCP -----------------------------------------------------------------------------------------
def test_pi_mcp_is_deferred_and_office_tools_are_direct_only_for_office_chats(home, tmp_path):
    _write_cfg()
    plain = PI._mcp_for_pi(tmp_path / "proj", "c", office=False)["mcpServers"]
    assert {"browser", "meshpool-assistant", "meshpool-office"} <= set(plain)
    assert all(s["exposure"] == "deferred" for s in plain.values())
    assert all(s.get("description") for n, s in plain.items() if n.startswith(("meshpool", "browser")))
    assert not any("toolExposure" in s for s in plain.values())
    office = PI._mcp_for_pi(tmp_path / "proj", "c", office=True)["mcpServers"]
    assert office["meshpool-office"]["exposure"] == "deferred"
    te = office["meshpool-office"]["toolExposure"]
    assert te["edit_office_file"] == "direct" and te["read_document"] == "direct"
    assert {"check_deck", "check_workbook", "check_document"} <= set(te)      # no file named: every check tool
    assert "make_deck" not in te and "convert_document" not in te             # convert_document: via tool_search
    assert all("toolExposure" not in s for n, s in office.items() if n != "meshpool-office")


def test_pi_default_tools_match_vanilla_pi(home, tmp_path):
    h = PI.prepare(tmp_path / "proj", "c5", False, "x")
    assert json.loads((h / "settings.json").read_text())["defaultTools"] == ["read", "bash", "edit", "write"]


# ---- 4. Codex over ACP -------------------------------------------------------------------------------------------
def _agent(tmp_path, office=False):
    """An ACP agent for Codex whose process is faked: it records the requests it would send."""
    from meshpool_harness import harnesses as H

    class R(ACP.ACPAgent):
        def __init__(s):
            super().__init__(H.CODEX, str(tmp_path / "proj"))
            s.sent, s.refuse_title = [], False

        def _start(s):
            return False

        def _request(s, method, params=None, timeout=60):
            s.sent.append((method, params))
            if method == "session/new":
                return {"sessionId": "s1", "modes": {}, "models": {}}
            if method == "session/set_title" and s.refuse_title:
                raise ACP.ACPError("method not found")
            return {}
    a = R()
    a.office_on = office
    a.title = "Add a Value column to inventory.xlsx"
    return a


def _servers(a):
    return [s["name"] for m, p in a.sent if m == "session/new" for s in p["mcpServers"]]


def test_codex_gets_no_meshpool_servers_for_a_chat_that_needs_no_office_file(home, tmp_path):
    a = _agent(tmp_path, office=False)
    a._ensure_session()
    assert _servers(a) == []


def test_codex_gets_the_office_server_only_for_an_office_chat(home, tmp_path):
    a = _agent(tmp_path, office=True)
    a._ensure_session()
    assert _servers(a) == ["meshpool-office"]


def _write_cfg():
    from meshpool_harness.agent import mcp as M
    own = lambda m: {"command": "python", "args": ["-m", f"meshpool_harness.agent.{m}"]}   # noqa: E731
    M.config_file().write_text(json.dumps({"mcpServers": {
        "browser": own("browser_mcp_server"), "meshpool-assistant": own("assistant_mcp_server"),
        "meshpool-web": own("web_mcp_server"), "meshpool-tools": own("demo_mcp_server"),
        "mine": {"command": "echo", "args": ["hi"]}}}))


def test_codex_keeps_servers_the_user_added_and_drops_meshpools_own(home, tmp_path):
    _write_cfg()
    a = _agent(tmp_path, office=False)
    a._ensure_session()
    assert _servers(a) == ["mine"]
    b = _agent(tmp_path, office=True)
    b._ensure_session()
    assert _servers(b) == ["mine", "meshpool-office"]


def test_codex_session_gets_an_explicit_title_so_it_writes_none(home, tmp_path):
    a = _agent(tmp_path)
    a._ensure_session()
    names = [m for m, _ in a.sent]
    assert names.index("session/set_title") > names.index("session/new")
    p = dict(a.sent)["session/set_title"]
    assert p == {"sessionId": "s1", "title": "Add a Value column to inventory.xlsx"}


def test_codex_title_refused_falls_back_silently(home, tmp_path):
    a = _agent(tmp_path)
    a.refuse_title = True
    a._ensure_session()                                           # no exception
    assert a.acp_session == "s1"


def test_an_office_request_later_in_a_codex_chat_reopens_the_session_with_the_office_server(home, tmp_path):
    a = _agent(tmp_path, office=False)
    a._ensure_session()
    a._p = type("P", (), {"poll": lambda self: None})()
    a.capabilities = {"loadSession": True}
    a.office_on = True
    a._ensure_session()
    loads = [p for m, p in a.sent if m == "session/load"]
    assert loads and [s["name"] for s in loads[0]["mcpServers"]] == ["meshpool-office"]


# ---- 5. capabilities.needs / note / first-message block ----------------------------------------------------------
def test_editing_an_existing_file_is_not_pointed_at_the_engines():
    assert CAP.needs(EDIT) == [] and CAP.edits(EDIT)
    note = CAP.note(["edit"])
    assert "edit_office_file" in note and "openpyxl" in note
    assert "make_spreadsheet" not in note.replace("Do not rebuild it with make_spreadsheet", "")
    assert len(note) < 400


def test_making_a_new_file_still_gets_the_engines_and_a_short_note():
    caps = CAP.needs("Create a new spreadsheet with a monthly budget")
    assert caps == ["spreadsheet"] and not CAP.edits("Create a new spreadsheet with a monthly budget")
    note = CAP.note(caps)
    assert "make_spreadsheet" in note and len(note) < 700
    assert CAP.needs("Make a deck about Q3") == ["presentation"]


def test_first_message_block_is_small(home):
    builtin = A.context_block("t", rules=False)                     # Meshpool's agents: rules are in their system prompt
    assert A.HONESTY not in builtin and A.PLAIN not in builtin
    assert len(builtin) <= 1400
    codex = A.context_block("t", tools="none", office=False)         # outside agent without Meshpool's servers
    assert A.HONESTY in codex and A.PLAIN in codex                   # kept once for outside harnesses
    assert "memory_save" not in codex and "delegate_task" not in codex and len(codex) <= 1500
    pi = A.context_block("t", tools="deferred")
    assert len(pi) <= 2600
    assert len(A.HONESTY) + len(A.PLAIN) <= 1150 and len(A.ENGINES) <= 560


def test_honesty_and_plain_keep_their_rules():
    for needle in ("I don't know", "N/A", "estimate", "was not"):
        assert needle in A.HONESTY
    for needle in ("em dash", "en dash", "emoji", "delve", "answer first".replace("answer first", "the answer first"),
                   "one idea per paragraph"):
        assert needle in A.PLAIN


# ---- 6. office tool descriptions ---------------------------------------------------------------------------------
def test_office_tool_descriptions_stay_short():
    t = OM.TOOLS
    assert len(t["make_deck"][1]) <= 360
    for name, (_f, desc, _p, _r) in t.items():
        assert len(desc) <= 400, name
    size = sum(len(json.dumps({"name": n, "description": d, "parameters": p})) for n, (_f, d, p, _r) in t.items())
    assert size <= 9000                                              # was 10.9k chars for 16 tools


def test_edit_office_file_lists_its_operations_when_none_given(home, tmp_path):
    from openpyxl import Workbook
    p = tmp_path / "proj" / "a.xlsx"
    wb = Workbook()
    wb.save(p)
    text, err = OM.edit_office_file(str(p), [])
    assert err and "tracked_replace" in text and "set_cell" in text


# ---- 7. lazy loading of Meshpool's own servers in the Work profile ------------------------------------------------
def _sch(server, tool):
    return {"type": "function", "function": {"name": f"mcp__{server}__{tool}", "description": "x", "parameters": {}}}


def test_work_profile_defers_the_other_meshpool_servers_and_keeps_office_in_full():
    ms = ([_sch("meshpool_office", t) for t in ("make_deck", "edit_office_file", "read_document")]
          + [_sch("browser", f"b{i}") for i in range(15)] + [_sch("meshpool_assistant", f"a{i}") for i in range(8)]
          + [_sch("meshpool_tools", "calculate")])
    assert MA.LazyTools.wanted(ms, "work")
    shown, hidden = MA.LazyTools("work").split(ms)
    assert {s["function"]["name"].split("__")[1] for s in shown} == {"meshpool_office"}
    assert len(hidden) == 24
    # every other profile keeps Meshpool's own tools in full, as before
    assert not MA.LazyTools.wanted(ms) and not MA.LazyTools.wanted(ms, "code")
    shown2, hidden2 = MA.LazyTools().split([s for s in ms if "browser" not in s["function"]["name"]])
    assert not [h for h in hidden2 if "meshpool" in h["function"]["name"]]


# ---- 8. a named output file lands where the user said ---------------------------------------------------------------
def test_make_pdf_writes_the_named_file_in_the_project_folder(home, tmp_path):
    proj = tmp_path / "proj"
    text, err = OM.make_pdf("# Hello\n\nbody", "report.pdf")
    assert not err and (proj / "report.pdf").is_file()
    assert not (proj / "Meshpool outputs" / "report.pdf").exists()
    text, err = OM.make_pdf("# Hello again", "report.pdf")             # never replaces the file that is there
    assert (proj / "report (2).pdf").is_file()
    OM.make_pdf("# In a folder", "out/q3.pdf")
    assert (proj / "out" / "q3.pdf").is_file()


def test_unnamed_files_and_paths_outside_the_project_go_to_the_top_of_the_folder(home, tmp_path):
    """Rescore: no "Meshpool outputs" folder of meshpool's own in the user's folder."""
    proj = tmp_path / "proj"
    OM.make_pdf("# a")
    assert (proj / "document.pdf").is_file()
    OM.make_document("# a", "document.docx")                           # the tool's own default name: not "named"
    assert (proj / "document.docx").is_file()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    OM.make_pdf("# a", str(elsewhere / "x.pdf"))
    assert not (elsewhere / "x.pdf").exists() and (proj / "x.pdf").is_file()
    assert not (proj / "Meshpool outputs").exists()


def test_make_document_and_convert_name_their_output(home, tmp_path):
    proj = tmp_path / "proj"
    OM.make_document("# Memo\n\ntext", "memo.docx", _clarity=False)
    assert (proj / "memo.docx").is_file()
    (proj / "notes.md").write_text("# N\n\nhello")
    OM.convert_document("notes.md", "pdf", output="notes-final.pdf")
    assert (proj / "notes-final.pdf").is_file()
    OM.convert_document("notes.md", "pdf")                             # no name: beside it, under its own name
    assert (proj / "notes.pdf").is_file() and not (proj / "Meshpool outputs").exists()


# ---- Codex: a request to change a file gets the six file tools, not sixteen ----------------------------------------
def test_codex_edit_chat_gets_only_the_file_tools(home, tmp_path, monkeypatch):
    a = _agent(tmp_path, office=True)
    a.office_edit = True
    a._ensure_session()
    srv = [s for m, p in a.sent if m == "session/new" for s in p["mcpServers"]][0]
    assert srv["name"] == "meshpool-office"
    assert {"name": "MH_OFFICE_TOOLS", "value": "edit"} in srv["env"]
    monkeypatch.setenv("MH_OFFICE_TOOLS", "edit")
    names = set(OM.tools())
    assert "edit_office_file" in names and "check_deck" in names and names.isdisjoint({"make_deck", "find_icon", "make_pdf"})
    monkeypatch.delenv("MH_OFFICE_TOOLS")
    assert {"make_deck", "edit_office_file"} <= set(OM.tools())
    # a later request to make a file reopens the session with the full set
    a._p = type("P", (), {"poll": lambda self: None})()
    a.capabilities = {"loadSession": True}
    a.office_edit = False
    a._ensure_session()
    load = [p for m, p in a.sent if m == "session/load"][0]
    assert not any(e["name"] == "MH_OFFICE_TOOLS" for e in load["mcpServers"][0]["env"])
