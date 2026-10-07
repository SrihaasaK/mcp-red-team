"""Exact-dollar tests for Anthropic prompt-cache pricing in runner.trial.llm_usage
(M3-T3 step 1c). Cache reads are 0.1x the base input price; cache writes are 1.25x for
the 5-minute TTL and 2x for the 1-hour TTL. Fails loud if the 1h share exceeds the total.
The OpenAI-compatible scaffolds carry no cache fields, so their cost is unchanged.
"""

import json
from pathlib import Path

import pytest

from mcpredteam.runner.trial import llm_usage


def _write_llm(trial_dir: Path, usage: dict) -> None:
    rec = {"seq": 0, "status": 200, "latency_s": 0.1,
           "response": {"usage": usage, "stop_reason": "end_turn"}}
    (trial_dir / "llm.jsonl").write_text(json.dumps(rec) + "\n")


def test_cache_pricing_includes_1h_writes(tmp_path):
    # sonnet: $2/Mtok in, $10/Mtok out. One call with a 1-hour cache write present.
    _write_llm(tmp_path, {"prompt_tokens": 1000, "completion_tokens": 500,
                          "cache_creation_input_tokens": 400,               # write total
                          "cache_creation_ephemeral_1h_input_tokens": 100,  # 1h share -> 2x; 300 5m -> 1.25x
                          "cache_read_input_tokens": 2000})
    u = llm_usage(tmp_path, "claude-sonnet-5-5")
    # 1000*2 + 500*10 + 300*2*1.25 + 100*2*2 + 2000*2*0.1 = 2000+5000+750+400+400 = 8550 (/1e6)
    assert u["cost_usd"] == 0.00855
    assert u["cache_creation_input_tokens"] == 400
    assert u["cache_creation_ephemeral_1h_input_tokens"] == 100


def test_cache_pricing_all_5m_when_no_1h_breakdown(tmp_path):
    # Older recordings carry no 1h field: the whole write is 5-minute at 1.25x.
    _write_llm(tmp_path, {"prompt_tokens": 0, "completion_tokens": 0,
                          "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0})
    u = llm_usage(tmp_path, "claude-sonnet-5-5")
    assert u["cost_usd"] == round(1000 * 2.0 * 1.25 / 1e6, 6)  # 0.0025


def test_cache_1h_exceeding_total_fails_loud(tmp_path):
    _write_llm(tmp_path, {"cache_creation_input_tokens": 50,
                          "cache_creation_ephemeral_1h_input_tokens": 100})
    with pytest.raises(ValueError, match="exceed the write total"):
        llm_usage(tmp_path, "claude-sonnet-5-5")


def test_openai_compat_usage_unaffected(tmp_path):
    # OpenAI-compatible records carry no cache fields -> only input/output priced (no regression).
    _write_llm(tmp_path, {"prompt_tokens": 1000, "completion_tokens": 200})
    u = llm_usage(tmp_path, "claude-sonnet-5-5")
    assert u["cost_usd"] == round((1000 * 2.0 + 200 * 10.0) / 1e6, 6)  # 0.004
