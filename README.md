# MCP Red Team

MCP Red Team is a dynamic red-team harness for agents that speak the Model Context Protocol. A
transparent proxy sits between an agent and its MCP servers, injects attacks into the traffic the
agent sees, measures attack success rate (ASR) with a deterministic oracle, and ships an
information-flow mitigation layer whose cost to the agent's useful work is measured alongside its
benefit. It is defensive security research: attacks run only against mock servers in this repo, and
canaries are synthetic.

## Threat model

- **Attacker:** controls exactly one upstream MCP server, or poisons data inside one
  otherwise-benign server's data source (a note, a file, a resource).
- **Trusted:** the user, the agent scaffold, the model provider, and every other upstream server.
- **Attacker goals:** exfiltrate a secret, trigger a forbidden action, hijack the final answer, or
  persist across turns.
- **Out of scope (v1):** compromised clients, network MITM, malicious gateways, model-weight attacks,
  and adaptive attackers who craft payloads against the deployed defense.

## Metrics: ASR and exposure

- **Raw ASR** = successes ÷ trials.
- **Exposure-conditioned ASR** = successes ÷ trials in which the payload reached the model.
- **Exposure** is measured at the LLM-request boundary, via the wrapped model client: a trial is
  *exposed* when the payload (or its decoded form, for encoding mutators) appears in any model
  request. We report both ASR definitions together, with exposure counts and Wilson 95% CIs.

## Model manifest

- **Static held-out (the headline):** `claude-sonnet-5-5`, `claude-opus-5-5` (Anthropic API),
  reported per model and pooled across scaffolds.
- **Adaptive held-out (ladder):** `claude-haiku-5-5` (Anthropic API) + `qwen3:8b` (local Ollama
  0.40.2, digest `500a1f067a9f`, Q4_K_M, 8.2B; `parallel_tool_calls=false`, `max_tokens=8192`,
  sampling unset). `qwen3:14b` qualified and was dropped (86.1% < 90% benign).
- **Scaffolds:** OpenAI Agents SDK + LangGraph (held-out, pooled for the headline). The Claude Agent
  SDK is a separate confirmatory benign slice and is excluded from held-out.

## Reproduce in one command

```
uv run mcpr report
```

Renders the held-out headline offline from the committed aggregate
(`review/M4/heldout-static/aggregate.json`) with **zero live model calls**. For trial-level
reproduction, `uv run pytest` replays the recorded sessions with zero live calls and asserts replay
equality — identical tool-call sequence, identical tool arguments, identical oracle verdict (SPEC
§9.1).

## Prior art

This harness is a *dynamic* red-team: a transparent MCP proxy injects attacks into live agent traffic
and scores them with a deterministic, objective oracle, and it measures a mitigation layer's utility
cost alongside its benefit. It positions against synthetic agent-injection benchmarks (AgentDojo,
InjecAgent) and static MCP scanners (mcp-scan).

## 14. Results

### Headline

We ran a pre-registered evaluation of 8 classes of indirect prompt injection against
MCP-connected agents, across 2 agent frameworks (OpenAI Agents SDK, LangGraph) and 4 models
(Claude Opus 5.5, Sonnet 5.5, Haiku 5.5; Qwen3-8B open-weight): ~2,800 held-out static trials
plus a rule-based adaptive search.

### Findings

1. **Data exfiltration failed.** 0 of 978 held-out exfiltration trials moved the planted secret to
   any sink. The only exfiltration in the project came from adaptive search against the
   unmitigated open-weight model (1 of 3 targets).
2. **Hijacking did not fail.** At baseline, injected instructions hijacked the answer or persisted
   in 3.1% [1.3, 7.1] of Sonnet 5.5 trials and 2.5% [1.0, 6.4] of Opus 5.5 trials (intervals
   overlap).
3. **The framework mattered (EXPLORATORY, not pre-registered).** At baseline, every frontier
   success occurred under the OpenAI Agents SDK (5.1% [2.7, 9.4]); none under LangGraph
   (0.0% [0.0, 2.7]), with comparable payload exposure. Across all seven arms the gap persists:
   OpenAI Agents SDK 5.6% [4.5, 7.0], LangGraph 0.6% [0.3, 1.3], with 70 of 76 successes under the
   OpenAI Agents SDK. LangGraph's 6 successes all occurred in mitigation arms.
4. **Susceptibility did not track model size (OpenAI Agents SDK only) (EXPLORATORY).** Haiku 5.5:
   16.7% [10.4, 25.7]; Qwen3-8B: 2.2% [0.6, 7.7]. A weaker model may also fail to carry out injected
   instructions.
5. **Provider safeguards did most of the defending.** Anthropic's content filter terminated 58.1% of
   frontier held-out trials vs 1.3% model refusals. These results include provider safeguards and may
   not transfer to deployments without equivalent filtering.
6. **Defenses trade off.** Fine-grained taint tracking costs 15.6–18.9 points of benign task success.
   Coarse taint and the combined set cost ~0, but no taint variant addresses hijacking or
   persistence, the only attacks that succeeded.

### Takeaways for agent builders

- Test your scaffold, not just your model.
- Count provider-level filtering as part of your security posture, and verify it on your stack.
- Coarse data-flow control is free but blind to attacks along allowed paths; closing that gap
  needs per-task policies (planned v2).

### Limitations

- The adaptive attacker is rule-based, not LLM-driven: measured ASR is a lower bound, and
  mitigation effectiveness an upper bound.
- Results include Anthropic provider safeguards.
- Two frameworks; Claude Agent SDK excluded from held-out (cli_cwd leak, D74); LangGraph class 3 N/A.
- qwen3:8b adaptive ran 1 seed instead of the pre-registered 3 (hardware; deviation D99).
- Answer-hijack counts include 2 refusal-quote cases (22 without them).
- Live runs are nondeterministic; reproducibility rests on replay.
- Era comparison (SPEC §4.3): the stateless-fallback was not triggered (two scaffolds speak the
  2026-07-28 era, D7), but the held-out headline scaffolds are OpenAI Agents SDK (stateless) and
  LangGraph (stateful-only), so protocol era is confounded with scaffold; no isolated era comparison
  is claimed.
