import pytest
from meshpool_harness.core import guards as G

CMD = "python check.py"


def mk():
    return G.Guards({"task_id": "t"}, None, require_check=False)


def run_seq(g, outs, cmd=CMD):
    return [g.after_command(cmd, 1, o) for o in outs]


def fail(n):
    return f"checking...\nFAIL: {n} issues; stats {{'rows': 40, 'bad': {n}}}\n"


def test_decreasing_issue_counts_not_identical():
    g = mk()
    msgs = run_seq(g, [fail(n) for n in (7, 6, 5, 4, 3)])
    assert all(m is None for m in msgs)


def test_identical_aborts_on_fifth_warns_on_third():
    g = mk()
    msgs = [g.after_command(CMD, 1, fail(7)) for _ in range(4)]
    assert msgs[0] is None and msgs[1] is None and msgs[3] is None
    assert "repeat_failure" in msgs[2] and "3 times" in msgs[2]
    with pytest.raises(G.Abort):
        g.after_command(CMD, 1, fail(7))


@pytest.mark.parametrize("tmpl", [
    "Error: boom at 0x{h}\n",
    "Error: boom after {a}.{b}s\n",
    "Error: boom in {a} ms\n",
    "Error: boom in {a}.5 seconds\n",
    "Error: cannot open /tmp/tmp{a}x/out.csv\n",
    "Error: boom at 12:{b}:{c}.123\n",
    "Error: boom at 2026-01-0{d}T10:{b}:{c}Z\n",
    "Error: boom pid {a}\n",
])
def test_volatile_tokens_normalised(tmpl):
    g = mk()
    outs = [tmpl.format(a=str(1000 + i * 37), b=f"{10 + i:02d}", c=f"{20 + i:02d}",
                        d=i + 1, h=f"{0x7fa0 + i * 4096:x}") for i in range(5)]
    with pytest.raises(G.Abort):
        run_seq(g, outs)


def test_consecutive_reset():
    g = mk()
    A, B = fail(7), fail(3)
    msgs = run_seq(g, [A, A, B, A, A])
    assert all(m is None for m in msgs)


def test_pass_clears():
    g = mk()
    A = fail(7)
    run_seq(g, [A, A])
    assert g.after_command(CMD, 0, "ok") is None
    msgs = run_seq(g, [A, A, A])
    assert msgs[2] and "repeat_failure" in msgs[2]  # third consecutive after the pass
    g.after_command(CMD, 1, A)
    with pytest.raises(G.Abort):
        g.after_command(CMD, 1, A)


def test_no_error_lines_uses_exit_and_tail():
    g = mk()
    with pytest.raises(G.Abort):
        run_seq(g, ["something odd\n"] * 5)


def test_switch_disabled(monkeypatch):
    monkeypatch.setenv("MH_GUARDS", "none")
    g = mk()
    assert all(m is None for m in run_seq(g, [fail(7)] * 6))
