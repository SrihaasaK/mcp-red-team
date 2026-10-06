"""D17 probe: list caching (ttlMs) and MRTR (input_required) through the real proxy.

Runs the mcp 2.3 modern client against the M1 proxy hub and two probe-only
upstreams (registered at runtime, never shipped). It settles, with evidence,
whether these protocol surfaces relay faithfully through the proxy. Per-scaffold
availability is a separate, capability-gated question answered from recordings.
No model and no API key are involved. Writes review/M2/d17_probe.json.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import mcp
from mcp.server import CacheHint, MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel

from mcpredteam.config import repo_root
from mcpredteam.env import UPSTREAMS
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import STATELESS
from mcpredteam.scaffolds.contract import TrialSpec

CANARY = "CANARY-ELICIT-5F3A"  # synthetic; marks the MRTR injection surface


def build_cache_server(ttl_ms: int):
    def factory() -> MCPServer:
        s = MCPServer(name="probe_cache", version="1.0.0", log_level="WARNING",
                      cache_hints={"tools/list": CacheHint(ttl_ms=ttl_ms)})

        @s.tool()
        def ping() -> str:
            """Return pong."""
            return "pong"

        return s

    return factory


def build_mrtr_server():
    s = MCPServer(name="probe_mrtr", version="1.0.0", log_level="WARNING")

    class Confirm(BaseModel):
        approved: bool

    @s.tool()
    async def needs_confirm(action: str, ctx: Context) -> str:
        """Ask the caller to confirm, then report the outcome (exercises MRTR)."""
        res = await ctx.elicit(f"Confirm {action}? [{CANARY}]", Confirm)
        return f"action={action} outcome={getattr(res, 'action', res)!r}"

    return s


def count_methods(trial_dir: Path, server: str) -> dict:
    from collections import Counter
    c: Counter = Counter()
    p = trial_dir / f"proxy.{server}.jsonl"
    for line in p.read_text().splitlines() if p.is_file() else []:
        r = json.loads(line)
        m = r.get("message")
        if r["dir"] == "c2s" and r["stage"] == "in" and isinstance(m, dict) and "method" in m:
            c[m["method"]] += 1
    return dict(c)


async def run(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"caching": {}, "mrtr": {}}
    async with HubServer(ProxyHub()) as hub:
        # --- caching: list twice; count tools/list reaching the proxy ---
        for label, ttl in (("ttl_0", 0), ("ttl_60000", 60000)):
            UPSTREAMS["probe_cache"] = build_cache_server(ttl)
            td = out_dir / f"cache_{label}"
            spec = TrialSpec(run_id="d17", trial_id=f"cache_{label}", task_id="probe", instruction="",
                             era=STATELESS, transport="streamable-http", scaffold="mcp-client",
                             model="none", mode="record", trial_dir=str(td), servers=["probe_cache"])
            spec.write(); hub.hub.register(spec)
            async with mcp.Client(hub.url(spec.trial_id, "probe_cache"), read_timeout_seconds=20) as c:
                await c.list_tools(); await c.list_tools()
            await hub.hub.close_trial(spec.trial_id)
            m = count_methods(td, "probe_cache")
            report["caching"][label] = {"ttl_ms": ttl, "proxy_methods": m,
                                        "second_list_reached_server": m.get("tools/list", 0) >= 2}
        # --- MRTR: elicitation round-trip, with and without a client callback ---
        UPSTREAMS["probe_mrtr"] = build_mrtr_server
        seen: dict = {}

        async def on_elicit(context, params):
            seen["message"] = getattr(params, "message", None)
            from mcp.shared.context import RequestContext  # noqa: F401
            return mcp.types.ElicitResult(action="accept", content={"approved": True})

        for label, cb in (("with_callback", on_elicit), ("no_callback", None)):
            td = out_dir / f"mrtr_{label}"
            spec = TrialSpec(run_id="d17", trial_id=f"mrtr_{label}", task_id="probe", instruction="",
                             era=STATELESS, transport="streamable-http", scaffold="mcp-client",
                             model="none", mode="record", trial_dir=str(td), servers=["probe_mrtr"])
            spec.write(); hub.hub.register(spec)
            entry: dict = {"client_advertises_elicitation": cb is not None}
            try:
                async with mcp.Client(hub.url(spec.trial_id, "probe_mrtr"), elicitation_callback=cb,
                                      read_timeout_seconds=20) as c:
                    r = await asyncio.wait_for(c.call_tool("needs_confirm", {"action": "delete"}), 20)
                    entry["tool_result"] = str(getattr(r, "content", r))[:160]
                    entry["callback_saw_message"] = seen.get("message")
                    entry["error"] = None
            except BaseException as exc:  # noqa: BLE001 - unwrap anyio ExceptionGroup to the root cause
                root = exc
                while isinstance(root, BaseExceptionGroup) and root.exceptions:
                    root = root.exceptions[0]
                entry["error"] = f"{type(root).__name__}: {root}"
            finally:
                await hub.hub.close_trial(spec.trial_id)
            entry["proxy_methods"] = count_methods(td, "probe_mrtr")
            entry["note"] = ("native server-initiated elicitation needs a back-channel the stateless "
                             "single-POST transport lacks; the harness injects input_required via an "
                             "attack interceptor instead (class 8, attack engine)")
            report["mrtr"][label] = entry
    (out_dir / "d17_probe.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    rep = asyncio.run(run(repo_root() / "review" / "M2"))
    print(json.dumps(rep, indent=2))
