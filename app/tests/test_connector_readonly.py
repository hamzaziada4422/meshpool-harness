"""Connectors in read-only mode: chats get only the read tools."""
from meshpool_harness.agent import mcp as M


def test_read_only_keeps_read_tools():
    tools = [{"name": "search_issues"}, {"name": "get_file_contents"}, {"name": "create_issue"},
             {"name": "update_pull_request"}, {"name": "send_message"}, {"name": "list_labels"},
             {"name": "delete_file"}, {"name": "add_comment_to_pending_review"},
             {"name": "runReport", "annotations": {"readOnlyHint": True}},           # the server's own word wins
             {"name": "fetch_thing", "annotations": {"readOnlyHint": False}},
             {"name": "clean_cache", "annotations": {"destructiveHint": True}}]
    assert [t["name"] for t in M.read_only_tools(tools)] == ["search_issues", "get_file_contents", "list_labels",
                                                             "runReport"]


def test_read_only_is_an_allow_list():                                      # third review, 1
    names = ["apply_migration", "push_files", "deploy_edge_function", "buy_domain", "sandbox_exec", "createIssue",
             "sendMessage", "deletePage", "fork_repository", "getIssue", "listProjects", "search_code", "frobnicate"]
    assert [t["name"] for t in M.read_only_tools([{"name": n} for n in names])] == ["getIssue", "listProjects",
                                                                                    "search_code"]


def test_the_setting_reaches_the_server_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHPOOL_HOME", str(tmp_path))
    from meshpool_harness import mcp_apps as CN
    cid = next(c["id"] for c in CN._all() if c["auth"] == "none")
    CN.set_enabled(cid, True)
    assert not CN.servers()[cid].get("read_only")
    CN.set_read_only(cid, True)
    assert CN.servers()[cid]["read_only"] is True
    assert next(r for r in CN.listing() if r["id"] == cid)["read_only"] is True
