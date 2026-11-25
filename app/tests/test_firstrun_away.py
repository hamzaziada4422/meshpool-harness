"""First-run starters from file names only, the samples folder, and While you were away."""
import json
import os
import time

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import away
from meshpool_harness.ui import firstrun_api as FR


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    (tmp_path / "user").mkdir()
    yield tmp_path


def test_starters_come_from_real_file_names_and_never_read_contents(tmp_path, monkeypatch):
    d = tmp_path / "user" / "Meshpool Projects" / "work"
    d.mkdir(parents=True)
    (d / "Q3_sales.xlsx").write_bytes(b"not really a workbook")
    (d / "meeting-notes.docx").write_bytes(b"not really a document")
    (d / ".hidden.xlsx").write_bytes(b"x")
    opened = []
    real = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real(f, *a, **k))[1])
    r = FR.scan(str(d))
    assert r["files"] == 2
    labels = {s["prompt"] for s in r["starters"]}
    assert "Summarize Q3_sales.xlsx in one slide." in labels and "Turn meeting-notes.docx into an action plan." in labels
    assert not any(x.endswith((".xlsx", ".docx")) for x in opened)


def test_a_folder_the_user_never_gave_is_not_scanned(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.xlsx").write_bytes(b"x")
    assert FR.scan(str(other)) == {"files": 0, "starters": []}


def test_empty_folder_gives_no_starters_and_samples_are_copied(tmp_path):
    d = tmp_path / "user" / "Meshpool Projects" / "empty"
    d.mkdir(parents=True)
    assert FR.scan(str(d))["starters"] == []
    s = FR.samples()
    assert sorted(s["files"]) == sorted(FR.SAMPLES) and "one slide" in s["prompt"]
    assert FR.samples()["files"] == s["files"]                      # again: nothing duplicated or lost


def _task_with_run(text, tid="t1", only=True, ts=None):
    sid = "chat-" + tid + str(int(time.time() * 1000) % 1000000)
    t = A.add_task("Brief", "Do the brief", "daily", only_on_change=only)
    return t, sid


def test_while_you_were_away_reports_changes_only(monkeypatch):
    t = A.add_task("Price watch", "Check the price", "daily", only_on_change=True)
    replies = {"s1": "The price is 10 dollars today. Nothing else moved.",
               "s2": "The price is 10 dollars today. Nothing else moved.",
               "s3": "The price jumped to 14 dollars and the supplier changed the delivery terms."}
    assert t["only_on_change"] is True and t["name"] == "Price watch"
    monkeypatch.setattr(away, "last_reply", lambda sid: replies[sid])

    def run(sid, ts):
        rows = A.tasks()
        for r in rows:
            r["last_thread"], r["last_run"] = sid, ts
        A._save_tasks(rows)
    run("s1", time.time())
    r1 = away.report(with_cards=False)
    assert len(r1["runs"]) == 1 and "10 dollars" in r1["runs"][0]["line"]       # first run: reported
    away.mark_seen()
    time.sleep(0.01)
    run("s2", time.time())
    r2 = away.report(with_cards=False)
    assert r2["runs"] == [] and r2["quiet"] == ["Price watch"] and r2["empty"]    # same result: silence
    away.mark_seen()
    time.sleep(0.01)
    run("s3", time.time())
    r3 = away.report(with_cards=False)
    assert len(r3["runs"]) == 1 and "14 dollars" in r3["runs"][0]["line"]


def test_a_task_without_the_option_always_reports(monkeypatch):
    A.add_task("Daily brief", "Do it", "daily")
    monkeypatch.setattr(away, "last_reply", lambda sid: "Same text every day, nothing new today.")
    for sid in ("a", "b"):
        rows = A.tasks()
        for r in rows:
            r["last_thread"], r["last_run"] = sid, time.time()
        A._save_tasks(rows)
        assert len(away.report(with_cards=False)["runs"]) == 1
        away.mark_seen()
        time.sleep(0.01)


def test_the_option_is_stored_and_updatable():
    t = A.add_task("X", "Y", "daily", only_on_change=True)
    assert A.tasks()[0]["only_on_change"] is True
    A.update_task(t["id"], only_on_change=False)
    assert A.tasks()[0]["only_on_change"] is False
    assert A.add_task("Z", "W", "daily")["only_on_change"] is False
