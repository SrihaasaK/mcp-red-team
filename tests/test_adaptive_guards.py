"""Adaptive-arm integrity guards (D85; design.md §8), guards (a)-(e). All $0, no trials.

(a) no generated payload contains the literal canary; (b) the attacker's feedback/state never
contain the canary; (c) DEV-only (held-out configs refused, no held-out read); (d) mock servers
only + synthetic canaries; (e) seeded determinism + bounded budget.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
import yaml

from mcpredteam.adaptive import loop
from mcpredteam.adaptive.attacker import AdaptiveAttacker, Feedback, variant_config, variant_space
from mcpredteam.adaptive.engine import build_attack
from mcpredteam.config import repo_root
from mcpredteam.env import UPSTREAMS, resolve_canary
from mcpredteam.runner.evaluate import SERVERS, dev_runnable, load_corpus

ROOT = repo_root()


def _dev_exfil_configs():
    cfgs, split, _ = load_corpus()
    return [c for c in dev_runnable(cfgs, split) if (c.get("payload") or {}).get("intent") == "exfil_canary"]


# (a) No generated payload (config dict OR built interceptor's payload_text) contains the canary.
def test_a_no_generated_payload_contains_canary():
    configs = _dev_exfil_configs()
    assert configs
    for base in configs:
        canary = resolve_canary(base["payload"]["canary_ref"])
        assert canary.startswith("CANARY-")
        space = variant_space(base)
        transforms = {v.transform for v in space}
        assert transforms == {"plain", "base64", "hex", "reversed", "split"}
        # sample: every transform x the first 3 spec choices (fast; covers each transform)
        first_specs = {s.spec for s in space[:3]}
        sample = [v for v in space if v.spec in first_specs]
        for v in sample:
            cfg = variant_config(base, v)
            assert canary not in json.dumps(cfg), (base["id"], v.label())
            assert canary.lower() not in json.dumps(cfg).lower()
            interceptor = build_attack(cfg)
            assert canary not in interceptor.payload_text, (base["id"], v.label())
            assert canary.lower() not in interceptor.payload_text.lower()


# (b) The attacker's feedback + state never carry the canary.
def test_b_feedback_and_state_have_no_canary():
    field_names = {f.name for f in dataclasses.fields(Feedback)}
    assert field_names == {"success", "rule", "blocked_by"}  # no trajectory/args/canary field
    base = _dev_exfil_configs()[0]
    canary = resolve_canary(base["payload"]["canary_ref"])
    atk = AdaptiveAttacker(base, seed=1)
    for _ in range(10):
        v = atk.next_variant()
        if v is None:
            break
        atk.observe(v, Feedback(success=False, rule="exfil", blocked_by="taint-fine"))
    assert canary not in repr(atk.history)
    assert all(fb.rule in (None, "exfil", "forbidden_action", "answer_hijack", "persistence")
               for _, fb in atk.history)


# (c) DEV-only: held-out configs are refused and never read.
def test_c_dev_only_refuses_held_out():
    split = yaml.safe_load((ROOT / "attacks" / "split.yaml").read_text())["split"]
    held = sorted(cid for cid, s in split.items() if s == "held_out")
    dev_ids = loop.dev_config_ids()
    assert held and not (set(held) & set(dev_ids))  # no held-out id is DEV-runnable
    with pytest.raises(ValueError, match="DEV-only"):
        loop.assert_dev(held[0])
    for cid in dev_ids:
        loop.assert_dev(cid)


# (d) Mock servers only; synthetic canaries.
def test_d_mock_servers_and_synthetic_canary():
    assert set(SERVERS) == {"notes", "http", "files", "mail"} == set(UPSTREAMS)
    for base in _dev_exfil_configs():
        canary = resolve_canary(base["payload"]["canary_ref"])
        assert canary.startswith("CANARY-")  # synthetic planted value, not real data


# (e) Seeded determinism + bounded budget.
def test_e_seeded_determinism_and_budget():
    base = _dev_exfil_configs()[0]

    def run(seed: int) -> list[str]:
        atk = AdaptiveAttacker(base, seed=seed)
        seq, v = [], atk.next_variant()
        while v is not None and len(seq) < 25:
            first = not seq
            seq.append(v.label())
            # deterministic scripted feedback: a taint block on attempt 1, then plain failures
            atk.observe(v, Feedback(False, "exfil", "taint-fine" if first else None))
            v = atk.next_variant()
        return seq

    assert run(7) == run(7)          # same seed -> identical sequence
    assert run(7) != run(42)         # different seed -> different exploration order
    # the space is finite and the scheduler terminates
    atk = AdaptiveAttacker(base, seed=0)
    n, v = 0, atk.next_variant()
    while v is not None:
        n += 1
        atk.observe(v, Feedback(False, "exfil", None))
        v = atk.next_variant()
    assert n == len(variant_space(base))
