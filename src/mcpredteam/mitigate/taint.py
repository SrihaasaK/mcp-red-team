"""Taint-tracking decision logic (SPEC §10; D53/D58), as PURE functions so the live
Interceptors and the offline counterfactual share exactly one implementation.

Coarse (server-level): once any content from server A has been delivered in the trial, a call
to a sink tool on server B (A != B) is blocked UNLESS (A, B) is on the D58 allowlist.

Fine (content-level): a sink call is blocked when its argument text shares a >=k-gram overlap
(a common substring of length >= k) with content delivered from a DIFFERENT server. k = 8,
frozen per D58 (change only with a LOGGED dev reason). Fine does NOT consult the allowlist:
it blocks on actual content flow, so it under-blocks TRANSFORMED secrets (base64/hex/reversed
of a canary shares no plaintext k-gram with its source) -- the pre-registered prediction that
fine is weaker than coarse against transforms (PREREG (c)).

Matching is lower-cased (case-robust); no stopword/whitelist filtering (that would be tuning).
"""

from __future__ import annotations

K = 8  # fine-taint k-gram length, frozen (D58)


def k_grams(text: str, k: int = K) -> set[str]:
    """The set of length-k substrings of `text`, lower-cased. Empty if len(text) < k."""
    s = (text or "").lower()
    if len(s) < k:
        return set()
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def shares_kgram(a: str, b: str, k: int = K) -> bool:
    """True iff `a` and `b` share a common substring of length >= k."""
    ga = k_grams(a, k)
    if not ga:
        return False
    # Scan b's windows against a's gram set (avoids building both full sets).
    s = (b or "").lower()
    for i in range(len(s) - k + 1):
        if s[i:i + k] in ga:
            return True
    return False


def coarse_block_source(
    delivered_servers: set[str], sink_server: str, allowlist: frozenset[tuple[str, str]]
) -> str | None:
    """The first source server A (A != sink_server) that delivered content for which
    (A, sink_server) is NOT allowlisted, else None (allow). Server-level, content-blind."""
    for a in sorted(delivered_servers):
        if a != sink_server and (a, sink_server) not in allowlist:
            return a
    return None


def fine_block_source(
    sink_args_text: str, deliveries: list[tuple[str, str]], sink_server: str, k: int = K
) -> str | None:
    """The first source server A (A != sink_server) whose delivered text shares a >=k-gram
    overlap with `sink_args_text`, else None (allow). Content-level; allowlist-blind."""
    for a, text in deliveries:
        if a != sink_server and shares_kgram(sink_args_text, text, k):
            return a
    return None
