"""Rebuild review/M2/v2/review_packet/ per SPEC §9.2 for the CORRECTED dev re-run
(runs/m2-dev-v2, D55/D56/D61/D66; D73): 10 trajectories = all 3 attack successes (so every
ASR success is auditable) + 7 drawn at random from the remaining trials with a FIXED seed.

Reproducible sampling call (CPython 3.12):
    succ = sorted(r["trial_id"] for r in records if r["arm"] == "attack" and r["attack_success"])
    rest = sorted(r["trial_id"] for r in records if r["trial_id"] not in set(succ))
    sample = succ + random.Random(SEED).sample(rest, 10 - len(succ))
Re-running yields the same 10 trial IDs. Each entry carries its raw logs (copied,
path-sanitized), its exposure flag, and its oracle verdict + reason (attack arm: success,
rule, disclosure, the answer-hijack refusal-quote audit, and the source-read-before-sink
backstop for exfil successes) or its benign-checker verdict (control arm). Configs are
hydrated (D61: canary resolved from its mock source) before scoring.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import yaml

from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import (score, audit_answer_hijack_refusal_quote,
                                     audit_source_read_before_sink)
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.runner.canary import hydrate_attack_config
from mcpredteam.runner.tasks import check, load_tasks

SEED = 20261008
FORBIDDEN = ("/Users/", "/home/", "sk-ant-")

ROOT = repo_root()
RUN = ROOT / "runs" / "m2-dev-v2"
OUT = ROOT / "review" / "M2" / "v2" / "review_packet"
records = [json.loads(line) for line in (RUN / "records.jsonl").read_text().splitlines() if line.strip()]
by_id = {r["trial_id"]: r for r in records}
configs = {c["id"]: c for c in yaml.safe_load((ROOT / "attacks" / "corpus.yaml").read_text())["configs"]}
tasks = {t.id: t for t in load_tasks()}

succ = sorted(r["trial_id"] for r in records if r["arm"] == "attack" and r["attack_success"])
rest = sorted(r["trial_id"] for r in records if r["trial_id"] not in set(succ))
sample = succ + random.Random(SEED).sample(rest, 10 - len(succ))

shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
index = []
for tid in sample:
    r = by_id[tid]
    td = RUN / tid
    dest = OUT / tid
    dest.mkdir()
    for f in td.iterdir():
        if f.name == "adapter.stderr.txt" or not f.is_file():
            continue
        dest.joinpath(f.name).write_bytes(
            f.read_bytes().decode("utf-8", "surrogateescape").replace(str(ROOT), "<repo>")
            .encode("utf-8", "surrogateescape"))
    traj = Trajectory.from_trial(td)
    entry = {"trial_id": tid, "arm": r["arm"], "model": r["model"], "task": r["task_id"],
             "exposed": r["exposed"], "final_answer": (traj.turns[-1].final_answer or "")[:300]}
    if r["arm"] == "attack":
        cfg = hydrate_attack_config(configs[r["config_id"]])
        v = score(traj, cfg, tasks[r["task_id"]])
        bs = audit_source_read_before_sink(traj, cfg, tasks[r["task_id"]])
        entry.update(cls=r["cls"], config_id=r["config_id"],
                     oracle={"success": v.success, "rule": v.rule,
                             "fired": list(v.fired),
                             "disclosure_count": len(v.disclosure),
                             "refusal_quote_audit": audit_answer_hijack_refusal_quote(
                                 traj, cfg, tasks[r["task_id"]]),
                             "source_read_before_sink": bs,
                             "evidence": [getattr(e, "__dict__", str(e)) for e in (v.evidence or [])][:3]})
    else:
        ok = check(tasks[r["task_id"]], traj.turns[-1].final_answer or "",
                   [{"server": c.server, "name": c.name, "arguments": c.arguments}
                    for t in traj.turns for c in t.tool_calls])
        entry.update(oracle={"control_arm": True, "benign_pass": ok.passed,
                             "note": "no-attack control: no attack oracle applies; scored for utility + FP"})
    index.append(entry)

(OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
leaks = [str(p) for p in OUT.rglob("*") if p.is_file()
         and any(s in p.read_bytes().decode("utf-8", "surrogateescape") for s in FORBIDDEN)]
if leaks:
    raise SystemExit(f"packet leak: {leaks}")

md = ["# M2 review packet — CORRECTED re-run v2 (SPEC §9.2)", "",
      f"10 trajectories from the corrected dev re-run `runs/m2-dev-v2` ({len(records)} trials, "
      "D55/D56/D61/D66, D73): **all 3 attack successes** (every ASR success is auditable) + 7 drawn "
      "at random from the rest with a fixed seed. Raw logs are beside each entry (path-sanitized).", "",
      "**Reproducible sampling call (CPython 3.12):**", "```python",
      "succ = sorted(r['trial_id'] for r in records if r['arm'] == 'attack' and r['attack_success'])",
      "rest = sorted(r['trial_id'] for r in records if r['trial_id'] not in set(succ))",
      f"sample = succ + random.Random({SEED}).sample(rest, 10 - len(succ))", "```",
      f"SEED = {SEED}. Sampled IDs:", ""]
for e in index:
    if e["arm"] == "attack":
        o = e["oracle"]
        md.append(f"- **{e['trial_id']}** [attack, class {e['cls']}, {e['model'].split('-')[1]}] "
                  f"success={o['success']} rule={o['rule']} exposed={e['exposed']} "
                  f"disclosure={o['disclosure_count']} refusal_quote_audit={o['refusal_quote_audit']} "
                  f"source_read_before_sink={o['source_read_before_sink']}  evidence={o['evidence']}")
    else:
        md.append(f"- **{e['trial_id']}** [control, {e['model'].split('-')[1]}] "
                  f"benign_pass={e['oracle']['benign_pass']} exposed={e['exposed']} (no attack oracle)")
(ROOT / "review" / "M2" / "v2" / "review_packet.md").write_text("\n".join(md) + "\n")
print("SEED", SEED, "| sampled 10:", sample)
print("arms:", {a: sum(e["arm"] == a for e in index) for a in ("attack", "control")}, "| leaks:", leaks or "none")
