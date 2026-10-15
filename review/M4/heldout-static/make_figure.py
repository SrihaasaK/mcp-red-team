"""Headline figure for the held-out result, built from the committed aggregate.json. $0, offline.

Two panels, saved as SVG + PNG (review/M4/heldout-static/headline_figure.{svg,png}):
  (a) Data exfiltration (held-out): 0 of 978 trials moved the planted secret (ASR 0.0%).
  (b) EXPLORATORY: baseline answer-hijack/persistence ASR PER MODEL PER FRAMEWORK — OpenAI Agents SDK
      and LangGraph, each x {Sonnet 5.5, Opus 5.5}, with Wilson 95% CIs. No pooling (per SPEC §8.3 the
      pre-registered results are per model).

Panel (b) numbers come from aggregate.json (exploratory.per_scaffold_baseline[scaffold][model], baseline
arm; Wilson CIs via report.stats). The exfil SUCCESS count (0) is read from aggregate.json; the 978
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
OA = "#2b6cb0"       # OpenAI Agents SDK (accessible blue)
LG = "#9aa5b1"       # LangGraph (muted grey)
CI = "#1a1a1a"
MODEL_LABEL = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}
FRAMEWORKS = (("openai-agents", "OpenAI Agents SDK", OA), ("langgraph", "LangGraph", LG))


def main() -> None:
    h = json.loads((OUT / "aggregate.json").read_text())
    ps = h["exploratory"]["per_scaffold_baseline"]
    exfil_succ = h["composition_successes"].get("exfil", 0)

    # panel (b): baseline ASR per MODEL per FRAMEWORK (no pooling)
    frames = []  # (label, point, lo_err, hi_err, succ, n, color)
    for sc, fname, color in FRAMEWORKS:
        for m in ("claude-sonnet-5-5", "claude-opus-5-5"):
            c = ps[sc][m]["raw"]
            r = stats.Rate.from_counts(c["succ"], c["n"])
            frames.append((f"{fname}  ·  {MODEL_LABEL[m]}", 100 * r.point,
                           100 * (r.point - r.ci_low), 100 * (r.ci_high - r.point), c["succ"], c["n"], color))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": "#cbd2d9",
                         "svg.fonttype": "none"})
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(11.5, 4.8), gridspec_kw={"width_ratios": [0.8, 1.35]})
    fig.suptitle("MCP Red Team — held-out results", fontsize=14, fontweight="bold", color=INK, x=0.5, y=0.99)

    # --- panel (a): exfiltration floored ---
    axa.barh([0], [100], color="#eef2f6", height=0.5, zorder=1)
    axa.barh([0], [max(0.4, 100 * exfil_succ / EXFIL_TRIALS)], color=LG, height=0.5, zorder=2)
    axa.set_xlim(0, 100)
    axa.set_ylim(-1, 1)
    axa.set_yticks([])
    axa.set_xticks([0, 25, 50, 75, 100])
    axa.set_xlabel("attack success rate (%)")
    axa.set_title("Data exfiltration (held-out)", fontweight="bold", color=INK, loc="left")
    axa.text(50, 0.72, f"{exfil_succ} of {EXFIL_TRIALS} trials moved the planted secret",
             ha="center", va="center", fontsize=10.5, color=INK)
    axa.text(1.5, 0, "0.0%", ha="left", va="center", fontsize=20, fontweight="bold", color=INK)
    for s in ("top", "right", "left"):
        axa.spines[s].set_visible(False)

    # --- panel (b): per-model, per-framework baseline (exploratory) ---
    ys = list(range(len(frames)))[::-1]
    axb.barh(ys, [f[1] for f in frames], color=[f[6] for f in frames], height=0.6, zorder=2)
    axb.errorbar([f[1] for f in frames], ys, xerr=[[f[2] for f in frames], [f[3] for f in frames]],
                 fmt="none", ecolor=CI, elinewidth=1.4, capsize=5, zorder=3)
    axb.set_yticks(ys)
    axb.set_yticklabels([f[0] for f in frames], fontsize=10)
    axb.set_xlim(0, 15)
    axb.set_xlabel("baseline ASR — answer-hijack / persistence (%)")
    axb.set_title("Framework × model  [EXPLORATORY, not pre-registered]", fontweight="bold",
                  color=INK, loc="left", fontsize=11)
    for y, f in zip(ys, frames):
        axb.text(f[1] + f[3] + 0.6, y, f"{f[1]:.1f}%  ({f[4]}/{f[5]})", va="center", fontsize=9.5, color=INK)
    for s in ("top", "right"):
        axb.spines[s].set_visible(False)

    fig.text(0.5, 0.015, "Per model, per framework; the pre-registered results are per model. "
             "Exfil ASR is a lower bound (rule-based adaptive attacker); results include provider "
             "content-filtering.", ha="center", fontsize=7.5, color="#52606d")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    (OUT / "headline_figure.svg").unlink(missing_ok=True)
    fig.savefig(OUT / "headline_figure.svg", format="svg", bbox_inches="tight")
    fig.savefig(OUT / "headline_figure.png", format="png", dpi=200, bbox_inches="tight")
    print("wrote", OUT / "headline_figure.svg", "and .png")
    print("panel b:", [(f[0], round(f[1], 2), f[4], f[5]) for f in frames])


if __name__ == "__main__":
    main()
