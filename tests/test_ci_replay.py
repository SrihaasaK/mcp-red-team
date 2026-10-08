"""The committed CI recordings replay with zero live calls (M3-T3, SPEC §9.1).

Proves the sanitized, committed recording set is fully replayable offline -- including
the attack oracle verdict for the m2-dev attack trials (matrix.compare, step 3). This is
the guarantee the CI workflow (.github/workflows/replay.yml) enforces from a fresh clone.
"""

import asyncio

import pytest

from mcpredteam.config import repo_root
from mcpredteam.runner.matrix import replay_run

SETS = ["m1/m1-matrix", "m2-dev", "m3-langgraph", "m3-claude-agent-sdk", "m3-openai-benign",
        "m4-dev-util"]


@pytest.mark.parametrize("name", SETS)
def test_committed_recording_replays_equal_zero_live(name, tmp_path):
    rec = repo_root() / "tests" / "recordings" / name
    assert rec.is_dir(), f"missing committed recording set {name}"
    summary = asyncio.run(replay_run(rec, tmp_path / name.replace("/", "_")))
    assert summary["trials"] > 0
    assert summary["equal"] == summary["trials"], \
        [c for c in summary["comparisons"] if not c["equal"]]
    assert all(c["zero_live_calls"] for c in summary["comparisons"]), \
        [c["trial_id"] for c in summary["comparisons"] if not c["zero_live_calls"]]
