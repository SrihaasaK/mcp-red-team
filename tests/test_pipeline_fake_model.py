"""End-to-end without a real model: adapter subprocess → Agents SDK → proxy → mocks.

A scripted OpenAI-compatible fake model calls one tool, then answers. Each
transport × era is recorded, then replayed offline, and must meet replay
equality (SPEC §9.1). This is the M1 pipeline check that spends no API credits.
"""

import asyncio
import json
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE, STATELESS
from mcpredteam.runner.matrix import SERVERS, replay_run, trial_id
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec


async def fake_completions(request: Request) -> JSONResponse:
    body = await request.json()
    has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
    if has_tool_result:
        message = {"role": "assistant", "content": "The team sync is at 14:30."}
        finish = "stop"
    else:
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "read_note", "arguments": json.dumps({"note_id": "n1"})}}]}
        finish = "tool_calls"
    return JSONResponse({
        "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    })


class FakeModel:
    def __init__(self) -> None:
        self.sock = socket.socket(); self.sock.bind(("127.0.0.1", 0)); self.port = self.sock.getsockname()[1]
        app = Starlette(routes=[Route("/v1/chat/completions", fake_completions, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


def test_fake_model_record_then_replay_all_transports_and_eras(tmp_path):
    task = next(t for t in load_tasks() if t.id == "notes-meeting-time")
    run_dir = tmp_path / "run"

    async def record():
        results = []
        async with FakeModel() as fm, HubServer(ProxyHub()) as hub:
            for transport in ("streamable-http", "stdio"):
                for era in (HANDSHAKE, STATELESS):
                    tid = trial_id(task.id, era, transport)
                    spec = TrialSpec(run_id="fake", trial_id=tid, task_id=task.id, instruction=task.instruction,
                                     era=era, transport=transport, scaffold="openai-agents",
                                     model="claude-sonnet-5-5", mode="record", trial_dir=str(run_dir / tid),
                                     servers=SERVERS, base_url=f"http://127.0.0.1:{fm.port}/v1/",
                                     model_settings={"parallel_tool_calls": False, "max_tokens": 1024})
                    results.append(await run_trial(spec, task, hub=hub, api_key="fake-key", timeout_s=120))
        return results

    recorded = asyncio.run(record())
    for r in recorded:
        assert r["error"] is None, (r["trial_id"], r["error"])
        assert r["verdict"]["passed"], r
        assert r["era_matches"], r["proxy"]
        assert [(c["server"], c["name"]) for c in r["tool_calls"]] == [("notes", "read_note")]
        assert r["adapter"]["live_llm_calls"] == 2

    summary = asyncio.run(replay_run(run_dir, tmp_path / "replay"))
    assert summary["trials"] == 4
    assert summary["eras_covered"] == [HANDSHAKE, STATELESS]
    bad = [c for c in summary["comparisons"] if not c["equal"]]
    assert not bad, bad
