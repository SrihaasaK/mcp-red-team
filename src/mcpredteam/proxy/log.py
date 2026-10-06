"""Per-trial JSONL message log (SPEC §4.1): every message, keyed by run and trial."""

from __future__ import annotations

import datetime
import json
from pathlib import Path


class TrialLog:
    """Append-only JSONL. `dir` is c2s (agent→upstream) or s2c; `stage` is
    `in` (as received) or `out` (as forwarded, after interceptors)."""

    def __init__(self, path: Path, **base: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", buffering=1)
        self.base = base
        self.seq = 0

    def write(self, direction: str, stage: str, message: object, **extra: object) -> None:
        rec = {
            "ts": datetime.datetime.now(datetime.UTC).isoformat(),
            **self.base,
            "seq": self.seq,
            "dir": direction,
            "stage": stage,
            "message": message,
            **extra,
        }
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.seq += 1

    def close(self) -> None:
        self._fh.close()
