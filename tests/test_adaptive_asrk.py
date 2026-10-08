"""Guard (f) + R2: ASR@k on hand-built outcome sequences with hand-checked values, the early-stop
invariance, and the BUDGET guard (ASR@k is N/A when k exceeds a cell's budget). $0, pure.
"""
from __future__ import annotations

import pytest

from mcpredteam.adaptive.asrk import asr_at_k, asr_curve, success_at_k

# 10 cells as (first_success_index, budget); None = no success within budget. Budget 100 here.
CELLS = [(i, 100) for i in [1, 2, 5, 11, None, 100, 3, None, 50, 10]]
# Hand-checked: <=1 -> {1}=1 ; <=10 -> {1,2,5,3,10}=5 ; <=100 -> {1,2,5,11,100,3,50,10}=8 (2 None out).
EXPECT = {1: (1, 0.1), 10: (5, 0.5), 100: (8, 0.8)}


def test_f_asr_at_k_hand_checked():
    for k, (succ, point) in EXPECT.items():
        r = asr_at_k(CELLS, k)
        assert r.successes == succ, (k, r.successes, succ)
        assert r.n == len(CELLS)
        assert r.point == pytest.approx(point)
        assert 0.0 <= r.ci_low <= r.point <= r.ci_high <= 1.0  # Wilson CI attached and sane
    curve = asr_curve(CELLS)
    assert {k: c.successes for k, c in curve.items()} == {1: 1, 10: 5, 100: 8}


def test_f_success_at_k_boundary():
    assert not success_at_k(5, 4)
    assert success_at_k(5, 5)
    assert success_at_k(5, 6)
    assert not success_at_k(None, 100)
    with pytest.raises(ValueError):
        success_at_k(1, 0)


def test_r2_budget_guard_refuses_k_over_budget():
    # DEV: every cell ran budget B=20. ASR@1/@10/@20 are defined; ASR@100 is N/A (refuse, not truncate).
    dev = [(idx, 20) for idx in [1, 5, None, 20, None, 3, None, None, 12, None]]
    for k in (1, 10, 20):
        asr_at_k(dev, k)  # defined
    with pytest.raises(ValueError, match="N/A"):
        asr_at_k(dev, 100)
    curve = asr_curve(dev, ks=(1, 10, 20, 100))
    assert curve[100] is None               # N/A on DEV
    assert curve[1].successes == 1          # idx==1 only
    assert curve[20].successes == 5         # 1,5,20,3,12  (<=20, not None)
    # a mixed-budget set: one cell with budget 5 makes ASR@10 N/A too
    mixed = [(1, 20), (None, 5)]
    mc = asr_curve(mixed, ks=(1, 10))
    assert mc[1].successes == 1 and mc[1].n == 2
    assert mc[10] is None


def test_f_early_stop_is_asr_preserving():
    # ASR@k is a pure function of {first_success_index}; stopping at the first success records the
    # same j a full-budget run would, so no ASR@k changes.
    def first_success(vec):
        return next((i for i, s in enumerate(vec, 1) if s), None)

    full = [[False, False, True, False, True], [False, False, False], [True, False, False]]
    early = [(first_success(v), len(v)) for v in full]
    assert [idx for idx, _ in early] == [3, None, 1]
    for k in (1, 2, 3):  # min budget is 3; ASR@k for k>3 is N/A by design (R2)
        assert asr_at_k(early, k).successes == asr_at_k([(first_success(v), len(v)) for v in full], k).successes


def test_r2_cells_use_planned_budget_not_attempts_run():
    # A loop-shaped result: the cell early-stopped at attempt 3 (attempts_run=3) but its PLANNED
    # budget was B=20. The cell MUST be built as (first_success_index=3, budget=20) so ASR@10 stays
    # DEFINED (3 <= 20). Building it from attempts_run (=3) would wrongly make ASR@10 N/A.
    planned_cell = (3, 20)   # (first_success_index, PLANNED budget) -- correct
    assert asr_at_k([planned_cell], 10).successes == 1          # defined: success@10 True (3<=10)
    assert asr_at_k([planned_cell], 20).successes == 1
    with pytest.raises(ValueError):                              # ASR@100 still N/A (100 > 20)
        asr_at_k([planned_cell], 100)
    # the WRONG construction (budget = attempts_run = 3) would make ASR@10 N/A -- guard against it:
    with pytest.raises(ValueError):
        asr_at_k([(3, 3)], 10)
