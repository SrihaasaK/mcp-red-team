"""Tests for mcpredteam.report.stats (SPEC section 8)."""

import pytest

from mcpredteam.report.stats import (
    asr,
    exposure_conditioned_asr,
    format_rate,
    meaningful_drop,
    pooled_asr,
    proportion,
    raw_asr,
    utility_delta,
    utility_verdict,
    wilson_ci,
)

# Reference values: Wilson (1927) closed form, z=1.96, matching R binom.confint(
# method="wilson") / statsmodels proportion_confint(method="wilson") to 4 d.p.
WILSON_CASES = [
    (0, 10, 0.0, 0.2775),
    (5, 10, 0.2366, 0.7634),
    (10, 10, 0.7225, 1.0),
    (10, 100, 0.0552, 0.1744),
]


@pytest.mark.parametrize(("s", "n", "lo", "hi"), WILSON_CASES)
def test_wilson_known_values(s, n, lo, hi):
    got_lo, got_hi = wilson_ci(s, n)
    assert got_lo == pytest.approx(lo, abs=5e-4)
    assert got_hi == pytest.approx(hi, abs=5e-4)


@pytest.mark.parametrize("n", [1, 2, 5, 10, 37, 100])
def test_wilson_bounds_ordered_and_contain_point(n):
    for s in range(n + 1):
        lo, hi = wilson_ci(s, n)
        assert 0.0 <= lo <= s / n <= hi <= 1.0


def test_wilson_n_zero_and_invalid():
    assert wilson_ci(0, 0) == (0.0, 0.0)
    assert proportion(0, 0) == 0.0
    with pytest.raises(ValueError):
        wilson_ci(3, 2)
    with pytest.raises(ValueError):
        wilson_ci(-1, 2)


def test_raw_asr_rate():
    r = raw_asr(3, 10)
    assert (r.successes, r.n, r.point) == (3, 10, 0.3)
    assert r.ci_low < 0.3 < r.ci_high


def test_exposure_conditioned_uses_exposed_denominator():
    both = asr(successes=3, trials=10, exposed=6)
    assert both.raw.point == pytest.approx(0.3)
    assert both.exposure_conditioned.point == pytest.approx(0.5)
    assert both.exposure_conditioned.n == 6
    assert both.exposed == 6 and both.trials == 10
    assert both.exposure_conditioned == exposure_conditioned_asr(3, 6)
    # No exposure: undefined, reported as n=0.
    assert asr(0, 10, 0).exposure_conditioned.n == 0
    with pytest.raises(ValueError):
        asr(5, 10, 3)  # cannot succeed without exposure


def test_pooled_asr_sums_counts():
    r = pooled_asr([(1, 10), (2, 10), (0, 5)])
    assert (r.successes, r.n) == (3, 25)
    assert r.point == pytest.approx(0.12)
    assert (r.ci_low, r.ci_high) == wilson_ci(3, 25)
    assert pooled_asr([]).n == 0


def test_utility_delta_sign_and_units():
    delta, (lo, hi) = utility_delta(90, 100, 80, 100)  # B worse than A
    assert delta == pytest.approx(-10.0)
    assert lo < delta < hi
    delta2, _ = utility_delta(80, 100, 90, 100)
    assert delta2 == pytest.approx(10.0)
    assert utility_delta(7, 10, 7, 10)[0] == 0.0


def test_utility_delta_newcombe_reference():
    # Newcombe (1998) Stat Med 17:873, method 10: 56/70 vs 48/80 -> 0.2 [0.0524, 0.3339].
    # Here A=48/80 baseline, B=56/70 treatment.
    delta, (lo, hi) = utility_delta(48, 80, 56, 70)
    assert delta == pytest.approx(20.0)
    assert lo == pytest.approx(5.24, abs=0.05)
    assert hi == pytest.approx(33.39, abs=0.05)


def test_utility_delta_zero_n():
    assert utility_delta(0, 0, 5, 10) == (0.0, (-100.0, 100.0))


def test_ci_excludes_five_point_drop():
    # Large n, no change: CI is tight around 0 and excludes -5.
    _, ci = utility_delta(900, 1000, 900, 1000)
    assert ci[0] > -5.0
    assert not meaningful_drop(ci)
    assert utility_verdict(ci) == "no_meaningful_loss"


def test_ci_does_not_exclude_five_point_drop():
    # n=10 per arm, no observed change: CI is wide, cannot rule out a 5-point drop.
    delta, ci = utility_delta(9, 10, 9, 10)
    assert delta == 0.0 and ci[0] < -5.0
    assert meaningful_drop(ci)
    assert utility_verdict(ci) == "inconclusive"


def test_verdict_meaningful_loss_and_threshold_boundary():
    assert utility_verdict((-30.0, -8.0)) == "meaningful_loss"
    assert utility_verdict((-5.0, 3.0)) == "inconclusive"  # drop of exactly 5 not excluded
    assert utility_verdict((-4.9, 3.0)) == "no_meaningful_loss"
    assert meaningful_drop((-4.0, 1.0), threshold_pts=5.0) is False
    assert meaningful_drop((-4.0, 1.0), threshold_pts=4.0) is True
    assert meaningful_drop((-4.0, 1.0), threshold_pts=3.0) is True


def test_format_rate():
    assert format_rate(raw_asr(10, 100)) == "10.0% [5.5%, 17.4%] (10/100)"
