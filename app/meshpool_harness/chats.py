"""The chats API for front ends that are not the web page (the terminal, scripts).

Nothing lives here: every name is the function the app's routes call (ui/agent_api.py), so a chat made in the terminal
is the same chat the app shows, with the same transcript, checkpoints, checks and receipts. Importing this module
instead of agent_api keeps the terminal off the app's internals; moving the code later changes one file.
"""
from __future__ import annotations

from meshpool_harness.ui.agent_api import (  # noqa: F401
    SESSIONS, ChatBusy, ProjectMissing, Session, create, get, harness_change, harness_info, listing, mcp_info,
    mcp_save, mcp_test, read_meta, run_scheduled, saved_messages, search, thread_action, view, write_meta)
