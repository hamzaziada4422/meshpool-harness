"""What the installers ship inside, and how the packaged app finds it (no network, no real installs)."""
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

from meshpool_harness import bundled, config, harnesses
from meshpool_harness import office_runtime as o

ROOT = Path(__file__).resolve().parents[2]


def _load_packaging():
    spec = importlib.util.spec_from_file_location("mp_bundled_tools", ROOT / "packaging" / "bundled_tools.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


BT = _load_packaging()


# ---------------------------------------------------------------- checksum verification (build time, no network)
class _Resp(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener(data: bytes):
    return lambda url, timeout=0: _Resp(data)


def test_checksum_ok(tmp_path):
    data = b"libreoffice" * 1000
    msgs = []
    out = BT.download_verified(["https://x/a.dmg"], tmp_path / "a.dmg", hashlib.sha256(data).hexdigest(), len(data),
                               msgs.append, _opener(data))
    assert out.read_bytes() == data and any("Downloading" in m for m in msgs)


def test_checksum_mismatch_deletes_the_file(tmp_path):
    with pytest.raises(BT.BundleError, match="checksum mismatch"):
        BT.download_verified(["https://x/a.dmg"], tmp_path / "a.dmg", "0" * 64, 5, None, _opener(b"evil"))
    assert not (tmp_path / "a.dmg").exists()


def test_unfilled_placeholder_hash_is_refused(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"x")
    for bad in ("", "FILL-IN", "<fill me>"):
        with pytest.raises(BT.BundleError, match="not filled in"):
            BT.verify_sha256(f, bad)


def test_falls_back_to_the_next_mirror(tmp_path):
    data, seen = b"abc", []

    def op(url, timeout=0):
        seen.append(url)
        if "first" in url:
            raise OSError("down")
        return _Resp(data)
    BT.download_verified(["https://first/x", "https://second/x"], tmp_path / "x", hashlib.sha256(data).hexdigest(), 3,
                         None, op)
    assert len(seen) == 2


def test_mingit_refuses_until_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(BT, "MINGIT_URL", "FILL-IN")
    with pytest.raises(BT.BundleError, match="not filled in"):
        BT.fetch_mingit(tmp_path / "c", tmp_path / "d")


def test_pins_are_complete_and_consistent_with_the_app():
    for key in ("mac-arm64", "mac-x64", "win-x64"):
        d, name, sha, size = BT.LO_PINNED[key]
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and size > 1e8 and BT.LO_VERSION in name
        assert all(u.startswith("https://") for u in BT.lo_urls(key))
    assert o.VERSION == BT.LO_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", BT.GIT_SOURCE_SHA256)


def test_libreoffice_is_pinned_to_the_permanent_archive_only():
    for key in ("mac-arm64", "mac-x64", "win-x64"):
        urls = BT.lo_urls(key)
        assert len(urls) == 1 and urls[0].startswith("https://downloadarchive.documentfoundation.org/libreoffice/old/")
        assert f"/old/{BT.LO_BUILD}/" in urls[0] and BT.LO_BUILD in urls[0].rsplit("/", 1)[1]
    assert BT.LO_BUILD.startswith(BT.LO_VERSION)


def test_codex_and_opencode_are_no_longer_packaged():
    assert not hasattr(BT, "NODE_AGENTS") and not (ROOT / "packaging" / "node-agents").exists()
    assert not hasattr(bundled, "node_agent_command")


def test_chromium_prune_keeps_english_only(tmp_path):
    loc = tmp_path / "chrome-linux64" / "locales"
    loc.mkdir(parents=True)
    for n in ("en-US.pak", "de.pak", "fr.pak", "ja.pak"):
        (loc / n).write_bytes(b"x" * 100)
    (tmp_path / "chrome-linux64" / "WidevineCdm").mkdir()
    (tmp_path / "chrome-linux64" / "WidevineCdm" / "lib").write_bytes(b"x" * 50)
    lp = tmp_path / "Chromium.app" / "Resources"
    for n in ("en.lproj", "de.lproj", "zh_CN.lproj"):
        (lp / n).mkdir(parents=True)
        (lp / n / "locale.pak").write_bytes(b"x" * 10)
    (tmp_path / "ffmpeg-1013").mkdir()
    (tmp_path / "ffmpeg-1013" / "ffmpeg").write_bytes(b"x" * 7)
    gone = BT.prune_chromium(tmp_path)
    assert not (tmp_path / "ffmpeg-1013").exists()
    assert sorted(p.name for p in loc.iterdir()) == ["en-US.pak"]
    assert not (tmp_path / "chrome-linux64" / "WidevineCdm").exists()
    assert sorted(p.name for p in lp.iterdir()) == ["en.lproj"]
    assert gone == 300 + 50 + 20 + 7


def test_hermes_copy_leaves_out_tests_website_and_apps(tmp_path):
    import shutil
    src = tmp_path / "hermes"
    for d in ("acp_adapter", "agent", "skills/x", "tests", "website", "apps/ios", "docs", "ui-tui", "optional-skills/y",
              "agent/tests", "skills/pdf/tests"):
        (src / d).mkdir(parents=True)
        (src / d / "f.py").write_text("x")
    for f in ("pyproject.toml", "LICENSE", "SOUL.md", "README.md", "README.zh-CN.md", "Dockerfile", "uv.lock", "run_agent.py"):
        (src / f).write_text("x")
    dst = tmp_path / "out"
    shutil.copytree(src, dst, ignore=BT.hermes_ignore(src))
    kept = sorted(str(p.relative_to(dst)) for p in dst.rglob("f.py")) + sorted(p.name for p in dst.iterdir() if p.is_file())
    assert kept == ["acp_adapter/f.py", "agent/f.py", "skills/x/f.py", "LICENSE", "SOUL.md", "pyproject.toml", "run_agent.py"]


# ---------------------------------------------------------------- the packaged app uses only its own copies
@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fake installed app: <root>/app (config.APP), <root>/node, <root>/git, vendored agents."""
    appdir = tmp_path / "Resources" / "app"
    appdir.mkdir(parents=True)
    monkeypatch.setattr(config, "APP", appdir)
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.delenv("MH_NODE", raising=False)
    return appdir


def _file(p: Path, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_packaged_node_is_only_the_bundled_one(app, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/node")     # a customer's node
    assert bundled.node_exe() is None
    n = _file(app.parent / "node" / "bin" / "node")
    assert bundled.node_exe() == str(n)


def test_source_checkout_still_uses_path(app, monkeypatch):
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/node")
    assert bundled.node_exe() == "/usr/local/bin/node"


def test_pi_node_has_no_path_fallback_when_packaged(app, monkeypatch):
    from meshpool_harness.harnesses import pi_rpc
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/node")
    assert pi_rpc.node_exe() is None


def test_path_gets_the_bundled_tools_first(app, monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    (app.parent / "node" / "bin").mkdir(parents=True)
    (app.parent / "git" / "bin").mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    bundled.use_bundled_tools()                                                         # idempotent
    parts = __import__("os").environ["PATH"].split(__import__("os").pathsep)
    assert parts[0] == str(app.parent / "node" / "bin") and parts[1] == str(app.parent / "git" / "bin")
    assert parts.count(parts[0]) == 1 and parts[-1] == "/usr/bin"


def test_source_checkout_leaves_path_alone(app, monkeypatch):
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    monkeypatch.setenv("PATH", "/usr/bin")
    (app.parent / "node" / "bin").mkdir(parents=True)
    bundled.use_bundled_tools()
    assert __import__("os").environ["PATH"] == "/usr/bin"


def test_codex_and_opencode_are_not_offered_in_the_packaged_app(app, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/" + n if n else None)   # npx exists on the PATH
    for hid in ("codex", "opencode"):
        assert "not part of the Meshpool app" in harnesses.unsupported(hid)
        h = next(h for h in harnesses.load_all() if h["id"] == hid)
        assert h["available"] is False and h["hidden"] is True
        assert "Reinstall" not in json.dumps(h) and h["install"] == ""
    from meshpool_harness.harnesses.acp import ACPAgent, ACPError
    with pytest.raises(ACPError, match="not part of the Meshpool app"):
        ACPAgent(dict(harnesses.CODEX, builtin=True), str(app))._start()
    with pytest.raises(ValueError, match="not part of the Meshpool app"):
        harnesses.login("codex")


def test_source_checkout_keeps_npx_for_codex_and_opencode(app, monkeypatch):
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/" + n if n else None)
    assert harnesses.unsupported("codex") == "" and harnesses.unsupported("opencode") == ""
    rows = {h["id"]: h for h in harnesses.load_all()}
    for hid in ("codex", "opencode"):
        assert rows[hid]["command"] == "npx" and rows[hid]["args"][0] == "-y" and rows[hid]["available"] is True
        assert not rows[hid].get("hidden")
    assert harnesses.CODEX["args"][1] == harnesses.CODEX_ACP and harnesses.OPENCODE["args"][1] == harnesses.OPENCODE_PKG


def test_windows_bash_is_the_bundled_git(app, monkeypatch):
    monkeypatch.delenv("MH_BASH", raising=False)
    monkeypatch.setattr("os.name", "nt")
    assert config.find_bash() is None                                  # packaged, none bundled: not the customer's Git
    b = _file(app.parent / "git" / "usr" / "bin" / "bash.exe")
    assert config.find_bash() == str(b)


def test_a_node_already_last_on_path_is_moved_to_the_front(app, monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    nb = app.parent / "node" / "bin"
    nb.mkdir(parents=True)
    monkeypatch.setenv("PATH", f"/usr/local/bin:{nb}")                 # the old desktop.py put it last
    bundled.use_bundled_tools()
    assert __import__("os").environ["PATH"].split(":")[0] == str(nb)


def test_libreoffice_folder_is_on_path_for_vendored_skills(app, monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    mac = app / "vendor" / "libreoffice" / "LibreOffice.app" / "Contents" / "MacOS"
    mac.mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    assert str(mac) in __import__("os").environ["PATH"].split(":")


def test_browser_use_says_it_needs_macos_13_on_a_mac_in_between(app, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    for rel, says in (("11.7.10", True), ("12.6", True), ("10.16", True), ("13.0", False), ("15.1", False), ("", False)):
        monkeypatch.setattr("platform.mac_ver", lambda rel=rel: (rel, ("", "", ""), "x86_64"))
        msg = bundled.browser_needs_newer_macos()
        assert bool(msg) is says, rel
        if says:
            assert "needs macOS 13 or later" in msg and "web_search" in msg
    monkeypatch.setattr(bundled, "packaged", lambda: False)                       # a source checkout is never blocked
    monkeypatch.setattr("platform.mac_ver", lambda: ("11.7", ("", "", ""), "x86_64"))
    assert bundled.browser_needs_newer_macos() == ""
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    assert bundled.browser_needs_newer_macos() == ""


def _load_build_all():
    spec = importlib.util.spec_from_file_location("mp_build_all", ROOT / "packaging" / "build_all.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_a_release_build_cannot_skip_libreoffice(tmp_path, monkeypatch, capsys):
    ba = _load_build_all()
    monkeypatch.setenv("MH_BUNDLE_LIBREOFFICE", "0")
    for var in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    ba.libreoffice(tmp_path, "win")                                   # a developer's local test build: allowed, says so
    assert "WITHOUT LibreOffice" in capsys.readouterr().out
    for var in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.setenv(var, "true")
        with pytest.raises(SystemExit, match="not allowed in CI"):
            ba.libreoffice(tmp_path, "mac-arm64")
        monkeypatch.delenv(var)


def test_intel_and_apple_silicon_both_need_macos_11():
    ba = _load_build_all()
    assert ba.MIN_MACOS == {"mac-arm64": "11.0", "mac-x64": "11.0"}
    assert ba.PW_PLATFORM["mac-x64"] == "mac14" and ba.PW_PLATFORM["mac-arm64"] == "mac14-arm64"     # the only Mac names Playwright's Chromium has
    for f in ("packaging/mac/repack.sh", "packaging/mac/build_git.sh", ".github/workflows/release.yml"):
        text = (ROOT / f).read_text()
        assert "MIN=10.15" not in text and "min=10.15" not in text, f


def test_publish_step_still_attaches_files_to_a_release_published_by_hand():
    """A release published on GitHub (what starts the workflow) is already public but has no files yet: only a public
    release that already carries SHA256SUMS.txt is left alone. And the Mac Git must not install 140 hard links."""
    text = (ROOT / ".github/workflows/release.yml").read_text()
    i = text.index("isDraft -q .isDraft")
    cond = text[i:i + 400]
    assert "grep -qx SHA256SUMS.txt" in cond, "the already-public branch must also require the uploaded SHA256SUMS.txt"
    assert "NO_INSTALL_HARDLINKS=1" in (ROOT / "packaging/mac/build_git.sh").read_text()


def test_auto_routing_never_picks_codex_or_opencode_in_the_packaged_app(app, monkeypatch, tmp_path):
    """Auto is the product: even when the benchmark table ranks Codex and OpenCode first, a packaged app skips them."""
    from meshpool_harness import harness_router as HRT
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "home"))
    table = {"categories": {"general": {"keywords": [], "scores": {"codex": 99, "opencode": 98, "meshpool-code": 50,
                                                                   "meshpool-work": 50}}}}
    monkeypatch.setattr(HRT, "table", lambda: table)
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/" + n if n else None)
    r = HRT.route("fix the failing test", "code")
    assert r["harness"] in ("meshpool-code", "meshpool-work")
    assert all(x["harness"] not in ("codex", "opencode") for x in r["ranking"])


def test_convert_document_opens_a_pdf_as_text_for_word_and_refuses_what_cannot_work(tmp_path, monkeypatch):
    """soffice cannot write Word from a PDF opened in Draw ("no export filter"); the PDF import filter makes it work."""
    import subprocess
    from meshpool_harness.agent import office_mcp_server as O
    monkeypatch.setenv("MESHPOOL_PROJECT", str(tmp_path))
    monkeypatch.setattr(O, "_soffice", lambda: "/x/soffice")
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--outdir") + 1])
        (out / (Path(cmd[-1]).stem + "." + cmd[cmd.index("--convert-to") + 1])).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake)
    msg, err = O.convert_document(str(tmp_path / "a.pdf"), "docx")
    assert not err and "--infilter=writer_pdf_import" in calls[-1]
    msg, err = O.convert_document(str(tmp_path / "a.pdf"), "png")
    assert not err and not any(a.startswith("--infilter") for a in calls[-1])
    n = len(calls)
    msg, err = O.convert_document(str(tmp_path / "a.pdf"), "pptx")
    assert err and "not to pptx" in msg and len(calls) == n                          # never reaches LibreOffice
    (tmp_path / "b.docx").write_bytes(b"x")
    msg, err = O.convert_document(str(tmp_path / "b.docx"), "pdf")
    assert not err and not any(a.startswith("--infilter") for a in calls[-1])


def test_windows_build_gives_mingit_a_bash_exe(tmp_path, monkeypatch):
    """MinGit ships only usr/bin/sh.exe (the MSYS2 bash binary). Without a bash.exe the agents' shell fell back to
    PowerShell on Windows; the build copies sh.exe to bash.exe, and the app takes sh.exe if a bash.exe is missing."""
    ba = _load_build_all()
    deps = tmp_path / "deps"
    (deps / "git-win" / "usr" / "bin").mkdir(parents=True)
    (deps / "git-win" / "usr" / "bin" / "sh.exe").write_bytes(b"MZbash")
    monkeypatch.setattr(ba, "DEPS", deps)
    root = tmp_path / "Meshpool"
    root.mkdir()
    ba.git_win(root)
    assert (root / "git" / "usr" / "bin" / "bash.exe").read_bytes() == b"MZbash"

    from meshpool_harness import bundled
    monkeypatch.setattr(bundled, "root", lambda: root)
    assert bundled.bundled_bash().endswith("bash.exe")
    (root / "git" / "usr" / "bin" / "bash.exe").unlink()
    assert bundled.bundled_bash().endswith("sh.exe")
