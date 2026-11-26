"""The release workflow's Linux smoke reads `meshpool check` for "ships with meshpool" on Git, Node.js and LibreOffice.
The 0.0.18 design pass lowercased the product name in that output, and the workflow still looked for "ships with
Meshpool", so the Linux release job would have failed. The grep is case-insensitive now, and every bundled row says the
same words."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_release_smoke_reads_the_bundled_rows_in_any_case():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    line = next(l for l in wf.splitlines() if "is not the bundled copy" in l)
    assert "grep -iE" in line or "grep -Ei" in line, line
    pat = re.search(r'grep -\w+ "(.*?)"', line).group(1)
    for t, row in (("Git ", "[ok] Git             /tmp/Meshpool/git/bin/git (ships with meshpool)"),
                   ("LibreOffice ", "[ok] LibreOffice     /tmp/Meshpool/app/vendor/libreoffice/program/soffice "
                                    "(the copy that ships with meshpool)")):
        assert re.search(pat.replace("$t", t), row, re.I), (pat, row)


def test_the_bundled_rows_name_the_product_in_lowercase():
    src = (ROOT / "app" / "meshpool_harness" / "config.py").read_text(encoding="utf-8")
    src += (ROOT / "app" / "meshpool_harness" / "office_runtime.py").read_text(encoding="utf-8")
    assert "ships with Meshpool\"" not in src and "ships with Meshpool)" not in src
