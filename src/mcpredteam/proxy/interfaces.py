"""Core design interfaces for the proxy (M0 design artifact — no behavior).

The symmetry rule (SPEC §5.1): attacks and mitigations are BOTH `Interceptor`s,
chained in order over the same message pipeline. There is no second mechanism.

`TrialCtx` is keyed by run/trial ID, NEVER by a protocol session (SPEC §4.2),
so a single proxy serves both the stateful (2025-11-25) and the sessionless
stateless (2026-07-28) era uniformly, and logging stays deterministic.

Concrete interceptors, the proxy, and TrialCtx wiring are implemented from M1+.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# The two protocol eras this harness supports (SPEC §4.2). Verified at M0 to be
# jointly supported by a single pinned `mcp` SDK (2.3.0): see DECISIONS.md.
ProtocolEra = Literal["2025-11-25", "2026-07-28"]


@dataclass
class TrialCtx:
    """Per-trial context threaded through the interceptor chain.

    Identity is (run_id, trial_id) — independent of any MCP protocol session
    (SPEC §4.2). `state` is interceptor scratch space (e.g. taint sets, call
    counts, pins) scoped to a single trial. Fields are the M0 design shape;
    population happens in the runner/proxy from M1+.
    """

    run_id: str
    trial_id: str
    era: ProtocolEra
    scaffold: str              # e.g. "openai-agents" | "langgraph" | "claude-agent-sdk"
    model: str                 # frozen manifest id, e.g. "claude-sonnet-5-5"
    transport: str             # "stdio" | "streamable-http"
    task_id: str | None = None        # benign task (tasks/benign.yaml), set by runner
    attack_config_id: str | None = None  # attack config (attacks/corpus.yaml), or None for control arm
    state: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Interceptor(Protocol):
    """A single stage in the proxy's interceptor chain (SPEC §5.1).

    Every hook may inspect, mutate, or pass through the message. Attacks inject;
    mitigations block/rewrite; both implement this same Protocol.
    """

    async def on_discover(self, caps: dict, ctx: TrialCtx) -> dict: ...
    async def on_tools_list(self, tools: list[dict], ctx: TrialCtx) -> list[dict]: ...
    async def on_tool_call(self, name: str, args: dict, ctx: TrialCtx) -> tuple[str, dict]: ...
    async def on_tool_result(self, name: str, result: Any, ctx: TrialCtx) -> Any: ...
    async def on_resource_read(self, uri: str, content: Any, ctx: TrialCtx) -> Any: ...
    async def on_mrtr(self, req: dict, ctx: TrialCtx) -> dict: ...  # 2026-07-28 only
    async def on_final_answer(self, text: str, ctx: TrialCtx) -> None: ...
