"""Drive the proxy with the real mcp 2.3.0 client, in both transports and both forced eras.

No model is involved: this checks era forcing, faithful relaying, and that a
recorded session replays identically with zero upstream calls.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from mcpredteam.config import repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE, STATELESS
from mcpredteam.scaffolds.contract import TrialSpec

ERAS = [HANDSHAKE, STATELESS]


def make_spec(trial_dir: Path, era: str, transport: str, mode: str, recording_dir: Path | None = None) -> TrialSpec:
    spec = TrialSpec(
        run_id="test", trial_id=trial_dir.name, task_id="none", instruction="",
        era=era, transport=transport, scaffold="mcp-client", model="none", mode=mode,
        trial_dir=str(trial_dir), servers=["notes", "http"],
        recording_dir=str(recording_dir) if recording_dir else None,
    )
    spec.write()
    return spec


async def exercise(target) -> tuple[list[str], str, str]:
    async with Client(target, read_timeout_seconds=20) as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        note = await c.call_tool("read_note", {"note_id": "n1"})
        again = await c.list_tools()
        return tools, note.content[0].text, ",".join(sorted(t.name for t in again.tools))


def expected_refusals(era: str) -> list[str]:
    return ["server/discover"] if era == HANDSHAKE else []


@pytest.mark.parametrize("era", ERAS)
def test_http_forced_era_record_then_replay(tmp_path, era):
    async def go():
        hub = ProxyHub()
        async with HubServer(hub) as srv:
            rec = make_spec(tmp_path / "rec", era, "streamable-http", "record")
            hub.register(rec)
            out_rec = await exercise(srv.url(rec.trial_id, "notes"))
            sum_rec = await hub.close_trial(rec.trial_id)
            rep = make_spec(tmp_path / "rep", era, "streamable-http", "replay", recording_dir=tmp_path / "rec")
            hub.register(rep)
            out_rep = await exercise(srv.url(rep.trial_id, "notes"))
            sum_rep = await hub.close_trial(rep.trial_id)
        return out_rec, sum_rec[0], out_rep, sum_rep[0]

    out_rec, s_rec, out_rep, s_rep = asyncio.run(go())
    assert "Cedar" in out_rec[1]
    assert out_rec == out_rep
    for s in (s_rec, s_rep):
        assert s["era_matches"], s
        assert s["refusals"] == expected_refusals(era)
        assert s["proxy_errors"] == []
    assert s_rec["upstream_live_calls"] > 0
    assert s_rep["upstream_live_calls"] == 0


@pytest.mark.parametrize("era", ERAS)
def test_stdio_forced_era_record_then_replay(tmp_path, era):
    def params(trial_dir: Path) -> StdioServerParameters:
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcpredteam.proxy.stdio", "--trial-dir", str(trial_dir), "--server", "notes"],
            cwd=str(repo_root()),
        )

    import json
    rec = make_spec(tmp_path / "rec", era, "stdio", "record")
    out_rec = asyncio.run(exercise(params(Path(rec.trial_dir))))
    rep = make_spec(tmp_path / "rep", era, "stdio", "replay", recording_dir=tmp_path / "rec")
    out_rep = asyncio.run(exercise(params(Path(rep.trial_dir))))
    s_rec = json.loads((tmp_path / "rec" / "proxy.notes.summary.json").read_text())
    s_rep = json.loads((tmp_path / "rep" / "proxy.notes.summary.json").read_text())
    assert "Cedar" in out_rec[1]
    assert out_rec == out_rep
    for s in (s_rec, s_rep):
        assert s["era_matches"], s
        assert s["refusals"] == expected_refusals(era)
        assert s["proxy_errors"] == []
    assert s_rec["upstream_live_calls"] > 0
    assert s_rep["upstream_live_calls"] == 0
