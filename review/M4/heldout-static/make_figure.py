"""Headline figure for the held-out result, built from the committed aggregate.json. $0, offline.

Two panels, saved as SVG + PNG (review/M4/heldout-static/headline_figure.{svg,png}):
  (a) Data exfiltration (held-out): 0 of 978 trials moved the planted secret (ASR 0.0%).
  (b) EXPLORATORY: baseline answer-hijack/persistence ASR by agent framework — OpenAI Agents SDK vs
      LangGraph — pooled across the two frontier models WITHIN each scaffold, with Wilson 95% CIs.
      No cross-scaffold pooled number is shown (per SPEC §8.3; this is the exploratory framework split).

Panel (b) numbers come from aggregate.json (exploratory.per_scaffold_baseline, summed across models per
scaffold; Wilson CIs via report.stats). The exfil SUCCESS count (0) is read from aggregate.json; the 978
exfil-trial denominator is the D96-verified count (not a field in the hash-pinned aggregate; reproducible
from the published recordings via rescore_from_release.py).

    uv run --with matplotlib python -I review/M4/heldout-static/make_figure.py
"""
import json

import matplotlib.pyplot as plt

from mcpredteam.config import repo_root
from mcpredteam.report import stats

plt.switch_backend("Agg")  # headless render (no display, e.g. CI)

OUT = repo_root() / "review" / "M4" / "heldout-static"
EXFIL_TRIALS = 978  # D96-verified held-out exfil-trial count (reproducible from the released recordings)
INK = "#1a1a1a"
BAR = "#2b6cb0"      # accessible blue
ZERO = "#9aa5b1"     # muted grey for the 0% bar
CI = "#1a1a1a"


def main() -> None:
    h = json.loads((OUT / "aggregate.json").read_text())
    ps = h["exploratory"]["per_scaffold_baseline"]
    exfil_succ = h["composition_successes"].get("exfil", 0)

    # panel (b): per-scaffold baseline, pooled across models within a scaffold
    frames = []
    for sc, label in (("openai-agents", "OpenAI Agents SDK"), ("langgraph", "LangGraph")):
        succ = sum(ps[sc][m]["raw"]["succ"] for m in ps[sc])
        n = sum(ps[sc][m]["raw"]["n"] for m in ps[sc])
        r = stats.Rate.from_counts(succ, n)
        frames.append((label, 100 * r.point, 100 * (r.point - r.ci_low), 100 * (r.ci_high - r.point),
                       succ, n))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": "#cbd2d9",
                         "svg.fonttype": "none"})
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(10, 4.2), gridspec_kw={"width_ratios": [1, 1.25]})
    fig.suptitle("MCP Red Team — held-out results", fontsize=14, fontweight="bold", color=INK, x=0.5, y=0.99)

    # --- panel (a): exfiltration floored ---
    axa.barh([0], [100], color="#eef2f6", height=0.5, zorder=1)
    axa.barh([0], [max(0.4, 100 * exfil_succ / EXFIL_TRIALS)], color=ZERO, height=0.5, zorder=2)
    axa.set_xlim(0, 100)
    axa.set_ylim(-1, 1)
    axa.set_yticks([])
    axa.set_xticks([0, 25, 50, 75, 100])
    axa.set_xlabel("attack success rate (%)")
    axa.set_title("Data exfiltration (held-out)", fontweight="bold", color=INK, loc="left")
    axa.text(50, 0.72, f"{exfil_succ} of {EXFIL_TRIALS} trials moved the planted secret",
             ha="center", va="center", fontsize=11, color=INK)
    axa.text(1.5, 0, "0.0%", ha="left", va="center", fontsize=20, fontweight="bold", color=INK)
    for s in ("top", "right", "left"):
        axa.spines[s].set_visible(False)

    # --- panel (b): framework split, exploratory ---
    ys = range(len(frames))[::-1]
    labels = [f[0] for f in frames]
    pts = [f[1] for f in frames]
    lo = [f[2] for f in frames]
    hi = [f[3] for f in frames]
    colors = [BAR, ZERO]
    axb.barh(list(ys), pts, color=colors, height=0.5, zorder=2)
    axb.errorbar(pts, list(ys), xerr=[lo, hi], fmt="none", ecolor=CI, elinewidth=1.4, capsize=5, zorder=3)
    axb.set_yticks(list(ys))
    axb.set_yticklabels(labels)
    axb.set_xlim(0, 12)
    axb.set_xlabel("baseline ASR — answer-hijack / persistence (%)")
    axb.set_title("Framework effect  [EXPLORATORY, not pre-registered]", fontweight="bold",
                  color=INK, loc="left", fontsize=11)
    for y, f in zip(ys, frames):
        axb.text(f[1] + f[3] + 0.5, y, f"{f[1]:.1f}%  ({f[4]}/{f[5]})", va="center", fontsize=10, color=INK)
    for s in ("top", "right"):
        axb.spines[s].set_visible(False)

    fig.text(0.5, 0.015, "Pre-registered held-out eval · ~2,800 static trials · OpenAI Agents SDK + "
             "LangGraph · Sonnet 5.5 + Opus 5.5. Exfil ASR is a lower bound (rule-based adaptive "
             "attacker); results include provider content-filtering.", ha="center", fontsize=7.5,
             color="#52606d")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    (OUT / "headline_figure.svg").unlink(missing_ok=True)
    fig.savefig(OUT / "headline_figure.svg", format="svg", bbox_inches="tight")
    fig.savefig(OUT / "headline_figure.png", format="png", dpi=200, bbox_inches="tight")
    print("wrote", OUT / "headline_figure.svg", "and .png")
    print("panel b:", [(f[0], round(f[1], 2), f[4], f[5]) for f in frames])


if __name__ == "__main__":
    main()
