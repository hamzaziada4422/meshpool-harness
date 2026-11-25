"""Count the skipped tests of a pytest run (its --junitxml report) and fail on the skips CI must not have.

PA-skills-16: tests skip themselves when LibreOffice, Chromium, Playwright or jsonschema is missing. The CI job
installs all of them, so such a skip there means an install step broke and the tests it guards did not run, while
the job still showed green. This prints every skip, grouped by reason (and adds them to the job summary on GitHub),
and exits 1 when a skip says one of those installed tools is missing. Other skips (a private knowledge base, bwrap,
a Windows-only test) are listed but allowed.

Usage: python packaging/ci_skips.py REPORT.xml
"""
from __future__ import annotations

import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

# Skip reasons that mean a tool the CI job installs is missing. Worded on the "not there" phrases only: a skip that
# merely notes which browser launches ("Playwright's own Chromium is not what launches here") is not one of them.
MUST_NOT_SKIP = re.compile(
    r"LibreOffice (is )?not installed|LibreOffice needed|needs LibreOffice"
    r"|no Chromium|browser did not start|PLAYWRIGHT_BROWSERS_PATH is not set"
    r"|could not import '(playwright|jsonschema)", re.I)


def skips(xml_path: str) -> list[tuple[str, str]]:
    """[(test id, reason)] for every skipped test case in a junit XML report."""
    out = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        sk = case.find("skipped")
        if sk is None:
            continue
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        reason = (sk.get("message") or sk.text or "").strip()
        out.append((name, re.sub(r"\s+", " ", reason)))
    return out


def check(xml_path: str) -> tuple[int, str]:
    """(exit code, report text)."""
    rows = skips(xml_path)
    bad = [(n, r) for n, r in rows if MUST_NOT_SKIP.search(r)]
    lines = [f"{len(rows)} skipped test(s); {len(bad)} because a tool this job installs is missing."]
    for reason, n in Counter(r for _, r in rows).most_common():
        lines.append(f"  {n:4d}  {reason[:200]}")
    if bad:
        lines.append("These must run in CI (check the install steps):")
        lines += [f"  {n}: {r[:200]}" for n, r in bad]
    return (1 if bad else 0), "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    code, text = check(argv[1])
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("### Skipped tests\n\n```\n" + text + "\n```\n")
        except OSError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
