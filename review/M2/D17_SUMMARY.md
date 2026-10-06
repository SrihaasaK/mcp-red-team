# D17 probe results — list caching and MRTR (M2 step 1)

Evidence: `review/M2/d17_probe.json` (live, through the real M1 proxy, no model) and
the M1 recordings under `tests/recordings/m1/`. The decisive per-scaffold facts come
from the recordings; the live probe confirms the proxy relays the protocol features.

## List caching (class 4 surface)
- **Proxy fidelity:** the proxy forwards `ttlMs`/`cacheScope`/`resultType` on
  `tools/list` unchanged (observed 0->0 and 60000->60000). It does not add, strip,
  or alter cache hints.
- **Client behavior:** the modern mcp client caches a list only when `ttlMs > 0`
  (documented; observed at least once). This decision is the client's and is
  instance/timing dependent, so it is not a proxy property.
- **OpenAI Agents SDK (the M1 scaffold):** re-fetches `tools/list` every turn
  (`cache_tools_list=False`), 4 lists per multi-turn trial in the recordings. There
  is no persistent client-side list to poison -> **class 4 N/A for this scaffold as
  configured.** Enabling `cache_tools_list=True` would create the surface, but that
  changes the scaffold config and is an author decision (deferred).
- **Claude Agent SDK:** documented persistent cross-run cache (M0) -> class 4
  candidate; measured when that adapter is built (M3).

## MRTR / input_required (class 8 surface)
- **Client capability gate:** the OpenAI Agents SDK advertises **empty client
  capabilities** (modern `clientCapabilities: {}`, handshake `capabilities: {}`) in
  the recordings -> no elicitation handler -> **class 8 N/A for this scaffold.**
- **Mechanism:** SPEC §6.1.8's "malicious server returns an input_required result"
  is synthesized by an **attack interceptor** at the proxy (the attack engine, M2
  step 5), not by a mock natively eliciting. A mock's native `ctx.elicit` over the
  stateless single-POST transport fails with "no back-channel for server-initiated
  requests" (expected; captured in d17_probe.json), which is why the harness injects
  the input_required result directly. The full class-8 round-trip is validated with
  the attack engine, and only against a scaffold whose client advertises elicitation.
- **Claude Agent SDK:** elicitation support measured at M3.

## Updated conditional-class map (M2, OpenAI Agents SDK scaffold)
Report **N/A**, never 0%, where the surface is absent.
- Always (era-independent): 1 indirect injection, 2 tool-description poisoning,
  5 shadow tool, 6 resource-content injection, 7 cross-server exfil.
- Handshake-era arm only: 3 rug pull.
- **N/A for this scaffold:** 4 cache poisoning (re-fetches every turn), 8 MRTR
  injection (no elicitation capability).
This map is frozen in `PREREG.md` at step 4, after author review.
