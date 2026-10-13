"""Re-score EVERY trial in the FULL released recordings set with the frozen oracle, and verify it
reproduces the per-cell records and aggregate.json. $0, offline, zero live calls.

For each of the recorded trials this re-runs the frozen A5 oracle (`score`) over the hydrated
trajectory and asserts the re-derived success verdict matches the recorded one; it then aggregates
the re-derived verdicts per cell and asserts they match the bundle's cells.jsonl
(successes / exposed / errors / n), and finally rebuilds review/M4/heldout-static/aggregate.json from
the bundle and asserts it is BYTE-IDENTICAL to the committed file.

Note (honest): the ORACLE verdict (success/rule) is RE-DERIVED here. `exposed` and `cost_usd` are
recorded measurements (exposure at the live LLM boundary; live API cost) that cannot be reconstructed
from a static recording; they are carried from the published records.jsonl and checked for
consistency, not re-derived.

Usage (after downloading + extracting heldout-static-recordings-full.tar.gz):
    uv run python -I review/M4/heldout-static/rescore_from_release.py <extracted-dir>
"""
import importlib.util
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import score
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.runner.canary import hydrate_attack_config
from mcpredteam.runner.evaluate import load_corpus

ROOT = repo_root()
COMMITTED = ROOT / "review" / "M4" / "heldout-static" / "aggregate.json"


def _rebuild_aggregate_identical(src_frontier: Path, src_ladder: Path) -> bool:
    path = ROOT / "review" / "M4" / "heldout-static" / "build_aggregate.py"
    spec = importlib.util.spec_from_file_location("heldout_build_aggregate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        wf = tmp / "frontier"
        wf.mkdir(parents=True)
        (wf / "cells.jsonl").write_text((src_frontier / "cells.jsonl").read_text())
        (wf / "summary.json").write_text((src_frontier / "summary.json").read_text())
        rows = []
        for line in (src_frontier / "records.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if not Path(r["trial_dir"]).is_absolute():
                r["trial_dir"] = str(src_frontier / r["trial_dir"])
            rows.append(json.dumps(r))
        (wf / "records.jsonl").write_text("\n".join(rows) + "\n")
        out = tmp / "out"
        out.mkdir()
        mod.FRONTIER, mod.LADDER, mod.OUT = wf, src_ladder, out
        mod.main()
        return (out / "aggregate.json").read_text() == COMMITTED.read_text()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    bundle = Path(sys.argv[1]).resolve()
    frontier = bundle / "frontier"
    if not (frontier / "records.jsonl").exists():
        raise SystemExit(f"not a full recordings bundle (missing frontier/records.jsonl): {bundle}")

    configs, _split, tasks = load_corpus()
    cfg_by_id = {c["id"]: c for c in configs}
    records = [json.loads(l) for l in (frontier / "records.jsonl").read_text().splitlines() if l.strip()]
    cells = [json.loads(l) for l in (frontier / "cells.jsonl").read_text().splitlines() if l.strip()]

    rescored = mism = 0
    cellacc: dict[tuple, dict] = defaultdict(lambda: {"n": 0, "successes": 0, "errors": 0, "exposed": 0})
    for r in records:
        tdir = Path(r["trial_dir"])
        if not tdir.is_absolute():
            tdir = frontier / tdir
        if r.get("error"):
            re_success = False  # an errored trial is not scored (mirrors evaluate.run_attack)
        else:
            traj = Trajectory.from_trial(tdir)
            re_success = score(traj, hydrate_attack_config(cfg_by_id[r["config_id"]]), tasks[r["task_id"]]).success
        rescored += 1
        if bool(re_success) != bool(r["attack_success"]):
            mism += 1
            if mism <= 10:
                print(f"  MISMATCH {r['trial_id']}: rescored={re_success} recorded={r['attack_success']}")
        key = (r["scaffold"], r["model"], r["arm"], r["config_id"])
        acc = cellacc[key]
        acc["n"] += 1
        acc["successes"] += int(re_success and not r.get("error"))
        acc["errors"] += int(bool(r.get("error")))
        acc["exposed"] += int(r.get("exposed") is True)

    # per-cell records consistency (re-derived successes vs the published cells.jsonl)
    cell_mism = 0
    for c in cells:
        key = (c["scaffold"], c["model"], c["arm"], c["config_id"])
        acc = cellacc.get(key, {})
        for f in ("n", "successes", "errors", "exposed"):
            if acc.get(f) != c[f]:
                cell_mism += 1
                print(f"  CELL MISMATCH {key} {f}: rescored={acc.get(f)} published={c[f]}")

    agg_ok = _rebuild_aggregate_identical(frontier, bundle / "ladder")

    print(f"re-scored {rescored} trials; oracle-verdict mismatches={mism}; "
          f"per-cell mismatches={cell_mism}; aggregate byte-identical={agg_ok}")
    if mism or cell_mism or not agg_ok:
        raise SystemExit("RESCORE FAILED")
    print("OK: every trial re-scored identically; per-cell records reproduced; aggregate byte-identical.")


if __name__ == "__main__":
    main()
