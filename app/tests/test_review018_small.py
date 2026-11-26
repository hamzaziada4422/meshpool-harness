"""Release review nits, 0.0.18: small fixes with small tests.

- steps_retried is in the transcript keep list (_KEEP), so it is saved soon after it is shown.
- The permissions note is not empty on Linux, and the page hides an empty one.
- An outside engine asks before it changes a file open in Office in Full access too.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_steps_retried_is_kept_with_the_transcript():
    from meshpool_harness.ui import agent_api as A
    assert "steps_retried" in A._KEEP
    s = A.Session.__new__(A.Session)
    s.workdir = ""
    s.listeners, saved = [], []
    s.lock = threading.RLock()
    s.changed = threading.Condition(s.lock)
    s.events, s.state, s.queue = [], "idle", []
    for k, v in dict(_open_deltas=False, _open_thoughts=False, _retry_open=False, _interrupting=None, _turn_id=0,
                     _turn_open=False, pendings={}, harness={"type": "x", "id": "x"}).items():
        setattr(s, k, v)
    s._persist = lambda *a, **k: None
    s._persist_soon = lambda *a, **k: saved.append(1)
    s._question_state = lambda ev: None
    s.emit({"type": "steps_retried", "ids": ["t1"]})
    assert saved == [1]


def test_the_permissions_note_is_not_empty_here_and_an_empty_one_is_hidden():
    from meshpool_harness.ui import desktop as D
    assert D.permissions()["note"].strip()
    src = (Path(D.__file__).parent / "app.js").read_text(encoding="utf-8")
    assert '(r.note ? `<li class="pnote">${esc(r.note)}</li>` : "")' in src
    assert '$("#oPermH", card).hidden = !rows.length && !r.note;' in src


def _acp(root: Path, mode: str):
    from meshpool_harness.harnesses import acp as ACP
    a = ACP.ACPAgent.__new__(ACP.ACPAgent)
    a.plan, a.mode, a.root, a._stop = False, mode, root, False
    asked = []
    a.approve = lambda kind, title, body, opts: (asked.append(title), ("keep", ""))[1]
    return a, asked


def test_an_outside_engine_asks_before_changing_a_file_open_in_office_in_full_access(tmp_path):
    (tmp_path / "Budget.xlsx").write_bytes(b"PK")
    (tmp_path / "~$Budget.xlsx").write_bytes(b"lock")
    for mode in ("auto", "edits", "ask"):
        a, asked = _acp(tmp_path, mode)
        note = a._open_file_guard("Add Q4 to Budget.xlsx")
        assert asked and "Budget.xlsx is open in Excel" in asked[0], (mode, asked)
        assert note, mode                                       # the engine is told to leave it
    (tmp_path / "~$Budget.xlsx").unlink()
    a, asked = _acp(tmp_path, "auto")
    assert a._open_file_guard("Add Q4 to Budget.xlsx") == "" and not asked   # nothing open: nothing asked
