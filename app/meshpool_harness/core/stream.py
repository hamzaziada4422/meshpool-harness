"""One reader for every model reply (SSE framing, time guards, chunk assembly). Filled in by ws02-stream.

Assembler protocol (chat completions: ChatAssembler here; Anthropic Messages: anthropic_wire.StreamAssembler):
    a = Assembler(on_text=None, on_reasoning=None)
    a.feed(event: str, data: str)   # one dispatched SSE event; `data` is the raw (joined) data text, not decoded
    a.result() -> dict              # chat-completions-shaped reply; raises StreamCut when the stream did not finish
"""
from __future__ import annotations


class StreamCut(RuntimeError):
    """The stream ended before the reply was complete. The message starts with "HTTP 0:" so classify_error reads it
    as transient and the call is retried."""

    def __init__(self, detail: str = ""):
        super().__init__("HTTP 0: the stream ended before the reply was complete" + (f" ({detail})" if detail else ""))
