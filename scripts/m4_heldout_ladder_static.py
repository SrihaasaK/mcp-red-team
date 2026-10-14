"""M4 LADDER STATIC slice driver (PREREG Amendment A8.10, FROZEN 2026-10-09).

PURPOSE (A8.10): run the SAME static held-out attacks as the frontier headline on the
LADDER rungs, so the capability comparison across rungs is NOT confounded with attack
type. This is a capability/comparison slice; it is **NOT pooled into the frontier static
headline** (which is Sonnet+Opus, both scaffolds, 7 arms -- scripts/m4_heldout_static.py).

Adapted from scripts/m4_heldout_static.py. It runs, over the FROZEN held_out split:

    held-out STATIC configs (project's own runnable filter; OpenAI conditional-class map)
      x models   {claude-haiku-5-5 (Anthropic API), qwen3:8b (LOCAL Ollama, $0)}   (A8.1/A8.10)
      x scaffold  openai-agents ONLY                                               (A8.10)
      x arms     {baseline, combined (pin+strip+coarse, D86/A7 §2), taint-fine}     (A8.10, 3 arms)
      n = 10 per cell (A8.3), mode="record", scored by the UNCHANGED oracle.

Reuses the project's OWN attack/scoring path (evaluate.run_attack -> run_trial ->
oracle.score with D61 canary hydration). It imports -- never redefines -- the oracle,
corpus, split, and mitigation specs.

    uv run python -I scripts/m4_heldout_ladder_static.py enumerate               # $0 OFFLINE
    uv run python -I scripts/m4_heldout_ladder_static.py full <prior_spent_usd>  # LIVE (author-gated)

qwen3:8b runs on the LOCAL Ollama OpenAI-compatible endpoint (base_url
http://localhost:11434/v1/, dummy api_key "ollama", $0 API -- wall-clock recorded). Both
ladder models are OUTSIDE the frozen PRICES manifest, so -- EXACTLY as scripts/
m4_ladder_qual.py does -- their unit prices are injected into runner.trial.PRICES AT
RUNTIME (no source file edited) so llm_usage does not raise in record mode. Haiku 5.5 =
public Anthropic rate ($0.10/$0.50 per MTok); qwen3 local => $0.

GUARDS (kept from the frontier driver): spend-guard mixed-model projection + ABORT if it
would exceed the $225 autonomous cap; per-(scaffold,arm) harness-error ABORT (>10% after
>=20); and the per-scaffold BASELINE exposure-floor apparatus ABORT (D31/check_aborts).

Protected paths: creates ONLY scripts/m4_heldout_ladder_static.py; writes under
runs/m4-heldout-ladder-static/. The runtime PRICES injection mutates the in-memory dict,
never the file. No protected-path change; no DEV-only guard on the static path.
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcpredteam.config import anthropic_api_key, repo_root
from mcpredteam.proxy.http_app import HubServer, ProxyHub
from mcpredteam.proxy.relay import HANDSHAKE
from mcpredteam.runner import trial as trial_mod
from mcpredteam.runner.evaluate import (
    RUNNABLE_CLASSES,
    SERVERS,
    TrialRecord,
    aggregate,
    check_aborts,
    era_for,
    load_corpus,
    model_settings,
    run_attack,
)
from mcpredteam.scaffolds.contract import TrialSpec

ROOT = repo_root()
OUT_ROOT = ROOT / "runs" / "m4-heldout-ladder-static"

SCAFFOLD = "openai-agents"          # A8.10: ladder static slice is OpenAI Agents ONLY
SCAFFOLDS = [SCAFFOLD]              # keep the per-scaffold machinery/guards (single entry)
N = 10                             # A8.3 cell size
CAP = 225.0                        # A8.7/A8.12 autonomous cap
PRIOR_SPENT_DEFAULT = 106.709125   # cumulative to date (coordinator handoff)

# --- Ladder rungs (A8.1/A8.10; wiring copied from scripts/m4_ladder_qual.py) ----------
# price = (input $/MTok, output $/MTok) injected into runner.trial.PRICES at runtime.
# rate = $/trial fallback for the spend projection until an observed mean exists.
#   Haiku: A8.12 ladder-static estimate (~$0.24 / 360-trial upper bound => ~$0.000667/trial;
#          consistent with Haiku 5.5 being ~1/20th of Sonnet's $0.0123/trial). qwen3: $0 local.
MODELS: dict[str, dict] = {
    "haiku55": {"model": "claude-haiku-5-5", "base_url": "https://api.anthropic.com/v1/",
                    "local": False, "digest": "hosted-api (no local digest)", "price": (0.10, 0.50),
                    "rate": 0.000667},
    "qwen3-8b": {"model": "qwen3:8b", "base_url": "http://localhost:11434/v1/",
                     "local": True, "digest": "500a1f067a9f", "price": (0.0, 0.0), "rate": 0.0},
}
MODEL_ORDER = ["haiku55", "qwen3-8b"]
RATE = {tag: MODELS[tag]["rate"] for tag in MODEL_ORDER}

# --- Arms (A8.10, exactly 3) ----------------------------------------------------------
# Individual mitigation specs are taken EXACTLY as frozen on DEV in scripts/m4_dev_util.py.
# `combined` = A7 §2 / A8.10-FROZEN `pinning + spotlight-strip + taint-coarse` (D86) -- NOT
# m4_dev_util.py's STALE (...+ taint-fine) composition. `taint-fine` is the separate strict-mode arm.
ARMS: dict[str, list[dict]] = {
    "baseline": [],
    "combined": [{"kind": "pinning"}, {"kind": "spotlight", "strip": True}, {"kind": "taint-coarse"}],
    "taint-fine": [{"kind": "taint-fine"}],
}

# --- Conditional-class map (OpenAI only, per the frontier driver) ---------------------
SCAFFOLD_RUNNABLE_CLASSES = {SCAFFOLD: frozenset(RUNNABLE_CLASSES)}  # {1,2,3,5,7}; 4/6/8 N/A
NA_REASON = {
    (SCAFFOLD, 4): "class 4 (list-cache) N/A on OpenAI (D22/D24/D27)",
    (SCAFFOLD, 6): "class 6 (resource->model) N/A on OpenAI (D24)",
    (SCAFFOLD, 8): "class 8 (MRTR/elicit) N/A on OpenAI (D22/D27)",
}


class AbortRun(Exception):
    pass


def heldout_runnable(configs: list[dict], split: dict[str, str], scaffold: str) -> list[dict]:
    """Held-out-split configs runnable on `scaffold` (mirrors evaluate.dev_runnable, split='held_out')."""
    classes = SCAFFOLD_RUNNABLE_CLASSES[scaffold]
    return [c for c in configs if split.get(c["id"]) == "held_out" and c["class"] in classes]


def inject_prices() -> None:
    """Runtime unit-price injection for the ladder rungs (copied from m4_ladder_qual.py), so
    runner.trial.llm_usage does not raise in record mode. Mutates the in-memory dict only."""
    for tag in MODEL_ORDER:
        trial_mod.PRICES[MODELS[tag]["model"]] = MODELS[tag]["price"]


def print_enumeration(configs: list[dict], split: dict[str, str], prior_spent: float) -> None:
    held = [c for c in configs if split.get(c["id"]) == "held_out"]
    runnable = heldout_runnable(configs, split, SCAFFOLD)
    run_ids = {c["id"] for c in runnable}
    print("=" * 92)
    print("M4 LADDER STATIC slice driver -- OFFLINE enumeration (A8.10). NO model calls.")
    print("=" * 92)
    print(f"held_out split size: {len(held)} configs; scaffold={SCAFFOLD} (ONLY); "
          f"models={MODEL_ORDER}; arms={list(ARMS)} (n={N}/cell)")
    print("PURPOSE: same static attacks on every rung for the capability comparison; "
          "NOT pooled into the frontier headline.")
    print()
    print(f"[{SCAFFOLD}] runnable held-out configs: {len(runnable)}")
    for c in sorted(held, key=lambda c: c["id"]):
        if c["id"] in run_ids:
            etag = "HANDSHAKE" if era_for(c) == HANDSHAKE else "STATELESS"
            print(f"    INCLUDE {c['id']:40s} class={c['class']} era={etag}")
        else:
            reason = NA_REASON.get((SCAFFOLD, c["class"]), f"class {c['class']} not runnable")
            print(f"    EXCLUDE {c['id']:40s} class={c['class']} -- {reason}")
    print()
    ncfg = len(runnable)
    cells = ncfg * len(MODEL_ORDER) * len(ARMS)
    trials = cells * N
    print(f"CELLS: {cells}  (= {ncfg} configs x {len(MODEL_ORDER)} models x {len(ARMS)} arms)")
    print(f"TOTAL TRIALS: {trials}  (n={N}/cell)")
    print()
    print("Breakdown (trials) by model x arm:")
    for tag in MODEL_ORDER:
        for arm in ARMS:
            print(f"    {tag:9s} {arm:12s} = {ncfg} configs x {N} = {ncfg * N} trials")
    print()
    proj_run = 0.0
    for tag in MODEL_ORDER:
        t = ncfg * len(ARMS) * N
        c = t * RATE[tag]
        proj_run += c
        tail = "  (LOCAL Ollama, $0 API; wall-clock recorded)" if MODELS[tag]["local"] else ""
        print(f"    {tag:9s} ({MODELS[tag]['model']}): {t} trials x ${RATE[tag]:.6f} = ${c:.4f}{tail}")
    print(f"PROJECTED RUN COST (API $): ${proj_run:.4f}")
    print("    qwen3-8b wall-clock: ~3 h LOCAL inference for this static slice (A8.12), "
          "overnight under `caffeinate -i`; Haiku wall-clock negligible (hosted).")
    print(f"CUMULATIVE: prior ${prior_spent:.6f} + run ${proj_run:.4f} = ${prior_spent + proj_run:.4f}  "
          f"(cap ${CAP:.0f}; {'UNDER' if prior_spent + proj_run <= CAP else 'OVER -- WOULD ABORT'})")
    print("=" * 92)


# ---- LIVE driver (author-gated; NOT run in this session) ------------------------------
def _project(prior: float, cumulative: float, planned_per_model: dict[str, int],
             mcost: dict[str, list[float]]) -> float:
    """Mixed-model projection (m4_dev_util): each model's remaining trials priced at its own
    observed mean (fallback RATE). qwen3's rate is $0, so it never moves the projection."""
    proj = prior + cumulative
    for tag in MODEL_ORDER:
        done = mcost[tag][1]
        rem = max(0, planned_per_model[tag] - done)
        rate = (mcost[tag][0] / done) if done else RATE[tag]
        proj += rem * rate
    return proj


def _apparatus_aborts(trecs: list[TrialRecord]) -> list[str]:
    """D31 abort checks (evaluate.aggregate -> check_aborts) on the BASELINE arm only: the
    exposure<0.50 floor is the apparatus-health signal (payload reached the model boundary).
    Control-arm checks are inert here (no control trials)."""
    if not trecs:
        return []
    return check_aborts(aggregate(trecs))


async def run_full(prior_spent: float) -> None:
    inject_prices()  # ladder rungs into runner.trial.PRICES (runtime only) BEFORE any trial
    for tag in MODEL_ORDER:
        if MODELS[tag]["model"] not in trial_mod.PRICES:
            raise SystemExit(f"price injection failed for {tag}")
    assert not OUT_ROOT.exists(), f"{OUT_ROOT} exists; a run never reuses a directory (refusing)"
    configs, split, tasks = load_corpus()

    runnable = heldout_runnable(configs, split, SCAFFOLD)
    planned_per_model = {tag: len(runnable) * len(ARMS) * N for tag in MODEL_ORDER}

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    cell_rows: list[dict] = []
    baseline_trecs: dict[str, list[TrialRecord]] = {SCAFFOLD: []}
    cumulative = 0.0
    wall_total = {tag: 0.0 for tag in MODEL_ORDER}
    mcost = {tag: [0.0, 0] for tag in MODEL_ORDER}  # [sum_cost, n]
    arm_n: dict[tuple[str, str], int] = {}
    arm_err: dict[tuple[str, str], int] = {}

    def flush() -> None:
        (OUT_ROOT / "records.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
        (OUT_ROOT / "cells.jsonl").write_text("\n".join(json.dumps(r) for r in cell_rows) + "\n")

    def write_summary(aborts: list[str], *, aborted: bool) -> dict:
        base = baseline_trecs[SCAFFOLD]
        summary = {
            "run_id": OUT_ROOT.name, "slice": "ladder-static (A8.10; NOT pooled into frontier headline)",
            "models": {tag: MODELS[tag]["model"] for tag in MODEL_ORDER},
            "model_digests": {tag: MODELS[tag]["digest"] for tag in MODEL_ORDER},
            "scaffold": SCAFFOLD, "arms": list(ARMS), "n": N, "aborted": aborted, "aborts": aborts,
            "prior_spent_usd": prior_spent, "run_cost_usd": round(cumulative, 6),
            "cumulative_usd": round(prior_spent + cumulative, 6), "cap_usd": CAP,
            "wall_clock_total_s": {tag: round(wall_total[tag], 1) for tag in MODEL_ORDER},
            "trials": len(records), "cells": len(cell_rows),
            "apparatus_check_note": "exposure<0.50 floor (D31/check_aborts) on BASELINE arm only",
            "aggregate_baseline": aggregate(base) if base else None,
            "aborts_baseline": _apparatus_aborts(base),
        }
        (OUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary

    async with HubServer(ProxyHub()) as hub:
        for config in runnable:
            era = era_for(config)  # OpenAI: class 3 -> HANDSHAKE, else STATELESS
            compat = [tasks[t] for t in config["compatible_tasks"] if t in tasks]
            if not compat:
                continue
            for tag in MODEL_ORDER:
                cfg = MODELS[tag]
                model = cfg["model"]
                api_key = anthropic_api_key() if not cfg["local"] else "ollama"
                for arm, mit in ARMS.items():
                    key = (SCAFFOLD, arm)
                    arm_n.setdefault(key, 0)
                    arm_err.setdefault(key, 0)
                    cell_succ = cell_err = cell_exposed = 0
                    cell_cost = cell_wall = 0.0
                    for k in range(N):
                        proj = _project(prior_spent, cumulative, planned_per_model, mcost)
                        if proj > CAP:
                            flush()
                            write_summary([f"projected ${proj:.2f} > ${CAP:.0f}"], aborted=True)
                            raise AbortRun(f"projected ${proj:.2f} > ${CAP:.0f} "
                                           f"(cumulative ${prior_spent + cumulative:.4f})")
                        task = compat[k % len(compat)]
                        tid = f"hls__{tag}__{config['id']}__{arm}__{k}"
                        trial_dir = OUT_ROOT / tag / arm / config["id"] / f"t{k}"
                        spec = TrialSpec(
                            run_id=OUT_ROOT.name, trial_id=tid, task_id=task.id,
                            instruction=task.prompts[0], prompts=list(task.prompts), era=era,
                            transport="streamable-http", scaffold=SCAFFOLD, model=model,
                            mode="record", trial_dir=str(trial_dir), servers=list(SERVERS),
                            attack_config=config, model_settings=model_settings(model),
                            mitigations=list(mit), base_url=cfg["base_url"])
                        rec = await run_attack(spec, task, config, hub=hub, api_key=api_key)
                        # wall-clock from the per-trial result.json (run_trial already wrote it);
                        # matters for the $0 qwen rung (A8.10 "record wall-clock").
                        rp = trial_dir / "result.json"
                        wall = (json.loads(rp.read_text()).get("wall_clock_s") or 0.0) if rp.is_file() else 0.0
                        if arm == "baseline":
                            baseline_trecs[SCAFFOLD].append(rec)
                        d = rec.to_dict()
                        d.update({"scaffold": SCAFFOLD, "model_tag": tag, "arm": arm,
                                  "mitigations": list(mit), "k": k, "wall_clock_s": wall,
                                  "trial_dir": str(trial_dir)})
                        records.append(d)
                        cost = rec.cost_usd or 0.0
                        cumulative += cost
                        wall_total[tag] += wall
                        mcost[tag][0] += cost
                        mcost[tag][1] += 1
                        arm_n[key] += 1
                        cell_cost += cost
                        cell_wall += wall
                        cell_succ += int(rec.attack_success)
                        cell_exposed += int(rec.exposed is True)
                        if rec.error:
                            cell_err += 1
                            arm_err[key] += 1
                        if arm_n[key] >= 20 and arm_err[key] / arm_n[key] > 0.10:
                            flush()
                            write_summary([(f"scaffold/arm {key} harness-error rate "
                                           f"{arm_err[key]}/{arm_n[key]} > 10%")], aborted=True)
                            raise AbortRun(f"scaffold/arm {key} harness-error rate "
                                           f"{arm_err[key]}/{arm_n[key]} > 10%")
                    cell_rows.append({
                        "scaffold": SCAFFOLD, "model_tag": tag, "model": model, "arm": arm,
                        "config_id": config["id"], "cls": config["class"], "era": era,
                        "n": N, "successes": cell_succ, "errors": cell_err, "exposed": cell_exposed,
                        "cost_usd": round(cell_cost, 6), "wall_clock_s": round(cell_wall, 1)})
                    c = cell_rows[-1]
                    print(f"  {tag:9s} {arm:12s} {config['id']:40s} succ={c['successes']}/{N} "
                          f"exp={c['exposed']} err={c['errors']} ${c['cost_usd']:.4f} "
                          f"wall={c['wall_clock_s']}s", flush=True)
        # Apparatus health (D31 exposure floor) on the baseline arm (both rungs pooled: exposure
        # is a proxy/boundary property, model-independent) -- a silent injection failure ABORTS
        # before the slice can be read.
        sab = _apparatus_aborts(baseline_trecs[SCAFFOLD])
        if sab:
            flush()
            write_summary(sab, aborted=True)
            raise AbortRun(f"apparatus check failed: {sab}")
    flush()
    write_summary([], aborted=False)
    print(f"FULL DONE: cells={len(cell_rows)} trials={len(records)} "
          f"run_cost=${round(cumulative,6)} cumulative=${prior_spent + cumulative:.4f} "
          f"wall={ {tag: round(wall_total[tag],1) for tag in MODEL_ORDER} } "
          f"errors={sum(r['errors'] for r in cell_rows)} successes={sum(r['successes'] for r in cell_rows)}",
          flush=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "enumerate"
    prior = float(sys.argv[2]) if len(sys.argv) > 2 else PRIOR_SPENT_DEFAULT
    if mode == "enumerate":
        configs, split, _ = load_corpus()
        print_enumeration(configs, split, prior)
        return
    if mode == "full":
        try:
            asyncio.run(run_full(prior))
        except AbortRun as a:
            print(f"ABORT (full): {a}", flush=True)
            raise SystemExit(3) from a
        return
    raise SystemExit("mode must be 'enumerate' (offline) or 'full' (live, author-gated)")


if __name__ == "__main__":
    main()
