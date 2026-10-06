"""Proxy core: one agent-facing MCP connection relayed to one mock upstream.

Transport-agnostic (SPEC §4.1): the Streamable HTTP app and the stdio entry point
both hand each JSON-RPC message to `Relay.handle` and send back the `Reply`.

Era forcing (DECISIONS.md D16): a handshake-only run refuses `server/discover`
with 404 / -32601, which the client's auto-negotiator treats as "fall back to
initialize". A stateless-only run refuses `initialize` the same way, so a
client that falls back fails loudly instead of quietly changing era. The era
actually negotiated is recorded per connection and compared with the forced one.

Record/replay (SPEC §9.1): in record mode each upstream exchange is appended to
`upstream.<server>.jsonl`; in replay mode no upstream object exists at all and
every response comes from the recording.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
from mcp.shared.inbound import encode_header_value
from mcp.types.version import HANDSHAKE_PROTOCOL_VERSIONS

from mcpredteam.env import UPSTREAMS
from mcpredteam.proxy.interfaces import Interceptor, TrialCtx
from mcpredteam.proxy.log import TrialLog
from mcpredteam.proxy.recording import UpstreamRecorder, UpstreamReplayer

HANDSHAKE = "2025-11-25"
STATELESS = "2026-07-28"
ERAS = (HANDSHAKE, STATELESS)
META_VERSION = "io.modelcontextprotocol/protocolVersion"
NAME_PARAM = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
# The DNS-rebinding guard on the mock's app requires a Host header with a port.
UPSTREAM_BASE = "http://127.0.0.1:8765"


@dataclass
class Reply:
    status: int
    body: dict | None
    headers: dict[str, str] = field(default_factory=dict)


def jsonrpc_error(req_id: object, code: int, message: str, data: object = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def modern_version(message: dict) -> str | None:
    """The stateless-era version stamped in params._meta, if any."""
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    version = meta.get(META_VERSION) if isinstance(meta, dict) else None
    return version if version and version not in HANDSHAKE_PROTOCOL_VERSIONS else None


def _parse_body(resp: httpx2.Response) -> list[dict]:
    ctype = resp.headers.get("content-type", "")
    if not resp.content:
        return []
    if "text/event-stream" in ctype:  # not expected: mocks run with json_response=True
        out = []
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
        return out
    body = resp.json()
    return body if isinstance(body, list) else [body]


class InProcessUpstream:
    """A fresh mock MCPServer reached over Streamable HTTP, in-process (httpx2 ASGITransport)."""

    def __init__(self, server_name: str) -> None:
        self.server = UPSTREAMS[server_name]()
        self.app = self.server.streamable_http_app(json_response=True)
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=UPSTREAM_BASE, timeout=60
        )
        self.calls = 0
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._serve())
        ready = asyncio.create_task(self._ready.wait())
        done, _ = await asyncio.wait({ready, self._task}, return_when=asyncio.FIRST_COMPLETED)
        if self._task in done:
            ready.cancel()
            self._task.result()  # surfaces the startup error
            raise RuntimeError("upstream stopped before it was ready")

    async def _serve(self) -> None:
        # session_manager.run() holds an anyio task group, so it must be entered
        # and exited in this one task.
        async with self.server.session_manager.run():
            self._ready.set()
            await self._stop.wait()

    async def send(self, method: str, headers: dict, message: dict | None = None) -> httpx2.Response:
        self.calls += 1
        if method == "POST":
            return await self.client.post("/mcp", json=message, headers=headers)
        return await self.client.request(method, "/mcp", headers=headers)

    async def close(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        await self.client.aclose()


class Relay:
    def __init__(
        self,
        *,
        server: str,
        ctx: TrialCtx,
        mode: str,
        trial_dir: Path,
        recording_dir: Path | None = None,
        interceptors: tuple[Interceptor, ...] = (),
    ) -> None:
        if ctx.era not in ERAS:
            raise ValueError(f"forced era must be one of {ERAS}, got {ctx.era!r}")
        self.server, self.ctx, self.mode = server, ctx, mode
        self.trial_dir = Path(trial_dir)
        self.interceptors = interceptors
        self.log = TrialLog(
            self.trial_dir / f"proxy.{server}.jsonl",
            run_id=ctx.run_id,
            trial_id=ctx.trial_id,
            server=server,
            transport=ctx.transport,
            era_forced=ctx.era,
            mode=mode,
        )
        self.upstream = InProcessUpstream(server) if mode == "record" else None
        self.recorder = UpstreamRecorder(self.trial_dir / f"upstream.{server}.jsonl") if mode == "record" else None
        self.replayer = (
            UpstreamReplayer(Path(recording_dir) / f"upstream.{server}.jsonl") if mode == "replay" else None
        )
        if mode == "replay" and recording_dir is None:
            raise ValueError("replay needs recording_dir")
        self.agent_session: str | None = None
        self.upstream_session: str | None = None
        self.handshake_version: str | None = None
        self.versions_seen: set[str] = set()
        self.refusals: list[str] = []
        self.errors: list[str] = []

    async def start(self) -> None:
        if self.upstream:
            await self.upstream.start()

    # -- message handling -------------------------------------------------

    async def handle(self, message: object, headers: dict | None = None) -> Reply:
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        self.log.write("c2s", "in", message, http={k: v for k, v in hdrs.items() if k.startswith("mcp-")})
        if not isinstance(message, dict) or "method" not in message:
            # Client responses to server-initiated requests: the mocks never send any.
            self.errors.append("unexpected non-request message from client")
            return Reply(400, jsonrpc_error(None, -32600, "mcpr: unexpected message"))
        method = message["method"]

        if method == "server/discover" and self.ctx.era == HANDSHAKE:
            return self._refuse(message, "handshake-only run: server/discover is not offered")
        if method == "initialize" and self.ctx.era == STATELESS:
            return self._refuse(message, "stateless-only run: initialize is not offered")
        if modern_version(message) and self.ctx.era == HANDSHAKE:
            return self._refuse(message, "handshake-only run: stateless requests are not accepted")

        if "id" not in message:  # notification
            self.log.write("c2s", "out", message)
            if self.upstream:
                await self.upstream.send("POST", self._upstream_headers(message), message)
            return Reply(202, None)

        out = await self._intercept_request(message)
        self.log.write("c2s", "out", out)
        try:
            status, responses = await self._exchange(out)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client and logged, never swallowed
            self.errors.append(f"{type(exc).__name__}: {exc}")
            body = jsonrpc_error(message["id"], -32603, f"mcpr proxy error: {type(exc).__name__}: {exc}")
            self.log.write("s2c", "out", body, status=500, proxy_error=True)
            return Reply(500, body)
        response = next((r for r in responses if r.get("id") == out["id"]), None)
        if response is None:
            # Session-level rejections from the upstream (404 "Session not found", 400
            # "Missing session ID", 503 ...) carry id:null. Pass one through with its real
            # status, re-ided to this request as the mcp client itself does.
            nulls = [r for r in responses if r.get("id") is None and "error" in r]
            if len(nulls) != 1:
                self.log.write("s2c", "in", responses, status=status, unmatched=True)
                body = jsonrpc_error(message["id"], -32603, "mcpr: upstream sent no response for this id")
                self.errors.append(f"no response for {method} (upstream status {status})")
                self.log.write("s2c", "out", body, status=502, proxy_error=True)
                return Reply(502, body)
            self.log.write("s2c", "in", nulls[0], status=status, upstream_id_null=True)
            response = {**copy.deepcopy(nulls[0]), "id": out["id"]}
        else:
            self.log.write("s2c", "in", response, status=status)
        response = await self._intercept_response(out, response)
        self._observe(out, response)
        self.log.write("s2c", "out", response, status=status)
        reply_headers = {"mcp-session-id": self.agent_session} if self.agent_session else {}
        return Reply(status, response, reply_headers)

    async def terminate(self) -> None:
        """Client DELETE: end the upstream session (handshake era only)."""
        self.log.write("c2s", "in", {"http": "DELETE"})
        if self.upstream and self.upstream_session:
            await self.upstream.send("DELETE", self._upstream_headers({"method": "DELETE"}))
        self.agent_session = None
        self.upstream_session = None
        self.handshake_version = None

    def note_get(self) -> None:
        self.log.write("c2s", "in", {"http": "GET"}, answered=405)

    def _refuse(self, message: dict, reason: str) -> Reply:
        self.refusals.append(message["method"])
        if "id" not in message:
            self.log.write("s2c", "out", None, refused=reason)
            return Reply(202, None)
        body = jsonrpc_error(message["id"], -32601, f"mcpr: {reason}", data=message["method"])
        self.log.write("s2c", "out", body, status=404, refused=reason)
        return Reply(404, body)

    async def _exchange(self, out: dict) -> tuple[int, list[dict]]:
        if self.replayer:
            return self.replayer.respond(out)
        resp = await self.upstream.send("POST", self._upstream_headers(out), out)
        responses = _parse_body(resp)
        if out["method"] == "initialize" and resp.status_code == 200:
            self.upstream_session = resp.headers.get("mcp-session-id")
        self.recorder.record(out, responses, resp.status_code)
        return resp.status_code, responses

    def _upstream_headers(self, message: dict) -> dict[str, str]:
        """Built from the message body, so headers always agree with it (no -32020)."""
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        version = modern_version(message)
        if version:
            h["mcp-protocol-version"] = version
            h["mcp-method"] = message["method"]
            param = NAME_PARAM.get(message["method"])
            params = message.get("params") or {}
            if param and param in params:
                h["mcp-name"] = encode_header_value(str(params[param]))
        elif message.get("method") != "initialize" and self.upstream_session:
            h["mcp-session-id"] = self.upstream_session
            if self.handshake_version:
                h["mcp-protocol-version"] = self.handshake_version
        return h

    def _observe(self, request: dict, response: dict) -> None:
        if "result" not in response:
            return
        if request["method"] == "initialize":
            self.handshake_version = response["result"].get("protocolVersion")
            if self.handshake_version:
                self.versions_seen.add(self.handshake_version)
            self.agent_session = uuid.uuid4().hex
        elif (v := modern_version(request)) is not None:
            self.versions_seen.add(v)

    # -- interceptor chain (SPEC §5.1); empty at M1 --------------------------

    async def _intercept_request(self, message: dict) -> dict:
        if not self.interceptors or message["method"] != "tools/call":
            return message
        out = copy.deepcopy(message)
        name, args = out["params"]["name"], out["params"].get("arguments") or {}
        for ic in self.interceptors:
            name, args = await ic.on_tool_call(name, args, self.ctx)
        out["params"]["name"], out["params"]["arguments"] = name, args
        return out

    async def _intercept_response(self, request: dict, response: dict) -> dict:
        if not self.interceptors or "result" not in response:
            return response
        out = copy.deepcopy(response)
        result, method = out["result"], request["method"]
        for ic in self.interceptors:
            if isinstance(result, dict) and result.get("resultType") == "input_required":
                result = await ic.on_mrtr(result, self.ctx)
            elif method == "server/discover":
                result = await ic.on_discover(result, self.ctx)
            elif method == "tools/list":
                result["tools"] = await ic.on_tools_list(result["tools"], self.ctx)
            elif method == "tools/call":
                result = await ic.on_tool_result(request["params"]["name"], result, self.ctx)
            elif method == "resources/read":
                result["contents"] = await ic.on_resource_read(request["params"]["uri"], result["contents"], self.ctx)
        out["result"] = result
        return out

    # -- lifecycle ----------------------------------------------------------

    def summary(self) -> dict:
        observed = sorted(self.versions_seen)
        return {
            "server": self.server,
            "trial_id": self.ctx.trial_id,
            "transport": self.ctx.transport,
            "mode": self.mode,
            "era_forced": self.ctx.era,
            "era_observed": observed[0] if len(observed) == 1 else observed,
            "era_matches": observed == [self.ctx.era],
            "refusals": self.refusals,
            "upstream_live_calls": self.upstream.calls if self.upstream else 0,
            "replay_unused": self.replayer.unused() if self.replayer else [],
            "proxy_errors": self.errors,
            "messages_logged": self.log.seq,
        }

    async def close(self) -> dict:
        summary = self.summary()
        (self.trial_dir / f"proxy.{self.server}.summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        self.log.close()
        if self.upstream:
            await self.upstream.close()
        return summary
