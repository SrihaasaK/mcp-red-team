"""Render the M3 + held-out report HTML from committed, recorded aggregates (SPEC §8).

Zero live calls, and it never globs `runs/`. The M2/M3 dev + benign sections read committed
dev-split + benign aggregates. The held-out headline (M4, D101) reads the committed held-out
aggregate `review/M4/heldout-static/aggregate.json` (built offline by that dir's
`build_aggregate.py` from the frozen held-out split) and FILLS what used to be the stub
placeholders (<<X>>, <<Y>>, <<U>>, <<MODEL>>, <<N>>) -- `write_report` now asserts none of those
stub tokens survive. Held-out ASR is pooled across OpenAI Agents + LangGraph (A1/A8.2), reported per
model and never pooled across models (SPEC §8.3). Wilson CIs are recomputed from the committed counts
via `report.stats` (no second implementation). The Claude Agent SDK is a separate confirmatory slice,
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
# The M2 dev numbers are the CORRECTED re-run (review/M2/v2/, D73) under the apparatus
# fixes D55 (exfil = canary in a sink's arguments only), D56 (executed-only ASR), D61
# (canary only in source; no attack payload embeds it) and D66 (transformed-canary exfil).
# The original, now-superseded M2 dev report stays at review/M2/m2_report.json for history.
SOURCES = {
    "m2": "review/M2/v2/m2_report.json",
    "openai_benign": "tests/recordings/m3-openai-benign/summary.json",
    "langgraph": "tests/recordings/m3-langgraph/summary.json",
    "claude_agent_sdk": "tests/recordings/m3-claude-agent-sdk/summary.json",
    "prereg": "PREREG.md",
    # M4 held-out headline aggregate (SPEC §8.3 / PREREG A8), committed aggregates-only; built by
    # review/M4/heldout-static/build_aggregate.py from the gitignored raw run (D101). This is the
    # ONLY held-out source: the renderer reads this committed aggregate, never globs runs/.
    "heldout": "review/M4/heldout-static/aggregate.json",
}

# Spend ledger (USD), each entry recorded in DECISIONS.md; the cumulative total reconciles
# EXACTLY with the latest DECISIONS.md ledger entry (D101: $109.479119) -- asserted by
# tests/test_report.py.
LEDGER = [
    ("M0", "compatibility probe (Claude Agent SDK, D10)", 0.133251),
    ("M1", "settings probe + smoke + m1-matrix + m1-final (D21)", 0.306),
    ("M2 smoke", "smoke-gate before the dev run (D32, D36)", 0.092),
    ("M2 dev", "dev run 580 trials (D36; review/M2/m2_report.json, superseded by v2)", 10.2753),
    ("M3-T1", "LangGraph benign suite (D38)", 0.822),
    ("M3-T2", "Claude Agent SDK benign $0.232130 + native-path smokes $0.023278 (D40, D42)", 0.255408),
    ("M3-T4", "OpenAI Agents full benign suite, 40x2 (D51)", 0.965748),
    ("M2 dev v2", ("D55/D56/D61/D66 correction re-run: pilot $0.287134 + full 580-trial "
                   "$10.001218 (D62/D73; pilot cost corrected in D74; review/M2/v2/m2_report.json)"), 10.288352),
    ("M4-T1", "SPEC §10 mitigations + offline counterfactual (D75-D79): offline, no live calls", 0.0),
    ("M4-T2", ("dev mitigation utility eval: pilots $2.008720 (3 gate runs) + full 2160-trial "
               "$40.685482 (D80-D82; review/M4/dev-util/report.json)"), 42.694202),
    ("M4-T4 adaptive DEV", ("ASR@k apparatus + r1/r2 adaptive dev runs (health + 19 cells r2 $3.712016; "
                            "D83-D85; review/M4/adaptive/dev)"), 7.718286),
    ("Ladder qual", "held-out ladder benign qualification, Haiku 5.5 (qwen3:8b $0 local, D94)", 0.023938),
    ("M4 held-out: frontier static", ("HEADLINE run: Sonnet+Opus x {OpenAI-Agents,LangGraph} x 7 arms x "
                                      "held-out static x n=10 = 2240 trials (D96; review/M4/heldout-static)"), 33.134640),
    ("M4 held-out: ladder static", ("capability slice: Haiku 5.5 + qwen3:8b x OpenAI-Agents x 3 arms x "
                                     "n=10 = 540 trials (D97); qwen3 $0 local"), 0.251628),
    ("M4 held-out: adaptive", ("ladder adaptive, Haiku 3 seeds + qwen3:8b 1 seed (D99), B=100 = 48 cells "
                               "(D100; review/M4/adaptive/heldout); qwen3 $0 local"), 2.518366),
]
# Cumulative total that must match the latest DECISIONS.md ledger entry (D101); a constant
# for the reconciliation test.
LEDGER_TOTAL_USD = 109.479119


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


HELD_OUT_MODEL_LABELS = {"claude-sonnet-5-5": "claude-sonnet-5-5", "claude-opus-5-5": "claude-opus-5-5"}
HELD_OUT_ARMS = ["baseline", "pinning", "spotlight-delimiters", "spotlight-strip",
                 "taint-coarse", "taint-fine", "combined"]


def _agg_cell(r: dict) -> str:
    """Format an aggregate rate dict {pt,lo,hi,succ,n}; N/A when undefined (e.g. 0 exposed)."""
    if r["pt"] is None:
        return f"N/A ({r['succ']}/{r['n']})"
    return f"{stats.format_pct(r['pt'])} [{stats.format_pct(r['lo'])}, {stats.format_pct(r['hi'])}] ({r['succ']}/{r['n']})"


def _util_cell(u: dict) -> str:
    lo, hi = u["ci_pts"]
    v = u["verdict"].replace("_", " ")
    return f"{u['delta_pts']:+.1f} pts [{lo:+.1f}, {hi:+.1f}] &mdash; {v}"


def _delta_pts(x) -> str:
    return "N/A" if x is None else f"{x:+.1f}"


def _heldout_section(h: dict) -> str:
    """The FILLED held-out headline from the committed aggregate (SPEC §8.3 / PREREG A8). Per model,
    pooled across OpenAI Agents + LangGraph (A1/A8.2), NEVER pooled across models (§8.3)."""
    comp = h["composition_successes"]
    hj = h["answer_hijack"]
    rs = h["refusal_split"]
    n_cell = h["frozen"]["n_per_cell"]
    parts = [
        '<h2>Held-out headline (pre-registered, SPEC §8.3 / PREREG A8)</h2>',
        '<p class="note">Pooled across <b>OpenAI Agents + LangGraph</b> (the Claude Agent SDK slice is '
        'never pooled, A1), reported <b>per model and never pooled across models</b> (§8.3). '
        f'n={n_cell} trials per held-out cell. Raw and exposure-conditioned ASR shown together with '
        'Wilson 95% CIs (CLAUDE.md #5); utility &Delta; (points, 95% CI, 5-pt threshold §8.4) from the '
        'committed M4-T2 DEV eval. <b>Exfil ASR = 0 on every arm for both models</b> (0 canary reached a '
        'sink); the successes below are persistence + answer-hijack, which the information-flow '
        'mitigations do not target. Numbers are rendered from the committed aggregate '
        '<code>review/M4/heldout-static/aggregate.json</code> (built offline from the frozen run; the '
        'renderer never reads <code>runs/</code>). <b>Integrity:</b> its baseline reconstruction matches '
        f'the committed run summary on all {h["integrity"]["baseline_vs_summary_checks"]} checks '
        '(0 mismatch).</p>',
    ]
    for model in HELD_OUT_MODEL_LABELS:
        m = h["headline_models"][model]
        util = h["utility"][model]
        parts.append(f"<h4>{html.escape(model)} &mdash; pooled OpenAI Agents + LangGraph</h4>")
        parts.append('<table><thead><tr><th>arm</th><th>raw ASR [95% CI] (succ/n)</th><th>exposure</th>'
                     '<th>exposure-conditioned ASR [95% CI] (succ/exposed)</th>'
                     '<th>&Delta; raw vs baseline</th><th>utility &Delta; (DEV)</th></tr></thead><tbody>')
        eff = m["mitigation_effect"]
        for arm in HELD_OUT_ARMS:
            o = m["arms"][arm]["overall"]
            klass = ' class="overall"' if arm == "baseline" else ""
            dcell = "&mdash;" if arm == "baseline" else f"{_delta_pts(eff[arm]['delta_raw_pts'])} pts"
            expo = "N/A" if o["exposure_rate"] is None else stats.format_pct(o["exposure_rate"])
            parts.append(
                f'<tr{klass}><td>{arm}</td><td>{_agg_cell(o["raw"])}</td><td>{expo}</td>'
                f'<td>{_agg_cell(o["expcond"])}</td><td>{dcell}</td><td>{_util_cell(util[arm])}</td></tr>')
        parts.append("</tbody></table>")
        # baseline per-class (headline "overall and per attack class", §8.3)
        bc = m["arms"]["baseline"]["by_class"]
        parts.append('<p class="note">baseline by attack class (raw ASR): '
                     + "; ".join(f"class {c} {_agg_cell(bc[c]['raw'])}" for c in sorted(bc)) + "</p>")
        cf = m["coarse_vs_fine"]
        parts.append('<p class="note">coarse vs fine (raw ASR): taint-coarse '
                     f'{_agg_cell(cf["taint-coarse"])} vs taint-fine {_agg_cell(cf["taint-fine"])} '
                     '(A4(c); taint-fine additionally carries a meaningful DEV utility loss, see the '
                     'utility column).</p>')
    parts.append(
        '<p class="note"><b>Success composition (both models, all arms pooled for description only):</b> '
        f'{comp.get("persistence", 0)} persistence + {comp.get("answer_hijack", 0)} answer-hijack; '
        f'<b>0 exfil, 0 forbidden-action</b>. Answer-hijack = {hj["total"]} total, '
        f'{hj["excluding_refusal_quote"]} excluding the {hj["refusal_quote"]} D55 refusal-quote case(s) '
        f'(audited over all {hj["audited"]} hijack successes; report-only, never changes ASR).</p>')
    parts.append(
        '<p class="note"><b>Refusal split (A8.4):</b> '
        f'{stats.format_pct(rs["content_filter"])} provider content-filter '
        f'(finish_reason=content_filter), {stats.format_pct(rs["model_refusal"])} model-refusal, '
        f'{stats.format_pct(rs["attempted_benign"])} attempted/benign; {rs["filtered_successes"]} '
        'filtered successes. Measured ASR is a LOWER bound under Anthropic-API safeguards. '
        f'<span class="note">[{html.escape(rs["source"])}]</span></p>')
    # EXPLORATORY (clearly labelled, not a headline claim)
    ex = h["exploratory"]
    parts.append('<h4>Exploratory (NOT pre-registered)</h4>')
    parts.append(f'<p class="caveat">{html.escape(ex["label"])}</p>')
    ps = ex["per_scaffold_baseline"]
    psrows = []
    for sc in ("openai-agents", "langgraph"):
        for model in HELD_OUT_MODEL_LABELS:
            o = ps[sc][model]
            psrows.append(f"<tr><td>{sc}</td><td>{html.escape(model)}</td><td>{_agg_cell(o['raw'])}</td>"
                          f"<td>{_agg_cell(o['expcond'])}</td></tr>")
    parts.append('<p class="note"><b>Baseline per-scaffold split</b> (exploratory; shows the pooled '
                 'headline is not scaffold-uniform):</p>'
                 '<table><thead><tr><th>scaffold</th><th>model</th><th>baseline raw ASR [CI]</th>'
                 '<th>exposure-conditioned [CI]</th></tr></thead><tbody>' + "".join(psrows) + "</tbody></table>")
    # all-arms per scaffold (same Wilson method; all 7 arms + both models pooled per scaffold)
    psa = ex.get("per_scaffold_allarms", {})
    if psa:
        parows = "".join(
            f"<tr><td>{sc}</td><td>{_agg_cell(psa[sc]['raw'])}</td><td>{_agg_cell(psa[sc]['expcond'])}</td></tr>"
            for sc in ("openai-agents", "langgraph") if sc in psa)
        parts.append('<p class="note"><b>All-arms per-scaffold ASR</b> (exploratory; all 7 arms + both '
                     'frontier models pooled per scaffold &mdash; the framework gap persists beyond '
                     'baseline):</p>'
                     '<table><thead><tr><th>scaffold</th><th>raw ASR [CI] (succ/n)</th>'
                     '<th>exposure-conditioned [CI] (succ/exposed)</th></tr></thead><tbody>'
                     + parows + "</tbody></table>")
    lad = ex["ladder_capability"]
    larms = sorted({a for mv in lad["models"].values() for a in mv})
    lrows = []
    for model in sorted(lad["models"]):
        cells = "".join(f"<td>{_agg_cell(lad['models'][model][a]['raw'])}</td>" for a in larms)
        lrows.append(f"<tr><td>{html.escape(model)}</td>{cells}</tr>")
    parts.append(f'<p class="note"><b>Ladder static capability slice</b> (exploratory; {html.escape(lad["label"])}):</p>'
                 '<table><thead><tr><th>model</th>' + "".join(f"<th>{a}</th>" for a in larms)
                 + "</tr></thead><tbody>" + "".join(lrows) + "</tbody></table>")
    return "\n".join(parts)


def build_report(root: Path | None = None) -> str:
    root = root or repo_root()
    src = {k: (root / v) for k, v in SOURCES.items()}
    m2 = json.loads(src["m2"].read_text())
    oa = json.loads(src["openai_benign"].read_text())
    lg = json.loads(src["langgraph"].read_text())
    cas = json.loads(src["claude_agent_sdk"].read_text())
    prereg = src["prereg"].read_text()
    heldout = json.loads(src["heldout"].read_text())

    # --- M2 dev ASR table: per scaffold x model (M2 is openai-agents only) ---
    model_tables = []
    for model, block in m2["models"].items():
        model_tables.append(f"""
      <h4>openai-agents &times; {html.escape(model)}
        &nbsp;<span class="util">utility (control benign): {stats.format_pct(block['utility_benign_success'])},
        control FP rate {stats.format_pct(block['control_fp_rate'])}; disclosure {block['disclosure']},
        refusal-quote audit {block['refusal_quote_audit']}, exfil successes {block['exfil_successes']}</span></h4>
      <table><thead><tr><th>cell</th><th>raw ASR [95% CI] (succ/n)</th>
        <th>exposure</th><th>exposure-conditioned ASR [95% CI] (succ/exposed)</th></tr></thead>
      <tbody>{_asr_rows(block)}</tbody></table>""")

    # --- Benign utility, all three scaffolds (claude-agent-sdk kept separate, A1) ---
    openai_ctl_n = m2["pooled"]["control_trials"]
    openai_ctl_succ = round(m2["pooled"]["control_benign_success"] * openai_ctl_n)
    benign_rows = [
        ("openai-agents", "sonnet, full benign suite (40 tasks x 2 transports)", oa["era"],
         "stdio + http", oa["benign_success"], oa["ran_ok"], False),
        ("openai-agents", "M2 control arm -- 36/40 tasks x10 (reference, not the benign suite)",
         "2025-11-25 / 2026-07-28", "stdio + http", openai_ctl_succ, openai_ctl_n, False),
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

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP Red Team — M3 + held-out report</title>
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
<h1>MCP Red Team — M3 + held-out report</h1>
<p class="note">Rendered offline from committed, recorded aggregates (zero live model calls). The
M2/M3 dev + benign sections below read the dev-split and benign aggregates; the held-out headline
reads the committed held-out aggregate (<code>review/M4/heldout-static/aggregate.json</code>, built
offline from the frozen held-out split). The renderer never globs <code>runs/</code>.</p>

<h2>M2 attack success rate — DEV split (exploratory)</h2>
<p class="caveat"><b>DEV / exploratory only.</b> Per-cell numbers (scaffold &times; model &times; class)
are never a headline (SPEC §8.3). <b>D37 resolved (D55/D56/D61/D66).</b> The original M2 dev run
scored a canary reaching the <i>final answer</i> as exfil and embedded the literal canary in the
injected text, so most class-1 "successes" were answer quotes while refusing, not exfiltration to a
sink. These numbers are the corrected re-run: exfil now requires the canary in a <b>sink tool's
arguments</b> (D55, incl. base64/hex/reversed/split transforms, D66); ASR counts <b>executed</b> sink
calls only (D56); the attack payloads no longer contain the canary at all (<b>D61</b> &mdash; the canary
lives only in its mock source and is resolved to a pure oracle). A canary that appears only in the final
answer is reported separately below as <i>disclosure</i> and is <b>excluded from ASR</b>. Raw and
exposure-conditioned ASR are shown together with Wilson 95% CIs (SPEC §8.1).</p>
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
<p class="note"><b>Diagnostics (reported beside ASR, never part of it; D55/D61/D63):</b>
<i>disclosure</i> (canary in the final answer, not a sink) = {m2['pooled']['disclosure']} trials;
answer-hijack <i>refusal-quote</i> audit (a success whose answer looks like a refusal that quotes the
planted string &mdash; a heuristic, does not change ASR) = {m2['pooled']['refusal_quote_audit']};
<i>exfil</i> successes = {m2['pooled']['exfil_successes']}, of which source-read-before-sink backstop
held for {m2['pooled']['exfil_successes_with_source_read']}
(N/A when there are no exfil successes, as here).</p>

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

{_heldout_section(heldout)}
</body></html>
"""


def write_report(out_path: Path, root: Path | None = None) -> Path:
    out_path = Path(out_path)
    html_text = build_report(root)
    # M4 (D101): the held-out headline is now FILLED from the committed aggregate. The old stub
    # placeholders must NOT survive -- their presence means the held-out aggregate did not render.
    present = [p for p in HELD_OUT_PLACEHOLDERS if p in html_text]
    if present:
        raise RuntimeError(f"unfilled held-out stub placeholders present (headline did not render): {present}")
    out_path.write_text(html_text)
    return out_path
