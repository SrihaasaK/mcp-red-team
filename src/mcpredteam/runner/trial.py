"""Run one trial: launch the scaffold adapter against the proxy, then score it.

Spend is computed from the model's reported token usage at the frozen manifest
prices (DECISIONS.md D19) and logged per trial (CLAUDE.md #8).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from mcpredteam.config import repo_root
from mcpredteam.proxy.http_app import HubServer
from mcpredteam.runner.tasks import Task, Verdict, check
from mcpredteam.scaffolds.contract import TrialSpec

# Frozen manifest, USD per million tokens (input, output). DECISIONS.md D19.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
ADAPTERS = {"openai-agents": "scaffolds/openai-agents", "langgraph": "scaffolds/langgraph",
            "claude-agent-sdk": "scaffolds/claude-agent-sdk"}


def adapter_python(scaffold: str) -> Path:
    project = repo_root() / ADAPTERS[scaffold]
    python = project / ".venv" / "bin" / "python"
    if not python.exists():
        uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
        subprocess.run([uv, "sync", "--quiet", "--frozen", "--project", str(project)], check=True)
    return python


def subprocess_env(api_key: str | None) -> dict[str, str]:
    """A minimal environment: no OPENAI_* or other inherited settings leak in."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TMPDIR") if k in os.environ}
    env["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    if api_key:
        env["MCPR_ANTHROPIC_API_KEY"] = api_key
    return env


def tool_calls(trial_dir: Path, servers: list[str]) -> list[dict]:
    """Tool calls as the agent issued them (proxy log, c2s/in), in arrival order."""
    calls = []
    for server in servers:
        path = trial_dir / f"proxy.{server}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            msg = rec.get("message")
            if rec["dir"] == "c2s" and rec["stage"] == "in" and isinstance(msg, dict) and msg.get("method") == "tools/call":
                params = msg.get("params") or {}
                calls.append({"ts": rec["ts"], "server": server, "name": params.get("name"),
                              "arguments": params.get("arguments") or {}})
    calls.sort(key=lambda c: c["ts"])
    return calls


def llm_usage(trial_dir: Path, model: str) -> dict:
    """Spend from reported usage. Fails closed: a successful call without usage is
    counted in `usage_missing`, and the runner stops when spend is unknown."""
    if model not in PRICES:
        raise ValueError(f"model {model!r} is not in the frozen manifest {sorted(PRICES)}")
    path = trial_dir / "llm.jsonl"
    prompt = completion = calls = missing = 0
    cache_creation = cache_read = 0
    latency = 0.0
    finish = []
    for line in path.read_text().splitlines() if path.is_file() else []:
        rec = json.loads(line)
        calls += 1
        resp = rec.get("response") if isinstance(rec.get("response"), dict) else {}
        usage = resp.get("usage") or {}
        if rec.get("status") == 200 and not usage:
            missing += 1
        prompt += usage.get("prompt_tokens", 0)
        completion += usage.get("completion_tokens", 0)
        cache_creation += usage.get("cache_creation_input_tokens", 0)
        cache_read += usage.get("cache_read_input_tokens", 0)
        latency += rec.get("latency_s", 0.0)
        finish += [c.get("finish_reason") for c in resp.get("choices", [])]
    price_in, price_out = PRICES[model]
    # Anthropic prompt-caching multipliers (ephemeral 5-min cache): a cache WRITE is
    # 1.25x and a cache READ 0.1x the base input price. Both fields are absent on the
    # OpenAI-compatible endpoint (no caching; PREREG), so these terms are 0 there and
    # the OpenAI/LangGraph cost is unchanged. They ARE priced for the Claude Agent SDK
    # native endpoint (PREREG Amendment A1, D39) so the spend cap sees the real cost.
    cost = (prompt * price_in + completion * price_out
            + cache_creation * price_in * 1.25 + cache_read * price_in * 0.1) / 1e6
    return {
        "model_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "cost_usd": round(cost, 6),
        "model_latency_s": round(latency, 3),
        "finish_reasons": finish,
        "usage_missing": missing,
    }


async def run_trial(spec: TrialSpec, task: Task, *, hub: HubServer | None, api_key: str | None,
                    timeout_s: float = 600) -> dict:
    trial_dir = Path(spec.trial_dir)
    if trial_dir.exists() and any(trial_dir.iterdir()):
        raise FileExistsError(f"{trial_dir} is not empty: a trial never reuses a directory")
    for server in spec.servers:
        if spec.transport == "streamable-http":
            spec.mcp[server] = {"url": hub.url(spec.trial_id, server)}
        else:
            spec.mcp[server] = {
                "command": sys.executable,
                "args": ["-m", "mcpredteam.proxy.stdio", "--trial-dir", str(trial_dir), "--server", server],
                "cwd": str(repo_root()),
            }
    spec.write()
    if spec.transport == "streamable-http":
        hub.hub.register(spec)
    python = adapter_python(spec.scaffold)
    adapter = repo_root() / ADAPTERS[spec.scaffold] / "adapter.py"
    env = subprocess_env(api_key if spec.mode == "record" else None)
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        str(python), str(adapter), str(spec.path), env=env, cwd=str(repo_root()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        _, err = await proc.communicate()
        timed_out = True
    wall = round(time.monotonic() - t0, 3)
    stderr = err[-20000:].decode(errors="replace")
    if api_key:
        stderr = stderr.replace(api_key, "<redacted>")
    (trial_dir / "adapter.stderr.txt").write_text(stderr)
    if spec.transport == "streamable-http":
        proxies = await hub.hub.close_trial(spec.trial_id)
    else:
        proxies = [json.loads(p.read_text()) for s in spec.servers
                   if (p := trial_dir / f"proxy.{s}.summary.json").is_file()]
    result_path = trial_dir / "adapter_result.json"
    adapter_out = json.loads(result_path.read_text()) if result_path.is_file() else {
        "final_answer": None, "error": "adapter wrote no result" + (" (timed out)" if timed_out else "")}
    if timed_out and not adapter_out.get("error"):
        adapter_out["error"] = f"adapter timed out after {timeout_s}s"
    calls = tool_calls(trial_dir, spec.servers)
    verdict = check(task, adapter_out.get("final_answer") or "", calls)
    if adapter_out.get("error"):  # a trial that errored or timed out never passes
        verdict = Verdict(False, (f"trial error: {adapter_out['error'][:200]}", *verdict.reasons))
    result = {
        "trial_id": spec.trial_id,
        "task_id": spec.task_id,
        "era": spec.era,
        "transport": spec.transport,
        "scaffold": spec.scaffold,
        "model": spec.model,
        "mode": spec.mode,
        "final_answer": adapter_out.get("final_answer"),
        "error": adapter_out.get("error"),
        "verdict": verdict.to_dict(),
        "tool_calls": [{k: c[k] for k in ("server", "name", "arguments")} for c in calls],
        "wall_clock_s": wall,
        "adapter": {k: adapter_out.get(k) for k in ("llm_calls", "live_llm_calls", "blocked_network_calls",
                                                    "llm_replay_unused", "settings_applied", "wall_clock_s",
                                                    "effective")},
        "proxy": proxies,
        "era_matches": bool(proxies) and all(p["era_matches"] for p in proxies),
        "usage": llm_usage(trial_dir, spec.model) if spec.mode == "record" else None,
    }
    (trial_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
