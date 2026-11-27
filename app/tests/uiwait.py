"""Waiting in the UI tests for something only the server can answer (an API call in the page).

Playwright's page.wait_for_function does not wait for a Promise: a predicate that returns one (`api(...).then(...)`,
an async function) is truthy at once, so the wait returned before the server had done the thing waited for, and the
test then raced it (a busy computer lost the race: the chats in test_ux018._chats were still running, the "Never show
tips" switch was not saved yet). wait_js asks again until the answer itself is true."""
from __future__ import annotations

import time

import pytest


def wait_js(page, fn: str, arg=None, timeout: float = 60, every: float = 0.25):
    """Evaluate fn(arg) in the page (a Promise is awaited) until it returns something truthy, and return that."""
    end = time.monotonic() + timeout
    while True:
        got = page.evaluate(fn, arg)
        if got:
            return got
        if time.monotonic() > end:
            pytest.fail(f"not true within {timeout:g} s: {fn}")
        page.wait_for_timeout(every * 1000)
