"""Description pinning (SPEC §10; D53): a tool's description is pinned to the hash it has in a
CLEAN, no-attack tools/list, and a later mismatch blocks calls to that tool.

The clean pin per server is the SHA-256 of each tool's description in the pristine mock's own
`tools/list` (`env.UPSTREAMS[server].list_tools()`). A control (no-attack) run records exactly
this list, so the pin is "taken from a clean no-attack tools/list" by construction; a test
(`test_mitigate_pinning.py`) asserts the pristine-mock pins equal a committed CLEAN control
recording's delivered descriptions, proving the two sources agree. Pins are computed once and
cached (offline, no network, $0), so nothing is re-run to obtain them.

A tool-description attack (class 2) rewrites a description the agent sees via `on_tools_list`;
pinning sees the rewritten text (the attack interceptor runs first in the chain), hashes it,
and flags a mismatch. A flagged tool's CALLS are then blocked via `BlockedCall` so the block
goes through the D56 tag path (counted as an attempt, excluded from the executed set / ASR).
An unknown tool (absent from the pins) is treated as a mismatch (fail-closed).
"""

from __future__ import annotations

import hashlib

from mcpredteam.env import UPSTREAMS

_PINS: dict[str, dict[str, str]] | None = None


def description_hash(description: str | None) -> str:
    return hashlib.sha256((description or "").encode("utf-8")).hexdigest()


async def clean_pins() -> dict[str, dict[str, str]]:
    """{server: {tool_name: sha256(description)}} from the pristine mock tools/list (cached)."""
    global _PINS
    if _PINS is None:
        pins: dict[str, dict[str, str]] = {}
        for server, build in UPSTREAMS.items():
            tools = await build().list_tools()
            pins[server] = {t.name: description_hash(t.description) for t in tools}
        _PINS = pins
    return _PINS


def mismatch(pins: dict[str, dict[str, str]], server: str, tool: str, description: str | None) -> bool:
    """True if `tool`'s delivered `description` does not match its clean pin on `server`
    (or the tool is unknown on that server -> fail-closed)."""
    server_pins = pins.get(server, {})
    if tool not in server_pins:
        return True
    return description_hash(description) != server_pins[tool]
