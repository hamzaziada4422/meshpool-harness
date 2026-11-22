from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"


def test_mermaid_and_katex_are_bundled_with_licences():
    for f in ("mermaid.min.js", "katex.min.js", "katex.min.css", "LICENSE-mermaid.txt", "LICENSE-katex.txt"):
        assert (UI / "vendor" / f).stat().st_size > 1000
    assert len(list((UI / "vendor" / "fonts").glob("*.woff2"))) >= 10
    css = (UI / "vendor" / "katex.min.css").read_text(encoding="utf-8")
    assert ".woff)" not in css and ".ttf)" not in css          # only the bundled font format is referenced


def test_the_server_serves_them_and_the_chat_loads_them_lazily():
    server = (UI / "server.py").read_text(encoding="utf-8")
    assert "/ui/vendor/" in server and "woff2" in server
    chat = (UI / "chat010.js").read_text(encoding="utf-8")
    assert "mermaid.min.js" in chat and "katex.min.js" in chat and 'securityLevel: "strict"' in chat
    assert 'index.html' and "vendor" not in (UI / "index.html").read_text(encoding="utf-8")      # nothing loads until a reply needs it
