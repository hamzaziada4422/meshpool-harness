"""meshpool in Excel, PowerPoint and Word: an Office add-in whose side panel talks to the meshpool app running on
this computer.

Office loads an add-in's panel from an HTTPS address named in its manifest. meshpool serves the panel itself, at
https://localhost:<port>/office/taskpane.html (port 44317 unless MH_OFFICE_PORT says otherwise), from the same local
server as the app, so a document's selection only ever goes to this computer's meshpool and from there to the model
you set up, like any other chat.

  meshpool office install [--apps excel,powerpoint,word] [--yes]
      1. makes a certificate for localhost (a local certificate authority limited to localhost and 127.0.0.1; its
         private key is deleted as soon as the certificate is signed, so it can never sign anything else),
      2. asks before it adds that authority to your user's trusted certificates (Windows and macOS),
      3. writes the add-in manifest and registers it with desktop Office (sideloading).
  meshpool office status | uninstall | manifest

Nothing here sends data anywhere. Office itself loads office.js from Microsoft's CDN, as every Office add-in must.
"""
from __future__ import annotations

import datetime as _dt
import ipaddress
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from meshpool_harness import config as C

APPS = {"excel": ("Workbook", "com.microsoft.Excel"), "powerpoint": ("Presentation", "com.microsoft.Powerpoint"),
        "word": ("Document", "com.microsoft.Word")}
LABEL = {"excel": "Excel", "powerpoint": "PowerPoint", "word": "Word"}
CA_NAME = "meshpool local add-in authority"
DEV_CERTS = Path.home() / ".office-addin-dev-certs"       # Microsoft's office-addin-dev-certs tool, when it was used


class OfficeError(RuntimeError):
    pass


def port() -> int:
    try:
        return int(os.environ.get("MH_OFFICE_PORT", "") or 44317)
    except ValueError:
        return 44317


def folder() -> Path:
    return C.home() / "office"


def base_url() -> str:
    return f"https://localhost:{port()}"


# ---------------------------------------------------------------------------------------------- certificates

def cert_paths() -> tuple[Path, Path] | None:
    """(certificate, key) the HTTPS listener uses: meshpool's own, or the ones Microsoft's dev tool made and trusted."""
    own = (folder() / "localhost.crt", folder() / "localhost.key")
    if own[0].is_file() and own[1].is_file():
        return own
    ms = (DEV_CERTS / "localhost.crt", DEV_CERTS / "localhost.key")
    if ms[0].is_file() and ms[1].is_file():
        return ms
    return None


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def make_certs(days: int = 825) -> dict:
    """A certificate authority limited by name constraints to localhost and 127.0.0.1, and one certificate for
    localhost signed by it. The authority's private key is thrown away once the certificate is signed: with the
    cryptography package it never touches the disk; with the openssl command it lives only in a temporary folder
    that is deleted right after. Needs the cryptography package (pip install cryptography) or the openssl command."""
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    try:
        return _make_with_cryptography(d, days)
    except ImportError:
        pass
    if shutil.which("openssl"):
        return _make_with_openssl(d, days)
    raise OfficeError("Making a certificate needs the cryptography package or the openssl command. Install one "
                      "(pip install cryptography), or run: npx office-addin-dev-certs install")


def _make_with_cryptography(d: Path, days: int) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
    only_local = x509.NameConstraints(
        permitted_subtrees=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32"))],
        excluded_subtrees=None)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now)
          .not_valid_after(now + _dt.timedelta(days=days))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(only_local, critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                       crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + _dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (d / "ca.crt").write_bytes(ca.public_bytes(pem))
    (d / "localhost.crt").write_bytes(leaf.public_bytes(pem))
    _write_private(d / "localhost.key", key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
    del ca_key                                            # never saved: it cannot sign anything else
    return {"ca": str(d / "ca.crt"), "cert": str(d / "localhost.crt"), "key": str(d / "localhost.key")}


def _make_with_openssl(d: Path, days: int) -> dict:
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "ca.ext").write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n"
                                  "nameConstraints=critical,permitted;DNS:localhost,permitted;IP:127.0.0.1/255.255.255.255\n")
        (t / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n"
                                    "subjectAltName=DNS:localhost,IP:127.0.0.1\n")
        run = lambda *a: subprocess.run(["openssl", *a], cwd=t, check=True, capture_output=True, timeout=60)  # noqa: E731
        run("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
        run("req", "-new", "-key", "ca.key", "-subj", f"/CN={CA_NAME}", "-out", "ca.csr")
        run("x509", "-req", "-in", "ca.csr", "-signkey", "ca.key", "-days", str(days), "-extfile", "ca.ext",
            "-out", "ca.crt")
        run("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "leaf.key")
        run("req", "-new", "-key", "leaf.key", "-subj", "/CN=localhost", "-out", "leaf.csr")
        run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
            "-days", str(days), "-extfile", "leaf.ext", "-out", "leaf.crt")
        shutil.copy(t / "ca.crt", d / "ca.crt")         # the temporary folder, with ca.key, goes when this block ends
        shutil.copy(t / "leaf.crt", d / "localhost.crt")
        _write_private(d / "localhost.key", (t / "leaf.key").read_bytes())
    return {"ca": str(d / "ca.crt"), "cert": str(d / "localhost.crt"), "key": str(d / "localhost.key")}


def days_left(cert: Path) -> int | None:
    """Days until the certificate expires, or None when it cannot be read."""
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(Path(cert).read_bytes())
        end = getattr(c, "not_valid_after_utc", None) or c.not_valid_after.replace(tzinfo=_dt.timezone.utc)
        return (end - _dt.datetime.now(_dt.timezone.utc)).days
    except ImportError:
        pass
    except (OSError, ValueError):
        return None
    if not shutil.which("openssl"):
        return None
    for d in (0, 30):                                     # openssl can only say "expires within N seconds"
        r = subprocess.run(["openssl", "x509", "-checkend", str(d * 86400), "-noout", "-in", str(cert)],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            return d - 1 if d else -1
    return 30


def _pair_loads(cert: Path, key: Path) -> bool:
    """The certificate and its key load together, as the HTTPS listener loads them. A crash between make_certs' writes
    or a damaged file leaves a pair that does not."""
    import ssl
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(cert), str(key))
        return True
    except (OSError, ssl.SSLError, ValueError):
        return False


def _own() -> bool:
    return (folder() / "localhost.crt").is_file() and (folder() / "localhost.key").is_file()


def trusted() -> bool:
    """meshpool's own certificate was added to the user's trusted ones (Microsoft's dev certificate is trusted by
    its own tool)."""
    return (folder() / "trusted").exists() or (not _own() and cert_paths() is not None)


def trust_command() -> list[str] | None:
    """The command that adds meshpool's authority to this user's trusted certificates, or None where Office has no
    desktop app (Linux: use Office on the web and let the browser trust the certificate)."""
    ca = str(folder() / "ca.crt")
    if sys.platform == "win32":
        return ["certutil", "-user", "-addstore", "Root", ca]
    if sys.platform == "darwin":
        return ["security", "add-trusted-cert", "-r", "trustRoot", "-k",
                str(Path.home() / "Library" / "Keychains" / "login.keychain-db"), ca]
    return None


def untrust_command() -> list[str] | None:
    if sys.platform == "win32":
        return ["certutil", "-user", "-delstore", "Root", CA_NAME]
    if sys.platform == "darwin":
        return ["security", "delete-certificate", "-c", CA_NAME]
    return None


# ---------------------------------------------------------------------------------------------- manifest

def addin_id() -> str:
    """A stable id for this install's add-in, kept next to the manifest."""
    p = folder() / "addin-id.txt"
    try:
        v = p.read_text().strip()
        uuid.UUID(v)
        return v
    except (OSError, ValueError):
        v = str(uuid.uuid4())
        folder().mkdir(parents=True, exist_ok=True)
        p.write_text(v)
        return v


def manifest_xml(apps: list[str] | None = None) -> str:
    """The add-in-only XML manifest: a task pane and a meshpool button on the Home tab of each app."""
    from meshpool_harness import __version__ as V
    apps = [a for a in (apps or list(APPS)) if a in APPS]
    if not apps:
        raise OfficeError("Name at least one of: excel, powerpoint, word.")
    b = base_url()
    nums = [int(x) for x in str(V).split(".")[:3] if x.isdigit()] + [0, 0, 0]
    version = ".".join(str(n) for n in nums[:3]) + ".0"
    icons = "".join(f'<bt:Image size="{s}" resid="Icon.{s}"/>' for s in (16, 32, 80))

    def host(kind: str) -> str:
        return f'''      <Host xsi:type="{kind}">
        <DesktopFormFactor>
          <GetStarted>
            <Title resid="GetStarted.Title"/>
            <Description resid="GetStarted.Description"/>
            <LearnMoreUrl resid="Help.Url"/>
          </GetStarted>
          <FunctionFile resid="Commands.Url"/>
          <ExtensionPoint xsi:type="PrimaryCommandSurface">
            <OfficeTab id="TabHome">
              <Group id="Meshpool.Group">
                <Label resid="Group.Label"/>
                <Icon>{icons}</Icon>
                <Control xsi:type="Button" id="Meshpool.Open">
                  <Label resid="Button.Label"/>
                  <Supertip>
                    <Title resid="Button.Label"/>
                    <Description resid="Button.Tooltip"/>
                  </Supertip>
                  <Icon>{icons}</Icon>
                  <Action xsi:type="ShowTaskpane">
                    <TaskpaneId>MeshpoolPane</TaskpaneId>
                    <SourceLocation resid="Taskpane.Url"/>
                  </Action>
                </Control>
              </Group>
            </OfficeTab>
          </ExtensionPoint>
        </DesktopFormFactor>
      </Host>
'''
    hosts = "".join(f'    <Host Name="{APPS[a][0]}"/>\n' for a in apps)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0"
           xmlns:ov="http://schemas.microsoft.com/office/taskpaneappversionoverrides"
           xsi:type="TaskPaneApp">
  <Id>{addin_id()}</Id>
  <Version>{version}</Version>
  <ProviderName>meshpool</ProviderName>
  <DefaultLocale>en-US</DefaultLocale>
  <DisplayName DefaultValue="meshpool"/>
  <Description DefaultValue="Ask meshpool about your selection and put the answer back in your workbook, deck or document."/>
  <IconUrl DefaultValue="{b}/office/icon-32.png"/>
  <HighResolutionIconUrl DefaultValue="{b}/office/icon-64.png"/>
  <SupportUrl DefaultValue="{b}/office/help.html"/>
  <AppDomains>
    <AppDomain>{b}</AppDomain>
  </AppDomains>
  <Hosts>
{hosts}  </Hosts>
  <DefaultSettings>
    <SourceLocation DefaultValue="{b}/office/taskpane.html"/>
  </DefaultSettings>
  <Permissions>ReadWriteDocument</Permissions>
  <VersionOverrides xmlns="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="VersionOverridesV1_0">
    <Hosts>
{"".join(host(APPS[a][0]) for a in apps)}    </Hosts>
    <Resources>
      <bt:Images>
        <bt:Image id="Icon.16" DefaultValue="{b}/office/icon-16.png"/>
        <bt:Image id="Icon.32" DefaultValue="{b}/office/icon-32.png"/>
        <bt:Image id="Icon.80" DefaultValue="{b}/office/icon-80.png"/>
      </bt:Images>
      <bt:Urls>
        <bt:Url id="Commands.Url" DefaultValue="{b}/office/commands.html"/>
        <bt:Url id="Taskpane.Url" DefaultValue="{b}/office/taskpane.html"/>
        <bt:Url id="Help.Url" DefaultValue="{b}/office/help.html"/>
      </bt:Urls>
      <bt:ShortStrings>
        <bt:String id="GetStarted.Title" DefaultValue="meshpool is ready"/>
        <bt:String id="Group.Label" DefaultValue="meshpool"/>
        <bt:String id="Button.Label" DefaultValue="meshpool"/>
      </bt:ShortStrings>
      <bt:LongStrings>
        <bt:String id="GetStarted.Description" DefaultValue="Open meshpool from the Home tab. Keep the meshpool app running on this computer."/>
        <bt:String id="Button.Tooltip" DefaultValue="{escape("Open the meshpool panel: ask about your selection and insert the answer.")}"/>
      </bt:LongStrings>
    </Resources>
  </VersionOverrides>
</OfficeApp>
'''


def write_manifest(apps: list[str] | None = None) -> Path:
    folder().mkdir(parents=True, exist_ok=True)
    p = folder() / "manifest.xml"
    p.write_text(manifest_xml(apps), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------------------------- sideloading

_WEF_KEY = r"Software\Microsoft\Office\16.0\WEF\Developer"


def _mac_wef(app: str) -> Path:
    return Path.home() / "Library" / "Containers" / APPS[app][1] / "Data" / "Documents" / "wef"


def sideload(manifest: Path, apps: list[str]) -> list[str]:
    """Registers the manifest with desktop Office, the way Microsoft's own developer tools do: a value under
    HKCU\\...\\WEF\\Developer on Windows, a copy in each app's wef folder on macOS. Returns what was done."""
    try:
        return _sideload(manifest, apps)
    except OSError as e:                                  # e.g. macOS refused access to another app's folder
        raise OfficeError(f"Could not add the add-in to Office ({e}). On a Mac, allow meshpool to reach other apps' "
                          "data when asked, or add it by hand: Insert, Add-ins, My Add-ins, then choose "
                          f"{manifest}.") from None


def _sideload(manifest: Path, apps: list[str]) -> list[str]:
    done = []
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WEF_KEY) as k:
            winreg.SetValueEx(k, addin_id(), 0, winreg.REG_SZ, str(manifest))
        done.append("Registered with Office for Windows (Excel, PowerPoint and Word read it when they start).")
    elif sys.platform == "darwin":
        for a in apps:
            d = _mac_wef(a)
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(manifest, d / "meshpool.manifest.xml")
            done.append(f"Added to {LABEL[a]} for Mac.")
    return done


def unsideload() -> list[str]:
    done = []
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WEF_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, addin_id())
                done.append("Removed from Office for Windows.")
        except OSError:
            pass
    elif sys.platform == "darwin":
        for a in APPS:
            p = _mac_wef(a) / "meshpool.manifest.xml"
            if p.exists():
                p.unlink()
                done.append(f"Removed from {LABEL[a]} for Mac.")
    return done


def status() -> dict:
    cp = cert_paths()
    man = folder() / "manifest.xml"
    loaded = []
    if sys.platform == "darwin":
        loaded = [a for a in APPS if (_mac_wef(a) / "meshpool.manifest.xml").exists()]
    elif sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WEF_KEY) as k:
                winreg.QueryValueEx(k, addin_id())
                loaded = _apps_installed()
        except OSError:
            loaded = []
    left = days_left(cp[0]) if cp else None
    return {"url": base_url() + "/office/taskpane.html", "port": port(), "certificate": str(cp[0]) if cp else "",
            "manifest": str(man) if man.exists() else "", "sideloaded": loaded, "trusted": trusted(),
            "days_left": left, "needs_trust": bool(cp) and not trusted() and trust_command() is not None}


def _apps_installed() -> list[str]:
    try:
        got = [a for a in (folder() / "apps.txt").read_text().split() if a in APPS]
        return got or list(APPS)
    except OSError:
        return list(APPS)


def icon_png(size: int) -> bytes:
    """The meshpool mark at the size Office asks for."""
    import io
    from PIL import Image
    src = Path(__file__).resolve().parent.parent / "ui" / "brand" / "mark.png"
    im = Image.open(src).convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


TRUST_NOTE = ("Office only loads the panel over HTTPS from a certificate this computer trusts, so meshpool adds its own "
              "certificate authority to your user's trusted certificates. It is limited to localhost and 127.0.0.1, "
              "and its private key was thrown away once the certificate was signed, so it cannot vouch for any other site.")


def install(apps: list[str] | None = None, confirm=lambda cmd: False) -> list[str]:
    """Certificate (made once, trusted when confirm(command) says yes), manifest, sideloading. Returns what was done,
    line by line. confirm is the user's say: a terminal question, or the click on Settings' button."""
    apps = [a for a in (apps or list(APPS)) if a in APPS]
    out = []
    left = days_left(folder() / "localhost.crt") if _own() else None
    broken = _own() and not _pair_loads(folder() / "localhost.crt", folder() / "localhost.key")
    if _own() and (broken or (left is not None and left < 30)):   # renew (its authority's key is gone)
        old = untrust_command()
        if old and trusted():
            _run(old)
        (folder() / "trusted").unlink(missing_ok=True)
        for n in ("ca.crt", "localhost.crt", "localhost.key"):
            (folder() / n).unlink(missing_ok=True)
        out.append("The certificate could not be loaded, so meshpool makes a new one." if broken else
                   "The certificate was about to expire, so meshpool makes a new one.")
    if cert_paths() is None:
        made = make_certs()
        out.append(f"Made a certificate for localhost: {made['cert']}")
    else:
        out.append(f"Using the certificate at {cert_paths()[0]}")
    cmd = trust_command()
    if _own() and not trusted():                         # new, or skipped or failed last time: ask (again)
        if not cmd:
            out.append("There is no desktop Office on this system. For Office on the web, trust " +
                       str(folder() / "ca.crt") + " in your browser.")
        elif confirm(cmd):
            r = _run(cmd)
            if r.returncode == 0:
                (folder() / "trusted").write_text(_dt.datetime.now().isoformat())
                out.append("Trusted the meshpool certificate.")
            else:
                out.append(f"Trusting the certificate did not work: {(r.stderr or r.stdout).strip()[:300]}")
        else:
            out.append("Skipped trusting the certificate. Office will not load the panel until it is trusted; "
                       "run this again to trust it.")
    man = write_manifest(apps)
    (folder() / "apps.txt").write_text(" ".join(apps))
    out.append(f"Wrote the add-in manifest: {man}")
    out += sideload(man, apps)
    out.append("Next: keep meshpool running (it serves the panel at " + base_url() + "/office/taskpane.html), "
               "restart " + ", ".join(LABEL[x] for x in apps) + ", and click meshpool on the Home tab.")
    out.append("Office on the web: Home > Add-ins > More add-ins > Upload My Add-in, and choose " + str(man) + ".")
    return out


def _run(cmd: list[str]):
    """A trust command. The system may show a dialog; two minutes is plenty, and a dialog hidden behind a window
    must not hang the app."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "no answer within two minutes (is a system dialog waiting?)")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def uninstall() -> list[str]:
    out = unsideload()
    cmd = untrust_command()
    if cmd and (folder() / "ca.crt").exists() and trusted():
        r = _run(cmd)
        out.append("Removed the meshpool certificate from your trusted ones." if r.returncode == 0 else
                   f"Could not remove the certificate: {(r.stderr or r.stdout).strip()[:200]}")
    for n in ("ca.crt", "localhost.crt", "localhost.key", "manifest.xml", "trusted", "apps.txt"):
        (folder() / n).unlink(missing_ok=True)
    out.append("The meshpool add-in is uninstalled.")
    return out


# ---------------------------------------------------------------------------------------------- command line

def cli(args: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="meshpool office", description="meshpool in Excel, PowerPoint and Word.")
    ap.add_argument("action", choices=["install", "status", "uninstall", "manifest"])
    ap.add_argument("--apps", default="excel,powerpoint,word", help="which apps (default: all three)")
    ap.add_argument("--yes", action="store_true", help="add the certificate to your trusted ones without asking")
    a = ap.parse_args(args)
    apps = [x.strip().lower() for x in a.apps.split(",") if x.strip()]
    bad = [x for x in apps if x not in APPS]
    if bad:
        print(f"Unknown app: {', '.join(bad)}. Use excel, powerpoint or word.", file=sys.stderr)
        return 2
    try:
        if a.action == "manifest":
            print(write_manifest(apps))
            return 0
        if a.action == "status":
            s = status()
            print(f"Panel address: {s['url']}")
            print(f"Certificate:   {s['certificate'] or 'none yet (run: meshpool office install)'}" +
                  (f" (expires in {s['days_left']} days)" if s.get("days_left") is not None else "") +
                  (" (not trusted yet: run meshpool office install)" if s.get("needs_trust") else ""))
            print(f"Manifest:      {s['manifest'] or 'none yet'}")
            print(f"Sideloaded in: {', '.join(s['sideloaded']) or 'none'}")
            return 0
        if a.action == "uninstall":
            for line in uninstall():
                print(line)
            return 0
        def confirm(cmd):
            print(TRUST_NOTE + " Command: " + " ".join(cmd))
            return a.yes or input("Add it now? [y/N] ").strip().lower() in ("y", "yes")
        for line in install(apps, confirm):
            print(line)
        return 0
    except OfficeError as e:
        print(str(e), file=sys.stderr)
        return 1
