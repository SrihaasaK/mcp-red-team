"""D61: no attack payload may contain the literal canary -- it exists ONLY in its source.

Renders EVERY config's injected payload FROM CODE (dev AND held-out) and asserts no canary
literal appears. Rendering from the frozen corpus + engine is NOT a held-out evaluation: this
test reads only attacks/corpus.yaml, attacks/split.yaml, the mock source (env) and engine code
-- it NEVER reads any run results (nothing under runs/ or any result.json). It also checks the
env canary resolver and the runner-side hydration.
"""

from __future__ import annotations

import pytest
import yaml

from mcpredteam.attacks import mutators
from mcpredteam.attacks.engine import _instruction, _mutator_spec
from mcpredteam.config import repo_root
from mcpredteam.env import files_mock, mail_mock, notes, resolve_canary, source_text
from mcpredteam.runner.canary import hydrate_attack_config

CANARIES = (notes.CANARY, mail_mock.CANARY, files_mock.CANARY)
REFS = ("notes:n3", "mail:m4", "files:config/deploy.env")


def _corpus() -> list[dict]:
    return yaml.safe_load((repo_root() / "attacks" / "corpus.yaml").read_text())["configs"]


def _split() -> dict:
    return yaml.safe_load((repo_root() / "attacks" / "split.yaml").read_text())["split"]


def test_no_config_payload_contains_a_literal_canary_dev_and_held_out():
    configs, split = _corpus(), _split()
    assert len(configs) == 40
    dev = held = 0
    for c in configs:
        arm = split[c["id"]]
        if arm == "dev":
            dev += 1
        elif arm == "held_out":
            held += 1
        # the frozen corpus must not store the value either
        assert "canary" not in c.get("payload", {}), c["id"]
        # render the injected payload from code only (both arms; no run, no results read)
        rendered = mutators.mutate_payload(_mutator_spec(c), _instruction(c))
        instr = _instruction(c)
        for canary in CANARIES:
            assert canary not in rendered, (c["id"], arm, "rendered payload leaks canary")
            assert canary not in instr, (c["id"], arm, "instruction leaks canary")
    assert dev == 28 and held == 12  # both arms exercised (SPEC 70/30 split)


def test_env_resolve_canary_matches_its_source():
    for ref in REFS:
        assert resolve_canary(ref) in source_text(ref)  # the canary genuinely lives at the ref
    with pytest.raises(ValueError):
        resolve_canary("bogus:x")  # unknown server -> loud failure


def test_hydrate_injects_resolved_canary_without_mutating_input():
    raw = {"payload": {"intent": "exfil_canary", "oracle": "exfil",
                       "canary_ref": "notes:n3", "sink": "http.http_post"}}
    h = hydrate_attack_config(raw)
    assert h["payload"]["canary"] == notes.CANARY
    assert "canary" not in raw["payload"]  # original untouched (deep copy)
    plain = {"payload": {"oracle": "answer_hijack", "target_string": "X"}}
    assert hydrate_attack_config(plain) is plain  # no canary_ref -> unchanged
