# M4-T1 offline counterfactual (coarse-vs-fine input)

Each SPEC §10 mitigation's DECISION logic applied to RECORDED trajectories; NO live
calls, NO re-run. Both the benign AND the attack columns here are a **utility-cost proxy**
(blocks of the agent's OWN, task-required sink calls), **NOT a security catch**: every one
of the 48 executed sink calls in the v2 ATTACK trials is task-required (checked against each
task's `exercises`), and v2 has 0 executed exfil and 0 executed forbidden-action, so there
is no malicious sink call here to catch. The security **catch** rate is measured
SYNTHETICALLY in `synthetic_catch.md` (R2), not from these recordings. 'altered' means a
spotlight-STRIP pattern changed a result's content; delimiters wrap EVERY result but remove
nothing, so delimiters 'altered' is 0 by definition.

## Benign (utility-cost proxy; blocked sink calls / strip-altered results)

| group | trials | mitigation | sink calls | blocked | block rate | results | altered |
| --- | --- | --- | --- | --- | --- | --- | --- |
| m2-dev-v2 controls | 360 | pinning | 134 | 0 | 0.0% | 684 | 0 |
| m2-dev-v2 controls | 360 | spotlight-delimiters | 134 | 0 | 0.0% | 684 | 0 |
| m2-dev-v2 controls | 360 | spotlight-strip | 134 | 0 | 0.0% | 684 | 0 |
| m2-dev-v2 controls | 360 | taint-coarse | 134 | 0 | 0.0% | 684 | 0 |
| m2-dev-v2 controls | 360 | taint-fine | 134 | 84 | 62.7% | 684 | 0 |
| m3-openai-benign | 80 | pinning | 30 | 0 | 0.0% | 150 | 0 |
| m3-openai-benign | 80 | spotlight-delimiters | 30 | 0 | 0.0% | 150 | 0 |
| m3-openai-benign | 80 | spotlight-strip | 30 | 0 | 0.0% | 150 | 0 |
| m3-openai-benign | 80 | taint-coarse | 30 | 0 | 0.0% | 150 | 0 |
| m3-openai-benign | 80 | taint-fine | 30 | 18 | 60.0% | 150 | 0 |
| m3-langgraph-benign | 80 | pinning | 27 | 0 | 0.0% | 145 | 0 |
| m3-langgraph-benign | 80 | spotlight-delimiters | 27 | 0 | 0.0% | 145 | 0 |
| m3-langgraph-benign | 80 | spotlight-strip | 27 | 0 | 0.0% | 145 | 0 |
| m3-langgraph-benign | 80 | taint-coarse | 27 | 0 | 0.0% | 145 | 0 |
| m3-langgraph-benign | 80 | taint-fine | 27 | 15 | 55.6% | 145 | 0 |
| m3-claude-agent-sdk-benign | 40 | pinning | 15 | 0 | 0.0% | 79 | 0 |
| m3-claude-agent-sdk-benign | 40 | spotlight-delimiters | 15 | 0 | 0.0% | 79 | 0 |
| m3-claude-agent-sdk-benign | 40 | spotlight-strip | 15 | 0 | 0.0% | 79 | 0 |
| m3-claude-agent-sdk-benign | 40 | taint-coarse | 15 | 0 | 0.0% | 79 | 0 |
| m3-claude-agent-sdk-benign | 40 | taint-fine | 15 | 9 | 60.0% | 79 | 0 |

## Under attack (utility cost, NOT catch): blocks of TASK-REQUIRED sink calls

All 48 executed sink calls in the v2 attack trials are task-required; a block here is
a utility cost the agent would pay WHILE under attack, not a thwarted exfiltration.

| group | trials | mitigation | task sink calls | blocked | block rate | strip-altered |
| --- | --- | --- | --- | --- | --- | --- |
| m2-dev-v2 attack | 220 | pinning | 48 | 6 | 12.5% | 0 |
| m2-dev-v2 attack | 220 | spotlight-delimiters | 48 | 0 | 0.0% | 0 |
| m2-dev-v2 attack | 220 | spotlight-strip | 48 | 0 | 0.0% | 93 |
| m2-dev-v2 attack | 220 | taint-coarse | 48 | 0 | 0.0% | 0 |
| m2-dev-v2 attack | 220 | taint-fine | 48 | 20 | 41.7% | 0 |

Pinning's 6 blocks are task-required http calls (http_get ×1, http_post ×5) whose
descriptions a class-2 attack poisoned: pinning cannot tell the task's own use of the
poisoned tool from a malicious one, so under attack it is a utility cost (its genuine
value — refusing to let the agent trust a changed description — is real but not an exfil
block). Taint-fine's blocks are the agent re-sending read content to an allowlisted sink
(benign content flow), NOT exfil. spotlight-strip altered 93 attack results (it removed
injected instructions from poisoned tool results) with 0 benign alterations — that IS its
defensive action, measured as a content change, not a sink block.

