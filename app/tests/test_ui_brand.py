"""The UI is Meshpool's own: no other company's name or brand colour in the interface files. Comments may name sources
the code reads from, and the Models and providers page names the sign-ins it finds on the computer (a tool, an
environment variable): those lines are the only allowed mentions."""
import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "meshpool_harness" / "ui"
FILES = ["app.js", "shell.js", "composer.js", "pages.js", "settings2.js", "projects.js", "company.js", "app.css", "shell.css", "index.html"]
NAME = re.compile(r"claude|anthropic", re.I)
ALLOWED = re.compile(r"claude_code|kind === \"claude\"|ANTHROPIC_API_KEY|Claude Code is installed|^\s*(//|/\*|\*)")


def test_no_other_brand_names_in_the_ui():
    bad = []
    for f in FILES:
        for n, line in enumerate((UI / f).read_text(encoding="utf-8").splitlines(), 1):
            if NAME.search(line) and not ALLOWED.search(line):
                bad.append(f"{f}:{n}: {line.strip()[:100]}")
    assert not bad, "\n".join(bad)


def test_the_other_brand_colour_and_fonts_are_gone():
    css = (UI / "app.css").read_text() + (UI / "shell.css").read_text()
    assert "d37358" not in css.lower() and "anthropic sans" not in css.lower()


def test_the_shell_files_are_served_and_loaded_in_order():
    html = (UI / "index.html").read_text()
    order = [html.index(f"/ui/{f}") for f in ("shell.js", "composer.js", "pages.js", "app.js", "projects.js", "settings2.js", "company.js")]
    assert order == sorted(order), "shell.js, composer.js and pages.js define what app.js boots with"
    assert "/ui/shell.css" in html
    server = (UI / "server.py").read_text()
    for f in ("shell\\.js", "composer\\.js", "pages\\.js", "shell\\.css"):
        assert f in server, f"server.py must serve {f}"
