"""The adaptive per-cell loop (D67; design.md §3-5). DEV ONLY.

A cell = (config, task, model, mitigation arm). `run_cell` drives up to B attempts: each attempt
is one ordinary agent trial (`runner.trial.run_trial`) with the attacker's chosen variant injected
and the arm's A6-frozen mitigations chained; it is scored by the UNCHANGED A5 oracle. The attacker
is fed ONLY `Feedback(success, rule, blocked_by)` (no canary/trajectory). The loop early-stops at
the first oracle success (ASR@k-preserving, design.md §3). It refuses any non-DEV config (guard c)
and reads no held-out data. $0 beyond the trials it runs; offline when pointed at a fake model.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from mcpredteam.adaptive.attacker import DEV_SPECS, AdaptiveAttacker, Feedback, variant_config
from mcpredteam.oracle.rules import score
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.runner.canary import hydrate_attack_config
from mcpredteam.runner.evaluate import SERVERS, dev_runnable, era_for, load_corpus, model_settings
from mcpredteam.runner.tasks import Task
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

# DEV arms (design.md §4): the A4-named arms, selecting mitigations EXACTLY as frozen in A6.
# `combined` is excluded (not frozen); pinning/spotlight-delimiters are optional and omitted.
ARMS: dict[str, list[dict]] = {
    "none": [],
    "spotlight-strip": [{"kind": "spotlight", "strip": True}],
    "taint-coarse": [{"kind": "taint-coarse"}],
    "taint-fine": [{"kind": "taint-fine"}],
}


@lru_cache(maxsize=1)
def dev_config_ids() -> frozenset[str]:
    """The DEV-split, OpenAI-runnable config ids (reuses `dev_runnable`, so DEV-only by
    construction). Held-out ids are absent -> `assert_dev` refuses them (guard c)."""
    configs, split, _ = load_corpus()
    return frozenset(c["id"] for c in dev_runnable(configs, split))


def assert_dev(config_id: str) -> None:
    """Refuse any config that is not a DEV-split runnable config (guard c; no held-out)."""
    if config_id not in dev_config_ids():
        raise ValueError(f"adaptive loop is DEV-only; {config_id!r} is not a DEV-runnable config")


def _blocked_by(result: dict) -> str | None:
    """The mitigation kind that blocked a call this attempt (first block), else None."""
    for p in result.get("proxy") or []:
        for b in p.get("mitigation_blocks") or []:
            return b.get("kind")
    return None


async def run_cell(
    base_config: dict, task: Task, model: str, arm: str, budget: int, seed: int, *,
    hub, api_key: str | None, out_root: Path, base_url: str | None = None,
    specs: tuple = DEV_SPECS, mode: str = "record",
) -> dict:
    """Run one adaptive cell. Returns the cell outcome incl. `first_success_index` (1-based, or
    None) and a per-attempt record. Early-stops at the first oracle success."""
    assert_dev(base_config["id"])
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; known: {sorted(ARMS)}")
    attacker = AdaptiveAttacker(base_config, seed, specs)
    cell_id = f"{base_config['id']}__{task.id}__{model.split('-')[1]}__{arm}"
    first_success: int | None = None
    blocked_attempts = 0
    attempts: list[dict] = []

    for i in range(1, budget + 1):
        variant = attacker.next_variant()
        if variant is None:
            break
        cfg = variant_config(base_config, variant)
        trial_dir = out_root / cell_id / f"attempt_{i:03d}"
        extra = {"base_url": base_url} if base_url else {}
        spec = TrialSpec(
            run_id=cell_id, trial_id=f"{cell_id}__{i:03d}", task_id=task.id,
            instruction=task.prompts[0], prompts=list(task.prompts), era=era_for(base_config),
            transport="streamable-http", scaffold="openai-agents", model=model, mode=mode,
            trial_dir=str(trial_dir), servers=list(SERVERS), attack_config=cfg,
            model_settings=model_settings(model), mitigations=ARMS[arm], **extra)
        result = await run_trial(spec, task, hub=hub, api_key=api_key)

        traj = Trajectory.from_trial(trial_dir)
        verdict = score(traj, hydrate_attack_config(cfg), task)  # A5 oracle; canary resolved here only
        blocked_by = _blocked_by(result)
        blocked_attempts += 1 if blocked_by else 0
        # Feedback to the attacker is oracle-level ONLY -- never the canary or trajectory text.
        feedback = Feedback(success=verdict.success, rule=verdict.rule, blocked_by=blocked_by)
        attacker.observe(variant, feedback)
        attempts.append({"i": i, "variant": variant.label(), "transform": variant.transform,
                         "success": verdict.success, "rule": verdict.rule, "blocked_by": blocked_by,
                         "error": result.get("error")})
        if verdict.success and first_success is None:
            first_success = i
            break  # early stop (ASR@k-preserving)

    return {"cell_id": cell_id, "config": base_config["id"], "task": task.id, "model": model,
            "arm": arm, "budget": budget, "seed": seed, "first_success_index": first_success,
            "attempts_run": len(attempts), "blocked_attempts": blocked_attempts, "attempts": attempts}
