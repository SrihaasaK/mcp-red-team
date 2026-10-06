"""Transport/scaffold-independent trajectory: the oracle's only input (SPEC §7.2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolCall:
    server: str
    name: str
    arguments: dict

    @property
    def qualified(self) -> str:
        return f"{self.server}.{self.name}"


@dataclass(frozen=True)
class Turn:
    final_answer: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class Trajectory:
    """Ordered turns (turn 1 is ``turns[0]``) plus the raw model-request bodies.

    ``delivered`` is the proxy's own "payload delivered to client" flag when known;
    it is reported next to exposure but never counts as exposure (SPEC §8.2).
    """

    turns: tuple[Turn, ...]
    model_requests: tuple[dict, ...] = ()
    delivered: bool | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Trajectory:
        turns = tuple(
            Turn(
                final_answer=t.get("final_answer") or "",
                tool_calls=tuple(
                    ToolCall(c["server"], c["name"], c.get("arguments") or {})
                    for c in t.get("tool_calls", [])
                ),
            )
            for t in d["turns"]
        )
        return cls(turns, tuple(d.get("model_requests", ())), d.get("delivered"))

    @classmethod
    def from_json(cls, path: Path | str) -> Trajectory:
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_trial(cls, trial_dir: Path | str) -> Trajectory:
        """Build from a real trial directory.

        Reads proxy.<server>.jsonl (c2s/in ``tools/call``), adapter_result.json and
        llm.jsonl (``request`` bodies). Per-turn grouping needs adapter_result.json
        to carry ``turns: [{final_answer, tool_call_count | end_ts}, ...]`` (calls are
        ordered by proxy timestamp); without it the whole trial is ONE turn. The
        current openai-agents adapter is single-turn and emits no ``turns`` key.
        """
        d = Path(trial_dir)
        calls: list[tuple[str, ToolCall]] = []
        for p in sorted(d.glob("proxy.*.jsonl")):
            server = p.name[len("proxy.") : -len(".jsonl")]
            for line in p.read_text().splitlines():
                rec = json.loads(line)
                msg = rec.get("message")
                if (
                    rec.get("dir") == "c2s"
                    and rec.get("stage") == "in"
                    and isinstance(msg, dict)
                    and msg.get("method") == "tools/call"
                ):
                    params = msg.get("params") or {}
                    calls.append(
                        (rec.get("ts", ""), ToolCall(server, params.get("name"), params.get("arguments") or {}))
                    )
        calls.sort(key=lambda c: c[0])
        res_path = d / "adapter_result.json"
        res = json.loads(res_path.read_text()) if res_path.is_file() else {}
        spec = res.get("turns")
        if spec:
            turns, i = [], 0
            for t in spec:
                if "tool_call_count" in t:
                    chunk = calls[i : i + t["tool_call_count"]]
                elif "end_ts" in t:
                    chunk = [c for c in calls[i:] if c[0] <= t["end_ts"]]
                else:
                    raise ValueError("adapter_result turn needs tool_call_count or end_ts")
                i += len(chunk)
                turns.append(Turn(t.get("final_answer") or "", tuple(c[1] for c in chunk)))
            if i < len(calls):  # stragglers belong to the last turn
                last = turns[-1]
                turns[-1] = Turn(last.final_answer, last.tool_calls + tuple(c[1] for c in calls[i:]))
        else:
            turns = [Turn(res.get("final_answer") or "", tuple(c[1] for c in calls))]
        reqs = []
        llm = d / "llm.jsonl"
        for line in llm.read_text().splitlines() if llm.is_file() else []:
            req = json.loads(line).get("request")
            if isinstance(req, dict):
                reqs.append(req)
        delivered = None
        for s in d.glob("proxy.*.summary.json"):
            flag = json.loads(s.read_text()).get("payload_delivered")
            if flag is not None:
                delivered = bool(flag) or bool(delivered)
        return cls(tuple(turns), tuple(reqs), delivered)

