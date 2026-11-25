"""One connector (GitHub, Jira, Linear ...) for an outside harness (Hermes, Codex, Pi, OpenCode), as a stdio MCP server.

A harness given the connector's URL and its Authorization header keeps that header for the life of its process, and an
OAuth access token lasts about an hour: every connector call then failed with 401 until the harness restarted. Through
this bridge the harness never holds the token: each call goes through Meshpool's own MCP client, which fetches a fresh
token when the server refuses the old one.

    python -m meshpool_harness.agent.connector_bridge <connector id>
"""
from __future__ import annotations

import sys

from meshpool_harness.agent.mcp_stdio import Tool, ToolFailed, serve


def _tool(server, name: str, meta: dict) -> Tool:
    def call(**args):
        (out, failed), images = server.call_with_images(name, args)
        if failed:
            raise ToolFailed(out)
        return [{"type": "text", "text": out}] + [{"type": "image", "data": im["data"], "mimeType": im["mime"]}
                                                  for im in images]
    t = Tool(call, meta.get("description") or "")
    schema = meta.get("inputSchema")
    if isinstance(schema, dict):
        t.schema = schema
    return t


def connect(cid: str):
    """Meshpool's MCP client for a connected connector, started. Raises when it is not connected or cannot start."""
    from meshpool_harness import config as C
    from meshpool_harness import mcp_apps as CN
    from meshpool_harness.agent import mcp as M
    spec = CN.servers().get(cid)
    if not spec:
        raise RuntimeError(f"The {cid} connector is not connected in Meshpool.")
    server = M.Server(cid, spec, C.runs_dir() / "mcp-logs", [])
    server.start(timeout=60)
    if server.status != "connected":
        raise RuntimeError(server.error or f"The {cid} connector did not start.")
    return server


def tools_for(server) -> dict[str, Tool]:
    return {t["name"]: _tool(server, t["name"], t) for t in server.tools}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        raise SystemExit("usage: python -m meshpool_harness.agent.connector_bridge <connector id>")
    cid = argv[0]
    try:
        server = connect(cid)
        tools = tools_for(server)
    except Exception as e:  # noqa: BLE001  still answer the harness: one tool that says what is wrong
        msg = str(e)

        def broken(**_):
            raise ToolFailed(msg)
        tools = {"connector_status": Tool(broken, f"The {cid} connector is unavailable: {msg}")}
    serve(f"meshpool-{cid}", tools)


if __name__ == "__main__":
    main()
