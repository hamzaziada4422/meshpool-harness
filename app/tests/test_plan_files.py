"""Plan mode replies are also kept as Markdown files (MH_PLANS_DIR)."""
import json

from meshpool_harness.agent import core as K


def _reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, False


def test_a_plan_is_saved_as_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    monkeypatch.setattr(K, "SESSIONS", tmp_path / "sessions")
    plan = "1. Read the parser.\n2. Add the flag.\n3. Write the tests and run them.\n" + "Details. " * 10
    monkeypatch.setattr(K.Agent, "_post", lambda self, base, key, body, stream: _reply(plan))
    events = []
    a = K.Agent(str(tmp_path), mode="ask", emit=events.append)
    a.plan, a.title = True, "Add a verbose flag"
    a.send("plan the verbose flag")
    files = list((tmp_path / "home" / "plans").glob("*-add-a-verbose-flag.md"))
    assert len(files) == 1 and "Write the tests" in files[0].read_text() and a.last_plan_file == str(files[0])
    assert any(e.get("type") == "status" and "Plan saved to" in e.get("text", "") for e in events)
    a.plan = False                                          # not in plan mode: nothing saved
    a.send("go")
    assert len(list((tmp_path / "home" / "plans").glob("*.md"))) == 1


def test_plans_folder_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MH_PLANS_DIR", str(tmp_path / "myplans"))
    assert K.plans_dir() == tmp_path / "myplans"
    monkeypatch.setenv("MH_PLANS_DIR", "off")
    assert K.plans_dir() is None
