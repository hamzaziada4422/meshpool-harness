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


def test_node_agent_lockfiles_match_the_pinned_versions():
    pins = {"codex-acp": harnesses.CODEX_ACP.rsplit("@", 1), "opencode": harnesses.OPENCODE_PKG.rsplit("@", 1)}
    for name, (pkg, ver) in pins.items():
        pj = json.loads((BT.NODE_AGENTS[name] / "package.json").read_text())
        assert pj["dependencies"] == {pkg: ver}
        lock = json.loads((BT.NODE_AGENTS[name] / "package-lock.json").read_text())
        assert lock["packages"][f"node_modules/{pkg}"]["version"] == ver
        assert lock["packages"][f"node_modules/{pkg}"]["integrity"].startswith("sha512-")


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


def _vendor_codex(app):
    node = _file(app.parent / "node" / "bin" / "node")
    nm = app / "vendor" / "codex-acp" / "node_modules"
    _file(nm / "@agentclientprotocol" / "codex-acp" / "package.json", json.dumps({"bin": {"codex-acp": "dist/index.js"}}))
    js = _file(nm / "@agentclientprotocol" / "codex-acp" / "dist" / "index.js")
    _file(nm / "@openai" / "codex" / "package.json", json.dumps({"bin": {"codex": "bin/codex.js"}}))
    login = _file(nm / "@openai" / "codex" / "bin" / "codex.js")
    return node, js, login


def test_codex_runs_from_the_vendored_copy_not_npx(app):
    node, js, login = _vendor_codex(app)
    assert bundled.node_agent_command("codex") == (str(node), [str(js.resolve())])
    assert bundled.node_agent_command("codex-login") == (str(node), [str(login.resolve())])
    h = harnesses.packaged_launch(harnesses.CODEX)
    assert h["command"] == str(node) and h["args"] == [str(js.resolve())] and "npx" not in json.dumps(h)


def test_opencode_uses_the_platform_binary_from_the_vendored_package(app, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    exe = _file(app / "vendor" / "opencode" / "node_modules" / "opencode-darwin-arm64" / "bin" / "opencode")
    h = harnesses.packaged_launch(harnesses.OPENCODE)
    assert h["command"] == str(exe) and h["args"] == ["acp"]


def test_missing_vendored_agent_never_falls_back_to_npx(app, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n, *a, **k: "/usr/local/bin/" + n if n else None)
    for h in (harnesses.CODEX, harnesses.OPENCODE):
        r = harnesses.packaged_launch(h)
        assert r["command"] == "" and not harnesses._available(r) and "Reinstall" in r["install"]


def test_source_checkout_keeps_npx(app, monkeypatch):
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    assert harnesses.packaged_launch(harnesses.CODEX)["command"] == "npx"
    assert harnesses.packaged_launch(harnesses.OPENCODE)["args"][0] == "-y"


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
