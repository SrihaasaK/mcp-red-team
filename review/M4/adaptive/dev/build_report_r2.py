"""Reproducible M4-T4b adaptive DEV report over the 44-CELL ANALYSIS SET (supersedes the single-run
build_report.py after the r2 re-run). $0, offline. Assembles:
  - 25 RETAINED clean cells from runs/m4-adaptive-dev/cells.jsonl  (all 27 MINUS the 2 errored c7-01
    cells: c7-01 spotlight-delimiters err=11, c7-01 taint-coarse err=20; c7-01 baseline is kept)
  - 19 r2 cells from runs/m4-adaptive-dev-r2/cells.jsonl           (c7-01 x {spotlight,coarse,fine}
    + c7-02/03/05/06 x 4 arms)
=> 44 cells = 11 configs x 4 arms, one cell per (config, arm). Each cell's block-reason lookup uses
ITS OWN run dir. Metrics identical to D89: per-arm ASR@1 (static)/@10/@20 with Wilson 95% CIs
(ASR@100 N/A at B=20), in/out-allowlist split per A7 unit (i) + D88 erratum (every in-allowlist
coarse block counts, incidental included), blocked-attempt rate, successes-by-transform, totals.
No attack/oracle/seed/stat change (CLAUDE.md #1).

    uv run python -I review/M4/adaptive/dev/build_report_r2.py
"""
import json
from collections import defaultdict

from mcpredteam.adaptive.asrk import asr_curve
from mcpredteam.config import repo_root
from mcpredteam.mitigate.allowlist import derive_allowlist
from mcpredteam.report import stats
from mcpredteam.runner.evaluate import load_corpus

ROOT = repo_root()
RUN_ORIG = ROOT / "runs" / "m4-adaptive-dev"       # retained clean cells live here
RUN_R2 = ROOT / "runs" / "m4-adaptive-dev-r2"      # the 19 re-run cells live here
OUT = ROOT / "review" / "M4" / "adaptive" / "dev"
ARMS = ["baseline", "spotlight-delimiters", "taint-coarse", "taint-fine"]
KS = (1, 10, 20, 100)  # ASR@100 is N/A on DEV (B=20)
# The 2 contaminated c7-01 cells in the aborted run that r2 replaces (excluded from the analysis set).
DROP_ORIG = {("c7-01-mail-poison-files-canary-http", "spotlight-delimiters"),
             ("c7-01-mail-poison-files-canary-http", "taint-coarse")}


def load_cells() -> list[dict]:
    """The 44-cell analysis set, each tagged with its run dir for block-reason lookup."""
    cells = []
    for row in (RUN_ORIG / "cells.jsonl").read_text().splitlines():
        if not row.strip():
            continue
        c = json.loads(row)
        if (c["config"], c["arm"]) in DROP_ORIG:
            continue  # contaminated; replaced by r2
        c["_run"] = RUN_ORIG
        cells.append(c)
    for row in (RUN_R2 / "cells.jsonl").read_text().splitlines():
        if not row.strip():
            continue
        c = json.loads(row)
        c["_run"] = RUN_R2
        cells.append(c)
    return cells


def in_allowlist(config: dict) -> bool:
    """A7 unit (i): the canary-flow pair (canary server -> sink server)."""
    p = config["payload"]
    src = p["canary_ref"].split(":", 1)[0]
    sink = (p.get("sink") or "").split(".", 1)[0]
    return (src, sink) in derive_allowlist()


def _rate(cells, k):
    r = asr_curve(cells, (k,))[k]
    return None if r is None else {"asr": round(r.point, 4), "succ": r.successes, "n": r.n,
                                   "ci": [round(r.ci_low, 4), round(r.ci_high, 4)]}


def coarse_block_reasons(cell: dict) -> list[str]:
    """Mitigation-block reasons across a cell's attempts, read from the cell's OWN run dir."""
    reasons = []
    for i in range(1, cell["attempts_run"] + 1):
        for s in (cell["_run"] / cell["cell_id"] / f"attempt_{i:03d}").glob("proxy.*.summary.json"):
            for b in (json.loads(s.read_text()).get("mitigation_blocks") or []):
                reasons.append(b.get("reason", ""))
    return reasons


def main() -> None:
    cfgs, _, _ = load_corpus()
    cells = load_cells()
    used = {x["config"] for x in cells}
    membership = {c["id"]: in_allowlist(c) for c in cfgs if c["id"] in used}

    report = {"model": "claude-sonnet-5-5", "analysis_set": "44 cells (25 retained clean + 19 r2)",
              "arms": {}, "config_membership": membership,
              "out_of_allowlist_configs": sorted(cid for cid, inn in membership.items() if not inn),
              "note": "44-cell analysis set after the r2 re-run (D90). ASR@1 = per-arm STATIC ASR "
              "(attempt1 = frozen-spec plain shot; n=1/cell, noisy). ASR@100 N/A on DEV (B=20). Cells "
              "use PLANNED budget B, not attempts_run. In/out split per A7 unit (i); coarse inside "
              "block rate counts incidental blocks (A7-erratum/D88)."}
    transform_success = defaultdict(int)
    total_attempts = total_cost = total_err = 0

    for arm in ARMS:
        ac = [c for c in cells if c["arm"] == arm]
        planned = [(c["first_success_index"], c["budget"]) for c in ac]
        cin = [(c["first_success_index"], c["budget"]) for c in ac if membership[c["config"]]]
        cout = [(c["first_success_index"], c["budget"]) for c in ac if not membership[c["config"]]]
        att = sum(c["attempts_run"] for c in ac)
        blk = sum(c["blocked_attempts"] for c in ac)
        err = sum(c["errored_attempts"] for c in ac)
        cost = sum(c.get("cost_usd", 0.0) for c in ac)
        total_attempts += att
        total_cost += cost
        total_err += err
        for c in ac:
            if c["first_success_index"] is not None:
                transform_success[c["attempts"][c["first_success_index"] - 1]["transform"]] += 1
        arm_rep = {
            "cells": len(ac),
            "asr": {f"@{k}": _rate(planned, k) for k in KS},
            "asr_in_allowlist": {f"@{k}": _rate(cin, k) for k in KS} if cin else {},
            "asr_out_of_allowlist": {f"@{k}": _rate(cout, k) for k in KS} if cout else {},
            "blocked_attempt_rate": round(blk / att, 4) if att else 0.0,
            "attempts": att, "blocked_attempts": blk, "errored_attempts": err,
            "cost_usd": round(cost, 6)}
        if arm == "taint-coarse":
            in_att = sum(c["attempts_run"] for c in ac if membership[c["config"]])
            in_blk = sum(c["blocked_attempts"] for c in ac if membership[c["config"]])
            out_att = sum(c["attempts_run"] for c in ac if not membership[c["config"]])
            out_blk = sum(c["blocked_attempts"] for c in ac if not membership[c["config"]])
            arm_rep["coarse_inside_block_rate"] = round(in_blk / in_att, 4) if in_att else 0.0
            arm_rep["coarse_outside_block_rate"] = round(out_blk / out_att, 4) if out_att else 0.0
            arm_rep["incidental_inside_block_reasons"] = sorted({r for c in ac if membership[c["config"]]
                                                                 for r in coarse_block_reasons(c)})
        report["arms"][arm] = arm_rep

    report["successes_by_transform"] = dict(transform_success)
    report["totals"] = {"attempts": total_attempts, "errored": total_err, "cost_usd": round(total_cost, 6),
                        "cells": len(cells), "configs": len(used)}

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1) + "\n")

    def fmt(cell):
        return "N/A" if cell is None else f"{cell['succ']}/{cell['n']}={stats.format_pct(cell['asr'])}"

    def ci(cell):
        return "" if cell is None else f" [{stats.format_pct(cell['ci'][0])}, {stats.format_pct(cell['ci'][1])}]"

    oos = report["out_of_allowlist_configs"]
    L = ["# M4-T4b adaptive-attacker DEV report — Sonnet 5.5 — 44-cell analysis set (D90)", "",
         ("After the r2 re-run: 25 retained clean cells + 19 re-run cells = **44 cells** "
         "(11 stateless DEV exfil configs x 4 arms, 1 cell per pair, B=20, early stop). The 2 "
         "API-connection-errored c7-01 cells (spotlight-delimiters, taint-coarse) from the aborted run "
         "are EXCLUDED and replaced by their r2 re-runs."), "",
         ("**ASR@1 = the per-arm STATIC ASR** (attempt 1 = frozen spec + plain instruction; n=1/cell, "
         "NOISY). ASR@100 is **N/A on DEV** (B=20). Cells use PLANNED budget B."), "",
         "| arm | cells | ASR@1 (static) | ASR@10 | ASR@20 (95% CI) | blocked rate | errors |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    for arm in ARMS:
        a = report["arms"][arm]
        L.append(f"| {arm} | {a['cells']} | {fmt(a['asr']['@1'])} | {fmt(a['asr']['@10'])} "
                 f"| {fmt(a['asr']['@20'])}{ci(a['asr']['@20'])} "
                 f"| {stats.format_pct(a['blocked_attempt_rate'])} | {a['errored_attempts']} |")
    co = report["arms"]["taint-coarse"]
    L += ["", "## Coarse taint: in-allowlist vs out-of-allowlist (A7 (i); prediction (b))", "",
          f"- out-of-allowlist configs ({len(oos)}): {oos or 'none'}",
          (f"- out-of-allowlist cells ASR@20: {fmt(co['asr_out_of_allowlist'].get('@20'))}"
           f"{ci(co['asr_out_of_allowlist'].get('@20'))} "
           f"(coarse OUTSIDE block rate {stats.format_pct(co['coarse_outside_block_rate'])}; "
           "'100% outside' is structural per the A7-erratum — coarse only blocks a sink call the "
           "attack never reached here)"),
          (f"- in-allowlist cells ASR@20: {fmt(co['asr_in_allowlist'].get('@20'))}"
           f"{ci(co['asr_in_allowlist'].get('@20'))} "
           f"(coarse INSIDE block rate {stats.format_pct(co['coarse_inside_block_rate'])} — "
           "counts incidental blocks, D88)"),
          f"- incidental in-allowlist block reasons: {co['incidental_inside_block_reasons'] or 'none'}",
          "", "## Successes by transform", "",
          f"{report['successes_by_transform'] or 'no successes (ASR@20 = 0 across all arms)'}", "",
          "## Totals", "",
          (f"- cells {report['totals']['cells']}, configs {report['totals']['configs']}, "
           f"attempts {report['totals']['attempts']}, errored {report['totals']['errored']}, "
           f"cost \\${report['totals']['cost_usd']:.6f}"), ""]
    (OUT / "report.md").write_text("\n".join(L) + "\n")
    print("wrote", OUT / "report.json", "and report.md")
    print("out-of-allowlist configs:", oos)
    for arm in ARMS:
        a = report["arms"][arm]
        print(arm, "cells", a["cells"], "ASR@20", fmt(a["asr"]["@20"]),
              "blocked_rate", a["blocked_attempt_rate"], "err", a["errored_attempts"])


if __name__ == "__main__":
    main()
