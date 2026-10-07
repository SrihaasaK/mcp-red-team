"""A local STUB of the native Anthropic Messages API (`POST /v1/messages`,
streaming SSE), for driving the Claude Agent SDK adapter fully offline.

No network, no key: the Claude Code CLI is pointed at this server via the
adapter's recording proxy (ANTHROPIC_BASE_URL). The stub is scriptable — it calls
one MCP tool per user prompt, then answers — and records the `tools`, `system`,
and `messages` of every request so the probe can read what the model was offered
(exposure, tool-search). Mirrors the LangGraph `FakeModel` in intent.

Shared by `probe/claude_agent_sdk_probe.py` and `tests/test_claude_agent_sdk_offline.py`.
"""

from __future__ import annotations

import asyncio
import json
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


def _event(obj: dict) -> str:
    return f"event: {obj['type']}\ndata: {json.dumps(obj)}\n\n"


def _sse_tool_use(tool_id: str, name: str, tool_input: dict, in_tokens: int) -> str:
    return "".join([
        _event({"type": "message_start", "message": {
            "id": "msg_stub", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": in_tokens, "output_tokens": 0}}}),
        _event({"type": "content_block_start", "index": 0,
                "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}}),
        _event({"type": "content_block_delta", "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool_input)}}),
        _event({"type": "content_block_stop", "index": 0}),
        _event({"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 6}}),
        _event({"type": "message_stop"}),
    ])


def _sse_text(text: str, in_tokens: int, stop_reason: str = "end_turn") -> str:
    blocks = [
        _event({"type": "message_start", "message": {
            "id": "msg_stub", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": in_tokens, "output_tokens": 0}}}),
        _event({"type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}}),
    ]
    if text:
        blocks.append(_event({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "text_delta", "text": text}}))
    blocks += [
        _event({"type": "content_block_stop", "index": 0}),
        _event({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 5}}),
        _event({"type": "message_stop"}),
    ]
    return "".join(blocks)


def _session_id(body: dict) -> str:
    """The CLI stamps a per-run session id in metadata.user_id; one trial = one
    session. Used to count requests per trial independently of message parsing."""
    try:
        return json.loads((body.get("metadata") or {}).get("user_id") or "{}").get("session_id", "")
    except (ValueError, AttributeError):
        return ""


class NativeModelStub:
    """One MCP tool call per user prompt, then a short answer.

    tool: fully-qualified name the CLI exposes an MCP tool under, e.g.
    ``mcp__notes__list_notes``. ``refuse`` makes every response a content-policy
    refusal (to exercise D32). ``answer`` is the final text.
    """

    def __init__(self, tool: str = "mcp__notes__list_notes", tool_input: dict | None = None,
                 answer: str = "answer: 14:30", refuse: bool = False) -> None:
        self.tool, self.tool_input = tool, tool_input or {}
        self.answer, self.refuse = answer, refuse
        self.requests: list[dict] = []
        self._per_session: dict[str, int] = {}  # session_id -> requests seen
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        app = Starlette(routes=[Route("/{path:path}", self._handle,
                                      methods=["GET", "POST", "PUT", "DELETE"])])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))

    async def _handle(self, request: Request) -> Response:
        raw = await request.body()
        if not request.url.path.endswith("/v1/messages"):
            return Response(json.dumps({"ok": True, "data": []}), media_type="application/json")
        body = json.loads(raw) if raw else {}
        self.requests.append({
            "tools": [t.get("name") for t in body.get("tools", []) if isinstance(t, dict)],
            "tool_descriptions": {t.get("name"): t.get("description") for t in body.get("tools", [])
                                  if isinstance(t, dict)},
            "system": body.get("system"),
            "messages": body.get("messages"),
        })
        in_tokens = len(json.dumps(body.get("messages", [])))
        sid = _session_id(body)
        n = self._per_session.get(sid, 0) + 1
        self._per_session[sid] = n
        # Each turn is [tool_use, answer]: odd request in a session calls the tool,
        # even request answers. Independent of the CLI's message bookkeeping.
        if self.refuse:
            sse = _sse_text("", in_tokens, stop_reason="refusal")
        elif n % 2 == 1:
            sse = _sse_tool_use(f"toolu_{len(self.requests)}", self.tool, self.tool_input, in_tokens)
        else:
            sse = _sse_text(f"{self.answer}", in_tokens)
        return Response(sse, media_type="text/event-stream")

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        # Stop the mock server without ever blocking on its drain. By now the trial
        # is over and every recorded request is captured, but a dead CLI's lingering
        # keep-alive socket can stall uvicorn's shutdown forever, and awaiting the
        # serve task (even `wait_for(self.task)`, whose post-timeout step re-awaits
        # the cancelled task) re-blocks on that same drain. So request a forced stop
        # and cancel the task, but never await it: it is finalized when the loop next
        # runs -- asyncio.run()'s shutdown in the test, moot under os._exit in the
        # probe. A later phase binds a fresh port, so nothing collides meanwhile.
        self.server.should_exit = True
        self.server.force_exit = True
        self.task.cancel()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/"
