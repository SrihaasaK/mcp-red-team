"""Offline contract test for the LangGraph scaffold adapter (M3; no live API calls).

A scripted OpenAI-compatible fake model drives the real adapter subprocess
(langchain-mcp-adapters + mcp 1.30.0, its own uv project) through the real proxy
and mocks. Each transport x {single-turn, two-turn} task is recorded against the
fake, then replayed with no model server at all.

`ADAPTERS["langgraph"]` is registered here by monkeypatch; the committed
trial.py needs the same one-line entry.
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE
from mcpredteam.runner import trial as trial_mod
from mcpredteam.runner.matrix import SERVERS
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

SETTINGS = {"parallel_tool_calls": False, "max_tokens": 8192,
            "extra_body": {"output_config": {"effort": "high"}}}


class FakeModel:
    """Last message a user turn -> call list_notes; last message a tool result -> answer."""

    def __init__(self, finish_override: str | None = None) -> None:
        self.bodies: list[dict] = []
        self.finish_override = finish_override
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        app = Starlette(routes=[Route("/v1/chat/completions", self._complete, methods=["POST"])])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))

    async def _complete(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.bodies.append(body)
        if self.finish_override:
            msg, finish = {"role": "assistant", "content": ""}, self.finish_override
        elif body["messages"][-1]["role"] == "tool":
            msg, finish = {"role": "assistant", "content": f"answer {len(self.bodies)}: 14:30"}, "stop"
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{len(self.bodies)}", "type": "function",
                "function": {"name": "list_notes", "arguments": "{}"}}]}
            finish = "tool_calls"
        return JSONResponse({
            "id": "chatcmpl-fake", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}})

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


@pytest.fixture(autouse=True)
def register_langgraph(monkeypatch):
    monkeypatch.setitem(trial_mod.ADAPTERS, "langgraph", "scaffolds/langgraph")


def _spec(task, transport, mode, trial_dir, **kw) -> TrialSpec:
    return TrialSpec(run_id="lg-offline", trial_id=f"{task.id}__{transport}__{mode}", task_id=task.id,
                     instruction=task.prompts[0], prompts=list(task.prompts), era=HANDSHAKE,
                     transport=transport, scaffold="langgraph", model="claude-sonnet-5-5", mode=mode,
                     trial_dir=str(trial_dir), servers=list(SERVERS), model_settings=SETTINGS, **kw)


def _llm_lines(trial_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (trial_dir / "llm.jsonl").read_text().splitlines() if x.strip()]


def test_langgraph_record_then_replay_both_transports(tmp_path):
    tasks = {t.id: t for t in load_tasks()}
    single = tasks["notes-meeting-time"]
    double = next(t for t in tasks.values() if t.turns == 2 and t.id.startswith("notes-"))
    assert len(double.prompts) == 2

    async def go():
        out = []
        async with FakeModel() as fm, HubServer(ProxyHub()) as hub:
            for transport in ("streamable-http", "stdio"):
                for task in (single, double):
                    rec_dir = tmp_path / "rec" / f"{task.id}-{transport}"
                    rec = await run_trial(
                        _spec(task, transport, "record", rec_dir, base_url=f"http://127.0.0.1:{fm.port}/v1/"),
                        task, hub=hub, api_key="fake-key-do-not-leak", timeout_s=180)
                    rep_dir = tmp_path / "rep" / f"{task.id}-{transport}"
                    rep = await run_trial(
                        _spec(task, transport, "replay", rep_dir, recording_dir=str(rec_dir)),
                        task, hub=hub, api_key=None, timeout_s=180)
                    out.append((task, transport, rec_dir, rec, rep_dir, rep))
        return out, fm.bodies

    runs, bodies = asyncio.run(go())
    assert bodies  # served by the local fake, never a real endpoint
    for task, transport, rec_dir, rec, rep_dir, rep in runs:
        tag = (task.id, transport)
        assert rec["error"] is None, (tag, rec["error"])
        assert rep["error"] is None, (tag, rep["error"])
        assert rec["era_matches"] and rep["era_matches"], (tag, rec["proxy"], rep["proxy"])
        n_turns = len(task.prompts)

        res = json.loads((rec_dir / "adapter_result.json").read_text())
        assert res["scaffold"] == "langgraph" and res["error"] is None
        assert [t["tool_call_count"] for t in res["turns"]] == [1] * n_turns
        assert all(t["final_answer"].startswith("answer") for t in res["turns"])
        assert res["final_answer"] == res["turns"][-1]["final_answer"]
        assert res["versions"]["mcp"] == "1.30.0"
        assert res["live_llm_calls"] == 2 * n_turns == res["llm_calls"]
        assert "fake-key-do-not-leak" not in json.dumps(res)
        assert [c["name"] for c in rec["tool_calls"]] == ["list_notes"] * n_turns

        lines = _llm_lines(rec_dir)
        assert len(lines) == 2 * n_turns and not any(line.get("replayed") for line in lines)
        assert "authorization" not in json.dumps(lines).lower()

        # Replay: served only from the recording, zero live calls, nothing blocked.
        rres = json.loads((rep_dir / "adapter_result.json").read_text())
        assert rres["live_llm_calls"] == 0 and rres["blocked_network_calls"] == 0
        assert rres["llm_calls"] == 2 * n_turns and rres["llm_replay_unused"] == 0
        assert rres["turns"] == res["turns"]
        assert all(line["replayed"] for line in _llm_lines(rep_dir))
        assert [(c["server"], c["name"], c["arguments"]) for c in rep["tool_calls"]] == \
               [(c["server"], c["name"], c["arguments"]) for c in rec["tool_calls"]]
        assert all(p["upstream_live_calls"] == 0 for p in rep["proxy"])

    # Pinned settings on the wire: effort, max tokens, one tool call at a time; never temperature/seed.
    for b in bodies:
        assert b["output_config"] == {"effort": "high"}
        assert b["max_completion_tokens"] == 8192
        assert b["parallel_tool_calls"] is False
        assert "temperature" not in b and "seed" not in b
        assert b["messages"][0]["role"] == "system"
    # Two-turn history is threaded: the second turn's first call carries turn 1's messages.
    two_turn = [b for b in bodies if sum(m["role"] == "user" for m in b["messages"]) == 2]
    assert two_turn and any(m["role"] == "tool" for m in two_turn[0]["messages"])


def test_langgraph_refusal_is_a_completed_empty_turn(tmp_path):
    task = next(t for t in load_tasks() if t.id == "notes-meeting-time")

    async def go():
        async with FakeModel(finish_override="content_filter") as fm, HubServer(ProxyHub()) as hub:
            return await run_trial(
                _spec(task, "streamable-http", "record", tmp_path / "refused",
                      base_url=f"http://127.0.0.1:{fm.port}/v1/"),
                task, hub=hub, api_key="fake-key", timeout_s=180)

    r = asyncio.run(go())
    assert r["error"] is None
    res = json.loads((tmp_path / "refused" / "adapter_result.json").read_text())
    assert res["refused"] is True
    assert res["turns"][0]["final_answer"] == "" and res["final_answer"] == ""
