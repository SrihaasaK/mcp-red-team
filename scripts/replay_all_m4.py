"""M4-T1 regression: with no mitigation selected, every committed/recorded run replays EQUAL and
zero-live (SPEC §9.1). Offline. Covers the orchestrator's regression list."""
import asyncio
import shutil
from pathlib import Path

from mcpredteam.config import repo_root
from mcpredteam.runner.matrix import replay_run

RUNS = {
    "m1-final": "m1-final",
    "m2-dev-v2": "m2-dev-v2",
    "m3-langgraph": "m3-langgraph-benign",
    "m3-claude-agent-sdk": "m3-claude-agent-sdk-benign",
    "m3-openai-benign": "m3-openai-benign",
}
OUT = Path("/private/tmp/claude-501/-Users-srihaasa-orca-workspaces-mcp-red-team-mcp-red-team/"
           "87eb7e07-2a50-44c3-abeb-9411a0fda68d/scratchpad/replay_all_m4_out")
shutil.rmtree(OUT, ignore_errors=True)
root = repo_root()
allok = True
for label, name in RUNS.items():
    d = root / "runs" / name
    if not d.is_dir():
        print(f"{label}: MISSING {d}"); allok = False; continue
    s = asyncio.run(replay_run(d, OUT / label))
    ne = [c["trial_id"] for c in s["comparisons"] if not c["equal"]]
    nz = [c["trial_id"] for c in s["comparisons"] if not c["zero_live_calls"]]
    ok = not ne and not nz
    allok = allok and ok
    print(f"{label}: trials={s['trials']} equal={s['equal']} "
          f"not_equal={len(ne)} not_zero_live={len(nz)} -> {'EQUAL+ZERO-LIVE' if ok else 'DIVERGENCE'}")
    if ne:
        print("   not-equal:", ne[:10])
print("REGRESSION:", "ALL EQUAL + ZERO-LIVE" if allok else "FAILED")
