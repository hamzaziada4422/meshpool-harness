"""The learning loop: a turn where the user states a standing preference adds it to memory once, the chat hears it
on its next message, and the deck engine's writer reads it."""
import json

import pytest

from meshpool_harness import assistant as A
from meshpool_harness import learn as L
from meshpool_harness.core.client import MockClient


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_LEARN", raising=False)
    A.set_memory("")
    yield


def _client(prefs):
    return MockClient([{"role": "assistant", "content": json.dumps({"preferences": prefs})}])


def test_a_correction_becomes_a_standing_preference_once():
    msg = "This is a hard no. The eyebrow over the title, never include those, never ever."
    got = L.after_turn("c1", msg, "", _client(["Never wants a small label (eyebrow) above slide titles"]), "m")
    assert got == ["Never wants a small label (eyebrow) above slide titles"]
    assert "eyebrow" in A.memory()
    again = L.after_turn("c1", msg, "", _client(["Never wants an eyebrow label above slide titles"]), "m")
    assert again == []                                        # already known, not saved twice
    assert A.memory().count("eyebrow") == 1


def test_the_chat_hears_it_on_its_next_message_only_once():
    L.after_turn("c2", "Always default to Segoe UI from now on", "", _client(["Always wants Segoe UI as the font"]), "m")
    note = L.pending("c2")
    assert "Segoe UI" in note and "follow them from now on" in note
    assert L.pending("c2") == ""


def test_plain_requests_cost_no_model_call():
    c = MockClient([])                                        # an empty script raises if called
    assert L.after_turn("c3", "Make me a deck on coffee prices in Brazil", "", c, "m") == []
    assert c.requests == []


def test_memory_off_learns_nothing():
    A.save_settings(memory_on=False)
    try:
        c = MockClient([])
        assert L.after_turn("c4", "Never use em dashes in anything you write", "", c, "m") == []
    finally:
        A.save_settings(memory_on=True)


def test_tasks_are_not_kept_as_preferences():
    got = L.after_turn("c5", "Make sure you add this to the todo list", "",
                       _client(["Wants the export bug added to the todo list"]), "m")
    assert got == [] and "todo" not in A.memory()


def test_the_deck_writer_reads_the_preferences():
    A.remember("Never wants a small label above slide titles")
    A.remember("Works at a bank in Cairo")                   # a fact, not a preference: not a writer rule
    p = L.preferences()
    assert "Never wants a small label above slide titles" in p and "Cairo" not in p
    from engines.forge import build as B
    assert "small label above slide titles" in B._user_prefs()
