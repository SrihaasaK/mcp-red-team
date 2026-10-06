"""M0 compatibility probe — wire-logging MCP server (throwaway measurement).

NOT part of the shippable package: imports nothing from `src/mcpredteam`. It
exists only to observe, at the wire, which protocol era each scaffold speaks,
whether it re-fetches tool lists, and how it reacts to an input-required (MRTR)
tool result. Raw observations are appended as JSONL; analysis/classification is
done separately. See probe/README.md and DECISIONS.md §"Compatibility matrix".
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import uvicorn
from mcp.server import MCPServer

LOG_PATH = os.environ.get("PROBE_LOG", "probe/_logs/wire.jsonl")
# Tool-call behavior knob: when set, `echo` returns an input_required (MRTR) result.
MRTR_MODE = os.environ.get("PROBE_MRTR", "0") == "1"

_INTERESTING = ("mcp-", "content-type", "accept", "last-event-id")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WireLogMiddleware:
    """Pure-ASGI middleware: buffer body, record JSON-RPC method + MCP headers, replay."""

    def __init__(self, app, log_path: str) -> None:
        self.app = app
        self.log_path = log_path
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        buffered: list[dict] = []
        body = b""
        while True:
            msg = await receive()
            buffered.append(msg)
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        method = None
        try:
            data = json.loads(body) if body else None
            if isinstance(data, dict):
                method = data.get("method")
            elif isinstance(data, list):
                method = [d.get("method") for d in data if isinstance(d, dict)]
        except Exception:
            method = "<unparsable>"

        headers = {}
        for k, v in scope.get("headers", []):
            name = k.decode("latin1").lower()
            if name == "authorization":
                headers[name] = "<redacted>"
            elif any(name.startswith(p) or name == p for p in _INTERESTING):
                headers[name] = v.decode("latin1")

        rec = {
            "ts": _now(),
            "http_method": scope.get("method"),
            "path": scope.get("path"),
            "jsonrpc_method": method,
            "headers": headers,
            "has_session_id": "mcp-session-id" in headers,
            "has_mcp_method_header": "mcp-method" in headers,
            "has_mcp_name_header": "mcp-name" in headers,
        }
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

        sent = iter(buffered)

        async def replay():
            try:
                return next(sent)
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)


def build_app(log_path: str):
    server = MCPServer(name="mcpr-probe", version="0.0.1")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the input text back (probe tool)."""
        return text

    @server.tool()
    def two(a: int, b: int) -> int:
        """Return a+b (second tool, so tools/list is non-trivial)."""
        return a + b

    app = server.streamable_http_app()
    return WireLogMiddleware(app, log_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9777)
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()
    uvicorn.run(build_app(args.log), host="127.0.0.1", port=args.port, log_level="warning")
