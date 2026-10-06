"""M1 acceptance (SPEC §12): a recorded benign session replays with zero live
calls and meets replay equality (SPEC §9.1) in each supported era.

Replays the committed live recording (20 trials: 5 tasks × 2 eras × 2
transports, Sonnet 5.5, OpenAI Agents SDK) entirely offline.
"""

import asyncio
from pathlib import Path

from mcpredteam.proxy.relay import HANDSHAKE, STATELESS
from mcpredteam.runner.matrix import replay_run

RECORDING = Path(__file__).parent / "recordings" / "m1" / "m1-matrix"


def test_m1_recorded_sessions_replay_with_zero_live_calls_in_both_eras(tmp_path):
    summary = asyncio.run(replay_run(RECORDING, tmp_path / "replay"))
    assert summary["trials"] == 20
    assert summary["eras_covered"] == [HANDSHAKE, STATELESS]
    not_equal = [c for c in summary["comparisons"] if not c["equal"]]
    assert not not_equal, not_equal
    assert all(c["zero_live_calls"] for c in summary["comparisons"])
    transports = {c["trial_id"].rsplit("__", 1)[1] for c in summary["comparisons"]}
    assert transports == {"http", "stdio"}
