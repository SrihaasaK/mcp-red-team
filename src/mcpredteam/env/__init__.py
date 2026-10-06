"""Mock upstream MCP servers. Attacks run only against these (CLAUDE.md #7)."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer

from mcpredteam.env import files_mock, http_mock, mail_mock, notes

UPSTREAMS: dict[str, Callable[[], MCPServer]] = {
    "notes": notes.build_server,
    "http": http_mock.build_server,
    "files": files_mock.build_server,
    "mail": mail_mock.build_server,
}
