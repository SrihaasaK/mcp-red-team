# M0 compatibility probe — results & evidence

Method: a throwaway `mcp` 2.3.0 logging server (`probe/logging_server.py`, speaks
both eras) fronted by an ASGI middleware that records each HTTP request's JSON-RPC
method + MCP headers. Each scaffold's **MCP client** was pointed at it (no model
call needed for OpenAI/LangGraph; the Claude Agent SDK was driven by one bounded
`claude -p` query over stored OAuth). Era is read from what the client *initiates*.

Classification rule: `initialize` request or `mcp-session-id` header ⇒ stateful
(2025-11-25); `mcp-method` header or `server/discover` with no `initialize` ⇒
stateless (2026-07-28).

## Findings (era settled empirically for all three)

| Scaffold | Package / mcp | Era INITIATED | Evidence |
|---|---|---|---|
| OpenAI Agents SDK 0.23.1 | mcp 2.3.0 | **stateless 2026-07-28** (auto, modern-first) | `openai-agents.jsonl`: POST `server/discover`, `mcp-protocol-version: 2026-07-28`, `mcp-method` header, no session id. Auto-negotiator then falls back to `initialize` (so it can also speak stateful). |
| Claude Agent SDK (bundled CLI 2.1.291) | sdk dep `mcp>=1.23,<3` | **stateless 2026-07-28** | `claude-agent-sdk.jsonl`: GET `/mcp` then POST `server/discover`, `mcp-protocol-version: 2026-07-28`, `mcp-method` header, no session id. |
| LangGraph + langchain-mcp-adapters 0.3.2 | mcp 1.30.0 | **stateful 2025-11-25 ONLY** | pin resolves mcp 1.30.0; `SUPPORTED_PROTOCOL_VERSIONS=[…,'2025-11-25']`, `LATEST=2025-11-25`, v2-only `mcp.types.version` absent; earlier traceback: `langchain_mcp_adapters/tools.py → mcp/client/session.py initialize()` (handshake). |

**Stateless-capable at M0 = 2 (OpenAI Agents SDK, Claude Agent SDK).** Stateful-capable
= all three (the two stateless ones negotiate `auto` with handshake fallback; LangGraph
natively). ⇒ SPEC §4.3 "fewer than two speak 2026-07-28" is **NOT** met → the era
comparison is retained as a headline candidate.

## Known limitations of this probe (do not affect era classification)
- My 20-line server returns an incomplete 2026-07-28 `server/discover` envelope, so the
  stateless handshake did not fully complete (OpenAI fell back to a timed-out `initialize`;
  the Claude CLI reported `CONNECT_TIMEOUT`). Era is determined by what the client
  **initiates**, which is unambiguous. The full stateless round-trip (discover → list →
  call) is validated at **M1** against the spec-complete proxy.
- The LangGraph mcp-v1 streamable client holds a persistent stream against the v2 server
  and hangs on teardown; its era is established from the version registry + traceback, not
  a clean wire capture.
- `tools/list` caching and MRTR/elicitation were **not** exercised to completion; those
  cells in DECISIONS.md are filled from documentation and confirmed at M1–M2.
- Side-facts: the Claude CLI's tool-search withheld the probe tool by default (needs
  `alwaysLoad`/tool-search off at M1); one trivial Claude query cost **$0.133** (≈30k
  cache-creation + 47k cache-read from the CLI's system prompt) — a real per-trial cost
  signal folded into the spend estimate.

Raw logs: `openai-agents.jsonl`, `claude-agent-sdk.jsonl`, `langgraph.*` in this dir.
