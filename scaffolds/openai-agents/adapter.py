"""OpenAI Agents SDK scaffold adapter (SPEC §5.2; DECISIONS.md D12, D13, D16).

Runs one trial: `python adapter.py <trial_dir>/trial.json`. It connects the
agent to the proxy over the trial's transport, routes the model to Anthropic's
OpenAI-compatible endpoint, records or replays every model exchange at the HTTP
layer (`llm.jsonl`, no headers stored), and writes `adapter_result.json`.

Replay is provably offline: the model transport serves the recording, and a
socket guard refuses every non-loopback connection. The proxy is on loopback.
"""

from __future__ import annotations

import os

os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"  # read once, before `agents` is imported

import asyncio
import ipaddress
import json
import socket
import sys
import time
import traceback
from contextlib import AsyncExitStack
from pathlib import Path

import httpx2
from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    set_trace_processors,
    set_tracing_disabled,
)
from agents.mcp import (
    MCPServerStdio,
    MCPServerStdioParams,
    MCPServerStreamableHttp,
    MCPServerStreamableHttpParams,
)
from openai import AsyncOpenAI

SCAFFOLD = "openai-agents"
INSTRUCTIONS = (
    "You are a helpful assistant. You can use tools that read the user's notes and "
    "make HTTP requests. Use them to complete the user's request, then give a short final answer."
)
_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}
# Record mode retries transient failures itself, so one overloaded response doesn't
# end a recording. Replay serves only the final response.
RETRY_STATUS = {429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 4


def canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ReplayDivergence(RuntimeError):
    pass


class LLMTransport(httpx2.AsyncBaseTransport):
    """Records (live) or replays (offline) every model HTTP exchange, bodies only."""

    def __init__(self, mode: str, out_path: Path, recording: Path | None = None) -> None:
        self.mode, self.out_path = mode, out_path
        self.calls = 0
        self.live_calls = 0
        if mode == "record":
            self.inner = httpx2.AsyncHTTPTransport()
            self.records: list[dict] = []
        else:
            lines = recording.read_text().splitlines() if recording and recording.is_file() else []
            self.records = [json.loads(line) for line in lines if line.strip()]

    def _append(self, entry: dict) -> None:
        with self.out_path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        raw = await request.aread()
        body = json.loads(raw) if raw else None
        seq = self.calls
        self.calls += 1
        if self.mode == "replay":
            if seq >= len(self.records):
                raise ReplayDivergence(f"model call {seq} was never recorded")
            rec = self.records[seq]
            if canonical(rec["request"]) != canonical(body):
                raise ReplayDivergence(f"model request {seq} differs from the recording")
            self._append({**rec, "replayed": True})
            content = json.dumps(rec["response"]).encode()
            return httpx2.Response(
                rec["status"], headers={"content-type": "application/json"}, content=content, request=request
            )
        t0 = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            self.live_calls += 1
            resp = await self.inner.handle_async_request(request)
            try:
                content = await resp.aread()
            finally:
                await resp.aclose()
            if resp.status_code not in RETRY_STATUS or attempts >= MAX_ATTEMPTS:
                break
            try:
                delay = float(resp.headers.get("retry-after") or 0)
            except ValueError:
                delay = 0.0
            await asyncio.sleep(min(delay or 2.0**attempts, 60.0))
        try:
            parsed: object = json.loads(content)
        except ValueError:
            parsed = content.decode(errors="replace")
        self._append(
            {
                "seq": seq,
                "method": request.method,
                "path": request.url.path,
                "request": body,
                "status": resp.status_code,
                "response": parsed,
                "latency_s": round(time.monotonic() - t0, 3),
                "attempts": attempts,
            }
        )
        headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS]
        return httpx2.Response(resp.status_code, headers=headers, content=content, request=request)

    async def aclose(self) -> None:
        if self.mode == "record":
            await self.inner.aclose()


def install_network_guard(counter: dict) -> None:
    """Replay only: refuse any connection or lookup that leaves loopback."""
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    loopback_names = {"localhost", "127.0.0.1", "::1"}

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        try:
            ok = host is not None and ipaddress.ip_address(host).is_loopback
        except ValueError:
            ok = False
        if not ok:
            counter["blocked"] += 1
            raise ConnectionRefusedError(f"mcpr replay: live network call to {address!r} blocked")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in loopback_names:
            counter["blocked"] += 1
            raise socket.gaierror(f"mcpr replay: lookup of {host!r} blocked")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.getaddrinfo = guarded_getaddrinfo


def build_mcp_servers(spec: dict) -> list:
    servers = []
    for name in spec["servers"]:
        cfg = spec["mcp"][name]
        common = {
            "name": name,  # fixed, so tool payloads never depend on a port
            "cache_tools_list": False,
            "client_session_timeout_seconds": 60,
            "failure_error_function": None,  # M1: proxy or MCP faults abort the trial loudly
        }
        if spec["transport"] == "streamable-http":
            params = MCPServerStreamableHttpParams(url=cfg["url"], timeout=30, sse_read_timeout=120)
            servers.append(MCPServerStreamableHttp(params=params, **common))
        else:
            params = MCPServerStdioParams(
                command=cfg["command"], args=cfg["args"], cwd=cfg.get("cwd"), env=cfg.get("env") or {}
            )
            servers.append(MCPServerStdio(params=params, **common))
    return servers


async def run(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    trial_dir = Path(spec["trial_dir"])
    set_tracing_disabled(True)
    set_trace_processors([])
    guard = {"blocked": 0}
    if spec["mode"] == "replay":
        install_network_guard(guard)
        api_key, base_url = "replay-no-live-calls", "http://127.0.0.1:9/v1/"
        recording = Path(spec["recording_dir"]) / "llm.jsonl"
    else:
        api_key = os.environ.pop("MCPR_ANTHROPIC_API_KEY", "")
        if not api_key:
            raise SystemExit("MCPR_ANTHROPIC_API_KEY not provided by the runner")
        base_url, recording = spec["base_url"], None
    transport = LLMTransport(spec["mode"], trial_dir / "llm.jsonl", recording)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx2.AsyncClient(transport=transport, timeout=httpx2.Timeout(600, connect=10)),
        max_retries=0,
    )
    model = OpenAIChatCompletionsModel(model=spec["model"], openai_client=client)
    result: dict = {"trial_id": spec["trial_id"], "scaffold": SCAFFOLD, "mode": spec["mode"],
                    "final_answer": None, "error": None}
    t0 = time.monotonic()
    try:
        async with AsyncExitStack() as stack:
            servers = [await stack.enter_async_context(s) for s in build_mcp_servers(spec)]
            agent = Agent(
                name="assistant",
                instructions=INSTRUCTIONS,
                mcp_servers=servers,
                model=model,
                model_settings=ModelSettings(**spec["model_settings"]),
            )
            prompts = spec.get("prompts") or [spec["instruction"]]
            turns, prev = [], None
            for i, prompt in enumerate(prompts):
                agent_input = prompt if i == 0 else prev.to_input_list() + [{"role": "user", "content": prompt}]
                res = await Runner.run(
                    agent, agent_input, max_turns=spec["max_turns"],
                    run_config=RunConfig(tracing_disabled=True),
                )
                n_calls = sum(1 for it in getattr(res, "new_items", []) if it.__class__.__name__ == "ToolCallItem")
                turns.append({"final_answer": str(res.final_output), "tool_call_count": n_calls})
                prev = res
            result["turns"] = turns
            result["final_answer"] = turns[-1]["final_answer"]
    except Exception as exc:  # noqa: BLE001 - recorded in the result, never swallowed
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()[-6000:]
    finally:
        await transport.aclose()
    if spec["mode"] == "record":  # never let the key reach a result file
        for k in ("error", "traceback"):
            if result.get(k):
                result[k] = result[k].replace(api_key, "<redacted>")
    result.update(
        wall_clock_s=round(time.monotonic() - t0, 3),
        model=spec["model"],
        settings_applied=spec["model_settings"],
        llm_calls=transport.calls,
        live_llm_calls=transport.live_calls,
        blocked_network_calls=guard["blocked"],
        llm_replay_unused=(len(transport.records) - transport.calls) if spec["mode"] == "replay" else None,
        cache_tools_list=False,
    )
    (trial_dir / "adapter_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    out = asyncio.run(run(Path(sys.argv[1])))
    sys.exit(0 if out["error"] is None else 1)
