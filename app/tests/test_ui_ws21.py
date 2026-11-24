"""P-11: many tabs on one streaming chat share one event stream (Web Lock leader + BroadcastChannel), so the browser's
six-connection limit is never reached and no tab stalls. Real browser, real app server, mock model ("think slowly" keeps
the chat working for about 30 seconds)."""
from __future__ import annotations

import time

import pytest

from test_ui_ws19 import app, make_page  # noqa: F401  (the fixtures)

TABS = 10


def test_p11_ten_tabs_on_a_streaming_chat_hold_one_connection_and_none_stall(make_page, app):  # noqa: F811
    first = make_page()
    first.evaluate("p => { S.draft.project = p; S.draft.mode = 'auto'; renderHero(); }", app["proj"])
    first.fill("#input", "think slowly please")
    first.press("#input", "Enter")
    first.wait_for_function("() => S.cur && S.cur.view && S.cur.view.workdir && S.cur.busy", timeout=60000)
    cid = first.evaluate("S.cur.id")
    ctx = first.context
    streams = []
    ctx.on("request", lambda r: streams.append(r.url) if "/events" in r.url else None)
    tabs = [first]
    for _ in range(TABS - 1):
        pg = ctx.new_page()
        pg.add_init_script("try { localStorage.setItem('mp_onbDone', '1'); } catch (e) {}")
        pg.goto(app["base"] + "/")
        pg.wait_for_function("() => typeof S === 'object' && S.projects && S.projects.length > 0", timeout=30000)
        pg.evaluate("id => openThread(id)", cid)
        tabs.append(pg)
    for pg in tabs:
        pg.wait_for_function("id => S.cur && S.cur.id === id && S.cur.busy", arg=cid, timeout=30000)
    time.sleep(3)                                             # let every tab ask for its stream
    holders = sum(1 for pg in tabs if pg.evaluate("!!(S.cur && S.cur.es)"))
    relays = sum(1 for pg in tabs if pg.evaluate("!!(S.cur && S.cur.relay)"))
    assert holders == 1, f"{holders} tabs hold their own event stream"
    assert relays == TABS, "every tab should be following through the shared stream"
    assert len(streams) <= 2, streams                          # one stream (a reconnect at most), not one per tab
    # no stall: an ordinary request from the last tab still answers at once
    ms = tabs[-1].evaluate("async () => { const t = performance.now(); await api('/api/agent?limit=5'); return performance.now() - t; }")
    assert ms < 3000, ms
    # the leader goes away: another tab takes the stream over and the rest keep getting the chat
    tabs[0].close()
    time.sleep(2)
    assert sum(1 for pg in tabs[1:] if pg.evaluate("!!(S.cur && S.cur.es)")) == 1
    # and the answer reaches a follower tab when the chat finishes
    tabs[-1].wait_for_function("() => S.cur && S.cur.view && S.cur.view.state === 'idle' && document.body.innerText.includes('done thinking')",
                               timeout=90000)
