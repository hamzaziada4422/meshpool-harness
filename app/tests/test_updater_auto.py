"""The automatic updater: release picking, checked downloads, resume, the Mac and Windows helpers, install on quit.
No network, no hdiutil: releases are fake JSON, assets are local files, helper scripts are generated and read."""
import hashlib
import io
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from meshpool_harness import autoupdate as AU
from meshpool_harness import updater as U


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("MESHPOOL_AUTO_UPDATE", raising=False)
    monkeypatch.delenv("MESHPOOL_NO_UPDATE", raising=False)
    AU._ST.clear()
    AU._ST["state"] = "idle"
    AU._CFG.update(started=False, busy=lambda: False)
    U._CACHE.clear()
    yield
    AU._ST.clear()
    AU._ST["state"] = "idle"


def _rel(tag, pre=False, draft=False, sums=True):
    assets = [{"name": "Meshpool-Setup.exe", "browser_download_url": "x", "size": 10}]
    if sums:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": "s"})
    return {"tag_name": tag, "prerelease": pre, "draft": draft, "assets": assets, "html_url": "u",
            "published_at": "2026-12-01T00:00:00Z"}


def _fake_releases(monkeypatch, rels):
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(json.dumps(rels).encode()))
    monkeypatch.setattr(U.MH, "__version__", "0.1.0")
    monkeypatch.setattr(U, "kind", lambda: "win-app")


# ------------------------------------------------------------------ release picking

def test_picks_newest_stable_and_skips_prereleases_and_drafts(monkeypatch):
    _fake_releases(monkeypatch, [_rel("v0.2.0"), _rel("v0.4.0", pre=True), _rel("v0.3.0"),
                                 _rel("v0.5.0", draft=True), _rel("v0.6.0-rc.1", pre=True)])
    v = U.check(force=True)
    assert v["ok"] and v["newer"] and v["tag"] == "v0.3.0" and v["prerelease"] is False


def test_release_without_sha256sums_is_not_offered(monkeypatch):
    _fake_releases(monkeypatch, [_rel("v0.2.0", sums=False)])
    v = U.check(force=True)
    assert v["ok"] and not v["newer"]


def test_prerelease_is_never_downloaded_automatically(monkeypatch):
    monkeypatch.setattr(U, "_sums", lambda v: pytest.fail("a pre-release must not be fetched"))
    AU.pipeline({"tag": "v0.3.0-rc.1", "latest": "0.3.0-rc.1", "kind": "win-app", "prerelease": True, "url": "u",
                 "assets": []}, download=True)
    assert AU._ST["state"] == "available" and AU._ST.get("manual")


# ------------------------------------------------------------------ checksums

def _win_release(monkeypatch, tmp_path, payload=b"installer bytes", sums="good", manifest=True):
    app = tmp_path / "inst" / "app"
    (app.parent / "bin").mkdir(parents=True)
    (app.parent / "bin" / "meshpool.cmd").write_text("x")
    app.mkdir()
    monkeypatch.setattr(U, "APP", app)
    v = {"tag": "v0.2.0", "latest": "0.2.0", "kind": "win-app", "url": "u", "prerelease": False,
         "assets": [{"name": "Meshpool-Setup.exe", "url": "d", "size": len(payload)}, {"name": U.SUMS, "url": "s"}]}
    good = hashlib.sha256(payload).hexdigest()
    table = None if sums is None else {"Meshpool-Setup.exe": good if sums == "good" else "0" * 64}
    monkeypatch.setattr(U, "_sums", lambda v: table)
    monkeypatch.setattr(U, "_fresh_sums", lambda v: table)
    monkeypatch.setattr(U, "_asset", lambda v: v["assets"][0])
    monkeypatch.setattr(AU, "read_manifest", lambda v, s: {"runtime_changed": True} if manifest else None)
    monkeypatch.setattr(AU, "_open_range", lambda url, start: _Resp(payload, start))
    return v, payload


class _Resp:
    """A fake HTTP response that honours Range like GitHub's CDN."""
    def __init__(self, data, start, honour=True):
        self.status = 206 if start and honour else 200
        self._b = io.BytesIO(data[start:] if honour else data)
        self.asked = start

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_matching_checksum_makes_the_update_ready(monkeypatch, tmp_path):
    v, payload = _win_release(monkeypatch, tmp_path)
    AU.pipeline(v, download=True)
    assert AU._ST["state"] == "ready" and AU._ST["mode"] == "win-installer"
    assert Path(AU._ST["file"]).read_bytes() == payload


def test_checksum_mismatch_is_refused_and_the_file_deleted(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path, sums="bad")
    AU.pipeline(v, download=True)
    assert AU._ST["state"] == "error" and "checksum" in AU._ST["error"]
    assert not list((tmp_path / "data" / "updates").rglob("Meshpool-Setup.exe*"))


def test_missing_sha256sums_is_refused(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path, sums=None)
    AU.pipeline(v, download=True)
    assert AU._ST["state"] == "error" and "no checksums" in AU._ST["error"]
    assert not (tmp_path / "data" / "updates" / "v0.2.0" / "Meshpool-Setup.exe").exists()


def test_installer_not_listed_in_the_sums_is_refused(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path)
    monkeypatch.setattr(U, "_sums", lambda v: {"Other.exe": "0" * 64})
    AU.pipeline(v, download=True)
    assert AU._ST["state"] == "error" and "do not list" in AU._ST["error"]


def test_auto_download_off_only_reports(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path)
    AU.save_settings({"auto_download": False})
    monkeypatch.setattr(U, "check", lambda force=False: dict(v, ok=True, newer=True, current="0.1.0"))
    AU.tick()
    assert AU._ST["state"] == "available"
    assert not (tmp_path / "data" / "updates" / "v0.2.0").exists()


def test_manifest_must_be_listed_and_match(monkeypatch):
    body = json.dumps({"runtime_changed": False}).encode()
    v = {"assets": [{"name": AU.MANIFEST, "url": "m"}]}
    monkeypatch.setattr(U, "_get", lambda *a, **k: io.BytesIO(body))
    assert AU.read_manifest(v, {AU.MANIFEST: hashlib.sha256(body).hexdigest()}) == {"runtime_changed": False}
    with pytest.raises(AU.UpdateError):
        AU.read_manifest(v, {AU.MANIFEST: "0" * 64})
    with pytest.raises(AU.UpdateError):
        AU.read_manifest(v, {})
    assert AU.read_manifest({"assets": []}, {}) is None
    assert AU.runtime_changed(None) and AU.runtime_changed({}) and not AU.runtime_changed({"runtime_changed": False})


# ------------------------------------------------------------------ resumable download

def test_download_resumes_from_the_partial_file(monkeypatch, tmp_path):
    data = bytes(range(256)) * 1000
    dst = tmp_path / "d" / "f.bin"
    dst.parent.mkdir()
    dst.with_name("f.bin.part").write_bytes(data[:70000])
    seen = []
    monkeypatch.setattr(AU, "_open_range", lambda url, start: seen.append(start) or _Resp(data, start))
    AU.download_resumable("u", dst, len(data))
    assert seen == [70000] and dst.read_bytes() == data and not dst.with_name("f.bin.part").exists()


def test_download_restarts_when_the_server_ignores_range(monkeypatch, tmp_path):
    data = b"abcdefgh" * 5000
    dst = tmp_path / "f.bin"
    dst.with_name("f.bin.part").write_bytes(b"JUNK" * 100)
    monkeypatch.setattr(AU, "_open_range", lambda url, start: _Resp(data, start, honour=False))
    AU.download_resumable("u", dst)
    assert dst.read_bytes() == data


def test_a_resumed_download_with_wrong_bytes_fails_the_checksum(monkeypatch, tmp_path):
    v, payload = _win_release(monkeypatch, tmp_path, payload=b"A" * 5000)
    work = tmp_path / "data" / "updates" / "v0.2.0"
    work.mkdir(parents=True)
    (work / "Meshpool-Setup.exe.part").write_bytes(b"B" * 2000)       # a corrupt partial file
    AU.pipeline(v, download=True)
    assert AU._ST["state"] == "error" and "checksum" in AU._ST["error"]


# ------------------------------------------------------------------ Windows helper

def test_windows_installer_command_line_and_helper():
    assert AU.win_installer_args(r"C:\x\Meshpool-Setup.exe", r"C:\Users\A B\AppData\Local\Programs\Meshpool") == \
        [r"C:\x\Meshpool-Setup.exe", "/S", r"/D=C:\Users\A B\AppData\Local\Programs\Meshpool"]
    cmd = AU.win_helper_cmd(r"C:\d\apply-win.ps1")
    assert cmd[0] == "powershell.exe" and "-File" in cmd and cmd[-1].endswith("apply-win.ps1") and "Hidden" in cmd
    ps = AU.win_helper_script(4242, r"C:\u\Meshpool-Setup.exe", "AB" * 32, r"C:\Programs\Meshpool",
                              r"C:\Programs\Meshpool\Meshpool.exe", "log", "res")
    assert "$appPid = 4242" in ps and "'/S'" in ps and "'/D=' + $dir" in ps and "WaitForExit" in ps
    assert "Get-FileHash" in ps and "Start-Process -FilePath $exe" in ps and "'" + "ab" * 32 + "'" in ps


def test_windows_launch_helper_starts_the_script_detached(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path)
    AU.pipeline(v, download=True)
    launched = []
    monkeypatch.setattr(AU, "_launch_detached", launched.append)
    r = AU.begin_apply()
    assert r["ok"] and launched and launched[0][0] == "powershell.exe"
    script = Path(launched[0][-1]).read_text(encoding="utf-8-sig")
    assert str(U.APP.parent) in script and "Meshpool.exe" in script
    assert AU.begin_apply()["ok"] and len(launched) == 1                 # not started twice


# ------------------------------------------------------------------ install on quit

def test_ready_update_installs_on_quit_unless_turned_off(monkeypatch, tmp_path):
    v, _ = _win_release(monkeypatch, tmp_path)
    AU.pipeline(v, download=True)
    calls = []
    monkeypatch.setattr(AU, "launch_helper", lambda st: calls.append(st["mode"]) or {"ok": True})
    AU.save_settings({"install_on_quit": False})
    AU.on_quit()
    assert calls == []
    AU.save_settings({"install_on_quit": True})
    AU.on_quit()
    assert calls == ["win-installer"]


def test_nothing_installs_on_quit_when_nothing_is_ready(monkeypatch):
    monkeypatch.setattr(AU, "launch_helper", lambda st: pytest.fail("no update is ready"))
    AU.on_quit()


def test_apply_refuses_without_a_ready_update():
    assert not AU.begin_apply()["ok"]


def test_settings_roundtrip_and_env_switch(monkeypatch):
    assert AU.settings() == {"auto_download": True, "install_on_quit": True}
    AU.save_settings({"auto_download": False})
    assert AU.settings()["auto_download"] is False
    AU.save_settings({"auto_download": True})
    monkeypatch.setenv("MESHPOOL_AUTO_UPDATE", "0")
    assert AU.settings()["auto_download"] is False


def test_dev_checkout_never_starts_the_background_updater(monkeypatch):
    monkeypatch.setattr(U, "kind", lambda: "dev")
    AU.start_background()
    assert not AU._CFG["started"]


# ------------------------------------------------------------------ Mac helper

MAC = dict(pid=777, old_app="/Applications/Meshpool.app", new_app="/Applications/.Meshpool-update-0.2.0/Meshpool.app",
           stage_root="/Applications/.Meshpool-update-0.2.0", backup="/Applications/Meshpool.app.previous",
           log="/tmp/apply.log", result="/tmp/result.json")


def test_mac_helper_script_has_every_step():
    s = AU.mac_helper_script(needs_admin=False, **MAC)
    assert s.startswith("#!/bin/bash")
    assert "kill -0" in s and "PID=777" in s                              # waits for the old process to exit
    assert "codesign --verify --deep --strict" in s                       # last check before the swap
    assert "mv $Q_OLD $Q_BAK" in s and "mv $Q_NEW $Q_OLD" in s           # old aside, new in place
    assert "mv $Q_BAK $Q_OLD" in s and ".failed" in s                     # roll back (swap failed / new app dead)
    assert "xattr -p com.apple.quarantine" in s and "xattr -dr com.apple.quarantine" in s   # only when present
    assert 'open "$OLD"' in s and "pgrep -f" in s                         # relaunch and wait for it to run
    assert s.index("pgrep") < s.index('run "rm -rf $Q_BAK"')              # old copy kept until the new one runs
    assert "NEED_ADMIN=0" in s
    assert "osascript" in s and "with administrator privileges" in s      # the protected-folder fallback
    assert AU.mac_helper_script(needs_admin=True, **MAC).count("NEED_ADMIN=1") == 1


def test_mac_helper_quotes_odd_paths_and_rejects_newlines():
    s = AU.mac_helper_script(**{**MAC, "old_app": "/Users/o'brien/Apps/My App.app"}, needs_admin=False)
    assert "'/Users/o'\"'\"'brien/Apps/My App.app'" in s
    with pytest.raises(AU.UpdateError):
        AU.mac_helper_script(**{**MAC, "old_app": "/a\nb.app"}, needs_admin=False)


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bash"), reason="needs bash")
def test_mac_helper_script_really_swaps_and_rolls_back(tmp_path):
    """Run the generated script with stand-ins for open, pgrep, codesign and xattr."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "open").write_text('#!/bin/bash\necho "$1" >> "$OPENED"\n')
    (stubs / "pgrep").write_text('#!/bin/bash\n[ -f "$ALIVE" ]\n')
    (stubs / "true_cs").write_text("#!/bin/bash\nexit 0\n")
    for n in ("open", "pgrep", "true_cs"):
        (stubs / n).chmod(0o755)

    def run(alive: bool):
        root = tmp_path / ("alive" if alive else "dead")
        old, new = root / "Meshpool.app", root / ".stage" / "Meshpool.app"
        old.mkdir(parents=True)
        new.mkdir(parents=True)
        (old / "v").write_text("old")
        (new / "v").write_text("new")
        text = AU.mac_helper_script(999999, str(old), str(new), str(root / ".stage"), str(root / "Meshpool.app.previous"),
                                    str(root / "log"), str(root / "result.json"), False)
        text = text.replace("/usr/bin/codesign", str(stubs / "true_cs")).replace("/usr/bin/xattr", str(stubs / "true_cs"))
        text = text.replace("seq 1 60", "seq 1 2")
        script = root / "apply.sh"
        script.write_text(text)
        env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", OPENED=str(root / "opened"),
                   ALIVE=str(root / "alive_flag"))
        if alive:
            (root / "alive_flag").write_text("1")
        subprocess.run(["/bin/bash", str(script)], env=env, timeout=60, check=False)
        return old, root

    old, root = run(alive=True)                          # the new app starts: swapped, old copy removed
    assert (old / "v").read_text() == "new" and not (root / "Meshpool.app.previous").exists()
    assert json.loads((root / "result.json").read_text())["ok"] is True
    assert (root / "opened").read_text().strip() == str(old) and not (root / ".stage").exists()
    old, root = run(alive=False)                         # the new app never runs: the old one is put back
    assert (old / "v").read_text() == "old" and not (root / "Meshpool.app.previous").exists()
    assert json.loads((root / "result.json").read_text())["ok"] is False


# ------------------------------------------------------------------ Mac checks on the staged app

def _app(root: Path, bundle_id="ai.meshpool.desktop", version="0.2.0"):
    (root / "Contents" / "MacOS").mkdir(parents=True)
    (root / "Contents" / "MacOS" / "Meshpool").write_text("x")
    (root / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version, "CFBundleExecutable": "Meshpool"}))
    return root


def _fake_run(codesign_rc=0, spctl_cur=1, spctl_new=0, team_cur="", team_new=""):
    def run(cmd, timeout=900):
        exe = Path(cmd[0]).name
        if exe == "codesign" and "--verify" in cmd:
            return subprocess.CompletedProcess(cmd, codesign_rc, "", "")
        if exe == "codesign":
            team = team_new if "NEWAPP" in cmd[-1] else team_cur
            return subprocess.CompletedProcess(cmd, 0, "", f"TeamIdentifier={team or 'not set'}\n")
        if exe == "spctl":
            return subprocess.CompletedProcess(cmd, spctl_new if "NEWAPP" in cmd[-1] else spctl_cur, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_staged_mac_app_is_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(U.MH, "__version__", "0.1.0")
    cur, new = _app(tmp_path / "Meshpool.app", version="0.1.0"), _app(tmp_path / "NEWAPP" / "Meshpool.app")
    v = {"tag": "v0.2.0"}
    monkeypatch.setattr(AU, "_run", _fake_run())
    AU.verify_staged_mac(new, cur, v)                                    # fine: same id, right version, signature ok
    monkeypatch.setattr(AU, "_run", _fake_run(codesign_rc=1))
    with pytest.raises(AU.UpdateError, match="signature"):
        AU.verify_staged_mac(new, cur, v)
    monkeypatch.setattr(AU, "_run", _fake_run())
    with pytest.raises(AU.UpdateError, match="bundle identifier"):
        AU.verify_staged_mac(_app(tmp_path / "other" / "NEWAPP" / "M.app", bundle_id="evil.app"), cur, v)
    with pytest.raises(AU.UpdateError, match="version"):
        AU.verify_staged_mac(_app(tmp_path / "old" / "NEWAPP" / "M.app", version="0.0.9"), cur, v)
    monkeypatch.setattr(AU, "_run", _fake_run(team_cur="ABCDE12345", team_new="ZZZZZ99999"))
    with pytest.raises(AU.UpdateError, match="different developer"):
        AU.verify_staged_mac(new, cur, v)
    monkeypatch.setattr(AU, "_run", _fake_run(spctl_cur=0, spctl_new=3))   # this copy passes Gatekeeper, the new one not
    with pytest.raises(AU.UpdateError, match="Gatekeeper"):
        AU.verify_staged_mac(new, cur, v)
