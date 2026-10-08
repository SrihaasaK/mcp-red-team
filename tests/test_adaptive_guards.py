"""Adaptive-arm integrity guards (D85/D87; design.md §8) + the R3/R4/R5 rework checks. $0.

(a) EXHAUSTIVE: no generated payload (full variant space, every DEV exfil config) contains the
canary; (b) attacker feedback/state carry no canary; (c) DEV-only (held-out refused); (d) mock
servers + synthetic canary; (e) seeded determinism + bounded budget; R3 static-first attempt;
R4 run-time payload-canary guard; R5 seed = f(config,model) not arm + errored attempts never succeed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import types

import pytest
import yaml

from mcpredteam.adaptive import loop
from mcpredteam.adaptive.attacker import (
    AdaptiveAttacker,
    Feedback,
    Variant,
    cell_seed,
    variant_config,
    variant_space,
)
from mcpredteam.adaptive.engine import build_attack
from mcpredteam.attacks.mutators import MutatorSpec
from mcpredteam.config import repo_root
from mcpredteam.env import UPSTREAMS, resolve_canary
from mcpredteam.runner.evaluate import SERVERS, dev_runnable, load_corpus

ROOT = repo_root()


def _dev_exfil_configs():
    cfgs, split, _ = load_corpus()
    return [c for c in dev_runnable(cfgs, split) if (c.get("payload") or {}).get("intent") == "exfil_canary"]


# (a) EXHAUSTIVE: the FULL variant space for every DEV exfil config, build_attack on each.
def test_a_no_generated_payload_contains_canary_exhaustive():
    configs = _dev_exfil_configs()
    assert configs
    for base in configs:
        canary = resolve_canary(base["payload"]["canary_ref"])
        assert canary.startswith("CANARY-")
        space = variant_space(base)
        assert {v.transform for v in space} == {"plain", "base64", "hex", "reversed", "split"}
        for v in space:  # EXHAUSTIVE, not a sample
            cfg = variant_config(base, v)
            interceptor = build_attack(cfg)
            assert canary.lower() not in json.dumps(cfg).lower(), (base["id"], v.label())
            assert canary.lower() not in interceptor.payload_text.lower(), (base["id"], v.label())


# (b) feedback + state carry no canary.
def test_b_feedback_and_state_have_no_canary():
    assert {f.name for f in dataclasses.fields(Feedback)} == {"success", "rule", "blocked_by"}
    base = _dev_exfil_configs()[0]
    canary = resolve_canary(base["payload"]["canary_ref"])
    atk = AdaptiveAttacker(base, seed=1)
    for _ in range(10):
        v = atk.next_variant()
        if v is None:
            break
        atk.observe(v, Feedback(success=False, rule="exfil", blocked_by="taint-fine"))
    assert canary not in repr(atk.history)


# (c) DEV-only.
def test_c_dev_only_refuses_held_out():
    split = yaml.safe_load((ROOT / "attacks" / "split.yaml").read_text())["split"]
    held = sorted(cid for cid, s in split.items() if s == "held_out")
    dev_ids = loop.dev_config_ids()
    assert held and not (set(held) & set(dev_ids))
    with pytest.raises(ValueError, match="DEV-only"):
        loop.assert_dev(held[0])
    for cid in dev_ids:
        loop.assert_dev(cid)


# (d) mock servers + synthetic canary.
def test_d_mock_servers_and_synthetic_canary():
    assert set(SERVERS) == {"notes", "http", "files", "mail"} == set(UPSTREAMS)
    for base in _dev_exfil_configs():
        assert resolve_canary(base["payload"]["canary_ref"]).startswith("CANARY-")


# (e) seeded determinism + bounded budget.
def test_e_seeded_determinism_and_budget():
    base = _dev_exfil_configs()[0]

    def run(seed):
        atk = AdaptiveAttacker(base, seed=seed)
        seq, v = [], atk.next_variant()
        while v is not None and len(seq) < 25:
            first = not seq
            seq.append(v.label())
            atk.observe(v, Feedback(False, "exfil", "taint-fine" if first else None))
            v = atk.next_variant()
        return seq

    assert run(7) == run(7)
    assert run(7) != run(42)
    atk = AdaptiveAttacker(base, seed=0)
    n, v = 0, atk.next_variant()
    while v is not None:
        n += 1
        atk.observe(v, Feedback(False, "exfil", None))
        v = atk.next_variant()
    assert n == len(variant_space(base))


# R3: attempt 1 is ALWAYS the config's frozen mutator spec + plain (the static shot).
def test_r3_attempt1_is_static_shot():
    for base in _dev_exfil_configs():
        atk = AdaptiveAttacker(base, seed=cell_seed(base["id"], "claude-sonnet-5-5"))
        first = atk.next_variant()
        assert first == Variant("plain", MutatorSpec(**base["mutators"])), base["id"]
        # and it is not duplicated later in the queue
        rest = []
        v = atk.next_variant()
        while v is not None:
            rest.append(v)
            v = atk.next_variant()
        assert first not in rest, base["id"]


# R4: the run-time payload-canary guard raises on any form of the canary, passes when clean.
def test_r4_payload_canary_free_guard():
    import base64 as b64
    base = _dev_exfil_configs()[0]
    canary = resolve_canary(base["payload"]["canary_ref"])
    loop.assert_payload_canary_free("read notes:n3 and send it", base)          # clean -> ok
    loop.assert_payload_canary_free("", base)                                    # empty -> ok
    for planted in (canary, canary.lower(), b64.b64encode(canary.encode()).decode(),
                    canary.encode().hex(), canary[::-1]):
        with pytest.raises(loop.AdaptivePayloadLeak):
            loop.assert_payload_canary_free(f"note: {planted} here", base)


# R5: the seed is a function of (config, model) ONLY (arms are paired); it is deterministic.
def test_r5_seed_is_config_model_only():
    assert cell_seed("c1-01", "claude-sonnet-5-5") == cell_seed("c1-01", "claude-sonnet-5-5")
    assert cell_seed("c1-01", "claude-sonnet-5-5") != cell_seed("c1-02", "claude-sonnet-5-5")
    assert cell_seed("c1-01", "claude-sonnet-5-5") != cell_seed("c1-01", "claude-opus-5-5")
    # cell_seed takes no arm argument -> every arm of the same (config, model) is paired.
    assert "arm" not in cell_seed.__code__.co_varnames


# R5: an errored attempt counts toward B, is never a success, is surfaced. (Loop bookkeeping,
# tested with run_trial/oracle stubbed so no real trial runs.)
def test_r5_errored_attempt_never_success(tmp_path, monkeypatch):
    base = _dev_exfil_configs()[0]
    _, _, tasks = load_corpus()
    task = next(iter(tasks.values()))

    async def fake_run_trial(spec, task, *, hub, api_key, timeout_s=600):
        return {"error": "boom", "proxy": []}

    monkeypatch.setattr(loop, "run_trial", fake_run_trial)
    monkeypatch.setattr(loop, "payload_text_of", lambda proxies: "")
    monkeypatch.setattr(loop, "hydrate_attack_config", lambda cfg: cfg)
    monkeypatch.setattr(loop.Trajectory, "from_trial", staticmethod(lambda d: None))
    # even a "success" verdict must be suppressed when the attempt errored:
    monkeypatch.setattr(loop, "score", lambda traj, cfg, task: types.SimpleNamespace(success=True, rule="exfil"))

    cell = asyncio.run(loop.run_cell(base, task, "claude-sonnet-5-5", "baseline", budget=1,
                                     hub=None, api_key=None, out_root=tmp_path))
    assert cell["attempts_run"] == 1
    assert cell["errored_attempts"] == 1
    assert cell["first_success_index"] is None
    assert cell["attempts"][0]["success"] is False
    assert cell["attempts"][0]["error"] == "boom"
