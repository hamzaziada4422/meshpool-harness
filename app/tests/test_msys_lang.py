"""Git Bash on Windows: MinGit's /etc/profile.d/lang.sh runs /usr/bin/locale when no locale variable is set, and MinGit
has no locale program, so every login-shell command printed "lang.sh: line 19: /usr/bin/locale: No such file or
directory" into its output (the 0.0.17 release smoke on Windows caught it). config.msys_lang sets LANG first."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness import config as C  # noqa: E402

# line 19 of etc/profile.d/lang.sh in MinGit-2.56.1-64-bit.zip (the pinned bundled Git)
LANG_SH = 'test -z "${LC_ALL:-${LC_CTYPE:-$LANG}}" && export LANG=$(exec /usr/bin/locale-missing -uU)'


def test_lang_is_set_from_the_windows_locale_only_when_no_locale_is_set():
    assert C.msys_lang({}, nt=True, locale_name=lambda: "de-DE")["LANG"] == "de_DE.UTF-8"
    assert C.msys_lang({}, nt=True, locale_name=lambda: "")["LANG"] == "C.UTF-8"
    assert C.msys_lang({}, nt=True, locale_name=lambda: "sr-Latn-RS")["LANG"] == "C.UTF-8"   # not a POSIX name
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):                    # the user's own setting stays
        env = {k: "fr_FR.UTF-8"}
        assert C.msys_lang(dict(env), nt=True, locale_name=lambda: "de-DE") == env
    assert C.msys_lang({}, nt=False) == {}                      # macOS and Linux: untouched


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_mingits_lang_sh_no_longer_runs_the_missing_locale_program():
    bare = subprocess.run(["bash", "-c", LANG_SH], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert "locale-missing" in bare.stderr                      # what every command printed before
    env = C.msys_lang({"PATH": "/usr/bin:/bin"}, nt=True, locale_name=lambda: "en-US")
    fixed = subprocess.run(["bash", "-c", LANG_SH + '; echo "$LANG"'], env=env, capture_output=True, text=True)
    assert fixed.stderr == "" and fixed.stdout.strip() == "en_US.UTF-8"


def test_every_git_bash_launch_on_windows_sets_it():
    src = (Path(C.__file__).parent / "core" / "tools.py").read_text(encoding="utf-8")
    nt = src[src.index('if bash and os.name == "nt":'):src.index("elif bash:")]
    assert "msys_lang(env)" in nt
    wb = (Path(C.__file__).parent / "ui" / "workbench.py").read_text(encoding="utf-8")
    assert "C.msys_lang(env)" in wb[wb.index("def term_run"):wb.index("def term_view")]
