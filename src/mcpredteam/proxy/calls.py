"""The EXECUTED tool calls of a recorded trial (D81): the agent's `tools/call` requests with any
relay-tagged `mitigation_blocked` call removed, so a blocked call counts as NOT executed in BOTH
the attack oracle (`oracle.trajectory.Trajectory.from_trial`) and the benign/utility checker
(`runner.trial.tool_calls` -> `runner.tasks.check`). One shared helper, so the two paths cannot
drift (D56 excluded blocked calls only on the attack path; D81 extends it to utility).

A block is logged (s2c/out, `mitigation_blocked`) immediately after the c2s/in it blocks, so each
block is matched to the nearest PRECEDING not-yet-blocked `tools/call` with the same request id,
PER SERVER FILE (robust to request-id reuse within a server). An unmitigated run has no block
record, so this is a strict no-op there (every recorded unmitigated verdict is unchanged -- the
D81 guard re-score confirms 0 changed).
"""

from __future__ import annotations

import json
from pathlib import Path


def executed_calls(trial_dir: Path | str, servers: list[str] | None = None) -> list[dict]:
    """Executed `tools/call`s (blocked excluded) as `{ts, server, name, arguments}`, ts-sorted.

    `servers=None` reads every `proxy.*.jsonl` in the trial dir (the attack-oracle use); a list
    restricts to those servers (the benign/utility use passes `spec.servers`).
    """
    d = Path(trial_dir)
    files = ([d / f"proxy.{s}.jsonl" for s in servers] if servers is not None
             else sorted(d.glob("proxy.*.jsonl")))
    out: list[dict] = []
    for p in files:
        if not p.is_file():
            continue
        server = p.name[len("proxy."):-len(".jsonl")]
        local: list[dict] = []  # {ts, id, name, arguments, blocked}
        for line in p.read_text().splitlines():
            rec = json.loads(line)
            msg = rec.get("message")
            if rec.get("dir") == "s2c" and rec.get("stage") == "out" and rec.get("mitigation_blocked"):
                bid = msg.get("id") if isinstance(msg, dict) else None
                for item in reversed(local):
                    if not item["blocked"] and item["id"] == bid:
                        item["blocked"] = True
                        break
                continue
            if (rec.get("dir") == "c2s" and rec.get("stage") == "in"
                    and isinstance(msg, dict) and msg.get("method") == "tools/call"):
                params = msg.get("params") or {}
                local.append({"ts": rec.get("ts", ""), "id": msg.get("id"), "server": server,
                              "name": params.get("name"), "arguments": params.get("arguments") or {},
                              "blocked": False})
        out.extend({"ts": i["ts"], "server": i["server"], "name": i["name"], "arguments": i["arguments"]}
                   for i in local if not i["blocked"])
    out.sort(key=lambda c: c["ts"])
    return out
