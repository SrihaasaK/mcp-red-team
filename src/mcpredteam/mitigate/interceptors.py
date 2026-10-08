"""The SPEC §10 mitigations as chainable `Interceptor`s (M4-T1; D53/D58).

Mitigations are chained AFTER the attack in the per-(trial, server) relay, so they see the
attacked traffic. Each implements the full `Interceptor` Protocol and passes through every
hook it does not use. When `TrialSpec.mitigations` is empty, `build_mitigations` returns no
interceptors and nothing changes (the regression guarantee).

- Pinning: `on_tools_list` hashes each delivered description and flags tools whose hash differs
  from the clean pin (fail-closed on unknown tools); a flagged tool's CALLS are then blocked.
- Spotlight: `on_tool_result`/`on_resource_read` wrap delivered text in data-not-instruction
  delimiters; the `strip` mode first removes DEV-derived instruction patterns (provenance in
  `patterns.py`). It alters results, never blocks.
- Taint (coarse/fine): records each delivered result's text (by source server) in the shared,
  per-trial `TaintStore`, and blocks a sink call per the pure `taint` policy functions.

Every block raises `BlockedCall`, which the relay turns into an isError result tagged
`mitigation_blocked` (D56): not forwarded upstream, excluded from the executed set by
`Trajectory.from_trial`, and counted in the per-mitigation attempt rate.
"""

from __future__ import annotations

from typing import Any

from mcpredteam.mitigate import pins as pinmod
from mcpredteam.mitigate import taint as taintmod
from mcpredteam.mitigate.allowlist import derive_allowlist
from mcpredteam.mitigate.block import BlockedCall
from mcpredteam.mitigate.patterns import strip_patterns
from mcpredteam.mitigate.store import TaintStore
from mcpredteam.oracle.rules import SIDE_EFFECTING_TOOLS
from mcpredteam.proxy.interfaces import TrialCtx

SPOTLIGHT_OPEN = "<untrusted-data server={server!r}>"
SPOTLIGHT_CLOSE = "</untrusted-data>"


def _value_leaves(obj: Any) -> list[str]:
    """Every string VALUE in a nested args/result structure (dict keys excluded)."""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_value_leaves(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_value_leaves(v))
    return out


def _args_text(args: dict) -> str:
    """Argument value leaves joined with taint.SEP, so a fine-taint k-gram can never straddle
    two distinct argument values (D79)."""
    return taintmod.SEP.join(_value_leaves(args))


def _result_text(result: Any) -> str:
    """Delivered text of a tools/call result or a resources/read contents list, with content
    BLOCKS joined by taint.SEP so a k-gram never straddles two delivered blocks (D79)."""
    if isinstance(result, dict):
        blocks = result.get("content")
        if isinstance(blocks, list):
            return taintmod.SEP.join(b.get("text", "") for b in blocks if isinstance(b, dict))
    return taintmod.SEP.join(_value_leaves(result))


class _PassThrough:
    """Identity for every Interceptor hook; mitigations override what they use."""

    async def on_discover(self, caps: dict, ctx: TrialCtx) -> dict:
        return caps

    async def on_tools_list(self, tools: list[dict], ctx: TrialCtx) -> list[dict]:
        return tools

    async def on_tool_call(self, name: str, args: dict, ctx: TrialCtx) -> tuple[str, dict]:
        return name, args

    async def on_tool_result(self, name: str, result: Any, ctx: TrialCtx) -> Any:
        return result

    async def on_resource_read(self, uri: str, content: Any, ctx: TrialCtx) -> Any:
        return content

    async def on_mrtr(self, req: dict, ctx: TrialCtx) -> dict:
        return req

    async def on_final_answer(self, text: str, ctx: TrialCtx) -> None:
        return None


class PinningMitigation(_PassThrough):
    kind = "pinning"

    def __init__(self, server: str) -> None:
        self.server = server

    async def on_tools_list(self, tools: list[dict], ctx: TrialCtx) -> list[dict]:
        pins = await pinmod.clean_pins()
        blocked = ctx.state.setdefault("pinning_blocked", set())
        for t in tools:
            if pinmod.mismatch(pins, self.server, t.get("name", ""), t.get("description")):
                blocked.add(t.get("name", ""))
        return tools  # the agent still sees the (attacked) list; CALLS to flagged tools are blocked

    async def on_tool_call(self, name: str, args: dict, ctx: TrialCtx) -> tuple[str, dict]:
        if name in ctx.state.get("pinning_blocked", set()):
            raise BlockedCall(self.kind, f"{self.server}.{name} description does not match its clean pin")
        return name, args


class SpotlightMitigation(_PassThrough):
    def __init__(self, server: str, *, strip: bool) -> None:
        self.server = server
        self.strip = strip
        self.kind = "spotlight-strip" if strip else "spotlight-delimiters"

    def _wrap(self, text: str, ctx: TrialCtx) -> str:
        if self.strip:
            for pat in strip_patterns():
                new = pat.sub("", text)
                if new != text:
                    ctx.state["spotlight_stripped"] = ctx.state.get("spotlight_stripped", 0) + 1
                    text = new
        return f"{SPOTLIGHT_OPEN.format(server=self.server)}\n{text}\n{SPOTLIGHT_CLOSE}"

    async def on_tool_result(self, name: str, result: Any, ctx: TrialCtx) -> Any:
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            for b in result["content"]:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    b["text"] = self._wrap(b["text"], ctx)
        return result

    async def on_resource_read(self, uri: str, content: Any, ctx: TrialCtx) -> Any:
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    b["text"] = self._wrap(b["text"], ctx)
        return content


class _TaintBase(_PassThrough):
    def __init__(self, server: str, trial_dir) -> None:
        self.server = server
        self.store = TaintStore(trial_dir)

    async def on_tool_result(self, name: str, result: Any, ctx: TrialCtx) -> Any:
        text = _result_text(result)
        if text:
            self.store.record_delivery(self.server, text)
        return result

    async def on_resource_read(self, uri: str, content: Any, ctx: TrialCtx) -> Any:
        text = _result_text(content)
        if text:
            self.store.record_delivery(self.server, text)
        return content

    def _is_sink(self, name: str) -> bool:
        return f"{self.server}.{name}" in SIDE_EFFECTING_TOOLS


class TaintCoarseMitigation(_TaintBase):
    kind = "taint-coarse"

    async def on_tool_call(self, name: str, args: dict, ctx: TrialCtx) -> tuple[str, dict]:
        if self._is_sink(name):
            src = taintmod.coarse_block_source(self.store.delivered_servers(), self.server, derive_allowlist())
            if src is not None:
                raise BlockedCall(self.kind, f"cross-server flow {src}->{self.server} is not on the allowlist")
        return name, args


class TaintFineMitigation(_TaintBase):
    kind = "taint-fine"

    async def on_tool_call(self, name: str, args: dict, ctx: TrialCtx) -> tuple[str, dict]:
        if self._is_sink(name):
            deliveries = [(d.server, d.text) for d in self.store.deliveries()]
            src = taintmod.fine_block_source(_args_text(args), deliveries, self.server, taintmod.K)
            if src is not None:
                raise BlockedCall(self.kind, f"sink args share a >={taintmod.K}-gram with {src} content")
        return name, args


def build_mitigations(spec, server: str) -> tuple:
    """The mitigation interceptors for one (trial, server), from `spec.mitigations`
    (each `{"kind": ..., ...params}`). Empty list -> empty tuple (nothing changes)."""
    out: list = []
    for m in getattr(spec, "mitigations", None) or []:
        kind = m["kind"]
        if kind == "pinning":
            out.append(PinningMitigation(server))
        elif kind == "spotlight":
            out.append(SpotlightMitigation(server, strip=bool(m.get("strip", False))))
        elif kind == "taint-coarse":
            out.append(TaintCoarseMitigation(server, spec.trial_dir))
        elif kind == "taint-fine":
            out.append(TaintFineMitigation(server, spec.trial_dir))
        else:
            raise ValueError(f"unknown mitigation kind {kind!r}")
    return tuple(out)
