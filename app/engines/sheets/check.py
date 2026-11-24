"""CLI: python -m engines.sheets.check file.xlsx  -> prints PASS or the issues; exit 1 on issues.

Runs the same gate as the engine's finish (engines.sheets.engine.finish_check: workbook QA, Checks tab, Cover tab,
forge workbook rules, input sources, model gaps), so PASS here means finish will accept the workbook."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        print("usage: python -m engines.sheets.check <file.xlsx>")
        return 2
    from engines.sheets.engine import finish_check
    path = os.path.abspath(argv[0])
    ws = SimpleNamespace(root=Path(path).parent)     # finish_check uses ws.root only to resolve relative paths
    problem = finish_check("", [path], ws)
    if problem:
        print(problem)
        return 1
    from engines.sheets.qa import check
    st = check(path)["stats"]
    print(f"PASS: {st['formulas']} formulas, sheets {st['sheets']}, named ranges {st['named_ranges']}"
          + ("" if st.get("recalculated") else
             f" (structure only: formula values were not recalculated ({_why_not_recalculated()}), so they and the "
             "Checks tab results are unverified)"))
    return 0


def _why_not_recalculated() -> str:
    """The real reason: no soffice at all, or a system copy Meshpool deliberately does not use, or a failed run."""
    from meshpool_harness.office_runtime import unavailable_reason
    return unavailable_reason() or "the LibreOffice recalculation did not complete"


if __name__ == "__main__":
    sys.exit(main())
