"""The M3 report reconciles with the committed records (M3-T3 rework items 2-3)."""

from mcpredteam.config import repo_root
from mcpredteam.report import render
from mcpredteam.report.render import HELD_OUT_PLACEHOLDERS, LEDGER, LEDGER_TOTAL_USD, build_report


def test_ledger_total_reconciles_with_decisions():
    total = round(sum(v for _, _, v in LEDGER), 6)
    assert total == LEDGER_TOTAL_USD, (total, LEDGER_TOTAL_USD)
    # the latest DECISIONS.md ledger entry (D51) states the same cumulative, to the cent-fraction.
    dec = (repo_root() / "DECISIONS.md").read_text()
    assert f"{LEDGER_TOTAL_USD:.6f}" in dec
    # and the rendered report shows that exact cumulative.
    assert f"${LEDGER_TOTAL_USD:.6f}" in build_report()


def test_report_pooled_line_has_exposure_conditioned():
    html = build_report()
    assert "exposure-conditioned ASR" in html  # reported with raw (CLAUDE.md #5)
    assert "(16/206)" in html   # pooled exposure-conditioned = 16 successes / 206 exposed
    assert "(206/220)" in html  # pooled exposure count


def test_report_placeholders_unfilled():
    html = build_report()
    for p in HELD_OUT_PLACEHOLDERS:
        assert p in html


def test_report_sources_are_dev_and_benign_only():
    # The report's data sources are a fixed allowlist of dev + benign aggregates; no held-out path.
    assert all("held" not in v.lower() for v in render.SOURCES.values())
