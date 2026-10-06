"""Regression tests for the M1 code-review findings (DECISIONS.md D21)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from mcpredteam.config import MissingKeyError, anthropic_api_key, repo_root
from mcpredteam.proxy.http_app import ctx_for
from mcpredteam.proxy.recording import UpstreamRecorder
from mcpredteam.proxy.relay import STATELESS, Relay
from mcpredteam.runner.matrix import replay_run
from mcpredteam.runner.recordings import export_run
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec


def spec_for(trial_dir: Path, mode="record", recording_dir=None, transport="stdio") -> TrialSpec:
    return TrialSpec(run_id="t", trial_id=trial_dir.name, task_id="notes-meeting-time", instruction="",
                     era=STATELESS, transport=transport, scaffold="openai-agents", model="claude-sonnet-5-5",
                     mode=mode, trial_dir=str(trial_dir), servers=["notes"],
                     recording_dir=str(recording_dir) if recording_dir else None)


def test_upstream_id_null_error_passes_through_with_its_status(tmp_path):
    rec = tmp_path / "rec"
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
               "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": STATELESS}}}
    rejection = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Session not found"}}
    UpstreamRecorder(rec / "upstream.notes.jsonl").record(request, [rejection], status=404)
    relay = Relay(server="notes", ctx=ctx_for(spec_for(tmp_path / "rep", "replay", rec)), mode="replay",
                  trial_dir=tmp_path / "rep", recording_dir=rec)
    reply = asyncio.run(relay.handle({**request, "id": 9}))
    assert reply.status == 404
    assert reply.body["id"] == 9 and reply.body["error"]["message"] == "Session not found"
    log = [json.loads(line) for line in (tmp_path / "rep" / "proxy.notes.jsonl").read_text().splitlines()]
    assert any(r["dir"] == "s2c" and r["stage"] == "in" and r.get("upstream_id_null") for r in log)


def test_trial_refuses_a_non_empty_directory(tmp_path):
    d = tmp_path / "trial"
    d.mkdir()
    (d / "result.json").write_text("{}")
    task = next(t for t in load_tasks() if t.id == "notes-meeting-time")
    with pytest.raises(FileExistsError):
        asyncio.run(run_trial(spec_for(d), task, hub=None, api_key=None))


def test_replay_refuses_unsafe_output_and_empty_runs(tmp_path):
    run = tmp_path / "run"
    (run / "t1").mkdir(parents=True)
    with pytest.raises(ValueError):
        asyncio.run(replay_run(run, run / "inside"))
    with pytest.raises(FileNotFoundError):
        asyncio.run(replay_run(run, tmp_path / "out"))


def test_incomplete_recorded_trial_counts_as_not_equal(tmp_path):
    run = tmp_path / "run"
    spec_for(run / "t1").write()  # trial.json but no result.json
    summary = asyncio.run(replay_run(run, tmp_path / "out"))
    assert summary["trials"] == 1 and summary["equal"] == 0


def test_key_with_whitespace_is_rejected(tmp_path):
    env = tmp_path / ".env"
    env.write_text('ANTHROPIC_API_KEY="abc def"\n')
    with pytest.raises(MissingKeyError):
        anthropic_api_key(env)


def test_stdio_proxy_handles_messages_over_64_kib(tmp_path):
    spec = spec_for(tmp_path / "big")
    spec.write()
    params = StdioServerParameters(command=sys.executable, cwd=str(repo_root()),
                                   args=["-m", "mcpredteam.proxy.stdio", "--trial-dir", spec.trial_dir,
                                         "--server", "notes"])

    async def go():
        async with Client(params, read_timeout_seconds=20) as c:
            r = await c.call_tool("search_notes", {"query": "x" * 200_000})
            return r.content[0].text if r.content else ""

    assert asyncio.run(go()) in ("[]", "")


def test_export_strips_paths_and_refuses_leaks(tmp_path):
    run = tmp_path / "run"
    t = run / "t1"
    t.mkdir(parents=True)
    spec_for(t).write()
    (t / "adapter.stderr.txt").write_text("noise")
    (t / "result.json").write_text(json.dumps({"ok": True}))
    exported = export_run(run, tmp_path / "out")
    assert exported == ["t1"]
    assert not (tmp_path / "out" / "t1" / "adapter.stderr.txt").exists()
    assert "/Users/" not in (tmp_path / "out" / "t1" / "trial.json").read_text()
    fake_key = "sk-" + "ant-" + "x" * 12  # built at runtime so no key-shaped literal is committed
    (t / "result.json").write_text(json.dumps({"leak": fake_key}))
    with pytest.raises(ValueError):
        export_run(run, tmp_path / "out2")
