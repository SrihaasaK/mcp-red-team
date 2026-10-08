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
# Segment separator: `_args_text` joins argument value LEAVES with it, and `_result_text` joins
# delivered content BLOCKS with it, so a k-gram never straddles two distinct leaves/blocks (D79;
# SPEC §10 counts overlap WITHIN an argument, not across an artificial join). NUL cannot occur in
# the mock text or args, so segmenting on it is lossless.
SEP = "\x00"


def k_grams(text: str, k: int = K, unit: str = "char") -> set[str]:
    """The length-k grams of `text`, lower-cased. unit='char' -> character k-grams (the live
    D58 default); unit='word' -> k-word shingles over whitespace tokens (only for the R3
    sensitivity table). Empty if the text has fewer than k units."""
    s = (text or "").lower()
    if unit == "word":
        toks = s.split()
        return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)} if len(toks) >= k else set()
    if len(s) < k:
        return set()
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def _segmented_grams(text: str, k: int, unit: str) -> set[str]:
    """Grams of `text`, computed PER SEGMENT (split on SEP) and unioned, so no gram crosses a
    leaf/block boundary (D79)."""
    out: set[str] = set()
    for seg in text.split(SEP):
        out |= k_grams(seg, k, unit)
    return out


def shares_kgram(a: str, b: str, k: int = K, unit: str = "char") -> bool:
    """True iff `a` and `b` share a length-k gram that lies WITHIN one segment of each (common
    substring for char; shared shingle for word). Segments are SEP-delimited, so a gram spanning
    two joined leaves/blocks never counts (D79). Inputs with no SEP are a single segment."""
    ga = _segmented_grams(a, k, unit)
    return bool(ga) and bool(ga & _segmented_grams(b, k, unit))


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
    sink_args_text: str, deliveries: list[tuple[str, str]], sink_server: str,
    k: int = K, unit: str = "char",
) -> str | None:
    """The first source server A (A != sink_server) whose delivered text shares a >=k-gram
    overlap with `sink_args_text`, else None (allow). Content-level; allowlist-blind. The live
    mitigation always uses the D58 default (char, k=8); unit/k vary only in the R3 sensitivity."""
    for a, text in deliveries:
        if a != sink_server and shares_kgram(sink_args_text, text, k, unit):
            return a
    return None
