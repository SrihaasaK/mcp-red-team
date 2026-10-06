"""Upstream record/replay for the proxy (SPEC §9.1).

Record: each upstream request and its response messages are appended to
`upstream.<server>.jsonl`. Replay: a request is matched to the first unused
recorded pair with the same method and params (ignoring `_meta`, which carries
per-request envelope data), its response ids are rewritten to the live request
id, and nothing reaches an upstream. A request with no match raises
`ReplayDivergence`, so a replay that drifts fails loudly instead of improvising.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


class ReplayDivergence(RuntimeError):
    """A replayed request has no matching recorded response."""


def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(message: dict) -> list[str]:
    params = copy.deepcopy(message.get("params") or {})
    if isinstance(params, dict):
        params.pop("_meta", None)
    return [message["method"], canonical(params)]


class UpstreamRecorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def record(self, request: dict, responses: list[dict], status: int = 200) -> None:
        entry = {
            "seq": self.count,
            "key": request_key(request),
            "status": status,
            "request": request,
            "responses": responses,
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.count += 1


class UpstreamReplayer:
    def __init__(self, path: Path) -> None:
        lines = path.read_text().splitlines() if path.is_file() else []
        self.records = [json.loads(line) for line in lines if line.strip()]
        self.used = [False] * len(self.records)

    def respond(self, request: dict) -> tuple[int, list[dict]]:
        key = request_key(request)
        for i, rec in enumerate(self.records):
            if not self.used[i] and rec["key"] == key:
                self.used[i] = True
                out = copy.deepcopy(rec["responses"])
                recorded_id = rec["request"].get("id")
                for msg in out:
                    if "id" in msg and msg["id"] == recorded_id:
                        msg["id"] = request.get("id")
                return rec.get("status", 200), out
        raise ReplayDivergence(f"no recorded upstream response for {key[0]} {key[1][:300]}")

    def unused(self) -> list[list[str]]:
        return [r["key"] for r, u in zip(self.records, self.used, strict=True) if not u]
