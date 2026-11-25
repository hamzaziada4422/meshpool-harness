"""Meshpool on Pi sends requests nearly as small as vanilla Pi (docs/results/v4/lean-pi.md).

The size tests start the real Pi (app/vendor/pi) against testkit/lean_pi.py's capture endpoint: no model is called.
They are skipped where Pi is not installed. The rest checks the mechanisms without Pi.
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "testkit"))

from meshpool_harness import assistant as A
from meshpool_harness import capabilities as CAP
from meshpool_harness.harnesses import pi_rpc as PI

needs_pi = pytest.mark.skipif(not PI.available(), reason="Pi is not installed (app/vendor/pi)")

CODE = "Create hello.py that prints hello, then run it."
OFFICE = ("In inventory.xlsx, add a Value column (qty times unit cost) as live formulas. Raise the unit cost of every "
          "item in the Cables category by 10%. Save over the same file.")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("MESHPOOL_HOME", str(h))
    for k in ("MH_PI_SKILLS", "MH_PI_MCP", "MH_ACP_MCP"):
        monkeypatch.delenv(k, raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("MESHPOOL_PROJECT", str(proj))
    return h


# ---- mechanisms (no Pi) -------------------------------------------------------------------------------------------
def test_pi_context_block_is_short_and_has_no_clock_or_office_text_for_a_coding_chat(home, tmp_path):
    t = A.context_block(CODE, str(tmp_path / "proj"), "", chat_id="c", rules=True, tools="deferred", office=False)
    assert len(t) < 450                                      # the date and the short rules, nothing else
    assert "Today is" in t and ":" not in t.split("\n")[0]    # a date, no time of day: the text stays stable
    assert "make_deck" not in t and "tool_search" not in t and "memory_save" not in t
    assert A.HONESTY not in t and A.PLAIN not in t
    assert "remembers about you" not in t                    # no memory or project block when there is none


def test_pi_context_block_names_office_rules_only_when_asked_and_incognito_only_when_on(home, tmp_path):
    root = str(tmp_path / "proj")
    off = A.context_block(OFFICE, root, "", rules=True, tools="deferred", office=True)
    assert "edit_office_file" in off
    inc = A.context_block(CODE, root, "", rules=True, tools="deferred", office=False, incognito=True)
    assert "incognito" in inc and "make_deck" not in inc
    # the other harnesses keep their text
    assert A.HONESTY in A.context_block(CODE, root, "", rules=True, tools="all")


def test_pi_loads_no_skill_for_a_coding_message_and_no_pointer_to_the_index(home, tmp_path):
    root = tmp_path / "proj"
    h = PI.prepare(root, "c1", False, CODE)
    assert not list((h / "skills" / "meshpool").glob("*/SKILL.md"))
    args = PI.skill_args(h)
    assert args == ["--no-skills"]                          # no catalog, no addendum
    h2 = PI.prepare(root, "c2", False, "Build an Excel model of unit economics with live formulas")
    assert any(a == "--skill" for a in PI.skill_args(h2))   # a real match still loads its skill


def test_pi_office_chat_declares_only_the_tools_it_needs(home, tmp_path):
    te = PI._mcp_for_pi(tmp_path / "proj", "c", True, OFFICE)["mcpServers"]["meshpool-office"]["toolExposure"]
    assert set(te) == {"edit_office_file", "read_document", "check_workbook"}
    te = PI._mcp_for_pi(tmp_path / "proj", "c", True, "Edit deck.pptx and notes.docx")["mcpServers"]["meshpool-office"][
        "toolExposure"]
    assert {"check_deck", "check_document"} <= set(te) and "check_workbook" not in te
    plain = PI._mcp_for_pi(tmp_path / "proj", "c", False)["mcpServers"]
    assert not any("toolExposure" in s for s in plain.values())
    assert all(s["exposure"] == "deferred" for s in plain.values())


# ---- sizes against vanilla Pi (real Pi, mock endpoint) -------------------------------------------------------------
def _first_sizes(tmp_path, msg, office):
    import lean_pi as L
    cap = L.Capture()
    try:
        w = tmp_path / "vw"
        w.mkdir()
        L.run_vanilla(cap, msg, w, tmp_path / "vh")
        van = cap.sizes(0)
        cap.rows.clear()
        w2 = tmp_path / "proj"
        L.run_meshpool(cap, msg, w2, tmp_path / "home", office)
        mp = cap.sizes(0)
        again = cap.sizes(len(cap.rows) - 1)
    finally:
        cap.close()
    return van, mp, again


@needs_pi
def test_first_request_for_a_coding_task_is_within_500_tokens_of_vanilla_pi(home, tmp_path):
    van, mp, last = _first_sizes(tmp_path, CODE, False)
    assert mp["total"] - van["total"] <= 500, (van, mp)
    assert mp["n_tools"] == van["n_tools"] + 1               # tool_search, the one entry for all Meshpool tools
    assert last["system"] == mp["system"]                    # nothing is re-sent or changed per turn


@needs_pi
def test_first_request_for_an_office_task_is_within_1500_tokens_of_vanilla_pi(home, tmp_path):
    (tmp_path / "proj").mkdir(exist_ok=True)
    van, mp, last = _first_sizes(tmp_path, OFFICE, True)
    assert mp["total"] - van["total"] <= 1500, (van, mp)
    assert last["system"] == mp["system"]


def _run_script(tmp_path, msg, script, office=False):
    import lean_pi as L
    cap = L.Capture(script=script)
    try:
        w = tmp_path / "proj"
        w.mkdir(exist_ok=True)
        L.run_meshpool(cap, msg, w, tmp_path / "home", office)
        return list(cap.rows)
    finally:
        cap.close()


@needs_pi
def test_memory_tool_works_through_the_deferred_path(home, tmp_path):
    rows = _run_script(tmp_path, "Remember that I prefer EUR.",
                       [("tool_search", {"query": "memory_save remember fact"}),
                        ("memory_save", {"fact": "The user prefers EUR."})])
    names = lambda b: [t["function"]["name"] for t in b["tools"]]       # noqa: E731
    assert not any("memory_save" in n for n in names(rows[0]))          # not declared up front
    assert any(n.endswith("memory_save") for n in names(rows[1]))       # declared after tool_search
    last = [m for m in rows[-1]["messages"] if m["role"] == "tool"][-1]["content"]
    assert "Saved" in last
    from meshpool_harness import memstore as MS
    assert any("EUR" in f["text"] for f in MS.select("EUR", "", "", ()))


@needs_pi
def test_office_tool_works_through_the_deferred_path(home, tmp_path):
    (tmp_path / "proj").mkdir(exist_ok=True)
    rows = _run_script(tmp_path, "Make a deck about our Q3 results.",
                       [("tool_search", {"query": "convert_document make_deck office"}),
                        ("convert_document", {"path": "nothing.docx", "to": "pdf"})], office=True)
    names = lambda b: [t["function"]["name"] for t in b["tools"]]       # noqa: E731
    assert not any("convert_document" in n for n in names(rows[0]))
    assert any(n.endswith("meshpool_office__convert_document") for n in names(rows[1]))
    tool_msgs = [m for m in rows[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and tool_msgs[-1]["content"]              # the office server answered
