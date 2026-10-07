"""Run a task × era × transport matrix live (record), and replay a recorded run.

Replay equality (SPEC §9.1): identical tool-call sequence (server, tool, canonical
arguments), identical verdict, and zero live calls. That means no model call left
the adapter, the socket guard blocked nothing, and no proxy reached an upstream.
Request ids, headers and timestamps may differ. Final-answer equality is
reported but not required.
"""

from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path

from mcpredteam.config import anthropic_api_key, repo_root
from mcpredteam.oracle.rules import score
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.recording import canonical
from mcpredteam.proxy.relay import HANDSHAKE, STATELESS
from mcpredteam.runner.tasks import Task, load_tasks
from mcpredteam.runner.trial import PRICES, run_trial
from mcpredteam.scaffolds.contract import TrialSpec


@lru_cache(maxsize=1)
def _task_map() -> dict:
    return {t.id: t for t in load_tasks()}

SERVERS = ["notes", "http"]
ERA_TAG = {HANDSHAKE: "hs", STATELESS: "sl"}
TRANSPORT_TAG = {"streamable-http": "http", "stdio": "stdio"}


def trial_id(task: str, era: str, transport: str) -> str:
    return f"{task}__{ERA_TAG[era]}__{TRANSPORT_TAG[transport]}"


def _line(r: dict) -> str:
    u = r.get("usage") or {}
    ok = "PASS" if r["verdict"]["passed"] else "FAIL"
    cost = f"${u['cost_usd']:.4f}" if u else "-"
    return (f"  {r['trial_id']:<38} {ok}  era_ok={r['era_matches']!s:<5} calls={len(r['tool_calls'])} "
            f"wall={r['wall_clock_s']:>6.1f}s cost={cost}" + (f"  ERROR {r['error'][:80]}" if r["error"] else ""))


async def run_matrix(*, run_id: str, tasks: list[Task], eras: list[str], transports: list[str],
                     scaffold: str, model: str, model_settings: dict, budget_usd: float,
                     out_root: Path | None = None) -> dict:
    if model not in PRICES:  # refuse before any spend
        raise ValueError(f"model {model!r} is not in the frozen manifest {sorted(PRICES)}")
    run_dir = (out_root or repo_root() / "runs") / run_id
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists: a run never reuses a directory")
    api_key = anthropic_api_key()
    results, spent, skipped, halted = [], 0.0, [], None
    async with HubServer(ProxyHub()) as hub:
        for transport in transports:
            for era in eras:
                for task in tasks:
                    tid = trial_id(task.id, era, transport)
                    if spent >= budget_usd or halted:
                        skipped.append(tid)
                        continue
                    spec = TrialSpec(run_id=run_id, trial_id=tid, task_id=task.id, instruction=task.instruction,
                                     era=era, transport=transport, scaffold=scaffold, model=model, mode="record",
                                     trial_dir=str(run_dir / tid), servers=SERVERS, model_settings=model_settings)
                    r = await run_trial(spec, task, hub=hub, api_key=api_key)
                    spent += (r["usage"] or {}).get("cost_usd", 0.0)
                    results.append(r)
                    if (r["usage"] or {}).get("usage_missing"):  # spend unknown: fail closed
                        halted = f"{tid}: model reported no usage, so spend is unknown"
                    print(_line(r), flush=True)
    summary = {
        "run_id": run_id,
        "finished_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "scaffold": scaffold,
        "model": model,
        "model_settings": model_settings,
        "budget_usd": budget_usd,
        "spent_usd": round(spent, 6),
        "trials": len(results),
        "skipped": skipped,
        "halted": halted,
        "passed": sum(r["verdict"]["passed"] for r in results),
        "errors": sum(r["error"] is not None for r in results),
        "era_mismatches": [r["trial_id"] for r in results if not r["era_matches"]],
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def compare(rec_dir: Path, rep_dir: Path) -> dict:
    rec = json.loads((rec_dir / "result.json").read_text())
    rep = json.loads((rep_dir / "result.json").read_text())

    def seq(r: dict) -> list[list[str]]:
        # From result.json's tool_calls (every server the trial used, in arrival order) rather than
        # a re-scan of a fixed server list, so files/mail calls are compared too, not silently dropped.
        return [[c["server"], c["name"], canonical(c["arguments"])] for c in r["tool_calls"]]

    adapter = rep["adapter"]
    zero_live = (adapter.get("live_llm_calls") == 0 and adapter.get("blocked_network_calls") == 0
                 and all(p["upstream_live_calls"] == 0 for p in rep["proxy"]))
    out = {
        "trial_id": rec["trial_id"],
        "tool_sequence_equal": seq(rec) == seq(rep),
        "verdict_equal": rec["verdict"] == rep["verdict"],
        "zero_live_calls": zero_live,
        "llm_fully_replayed": adapter.get("llm_replay_unused") == 0,
        "era_matches": rec["era_matches"] and rep["era_matches"],
        "recorded_error": rec["error"],
        "replay_error": rep["error"],
        "final_answer_equal": rec["final_answer"] == rep["final_answer"],
        "tool_calls": len(rep["tool_calls"]),
    }
    # SPEC §9.1: an attack-arm trial replays equally only if the ATTACK oracle verdict
    # (success + rule) also matches, recomputed from each side's trajectory with the same
    # config. This CALLS the oracle (no oracle change). Benign/control trials carry no
    # attack_config, so attack_verdict_equal is vacuously True.
    spec = json.loads((rec_dir / "trial.json").read_text())
    attack_cfg = spec.get("attack_config")
    out["attack_verdict_equal"] = True
    if attack_cfg:
        task = _task_map()[spec["task_id"]]
        rv = score(Trajectory.from_trial(rec_dir), attack_cfg, task)
        pv = score(Trajectory.from_trial(rep_dir), attack_cfg, task)
        out["attack_verdict_equal"] = (rv.success == pv.success and rv.rule == pv.rule)
        out["attack_verdict"] = {"recorded": [rv.success, rv.rule], "replayed": [pv.success, pv.rule]}
    # A recorded error replayed faithfully counts as equal; a new one never does.
    out["equal"] = (out["tool_sequence_equal"] and out["verdict_equal"] and out["zero_live_calls"]
                    and out["llm_fully_replayed"] and out["era_matches"]
                    and out["attack_verdict_equal"]
                    and out["replay_error"] == out["recorded_error"])
    return out


async def replay_run(run_dir: Path, out_dir: Path | None = None) -> dict:
    run_dir = Path(run_dir).resolve()
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(out_dir or repo_root() / "runs" / f"{run_dir.name}-replay-{stamp}").resolve()
    if out_dir == run_dir or run_dir in out_dir.parents:
        raise ValueError("replay output must not be inside the recorded run")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty: replay never reuses a directory")
    tasks = {t.id: t for t in load_tasks()}
    trial_dirs = sorted(p.parent for p in run_dir.glob("*/trial.json"))
    if not trial_dirs:
        raise FileNotFoundError(f"no recorded trials under {run_dir}")
    recorded = [d for d in trial_dirs if (d / "result.json").is_file()]
    comparisons = [{"trial_id": d.name, "equal": False, "replay_error": "recorded trial is incomplete (no result.json)"}
                   for d in trial_dirs if d not in recorded]
    async with HubServer(ProxyHub()) as hub:
        for rec_dir in recorded:
            old = TrialSpec.read(rec_dir / "trial.json")
            spec = TrialSpec(run_id=f"{old.run_id}-replay", trial_id=old.trial_id, task_id=old.task_id,
                             instruction=old.instruction, era=old.era, transport=old.transport,
                             scaffold=old.scaffold, model=old.model, mode="replay",
                             trial_dir=str(out_dir / old.trial_id), servers=old.servers,
                             recording_dir=str(rec_dir), model_settings=old.model_settings,
                             max_turns=old.max_turns,
                             # Carry the per-turn prompts (else a two-turn trial replays only turn 1)
                             # and the attack config (the proxy rebuilds the same interceptor in replay,
                             # so the mutated request/response match the recording).
                             prompts=old.prompts, attack_config=old.attack_config)
            await run_trial(spec, tasks[old.task_id], hub=hub, api_key=None)
            c = compare(rec_dir, out_dir / old.trial_id)
            comparisons.append(c)
            print(f"  {c['trial_id']:<38} {'EQUAL' if c['equal'] else 'DIFFERS'}  calls={c['tool_calls']} "
                  f"zero_live={c['zero_live_calls']} era_ok={c['era_matches']}"
                  + (f"  ERROR {c['replay_error'][:80]}" if c["replay_error"] else ""), flush=True)
    summary = {
        "recorded_run": str(run_dir),
        "replay_dir": str(out_dir),
        "trials": len(comparisons),
        "equal": sum(c["equal"] for c in comparisons),
        "eras_covered": sorted({json.loads((d / "trial.json").read_text())["era"] for d in recorded}),
        "comparisons": comparisons,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "replay_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
