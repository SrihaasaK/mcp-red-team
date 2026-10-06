# M0 compatibility probe (throwaway measurement)

These scripts are **not** part of the `mcpredteam` package and import nothing
from `src/`. They exist only to establish the per-scaffold compatibility matrix
empirically at M0 (SPEC §4.2, §5.2.5): which protocol era each scaffold speaks,
whether it re-fetches `tools/list`, and how it handles input-required (MRTR).

- `logging_server.py` — an `mcp` 2.x server (speaks both eras) fronted by an
  ASGI middleware that appends one JSONL record per HTTP request (JSON-RPC
  method + MCP headers). Run: `uv run python probe/logging_server.py --port 9777`.
- `selftest_client.py` — drives the server with the in-venv `mcp` 2.x client.
- `run_matrix.sh` — installs each scaffold in its own throwaway venv and points
  its MCP client at the server (no LLM / no API key needed for the client-layer
  handshake). Results summarized in `DECISIONS.md` and raw logs kept in
  `review/M0/`.

Era classification from a log: an `initialize` request or an `mcp-session-id`
header ⇒ stateful (2025-11-25); an `mcp-method` header or a `server/discover`
request with no `initialize` ⇒ stateless (2026-07-28).

`m1_model_settings.py` (M1) sends tiny prompts to Sonnet 5.5 through the
OpenAI-compatible endpoint and the native Messages API to see which temperature
and thinking settings are accepted and whether hidden thinking is billed.
Results: `review/M1/model_settings_probe.json`; summary in DECISIONS.md D21.
