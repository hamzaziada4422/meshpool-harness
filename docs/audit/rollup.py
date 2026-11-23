"""Roll the per-workstream ledgers (docs/audit/ledger/*.md) up into docs/audit/LEDGER.md.

Each workstream ledger has rows: | ID | status | root cause | fix | files | test |
The master ledger keeps one row per finding; this copies status and test across and reports:
- IDs with no row in any workstream ledger (still open),
- IDs with a status outside the allowed set,
- IDs claimed by two workstreams with different statuses.
Exit code 1 when anything is open or inconsistent, so it can gate a release.

Usage: python docs/audit/rollup.py [--check]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOWED = {"fixed", "already fixed", "not reproducible"}
ROW = re.compile(r"^\|\s*([A-Z]+-?[0-9A-Z]*(?:-\d+)?)\s*\|(.*)\|\s*$")


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def read_ws() -> dict[str, list[tuple[str, str, str]]]:
    found: dict[str, list[tuple[str, str, str]]] = {}
    for f in sorted((HERE / "ledger").glob("*.md")):
        for line in f.read_text(encoding="utf-8").splitlines():
            m = ROW.match(line)
            if not m:
                continue
            c = cells(line)
            if len(c) < 2 or c[0] in ("ID", "---") or set(c[0]) <= {"-"}:
                continue
            status = c[1].lower().strip("* ")
            test = c[5] if len(c) > 5 else (c[-1] if len(c) > 2 else "")
            found.setdefault(c[0], []).append((f.stem, status, test))
    return found


def main() -> int:
    check = "--check" in sys.argv
    master = HERE / "LEDGER.md"
    lines = master.read_text(encoding="utf-8").splitlines()
    ws = read_ws()
    open_ids, bad, conflict, out = [], [], [], []
    for line in lines:
        c = cells(line) if line.startswith("| ") else []
        if len(c) == 5 and c[1].startswith("ws") and c[0] != "ID":
            rid = c[0]
            rows = ws.get(rid, [])
            if not rows:
                open_ids.append(rid)
            else:
                statuses = {s for _, s, _ in rows}
                if len(statuses) > 1:
                    conflict.append(f"{rid}: {rows}")
                st = rows[0][1]
                if st not in ALLOWED:
                    bad.append(f"{rid}: '{st}' ({rows[0][0]})")
                c[3] = st
                c[4] = "; ".join(t for _, _, t in rows if t)[:200]
                line = "| " + " | ".join(c) + " |"
        out.append(line)
    if not check:
        master.write_text("\n".join(out) + "\n", encoding="utf-8")
    known = {cells(l)[0] for l in lines if l.startswith("| ") and len(cells(l)) == 5}
    extra = sorted(set(ws) - known)
    total = sum(1 for l in lines if l.startswith("| ") and len(cells(l)) == 5 and cells(l)[1].startswith("ws"))
    print(f"{total} findings; {total - len(open_ids)} with a workstream row; open {len(open_ids)}; "
          f"bad status {len(bad)}; conflicting {len(conflict)}; extra rows (new IDs) {len(extra)}")
    for title, items in (("OPEN", open_ids), ("BAD STATUS", bad), ("CONFLICT", conflict), ("EXTRA", extra)):
        if items:
            print(f"{title}: " + ", ".join(items))
    return 1 if (open_ids or bad or conflict) else 0


if __name__ == "__main__":
    sys.exit(main())
