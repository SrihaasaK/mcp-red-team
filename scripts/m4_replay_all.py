"""M4-T2 full replay (SPEC 9.1 reproducibility standard, $0 / zero live calls): replay every
recorded arm with its mitigations re-applied (D81) and assert EQUAL + zero-live for all 2160 trials.
Writes review/M4/dev-util/replay_all.json.

    uv run python -I scratchpad/m4_replay_all.py
"""
import asyncio
import json

from mcpredteam.config import repo_root
from mcpredteam.runner.matrix import replay_run

ROOT = repo_root()
ARMS = ["pinning", "spotlight-delimiters", "spotlight-strip", "taint-coarse", "taint-fine", "combined"]


async def main() -> None:
    out = {"arms": {}, "total_trials": 0, "total_equal": 0, "total_nonzero_live": 0}
    for arm in ARMS:
        run_dir = ROOT / "runs" / f"m4-dev-util-{arm}"
        print(f"=== replay {arm} ===", flush=True)
        s = await replay_run(run_dir)
        nonzero_live = sum(1 for c in s["comparisons"] if not c.get("zero_live_calls", False))
        out["arms"][arm] = {"trials": s["trials"], "equal": s["equal"],
                            "nonzero_live": nonzero_live, "replay_dir": s["replay_dir"]}
        out["total_trials"] += s["trials"]
        out["total_equal"] += s["equal"]
        out["total_nonzero_live"] += nonzero_live
        print(f"  {arm}: {s['equal']}/{s['trials']} EQUAL, nonzero_live={nonzero_live}", flush=True)
    (ROOT / "review" / "M4" / "dev-util" / "replay_all.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"REPLAY DONE: {out['total_equal']}/{out['total_trials']} EQUAL, "
          f"nonzero_live={out['total_nonzero_live']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
