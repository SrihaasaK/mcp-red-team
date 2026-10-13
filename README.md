# MCP Red Team

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
4. **Susceptibility did not track model size (EXPLORATORY).** Haiku 5.5: 16.7% [10.4, 25.7];
   Qwen3-8B: 2.2% [0.6, 7.7]. A weaker model may also fail to carry out injected instructions.
5. **Provider safeguards did most of the defending.** Anthropic's content filter terminated 58.1% of
   trials vs 1.3% model refusals. These results include provider safeguards and may not
   transfer to deployments without equivalent filtering.
6. **Defenses trade off.** Fine-grained taint tracking costs 15.6–18.9 points of benign task success.
   Coarse taint and the combined set cost ~0, but no taint variant addresses hijacking or
   persistence, the only attacks that succeeded.

### Takeaways for agent builders

- Test your scaffold, not just your model.
