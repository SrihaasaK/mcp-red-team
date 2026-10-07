"""Offline end-to-end check of the M2 measurement loop (no live API calls).

A scripted OpenAI-compatible fake model drives the real adapter → proxy →
attack interceptors → mocks → oracle → aggregation. It verifies the loop runs
and scores coherently; it does NOT assert attack success (the fake is scripted,
not a real agent). The live run is what produces real ASR.
"""

import asyncio
import json
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcpredteam.runner.evaluate import run_dev


class FakeModel:
    """Calls list_notes once (exercises tools/list + a tool_result), then answers."""

    def __init__(self) -> None:
        self.requests = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        app = Starlette(routes=[Route("/v1/chat/completions", self._complete, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))

    async def _complete(self, request: Request) -> JSONResponse:
        self.requests += 1
        body = await request.json()
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        offers_list_notes = any(
            (t.get("function") or {}).get("name") == "list_notes" for t in body.get("tools", [])
        )
        if has_tool_result or not offers_list_notes:
            msg, finish = {"role": "assistant", "content": "Offline fake answer: 14:30."}, "stop"
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "list_notes", "arguments": "{}"}}]}
            finish = "tool_calls"
        return JSONResponse({
            "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}})

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


def test_measurement_loop_runs_offline_end_to_end(tmp_path):
    async def go():
        async with FakeModel() as fm:
            summary = await run_dev(
                "offline", ["claude-sonnet-5-5"], n=1, out_root=tmp_path,
                api_key="offline-fake-key", base_url=f"http://127.0.0.1:{fm.port}/v1/",
                one_per_class=True, control_n=1,
            )
            return summary, fm.requests

    summary, fake_requests = asyncio.run(go())
    assert fake_requests > 0  # the fake model served the loop (no real API)
    assert set(summary["aggregate"]["by_class"]) <= {1, 2, 3, 5, 7}
    assert summary["trials"] >= len(summary["dev_configs"])
    records = [json.loads(line) for line in
               (tmp_path / "offline" / "records.jsonl").read_text().splitlines() if line.strip()]
    assert records
    for r in records:
        assert "api.anthropic.com" not in (r["error"] or "")
    ctrl = summary["aggregate"]["control"]
    assert ctrl["trials"] >= 1
    assert isinstance(summary["aborts"], list)
    assert any(r["arm"] == "attack" and r["cls"] in (2, 3, 5) for r in records)
