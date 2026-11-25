"""Secrets in shown tool output are hidden; what the model reads is not."""
import pytest

from meshpool_harness.redact import HIDDEN, redact


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-" + "a" * 40, "sk-proj-" + "B" * 30, "ghp_" + "c" * 36, "github_pat_" + "d" * 50,
    "AKIAIOSFODNN7EXAMPLE", "xoxb-1234567890-abcdefghij", "AIza" + "e" * 35, "glpat-" + "f" * 20,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
])
def test_known_key_shapes_are_hidden(secret):
    out = redact(f"found {secret} in config")
    assert secret not in out and HIDDEN in out and out.startswith("found ")


@pytest.mark.parametrize("line,keep", [
    ("OPENAI_API_KEY=abcdef123456", "OPENAI_API_KEY="),
    ('"client_secret": "zzzzzzzzzzzz"', '"client_secret": "'),
    ("DB_PASSWORD: hunter22hunter", "DB_PASSWORD: "),
    ("Authorization: Bearer abcdefghijklmnop", "Authorization: Bearer "),
    ("postgres://app:s3cretpass@db:5432/x", "postgres://app:"),
])
def test_named_values_are_hidden_and_the_name_stays(line, keep):
    out = redact(line)
    assert out.startswith(keep) and HIDDEN in out
    assert not any(v in out for v in ("abcdef123456", "zzzzzzzzzzzz", "hunter22hunter", "abcdefghijklmnop", "s3cretpass"))


def test_private_keys_are_hidden():
    pem = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"
    assert redact("key:\n" + pem) == "key:\n" + HIDDEN


def test_ordinary_output_is_untouched_and_it_can_be_turned_off(monkeypatch):
    text = "tests passed: 12\ntoken count 1200\nsk-short\nmy_function(api)"
    assert redact(text) == text
    monkeypatch.setenv("MH_REDACT", "0")
    assert redact("OPENAI_API_KEY=abcdef123456") == "OPENAI_API_KEY=abcdef123456"


def test_the_model_still_reads_the_real_output(tmp_path, monkeypatch):
    from meshpool_harness.agent import core
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "h"))
    monkeypatch.setattr(core, "SESSIONS", tmp_path / "s")
    monkeypatch.setenv("MESHPOOL_API_KEY", "k")
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / ".env").write_text("STRIPE_KEY=sk_live_" + "x" * 24 + "\nAPI_TOKEN=abcdef123456\n")
    events = []
    a = core.Agent(str(tmp_path / "p"), mode="auto", emit=events.append)
    import json
    a._turn_no = 1
    out = a._run_tool({"id": "c1", "type": "function", "function": {"name": "read_file",
                                                                    "arguments": json.dumps({"path": ".env"})}})
    shown = [e for e in events if e.get("type") == "tool_end"]
    assert "abcdef123456" in out and "abcdef123456" not in shown[-1]["output"]


def test_web_site_lists(monkeypatch):
    from meshpool_harness import web as WEB
    monkeypatch.setenv("MH_FETCH_DENY", "evil.example, *.tracker.io")
    for u in ("https://evil.example/x", "https://a.evil.example/", "http://cdn.tracker.io/p"):
        with pytest.raises(WEB.WebError, match="never read"):
            WEB.check_domain(u)
    WEB.check_domain("https://docs.python.org/3/")
    monkeypatch.setenv("MH_FETCH_ALLOW", "python.org,github.com")
    WEB.check_domain("https://docs.python.org/3/")
    with pytest.raises(WEB.WebError, match="read only"):
        WEB.check_domain("https://notgithub.com/")
    monkeypatch.setenv("MH_ALLOW_PRIVATE_FETCH", "1")                     # the lists apply even then
    with pytest.raises(WEB.WebError):
        WEB.check_public("https://example.org/")


def test_the_browser_tool_keeps_to_the_site_lists_too(monkeypatch):
    from meshpool_harness import web as WEB
    from meshpool_harness.agent import browser_mcp_server as BM
    monkeypatch.setenv("MH_FETCH_DENY", "evil.example")
    with pytest.raises(WEB.WebError):
        BM.browser_open("evil.example/login")
