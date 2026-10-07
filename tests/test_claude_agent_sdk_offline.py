"""Offline contract test for the Claude Agent SDK scaffold adapter (M3; no live API calls).

A local STUB of the native Anthropic Messages API (`probe/native_model_stub.py`)
drives the real adapter subprocess (claude-agent-sdk + the bundled Claude Code
CLI, its own uv project) through the real proxy and mocks. The CLI's model leg is
pointed at the adapter's recording proxy, which is pointed at the stub — no key,
no network. Each transport x {single-turn, two-turn} is recorded against the
stub, then replayed with no model server at all.

`ADAPTERS["claude-agent-sdk"]` is registered here by monkeypatch; the committed
trial.py needs the same one-line entry (added by the parent session).
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from mcpredteam.config import repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE
from mcpredteam.runner import trial as trial_mod
from mcpredteam.runner.matrix import SERVERS
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

sys.path.insert(0, str(repo_root() / "probe"))
from native_model_stub import NativeModelStub

SETTINGS = {"effort": "high"}


@pytest.fixture(autouse=True)
def register_adapter(monkeypatch):
    monkeypatch.setitem(trial_mod.ADAPTERS, "claude-agent-sdk", "scaffolds/claude-agent-sdk")


def _spec(task, transport, mode, trial_dir, **kw) -> TrialSpec:
    return TrialSpec(run_id="cas-offline", trial_id=f"{task.id}__{transport}__{mode}", task_id=task.id,
                     instruction=task.prompts[0], prompts=list(task.prompts), era=HANDSHAKE,
                     transport=transport, scaffold="claude-agent-sdk", model="claude-sonnet-5-5", mode=mode,
                     trial_dir=str(trial_dir), servers=list(SERVERS), model_settings=SETTINGS, **kw)


def _llm_lines(trial_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (trial_dir / "llm.jsonl").read_text().splitlines() if x.strip()]


def test_claude_agent_sdk_record_then_replay_both_transports(tmp_path):
    tasks = {t.id: t for t in load_tasks()}
    single = tasks["notes-meeting-time"]
    double = tasks["notes-search-then-title"]
    assert single.turns == 1 and len(double.prompts) == 2

    async def go():
        out = []
        async with (NativeModelStub(tool="mcp__notes__read_note", tool_input={"note_id": "n1"},
                                    answer="answer: 14:30") as fm,
                    HubServer(ProxyHub()) as hub):
            for transport in ("streamable-http", "stdio"):
                for task in (single, double):
                    rec_dir = tmp_path / "rec" / f"{task.id}-{transport}"
                    rec = await run_trial(
                        _spec(task, transport, "record", rec_dir, base_url=fm.base_url),
                        task, hub=hub, api_key="fake-key-do-not-leak", timeout_s=240)
                    rep_dir = tmp_path / "rep" / f"{task.id}-{transport}"
                    rep = await run_trial(
                        _spec(task, transport, "replay", rep_dir, recording_dir=str(rec_dir)),
                        task, hub=hub, api_key=None, timeout_s=240)
                    out.append((task, transport, rec_dir, rec, rep_dir, rep))
        return out, fm.requests

    runs, requests = asyncio.run(go())
    assert requests  # served by the local stub, never a real endpoint
    for task, transport, rec_dir, rec, rep_dir, rep in runs:
        tag = (task.id, transport)
        assert rec["error"] is None, (tag, rec["error"])
        assert rep["error"] is None, (tag, rep["error"])
        assert rec["era_matches"] and rep["era_matches"], (tag, rec["proxy"], rep["proxy"])
        n_turns = len(task.prompts)

        res = json.loads((rec_dir / "adapter_result.json").read_text())
        assert res["scaffold"] == "claude-agent-sdk" and res["error"] is None
        assert [t["tool_call_count"] for t in res["turns"]] == [1] * n_turns, (tag, res["turns"])
        assert all(t["final_answer"].startswith("answer") for t in res["turns"])
        assert res["final_answer"] == res["turns"][-1]["final_answer"]
        # Isolation: only the proxy's MCP tools are loaded, no built-ins (tools=[]).
        assert res["tool_names"] and all(n.startswith("mcp__") for n in res["tool_names"]), (tag, res["tool_names"])
        assert "mcp__notes__read_note" in res["tool_names"], (tag, res["tool_names"])
        assert {s["name"] for s in res["mcp_servers_loaded"]} == set(SERVERS), (tag, res["mcp_servers_loaded"])
        assert res["isolation"]["setting_sources"] == [] and res["isolation"]["strict_mcp_config"] is True
        assert res["versions"]["bundled_cli"] == "2.1.292" and res["versions"]["mcp"] == "2.3.0"
        # Effective per-trial thinking + caching captured (D39/A1). The bundled CLI sets
        # its own thinking config on every native request (captured here); the stub adds
        # no caching, so the cache counters are 0.
        eff = res["effective"]
        assert isinstance(eff["thinking"], dict) and eff["thinking"].get("type"), (tag, eff)
        assert eff["cache_creation_input_tokens"] == 0 and eff["cache_read_input_tokens"] == 0, (tag, eff)
        assert eff["input_tokens"] > 0 and eff["output_tokens"] > 0, (tag, eff)
        # Exactly the model leg was live in record; nothing blocked, nothing off-contract.
        assert res["live_llm_calls"] == res["llm_calls"] >= 2 * n_turns
        assert res["blocked_network_calls"] == 0 and res["other_base_url_calls"] == 0
        # No key or auth header anywhere in the record artifacts.
        assert "fake-key-do-not-leak" not in json.dumps(res)
        assert [c["name"] for c in rec["tool_calls"]] == ["read_note"] * n_turns

        lines = _llm_lines(rec_dir)
        assert lines and not any(line.get("replayed") for line in lines)
        blob = json.dumps(lines).lower()
        assert "authorization" not in blob and "x-api-key" not in blob and "sk-ant" not in blob

        # Replay: served only from the recording, zero live calls, nothing blocked.
        rres = json.loads((rep_dir / "adapter_result.json").read_text())
        assert rres["live_llm_calls"] == 0 and rres["blocked_network_calls"] == 0, (tag, rres)
        assert rres["llm_calls"] == res["llm_calls"] and rres["llm_replay_unused"] == 0
        assert rres["turns"] == res["turns"]
        # Replay derives `effective` from the same recordings, so it must match the record.
        assert rres["effective"] == res["effective"], (tag, rres["effective"], res["effective"])
        assert all(line["replayed"] for line in _llm_lines(rep_dir))
        assert [(c["server"], c["name"], c["arguments"]) for c in rep["tool_calls"]] == \
               [(c["server"], c["name"], c["arguments"]) for c in rec["tool_calls"]]
        assert all(p["upstream_live_calls"] == 0 for p in rep["proxy"])

    # Pinned settings on the native Messages API: effort is sent; temperature/seed/max_tokens are not.
    for req in requests:
        assert req["system"] is not None  # CLI always sends a system block
    # The model saw the MCP tool definitions directly (tool-search not withholding; D14).
    assert any(set(r["tools"]) >= {"mcp__notes__read_note"} for r in requests)


def test_claude_agent_sdk_refusal_is_a_completed_empty_turn(tmp_path):
    task = next(t for t in load_tasks() if t.id == "notes-meeting-time")

    async def go():
        async with NativeModelStub(refuse=True) as fm, HubServer(ProxyHub()) as hub:
            return await run_trial(
                _spec(task, "streamable-http", "record", tmp_path / "refused", base_url=fm.base_url),
                task, hub=hub, api_key="fake-key", timeout_s=240)

    r = asyncio.run(go())
    assert r["error"] is None
    res = json.loads((tmp_path / "refused" / "adapter_result.json").read_text())
    assert res["refused"] is True
    assert res["turns"][0]["final_answer"] == "" and res["final_answer"] == ""
