"""Per-trial, cross-server taint store (M4-T1, D53).

The proxy runs one `Relay` per server, each with its OWN `TrialCtx`: a separate
OS process per server on stdio, a separate relay object per (trial, server) on
streamable-http (see `proxy/stdio.py` and `proxy/http_app.py`). So `ctx.state`
cannot carry taint ACROSS servers. The taint mitigations need exactly that —
"did content from server A reach a sink on server B?" — so this store backs that
one piece of cross-server state with a single append-only file in the trial
directory, guarded by an OS advisory lock (`fcntl.flock`). The lock makes it
correct whether the readers/writers are separate processes (stdio) or coroutines
sharing one process (streamable-http): the store behaves identically on both
transports.

Description pins (the pinning mitigation) are per-server, so they live in
`ctx.state` and never touch this file.
"""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from pathlib import Path

TAINT_FILE = "_mitigate_taint.jsonl"


@dataclass(frozen=True)
class Delivery:
    """One tool result delivered to the agent, tagged by its source server."""

    server: str
    text: str


class TaintStore:
    """File-backed set of cross-server deliveries for a single trial."""

    def __init__(self, trial_dir: Path) -> None:
        self.path = Path(trial_dir) / TAINT_FILE

    def record_delivery(self, server: str, text: str) -> None:
        """Append one delivered tool-result's text under an exclusive lock."""
        rec = json.dumps({"server": server, "text": text}, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open for append; the exclusive flock serialises concurrent writers
        # (separate stdio subprocesses, or concurrent http coroutines).
        with self.path.open("a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(rec + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def deliveries(self) -> list[Delivery]:
        """Every delivery recorded so far, in append order (shared lock)."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                lines = f.read().splitlines()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        out: list[Delivery] = []
        for line in lines:
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(Delivery(server=d["server"], text=d["text"]))
        return out

    def delivered_servers(self) -> set[str]:
        return {d.server for d in self.deliveries()}
