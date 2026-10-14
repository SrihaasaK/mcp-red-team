"""HISTORICAL (D107) — kept for provenance; NOT reproducible in the current tree.

Since the D61 corpus re-pin this script raises "config has canary_ref but no resolved canary"
(it scores without hydrating canary_ref), so it can no longer regenerate review/M2/review_packet/
here. The committed review/M2/review_packet/ is the source of truth. Ruff-excluded (pre-existing debt).

Rebuild review/M2/review_packet/ per SPEC §9.2: 10 trajectories drawn at random
from ALL m2-dev trials (both models, both arms) with a FIXED, recorded seed.

Reproducible sampling call (CPython 3.12):
    ids = sorted(r["trial_id"] for r in records)   # all 580 m2-dev trials
    sample = random.Random(SEED).sample(ids, 10)
Re-running yields the same 10 trial IDs. Each entry carries its raw logs (copied,
path-sanitized), its exposure flag, and its oracle verdict with evidence (attack
arm) or its benign-checker verdict (control arm).
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import yaml

from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import score
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.runner.tasks import check, load_tasks

SEED = 20261007
FORBIDDEN = ("/Users/", "/home/", "sk-ant-")

ROOT = repo_root()
RUN = ROOT / "runs" / "m2-dev"
OUT = ROOT / "review" / "M2" / "review_packet"
records = [json.loads(line) for line in (RUN / "records.jsonl").read_text().splitlines() if line.strip()]
by_id = {r["trial_id"]: r for r in records}
configs = {c["id"]: c for c in yaml.safe_load((ROOT / "attacks" / "corpus.yaml").read_text())["configs"]}
tasks = {t.id: t for t in load_tasks()}

ids = sorted(r["trial_id"] for r in records)
sample = random.Random(SEED).sample(ids, 10)

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
        dest.joinpath(f.name).write_text(f.read_text().replace(str(ROOT), "<repo>"))
    traj = Trajectory.from_trial(td)
    entry = {"trial_id": tid, "arm": r["arm"], "model": r["model"], "task": r["task_id"],
             "exposed": r["exposed"], "final_answer": (traj.turns[-1].final_answer or "")[:300]}
    if r["arm"] == "attack":
        v = score(traj, configs[r["config_id"]], tasks[r["task_id"]])
        entry.update(cls=r["cls"], config_id=r["config_id"],
                     oracle={"success": v.success, "rule": v.rule,
                             "evidence": [getattr(e, "__dict__", str(e)) for e in (v.evidence or [])][:3]})
    else:
        ok = check(tasks[r["task_id"]], traj.turns[-1].final_answer or "",
                   [{"server": c.server, "name": c.name, "arguments": c.arguments}
                    for t in traj.turns for c in t.tool_calls])
        entry.update(oracle={"control_arm": True, "benign_pass": ok.passed,
                             "note": "no-attack control: no attack oracle applies; scored for utility + FP"})
    index.append(entry)

(OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")
leaks = [str(p) for p in OUT.rglob("*") if p.is_file() and any(s in p.read_text() for s in FORBIDDEN)]
if leaks:
    raise SystemExit(f"packet leak: {leaks}")

md = ["# M2 review packet (SPEC §9.2)", "",
      f"10 trajectories drawn at random from all {len(records)} `m2-dev` trials (both models, "
      "both arms) with a fixed seed. Raw logs are beside each entry (path-sanitized).", "",
      "**Reproducible sampling call (CPython 3.12):**", "```python",
      f"ids = sorted(r['trial_id'] for r in records)   # all {len(records)} m2-dev trials",
      f"sample = random.Random({SEED}).sample(ids, 10)", "```",
      f"SEED = {SEED}. Sampled IDs:", ""]
for e in index:
    if e["arm"] == "attack":
        o = e["oracle"]
        md.append(f"- **{e['trial_id']}** [attack, class {e['cls']}, {e['model'].split('-')[1]}] "
                  f"success={o['success']} rule={o['rule']} exposed={e['exposed']}  evidence={o['evidence']}")
    else:
        md.append(f"- **{e['trial_id']}** [control, {e['model'].split('-')[1]}] "
                  f"benign_pass={e['oracle']['benign_pass']} exposed={e['exposed']} (no attack oracle)")
(ROOT / "review" / "M2" / "review_packet.md").write_text("\n".join(md) + "\n")
print("SEED", SEED, "| sampled 10:", sample)
print("arms:", {a: sum(e["arm"] == a for e in index) for a in ("attack", "control")}, "| leaks:", leaks or "none")
