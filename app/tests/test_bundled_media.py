"""ffmpeg, pandoc, the speech model and the Python packages the skills use ship inside the app (docs/plan/0.0.11-bundled.md):
how the build takes them out of their pinned wheels, how the app finds them, what the system check says, and that no
default skill sends the agent off to install something that ships. No network."""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from meshpool_harness import bundled, config
from meshpool_harness import skills as SK

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "app" / "defaults" / "skills"


def _bt():
    spec = importlib.util.spec_from_file_location("mp_bundled_tools_media", ROOT / "packaging" / "bundled_tools.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


BT = _bt()


@pytest.fixture
def app(tmp_path, monkeypatch):
    appdir = tmp_path / "Resources" / "app"
    appdir.mkdir(parents=True)
    monkeypatch.setattr(config, "APP", appdir)
    monkeypatch.setattr(bundled, "packaged", lambda: True)
    for k in ("MESHPOOL_WHISPER_MODEL", "HF_HUB_OFFLINE", "MESHPOOL_PYTHON"):
        monkeypatch.delenv(k, raising=False)
    return appdir


def _exe(path: Path, text="#!/bin/sh\necho \"NAME 9.9\"\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("NAME", path.name))
    path.chmod(0o755)
    return path


def _model(app: Path) -> Path:
    m = app / "vendor" / "whisper" / "base"
    m.mkdir(parents=True)
    (m / "model.bin").write_bytes(b"m")
    (m / "config.json").write_text("{}")
    return m


# ---------------------------------------------------------------- the build: programs out of pinned wheels, the model
def _fake_wheel(tmp_path, member: str, payload: bytes) -> tuple:
    whl = tmp_path / "cache" / "tool-1-py3-none-any.whl"
    whl.parent.mkdir()
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(member, payload)
        z.writestr("other/readme.txt", b"hi")
    return whl, hashlib.sha256(whl.read_bytes()).hexdigest()


def test_the_program_comes_out_of_the_pinned_wheel_and_is_executable(tmp_path, monkeypatch):
    whl, sha = _fake_wheel(tmp_path, "imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2", b"\x7fELFfake")
    pins = dict(BT.WHEEL_PROGRAMS["ffmpeg"]["pins"], **{"linux-x64": ("https://x/" + whl.name, sha, whl.stat().st_size)})
    monkeypatch.setitem(BT.WHEEL_PROGRAMS["ffmpeg"], "pins", pins)
    exe = BT.fetch_wheel_program("ffmpeg", "linux-x64", whl.parent, tmp_path / "vendor" / "ffmpeg")
    assert exe == tmp_path / "vendor" / "ffmpeg" / "ffmpeg" and exe.read_bytes() == b"\x7fELFfake"
    assert os.access(exe, os.X_OK)
    marker = json.loads((exe.parent / "MESHPOOL-FFMPEG.json").read_text())
    assert marker["wheel"] == whl.name and marker["sha256"] == sha and "GPL" in marker["license"]
    win = dict(pins, **{"win-x64": pins["linux-x64"]})
    monkeypatch.setitem(BT.WHEEL_PROGRAMS["ffmpeg"], "pins", win)
    assert BT.fetch_wheel_program("ffmpeg", "win-x64", whl.parent, tmp_path / "w").name == "ffmpeg.exe"


def test_a_wheel_that_is_not_the_pinned_one_is_refused(tmp_path, monkeypatch):
    whl, sha = _fake_wheel(tmp_path, "pypandoc/files/pandoc", b"pandoc")
    pins = dict(BT.WHEEL_PROGRAMS["pandoc"]["pins"], **{"linux-x64": ("https://x/" + whl.name, "0" * 64, 1)})
    monkeypatch.setitem(BT.WHEEL_PROGRAMS["pandoc"], "pins", pins)
    monkeypatch.setattr(BT, "download_verified", lambda *a, **k: (_ for _ in ()).throw(BT.BundleError("checksum mismatch")))
    with pytest.raises(BT.BundleError, match="checksum mismatch"):
        BT.fetch_wheel_program("pandoc", "linux-x64", whl.parent, tmp_path / "v")
    with pytest.raises(BT.BundleError, match="no pinned"):
        BT.fetch_wheel_program("pandoc", "freebsd", whl.parent, tmp_path / "v")


def test_every_platform_has_a_pinned_hash_for_each_program():
    for name, spec in BT.WHEEL_PROGRAMS.items():
        assert set(spec["pins"]) == {"mac-arm64", "mac-x64", "win-x64", "linux-x64"}, name
        for key, (url, sha, size) in spec["pins"].items():
            assert url.startswith("https://files.pythonhosted.org/packages/") and url.endswith(".whl"), (name, key)
            assert re.fullmatch(r"[0-9a-f]{64}", sha) and size > 1_000_000, (name, key)
    assert set(BT.WHEEL_PROGRAMS["ffmpeg"]["pins"]) == set(BT.LO_PINNED)        # the same four platforms as LibreOffice


def test_the_speech_model_is_pinned_to_a_commit_with_every_file_hashed():
    assert re.fullmatch(r"[0-9a-f]{40}", BT.WHISPER_REVISION)
    assert set(BT.WHISPER_FILES) == {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s, _ in BT.WHISPER_FILES.values())


def test_the_speech_model_is_downloaded_checked_and_laid_out_for_faster_whisper(tmp_path, monkeypatch):
    files = {n: (n.encode() * 50) for n in BT.WHISPER_FILES}
    monkeypatch.setattr(BT, "WHISPER_FILES", {n: (hashlib.sha256(b).hexdigest(), len(b)) for n, b in files.items()})
    got = []

    def fake(urls, dest, sha, size=0, progress=None, opener=None, label=""):
        got.append(urls[0])
        name = urls[0].rsplit("/", 1)[1]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(files[name])
        assert hashlib.sha256(files[name]).hexdigest() == sha
        return dest
    monkeypatch.setattr(BT, "download_verified", fake)
    out = BT.fetch_whisper_model(tmp_path / "cache", tmp_path / "vendor" / "whisper")
    assert out == tmp_path / "vendor" / "whisper" / "base"
    assert {p.name for p in out.iterdir()} == set(files) | {"MESHPOOL-WHISPER.json"}
    assert all(BT.WHISPER_REVISION in u and u.startswith("https://huggingface.co/Systran/faster-whisper-base/") for u in got)
    assert (out / "model.bin").read_bytes() == files["model.bin"]


def test_build_all_adds_the_new_pieces_to_every_platform_tree():
    src = (ROOT / "packaging" / "build_all.py").read_text(encoding="utf-8")
    for fn in ("mac_app", "linux_app", "win_app"):
        body = src.split(f"def {fn}(")[1].split("\ndef ")[0]
        assert "media_tools(" in body and "speech_model(" in body, fn


# ---------------------------------------------------------------- the app: PATH, environment, the Mac message
def test_ffmpeg_and_pandoc_are_found_by_name_and_the_model_by_variable(app, monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setattr(bundled.sys, "platform", "linux")
    ff, pd = _exe(app / "vendor" / "ffmpeg" / "ffmpeg"), _exe(app / "vendor" / "pandoc" / "pandoc")
    model = _model(app)
    assert bundled.bundled_ffmpeg() == str(ff) and bundled.bundled_pandoc() == str(pd) and bundled.whisper_model_dir() == model
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    parts = os.environ["PATH"].split(os.pathsep)
    assert str(ff.parent) in parts and str(pd.parent) in parts and parts[-1] == "/usr/bin"
    assert os.environ["MESHPOOL_WHISPER_MODEL"] == str(model) and os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["MESHPOOL_PYTHON"]


def test_a_source_checkout_leaves_ffmpeg_alone(app, monkeypatch):
    monkeypatch.setattr(bundled, "packaged", lambda: False)
    _exe(app / "vendor" / "ffmpeg" / "ffmpeg")
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    assert os.environ["PATH"] == "/usr/bin" and "HF_HUB_OFFLINE" not in os.environ


def test_apple_silicon_ffmpeg_says_it_needs_macos_12_and_stays_off_path(app, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    for rel, says in (("11.7.10", True), ("11.0", True), ("12.0", False), ("14.5", False), ("", False)):
        monkeypatch.setattr("platform.mac_ver", lambda rel=rel: (rel, ("", "", ""), "arm64"))
        msg = bundled.ffmpeg_needs_newer_macos()
        assert bool(msg) is says, rel
        if says:
            assert "macOS 12 or later" in msg and "Speech to text" in msg
    monkeypatch.setattr("platform.mac_ver", lambda: ("11.7", ("", "", ""), "arm64"))
    ff = _exe(app / "vendor" / "ffmpeg" / "ffmpeg")
    monkeypatch.setattr("os.name", "posix")
    monkeypatch.setenv("PATH", "/usr/bin")
    bundled.use_bundled_tools()
    assert str(ff.parent) not in os.environ["PATH"].split(os.pathsep)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")             # Intel: the same file needs only macOS 11
    assert bundled.ffmpeg_needs_newer_macos() == ""


def test_the_system_check_lists_the_bundled_tools_with_their_versions(app, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    _exe(app / "vendor" / "ffmpeg" / "ffmpeg", "#!/bin/sh\necho 'ffmpeg version 7.0.2-static'\n")
    _exe(app / "vendor" / "pandoc" / "pandoc", "#!/bin/sh\necho 'pandoc 2.19.2'\n")
    _model(app)
    rows = {r["name"]: r for r in config.system_check()}
    assert rows["ffmpeg"]["ok"] and "ffmpeg version 7.0.2" in rows["ffmpeg"]["detail"] and "ships with Meshpool" in rows["ffmpeg"]["detail"]
    assert rows["pandoc"]["ok"] and "pandoc 2.19.2" in rows["pandoc"]["detail"]
    assert "Speech to text" in rows and "Notebooks" in rows
    assert all(rows[k]["optional"] for k in ("ffmpeg", "pandoc", "Speech to text", "Notebooks"))


def test_the_system_check_says_when_a_program_is_missing_from_the_install(app, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda *a, **k: "/usr/bin/pandoc")          # a pandoc on the PATH is not used
    rows = {r["name"]: r for r in config.system_check()}
    assert not rows["pandoc"]["ok"] and "reinstall Meshpool" in rows["pandoc"]["fix"]
    assert not rows["Speech to text"]["ok"] and "reinstall Meshpool" in rows["Speech to text"]["fix"]


def test_a_broken_program_shows_as_a_row_not_a_crash(app, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "linux")
    _exe(app / "vendor" / "ffmpeg" / "ffmpeg", "#!/bin/sh\nexit 3\n")
    rows = {r["name"]: r for r in config.system_check()}
    assert not rows["ffmpeg"]["ok"] and "did not start" in rows["ffmpeg"]["detail"]


def test_a_mac_too_old_for_ffmpeg_shows_the_reason_in_settings(app, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setattr("platform.mac_ver", lambda: ("11.7", ("", "", ""), "arm64"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    _exe(app / "vendor" / "ffmpeg" / "ffmpeg")
    row = next(r for r in config.system_check() if r["name"] == "ffmpeg")
    assert not row["ok"] and row["detail"] == "needs macOS 12 or later" and "macOS 12" in row["fix"]


def test_program_version_never_raises(tmp_path):
    assert bundled.program_version(str(tmp_path / "nothing-here")) == ""


# ---------------------------------------------------------------- skills
SHIPPED = r"(faster[-_ ]whisper|imageio[-_]ffmpeg|pypandoc|nbformat|nbconvert|ipykernel|duckdb|pypdf|pypdfium2|pillow[-_]heif|pillow\b|reportlab|matplotlib|openpyxl|python-docx|python-pptx|pandas|ffmpeg|pandoc)"


def test_no_default_skill_tells_the_agent_to_install_something_that_ships():
    pat = re.compile(rf"(pip3?\s+install|python3?\s+-m\s+pip\s+install|apt(-get)?\s+install|brew\s+install|choco\s+install|"
                     rf"winget\s+install|npm\s+install|npx\s)[^\n]{{0,80}}{SHIPPED}|{SHIPPED}[^\n]{{0,60}}(if missing|if not present)", re.I)
    for d in sorted(SKILLS.iterdir()):
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        text = re.sub(r"(never|do not|don't|not)\s+(try to\s+)?(pip|apt|brew|npm)[^.\n]*", "", text, flags=re.I)   # "never pip install it" is the point
        hit = pat.search(text)
        assert not hit, f"{d.name}: {hit.group(0)!r}"


def test_the_skills_that_used_to_install_things_now_name_what_ships():
    t = {n: (SKILLS / n / "SKILL.md").read_text(encoding="utf-8") for n in
         ("transcribe-audio", "convert-files", "pdf-tools", "image-tools", "notebooks", "query-database", "make-chart")}
    assert "transcribe.py" in t["transcribe-audio"] and "never leaves it" in t["transcribe-audio"]
    assert "not available" in t["transcribe-audio"] and "text transcript" in t["transcribe-audio"]
    assert "`pandoc`" in t["convert-files"] and "`ffmpeg`" in t["convert-files"] and "macOS 12" in t["convert-files"]
    assert "Ghostscript" not in t["pdf-tools"] and "pypdf" in t["pdf-tools"]
    assert "register_heif_opener" in t["image-tools"]
    assert "nbformat" in t["notebooks"] and "ipykernel" in t["notebooks"]
    assert "import duckdb" in t["query-database"]


def test_the_transcribe_helper_exits_cleanly_when_the_model_is_missing(tmp_path):
    script = SKILLS / "transcribe-audio" / "transcribe.py"
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    env = {k: v for k, v in os.environ.items() if k != "MESHPOOL_WHISPER_MODEL"}
    r = subprocess.run([sys.executable, str(script), str(audio)], capture_output=True, text=True, env=env)
    assert r.returncode == 3 and "not available" in r.stderr and "text transcript" in r.stderr
    r = subprocess.run([sys.executable, str(script), str(tmp_path / "missing.mp3")], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "No such file" in r.stderr


def test_an_unedited_old_default_skill_is_refreshed_and_an_edited_one_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    dst = SK.folder()
    dst.mkdir(parents=True)
    names = sorted(SK.REFRESHED_DEFAULTS)
    (dst / ".defaults-installed").write_text(json.dumps(sorted(d.name for d in SKILLS.iterdir() if d.is_dir())))
    old = {}
    for n in names:                                                   # the text the previous release shipped
        old[n] = subprocess.run(["git", "show", f"2405870:app/defaults/skills/{n}/SKILL.md"], capture_output=True,
                                cwd=ROOT).stdout
    if not all(old.values()):
        pytest.skip("the previous release is not in this checkout's history")
    for n in names:
        (dst / n).mkdir()
        (dst / n / "SKILL.md").write_bytes(old[n])
    (dst / "notebooks" / "SKILL.md").write_text(old["notebooks"].decode() + "\nMy own rule.\n")      # the user edited this one
    SK.ensure_defaults()
    assert "never pip install" in (dst / "query-database" / "SKILL.md").read_text()
    assert (dst / "transcribe-audio" / "transcribe.py").is_file()
    assert "My own rule." in (dst / "notebooks" / "SKILL.md").read_text()
    assert "never pip install" not in (dst / "notebooks" / "SKILL.md").read_text()
    SK.ensure_defaults()                                              # and it stays so
    assert "My own rule." in (dst / "notebooks" / "SKILL.md").read_text()
