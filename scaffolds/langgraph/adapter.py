"""LangGraph scaffold adapter (SPEC §5.2; DECISIONS.md D6, D12, D13, D16, D20, D21).

Runs one trial: `python adapter.py <trial_dir>/trial.json`. Mirrors the OpenAI
Agents adapter's contract: it connects the agent to the proxy over the trial's
transport (langchain-mcp-adapters, mcp 1.30.0, stateful 2025-11-25 era only),
routes the model to Anthropic's OpenAI-compatible endpoint through
`langchain_openai.ChatOpenAI`, records or replays every model exchange at the
HTTP layer (`llm.jsonl`, no headers stored), and writes `adapter_result.json`.

Replay is provably offline: the model transport serves the recording, a socket
guard refuses every non-loopback connection, and LangSmith tracing is disabled.
"""

from __future__ import annotations

import os

# Disable every telemetry path before any langchain import (read at import/first use).
for _k in [k for k in os.environ if k.startswith(("LANGCHAIN_", "LANGSMITH_"))]:
    del os.environ[_k]
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING_V2"] = "false"
os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "false"

import asyncio
import ipaddress
import json
import socket
import sys
import time
import traceback
import warnings
from pathlib import Path

import openai
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

# `openai>=3` is backed by httpx2, `openai<3` by httpx. The model transport must be built
# from the library the installed SDK uses (the MCP client side is plain httpx either way).
from langchain_openai._compat import httpx
from langgraph.prebuilt import create_react_agent

warnings.filterwarnings("ignore", message="create_react_agent has been moved")

SCAFFOLD = "langgraph"
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


class LLMTransport(httpx.AsyncBaseTransport):
    """Records (live) or replays (offline) every model HTTP exchange, bodies only."""

    def __init__(self, mode: str, out_path: Path, recording: Path | None = None) -> None:
        self.mode, self.out_path = mode, out_path
        self.calls = 0
        self.live_calls = 0
        if mode == "record":
            self.inner = httpx.AsyncHTTPTransport()
            self.records: list[dict] = []
        else:
            lines = recording.read_text().splitlines() if recording and recording.is_file() else []
            self.records = [json.loads(line) for line in lines if line.strip()]

    def _append(self, entry: dict) -> None:
        with self.out_path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
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
            return httpx.Response(
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
        return httpx.Response(resp.status_code, headers=headers, content=content, request=request)

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


def build_connections(spec: dict) -> dict:
    connections: dict = {}
    for name in spec["servers"]:
        cfg = spec["mcp"][name]
        if spec["transport"] == "streamable-http":
            connections[name] = {
                "transport": "streamable_http",
                "url": cfg["url"],
                "timeout": 30,
                "sse_read_timeout": 120,
            }
        else:
            conn = {
                "transport": "stdio",
                "command": cfg["command"],
                "args": cfg["args"],
                "cwd": cfg.get("cwd"),
            }
            if cfg.get("env"):
                conn["env"] = cfg["env"]
            connections[name] = conn
    return connections


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b if isinstance(b, str) else b.get("text", "") for b in content
                 if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")]
        return "".join(parts)
    return "" if content is None else str(content)


def _refusal(messages: list) -> str | None:
    """A content-filter / refusal finish on the final assistant message of a turn."""
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last is None:
        return None
    finish = (last.response_metadata or {}).get("finish_reason")
    if finish == "content_filter":
        return "content_filter"
    refusal = (last.additional_kwargs or {}).get("refusal")
    return str(refusal) if refusal else None


def build_model(spec: dict, api_key: str, base_url: str, transport: LLMTransport) -> ChatOpenAI:
    ms = spec.get("model_settings") or {}
    kwargs: dict = {}
    if ms.get("max_tokens"):
        kwargs["max_tokens"] = ms["max_tokens"]
    # temperature is rejected by the endpoint and seed is ignored (D21): neither is set.
    return ChatOpenAI(
        model=spec["model"],
        base_url=base_url,
        api_key=api_key,
        http_async_client=httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600, connect=10)),
        max_retries=0,
        streaming=False,
        extra_body=ms.get("extra_body") or {"output_config": {"effort": "high"}},
        **kwargs,
    )


def settings_report(spec: dict) -> dict:
    """Which pinned settings this scaffold actually put on the wire (SPEC §5.2 item 4)."""
    ms = spec.get("model_settings") or {}
    eb = ms.get("extra_body") or {"output_config": {"effort": "high"}}
    return {
        "requested": ms,
        "temperature": "not set (the endpoint rejects it; D21)",
        "seed": "not fixable (LangGraph/ChatOpenAI sends none; the Anthropic endpoint ignores it)",
        "effort": f"honored via extra_body={eb}",
        "max_tokens": f"honored as max_completion_tokens={ms['max_tokens']}" if ms.get("max_tokens") else "unset",
        "parallel_tool_calls": "honored via bind_tools(parallel_tool_calls=False)"
        if ms.get("parallel_tool_calls") is False else "default",
    }


async def run(spec_path: Path) -> dict:
    spec = json.loads(spec_path.read_text())
    trial_dir = Path(spec["trial_dir"])
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
    result: dict = {"trial_id": spec["trial_id"], "scaffold": SCAFFOLD, "mode": spec["mode"],
                    "final_answer": None, "error": None}
    t0 = time.monotonic()
    turns: list[dict] = []
    try:
        model = build_model(spec, api_key, base_url, transport)
        client = MultiServerMCPClient(build_connections(spec))
        tools = await client.get_tools()
        result["tool_names"] = [t.name for t in tools]
        bound = model.bind_tools(
            tools, **({"parallel_tool_calls": False} if (spec.get("model_settings") or {}).get(
                "parallel_tool_calls") is False else {})
        )
        agent = create_react_agent(bound, tools, prompt=SystemMessage(content=INSTRUCTIONS))
        # One model call = agent step + tool step; the recursion limit counts both.
        config = {"recursion_limit": 2 * spec["max_turns"] + 1, "callbacks": []}
        prompts = spec.get("prompts") or [spec["instruction"]]
        history: list = []
        for prompt in prompts:
            out = await agent.ainvoke({"messages": [*history, HumanMessage(content=prompt)]}, config=config)
            messages = out["messages"]
            new = messages[len(history):]
            history = messages
            refused = _refusal(new)
            n_calls = sum(len(m.tool_calls) for m in new if isinstance(m, AIMessage))
            if refused is not None:
                # A refusal is a valid outcome (D32): a completed empty-answer turn.
                result["refused"] = True
                turns.append({"final_answer": "", "tool_call_count": n_calls, "refused": refused[:200]})
                break
            last_ai = next((m for m in reversed(new) if isinstance(m, AIMessage)), None)
            turns.append({"final_answer": _text(last_ai.content) if last_ai else "", "tool_call_count": n_calls})
        result["turns"] = turns
        result["final_answer"] = turns[-1]["final_answer"]
        result["tool_message_count"] = sum(1 for m in history if isinstance(m, ToolMessage))
    except openai.ContentFilterFinishReasonError as exc:
        result["refused"] = True
        turns.append({"final_answer": "", "tool_call_count": 0, "refused": str(exc)[:200]})
        result["turns"] = turns
        result["final_answer"] = ""
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
        settings_honored=settings_report(spec),
        llm_calls=transport.calls,
        live_llm_calls=transport.live_calls,
        blocked_network_calls=guard["blocked"],
        llm_replay_unused=(len(transport.records) - transport.calls) if spec["mode"] == "replay" else None,
        tools_fetched="get_tools() once per trial; a fresh MCP session per tool call (langchain-mcp-adapters 0.3.2)",
        versions=_versions(),
    )
    (trial_dir / "adapter_result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def _versions() -> dict:
    from importlib.metadata import version

    return {p: version(p) for p in ("mcp", "langchain-mcp-adapters", "langgraph", "langchain-openai",
                                    "langchain-core", "openai", "httpx")}


if __name__ == "__main__":
    out = asyncio.run(run(Path(sys.argv[1])))
    sys.exit(0 if out["error"] is None else 1)
