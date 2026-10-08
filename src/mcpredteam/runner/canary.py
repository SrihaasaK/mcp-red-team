"""Resolve a corpus config's canary from its source at run time (D61, option 3).

The frozen corpus carries only `canary_ref`; the canary VALUE lives only in the mock source
(env.resolve_canary). The runner hydrates a config here and hands it to the (pure) oracle.
Scoring a config that still has a `canary_ref` but no resolved `canary` raises in the oracle
(the D61 loud guard), so this must be applied at every attack-scoring site.
"""

from __future__ import annotations

import copy

from mcpredteam.env import resolve_canary


def hydrate_attack_config(config: dict | None) -> dict | None:
    """Return a copy of `config` with `payload.canary` resolved from `payload.canary_ref`.

    No-op when `config` is None/empty, carries no `canary_ref`, or already has a `canary`
    (e.g. a pre-D61 recording whose frozen trial.json still embeds the value).
    """
    if not config:
        return config
    payload = config.get("payload") or {}
    ref = payload.get("canary_ref")
    if not ref or payload.get("canary"):
        return config
    out = copy.deepcopy(config)
    out["payload"]["canary"] = resolve_canary(ref)
    return out
