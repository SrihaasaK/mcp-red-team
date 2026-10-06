"""Mock upstream MCP servers. Attacks run only against these (CLAUDE.md #7)."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer

from mcpredteam.env import http_mock, notes

UPSTREAMS: dict[str, Callable[[], MCPServer]] = {
    "notes": notes.build_server,
    "http": http_mock.build_server,
}
