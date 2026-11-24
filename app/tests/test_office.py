"""meshpool in Excel, PowerPoint and Word: certificates, manifest, sideloading, the HTTPS listener, the panel's helpers.
The panel itself is driven end to end by testkit/office_panel.js (stand-in Office objects, real app, mock model)."""
import json
import shutil
import socket
import ssl
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest

from meshpool_harness import office as OF

PANEL_JS = Path(OF.__file__).resolve().parent / "taskpane.js"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path / "mp"))
    monkeypatch.setattr(OF, "DEV_CERTS", tmp_path / "no-dev-certs")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        monkeypatch.setenv("MH_OFFICE_PORT", str(s.getsockname()[1]))
    return tmp_path


def _check_certs(made):
    from cryptography import x509
    ca = x509.load_pem_x509_certificate(Path(made["ca"]).read_bytes())
    leaf = x509.load_pem_x509_certificate(Path(made["cert"]).read_bytes())
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical and {str(getattr(n, "value", n)) for n in nc.value.permitted_subtrees} >= {"localhost"}
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    leaf.verify_directly_issued_by(ca)
    key = Path(made["key"])
    if sys.platform != "win32":
        assert key.stat().st_mode & 0o077 == 0                 # the server key is private to the user
    assert sorted(p.name for p in key.parent.iterdir() if p.suffix == ".key") == ["localhost.key"]   # no CA key kept


def test_certificates_are_limited_to_localhost_and_keep_no_ca_key(home):
    pytest.importorskip("cryptography")
    _check_certs(OF.make_certs())
    assert OF.cert_paths()[0].name == "localhost.crt"


@pytest.mark.skipif(not shutil.which("openssl"), reason="no openssl")
def test_certificates_without_the_cryptography_package(home, monkeypatch):
    pytest.importorskip("cryptography")                       # only to check the result
    def no_crypto(d, days):
        raise ImportError
    monkeypatch.setattr(OF, "_make_with_cryptography", no_crypto)
    _check_certs(OF.make_certs())


def test_microsofts_dev_certificate_is_reused(home, monkeypatch):
    d = home / "dev"
    d.mkdir()
    (d / "localhost.crt").write_text("c")
    (d / "localhost.key").write_text("k")
    monkeypatch.setattr(OF, "DEV_CERTS", d)
    assert OF.cert_paths() == (d / "localhost.crt", d / "localhost.key")


def test_manifest(home):
    from lxml import etree
    x = etree.fromstring(OF.manifest_xml().encode())
    ns = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1"}
    assert [h.get("Name") for h in x.findall("o:Hosts/o:Host", ns)] == ["Workbook", "Presentation", "Document"]
    port = OF.port()
    src = x.find("o:DefaultSettings/o:SourceLocation", ns).get("DefaultValue")
    assert src == f"https://localhost:{port}/office/taskpane.html"
    assert x.find("o:Permissions", ns).text == "ReadWriteDocument"
    assert x.find("o:Id", ns).text == OF.addin_id() == OF.addin_id()       # stable for this install
    urls = [e.get("DefaultValue") for e in x.iter() if (e.get("DefaultValue") or "").startswith("http")]
    assert urls and all(u.startswith(f"https://localhost:{port}/office/") for u in urls)
    assert len(x.findall(".//{http://schemas.microsoft.com/office/taskpaneappversionoverrides}Control")) == 3
    one = etree.fromstring(OF.manifest_xml(["excel"]).encode())
    assert [h.get("Name") for h in one.findall("o:Hosts/o:Host", ns)] == ["Workbook"]
    with pytest.raises(OF.OfficeError):
        OF.manifest_xml(["outlook"])


def test_sideload_on_mac(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    man = OF.write_manifest()
    done = OF.sideload(man, ["excel", "powerpoint", "word"])
    assert len(done) == 3 and "PowerPoint" in done[1]
    for bundle in ("com.microsoft.Excel", "com.microsoft.Powerpoint", "com.microsoft.Word"):
        assert (home / "user/Library/Containers" / bundle / "Data/Documents/wef/meshpool.manifest.xml").is_file()
    assert OF.status()["sideloaded"] == ["excel", "powerpoint", "word"]
    assert len(OF.unsideload()) == 3 and OF.status()["sideloaded"] == []


def test_sideload_on_windows_uses_the_office_developer_key(home, monkeypatch):
    reg = {}

    class Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", REG_SZ=1, KEY_SET_VALUE=2,
        CreateKey=lambda root, path: Key(path), OpenKey=lambda root, path, *a: Key(path),
        SetValueEx=lambda k, name, _r, _t, v: reg.__setitem__((k.path, name), v),
        QueryValueEx=lambda k, name: (reg[(k.path, name)], 1),
        DeleteValue=lambda k, name: reg.pop((k.path, name)))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    man = OF.write_manifest()
    OF.sideload(man, list(OF.APPS))
    assert reg == {(r"Software\Microsoft\Office\16.0\WEF\Developer", OF.addin_id()): str(man)}
    assert OF.status()["sideloaded"] == list(OF.APPS)
    OF.unsideload()
    assert reg == {}
    assert OF.trust_command()[:4] == ["certutil", "-user", "-addstore", "Root"]


def test_install_asks_before_trusting_and_writes_the_manifest(home, monkeypatch, capsys):
    pytest.importorskip("cryptography")
    monkeypatch.setattr(sys, "platform", "darwin")
    ran = []
    monkeypatch.setattr(OF.subprocess, "run", lambda cmd, **k: ran.append(cmd) or types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert OF.cli(["install", "--apps", "excel"]) == 0
    out = capsys.readouterr().out
    assert ran == [] and "Skipped" in out and "limited to localhost" in out
    assert (OF.folder() / "manifest.xml").is_file() and (OF.folder() / "localhost.crt").is_file()
    assert OF.cli(["install", "--apps", "excel"]) == 0                 # a second run reuses the certificate
    assert "Using the certificate" in capsys.readouterr().out
    assert OF.cli(["uninstall"]) == 0
    assert ran and ran[0][:2] == ["security", "delete-certificate"]
    assert not (OF.folder() / "localhost.key").exists()
    assert OF.cli(["install", "--apps", "outlook"]) == 2


def test_https_listener_serves_the_panel_with_the_token(home, monkeypatch):
    pytest.importorskip("cryptography")
    made = OF.make_certs()
    from meshpool_harness.ui import server as S
    assert S._start_office() == ""                              # a certificate alone does not start it
    OF.write_manifest()
    url = S._start_office()
    try:
        assert url == f"https://localhost:{OF.port()}/office/taskpane.html"
        ctx = ssl.create_default_context(cafile=made["ca"])
        base = f"https://localhost:{OF.port()}"
        with urllib.request.urlopen(base + "/office/taskpane.html", context=ctx, timeout=10) as r:
            html = r.read().decode()
            assert S.TOKEN in html and "__MESHPOOL_TOKEN__" not in html
            csp = r.headers["Content-Security-Policy"]
            assert csp.startswith("frame-ancestors 'self' https://*.officeapps.live.com") and "*" not in csp.split()[1:2]
        with urllib.request.urlopen(base + "/office/icon-80.png", context=ctx, timeout=10) as r:
            from PIL import Image
            import io
            assert Image.open(io.BytesIO(r.read())).size == (80, 80)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/settings", context=ctx, timeout=10)
        assert e.value.code == 403                             # the token is still required on this listener
        req = urllib.request.Request(base + "/api/settings", headers={"X-Meshpool-Token": S.TOKEN})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            assert "fields" in json.loads(r.read())
        req = urllib.request.Request(base + "/office/taskpane.html", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, context=ctx, timeout=10)
        assert e.value.code == 403                             # DNS rebinding: only localhost
    finally:
        for srv in S.OFFICE_HTTPD:
            srv.shutdown()
            srv.server_close()
        S.OFFICE_HTTPD.clear()


def test_no_listener_without_a_certificate_or_when_turned_off(home, monkeypatch):
    from meshpool_harness.ui import server as S
    assert S._start_office() == ""
    monkeypatch.setenv("MH_OFFICE", "0")
    assert S._start_office() == ""


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_panel_helpers():
    script = """
const m = require(%s);
const out = {
  table: m.parseTable("Here:\\n\\n| A | B \\\\| C |\\n|---|:--:|\\n| **1** | `=SUM(A1:A2)` |\\n\\nDone"),
  formula: m.toMatrix("Use this:\\n```\\n=SUM(A1:A3)\\n```"),
  tsv: m.toMatrix("```\\na\\tb\\nc\\td\\n```"),
  csv: m.toMatrix("```csv\\nx, y\\n1, 2\\n```"),
  lines: m.toMatrix("one\\n- two"),
  html: m.mdToHtml("# T\\n<img src=x onerror=alert(1)>\\n- **b** and *i*\\n\\n| h |\\n|---|\\n| <script> |"),
  plain: m.plain("## Title\\n- **Bold** point\\n- `code`"),
  addr: m.splitAddress("'Bob''s sheet'!A1:B2"),
  cells: m.cellsText([[1, 2], ["x", 3]], [[1, "=A1*2"], ["x", 3]]),
  msg: m.buildMessage("Excel", {label: "Selected range Sheet1!A1", text: "1", cut: true}, "Sum it", true),
};
console.log(JSON.stringify(out));
""" % json.dumps(str(PANEL_JS))
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert out["table"] == [["A", "B | C"], ["1", "=SUM(A1:A2)"]]
    assert out["formula"] == [["=SUM(A1:A3)"]]
    assert out["tsv"] == [["a", "b"], ["c", "d"]]
    assert out["csv"] == [["x", "y"], ["1", "2"]]
    assert out["lines"] == [["one"], ["• two"]]
    h = out["html"]
    assert "<img" not in h and "&lt;img" in h and "<script>" not in h and "&lt;script&gt;" in h
    assert "<h1>T</h1>" in h and "<strong>b</strong>" in h and "<em>i</em>" in h and "<table>" in h
    assert out["plain"] == "Title\n• Bold point\n• code"
    assert out["addr"] == ["Bob's sheet", "A1:B2"]
    assert out["cells"] == "1\t=A1*2 [2]\nx\t3"
    assert out["msg"].startswith("[I am in Excel") and "(cut to fit)" in out["msg"] and out["msg"].endswith("Sum it")
