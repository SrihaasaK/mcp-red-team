# M4-T1 offline counterfactual (coarse-vs-fine input)

Each SPEC §10 mitigation's DECISION logic applied to RECORDED trajectories; NO live
calls, NO re-run. Benign numbers are a utility-cost **proxy** (NOT measured utility; the
agent did not react to a block). Attack numbers are a **counterfactual** (NOT ASR).
Taint-fine under-blocks TRANSFORMED secrets by design (PREREG (c)); here the v2 attacks
contain no exfil, so both taint variants mainly exercise the benign false-block surface.

## Benign (utility-cost proxy; blocked sink calls / altered results)

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

## Attack (counterfactual; attack sink calls that WOULD be blocked)

| group | trials | mitigation | sink calls | would-block | block rate | altered |
| --- | --- | --- | --- | --- | --- | --- |
| m2-dev-v2 attack | 220 | pinning | 48 | 6 | 12.5% | 0 |
| m2-dev-v2 attack | 220 | spotlight-delimiters | 48 | 0 | 0.0% | 0 |
| m2-dev-v2 attack | 220 | spotlight-strip | 48 | 0 | 0.0% | 93 |
| m2-dev-v2 attack | 220 | taint-coarse | 48 | 0 | 0.0% | 0 |
| m2-dev-v2 attack | 220 | taint-fine | 48 | 20 | 41.7% | 0 |

