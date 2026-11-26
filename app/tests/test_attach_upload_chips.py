"""Release review item 10: chips for files dropped into a chat.

- A dropped file is kept in runs/_uploads/chat-<id> and named by its absolute path; the chip opened it with that path
  and files_api refused it ("Path is outside the project"). It opens from the runs folder by a relative path now, and
  only the chat it was dropped into may open it.
- On Windows the chip's label was the whole path: it is the file name.
- A message with two "Attached files" lines kept the first one as text: both go.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_wf_ui018 import PNG, _fake_chat, app, page  # noqa: E402,F401  (the real app and a browser page)


# ---------------------------------------------------------------------------------------------------- files_api
@pytest.fixture
def runs(tmp_path, monkeypatch):
    r = tmp_path / "runs"
    for cid in ("aaa", "bbb"):
        d = r / "_uploads" / f"chat-{cid}"
        d.mkdir(parents=True)
        (d / f"{cid}-data.csv").write_text("a,b\n1,2\n")
    monkeypatch.setenv("MH_RUNS_DIR", str(r))
    return r


def _as(chat):
    from meshpool_harness.ui import files_api as FA
    return FA.CHAT.set(chat)


def test_a_chat_opens_its_own_dropped_file_from_the_runs_folder(runs):
    from meshpool_harness.ui import files_api as FA
    tok = _as("aaa")
    try:
        got = FA.read(str(runs), "_uploads/chat-aaa/aaa-data.csv")
        assert "1,2" in got.get("text", ""), got
        assert [e["name"] for e in FA.list_dir(str(runs), "_uploads/chat-aaa")["entries"]] == ["aaa-data.csv"]
    finally:
        FA.CHAT.reset(tok)


def test_chat_a_cannot_open_chat_bs_dropped_file(runs):
    from meshpool_harness.ui import files_api as FA
    tok = _as("aaa")
    try:
        for rel in ("_uploads/chat-bbb/bbb-data.csv", "_uploads/chat-aaa/../chat-bbb/bbb-data.csv"):
            with pytest.raises(ValueError, match="another chat"):
                FA.read(str(runs), rel)
        with pytest.raises(ValueError, match="another chat"):
            FA.list_dir(str(runs), "_uploads")                     # no listing of every chat's uploads
        names = [h["name"] for h in FA.search(str(runs), "data")["results"]]
        assert names == ["aaa-data.csv"], names                    # a search never walks into chat B's folder
    finally:
        FA.CHAT.reset(tok)
    with pytest.raises(ValueError, match="another chat"):          # a request that names no chat opens neither
        FA.read(str(runs), "_uploads/chat-aaa/aaa-data.csv")


# ---------------------------------------------------------------------------------------------------- the page
def test_both_attached_lines_go_and_windows_paths_show_the_file_name(page):
    _fake_chat(page)
    text = ("Summarise these.\n\nAttached files (in the project folder): notes.csv\n\n"
            "Attached files (in meshpool's attachments for this chat): C:\\Users\\Ann\\.meshpool\\runs\\_uploads\\"
            "chat-t-wf018\\sales q3.xlsx")
    got = page.evaluate("""(t) => { const a = splitAtt(t); const d = document.createElement('div');
        d.innerHTML = userBody(t);
        return {text: a.text, files: a.files, wheres: a.wheres,
                chips: [...d.querySelectorAll('.flink')].map(b => ({label: b.textContent, open: b.dataset.open,
                                                                    root: b.dataset.root || ''})),
                shown: d.textContent}; }""", text)
    assert got["text"] == "Summarise these.", got
    assert "Attached files" not in got["shown"], got
    assert got["wheres"] == ["in the project folder", "in meshpool's attachments for this chat"], got
    assert [c["label"] for c in got["chips"]] == ["notes.csv", "sales q3.xlsx"], got
    win = got["chips"][1]
    assert win["root"] == "C:\\Users\\Ann\\.meshpool\\runs" and win["open"] == "_uploads/chat-t-wf018/sales q3.xlsx", win
    # put back in the box (Undo): each file comes back as a chip with its own place, and is named again that way
    sent = page.evaluate("""(t) => { S.attachments = []; fillInput(t);
        return S.attachments.map(a => [a.name, a.where]); }""", text)
    assert sent == [["notes.csv", "in the project folder"],
                    ["sales q3.xlsx", "in meshpool's attachments for this chat"]], sent
    assert page.errors == [], page.errors


def _drop(page, cid: str, name: str) -> Path:
    mh = Path(page.app["home"]).parent / "mh"
    d = mh / "runs" / "_uploads" / f"chat-{cid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(PNG)
    return d / name


def test_a_chip_for_a_dropped_file_opens_it_in_its_own_chat_only(page):
    f = _drop(page, "t-own", "shot.png")
    line = f"Look at this.\n\nAttached files (in meshpool's attachments for this chat): {f}"
    _fake_chat(page, "t-own")
    page.evaluate("(t) => { const d = document.createElement('div'); d.id = 'chiptest'; d.innerHTML = userBody(t); "
                  "document.querySelector('#col').append(d); }", line)
    with page.expect_response(lambda r: "/api/preview" in r.url and "page=" not in r.url) as resp:
        page.click("#chiptest .flink")
    assert resp.value.status == 200, resp.value.text()
    # the same chip shown in another chat (a pasted message names chat t-own's file): refused
    page.keyboard.press("Escape")
    _fake_chat(page, "t-other")
    page.evaluate("(t) => { const d = document.createElement('div'); d.id = 'chiptest2'; d.innerHTML = userBody(t); "
                  "document.querySelector('#col').append(d); }", line)
    with page.expect_response(lambda r: "/api/preview" in r.url and "page=" not in r.url) as resp:
        page.click("#chiptest2 .flink")
    assert resp.value.status == 400 and "another chat" in resp.value.text(), resp.value.text()
