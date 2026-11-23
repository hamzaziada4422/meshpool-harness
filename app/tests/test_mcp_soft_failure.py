"""0.0.13: the soft-failure detector only marks a result failed on structured signals or a short whole-text error."""
import json

import pytest

from meshpool_harness.agent import mcp_assist as MA

LOG = "Error: connection reset by peer\n2026-11-23 10:00:01 INFO retrying\n2026-11-23 10:00:02 INFO ok\n" * 8
GREP = "\n".join(f"src/app/mod{i}.py:{i}:    raise ValueError('x')  # Error: handled" for i in range(30))
DOCS = ("Error: handling in the Foo API. When a request fails the server returns a JSON body with a code and a "
        "message; retry with backoff. " * 6)


@pytest.mark.parametrize("text", [
    LOG, GREP, DOCS,
    "Error: " + "x" * 400,                              # long text starting with the word
    "Error handling in Python is done with try",         # no colon
    "Failed tests: 0. All 12 passed.",                   # contains the word
    "Traceback (most recent call last):\n  File 'a.py', line 3\nValueError: x\n",
    '{"entries": [{"level": "error", "msg": "disk"}], "count": 1}',
    '{"status": "failed", "build": 12, "steps": []}',     # a job record that is the answer
    '{"status": "ok", "error": null}',
    '{"ok": true, "error": "partial", "items": [1]}',
    '{"status": 200, "items": [1]}',
    '{"results": [], "errors": []}',
    '[{"error": "x"}]',                                   # a list is data
    '{"error": "x", "tickets": [{"id": 1}]}',
    '{"isError": true}',                                  # the MCP layer handles isError; as text it has no verdict
])
def test_normal_output_is_not_a_failure(text):
    assert MA.soft_failure(text) is False


@pytest.mark.parametrize("text", [
    '{"ok": false, "error": "closing requires a note"}',
    '{"success": false}',
    '{"error": "bad cursor"}',
    '{"errors": ["a", "b"]}',
    '{"status": 404, "message": "not found"}',
    '{"status_code": 503}',
    '{"statusCode": "429", "message": "slow down"}',
    '{"status": "error", "message": "x"}',
    '{"jsonrpc": "2.0", "id": 3, "error": {"code": -32602, "message": "Invalid params"}}',
    "Error: booking failed (code E42)",
    "FAILED: no such file",
    "Fatal: not allowed",
])
def test_structured_signals_and_short_error_phrases_fail(text):
    assert MA.soft_failure(text) is True


def test_a_log_reader_result_is_passed_on_as_ok():
    pre, post, ok = MA.classify(LOG, False)
    assert ok and post == "" and pre == ""


def test_a_docs_search_starting_with_error_is_ok():
    assert MA.classify(DOCS, False)[2] is True


def test_status_number_with_data_is_not_a_failure():
    assert MA.soft_failure(json.dumps({"status": 500, "results": [1]})) is False
