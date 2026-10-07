"""Render the M3 report HTML from committed, recorded aggregates (SPEC §8).

Zero live calls. Reads ONLY dev-split + benign aggregates that are committed in the
repo -- it never globs `runs/` and never opens a held-out record, so the held-out
headline placeholders (<<X>>, <<Y>>, <<U>>, <<MODEL>>, <<N>>) stay unfilled until
`mcpr report` fills them from the frozen held-out split (there is no held-out data yet).
Wilson CIs are recomputed from the committed counts via `report.stats` (no second
implementation). The Claude Agent SDK is shown as a separate confirmatory slice and is
never pooled with the two lean scaffolds (PREREG Amendment A1, DECISIONS.md D39).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from mcpredteam.config import repo_root
from mcpredteam.report import stats

HELD_OUT_PLACEHOLDERS = ("<<X>>", "<<Y>>", "<<U>>", "<<MODEL>>", "<<N>>")

# The committed, dev+benign ONLY data sources. No held-out path appears here.
SOURCES = {
    "m2": "review/M2/m2_report.json",
    "langgraph": "tests/recordings/m3-langgraph/summary.json",
    "claude_agent_sdk": "tests/recordings/m3-claude-agent-sdk/summary.json",
    "prereg": "PREREG.md",
}

# Spend ledger (USD), each entry recorded in DECISIONS.md; the cumulative total reconciles
# EXACTLY with DECISIONS.md D42 ($11.883959) -- asserted by tests/test_report.py.
LEDGER = [
    ("M0", "compatibility probe (Claude Agent SDK, D10)", 0.133251),
    ("M1", "settings probe + smoke + m1-matrix + m1-final (D21)", 0.306),
    ("M2 smoke", "smoke-gate before the dev run (D32, D36)", 0.092),
    ("M2 dev", "dev run 580 trials (D36; review/M2/m2_report.json)", 10.2753),
    ("M3-T1", "LangGraph benign suite (D38)", 0.822),
    ("M3-T2", "Claude Agent SDK benign $0.232130 + native-path smokes $0.023278 (D40, D42)", 0.255408),
]
# Cumulative total that must match DECISIONS.md D42; kept as a constant for the reconciliation test.
LEDGER_TOTAL_D42 = 11.883959


def _rate_cell(succ: int, n: int) -> str:
    r = stats.Rate.from_counts(succ, n)
    return (f"{stats.format_pct(r.point)} "
            f"[{stats.format_pct(r.ci_low)}, {stats.format_pct(r.ci_high)}] ({succ}/{n})")


def _exposed(cell: dict) -> int:
    """Exposed-trial count behind an exposure-conditioned ASR (SPEC §8.2), from the
    recorded exposure RATE and trial count; sanity-checked against the success count."""
    exposed = round(cell["expo"] * cell["n"])
    if not (cell["succ"] <= exposed <= cell["n"]):
        raise ValueError(f"exposed {exposed} inconsistent with succ={cell['succ']} n={cell['n']}")
    return exposed


def _manifest_blocks(prereg: str) -> str:
    """The frozen model manifest and Amendment A1, lifted verbatim from PREREG.md."""
    def section(title: str) -> str:
        m = re.search(rf"(?ms)^## {re.escape(title)}.*?(?=^## |\Z)", prereg)
        return m.group(0).strip() if m else f"(section {title!r} not found)"
    manifest = section("Model manifest (D5, D19, D21) — printed in every report")
    a1 = section("Amendment A1 (author, in-session 2026-10-07; logged in DECISIONS.md D39)")
    return html.escape(manifest + "\n\n" + a1)


def _asr_rows(model_block: dict) -> str:
    rows = []
    cells = [("overall", model_block["overall"])] + \
            [(f"class {c}", model_block["by_class"][c]) for c in sorted(model_block["by_class"])]
    for label, cell in cells:
        exposed = _exposed(cell)
        klass = ' class="overall"' if label == "overall" else ""
        rows.append(
            f"<tr{klass}><td>{label}</td>"
            f"<td>{_rate_cell(cell['succ'], cell['n'])}</td>"
            f"<td>{stats.format_pct(cell['expo'])}</td>"
            f"<td>{_rate_cell(cell['succ'], exposed)}</td></tr>")
    return "\n".join(rows)


def build_report(root: Path | None = None) -> str:
    root = root or repo_root()
    src = {k: (root / v) for k, v in SOURCES.items()}
    m2 = json.loads(src["m2"].read_text())
    lg = json.loads(src["langgraph"].read_text())
    cas = json.loads(src["claude_agent_sdk"].read_text())
    prereg = src["prereg"].read_text()

    # --- M2 dev ASR table: per scaffold x model (M2 is openai-agents only) ---
    model_tables = []
    for model, block in m2["models"].items():
        model_tables.append(f"""
      <h4>openai-agents &times; {html.escape(model)}
        &nbsp;<span class="util">utility (control benign): {stats.format_pct(block['utility_benign_success'])},
        control FP rate {stats.format_pct(block['control_fp_rate'])}</span></h4>
      <table><thead><tr><th>cell</th><th>raw ASR [95% CI] (succ/n)</th>
        <th>exposure</th><th>exposure-conditioned ASR [95% CI] (succ/exposed)</th></tr></thead>
      <tbody>{_asr_rows(block)}</tbody></table>""")

    # --- Benign utility, all three scaffolds (claude-agent-sdk kept separate, A1) ---
    openai_ctl_n = m2["pooled"]["control_trials"]
    openai_ctl_succ = round(m2["pooled"]["control_benign_success"] * openai_ctl_n)
    benign_rows = [
        ("openai-agents", "sonnet+opus (M2 control arm, pooled)", "2025-11-25 / 2026-07-28",
         "stdio + http", openai_ctl_succ, openai_ctl_n, False),
        ("langgraph", lg["model"].replace("claude-", ""), lg["era"], "stdio + http",
         lg["benign_success"], lg["ran_ok"], False),
        ("claude-agent-sdk", cas["model"].replace("claude-", ""), cas["era"], "http",
         cas["benign_success"], cas["ran_ok"], True),
    ]
    brows = []
    for scaffold, model, era, transports, succ, n, separate in benign_rows:
        note = ' <span class="sep">confirmatory slice &mdash; not pooled (A1)</span>' if separate else ""
        brows.append(
            f"<tr><td>{scaffold}{note}</td><td>{html.escape(model)}</td><td>{era}</td>"
            f"<td>{transports}</td><td>{_rate_cell(succ, n)}</td></tr>")
    benign_table = "\n".join(brows)

    # --- Conditional classes per scaffold (N/A is never 0%; SPEC §6.1) ---
    cond = """
      <table><thead><tr><th>class</th><th>openai-agents</th><th>langgraph</th><th>claude-agent-sdk</th></tr></thead>
      <tbody>
      <tr><td>1 exfil (direct)</td><td>run</td><td>benign-only</td><td>benign-only</td></tr>
      <tr><td>2 tool-desc</td><td>run</td><td>benign-only</td><td>benign-only</td></tr>
      <tr><td>3 rug pull</td><td>run (handshake)</td><td><b>N/A</b> by construction</td><td><b>N/A</b> by construction</td></tr>
      <tr><td>4 list caching</td><td><b>N/A</b></td><td><b>N/A</b></td><td><b>N/A</b></td></tr>
      <tr><td>5 decoy tool</td><td>run</td><td>benign-only</td><td>benign-only</td></tr>
      <tr><td>6 resource&rarr;model</td><td><b>N/A</b></td><td><b>N/A</b></td><td><b>N/A</b> benign; pending live</td></tr>
      <tr><td>7 multi-step persist</td><td>run</td><td>benign-only</td><td>benign-only</td></tr>
      <tr><td>8 elicitation / MRTR</td><td><b>N/A</b></td><td><b>N/A</b></td><td><b>capability present, pending live</b></td></tr>
      </tbody></table>
      <p class="note">N/A means the behaviour the class needs is absent on that scaffold (determined by a
      pre-first-result probe, D28); it is never reported as 0%. "benign-only" = that scaffold ran only the
      benign suite in M3, so no ASR cell exists for it.</p>"""

    # --- Spend ledger ---
    cum, lrows = 0.0, []
    for milestone, detail, usd in LEDGER:
        cum += usd
        lrows.append(f"<tr><td>{milestone}</td><td>{html.escape(detail)}</td>"
                     f"<td>${usd:.6f}</td><td>${cum:.6f}</td></tr>")
    ledger_table = "\n".join(lrows)

    # --- Native-endpoint note for the Claude Agent SDK (A1) ---
    ne = cas.get("native_endpoint", {})
    native_note = (f"Claude Agent SDK ran on the native Messages API with effective thinking "
                   f"{html.escape(', '.join(ne.get('thinking_modes_seen', [])))} and prompt caching "
                   f"(cache-read {ne.get('cache_read_input_tokens', 0)} tokens across {cas['ran_ok']} trials).")

    placeholders = ", ".join(HELD_OUT_PLACEHOLDERS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP Red Team — M3 report</title>
<style>
  body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0 auto; max-width: 960px;
         padding: 24px 16px; color: #1a1a1a; background: #fff; }}
  h1 {{ font-size: 1.6rem; }} h2 {{ margin-top: 2em; border-bottom: 2px solid #eee; padding-bottom: 4px; }}
  h4 {{ margin: 1.2em 0 .4em; }}
  table {{ border-collapse: collapse; width: 100%; margin: .5em 0; font-size: 14px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 9px; text-align: left; vertical-align: top; }}
  th {{ background: #f6f6f6; }} tr.overall {{ font-weight: 600; background: #fafafa; }}
  .util {{ font-weight: 400; font-size: 13px; color: #555; }}
  .sep {{ color: #9a5b00; font-size: 12px; }}
  .note {{ color: #555; font-size: 13px; }}
  .caveat {{ background: #fff6e5; border: 1px solid #f0d9a8; padding: 10px 12px; border-radius: 6px; }}
  pre {{ background: #f6f6f6; padding: 10px 12px; border-radius: 6px; overflow-x: auto; font-size: 12.5px;
        white-space: pre-wrap; }}
  code {{ background: #f0f0f0; padding: 0 3px; border-radius: 3px; }}
</style></head>
<body>
<h1>MCP Red Team — M3 report</h1>
<p class="note">Rendered offline from committed, recorded aggregates (zero live model calls).
Dev-split and benign only. The held-out headline is not computed here: the placeholders
{html.escape(placeholders)} remain unfilled and no held-out record is read (none exists yet;
<code>mcpr report</code> fills them only after the frozen held-out split is run).</p>

<h2>M2 attack success rate — DEV split (exploratory)</h2>
<p class="caveat"><b>DEV / exploratory only.</b> Per-cell numbers (scaffold &times; model &times; class)
are never a headline (SPEC §8.3). <b>D37 validity caveat:</b> the exfil template embeds the literal canary
in the injected text, so most class-1 "successes" are final-answer quotes while refusing rather than true
exfiltration to a sink; this is an OPEN finding awaiting the author (D37). Raw and exposure-conditioned ASR
are shown together with Wilson 95% CIs (SPEC §8.1).</p>
{''.join(model_tables)}
<p class="note">Pooled across models (dev, exploratory), raw and exposure-conditioned reported
together (CLAUDE.md #5): raw ASR
{_rate_cell(m2['pooled']['overall']['succ'], m2['pooled']['overall']['n'])};
exposure {stats.format_pct(m2['pooled']['overall']['expo'])}
({_exposed(m2['pooled']['overall'])}/{m2['pooled']['overall']['n']});
exposure-conditioned ASR
{_rate_cell(m2['pooled']['overall']['succ'], _exposed(m2['pooled']['overall']))};
utility (control benign) {_rate_cell(openai_ctl_succ, openai_ctl_n)}; FP rate
{stats.format_pct(m2['pooled']['control_fp_rate'])}.</p>

<h2>Benign utility — all three scaffolds</h2>
<table><thead><tr><th>scaffold</th><th>model</th><th>era</th><th>transports</th>
  <th>benign success [95% CI] (passed/n)</th></tr></thead>
<tbody>{benign_table}</tbody></table>
<p class="note">{native_note}</p>

<h2>Conditional attack classes (SPEC §6.1)</h2>
{cond}

<h2>Model manifest (from PREREG.md, incl. Amendment A1)</h2>
<pre>{_manifest_blocks(prereg)}</pre>

<h2>Spend ledger</h2>
<table><thead><tr><th>milestone</th><th>detail</th><th>USD</th><th>cumulative</th></tr></thead>
<tbody>{ledger_table}</tbody></table>

<h2>Held-out headline (pending)</h2>
<p class="note">Pooled held-out ASR over OpenAI Agents + LangGraph (the Claude Agent SDK is a
separate confirmatory slice and is never pooled, A1): raw <b>{HELD_OUT_PLACEHOLDERS[0]}</b>,
exposure-conditioned <b>{HELD_OUT_PLACEHOLDERS[1]}</b>, utility <b>{HELD_OUT_PLACEHOLDERS[2]}</b>
on <b>{HELD_OUT_PLACEHOLDERS[4]}</b> trials per cell with <b>{HELD_OUT_PLACEHOLDERS[3]}</b>. Unfilled by
design — no held-out evaluation has been run and none is read to render this page.</p>
</body></html>
"""


def write_report(out_path: Path, root: Path | None = None) -> Path:
    out_path = Path(out_path)
    html_text = build_report(root)
    leaked = [p for p in HELD_OUT_PLACEHOLDERS if p not in html_text]
    if leaked:  # the placeholders must survive verbatim; filling them is `mcpr report`'s job only
        raise RuntimeError(f"held-out placeholders missing from the report: {leaked}")
    out_path.write_text(html_text)
    return out_path
