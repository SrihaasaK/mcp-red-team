"""Reproducible producer for review/M2/v2/m2_report.json — the CORRECTED per-model dev report
(runs/m2-dev-v2, D55/D56/D61/D66; D73), in the committed review/M2/m2_report.json schema PLUS
the D63 #2 diagnostics (disclosure, answer-hijack refusal-quote audit, exfil successes and the
source-read-before-sink backstop). Pure re-scoring of recorded trajectories -- NO live calls.

    uv run python -I review/M2/v2/build_report_json.py
"""
import json
from collections import defaultdict

from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import (score, audit_answer_hijack_refusal_quote,
                                     audit_source_read_before_sink)
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.report import stats
from mcpredteam.runner.canary import hydrate_attack_config
from mcpredteam.runner.evaluate import load_corpus

root = repo_root()
RUN = root / "runs" / "m2-dev-v2"
OUT = root / "review" / "M2" / "v2" / "m2_report.json"
configs, split, tasks = load_corpus()
cfg_by_id = {c["id"]: c for c in configs}
recs = [json.loads(x) for x in (RUN / "records.jsonl").read_text().splitlines() if x.strip()]
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5"]
CLASSES = [1, 2, 3, 5, 7]


def cell(succ, n, exp, succ_exp):
    lo, hi = stats.wilson_ci(succ, n) if n else (0.0, 0.0)
    elo, ehi = stats.wilson_ci(succ_exp, exp) if exp else (0.0, 0.0)
    return {"n": n, "succ": succ, "raw": round(succ / n, 4) if n else 0.0,
            "raw_ci": [round(lo, 3), round(hi, 3)], "expo": round(exp / n, 4) if n else 0.0,
            "exp_asr": round(succ_exp / exp, 4) if exp else 0.0, "exp_ci": [round(elo, 3), round(ehi, 3)]}


by = defaultdict(lambda: {"n": 0, "succ": 0, "exp": 0, "succ_exp": 0})
disc = defaultdict(int); refq = defaultdict(int); exfil_succ = defaultdict(int); backstop_true = defaultdict(int)
rec_by_id = {r["trial_id"]: r for r in recs}
for d in sorted(RUN.glob("atk__*")):
    r = rec_by_id[d.name]
    if r["error"]:
        continue
    cfg = cfg_by_id[r["config_id"]]; hcfg = hydrate_attack_config(cfg); task = tasks[r["task_id"]]
    traj = Trajectory.from_trial(d)
    v = score(traj, hcfg, task)
    assert v.success == r["attack_success"], (d.name, v.success, r["attack_success"])
    m, c = r["model"], r["cls"]
    for key in ((m, c), (m, "all")):
        by[key]["n"] += 1
        by[key]["succ"] += int(v.success)
        if r["exposed"] is True:
            by[key]["exp"] += 1
            by[key]["succ_exp"] += int(v.success)
    if v.disclosure:
        disc[m] += 1
    if audit_answer_hijack_refusal_quote(traj, hcfg, task):
        refq[m] += 1
    if v.success and cfg["payload"].get("oracle") == "exfil":
        exfil_succ[m] += 1
        if audit_source_read_before_sink(traj, hcfg, task) is True:
            backstop_true[m] += 1

ctl = [r for r in recs if r["arm"] == "control" and not r["error"]]
cost_by_model = defaultdict(float)
for r in recs:
    cost_by_model[r["model"]] += r.get("cost_usd") or 0.0


def control_for(m):
    xs = [r for r in ctl if r["model"] == m]
    bs = sum(r["benign_pass"] for r in xs)
    fp = sum(bool(r["fp_configs"]) for r in xs)
    return (bs / len(xs) if xs else 0.0), (fp / len(xs) if xs else 0.0)


out = {"models": {}, "pooled": {}, "spend_usd": round(sum(cost_by_model.values()), 6)}
for m in MODELS:
    o = by[(m, "all")]
    butil, cfp = control_for(m)
    out["models"][m] = {
        "overall": cell(o["succ"], o["n"], o["exp"], o["succ_exp"]),
        "by_class": {str(c): cell(by[(m, c)]["succ"], by[(m, c)]["n"], by[(m, c)]["exp"], by[(m, c)]["succ_exp"])
                     for c in CLASSES},
        "utility_benign_success": round(butil, 4),
        "control_fp_rate": round(cfp, 4),
        "spend_usd": round(cost_by_model[m], 4),
        "disclosure": disc[m],
        "refusal_quote_audit": refq[m],
        "exfil_successes": exfil_succ[m],
        "exfil_successes_with_source_read": backstop_true[m],
    }

pall = defaultdict(lambda: {"n": 0, "succ": 0, "exp": 0, "succ_exp": 0})
for (m, c), v in by.items():
    for k in v:
        pall[c][k] += v[k]
pc = pall["all"]; allctl = len(ctl)
out["pooled"] = {
    "overall": cell(pc["succ"], pc["n"], pc["exp"], pc["succ_exp"]),
    "by_class": {str(c): cell(pall[c]["succ"], pall[c]["n"], pall[c]["exp"], pall[c]["succ_exp"]) for c in CLASSES},
    "control_trials": allctl,
    "control_fp_rate": round(sum(bool(r["fp_configs"]) for r in ctl) / allctl, 4),
    "control_benign_success": round(sum(r["benign_pass"] for r in ctl) / allctl, 4),
    "disclosure": sum(disc.values()),
    "refusal_quote_audit": sum(refq.values()),
    "exfil_successes": sum(exfil_succ.values()),
    "exfil_successes_with_source_read": sum(backstop_true.values()),
}
OUT.write_text(json.dumps(out, indent=1) + "\n")
print("wrote", OUT, "| pooled:", out["pooled"]["overall"])
