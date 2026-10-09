"""M4-T2 LIVE: utility-only dev mitigation evaluation (D78). Runs the SAME v2 control task set
(36 benign tasks x {sonnet 5.5, opus 5.5} x control_n) as NO-ATTACK control trials, once PER ARM,
with that arm's mitigations selected (recorded in trial.json). m2-dev-v2 is the UNMITIGATED
baseline and is NOT re-run. Authorized: D33/D70 M4-dev autonomy + orchestrator GO, $48 task cap.

    uv run python -I scratchpad/m4_dev_util.py pilot                       # pilot2: 36 trials
    uv run python -I scratchpad/m4_dev_util.py full <prior_spent_usd>      # full: 2160 trials, w/ aborts

IN-RUN ABORTS (full mode): before each trial, stop if cumulative M4-T2 spend + remaining_trials x
observed mean $/trial for THIS trial's model > $48; stop an arm if its harness-error rate exceeds
10% after >=20 trials. Never retry/skip/adjust -- raise AbortRun and stop.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcpredteam.config import anthropic_api_key, repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import STATELESS
from mcpredteam.runner.evaluate import SERVERS, _benign_pass, model_settings
from mcpredteam.runner.tasks import load_tasks
from mcpredteam.runner.trial import run_trial
from mcpredteam.scaffolds.contract import TrialSpec

MODELS = ["claude-sonnet-5-5", "claude-opus-5-5"]
TAG = {"claude-sonnet-5-5": "sonnet", "claude-opus-5-5": "opus"}
V2_RATE = {"claude-sonnet-5-5": 0.01233, "claude-opus-5-5": 0.02475}  # fallback mean until observed
CAP = 48.0
ARMS = {
    "pinning": [{"kind": "pinning"}],
    "spotlight-delimiters": [{"kind": "spotlight", "strip": False}],
    "spotlight-strip": [{"kind": "spotlight", "strip": True}],
    "taint-coarse": [{"kind": "taint-coarse"}],
    "taint-fine": [{"kind": "taint-fine"}],
    "combined": [{"kind": "pinning"}, {"kind": "spotlight", "strip": True}, {"kind": "taint-fine"}],
}
PILOT_TASKS = ["files-todo-to-mail", "notes-sync-then-webhook", "notes-meeting-time"]
ROOT = repo_root()


class AbortRun(Exception):
    pass


def control_task_ids() -> list[str]:
    recs = [json.loads(x) for x in (ROOT / "runs" / "m2-dev-v2" / "records.jsonl").read_text().splitlines() if x]
    return sorted({r["task_id"] for r in recs if r["arm"] == "control"})


async def main(mode: str, prior_spent: float) -> None:
    if mode == "pilot":
        task_ids, control_n, out_root, aborts = PILOT_TASKS, 1, ROOT / "runs" / "m4-dev-util-pilot3", False
    elif mode == "full":
        task_ids, control_n, out_root, aborts = control_task_ids(), 5, ROOT / "runs" / "m4-dev-util", True
    else:
        raise SystemExit("mode must be 'pilot' or 'full'")
    tasks = {t.id: t for t in load_tasks()}
    api_key = anthropic_api_key()
    total_planned = len(ARMS) * len(task_ids) * len(MODELS) * control_n
    cumulative = prior_spent
    done = 0
    mcost = {m: [0.0, 0] for m in MODELS}  # [sum_cost, n]
    summaries = []
    reason = None
    async with HubServer(ProxyHub()) as hub:
        for arm, mit in ARMS.items():
            run_id = f"{out_root.name}-{arm}"
            run_dir = out_root.parent / run_id
            assert not run_dir.exists(), f"{run_dir} exists; refusing to overwrite"
            records = []
            arm_n = arm_err = 0
            for tid in task_ids:
                task = tasks[tid]
                for model in MODELS:
                    for k in range(control_n):
                        if aborts:
                            # Mixed-model projection (orchestrator R2 iii): price each model's OWN
                            # remaining trials at its own observed mean, not all remaining at the
                            # current model's rate (which spuriously aborted ~trial 6).
                            planned_per = total_planned // len(MODELS)
                            proj = cumulative
                            for mm in MODELS:
                                rem = max(0, planned_per - mcost[mm][1])
                                rate = (mcost[mm][0] / mcost[mm][1]) if mcost[mm][1] else V2_RATE[mm]
                                proj += rem * rate
                            if proj > CAP:
                                reason = (f"projected spend ${proj:.2f} > ${CAP} "
                                          f"(cumulative ${cumulative:.4f}; mixed-model remaining)")
                                raise AbortRun(reason)
                        trial_id = f"mit__{arm}__{tid}__{TAG[model]}__{k}"
                        spec = TrialSpec(
                            run_id=run_id, trial_id=trial_id, task_id=tid,
                            instruction=task.prompts[0], prompts=list(task.prompts), era=STATELESS,
                            transport="streamable-http", scaffold="openai-agents", model=model, mode="record",
                            trial_dir=str(run_dir / trial_id), servers=list(SERVERS), attack_config=None,
                            model_settings=model_settings(model), mitigations=list(mit))
                        result = await run_trial(spec, task, hub=hub, api_key=api_key)
                        cost = (result.get("usage") or {}).get("cost_usd", 0.0)
                        blocks = [b for p in (result.get("proxy") or []) for b in (p.get("mitigation_blocks") or [])]
                        err = result.get("error")
                        records.append({
                            "trial_id": trial_id, "arm": arm, "task_id": tid, "model": model, "error": err,
                            "benign_pass": _benign_pass(Path(spec.trial_dir), task, result),
                            "n_blocks": len(blocks), "blocks": blocks,
                            "n_tool_calls": len(result.get("tool_calls") or []),
                            "cost_usd": cost, "final_answer": (result.get("final_answer") or "")[:400]})
                        cumulative += cost
                        done += 1
                        mcost[model][0] += cost
                        mcost[model][1] += 1
                        arm_n += 1
                        arm_err += 1 if err else 0
                        if aborts and arm_n >= 20 and arm_err / arm_n > 0.10:
                            reason = f"arm {arm} harness-error rate {arm_err}/{arm_n} > 10%"
                            run_dir.mkdir(parents=True, exist_ok=True)
                            (run_dir / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
                            raise AbortRun(reason)
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
            summaries.append({"arm": arm, "trials": len(records),
                              "benign_pass": sum(r["benign_pass"] for r in records),
                              "blocks": sum(r["n_blocks"] for r in records),
                              "errors": sum(1 for r in records if r["error"]),
                              "cost": round(sum(r["cost_usd"] for r in records), 6)})
            s = summaries[-1]
            print(f"  {arm:22s} trials={s['trials']} pass={s['benign_pass']} blocks={s['blocks']} "
                  f"errors={s['errors']} cost=${s['cost']:.4f}", flush=True)
    print(f"{mode} DONE: arms={len(summaries)} run_cost=${round(sum(s['cost'] for s in summaries),6)} "
          f"cumulative=${cumulative:.4f} errors={sum(s['errors'] for s in summaries)} "
          f"blocks={sum(s['blocks'] for s in summaries)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "pilot"
    prior = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    try:
        asyncio.run(main(mode, prior))
    except AbortRun as a:
        print(f"ABORT ({mode}): {a}", flush=True)
        raise SystemExit(3) from a
