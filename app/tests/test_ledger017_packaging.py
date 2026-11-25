"""Ledger 0.0.17, packaging area: the PA-toplevel, PA-packaging, PA-skills, PA-extra and PA-ND rows.

Where the row is about code, the test calls the code. Rows about release workflows, installer scripts and other files
that cannot run here (GitHub Actions, NSIS, PowerShell) are checked on the file text; each such test says so.
"""
from __future__ import annotations

import ast
import copy
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT / "packaging"))


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _toml(path: Path) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:          # Python 3.10: tomli is a declared dependency there
        import tomli as tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ---------- PA-toplevel ---------------------------------------------------------------------------------------------

def test_subagent_help_text_matches_the_limits_enforced(monkeypatch):
    """PA-toplevel-20: the Settings help for sub-agents names the limits the code really applies, in-process
    (agent/delegate.py) and through the assistant server for Hermes, Codex and Pi (assistant.py)."""
    from meshpool_harness import assistant as AS
    from meshpool_harness import config as C
    from meshpool_harness.agent import delegate as D
    help_ = {k: h for k, _label, _default, h in C.FIELDS}

    def with_cfg(cfg):
        monkeypatch.setattr(C, "read", lambda *a, **k: dict(cfg))
        return D.settings(), AS.subagent_settings()

    hi_d, hi_a = with_cfg({"MH_SUBAGENT_PARALLEL": "99", "MH_SUBAGENT_STEPS": "9999", "MH_SUBAGENT_MINUTES": "9999"})
    lo_d, lo_a = with_cfg({"MH_SUBAGENT_PARALLEL": "0", "MH_SUBAGENT_STEPS": "0", "MH_SUBAGENT_MINUTES": "0"})
    par, steps, mins = (help_["MH_SUBAGENT_PARALLEL"], help_["MH_SUBAGENT_STEPS"], help_["MH_SUBAGENT_MINUTES"])
    assert f"{lo_d['parallel']} to {hi_d['parallel']}" in par and f"{hi_a['parallel']} at most" in par
    assert f"{lo_d['steps']} to {hi_d['steps']}" in steps and f"{hi_a['steps']} at most" in steps
    assert lo_d["minutes"] == 0 and "0: no limit" in mins and f"up to {hi_d['minutes']}" in mins
    assert lo_a["minutes"] == hi_a["minutes"] == 25 and f"{hi_a['minutes']} minutes at most, also with 0" in mins


@pytest.fixture()
def ck_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)
    yield tmp_path
    monkeypatch.delenv("MH_CHECK_RUN", raising=False)


def test_two_chats_at_once_keep_their_own_checks(ck_home):
    """PA-toplevel-23: two turns recording at the same time each get only their own checks (the recorder is keyed
    by context, not by the one process-wide MH_CHECK_RUN and pointer file)."""
    from meshpool_harness import checks as CK
    both_began, recorded = threading.Barrier(2, timeout=10), threading.Barrier(2, timeout=10)
    errors = []

    def turn(run, label, n):
        try:
            CK.begin(run)
            both_began.wait()                      # the other chat began after this one: the global pointer is theirs
            CK.passed("sources", label, n)
            recorded.wait()
            CK.end(run)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=turn, args=("chatA:1", "sources", 3)),
          threading.Thread(target=turn, args=("chatB:2", "sources", 7))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    assert not errors
    assert CK.summary("chatA:1")["line"] == "Checked: 3 sources"
    assert CK.summary("chatB:2")["line"] == "Checked: 7 sources"
    assert CK.current() == ""


def test_an_engine_started_for_a_chat_records_into_that_chats_turn(ck_home, monkeypatch, tmp_path):
    """PA-toplevel-23: an office engine process started for chat A gets chat A's run, even when chat B began a turn
    later; an MCP server (which outlives its turn) is started without the turn's MH_CHECK_RUN."""
    from meshpool_harness import checks as CK
    from meshpool_harness.agent import mcp as M
    from meshpool_harness.agent import office_jobs as OJ
    CK.begin("chatA:100")
    other = threading.Thread(target=CK.begin, args=("chatB:200",))   # the latest turn, in another thread
    other.start()
    other.join(10)
    try:
        assert os.environ.get("MH_CHECK_RUN") == "chatB:200"         # the process-wide fallback is the latest turn
        assert CK.env_for("chatA") == {"MH_CHECK_RUN": "chatA:100"}
        seen = {}

        def fake_popen(cmd, **kw):
            seen.update(kw["env"])
            raise RuntimeError("not started in this test")
        monkeypatch.setattr(OJ.subprocess, "Popen", fake_popen)
        jid = "t-pa-tl-23"
        OJ.JOBS[jid] = {"engine": "slides", "cmd": ["true"], "out": tmp_path, "started": 0, "scope": "chatA",
                        "event": threading.Event(), "env_extra": {"MH_USAGE_TEST": "1"}}
        try:
            OJ.run(jid, timeout=5)
        finally:
            OJ.JOBS.pop(jid, None)
        assert seen.get("MH_CHECK_RUN") == "chatA:100"
        monkeypatch.setattr(M, "login_shell_env", lambda: {})
        assert "MH_CHECK_RUN" not in M.child_env()
    finally:
        CK.end("chatA:100")
        CK.end("chatB:200")


# ---------- PA-packaging ----------------------------------------------------------------------------------------------

def test_readme_beta_note_names_the_current_version():
    """PA-packaging-04: the README's beta note says the version __init__.py ships, not an old one."""
    from meshpool_harness import __version__
    m = re.search(r"Meshpool is a beta \(currently ([0-9][^)]*)\)", _text("README.md"))
    assert m and m.group(1) == __version__


def test_windows_installer_does_not_say_codex_and_opencode_are_inside():
    """PA-packaging-05 (NSIS text; cannot run here): the welcome page says Codex and OpenCode download on first use."""
    m = re.search(r'MUI_WELCOMEPAGE_TEXT "([^"]*)"', _text("packaging/nsis/installer.nsi"))
    assert m
    text = m.group(1)
    assert "Codex and OpenCode run on demand and download when first used" in text
    inside = text.split("Codex", 1)[0]
    assert "Codex" not in inside and "OpenCode" not in inside


def test_release_takes_the_previous_published_release_only():
    """PA-packaging-06 (GitHub Actions; cannot run here): the previous manifest comes from a published, non-draft
    release other than the one being made."""
    y = _text(".github/workflows/release.yml")
    lines = [ln for ln in y.splitlines() if "gh release list" in ln and "download" in ln]
    assert lines and all("--exclude-drafts" in ln and 'select(.tagName != "' in ln and "v$VER" in ln
                         for ln in lines)


def test_release_stops_when_the_tag_and_version_differ():
    """PA-packaging-07 (GitHub Actions; cannot run here): a release event whose tag is not v__version__ fails early."""
    y = _text(".github/workflows/release.yml")
    checks = re.findall(r'github\.event\.release\.tag_name \}\}" != "\$TAG" \]; then\s*\n\s*echo "::error::.*?exit 1',
                        y)
    assert len(checks) >= 2
    assert re.search(r'TAG="?v\$VER', y)


def test_node_downloads_are_checked_against_shasums():
    """PA-packaging-08 (GitHub Actions; cannot run here): Node for macOS and Windows (and Linux) is checked against
    SHASUMS256.txt. Note: Node still comes from latest-v22.x, it is not pinned to one version."""
    y = _text(".github/workflows/release.yml")
    assert re.search(r"darwin-\$\{\{ matrix\.arch \}\}\.tar\.gz.*node\.tar\.gz\" \| shasum -a 256 -c -", y)
    assert re.search(r"node\.zip\" \| sha256sum -c -", y)
    assert y.count("SHASUMS256.txt") >= 3


def _package_data_matches(rel: Path, globs: dict) -> bool:
    """Whether setuptools' package-data globs (keys: a package or "*"; patterns relative to the package folder)
    pick up the file `rel` (relative to app/). Every folder under meshpool_harness/ and engines/ is a package
    (namespace packages); defaults and profiles are one package each."""
    top = rel.parts[0]
    if top in ("defaults", "profiles"):
        pkg_dir, pkg = Path(top), top
    else:
        pkg_dir, pkg = rel.parent, ".".join(rel.parent.parts)
    sub = rel.relative_to(pkg_dir).as_posix()
    pats = list(globs.get("*", [])) + list(globs.get(pkg, []))
    for p in pats:
        if p == "**/*" or (("/" not in sub) and fnmatch.fnmatch(sub, p)):
            return True
    return False


def test_a_plain_pip_install_ships_defaults_profiles_and_fonts():
    """PA-packaging-10: `pip install ./app` (not editable) must carry what the code reads next to the package:
    defaults/, profiles/, the deck fonts and the icon bundles. Checked against pyproject's package list and
    package-data globs (a wheel built from this config was inspected by hand: every file was in it)."""
    cfg = _toml(APP / "pyproject.toml")["tool"]["setuptools"]
    include = cfg["packages"]["find"]["include"]
    for name in ("meshpool_harness", "engines", "defaults", "profiles"):
        assert any(fnmatch.fnmatch(name, pat) for pat in include), name
    globs = cfg["package-data"]
    missing = []
    for top in ("defaults", "profiles", "engines", "meshpool_harness"):
        for p in (APP / top).rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or "node_modules" in p.parts or p.suffix == ".pyc":
                continue
            rel = p.relative_to(APP)
            if top in ("engines", "meshpool_harness") and p.suffix == ".py":
                continue                                   # modules of a package: always in the wheel
            if not _package_data_matches(rel, globs):
                missing.append(rel.as_posix())
    assert not missing, missing[:20]
    # the code finds them at config.APP, the folder that holds meshpool_harness (site-packages after an install)
    from meshpool_harness import config as C
    assert (C.APP / "defaults" / "mcp.json").is_file() and (C.APP / "profiles").is_dir()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_install_sh_never_runs_the_xcode_python_stub(tmp_path):
    """PA-packaging-11: on a Mac without the Command Line Tools, find_python skips /usr/bin/python3 (a stub that
    opens an install dialog) without running it; with the tools installed it is used. Runs install.sh's own
    find_python with uname, xcode-select and python3 replaced by stubs."""
    src = _text("install.sh")
    m = re.search(r"^find_python\(\) \{\n.*?^\}\n", src, re.M | re.S)
    assert m
    ran = tmp_path / "ran"

    def run(has_clt: bool) -> subprocess.CompletedProcess:
        script = (m.group(0) + "\n"
                  "uname() { echo Darwin; }\n"
                  f"xcode-select() {{ return {0 if has_clt else 1}; }}\n"
                  "command() { if [ \"$1\" = -v ]; then case \"$2\" in python3) echo /usr/bin/python3;; "
                  "*) return 1;; esac; else builtin command \"$@\"; fi; }\n"
                  f"python3() {{ echo ran >> '{ran}'; return 0; }}\n"
                  "find_python\n")
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=20)
    r = run(has_clt=False)
    assert r.returncode == 1 and not ran.exists()
    r = run(has_clt=True)
    assert r.returncode == 0 and r.stdout.strip() == "/usr/bin/python3" and ran.exists()


@pytest.mark.skipif(sys.platform == "darwin" or os.name == "nt", reason="the Linux path")
def test_cli_readme_settings_folder_is_the_one_the_code_uses(monkeypatch, tmp_path):
    """PA-packaging-12: the CLI README lists a Linux settings path, and it is the folder the code uses."""
    from meshpool_harness import config as C
    doc = _text("packaging/harness-docs/README.md")
    m = re.search(r"- Linux: `([^`]+)/meshpool\.config`.*?\(`\$XDG_DATA_HOME/meshpool` when that is set\)", doc)
    assert m
    monkeypatch.delenv("MESHPOOL_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert C.home() == Path(m.group(1).replace("~", str(tmp_path)))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert C.home() == tmp_path / "xdg" / "meshpool"
    assert not re.search(r"MESHPOOL_HOME=.*meshpool-cli", _text("install.sh"))   # the shim does not move it


def test_label_triggered_agent_runs_only_for_trusted_authors():
    """PA-packaging-13 (GitHub Actions template; cannot run here): a label on an issue starts the agent only when
    the issue author has write access, and the file says why."""
    yaml = pytest.importorskip("yaml")
    raw = _text("packaging/harness-docs/ci/meshpool-agent.yml")
    job = yaml.safe_load(raw)["jobs"]["agent"]
    cond = job["if"]
    assert "author_association" in cond and "github.event.label.name == 'meshpool'" in cond
    assert re.search(r'\["OWNER","MEMBER","COLLABORATOR"\]', cond) and "NONE" not in cond
    assert "injected instructions" in raw


def test_readme_has_a_linux_download():
    """PA-packaging-14: the README install table has the Linux tarball the release ships."""
    readme = _text("README.md")
    assert re.search(r"\| Linux[^|]*\| \[Meshpool-Linux-x64\.tar\.gz\]\(https://github\.com/[^)]*/releases/latest/"
                     r"download/Meshpool-Linux-x64\.tar\.gz\)", readme)
    assert "Meshpool-Linux-x64.tar.gz" in _text(".github/workflows/release.yml")


def test_release_notes_do_not_say_chatgpt_sign_in_is_not_offered():
    """PA-packaging-15: the stale 0.0.7 sentence is gone; the notes say ChatGPT sign-in works."""
    notes = _text("packaging/release-notes.md")
    assert not re.search(r"ChatGPT sign-in is not offered|not offered in the app", notes)
    assert "sign in with ChatGPT" in notes


def test_build_all_checks_winres_only_for_the_windows_build():
    """PA-packaging-16 (build script main block; not run here, it builds the apps): check_winres() runs only when
    the build includes win."""
    tree = ast.parse(_text("packaging/build_all.py"))
    main = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test))
    calls = []
    for node in ast.walk(main):
        if isinstance(node, ast.If) and '"win"' in ast.unparse(node.test).replace("'", '"'):
            calls += [c for c in ast.walk(node) if isinstance(c, ast.Call) and ast.unparse(c.func) == "check_winres"
                      and not c.keywords]
    every = [c for c in ast.walk(main) if isinstance(c, ast.Call) and ast.unparse(c.func) == "check_winres"
             and not c.keywords]
    assert calls and len(calls) == len(every)


def test_release_sh_usage_lists_every_file_the_release_needs():
    """PA-packaging-17 (release script; publishes to GitHub, not run here): the usage line names the source and
    Linux tarballs."""
    usage = next(ln for ln in _text("packaging/release.sh").splitlines() if ln.startswith("# Usage:"))
    for f in ("Meshpool-src.tar.gz", "Meshpool-Linux-x64.tar.gz", "update-manifest.json", "Meshpool-Setup.exe"):
        assert f in usage


def test_harness_loop_inputs_reach_the_shell_only_as_plain_characters():
    """PA-packaging-18 (GitHub Actions; cannot run here): dispatch inputs come in through an environment variable
    and are cut to plain characters before they are written to GITHUB_OUTPUT and used in shell lines."""
    y = _text(".github/workflows/harness-loop.yml")
    runs = re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", y)
    assert runs and not any("${{ inputs." in r or "${{ github.event.inputs." in r for r in runs)
    assert y.count("INPUTS: ${{ toJSON(inputs) }}") >= 2
    classes = re.findall(r're\.sub\(r"(\[\^[^\]]*\])", ""', y)
    assert len(classes) >= 2
    hostile = '" ; id #\nX=1 $(id) `id` \'q\''
    for cls in classes:
        clean = re.sub(cls, "", hostile)
        assert not set(clean) & set("\"';#\n$`()"), clean


def test_tests_workflow_header_matches_its_triggers():
    """PA-packaging-19 (GitHub Actions; cannot run here): the header does not promise a run on every pull request
    the triggers do not have."""
    y = _text(".github/workflows/tests.yml")
    head = y.split("\nname:", 1)[0]
    on = y.split("\non:", 1)[1].split("\nconcurrency:", 1)[0]
    assert "pull_request" not in on
    assert "every pull request" not in head.replace("every pull-request push", "")


def test_nsis_never_puts_the_install_folder_inside_a_quoted_powershell_command():
    """PA-packaging-20 (NSIS; cannot run here): no PowerShell command line embeds $INSTDIR in quotes (a folder with
    an apostrophe broke it); closing the app's programs goes through WinSetup, which reads the folder from an
    environment variable."""
    for rel in ("packaging/nsis/installer.nsi", "packaging/nsis/uninstaller.nsi"):
        code = [ln for ln in _text(rel).splitlines() if not ln.lstrip().startswith(";")]
        bad = [ln for ln in code if "powershell" in ln.lower() and "$INSTDIR" in ln]
        assert not bad, (rel, bad)
        text = "\n".join(code)
        for sec in re.findall(r"^Section .*?^SectionEnd", text, re.M | re.S):
            if "taskkill" in sec:
                assert '!insertmacro WinSetup "$INSTDIR"' in sec, rel
    assert "$env:MP_STOP" in _text("packaging/nsis/winsetup.ps1")


def test_install_ps1_app_finds_a_custom_install_folder():
    """PA-packaging-21 (PowerShell; cannot run here): -App reads the folder the installer recorded in
    HKCU\\Software\\Meshpool InstallDir, the same key the installer writes."""
    ps = _text("install.ps1")
    assert re.search(r'Get-ItemProperty -Path "HKCU:\\Software\\Meshpool".*\)\.InstallDir', ps)
    assert re.search(r'\$ExeDir = \$reg', ps)
    nsi = _text("packaging/nsis/installer.nsi")
    assert 'WriteRegStr HKCU "Software\\Meshpool" "InstallDir" "$INSTDIR"' in nsi


# ---------- PA-skills -------------------------------------------------------------------------------------------------

def test_chatgpt_wire_compares_years_on_the_utc_clock():
    """PA-skills-11 (a test's own check): the last_refresh year is compared with the UTC year, never the local
    strftime year that fails around New Year in a zone behind UTC."""
    src = (APP / "tests" / "test_chatgpt_wire.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
              and n.name == "test_expired_token_is_refreshed_first_and_written_back")
    body = ast.unparse(fn)
    assert "last_refresh" in body and "time.gmtime().tm_year" in body and 'strftime("%Y-")' not in body


def test_tui_v2_does_not_swallow_read_input_failures():
    """PA-skills-14 (a test's own check): no try/except in test_tui_v2.py hides an exception from read_input()."""
    tree = ast.parse((APP / "tests" / "test_tui_v2.py").read_text(encoding="utf-8"))
    for t in [n for n in ast.walk(tree) if isinstance(n, ast.Try)]:
        if any("read_input" in ast.unparse(s) for s in t.body):
            for h in t.handlers:
                assert not all(isinstance(s, ast.Pass) for s in h.body), ast.unparse(t)


def _junit(tmp_path, skips: list) -> str:
    cases = "".join(f'<testcase classname="tests.test_x" name="t{i}"><skipped type="pytest.skip" message="{r}">'
                    f"{r}</skipped></testcase>" for i, r in enumerate(skips))
    p = tmp_path / "report.xml"
    p.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}'
                 f'<testcase classname="tests.test_x" name="ok"/></testsuite></testsuites>', encoding="utf-8")
    return str(p)


def test_ci_counts_skips_and_fails_on_a_missing_installed_tool(tmp_path):
    """PA-skills-16: the CI test step lists every skip and fails when a test skipped because LibreOffice, Chromium,
    Playwright or jsonschema (all installed by the job) is missing; other skips are only listed."""
    import ci_skips
    fine = ["MESHPOOL_KB not set: the slide knowledge base is private and not in CI",
            "Playwright's own Chromium is not what launches here", "bwrap is not installed", "Windows only"]
    code, text = ci_skips.check(_junit(tmp_path, fine))
    assert code == 0 and text.startswith("4 skipped test(s); 0 because")
    for bad in ("LibreOffice not installed", "LibreOffice needed for recalculation", "no Chromium on this computer",
                "no Chromium for Playwright here: boom", "could not import 'playwright': No module named 'playwright'"):
        code, text = ci_skips.check(_junit(tmp_path, fine + [bad]))
        assert code == 1 and "1 because a tool this job installs is missing" in text and bad[:30] in text, bad
    y = _text(".github/workflows/tests.yml")
    step = y.split("- name: Tests", 1)[1]
    assert "-rs" in step and "--junitxml=" in step and "packaging/ci_skips.py" in step
    assert y.count("runs-on:") == 1                                       # no extra CI job


def test_jsonschema_test_cannot_be_skipped_silently_in_ci(tmp_path):
    """PA-skills-15: CI installs jsonschema, and a skip because it is missing would now fail the job."""
    import ci_skips
    y = _text(".github/workflows/tests.yml")
    assert re.search(r"pip install -r packaging/requirements-cli\.txt[^\n]*\bjsonschema\b", y)
    code, _ = ci_skips.check(_junit(tmp_path, ["could not import 'jsonschema': No module named 'jsonschema'"]))
    assert code == 1


def test_testkit_run_all_leaves_a_developers_meshpool_and_folders_alone():
    """PA-skills-minor (testkit shell script; it starts apps and browsers, not run here): run_all.sh stops only the
    apps it started itself (a pid file), and replaces /tmp/proj1 and /tmp/proj2 only when it made them."""
    sh = "\n".join(ln for ln in _text("testkit/run_all.sh").splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r'pkill -f "[^"]*ui --port 879', sh)
    assert 'echo $! >> "$PIDS"' in sh and 'done < "$PIDS"' in sh
    guard = sh.index(".meshpool-testkit")
    assert guard < sh.index('rm -rf "$d"') and guard < sh.index("start 8799")


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is a dev tool")
def test_the_flagged_tests_use_what_they_compute():
    """PA-skills-minor: ruff F841 (a value computed and never checked) is clean in the two tests the audit named."""
    r = subprocess.run(["ruff", "check", "--select", "F841", "--output-format", "concise",
                        "tests/test_firstrun_away.py", "tests/test_projects_v2.py"], cwd=APP, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stdout


# ---------- PA-extra --------------------------------------------------------------------------------------------------

def _dict_literal(path: Path, name: str) -> ast.Dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n.value for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))


def test_acp_kinds_has_no_repeated_key():
    """PA-extra-2: KINDS in agent/acp_server.py names each tool once (ruff F601), and fetch_url is still a fetch."""
    from meshpool_harness.agent import acp_server
    keys = [k.value for k in _dict_literal(APP / "meshpool_harness" / "agent" / "acp_server.py", "KINDS").keys]
    assert len(keys) == len(set(keys))
    assert acp_server.KINDS["fetch_url"] == "fetch"


def test_no_f_string_without_a_placeholder():
    """PA-extra-3: memstore.py and skills.py (and the other files ruff F541 flagged) have no f-string with nothing
    to fill in."""
    for rel in ("memstore.py", "skills.py", "core/endpoint.py", "ui_smoke.py"):
        tree = ast.parse((APP / "meshpool_harness" / rel).read_text(encoding="utf-8"))
        inner = {id(v) for n in ast.walk(tree) if isinstance(n, ast.FormattedValue) and n.format_spec
                 for v in [n.format_spec]}
        empty = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) and id(n) not in inner
                 and not any(isinstance(v, ast.FormattedValue) for v in n.values)]
        assert not empty, (rel, empty)


def test_restore_version_refuses_a_negative_index(tmp_path, monkeypatch):
    """PA-extra-4: restore_version(fid, -1) is refused; it does not quietly take the last version."""
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    from meshpool_harness import memstore as MS
    MS._ENSURED.clear()
    MS._CACHE.clear()
    f, _ = MS.add("Works at Acme")
    MS.update(f["id"], text="Works at Acme Gulf")
    MS.update(f["id"], text="Works at Beta")
    MS.restore_version(f["id"], 0)
    assert MS.get(f["id"])["text"] == "Works at Acme"
    for bad in (-1, -3, "-1"):
        with pytest.raises(ValueError):
            MS.restore_version(f["id"], bad)
    assert MS.get(f["id"])["text"] == "Works at Acme"


# ---------- PA-ND -----------------------------------------------------------------------------------------------------

def _deck(path: Path, w_in: float, h_in: float, logo_at=None) -> str:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(w_in), Inches(h_in)
    m = prs.slide_masters[0]
    if logo_at:                                   # a small mark on the master (made on a slide, then moved there)
        tmp = prs.slides.add_slide(prs.slide_layouts[6])
        sh = tmp.shapes.add_shape(1, Inches(logo_at[0]), Inches(logo_at[1]), Inches(0.6), Inches(0.25))
        sh.text_frame.text = "ACME"
        sh.text_frame.paragraphs[0].runs[0].font.size = Pt(9)
        m.shapes._spTree.append(copy.deepcopy(sh._element))
        sid = prs.slides._sldIdLst[-1]
        prs.part.drop_rel(sid.rId)
        prs.slides._sldIdLst.remove(sid)
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "A page"
    prs.save(path)
    return str(path)


def test_a_16_9_master_at_10_by_5_625_in_is_scaled_and_used(tmp_path):
    """PA-ND-2: a company master at 10 x 5.625 in (16:9, PowerPoint's older 'On-screen Show 16:9') is accepted: it
    is scaled to 13.333 x 7.5 in, positions and font sizes with it. A 4:3 master is still refused with the size
    the forge needs."""
    from pptx import Presentation
    from pptx.util import Pt
    from engines.forge import brand as B
    src = _deck(tmp_path / "small.pptx", 10, 5.625, logo_at=(8.7, 5.2))
    before = Presentation(src)
    t0 = next(p for p in before.slide_masters[0].placeholders if p.placeholder_format.type == 1)   # the title
    ok, why, corner = B.make_master(src, str(tmp_path / "master.pptx"))
    assert ok, why
    after = Presentation(str(tmp_path / "master.pptx"))
    assert (after.slide_width, after.slide_height) == B.WIDE and len(after.slides) == 0
    k = B.WIDE[0] / before.slide_width
    t1 = next(p for p in after.slide_masters[0].placeholders if p.placeholder_format.type == 1)
    assert abs(t1.left - t0.left * k) <= 2 and abs(t1.width - t0.width * k) <= 2
    logo = next(s for s in after.slide_masters[0].shapes if not s.is_placeholder)
    assert abs(logo.left / B.EMU - 8.7 * k) < 0.01 and logo.text_frame.paragraphs[0].runs[0].font.size == Pt(12)
    assert corner is not None and abs(corner - 8.7 * k) < 0.01          # in the footer's right corner, scaled
    ok, why, _ = B.make_master(_deck(tmp_path / "four3.pptx", 10, 7.5), str(tmp_path / "m2.pptx"))
    assert not ok and "10.00 x 7.50 in" in why and "13.33 x 7.5 in" in why
