"""Reproducible M4-T1 offline counterfactual (D53): apply each SPEC §10 mitigation's DECISION
logic to RECORDED benign + attack trajectories (NO live calls, NO re-run) and write the
coarse-vs-fine input tables to review/M4/counterfactual.{json,md}.

    uv run python -I review/M4/build_counterfactual.py

BENIGN groups (utility-cost PROXY, explicitly NOT measured utility; the agent did not react):
m2-dev-v2 controls, m3-openai-benign, m3-langgraph-benign, m3-claude-agent-sdk-benign.
ATTACK group (COUNTERFACTUAL, explicitly NOT ASR): m2-dev-v2 attack trials.
"""
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from mcpredteam.config import repo_root
from mcpredteam.mitigate import pins as pinmod
from mcpredteam.mitigate.counterfactual import KINDS, evaluate

ROOT = repo_root()
OUT_JSON = ROOT / "review" / "M4" / "counterfactual.json"
OUT_MD = ROOT / "review" / "M4" / "counterfactual.md"

BENIGN = {
    "m2-dev-v2 controls": ("m2-dev-v2", "ctl__"),
    "m3-openai-benign": ("m3-openai-benign", "benign__"),
    "m3-langgraph-benign": ("m3-langgraph-benign", "benign__"),
    "m3-claude-agent-sdk-benign": ("m3-claude-agent-sdk-benign", "benign__"),
}
ATTACK = ("m2-dev-v2", "atk__")


def trial_dirs(run: str, prefix: str) -> list[Path]:
    d = ROOT / "runs" / run
    return sorted(p for p in d.glob(f"{prefix}*") if p.is_dir()) if d.is_dir() else []


def aggregate(dirs: list[Path], pins) -> dict:
    agg = {k: defaultdict(int) for k in KINDS}
    for td in dirs:
        for k in KINDS:
            r = evaluate(td, k, pins)
            for f in ("sink_calls", "blocked_calls", "results", "altered_results"):
                agg[k][f] += r[f]
    return {k: dict(v) for k, v in agg.items()}


def main() -> None:
    pins = asyncio.run(pinmod.clean_pins())
    out = {"benign": {}, "attack": {}, "trial_counts": {}}
    for label, (run, prefix) in BENIGN.items():
        dirs = trial_dirs(run, prefix)
        out["trial_counts"][label] = len(dirs)
        out["benign"][label] = aggregate(dirs, pins)
    atk_dirs = trial_dirs(*ATTACK)
    out["trial_counts"]["m2-dev-v2 attack"] = len(atk_dirs)
    out["attack"]["m2-dev-v2 attack"] = aggregate(atk_dirs, pins)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1) + "\n")

    def rate(n, d):
        return f"{(n / d * 100):.1f}%" if d else "n/a"

    lines = ["# M4-T1 offline counterfactual (coarse-vs-fine input)", "",
             "Each SPEC §10 mitigation's DECISION logic applied to RECORDED trajectories; NO live",
             "calls, NO re-run. Benign numbers are a utility-cost **proxy** (NOT measured utility; the",
             "agent did not react to a block). Attack numbers are a **counterfactual** (NOT ASR).",
             "Taint-fine under-blocks TRANSFORMED secrets by design (PREREG (c)); here the v2 attacks",
             "contain no exfil, so both taint variants mainly exercise the benign false-block surface.",
             ""]
    lines.append("## Benign (utility-cost proxy; blocked sink calls / altered results)")
    lines.append("")
    lines.append("| group | trials | mitigation | sink calls | blocked | block rate | results | altered |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for label in BENIGN:
        n = out["trial_counts"][label]
        for k in KINDS:
            a = out["benign"][label][k]
            lines.append(f"| {label} | {n} | {k} | {a['sink_calls']} | {a['blocked_calls']} | "
                         f"{rate(a['blocked_calls'], a['sink_calls'])} | {a['results']} | {a['altered_results']} |")
    lines.append("")
    lines.append("## Attack (counterfactual; attack sink calls that WOULD be blocked)")
    lines.append("")
    lines.append("| group | trials | mitigation | sink calls | would-block | block rate | altered |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    na = out["trial_counts"]["m2-dev-v2 attack"]
    for k in KINDS:
        a = out["attack"]["m2-dev-v2 attack"][k]
        lines.append(f"| m2-dev-v2 attack | {na} | {k} | {a['sink_calls']} | {a['blocked_calls']} | "
                     f"{rate(a['blocked_calls'], a['sink_calls'])} | {a['altered_results']} |")
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n")
    print("wrote", OUT_JSON, "and", OUT_MD)
    print("benign m2-dev-v2 controls coarse blocked:",
          out["benign"]["m2-dev-v2 controls"]["taint-coarse"]["blocked_calls"])
    print("attack coarse/fine would-block:",
          out["attack"]["m2-dev-v2 attack"]["taint-coarse"]["blocked_calls"],
          out["attack"]["m2-dev-v2 attack"]["taint-fine"]["blocked_calls"])


if __name__ == "__main__":
    main()
