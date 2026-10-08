"""The block signal shared by the Relay and the mitigation interceptors (M4-T1, D53).

A leaf module (no intra-package imports) so `proxy.relay` can catch `BlockedCall`
without importing the heavier `mitigate.interceptors` module and risking a cycle.

A mitigation raises `BlockedCall` from `on_tool_call` to stop a tool call. The
Relay turns it into an `isError` tools/call result WITHOUT forwarding the call
upstream, and logs the block (SPEC §10; D53). Blocking does not touch the oracle
or the trajectory reader (D54).
"""

from __future__ import annotations


class BlockedCall(Exception):
    """Raised by a mitigation's `on_tool_call` to block the call."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"[mitigation:{kind}] {reason}")
        self.kind = kind
        self.reason = reason
