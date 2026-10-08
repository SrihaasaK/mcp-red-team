"""The D58 coarse-taint allowlist: the cross-server (source -> sink) flows a clean benign
run performs, derived from `tasks/benign.yaml` `exercises` (NOT hand-written).

A sink is a side-effecting tool (`oracle.rules.SIDE_EFFECTING_TOOLS`); a source is any other
(read/list) tool. For every benign task, each source server it exercises that differs from a
sink server it also exercises is an allowed source->sink flow. The coarse taint mitigation
blocks a cross-server flow UNLESS its (source, sink) pair is in this set (SPEC §10; D58).

Frozen per D58 as a PREREG amendment BEFORE held-out (the amendment itself lands in its own
task, after the dev eval). `test_mitigate_allowlist.py` asserts the derived set equals the
frozen D58 snapshot, so a later benign-suite edit that changes the needed pairs fails loudly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from mcpredteam.config import repo_root
from mcpredteam.oracle.rules import SIDE_EFFECTING_TOOLS

# The frozen D58 snapshot (the 6 pairs the dev benign suite needs). Derivation must equal this.
D58_SNAPSHOT: frozenset[tuple[str, str]] = frozenset({
    ("files", "mail"),
    ("http", "files"),
    ("mail", "files"),
    ("mail", "http"),
    ("notes", "http"),
    ("notes", "mail"),
})


def _server(qualified_tool: str) -> str:
    return qualified_tool.split(".", 1)[0]


@lru_cache(maxsize=1)
def derive_allowlist() -> frozenset[tuple[str, str]]:
    """Derive the (source_server, sink_server) allowlist from `tasks/benign.yaml` `exercises`.

    LAZY + cwd-independent on purpose: the stdio proxy imports this module from a subprocess
    whose cwd is OUTSIDE the repo (the CLI's cli_cwd), so `repo_root` is anchored on this file
    (not cwd) and no filesystem work happens at import time (that crashed the stdio proxy)."""
    tasks = yaml.safe_load((repo_root(Path(__file__)) / "tasks" / "benign.yaml").read_text())["tasks"]
    pairs: set[tuple[str, str]] = set()
    for t in tasks:
        ex = t.get("exercises") or []
        sink_servers = {_server(e) for e in ex if e in SIDE_EFFECTING_TOOLS}
        src_servers = {_server(e) for e in ex if e not in SIDE_EFFECTING_TOOLS}
        for s in src_servers:
            for k in sink_servers:
                if s != k:
                    pairs.add((s, k))
    return frozenset(pairs)
