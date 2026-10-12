"""Reconstruct the FULL 48-cell held-out ADAPTIVE record from the on-disk attempt dirs.

WHY: the resume run (scripts/m4_adaptive_heldout.py resume) rewrote
runs/m4-heldout-adaptive/{cells.jsonl,summary.json} with ONLY its 9 qwen seed-0 cells,
dropping the 36 Haiku cells (seed0/1/2) and the 3 pre-resume qwen cells (c1-07
baseline/spotlight/taint-coarse) from those two aggregate files. The raw per-attempt
evidence for all 48 cells is intact on disk, so the dropped records are RECONSTRUCTED here
by REPLAYING THE FROZEN ORACLE over the saved logs -- the CLAUDE.md #6 "replay with zero
live calls" standard, NOT a new judgment. No model is called; $0.

METHOD (byte-identical to mcpredteam.adaptive.loop.run_cell lines 159-174):
  errored   = bool(result.json["error"])
  traj      = Trajectory.from_trial(attempt_dir)
  verdict   = score(traj, hydrate_attack_config(trial.json["attack_config"]), task)
  success   = verdict.success and not errored
  blocked_by= first proxy[].mitigation_blocks[].kind, else None
  first_success_index = i of the first success (live early-stopped there, so the dir set
                        already stops at that attempt for a successful cell).

INTEGRITY CHECK (the whole point): the 9 cells that DID survive in cells.jsonl are
reconstructed by this same code and compared field-by-field (first_success_index,
attempts_run, blocked_attempts, errored_attempts, per-attempt success). They must match
EXACTLY; if they do, the 39 reconstructed cells are trustworthy by the same path. A
mismatch aborts (no report is built on an unverified reconstruction).

No attack/oracle/seed/stat is changed (CLAUDE.md #1). Writes rebuilt_cells.jsonl +
validation.json next to this file.

    uv run python -I review/M4/adaptive/heldout/rebuild_cells.py
"""
from __future__ import annotations

import json
from pathlib import Path

from mcpredteam.adaptive.loop import _blocked_by
from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import score
from mcpredteam.oracle.trajectory import Trajectory
from mcpredteam.runner.canary import hydrate_attack_config
from mcpredteam.runner.evaluate import load_corpus

ROOT = repo_root()
RUN = ROOT / "runs" / "m4-heldout-adaptive"
OUT = ROOT / "review" / "M4" / "adaptive" / "heldout"
B = 100
ARMS = ["baseline", "spotlight-delimiters", "taint-coarse", "taint-fine"]


def _attempt_dirs(cell_dir: Path) -> list[Path]:
    return sorted(cell_dir.glob("attempt_*"), key=lambda p: int(p.name.split("_")[1]))


def rebuild_cell(cell_dir: Path, task_by_id: dict) -> dict:
    """Re-derive a cell record from its saved attempt dirs via the frozen oracle."""
    first = json.loads((_attempt_dirs(cell_dir)[0] / "result.json").read_text())
    task = task_by_id[first["task_id"]]
    model = first["model"]
    model_tag = cell_dir.parent.parent.name          # runs/.../<model_tag>/seed<k>/<cell>
    seed_idx = int(cell_dir.parent.name.removeprefix("seed"))
    arm = cell_dir.name.rsplit("__", 1)[1]
    assert arm in ARMS, f"bad arm parsed from {cell_dir.name!r}: {arm!r}"

    config_id = None
    first_success = None
    blocked_attempts = errored_attempts = 0
    attempts: list[dict] = []
    for i, adir in enumerate(_attempt_dirs(cell_dir), 1):
        result = json.loads((adir / "result.json").read_text())
        trial = json.loads((adir / "trial.json").read_text())
        cfg = trial["attack_config"]
        config_id = cfg["id"]
        errored = bool(result.get("error"))
        if errored:
            errored_attempts += 1
        traj = Trajectory.from_trial(adir)
        verdict = score(traj, hydrate_attack_config(cfg), task)  # frozen A5 oracle; canary in-memory only
        success = verdict.success and not errored               # R5: errored is never a success
        blocked_by = _blocked_by(result)
        blocked_attempts += 1 if blocked_by else 0
        attempts.append({"i": i, "transform": (cfg.get("mutators") or ["plain"]),
                         "success": success, "rule": verdict.rule, "blocked_by": blocked_by,
                         "error": result.get("error")})
        if success and first_success is None:
            first_success = i
    return {"cell_id": cell_dir.name, "config": config_id, "task": first["task_id"],
            "model": model, "model_tag": model_tag, "seed_idx": seed_idx, "arm": arm, "budget": B,
            "first_success_index": first_success, "attempts_run": len(attempts),
            "blocked_attempts": blocked_attempts, "errored_attempts": errored_attempts,
            "attempts": attempts}


def main() -> None:
    configs, _split, tasks = load_corpus()
    task_by_id = tasks  # load_corpus already returns {task_id: Task}

    cell_dirs = sorted(p for p in RUN.glob("*/seed*/*") if p.is_dir() and (p / "attempt_001").exists())
    rebuilt = [rebuild_cell(d, task_by_id) for d in cell_dirs]

    # --- INTEGRITY CHECK vs the 9 surviving cells.jsonl rows ---
    survivors = {}
    for line in (RUN / "cells.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            survivors[(r["model_tag"], r["seed_idx"], r["cell_id"])] = r
    mismatches = []
    checked = 0
    rb_index = {(c["model_tag"], c["seed_idx"], c["cell_id"]): c for c in rebuilt}
    for key, sv in survivors.items():
        rb = rb_index.get(key)
        if rb is None:
            mismatches.append({"key": key, "why": "survivor not found in rebuilt set"}); continue
        checked += 1
        for f in ("first_success_index", "attempts_run", "blocked_attempts", "errored_attempts"):
            if rb[f] != sv[f]:
                mismatches.append({"key": key, "field": f, "rebuilt": rb[f], "survivor": sv[f]})
        sv_succ = [a["success"] for a in sv["attempts"]]
        rb_succ = [a["success"] for a in rb["attempts"]]
        if sv_succ != rb_succ:
            mismatches.append({"key": key, "field": "per_attempt_success",
                               "rebuilt_true_at": [i + 1 for i, x in enumerate(rb_succ) if x],
                               "survivor_true_at": [i + 1 for i, x in enumerate(sv_succ) if x]})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rebuilt_cells.jsonl").write_text("".join(json.dumps(c) + "\n" for c in rebuilt))
    validation = {"cells_rebuilt": len(rebuilt), "survivors_checked": checked,
                  "mismatches": mismatches, "ok": not mismatches}
    (OUT / "validation.json").write_text(json.dumps(validation, indent=1) + "\n")

    print(f"rebuilt {len(rebuilt)} cells -> {OUT / 'rebuilt_cells.jsonl'}")
    by_tag = {}
    for c in rebuilt:
        by_tag.setdefault(c["model_tag"], set()).add((c["seed_idx"], c["cell_id"]))
    for tag, s in sorted(by_tag.items()):
        print(f"  {tag}: {len(s)} cells")
    print(f"integrity: checked {checked} survivor rows, mismatches={len(mismatches)}")
    if mismatches:
        print("  FAIL — mismatches:")
        for m in mismatches[:20]:
            print("   ", m)
        raise SystemExit(1)
    succ = [c for c in rebuilt if c["first_success_index"] is not None]
    print(f"OK. total successes across 48 cells: {len(succ)}")
    for c in succ:
        print(f"    SUCCESS {c['model_tag']} seed{c['seed_idx']} {c['cell_id']} @attempt {c['first_success_index']}")


if __name__ == "__main__":
    main()
