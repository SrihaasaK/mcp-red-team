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
             "calls, NO re-run. Both the benign AND the attack columns here are a **utility-cost proxy**",
             "(blocks of the agent's OWN, task-required sink calls), **NOT a security catch**: every one",
             "of the 48 executed sink calls in the v2 ATTACK trials is task-required (checked against each",
             "task's `exercises`), and v2 has 0 executed exfil and 0 executed forbidden-action, so there",
             "is no malicious sink call here to catch. The security **catch** rate is measured",
             "SYNTHETICALLY in `synthetic_catch.md` (R2), not from these recordings. 'altered' means a",
             "spotlight-STRIP pattern changed a result's content; delimiters wrap EVERY result but remove",
             "nothing, so delimiters 'altered' is 0 by definition.",
             ""]
    lines.append("## Benign (utility-cost proxy; blocked sink calls / strip-altered results)")
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
    lines.append("## Under attack (utility cost, NOT catch): blocks of TASK-REQUIRED sink calls")
    lines.append("")
    lines.append("All 48 executed sink calls in the v2 attack trials are task-required; a block here is")
    lines.append("a utility cost the agent would pay WHILE under attack, not a thwarted exfiltration.")
    lines.append("")
    lines.append("| group | trials | mitigation | task sink calls | blocked | block rate | strip-altered |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    na = out["trial_counts"]["m2-dev-v2 attack"]
    for k in KINDS:
        a = out["attack"]["m2-dev-v2 attack"][k]
        lines.append(f"| m2-dev-v2 attack | {na} | {k} | {a['sink_calls']} | {a['blocked_calls']} | "
                     f"{rate(a['blocked_calls'], a['sink_calls'])} | {a['altered_results']} |")
    lines += ["",
              "Pinning's 6 blocks are task-required http calls (http_get ×1, http_post ×5) whose",
              "descriptions a class-2 attack poisoned: pinning cannot tell the task's own use of the",
              "poisoned tool from a malicious one, so under attack it is a utility cost (its genuine",
              "value — refusing to let the agent trust a changed description — is real but not an exfil",
              "block). Taint-fine's blocks are the agent re-sending read content to an allowlisted sink",
              "(benign content flow), NOT exfil. spotlight-strip altered 93 attack results (it removed",
              "injected instructions from poisoned tool results) with 0 benign alterations — that IS its",
              "defensive action, measured as a content change, not a sink block.", ""]
    OUT_MD.write_text("\n".join(lines) + "\n")
    print("wrote", OUT_JSON, "and", OUT_MD)
    print("benign m2-dev-v2 controls coarse blocked:",
          out["benign"]["m2-dev-v2 controls"]["taint-coarse"]["blocked_calls"])
    print("attack coarse/fine would-block:",
          out["attack"]["m2-dev-v2 attack"]["taint-coarse"]["blocked_calls"],
          out["attack"]["m2-dev-v2 attack"]["taint-fine"]["blocked_calls"])


if __name__ == "__main__":
    main()
