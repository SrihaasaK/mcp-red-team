"""Adaptive-attacker arm (D67; DEV design M4-T4a).

A rule-based, seeded, feedback-aware search over the FROZEN `attacks/mutators.py` operators,
extended with an exfil-transform instruction family (plain/base64/hex/reversed/split, the D66
transforms). Isolated from the frozen policy: imports `attacks.engine`/`attacks.mutators`,
`oracle.rules`, `runner.trial`, `report.stats` but changes none of them. See
`review/M4/adaptive/design.md` and DECISIONS.md D85. Pre-registration is PREREG Amendment A7
(after this DEV design, before any held-out run). No held-out data is read here.
"""
