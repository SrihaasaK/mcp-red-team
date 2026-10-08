"""End-to-end D56: a mitigation BlockedCall becomes an isError tools/call result that is NOT
forwarded upstream, is tagged in the log, and is then excluded from the scored trajectory
(reviewer finding 6). Exercises Relay._blocked + Trajectory.from_trial together, with a dummy
blocking interceptor and no upstream (a blocked call never reaches the upstream)."""

from __future__ import annotations

import asyncio
import json

from mcpredteam.mitigate.block import BlockedCall
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.proxy.interfaces import TrialCtx
from mcpredteam.proxy.relay import STATELESS, Relay

META = "io.modelcontextprotocol/protocolVersion"


class BlockHttpPost:
    """Dummy mitigation: blocks http_post, passes every other hook through."""

    async def on_discover(self, caps, ctx):
        return caps

    async def on_tools_list(self, tools, ctx):
        return tools

    async def on_tool_call(self, name, args, ctx):
        if name == "http_post":
            raise BlockedCall("coarse_taint", "no allowed flow to http")
        return name, args

    async def on_tool_result(self, name, result, ctx):
        return result

    async def on_resource_read(self, uri, content, ctx):
        return content

    async def on_mrtr(self, req, ctx):
        return req

    async def on_final_answer(self, text, ctx):
        return None


def _ctx() -> TrialCtx:
    return TrialCtx(run_id="t", trial_id="tb", era=STATELESS, scaffold="x", model="none",
                    transport="streamable-http", task_id=None)


def _post(mid: int) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": "http_post", "arguments": {"url": "http://x", "body": "CANARY-X"},
                       "_meta": {META: STATELESS}}}


def test_relay_blocks_sink_tags_log_and_trajectory_excludes(tmp_path):
    async def go():
        relay = Relay(server="http", ctx=_ctx(), mode="record", trial_dir=tmp_path,
                      interceptors=(BlockHttpPost(),))
        # no relay.start(): a blocked call never reaches the upstream
        reply = await relay.handle(_post(7))
        summary = relay.summary()
        await relay.close()
        return reply, summary

    reply, summary = asyncio.run(go())

    # 1. the scaffold receives a valid isError tools/call RESULT (not a JSON-RPC error)
    assert reply.status == 200
    assert reply.body["result"]["isError"] is True
    assert "[mitigation:coarse_taint]" in reply.body["result"]["content"][0]["text"]
    assert "error" not in reply.body  # it is a result, not a protocol error

    # 2. the block is recorded and tagged
    assert summary["mitigation_blocks"] == [
        {"tool": "http_post", "server": "http", "kind": "coarse_taint", "reason": "no allowed flow to http"}
    ]
    log = (tmp_path / "proxy.http.jsonl").read_text().splitlines()
    recs = [json.loads(x) for x in log]
    assert any(r["dir"] == "c2s" and r["stage"] == "in" and (r.get("message") or {}).get("method") == "tools/call"
               for r in recs), "the attempted call is still logged c2s/in (pre-interception)"
    assert any(r.get("mitigation_blocked") == "coarse_taint" for r in recs)
    assert not any(r["dir"] == "c2s" and r["stage"] == "out" for r in recs), "a blocked call is never forwarded"

    # 3. the trajectory EXCLUDES the blocked call -> the oracle scores zero executed calls
    tr = Trajectory.from_trial(tmp_path)
    assert tr.turns[0].tool_calls == ()
