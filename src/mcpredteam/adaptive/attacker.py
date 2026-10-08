"""The rule-based adaptive attacker (D67; design.md §1-2).

Deterministic + seeded: given a seed and the (deterministic) per-attempt oracle feedback, the
attempt sequence is reproducible. The attacker searches (transform x MutatorSpec) variants in a
seed-derived permutation, with ONE feedback rule: after a TAINT block it defers the remaining
`plain` variants (plaintext is what taint catches) and prefers transformed payloads next.

The attacker sees only `Feedback(success, rule, blocked_by)` — never the canary or any trajectory
text (design.md §2; guards (a)/(b)). `variant_config` builds the attack_config dict the proxy
injects; it carries only `canary_ref` (never the canary value, A2).
"""

from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass

from mcpredteam.adaptive.engine import TRANSFORM_NAMES
from mcpredteam.attacks import mutators
from mcpredteam.attacks.mutators import MutatorSpec

# The curated DEV MutatorSpec subset (design.md §1; pinned exactly in A7). 5 framings (incl none)
# x 3 placements x 4 encodings (incl none), language=en -> 60 specs.
DEV_SPECS: tuple[MutatorSpec, ...] = tuple(mutators.combinations(
    encodings=(None, "base64", "zero_width", "homoglyph"),
    framings=(None, "system_tag", "tool_call", "urgency", "roleplay"),
    placements=("suffix", "prefix", "mid"),
    languages=(None,),
))


@dataclass(frozen=True)
class Feedback:
    """Everything the attacker learns from one attempt. Oracle-level only; no canary/trajectory."""

    success: bool
    rule: str | None
    blocked_by: str | None  # the mitigation kind that blocked a call this attempt, else None


@dataclass(frozen=True)
class Variant:
    transform: str  # "plain" | base64 | hex | reversed | split
    spec: MutatorSpec

    def label(self) -> str:
        return f"{self.transform}|{self.spec.label()}"


def _applicable_transforms(base_config: dict) -> tuple[str, ...]:
    """Transforms apply only to exfil_canary (there is no secret to transform otherwise)."""
    if (base_config.get("payload") or {}).get("intent") == "exfil_canary":
        return TRANSFORM_NAMES
    return ("plain",)


def variant_space(base_config: dict, specs: tuple[MutatorSpec, ...] = DEV_SPECS) -> list[Variant]:
    """Every (transform, spec) variant for a config, in a stable (unshuffled) order."""
    return [Variant(t, s) for t in _applicable_transforms(base_config) for s in specs]


def variant_config(base_config: dict, variant: Variant) -> dict:
    """The attack_config dict for one variant: the base config with this variant's mutator spec,
    and (for a non-plain transform) the `adaptive_transform` marker the proxy dispatcher reads.
    Carries only `canary_ref` — never the canary value (A2; guard (a))."""
    cfg = copy.deepcopy(base_config)
    cfg["mutators"] = asdict(variant.spec)
    cfg.pop("adaptive_transform", None)
    if variant.transform != "plain":
        cfg["adaptive_transform"] = variant.transform
    return cfg


class AdaptiveAttacker:
    """Seeded, feedback-aware variant scheduler for one cell. Pure of any canary knowledge."""

    def __init__(self, base_config: dict, seed: int, specs: tuple[MutatorSpec, ...] = DEV_SPECS) -> None:
        self.base_config = base_config
        variants = variant_space(base_config, specs)
        random.Random(seed).shuffle(variants)  # seed-derived permutation (deterministic)
        self._queue: list[Variant] = variants
        self.history: list[tuple[Variant, Feedback]] = []

    def next_variant(self) -> Variant | None:
        """The next variant to try, or None when the space is exhausted."""
        return self._queue.pop(0) if self._queue else None

    def observe(self, variant: Variant, feedback: Feedback) -> None:
        """Record the attempt's outcome and apply the single adaptive rule: a TAINT block defers
        the remaining plain variants to the end (prefer transformed payloads next)."""
        self.history.append((variant, feedback))
        if feedback.blocked_by and "taint" in feedback.blocked_by:
            plain = [v for v in self._queue if v.transform == "plain"]
            rest = [v for v in self._queue if v.transform != "plain"]
            self._queue = rest + plain
