"""M4-T4b adaptive DEV live run (D67/D86 scope; author-authorised D67/D34, orchestrator GO $20 cap).
Drives adaptive.loop.run_cell over the DEV exfil configs x Sonnet x 4 arms x B=20, with early stop.
Cost is read from each attempt's result.json (no loop.py change). In-run aborts (full mode).

    uv run python -I scratchpad/m4_adaptive_dev.py pilot                  # c7-01 only
    uv run python -I scratchpad/m4_adaptive_dev.py full <prior_spent>     # 11 configs, w/ aborts
"""
import asyncio
import json
import sys
from pathlib import Path

from mcpredteam.adaptive.loop import ARMS, AdaptivePayloadLeak, run_cell
from mcpredteam.config import anthropic_api_key, repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import STATELESS
from mcpredteam.runner.evaluate import dev_runnable, era_for, load_corpus

ROOT = repo_root()
MODEL = "claude-sonnet-5-5"
ARMS_ORDER = ["baseline", "spotlight-delimiters", "taint-coarse", "taint-fine"]
assert set(ARMS_ORDER) <= set(ARMS)
B = 20
CAP = 20.0
FALLBACK_RATE = 0.0145  # until an attempt is observed (M4-T2 Sonnet mitigated)


class AbortRun(Exception):
    pass


def exfil_configs():
    cfgs, split, tasks = load_corpus()
    dev = dev_runnable(cfgs, split)
    exfil = [c for c in dev if (c.get("payload") or {}).get("intent") == "exfil_canary"
             and era_for(c) == STATELESS]
    return sorted(exfil, key=lambda c: c["id"]), tasks


def cell_cost_and_n(out_root: Path, cell_id: str) -> tuple[float, int]:
    tot, n = 0.0, 0
    for rj in sorted((out_root / cell_id).glob("attempt_*/result.json")):
        u = (json.loads(rj.read_text()).get("usage") or {})
        tot += u.get("cost_usd", 0.0)
        n += 1
    return tot, n


async def main(mode: str, prior_spent: float) -> None:
    configs_all, tasks = exfil_configs()
    if mode == "pilot":
        configs = [c for c in configs_all if c["id"] == "c7-01-mail-poison-files-canary-http"]
        out_root, aborts = ROOT / "runs" / "m4-adaptive-dev-pilot", False
    elif mode == "full":
        configs, out_root, aborts = configs_all, ROOT / "runs" / "m4-adaptive-dev", True
    else:
        raise SystemExit("mode must be 'pilot' or 'full'")
    assert configs, "no configs"
    assert not out_root.exists(), f"{out_root} exists; refusing to overwrite"
    api_key = anthropic_api_key()

    total_cells = len(configs) * len(ARMS_ORDER)
    cells_done = 0
    cost_sum = 0.0
    n_attempts = 0
    arm_attempts: dict[str, int] = {a: 0 for a in ARMS_ORDER}
    arm_errors: dict[str, int] = {a: 0 for a in ARMS_ORDER}
    records = []
    out_root.mkdir(parents=True, exist_ok=True)

    async with HubServer(ProxyHub()) as hub:
        for cfg in configs:
            task = tasks[cfg["compatible_tasks"][0]]
            for arm in ARMS_ORDER:
                if aborts:
                    mean = (cost_sum / n_attempts) if n_attempts else FALLBACK_RATE
                    remaining_worst = (total_cells - cells_done) * B
                    proj = prior_spent + cost_sum + remaining_worst * mean
                    if proj > CAP:
                        raise AbortRun(f"projected ${proj:.2f} > ${CAP} (cumulative ${prior_spent + cost_sum:.4f})")
                try:
                    cell = await run_cell(cfg, task, MODEL, arm, B, hub=hub, api_key=api_key, out_root=out_root)
                except AdaptivePayloadLeak:
                    raise AbortRun(f"AdaptivePayloadLeak in {cfg['id']}/{arm}") from None
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
                if aborts and arm_attempts[arm] >= 20 and arm_errors[arm] / arm_attempts[arm] > 0.10:
                    (out_root / "cells.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
                    raise AbortRun(f"arm {arm} error rate {arm_errors[arm]}/{arm_attempts[arm]} > 10%")
    (out_root / "cells.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    print(f"{mode} DONE: cells={len(records)} attempts={n_attempts} "
          f"run_cost=${cost_sum:.6f} cumulative=${prior_spent + cost_sum:.4f} "
          f"errors={sum(arm_errors.values())} "
          f"successes={sum(1 for r in records if r['first_success_index'] is not None)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    prior = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    try:
        asyncio.run(main(mode, prior))
    except AbortRun as a:
        print(f"ABORT ({mode}): {a}", flush=True)
        raise SystemExit(3) from a
