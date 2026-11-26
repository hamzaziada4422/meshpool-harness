End-to-end check of the published v0.0.16 (dd4ad47d), Linux x64 asset, 2026-11-25 17:30 UTC:
- SHA256 of Meshpool-Linux-x64.tar.gz matches SHA256SUMS.txt (5eee8283...).
- packaging/smoke.py with the bundled Python: imports, Word/PDF, ffmpeg/pandoc/speech/notebook/HEIC, server and chat: PASSED.
  The chat step passed on an "unknown tool 'bash'" reply (stand-in model out of date); patch 0012 fixes the mock and
  makes the smoke fail on it.
- Shipped CLI against the real gateway (deepseek-v4.1-flash): code chat ran app.py and answered "hi", 3 calls, $0.0022.
- Shipped CLI, work profile: "add a Total row ... formula" on costs.xlsx wrote A4 Total, B4 =SUM(B2:B3), 5 calls, $0.0028.
CI on ee728ce5: tests.yml success, Release workflow success.
