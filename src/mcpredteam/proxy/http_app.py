"""Streamable HTTP face of the proxy: one hub serving many trials.

Route: `/t/<trial_id>/<server>/mcp`. POST carries JSON-RPC and always gets an
`application/json` reply. GET (the handshake era's server-to-client stream) gets
405: the mocks never push messages, and the client gives up after two retries.
DELETE ends the upstream session.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Self

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcpredteam.attacks.engine import build_attack
from mcpredteam.proxy.interfaces import TrialCtx
from mcpredteam.proxy.relay import Relay, jsonrpc_error
from mcpredteam.scaffolds.contract import TrialSpec


def ctx_for(spec: TrialSpec) -> TrialCtx:
    return TrialCtx(
        run_id=spec.run_id,
        trial_id=spec.trial_id,
        era=spec.era,
        scaffold=spec.scaffold,
        model=spec.model,
        transport=spec.transport,
        task_id=spec.task_id,
    )


class ProxyHub:
    def __init__(self) -> None:
        self.specs: dict[str, TrialSpec] = {}
        self.relays: dict[tuple[str, str], Relay] = {}
        self._lock = asyncio.Lock()

    def register(self, spec: TrialSpec) -> None:
        self.specs[spec.trial_id] = spec

    async def relay(self, trial_id: str, server: str) -> Relay | None:
        spec = self.specs.get(trial_id)
        if spec is None or server not in spec.servers:
            return None
        async with self._lock:
            key = (trial_id, server)
            if key not in self.relays:
                attack = spec.attack_config
                on_target = bool(attack) and (attack.get("target") or {}).get("server") == server
                interceptors = (build_attack(attack),) if on_target else ()
                relay = Relay(
                    server=server,
                    ctx=ctx_for(spec),
                    mode=spec.mode,
                    trial_dir=Path(spec.trial_dir),
                    recording_dir=Path(spec.recording_dir) if spec.recording_dir else None,
                    interceptors=interceptors,
                )
                await relay.start()
                self.relays[key] = relay
            return self.relays[key]

    async def close_trial(self, trial_id: str) -> list[dict]:
        summaries = []
        for key in [k for k in self.relays if k[0] == trial_id]:
            summaries.append(await self.relays.pop(key).close())
        self.specs.pop(trial_id, None)
        return summaries

    def app(self) -> Starlette:
        async def endpoint(request: Request) -> Response:
            relay = await self.relay(request.path_params["trial_id"], request.path_params["server"])
            if relay is None:
                return JSONResponse(jsonrpc_error(None, -32601, "mcpr: unknown trial or server"), status_code=404)
            if request.method == "GET":
                relay.note_get()
                return Response(status_code=405, headers={"allow": "POST, DELETE"})
            if request.method == "DELETE":
                await relay.terminate()
                return Response(status_code=200)
            try:
                message = json.loads(await request.body())
            except json.JSONDecodeError:
                return JSONResponse(jsonrpc_error(None, -32700, "Parse error"), status_code=400)
            if isinstance(message, list):
                return JSONResponse(jsonrpc_error(None, -32600, "mcpr: batches are not supported"), status_code=400)
            reply = await relay.handle(message, dict(request.headers))
            if reply.body is None:
                return Response(status_code=reply.status, headers=reply.headers)
            return JSONResponse(reply.body, status_code=reply.status, headers=reply.headers)

        return Starlette(routes=[Route("/t/{trial_id}/{server}/mcp", endpoint, methods=["GET", "POST", "DELETE"])])


class HubServer:
    """Runs the hub on 127.0.0.1 with an OS-assigned port, inside the caller's event loop."""

    def __init__(self, hub: ProxyHub) -> None:
        self.hub = hub
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(hub.app(), log_level="warning", lifespan="off"))
        self._task: asyncio.Task | None = None

    def url(self, trial_id: str, server: str) -> str:
        return f"http://127.0.0.1:{self.port}/t/{trial_id}/{server}/mcp"

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        while not self.server.started:
            if self._task.done():
                self._task.result()
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.server.should_exit = True
        if self._task:
            await self._task
