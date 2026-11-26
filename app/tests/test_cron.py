"""Cron expressions for scheduled tasks."""
from datetime import datetime

import pytest

from meshpool_harness import cron as CR


@pytest.mark.parametrize("expr,after,want", [
    ("30 8 * * 1-5", datetime(2026, 11, 27, 9, 0), datetime(2026, 11, 30, 8, 30)),     # Fri after 8:30 -> Mon
    ("0 */6 * * *", datetime(2026, 11, 25, 7, 10), datetime(2026, 11, 25, 12, 0)),
    ("15 9 1 * *", datetime(2026, 11, 25, 0, 0), datetime(2026, 12, 1, 9, 15)),
    ("0 0 29 feb *", datetime(2026, 3, 1), datetime(2028, 2, 29, 0, 0)),              # the next leap day
    ("0 12 * * sun", datetime(2026, 11, 25), datetime(2026, 11, 29, 12, 0)),
    ("0 12 * * 7", datetime(2026, 11, 25), datetime(2026, 11, 29, 12, 0)),             # 7 is Sunday too
    ("0 9 13 * fri", datetime(2026, 11, 1), datetime(2026, 11, 6, 9, 0)),              # either day field
])
def test_next_time(expr, after, want):
    assert CR.next_after(expr, after) == want


@pytest.mark.parametrize("bad", ["", "* * * *", "61 * * * *", "* * * * mon-xyz", "*/0 * * * *", "5-1 * * * *",
                                 "*/5 * * * *"])
def test_bad_or_too_frequent_expressions_are_refused(bad):
    with pytest.raises(CR.CronError):
        CR.validate(bad)


def test_a_cron_task(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    from meshpool_harness import assistant as A
    t = A.add_task("Weekday brief", "Summarize the news", "cron", cron="30  8 * * 1-5")
    assert t["cron"] == "30 8 * * 1-5" and t["next_run"] and "on the schedule 30 8 * * 1-5" in A.describe(t)
    assert datetime.fromtimestamp(t["next_run"]).strftime("%H:%M") == "08:30"
    with pytest.raises(ValueError, match="five fields"):
        A.add_task("x", "y", "cron", cron="every day")
