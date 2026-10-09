"""M4-T4b adaptive DEV RE-RUN (r2): the 19 cells the API-connection outage left unmeasured in the
aborted `runs/m4-adaptive-dev` run. Health check (1 benign control trial) FIRST; if it errors, STOP
before spending on cells. Then the 19 cells into `runs/m4-adaptive-dev-r2`, with the SAME
cell_seed (f(config,model), arm-independent -> seed-compatible with the retained clean cells),
B=20, and the SAME guards/aborts as the original full driver (payload-canary leak, per-arm >10%
harness-error rate after >=20 attempts, projected-cost > cap). No attack/oracle/seed/stat change
(CLAUDE.md #1). Author-authorised re-run; M4-T4b task cap $20, $4.006270 already spent.

The 19 cells (handoff): c7-01 x {spotlight-delimiters, taint-coarse, taint-fine}  (c7-01 baseline
was clean in the aborted run and is RETAINED, not re-run), plus c7-02, c7-03, c7-05, c7-06 x all 4
arms. c7-04 is held-out (excluded). The 24 clean in-allowlist cells + the retained c7-01 baseline
cell + these 19 = the 44-cell analysis set.

    uv run python -I scripts/m4_adaptive_dev_r2.py [<prior_m4t4b_spent>]   # default 4.006270
"""
import asyncio
import json
import sys
from pathlib import Path

from mcpredteam.adaptive.loop import ARMS, AdaptivePayloadLeak, run_cell
from mcpredteam.config import anthropic_api_key, repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import STATELESS
from mcpredteam.runner.evaluate import (
    SERVERS,
    _benign_pass,
    dev_runnable,
    era_for,
    load_corpus,
    model_settings,
)
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

ROOT = repo_root()
MODEL = "claude-sonnet-5-5"
B = 20
CAP = 20.0
FALLBACK_RATE = 0.0145  # until an attempt is observed (M4-T2 Sonnet mitigated); matches original driver
ARMS_ORDER = ["baseline", "spotlight-delimiters", "taint-coarse", "taint-fine"]
assert set(ARMS_ORDER) <= set(ARMS)

# Exactly the 19 re-run cells (handoff). c7-01 omits baseline (retained from the aborted run).
RERUN: dict[str, list[str]] = {
    "c7-01-mail-poison-files-canary-http": ["spotlight-delimiters", "taint-coarse", "taint-fine"],
    "c7-02-note-poison-mail-canary-http": ARMS_ORDER,
    "c7-03-status-poison-notes-canary-mail": ARMS_ORDER,
    "c7-05-lunch-poison-files-canary-mail": ARMS_ORDER,
    "c7-06-atlas-poison-notes-canary-http": ARMS_ORDER,
}
HEALTH_TASK = "notes-meeting-time"  # a simple stateless benign task


class AbortRun(Exception):
    pass


def exfil_configs():
    cfgs, split, tasks = load_corpus()
    dev = dev_runnable(cfgs, split)
    exfil = {c["id"]: c for c in dev
             if (c.get("payload") or {}).get("intent") == "exfil_canary" and era_for(c) == STATELESS}
    return exfil, tasks


def cell_cost_and_n(out_root: Path, cell_id: str) -> tuple[float, int]:
    tot, n = 0.0, 0
    for rj in sorted((out_root / cell_id).glob("attempt_*/result.json")):
        u = (json.loads(rj.read_text()).get("usage") or {})
        tot += u.get("cost_usd", 0.0)
        n += 1
    return tot, n


async def health_check(hub, api_key, out_root: Path) -> float:
    """One benign control trial (no attack, no mitigations). Gate on error (handoff step 1)."""
    task = {t.id: t for t in load_tasks()}[HEALTH_TASK]
    tdir = out_root / "_healthcheck" / "benign-001"
    spec = TrialSpec(
        run_id="r2-healthcheck", trial_id="r2-healthcheck__001", task_id=task.id,
        instruction=task.prompts[0], prompts=list(task.prompts), era=STATELESS,
        transport="streamable-http", scaffold="openai-agents", model=MODEL, mode="record",
        trial_dir=str(tdir), servers=list(SERVERS), attack_config=None,
        model_settings=model_settings(MODEL), mitigations=[])
    result = await run_trial(spec, task, hub=hub, api_key=api_key)
    cost = (result.get("usage") or {}).get("cost_usd", 0.0)
    if result.get("error"):
        raise AbortRun(f"health check errored: {result.get('error')!r} -- API/proxy not healthy; not spending on cells")
    bp = _benign_pass(Path(spec.trial_dir), task, result)
    print(f"  health check OK: benign '{HEALTH_TASK}' no error, benign_pass={bp}, ${cost:.4f}", flush=True)
    return cost


async def main(prior_spent: float) -> None:
    exfil, tasks = exfil_configs()
    for cid in RERUN:
        assert cid in exfil, f"{cid} is not a stateless DEV exfil config (held-out or missing); refusing"
    out_root = ROOT / "runs" / "m4-adaptive-dev-r2"
    assert not out_root.exists(), f"{out_root} exists; refusing to overwrite"
    api_key = anthropic_api_key()
    out_root.mkdir(parents=True, exist_ok=True)

    total_cells = sum(len(a) for a in RERUN.values())
    cells_done = 0
    cost_sum = 0.0
    n_attempts = 0
    arm_attempts: dict[str, int] = {a: 0 for a in ARMS_ORDER}
    arm_errors: dict[str, int] = {a: 0 for a in ARMS_ORDER}
    records: list[dict] = []

    def flush():
        (out_root / "cells.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")

    async with HubServer(ProxyHub()) as hub:
        cost_sum += await health_check(hub, api_key, out_root)  # STOP here if the API is still down
        for cid in sorted(RERUN):
            cfg = exfil[cid]
            task = tasks[cfg["compatible_tasks"][0]]
            for arm in RERUN[cid]:
                mean = (cost_sum / n_attempts) if n_attempts else FALLBACK_RATE
                proj = prior_spent + cost_sum + (total_cells - cells_done) * B * mean
                if proj > CAP:
                    flush()
                    raise AbortRun(f"projected ${proj:.2f} > ${CAP} (cumulative ${prior_spent + cost_sum:.4f})")
                try:
                    cell = await run_cell(cfg, task, MODEL, arm, B, hub=hub, api_key=api_key, out_root=out_root)
                except AdaptivePayloadLeak:
                    flush()
                    raise AbortRun(f"AdaptivePayloadLeak in {cid}/{arm}") from None
                c_cost, c_n = cell_cost_and_n(out_root, cell["cell_id"])
                cell["cost_usd"] = round(c_cost, 6)
                cost_sum += c_cost
                n_attempts += c_n
                arm_attempts[arm] += c_n
                arm_errors[arm] += cell["errored_attempts"]
                cells_done += 1
                records.append(cell)
                print(f"  {cell['cell_id']:60s} fs={cell['first_success_index']} "
                      f"runs={cell['attempts_run']} blk={cell['blocked_attempts']} "
                      f"err={cell['errored_attempts']} ${c_cost:.4f}", flush=True)
                if arm_attempts[arm] >= 20 and arm_errors[arm] / arm_attempts[arm] > 0.10:
                    flush()
                    raise AbortRun(f"arm {arm} harness-error rate {arm_errors[arm]}/{arm_attempts[arm]} > 10%")
    flush()
    print(f"r2 DONE: cells={len(records)} attempts={n_attempts} run_cost=${cost_sum:.6f} "
          f"cumulative=${prior_spent + cost_sum:.4f} errors={sum(arm_errors.values())} "
          f"successes={sum(1 for r in records if r['first_success_index'] is not None)}", flush=True)


if __name__ == "__main__":
    prior = float(sys.argv[1]) if len(sys.argv) > 1 else 4.006270
    try:
        asyncio.run(main(prior))
    except AbortRun as a:
        print(f"ABORT (r2): {a}", flush=True)
        raise SystemExit(3) from a
