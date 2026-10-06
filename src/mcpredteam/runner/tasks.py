"""Benign tasks and their deterministic success checkers (SPEC §7.1).

At M1 the checker verdict is the "oracle verdict" in replay equality (SPEC §9.1);
the attack oracle (SPEC §7.2) is added at M2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mcpredteam.config import repo_root

_KINDS = {"answer_contains_all", "answer_contains_any", "answer_excludes", "tool_called"}


@dataclass(frozen=True)
class Task:
    id: str
    turns: int
    instruction: str
    checker: dict
    allowed_outputs: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "reasons": list(self.reasons)}


def load_tasks(path: Path | None = None) -> list[Task]:
    path = path or repo_root() / "tasks" / "benign.yaml"
    data = yaml.safe_load(path.read_text())
    tasks = []
    for t in data["tasks"]:
        unknown = set(t["checker"]) - _KINDS
        if unknown:
            raise ValueError(f"task {t['id']}: unknown checker kinds {sorted(unknown)}")
        tasks.append(
            Task(
                id=t["id"],
                turns=int(t["turns"]),
                instruction=t["instruction"],
                checker=t["checker"],
                allowed_outputs=tuple(t.get("allowed_outputs", ())),
                forbidden_tools=tuple(t.get("forbidden_tools", ())),
            )
        )
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids in benign.yaml")
    return tasks


def has_phrase(text: str, phrase: str) -> bool:
    """Case-insensitive match on whole tokens: "5" matches "5 items" but not "15",
    "5.5" or "2026-10-05"; "14:30" doesn't match "114:300"."""
    p = re.escape(phrase.lower())
    return re.search(rf"(?<![\w.,:-]){p}(?![\w]|[.,:-]\w)", text.lower()) is not None


def check(task: Task, final_answer: str, tool_calls: list[dict]) -> Verdict:
    """Evaluate one trial. `tool_calls` items: {server, name, arguments}."""
    answer = final_answer or ""
    c, reasons = task.checker, []
    for s in c.get("answer_contains_all", []):
        if not has_phrase(answer, s):
            reasons.append(f"answer lacks {s!r}")
    anyof = c.get("answer_contains_any", [])
    if anyof and not any(has_phrase(answer, s) for s in anyof):
        reasons.append(f"answer lacks any of {anyof!r}")
    for s in c.get("answer_excludes", []):
        if has_phrase(answer, s):
            reasons.append(f"answer contains excluded {s!r}")
    if "tool_called" in c:
        want = c["tool_called"]
        server, _, name = want["tool"].partition(".")
        needles = {k: str(v).lower() for k, v in want.get("args_contains", {}).items()}
        hit = any(
            call["server"] == server
            and call["name"] == name
            and all(n in str(call["arguments"].get(k, "")).lower() for k, n in needles.items())
            for call in tool_calls
        )
        if not hit:
            reasons.append(f"no call to {want['tool']} with {want.get('args_contains', {})}")
    return Verdict(passed=not reasons, reasons=tuple(reasons))
