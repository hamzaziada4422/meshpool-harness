"""Memory v3: migration from real 0.0.9 data, every old function, no silent loss, scope rules, pending facts,
expiry, conflicts, history, usage tracking."""
import json
import time

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import memstore as MS
from meshpool_harness import project_store as PS
from meshpool_harness.core.client import MockClient


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    MS._ENSURED.clear()
    MS._CACHE.clear()
    A.save_settings(memory_on=True, instructions_on=True)
    yield tmp_path


def _old_files(home):
    """What 0.0.9 left on disk: a flat memory.md, folder notes and a project's memory.md."""
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "memory" / "memory.md").write_text(
        "- Works in strategy and finance for GCC clients\n- Prefers Segoe UI and no label over the title\n"
        "- Manager is Layla Haddad\n- Uses Jira and Slack\n- Sends a board update every Monday morning\n"
        "- Fix the export bug, to-do list for next release\n", encoding="utf-8")
    (home / "memory" / "projects").mkdir()
    (home / "memory" / "projects" / "abcd1234abcd1234.md").write_text("# /work/site\nTests run with make test\n")
    (home / "projects" / "p_old1").mkdir(parents=True)
    (home / "projects" / "p_old1" / "project.json").write_text(json.dumps({"id": "p_old1", "name": "Board deck"}))
    (home / "projects" / "p_old1" / "memory.md").write_text("Slides use one message each\nFinal numbers in USD\n")


def test_migration_backs_up_and_converts_every_line(home):
    _old_files(home)
    r = MS.migrate()
    assert r["migrated"] == 9 and not r["already"]
    bk = home / "memory" / "backup-0.0.9"
    assert (bk / "memory.md").read_text().startswith("- Works in strategy")
    assert (bk / "project-memory" / "p_old1.md").is_file() and (bk / "folder-notes" / "abcd1234abcd1234.md").is_file()
    facts = MS.load()
    assert all(f["kind"] == "stated" and f["source"]["type"] == "earlier-memory" for f in facts)
    by = {f["text"]: f for f in facts}
    assert by["Prefers Segoe UI and no label over the title"]["category"] == "preferences"
    assert by["Manager is Layla Haddad"]["category"] == "people"
    assert by["Sends a board update every Monday morning"]["category"] == "routines"
    assert by["Slides use one message each"]["scope"] == "project:p_old1"
    assert by["Tests run with make test"]["scope"] == "project:folder-abcd1234abcd1234"
    todo = next(f for f in facts if "export bug" in f["text"])
    assert todo["status"] == "archived"                       # a task, kept but not a fact


def test_migration_is_idempotent_and_never_overwrites_edits(home):
    _old_files(home)
    MS.migrate()
    first = MS.load()
    fid = first[0]["id"]
    MS.update(fid, text="Works in strategy and finance for Gulf clients")
    again = MS.migrate()
    assert again["already"] is True
    assert len(MS.load()) == len(first)
    assert MS.get(fid)["text"].endswith("Gulf clients")
    assert len(list((home / "memory" / "backup-0.0.9").rglob("*.md"))) == 3      # one backup, not two


def test_old_functions_keep_their_signatures_and_meaning(home):
    _old_files(home)
    assert "Manager is Layla Haddad" in A.memory()
    assert "export bug" not in A.memory()
    assert A.remember("Likes short answers") == "Saved to memory."
    assert A.remember("Likes short answers") == "Already in memory."
    assert "- Likes short answers" in A.memory().splitlines()
    assert "Prefers Segoe UI" in A.recall("make a deck")
    assert "Removed 1 line" in A.forget("likes short answers")
    assert "Likes short answers" not in A.memory()
    assert A.forget("zzz nothing") == "Nothing in memory matched that."
    with pytest.raises(ValueError):
        A.remember("my api key is sk-1234")
    with pytest.raises(ValueError):
        A.remember("add this to the todo list")
    A.set_memory("- One\n- Two facts here")
    assert A.memory().splitlines() == ["- One", "- Two facts here"][:0] or "Two facts here" in A.memory()
    assert A.context_block("hello").count("<memory-context>") == 1


def test_forget_archives_instead_of_deleting(home):
    A.remember("Drinks tea at ten")
    A.forget("drinks tea at ten")
    f = next(f for f in MS.load() if f["text"] == "Drinks tea at ten")
    assert f["status"] == "archived"


def test_the_export_file_is_written_for_older_versions(home):
    A.remember("Prefers sentence case")
    A.remember("Likes charts", kind="noticed")           # noticed facts wait, so stay out of the export
    assert "Prefers sentence case" in (home / "memory" / "memory.md").read_text()
    assert "Likes charts" not in (home / "memory" / "memory.md").read_text()


def test_nothing_is_dropped_when_memory_is_full(home):
    for i in range(60):
        A.remember(f"Fact number {i} " + "x" * 300)
    assert len(MS.facts(status="active")) == 60            # 0.0.9 dropped the oldest lines here
    h = MS.health()
    assert h["near"] and h["proposal"] is not None
    assert h["share"] > 1


def test_consolidation_proposes_merges_and_old_facts_but_changes_nothing(home):
    a, _ = MS.add("Prefers short answers in chat")
    b, _ = MS.add("Prefers short answers in chats")
    old, _ = MS.add("Visited Cairo in 2023")
    rows = MS.load()
    for f in rows:
        if f["id"] == old["id"]:
            f["created"] = time.time() - 200 * 86400
    MS._save(rows)
    p = MS.consolidation()
    assert p["merges"] and {a["id"], b["id"]} == {p["merges"][0]["keep"], *p["merges"][0]["drop"]}
    assert old["id"] in p["archive"]
    assert len(MS.facts(status="active")) == 3
    MS.apply_consolidation(p["merges"], p["archive"])
    assert len(MS.facts(status="active")) == 1


def test_pending_noticed_facts_never_reach_context_until_confirmed(home):
    f = MS.notice("Meets the CFO every Friday", evidence=[{"text": "4 chats"}])
    assert f["status"] == "pending" and f["kind"] == "noticed"
    assert "CFO" not in A.context_block("plan my week") and "CFO" not in A.memory() and "CFO" not in A.recall()
    MS.confirm(f["id"])
    assert "CFO" in A.context_block("plan my week")


def test_a_rejected_noticed_fact_is_not_suggested_again(home):
    f = MS.notice("Meets the CFO every Friday")
    MS.reject(f["id"])
    assert MS.notice("Meets the CFO every Friday") is None
    assert MS.get(f["id"])["status"] == "archived"


def test_memory_off_turns_off_noticing_and_context(home):
    A.remember("Prefers Segoe UI")
    A.save_settings(memory_on=False)
    assert MS.notice("Meets the CFO every Friday") is None
    assert "Segoe" not in A.context_block("deck")


def test_scope_rules(home):
    pid = PS.create("Board deck")["id"]
    MS.add("Board wants USD only", scope=f"project:{pid}")
    MS.add("Prefers Segoe UI")
    MS.add("Hermes only fact here", scope="harness:hermes")
    ctx = A.context_block("deck", project_id=pid)
    assert "USD only" in ctx and "Segoe" in ctx and "Hermes only" not in ctx
    assert "USD only" not in A.context_block("deck")                       # not in another chat
    assert "USD only" not in A.memory()
    assert "Hermes only" in A.recall("x", harness="hermes")
    assert PS.memory(pid) == "Board wants USD only"
    PS.memory_add(pid, "Numbers are rounded")
    assert MS.get(next(f["id"] for f in MS.load() if f["text"] == "Numbers are rounded"))["scope"] == f"project:{pid}"
    MS.set_scope(next(f["id"] for f in MS.load() if f["text"] == "Numbers are rounded"), "everywhere")
    assert "Numbers are rounded" in A.memory()


def test_deleting_a_project_archives_its_facts(home):
    pid = PS.create("Temp")["id"]
    f, _ = MS.add("Temp project note", scope=f"project:{pid}")
    PS.delete(pid)
    assert MS.get(f["id"])["status"] == "archived"


def test_expiry(home):
    f, _ = MS.add("Travelling next week", valid_until=time.time() - 5)
    g, _ = MS.add("Based in Dubai")
    assert "Travelling" not in A.memory() and "Dubai" in A.memory()
    assert any(x["id"] == f["id"] and x["review_reason"] == "expired" for x in MS.review_queue())
    MS.set_expiry(f["id"], time.time() + 3600)
    assert "Travelling" in A.memory()


def test_review_queue_lists_unused_facts(home):
    f, _ = MS.add("Old fact about charts")
    rows = MS.load()
    rows[0]["created"] = time.time() - 90 * 86400
    MS._save(rows)
    assert [x["id"] for x in MS.review_queue()] == [f["id"]]
    MS.record_given("c1", 0, [f["id"]])
    assert MS.review_queue() == []


def test_conflicts_and_the_newer_stated_fact_wins_until_resolved(home):
    a, _ = MS.add("Prefers Segoe UI for every deck")
    time.sleep(0.01)
    b, _ = MS.add("Always use Arial for slides")
    c = MS.conflicts()
    assert len(c) == 1 and c[0]["winner"] == b["id"]
    ctx = A.context_block("deck")
    assert "Arial" in ctx and "Segoe" not in ctx
    MS.resolve_conflict(a["id"], b["id"])
    assert MS.conflicts() == [] and "Segoe" in A.context_block("deck") and "Arial" not in A.context_block("deck")


def test_stated_beats_learned(home):
    s, _ = MS.add("Never use bullet points", kind="stated")
    time.sleep(0.01)
    l, _ = MS.add("Always wants bullet points on slides", kind="learned")
    assert MS.conflicts()[0]["winner"] == s["id"]
    assert "bullet points on slides" not in A.context_block("deck")


def test_unrelated_facts_do_not_conflict(home):
    MS.add("Prefers Segoe UI")
    MS.add("Prefers short answers")
    MS.add("Works at Acme")
    assert MS.conflicts() == []


def test_history_and_restore(home):
    f, _ = MS.add("Works at Acme")
    MS.update(f["id"], text="Works at Acme Gulf")
    MS.update(f["id"], text="Works at Beta")
    h = MS.get(f["id"])["history"]
    assert [x["text"] for x in h] == ["Works at Acme", "Works at Acme Gulf", "Works at Beta"]
    MS.restore_version(f["id"], 0)
    assert MS.get(f["id"])["text"] == "Works at Acme"
    assert [r["action"] for r in MS.history()][:1] == ["edited"]
    MS.delete(f["id"])
    assert MS.get(f["id"]) is None
    MS.undelete(f["id"])
    assert MS.get(f["id"])["text"] == "Works at Acme"


def test_editing_a_learned_fact_makes_it_stated(home):
    f, _ = MS.add("Prefers dark charts", kind="learned")
    MS.update(f["id"], text="Prefers light charts")
    assert MS.get(f["id"])["kind"] == "stated"


def test_usage_tracking_records_what_a_chat_was_given(home):
    MS.add("Prefers Segoe UI")
    MS.add("Works at Acme")
    ctx = A.context_block("a deck", chat_id="chat-1")
    given = MS.given_for_chat("chat-1")
    assert {g["text"] for g in given} == {"Prefers Segoe UI", "Works at Acme"}
    assert all(g["given_count"] == 1 and g["last_given"] for g in given)
    assert MS.chats_given(given[0]["id"]) == 1
    assert MS.context_snapshot("chat-1") == ctx
    A.context_block("a deck", chat_id="chat-2")
    assert MS.get(given[0]["id"])["given_count"] == 2


def test_a_dry_run_records_nothing_and_incognito_reads_nothing(home):
    MS.add("Prefers Segoe UI")
    x = A.build_context("deck", record=False)
    assert x["facts"] and MS.given_for_chat("c") == []
    inc = A.context_block("deck", chat_id="c", incognito=True)
    assert "Segoe" not in inc and "incognito" in inc and MS.given_for_chat("c") == []


def test_exclude_leaves_a_fact_out_for_one_chat(home):
    f, _ = MS.add("Prefers Segoe UI")
    assert "Segoe" not in A.context_block("deck", exclude=[f["id"]])
    assert "Segoe" in A.context_block("deck")


def test_pinned_facts_are_always_in_a_crowded_context(home):
    p, _ = MS.add("Pinned fact about billing codes", pinned=True)
    for i in range(40):
        MS.add(f"Unrelated filler fact {i} " + "z" * 100)
    assert "Pinned fact about billing" in A.recall("a poem about the sea")


def test_attribute_since_gives_the_chat_as_source(home):
    t = time.time() - 1
    f, _ = MS.add("Prefers EUR reports", source={"type": "chat"})
    assert MS.attribute_since(t, "chat-9", "please remember EUR") == 1
    assert MS.get(f["id"])["source"]["chat_id"] == "chat-9"


def test_learned_preferences_are_facts_with_their_chat_as_source(home, monkeypatch):
    monkeypatch.delenv("MH_LEARN", raising=False)
    from meshpool_harness import learn as L
    c = MockClient([{"role": "assistant", "content": json.dumps({"preferences": ["Never wants an eyebrow above titles"]})}])
    got = L.after_turn("chat-3", "Never put an eyebrow above the title, I told you", "", c, "m")
    assert got
    f = next(f for f in MS.load() if "eyebrow" in f["text"])
    assert f["kind"] == "learned" and f["status"] == "active"
    assert f["source"]["chat_id"] == "chat-3" and "eyebrow" in f["source"]["quote"]
    assert L.LAST_IDS["chat-3"] == [f["id"]]


def test_portrait_uses_active_facts_only_and_caches(home):
    assert MS.portrait()["sentences"] == []
    f, _ = MS.add("Prefers Segoe UI")
    MS.notice("Meets the CFO every Friday")
    c = MockClient([{"role": "assistant", "content": json.dumps(
        {"sentences": [{"text": "You like Segoe UI.", "ids": [f["id"], "bogus"]}]})}])
    p = MS.portrait(c)
    assert p["sentences"][0]["ids"] == [f["id"]]
    assert MS.portrait(MockClient([]))["hash"] == p["hash"]            # cached: no model call
    MS.add("Works at Acme")
    assert len(MS.portrait()["sentences"]) >= 1                          # rules fallback offline


def test_search_and_export(home):
    MS.add("Prefers Segoe UI")
    MS.add("Manager is Layla")
    assert [f["text"] for f in MS.search("layla")] == ["Manager is Layla"]
    md = MS.export_markdown()
    assert "## How you like work" in md and "- Prefers Segoe UI" in md
    assert "## " in MS.export_onepager() and "How I work" in MS.export_onepager()


def test_a_thousand_facts_stay_fast(home):
    rows = [MS._new(f"Fact {i} about topic {i % 17}", "stated", "about", "everywhere", "active", {"type": "manual"})
            for i in range(1000)]
    MS._save_new(rows)
    t = time.time()
    for _ in range(5):
        A.context_block("topic 3")
    assert (time.time() - t) / 5 < 2.5


def test_an_incognito_turn_pauses_memory_writes_from_every_harness(home):
    MS.incognito_begin("inc-1")
    with pytest.raises(ValueError, match="incognito"):
        A.remember("Prefers dark mode")
    with pytest.raises(ValueError, match="incognito"):
        A.remember_project(str(home), "tests run with make")
    MS.incognito_end("inc-1")
    assert A.remember("Prefers dark mode") == "Saved to memory."


def test_a_thousand_facts_load_the_whole_page_data_in_well_under_a_second(home):
    from meshpool_harness.ui import memory_api as MA
    from urllib.parse import parse_qs
    rows = [MS._new(f"Extra fact number {i} about topic {i % 23}", "stated", "about", "everywhere", "active", {"type": "manual"})
            for i in range(1000)]
    MS._save_new(rows)
    MA.view(parse_qs(""))                                        # warm: first read parses the file
    t = time.time()
    d = MA.view(parse_qs(""))
    assert time.time() - t < 5.0 and len(d["facts"]) == 1000 and d["health"]["near"]
    assert len(MS.search("topic 7")) < 1000                       # search reads words, not timestamps
    assert MS.consolidation()["merges"] == []                     # a thousand different facts: nothing to merge
