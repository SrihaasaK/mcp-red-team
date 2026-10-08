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

# D61 (option 3): the canary exists ONLY in its mock source. A corpus config carries only a
# `canary_ref` ("<server>:<resource>"); the runner resolves it against the mock env at run time
# and hands the value to the pure oracle. Attack payloads never carry the literal canary.
_CANARY_BY_SERVER = {"notes": notes.CANARY, "mail": mail_mock.CANARY, "files": files_mock.CANARY}


def source_text(canary_ref: str) -> str:
    """The text content served at ``canary_ref`` ("<server>:<resource>").

    Raises ValueError for any bad ref (unknown server OR missing resource), so callers see
    one exception type.
    """
    server, _, rid = canary_ref.partition(":")
    try:
        if server == "notes":
            n = notes.NOTES[rid]
            return f"{n.get('title', '')} {n.get('body', '')}"
        if server == "mail":
            m = mail_mock.INBOX[rid]
            return f"{m.get('subject', '')} {m.get('body', '')}"
        if server == "files":
            return files_mock.FILES[rid]
    except KeyError:
        raise ValueError(f"no resource {rid!r} on server {server!r} for {canary_ref!r}") from None
    raise ValueError(f"unknown canary_ref server in {canary_ref!r}")


def resolve_canary(canary_ref: str) -> str:
    """Resolve ``canary_ref`` to the synthetic canary planted in that source (D61).

    Verifies the canary is actually present at the referenced resource, so a mis-pointed ref
    fails loudly rather than returning a value no agent could have read.
    """
    server = canary_ref.partition(":")[0]
    try:
        canary = _CANARY_BY_SERVER[server]
    except KeyError:
        raise ValueError(f"no canary source for server in {canary_ref!r}") from None
    if canary not in source_text(canary_ref):
        raise ValueError(f"canary for {server!r} is not present at {canary_ref!r}")
    return canary
