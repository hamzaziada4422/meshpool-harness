"""Keep test runs from writing into the package folder (the failure catalog defaults to it)."""
import os
import sys

os.environ.setdefault("MH_FAILURES_LOG", "off")
os.environ.setdefault("MH_RECOVER_MIN", "0")        # an outage in a test fails at once instead of waiting 30 min
os.environ.setdefault("MH_GRADER", "0")            # the independent grader has its own tests (test_grader.py); off elsewhere
os.environ.setdefault("MH_LEARN", "0")
os.environ.setdefault("MH_UI_OPEN_PAGE", "1")      # UI tests load / directly; test_ui_launch_key.py covers the key            # the learning loop calls a model after a turn: off in tests

import pytest  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "needs_kb: needs the private slide knowledge base (MESHPOOL_KB); skipped when absent")


def pytest_collection_modifyitems(config, items):
    """Tests marked needs_kb are skipped when MESHPOOL_KB is unset or missing, so CI stays green without it."""
    kb = os.environ.get("MESHPOOL_KB", "")
    if kb and os.path.isdir(kb):
        return
    skip = pytest.mark.skip(reason="MESHPOOL_KB not set: the slide knowledge base is private and not in CI")
    for it in items:
        if "needs_kb" in it.keywords:
            it.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _no_slot_files_in_real_temp():
    """A whole run leaves nothing new in the slot folder real chats share for their MH_MAX_INFLIGHT cap."""
    from meshpool_harness.core import gateway as GW
    real = GW.default_slots_dir()
    used = []                                # slot folders THIS process opened: other processes may share the real one
    orig = GW._Slots.__init__

    def spy(self, *a, **k):
        orig(self, *a, **k)
        if os.path.abspath(self.dir) == os.path.abspath(real):
            used.append(a[:1])
    GW._Slots.__init__ = spy
    try:
        yield
    finally:
        GW._Slots.__init__ = orig
    assert not used, (f"{len(used)} slot lock(s) were taken in the real {real} by this run: a test ran without "
                      "MH_SLOTS_DIR. (Slot files other processes create there are not this run's concern.)")


def _forget_chats():
    """Chats opened live by one test (agent_api.LIVE, the deleted set, the sidebar and search caches) must not show up in the
    next test's listing or count: a worker runs hundreds of tests in one process."""
    api = sys.modules.get("meshpool_harness.ui.agent_api")
    if api is None:
        return
    with api.LOCK:
        api.LIVE.clear()
    api.DELETED.clear()
    api.QUIESCING[0] = False
    api._METAS.clear()
    api._ROWS.clear()
    api._RAW.clear()
    api._RAW_BYTES[0] = 0
    api.list_dirty()


def _forget_learned():
    """Windows and refused fields learned in one test (process-wide, keyed by model) must not trim the next test."""
    from meshpool_harness.agent import core as _AC
    from meshpool_harness.core import context as _CX
    _CX.forget()
    _AC._REFUSED.clear()


@pytest.fixture(autouse=True)
def _offline_gateway(monkeypatch, tmp_path):
    """No test probes the real endpoint, and no endpoint state leaks between tests."""
    from meshpool_harness.agent.core import Agent
    from meshpool_harness.core import gateway as GW
    GW.reset()
    from meshpool_harness import config as _C
    monkeypatch.setattr(_C, "SHELL", {}, raising=False)             # the shell's own endpoint values and the injected names are
    monkeypatch.setattr(_C, "_INJECTED", set())         # per-process state: no test inherits another's
    env_before = dict(os.environ)       # app code may load a test's config into os.environ (C.load_into_env): undone below
    from meshpool_harness.core import modelcaps as MCAPS
    monkeypatch.setenv("MH_CAPS_LEARNED", str(tmp_path / "caps_learned.json"))   # learned facts never reach the real folder
    MCAPS.reset()
    _forget_learned()
    _forget_chats()
    # Loopback mocks count as local servers, which wait 15 min for a first byte and 5 min between bytes (H-06): tests that
    # check the timeouts use short ones, so the local floors are off here (the H-06 test turns them on).
    monkeypatch.setenv("MH_LOCAL_FIRST_BYTE_S", "1")
    monkeypatch.setenv("MH_LOCAL_IDLE_S", "1")
    # A test that misses its mock must fail, never reach a real model: a module reload once let a patched Agent fall
    # through to the live endpoint (12 paid calls). Tests that run a mock server set their own URL after this.
    monkeypatch.setenv("MESHPOOL_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MH_FETCH_LOGOS", "0")           # logos.py never reaches the internet in tests
    monkeypatch.setenv("MH_AUTO_ROUTE", "0")            # unit tests drive a bare Agent; test_ws20_cli_route opts in
    monkeypatch.setenv("MH_CLASSIFY_TIMEOUT", "1")      # a routing hint against the dead URL above gives up fast
    monkeypatch.setenv("MH_ROUTING_LOG", "0")           # tests never write a routing log (the log test turns it on)
    # ordinary work in the folder runs without a question in the OS sandbox (0.0.18); the permission tests written for
    # the questions keep testing them on machines with bubblewrap (CI) too. test_sandbox_auto.py turns it on.
    monkeypatch.setenv("MH_SANDBOX_AUTO", "0")
    # Unit tests exercise the mechanisms with no shipped profile assignment (app/defaults/profiles.json picks the
    # Codex-design engine for every route); test_profiles.test_shipped_assignment and test_codex_engine test that file.
    monkeypatch.setenv("MH_PROFILES_DEFAULTS", "none")
    # Never the real data folder: a saved company profile, settings or memory on the developer's machine must not
    # change what a test sees (the brief tests failed on machines with a saved company look).
    iso = tmp_path.parent / (tmp_path.name + "_mh_home")   # beside tmp_path: tests list and create inside tmp_path
    iso.mkdir(exist_ok=True)
    monkeypatch.setenv("MESHPOOL_HOME", str(iso))
    monkeypatch.setenv("MH_SLOTS_DIR", str(tmp_path / "gateway-slots"))   # never the machine's real temp folder
    monkeypatch.setenv("MH_REVIEW_CALIBRATION", "0")    # reviewer tests grade raw; the calibration has its own test
    monkeypatch.setattr(Agent, "_probe", lambda self, base, key, model: "HTTP 0: offline in tests")
    yield
    os.environ.clear()                  # e.g. MH_API_WIRE=anthropic from one test sent later agents down the wrong wire
    os.environ.update(env_before)
    GW.reset()
    MCAPS.reset()
    _forget_learned()
    _forget_chats()
