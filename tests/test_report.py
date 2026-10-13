"""The M3 + held-out report reconciles with the committed records (M3-T3 items 2-3; M4 D101).

M4 (D101) replaced the three M3-era stubs with STRICTLY STRONGER checks: the held-out headline
placeholders are now FILLED from the committed held-out aggregate (not asserted absent-of-data), the
renderer's SOURCES now INCLUDE the held-out aggregate, and the spend ledger reconciles across the
render LEDGER, DECISIONS.md, the rendered report, AND the committed aggregate.
"""
import json

from mcpredteam.config import repo_root
from mcpredteam.report import render
from mcpredteam.report.render import HELD_OUT_PLACEHOLDERS, LEDGER, LEDGER_TOTAL_USD, build_report


def _aggregate() -> dict:
    return json.loads((repo_root() / render.SOURCES["heldout"]).read_text())


def test_ledger_total_reconciles_with_decisions_and_aggregate():
    total = round(sum(v for _, _, v in LEDGER), 6)
    assert total == LEDGER_TOTAL_USD, (total, LEDGER_TOTAL_USD)
    # the latest DECISIONS.md ledger entry (D101) states the same cumulative, to the cent-fraction.
    dec = (repo_root() / "DECISIONS.md").read_text()
    assert f"{LEDGER_TOTAL_USD:.6f}" in dec
    # the rendered report shows that exact cumulative ...
    assert f"${LEDGER_TOTAL_USD:.6f}" in build_report()
    # ... and the committed held-out aggregate agrees (ledger matches the spend log across sources).
    assert _aggregate()["cost_usd"]["cumulative_ledger_total"] == LEDGER_TOTAL_USD


def test_report_pooled_line_has_exposure_conditioned():
    # Golden tracks the CORRECTED v2 dev report (D55/D56/D61/D66; review/M2/v2/, D73):
    # pooled 3/220 raw, exposure 205/220, exposure-conditioned 3/205.
    html = build_report()
    assert "exposure-conditioned ASR" in html  # reported with raw (CLAUDE.md #5)
    assert "(3/205)" in html    # pooled exposure-conditioned = 3 successes / 205 exposed
    assert "(205/220)" in html  # pooled exposure count


def test_report_heldout_placeholders_filled_from_aggregate():
    # M4 (D101), strictly stronger than the retired 'placeholders unfilled' test: the stub tokens must
    # be GONE (the headline rendered), and the held-out numbers must come from the committed aggregate.
    html = build_report()
    for p in HELD_OUT_PLACEHOLDERS:
        assert p not in html, f"unfilled held-out stub {p} still present (headline did not render)"
    agg = _aggregate()
    assert agg["headline_models"], "aggregate has no held-out models"
    for model, m in agg["headline_models"].items():
        assert model in html
        base = m["arms"]["baseline"]["overall"]["raw"]
        assert f"({base['succ']}/{base['n']})" in html  # the model's baseline succ/n renders verbatim


def test_report_sources_include_heldout_aggregate():
    # M4 (D101), replaces 'sources are dev+benign only': the held-out aggregate is a source AND exists.
    assert "heldout" in render.SOURCES
    assert "heldout" in render.SOURCES["heldout"].lower()
    assert (repo_root() / render.SOURCES["heldout"]).exists()


def test_report_sources_never_point_under_runs():
    # Preserves the retired test's real intent (#12 / CLAUDE.md #6): the renderer reads only committed
    # aggregates, NEVER the gitignored raw runs. Every SOURCES value is a committed file outside runs/.
    for v in render.SOURCES.values():
        assert not v.startswith("runs/") and "/runs/" not in v, v


def test_heldout_aggregate_integrity_and_zero_exfil():
    # Corroborating M4 invariants (CLAUDE.md #1): the committed aggregate validated against the run
    # summary (0 mismatch), and exfil/forbidden ASR is 0 in the success composition.
    agg = _aggregate()
    assert agg["integrity"]["ok"] and not agg["integrity"]["mismatches"]
    assert agg["composition_successes"].get("exfil", 0) == 0
    assert agg["composition_successes"].get("forbidden_action", 0) == 0
