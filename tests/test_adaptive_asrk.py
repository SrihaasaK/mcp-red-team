"""Guard (f): ASR@k on hand-built outcome sequences with hand-checked values, and the
early-stop invariance (design.md §3). $0, pure.
"""
from __future__ import annotations

import pytest

from mcpredteam.adaptive.asrk import asr_at_k, asr_curve, success_at_k

# 10 cells, each by its first-success attempt index (None = no success within budget).
INDICES = [1, 2, 5, 11, None, 100, 3, None, 50, 10]
# Hand-checked: <=1 -> {1}=1 ; <=10 -> {1,2,5,3,10}=5 ; <=100 -> {1,2,5,11,100,3,50,10}=8 (2 None out).
EXPECT = {1: (1, 0.1), 10: (5, 0.5), 100: (8, 0.8)}


def test_f_asr_at_k_hand_checked():
    for k, (succ, point) in EXPECT.items():
        r = asr_at_k(INDICES, k)
        assert r.successes == succ, (k, r.successes, succ)
        assert r.n == len(INDICES)
        assert r.point == pytest.approx(point)
        assert 0.0 <= r.ci_low <= r.point <= r.ci_high <= 1.0  # Wilson CI attached and sane
    curve = asr_curve(INDICES)
    assert {k: c.successes for k, c in curve.items()} == {1: 1, 10: 5, 100: 8}


def test_f_success_at_k_boundary():
    # a cell that first succeeds at attempt j: success@k is False for k<j, True for k>=j
    assert not success_at_k(5, 4)
    assert success_at_k(5, 5)
    assert success_at_k(5, 6)
    assert not success_at_k(None, 100)
    with pytest.raises(ValueError):
        success_at_k(1, 0)


def test_f_early_stop_is_asr_preserving():
    # Early stop records first_success_index = j and stops. The FULL-budget run would record the
    # same j (the first success is the same attempt). So ASR@k is identical either way: it is a
    # pure function of {first_success_index}. Model a full run by the per-attempt success vector
    # and derive its first-success index, then compare.
    def first_success(vec: list[bool]) -> int | None:
        return next((i for i, s in enumerate(vec, 1) if s), None)

    full_vectors = [
        [False, False, True, False, True],   # first success at 3
        [False] * 5,                         # never
        [True, False, False],                # first success at 1
    ]
    early = [first_success(v) for v in full_vectors]   # what the early-stop loop records
    assert early == [3, None, 1]
    for k in (1, 2, 3, 10):
        from_early = asr_at_k(early, k)
        from_full = asr_at_k([first_success(v) for v in full_vectors], k)
        assert from_early.successes == from_full.successes
        assert from_early.point == from_full.point
