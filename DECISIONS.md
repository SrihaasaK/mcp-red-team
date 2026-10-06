# DECISIONS.md

Append-only log of architectural decisions, each with its rationale and the
rejected alternative (CLAUDE.md #9). Newest milestone appended at the end.

---

# M0 — Design, environment, compatibility (2026-10-06)

Milestone scope (SPEC §12): design + environment + compatibility only. **No M1
code** (no proxy, attack, oracle, or mitigation behavior). The package ships
design interfaces and CLI stubs only. Acceptance = every item below present
here; then stop for review.

## D1. One-page design

### Threat model (restated, SPEC §2)
- **Attacker** controls exactly one upstream MCP server, or poisons data inside
  one otherwise-benign server's data source (a note, file, or resource).
- **Trusted:** the user, the agent scaffold, the model provider, all other
  upstream servers.
- **Attacker goals:** exfiltrate a secret, trigger a forbidden action, hijack
  the final answer, persist across turns.
- **Out of scope (v1):** compromised clients, network MITM, malicious gateways,
  model-weight attacks, adaptive attackers who craft payloads against the
  deployed defense. The proxy simulates the attacker; every attack class maps to
  this model.

### Module boundaries (`src/mcpredteam/…`, SPEC §11)
- `proxy/` — transparent MCP proxy: MCP **server** to the scaffold, MCP
  **client** to the mock upstreams; runs the interceptor chain; logs every
  message to JSONL keyed by run/trial. Holds `interfaces.py` (M0 design).
- `attacks/` — attack interceptors + the published corpus loader.
- `mitigate/` — mitigation interceptors (pinning, spotlighting, taint).
- `env/` — mock upstream servers (`notes`, `http`, later `files`, `mail`).
- `oracle/` — deterministic, objective verdict (no LLM judge).
- `scaffolds/` — adapters (Claude Agent SDK, LangGraph, OpenAI Agents SDK),
  each wrapping the LLM client for record/replay + exposure.
- `runner/` — task × attack matrix driver, trial sampling, control arm.
- `report/` — ASR / exposure / utility tables, CIs, `<<...>>` filling.

### The core interface (SPEC §5.1) — symmetry is the design
Attacks and mitigations are **both** `Interceptor`s, chained in order over one
message pipeline. There is no second mechanism. Implemented as a `typing.Protocol`
in `src/mcpredteam/proxy/interfaces.py` with the seven hooks `on_discover`,
`on_tools_list`, `on_tool_call`, `on_tool_result`, `on_resource_read`,
`on_mrtr` (2026-07-28 only), `on_final_answer`.

### `TrialCtx` (SPEC §4.2)
Per-trial context keyed by `(run_id, trial_id)` — **never** by an MCP protocol
session — so one proxy serves both eras uniformly and logging is deterministic.
M0 design shape (`src/mcpredteam/proxy/interfaces.py`): `run_id, trial_id, era,
scaffold, model, transport, task_id?, attack_config_id?, state: dict`. `state`
is interceptor scratch space (taint sets, call counts, pins) scoped to a trial.

### Dual-era plan
Support both the stateful `2025-11-25` handshake era and the stateless
`2026-07-28` era with one pinned SDK (D2). Every trial is tagged with its era.
Proxy-internal state lives in `TrialCtx`, not a protocol session, so the
stateless era (no session) is handled with the same code path. See D7 for the
era-comparison decision.

### Rejected design alternatives (D1)
(a) Two parallel mechanisms for attacks vs mitigations — rejected for the single
`Interceptor` chain (SPEC §5.1). (b) Keying proxy state by MCP session — rejected
for `TrialCtx` keyed by run/trial, so the sessionless stateless era runs on the
same path.

## D2. Environment & pinned SDK
- **Decision:** Python 3.12 managed by `uv` (installed 0.12.23; interpreter
  3.12.14). Runtime deps pinned `mcp>=2.3,<3`, `typer>=0.12`, `pydantic>=2`;
  dev `pytest>=8`, `ruff>=0.6`. Resolved: **mcp 2.3.0 + mcp-types 2.3.0**
  (released 2026-10-02). Lockfile `uv.lock` committed.
- **Dual-era support verified** (SPEC §3) by reading the SDK's own version
  registry — `uv run python -c "from mcp.types.version import *"`:
  ```
  KNOWN      = ('2024-11-05','2025-03-26','2025-06-18','2025-11-25','2026-07-28')
  HANDSHAKE  = ('2024-11-05','2025-03-26','2025-06-18','2025-11-25')
  MODERN     = ('2026-07-28',)
  LATEST     = 2026-07-28   LATEST_HANDSHAKE = 2025-11-25   LATEST_MODERN = 2026-07-28
  ```
  `2025-11-25 ∈ HANDSHAKE` and `2026-07-28 ∈ MODERN` ⇒ one pin speaks both eras.
- **API note (affects M1):** `mcp` 2.x renamed `FastMCP`→`mcp.server.MCPServer`,
  added a first-class `mcp.Client`, and depends on `httpx2`. Mock upstreams (M1)
  use `MCPServer`; the proxy's upstream client uses `mcp.Client`. All four
  transports present (`client/server × stdio/streamable_http`); HTTP+SSE is
  deprecated as of 2026-07-28 and will not be used.
- **Red-team caveat:** the 2.x client auto-negotiates and may normalize
  malformed handshake/modern requests. Attacks that need raw/malformed wire
  bytes (e.g. header/body games) may have to drive the transport directly rather
  than through the high-level client. Flagged for M2.
- **Rejected:** `mcp>=1,<2` (latest 1.30.0) — stateful-only (`LATEST=2025-11-25`,
  no `MODERN`); would forfeit the stateless era entirely.

## D3. License — MIT
- **Decision:** MIT (`LICENSE`, `pyproject` `license="MIT"` + `license-files`).
- **Rationale:** author preference; shortest, maximally permissive, lowest
  friction for a portfolio artifact.
- **Rejected:** Apache-2.0 — its explicit patent grant / contributor terms are
  valuable for corporate adoption, but not required here; MIT chosen for brevity.

## D4. Package & CLI name
- **Decision:** PyPI package `mcpredteam`; CLI command `mcpr` (entry point
  `mcpredteam.cli:main`).
- **Confirmed free on PyPI** (2026-10-06, `GET /pypi/<name>/json` → 404):
  `mcpredteam`, `mcp-red-team`, `mcpr`. Confirmation only — reservation/publish
  is M4. (A placeholder upload is the only hard reservation, and PyPI's
  anti-typosquat check can still reject at upload.)
- **Rejected:** `mcp-red-team` as the import/package name — kept the no-hyphen
  `mcpredteam` to match `src/` and the import path; the hyphenated form
  normalizes to the same project and is held in reserve.

## D5. Model manifest (FROZEN, SPEC §8.6)
Printed in every report; changed only with a logged reason + full re-run.

| Role | Model id | \$/Mtok in | \$/Mtok out | Notes |
|---|---|---|---|---|
| Frontier (strong) | `claude-opus-5-5` | 4 | 20 | verified (Anthropic) |
| Frontier (mid)    | `claude-sonnet-5-5` | 2 | 10 | verified (Anthropic) |
| Iteration / full-factorial | Ollama `qwen3:14b` | 0 | 0 | local; dependable tool-calling (`qwen3:8b` = weaker fallback) |

- **Caveats recorded:** Claude 4.7+ tokenizer emits ~30% more tokens for the same
  text; Batch API is −50%; prompt-cache reads ≈10% of input price. These change
  cost but not the ids.
- **Rationale:** Anthropic-only keeps one provider key, verified ids/prices, and
  two capability tiers under the spend guard (author decision).
- **Rejected:** (a) cross-provider OpenAI tier — OpenAI ids/prices were
  unverifiable at M0 and need a second key; excluded from the frozen manifest.
  (b) Haiku 4.5 — retirement "not before Oct 15 2026" is a risky long-lived pin.
  (c) Fable 5.1 — unnecessary cost tier. Ollama is required by SPEC §6.4 for the
  free full-factorial run.
- **Price re-confirmation:** ids/prices are re-confirmed against live Anthropic
  pricing when `PREREG.md` is frozen (SPEC §8.6); a change there forces a full
  re-run of published numbers.

## D6. Compatibility matrix (EMPIRICAL probe)
Method + raw evidence: `review/M0/PROBE_SUMMARY.md` and the `*.jsonl` /
`langgraph.version.txt` beside it. Each scaffold's MCP **client** was pointed at
a throwaway logging MCP server; era is read from what the client *initiates*.

| Scaffold | pkg / mcp | Era initiated | Transports | tools/list caching | MRTR / elicit | LLM-wrap (record/replay + exposure) | temp / seed |
|---|---|---|---|---|---|---|---|
| OpenAI Agents SDK 0.23.1 | mcp 2.3.0 | **stateless 2026-07-28** (auto, modern-first; falls back to `initialize`) [V] | stdio, Streamable HTTP, SSE(legacy) [D] | `cache_tools_list` default **off** ⇒ re-fetch [D] | `message_handler` hook exists; MRTR unconfirmed [U] | `set_default_openai_client(base_url=…)` / custom `ModelProvider` / tracing [D] | temp via `ModelSettings`; seed via `extra_args` (Chat Completions) [D] |
| Claude Agent SDK (CLI 2.1.291) | sdk dep `mcp>=1.23,<3` | **stateless 2026-07-28** [V] | stdio, http, sse, in-process [D] | persistent cross-run cache; lazy connect; **tool-search on by default withholds tools** [D/V] | undocumented, unconfirmed [U] | `ANTHROPIC_BASE_URL` via `env`; `PostToolUse` hook [D] | **not exposed** (model/effort/thinking only) [D] |
| LangGraph + langchain-mcp-adapters 0.3.2 | mcp 1.30.0 | **stateful 2025-11-25 only** [V] | stdio, streamable_http, sse [D] | re-fetch per `get_tools`; no 2026 `ttlMs`/`cacheScope` (v1) [D] | none (v1) [D] | LangChain callbacks; `base_url`/`http_client` [D] | temp yes; seed provider-dependent (OpenAI yes, Anthropic no) [D] |

Legend: [V] verified empirically this M0; [D] documented; [U] unknown / defer to M1–M2.

- **Scaffold-choice note:** `langchain-mcp-adapters` 0.3.2 is pinned for M1 (its
  repo is archived; MCP has moved to the beta `langchain[mcp]`, the only path
  that could give LangGraph the stateless era — deferred, out of M0 scope).
- **Caching/MRTR cells are documented, not probed to completion** (my toy
  server's discover envelope was incomplete); confirmed at M1–M2.
- **Era attribution:** the Claude Agent SDK's wire era is set by the bundled
  Claude Code CLI (TS, 2.1.291), not the Python `mcp` dep range shown in the
  table; its era cell comes from the CLI wire capture
  (`review/M0/claude-agent-sdk.jsonl`).

## D7. Stateless-fallback decision (SPEC §4.3) — fallback NOT triggered
- **Finding:** **two** scaffolds initiate the stateless `2026-07-28` era (OpenAI
  Agents SDK, Claude Agent SDK); LangGraph is stateful-only. Stateful-capable =
  all three (the two modern ones negotiate `auto` with handshake fallback).
- **Decision:** since **not** "fewer than two speak 2026-07-28", the era
  comparison is **retained as a headline candidate**, not dropped. The stateless
  cohort is {OpenAI, Claude Agent SDK}; the stateful cohort is all three.
- **Caveat (honest):** M0 confirmed era *initiation*, not a full stateless
  round-trip (discover → list → call) — my probe server returned an incomplete
  discover envelope. The round-trip is validated at **M1** against the
  spec-complete proxy. If M1 shows a stateless scaffold cannot actually complete
  a session, this decision is revisited and logged.
- **Residual risk:** OpenAI's client is modern-first but falls back to
  `initialize` on any hiccup; if at M1 only the Claude Agent SDK sustains a
  stateless-only session, the stateless cohort collapses to one and §4.3 *does*
  trigger. README era-status stays provisional until M1 shows a full stateless
  round-trip.

## D8. Conditional-class map (origin at M0, SPEC §6.1/§8.6)
Which of the 8 attack classes run where. Absent-behavior cells report **N/A**,
never 0% (SPEC §6.1).
- **Era-independent (all three scaffolds):** (1) indirect injection in tool
  results, (2) tool-description poisoning, (5) shadow tool, (6) resource-content
  injection, (7) cross-server exfil chain.
- **(3) Rug pull — stateful:** stateful-era arm only (LangGraph; stateful runs of
  OpenAI/Claude). N/A in the stateless arm.
- **(4) Cache poisoning — stateless, conditional:** stateless scaffolds that
  re-fetch a cacheable list. Prime candidate **Claude Agent SDK** (persistent
  cache); OpenAI only if `cache_tools_list` is enabled. Confirmed at M2; else N/A.
- **(8) MRTR injection — stateless, conditional:** only where MRTR handling is
  confirmed. **Unconfirmed at M0 for all three ⇒ N/A until confirmed** at M1/M2.
- Excluded entirely (SPEC §6.2): gateway header/body desync — not an attack on
  the agent; lives in a separate `gateway-conformance` suite if ever built.

## D9. Spend estimate for M2–M4 (SPEC §12; guard CLAUDE.md #8 = \$150)
Assumptions (list prices, prompt-caching on system prompts): lean scaffolds
(OpenAI, LangGraph) ≈ 12k in + 2k out per trial ⇒ ~\$0.05 (Sonnet) / ~\$0.09
(Opus). **Claude Agent SDK is markedly costlier** — a measured trivial probe
query cost **\$0.133** (≈30k cache-creation + 47k cache-read from the CLI's system
prompt); a real multi-turn trial ≈ \$0.15–0.30 (Sonnet) / \$0.30–0.60 (Opus).
Published suite 40 configs → dev 28 (n=5), held-out 12 (n=10). Ollama
full-factorial = free.

- **M2** (dev tables, 1 lean scaffold × 2 models, + control arm): ≈ \$30–50.
- **M3** (benign suite, 3 scaffolds × 2 models, once then replayed free): ≈ \$30–50.
- **M4 — FULL held-out grid** — transparent derivation (trial = one
  (config, compatible-task) sample, SPEC §6.5; n = per-cell sample count):
  - attack trials = scaffolds 3 × models 2 × held-out configs 12 × n 10 ×
    conditions 7 (baseline + pinning + spotlight-delims + spotlight-strip +
    taint-coarse + taint-fine + combined) = **5,040**.
  - utility/control arm = 3 × 2 × ~40 benign × 7 conditions, n=1 ≈ **1,680**
    (≈ 8,400 at n=5).
  - subtotal ≈ **6,700–13,400 trials**, cost-skewed by scaffold: the two lean
    scaffolds ≈ \$0.05 (Sonnet)/\$0.09 (Opus) per trial; the Claude Agent SDK
    ≈ \$0.20–0.45 (measured CLI prompt overhead). Blended ≈ **\$0.8k–1.5k —
    infeasible, well over the \$150 guard.** (An optional both-eras run for the
    stateless cohort roughly adds those scaffolds' attack trials again.)
- **Recommended affordable design (target ≤ \$150; confirm at M2 start):**
  Sonnet 5.5 as the sweep model; the two **lean** scaffolds for the full held-out
  sweep (2 × 12 × n10 × 7 ≈ 1,680 attack + ~560 utility ≈ 2,240 trials × ~\$0.05 ≈
  **\$110**); the Claude Agent SDK only in a confirmatory slice (baseline +
  combined, n=5 ≈ 120 trials × ~\$0.20 ≈ **\$24**); Opus 5.5 as a held-out
  **spot-check** on baseline + best-mitigation only (≈ 480 trials × ~\$0.09 ≈
  **\$43**); n=10 held-out / n=5 dev. Projected ≈ **\$110–180** — if the measured
  figure tops \$150, drop the Opus spot-check or trim conditions.
- **GUARD ACTION:** the full design exceeds \$150, and even the lean design can
  graze it. Per CLAUDE.md #8, the exact per-trial token count is measured at the
  start of M2 and, if the projected frontier spend exceeds **\$150**, work
  **stops for an author decision on scope** before any M2 run — no silent
  downscoping.

## D10. Probe methodology, limitations, and M0 spend log
- Throwaway probe lives in `probe/` (its own venvs, git-ignored); it imports
  nothing from `src/mcpredteam` and is **measurement, not M1 proxy code** — a
  scope judgment recorded here per CLAUDE.md #10.
- Limitations (do not affect the era conclusions): incomplete discover envelope
  on the toy server; LangGraph v1 streamable client hangs on teardown; caching
  and MRTR not exercised to completion. All re-validated at M1–M2.
- **M0 model spend logged (CLAUDE.md #8):** one bounded Claude Agent SDK probe
  query over stored OAuth = **\$0.1332514** (Sonnet 5.5). No other model spend.

## M0 acceptance checklist (SPEC §12)
- [x] One-page design (modules, `Interceptor` symmetry, `TrialCtx`, dual-era,
  threat model) — D1 + `src/mcpredteam/proxy/interfaces.py`.
- [x] Package skeleton — `src/mcpredteam/{proxy,attacks,env,oracle,scaffolds,
  runner,report,mitigate}/`, CLI stubs, data dirs.
- [x] `uv` env + pinned SDK (mcp 2.3.0) + `uv.lock` — D2.
- [x] `.env.example` — committed.
- [x] License chosen (MIT) — D3 + `LICENSE`.
- [x] Package name confirmed free on PyPI — D4.
- [x] Model manifest frozen — D5.
- [x] Compatibility matrix per scaffold (eras, caching, MRTR) — D6 + `review/M0/`.
- [x] Stateless-fallback decision recorded (§4.3) — D7.
- [x] Conditional-class map — D8.
- [x] Spend estimate M2–M4 (+ \$150-guard action) — D9.
- [x] Reviewer pass — see **D-review** below (no blocking issues).
- **STOP for review. No M1 code.**

## D-review. Reviewer pass (CLAUDE.md #11)

A subagent reviewed `DECISIONS.md`, the skeleton, `interfaces.py`, and the probe
evidence against SPEC §12 M0 and the CLAUDE.md non-negotiables, verifying the
load-bearing claims against the installed `mcp` 2.3.0.

**Verdict: no BLOCKING issues; M0 acceptable.** Independently confirmed: the
dual-era registry (D2), the `FastMCP`→`MCPServer` shim + top-level `mcp.Client`,
the `mcp>=2.3,<3` pin→2.3.0 in `uv.lock`, no M1 behavior in `src/` (interfaces +
CLI stubs only), the probe scoped as measurement (imports nothing from `src/`),
the logged M0 spend (\$0.1332514), the §5.1/§4.2 interface fidelity, the §6.1
conditional-class mapping (N/A not 0%; §6.2 exclusion), and the 70/30 split math.
The §4.3 call was judged sound and honestly hedged (era *initiation* confirmed,
round-trip deferred to M1).

Advisories applied in this commit: **A1** D9 trial-count made a transparent
derivation (the earlier "≈47k / \$5–8k" was inflated; corrected to ≈6.7–13.4k
trials / ≈\$0.8–1.5k, guard conclusion unchanged); **A2** OpenAI-fallback residual
risk added to D7; **A3** Claude era-attribution clarified in D6; **A4** price
re-confirmation-at-PREREG note added to D5; **A6** explicit rejected alternatives
added to D1. Advisory **A4/A5** (live price re-confirmation; same-commit landing)
are honored at PREREG freeze and at this commit respectively.

---

# M0 closeout (2026-10-06)

## D11. Author decisions at M0 acceptance
The author accepted M0 and made these decisions, recorded as listed:
1. **Spend guard held at \$150** (CLAUDE.md #8).
2. **Sonnet 5.5 only**; the Opus 5.5 spot-check is optional.
3. **Pinning and spotlighting run only against their target classes**; baseline,
   coarse taint, fine taint and combined run on all classes.
4. **Headline scope:** the two full-sweep scaffolds (OpenAI Agents SDK,
   LangGraph) plus a Claude Agent SDK confirmatory slice.
5. **Era comparison is exploratory**, not headline; the dual-era proxy stays.
6. **Resume line rephrased** to match. The `<<…>>` placeholders stay unfilled
   until `mcpr report` fills them (CLAUDE.md #12):

   ```text
   Built an open-source dynamic red-team harness for MCP agents; measured attack success across 2 agent scaffolds on <<MODEL>>, with a confirmatory slice on the Claude Agent SDK; an information-flow mitigation layer cut held-out ASR from <<X>>% to <<Y>>% with a benign-utility change of <<U>> pts (95% CI).
   ```

Notes:
- **Target classes.** SPEC §10 says pinning targets rug pull (3) and cache
  poisoning (4). It names spotlighting's mechanism but not its classes, so
  spotlighting's target set is fixed in `PREREG.md` before any M2 result.
- **Supersedes:** D7's "retained as a headline candidate" (the era comparison is
  now exploratory by author decision, not by the §4.3 trigger, and the README's
  Limitations section says so, SPEC §14); D9's recommended design (this design is
  a subset of it, so D9's Sonnet figures are an upper bound, and the exact figure
  is still measured at M2 start under D9's guard action); and SPEC §1's
  resume-line template. `docs/SPEC.md` itself is unchanged.
- **Open before M2 (author):** SPEC §12's M2 acceptance renders the dev table for
  "1 scaffold × 2 models". With Sonnet 5.5 as the only frontier model, the second
  model is either the Ollama iteration model (`qwen3:14b`, already in the
  manifest, free) or the optional Opus spot-check. The acceptance check itself is
  not edited (CLAUDE.md #3).
- **Session scope:** no override of CLAUDE.md #2. This session closes M0; M1
  starts in a fresh session.
- **Pre-M1 fixes from the author's review:** fixes 1–6 are D12–D17. Fix 7 is the
  rendering fix below.
- **Rendering fix (pre-M1 fix 7, applied in place).** Paired dollar signs were
  parsed as inline math and swallowed D9 text. Every dollar sign outside code in
  this file is now escaped as `\$` (D-review also had paired dollars). No number
  changed: the file matches its previous version once the escapes are removed.
  The bold markers were already paired; with the dollars escaped, every bold
  span renders.

## D12. One uv project per scaffold (pre-M1 fix 1)
- **Decision:** each scaffold adapter is an independent uv project under
  `scaffolds/<name>/`, with its own `pyproject.toml` and `uv.lock`, and is
  **not** a uv workspace member. The runner launches it as a subprocess, and it
  talks to the proxy over the wire (stdio or Streamable HTTP).
- **Rationale:** `langchain-mcp-adapters` 0.3.2 pins `mcp` 1.30.0 while the
  project pins `mcp` 2.3.0, and one lockfile can't hold both. The M0 probe
  already ran each scaffold in its own venv for this reason (`probe/.venv-*`).
- **Updated layout.** This supersedes D1's `scaffolds/` bullet; SPEC §11 is
  unchanged.
  ```text
  src/mcpredteam/{proxy,attacks,env,oracle,runner,report,mitigate}/  harness (mcp 2.3.0)
  src/mcpredteam/scaffolds/    runner-side launch and result contract only
  scaffolds/openai-agents/     independent uv project (M1)
  scaffolds/langgraph/         independent uv project, mcp 1.30.0 (M3)
  scaffolds/claude-agent-sdk/  independent uv project (M3)
  ```
- **Contract (defined at M1):** the runner gives the subprocess the proxy
  endpoint, era, task and trial IDs. The adapter returns its final answer and its
  recording of model requests and responses.
- **Rejected:** one uv workspace holding every scaffold. Its members share one
  lockfile and environment, so the two `mcp` majors can't be installed side by
  side, and separate projects keep each scaffold's dependency tree out of the
  harness.

## D13. OpenAI Agents SDK on the Anthropic-only manifest (pre-M1 fix 2; M1)
- **Decision:** the OpenAI Agents SDK adapter routes Sonnet 5.5 through a path
  the installed SDK supports.
- **Options in openai-agents 0.23.1 (checked at M0 close):**
  `OpenAIChatCompletionsModel` with a custom `AsyncOpenAI` client pointed at
  Anthropic's OpenAI-compatible endpoint, available now; or `LitellmModel`, which
  needs the `openai-agents[litellm]` extra (`litellm` isn't installed). The M1
  adapter picks one and records why here.
- **M1 gate:** verify tool-calling parity on the 5 benign tasks. If it fails, log
  the failure here and **stop**: changing the manifest is an author decision.
- **Known limit:** per D6, Anthropic has no seed parameter, so seed is reported
  as not fixable (SPEC §5.2, item 4). Temperature is set to 0 where the endpoint
  accepts it (SPEC §8.3).
- **Rejected:** driving this scaffold with an OpenAI model. That would break the
  Anthropic-only manifest (D5), which only the author can change.

## D14. Claude Agent SDK tool search (pre-M1 fix 3; M3)
- **Finding:** tool search is on by default (documented, D6). In the M0 probe the
  model looked tools up through search instead of receiving the MCP tool
  definitions up front.
- **Why it matters:** it changes exposure for tool-description poisoning (2) and
  shadow tool (5), whose payloads sit in tool descriptions.
- **M3 requirement:** disable tool search for harness runs if the SDK allows it.
  Otherwise, record per trial whether descriptions were withheld, and let the
  exposure metric (measured at the LLM-request boundary, SPEC §8.2) account for
  it.
- **Rejected:** leaving the default unrecorded. A description-borne attack whose
  payload never reached the model would then read as a failed attack.

## D15. Claude Agent SDK model-call recording (pre-M1 fix 4; M3)
- **Decision:** for the Claude Agent SDK, the LLM wrap is an HTTP recording proxy
  set as `ANTHROPIC_BASE_URL`, not an in-process wrapper. The model calls come
  from the bundled CLI subprocess, which Python-level wrapping can't reach.
- It records every model request and response for replay (SPEC §9.1) and
  exposure (SPEC §8.2), and serves the recordings back during replay.
- **Scope:** built in M3 with that adapter. Not M1.
- **Rejected:** SDK hooks such as `PostToolUse`. They see tool results, not the
  full model request, so they can't establish exposure or replay model responses.

## D16. M1 covers both eras with one adapter (pre-M1 fix 5; M1)
- **Decision:** the M1 scaffold is the OpenAI Agents SDK only. It exercises both
  eras: the proxy forces each one by advertising only that era, in a
  stateless-only run and a handshake-only run, so one adapter can meet replay
  equality in both (SPEC §12, M1 acceptance).
- **Evidence the client will follow (M0 probe):** it is modern-first
  (`server/discover`), and its negotiator falls back to `initialize` after an RPC
  error (`negotiate_auto` in `mcp/client/_probe.py`). In a handshake-only run the
  proxy answers `server/discover` the way a handshake-era server would, which
  triggers that fallback.
- **M1 requirement:** in a stateless-only run the proxy rejects `initialize`, so
  a fallback fails loudly instead of quietly changing era. Each trial records the
  era actually negotiated on the wire and asserts it equals the forced era.
- **Supersedes:** D6's note pinning `langchain-mcp-adapters` 0.3.2 for M1. The
  LangGraph adapter moves to M3, still at 0.3.2 (D12).
- **Rejected:** a second M1 adapter for the handshake era. Forcing the era at the
  proxy covers both with one adapter, so there's less to get right before replay
  is proven.

## D17. M2 starts by verifying caching and MRTR (pre-M1 fix 6; M2)
- **Status:** D6's caching and MRTR cells are documented, not verified.
- **Decision:** the first M2 task completes those probes against the real proxy:
  tool-list re-fetch across runs, handling of `ttlMs`/`cacheScope`, and the
  reaction to an `input_required` result. It covers all three scaffolds, at the
  MCP-client level as in M0 where an adapter doesn't exist yet.
- The conditional-class map (D8), updated with those results, goes into
  `PREREG.md` before any result exists (SPEC §8.6). Classes whose behavior is
  absent report N/A, never 0%.
- **Rejected:** freezing D8 as documented. Classes (4) and (8) would then rest on
  documentation instead of measured behavior.

---

# M1 (begun 2026-10-06)

## D18. qwen3:14b via Ollama as M2's second model (author decision; superseded by D19 before taking effect)
- **As given:** M2's second model is `qwen3:14b` through Ollama's
  OpenAI-compatible endpoint, on the same `OpenAIChatCompletionsModel` path as
  D13. It also runs the held-out sweep for the two lean scaffolds at no API cost.
  Sonnet 5.5 stays the frontier headline model, and the Claude Agent SDK slice is
  Sonnet-only. To go into `PREREG.md` at M2. During M1, per-trial wall-clock was
  to be measured for both models, with a sampled design proposed if the Ollama
  runtime projection exceeded 48 hours.
- **Status:** superseded by D19 the same day, before any of it was implemented.
  Ollama was never installed.

## D19. Budget change: \$375 guard, Ollama dropped, Opus as second model (author decision)
Supersedes D18 and the Opus/Ollama parts of D11.
- **Spend guard** raised from \$150 to \$375 for this project (CLAUDE.md #8
  updated). About \$125 of the author's \$500 in API credits (expiring in about
  six months) is reserved for other projects and is never assumed available here.
- **Ollama is dropped.** Nothing is installed.
- **Models.** Sonnet 5.5 runs the held-out sweep under all 7 conditions. Opus 5.5
  is M2's second model and runs the held-out sweep on the two lean scaffolds
  under 4 conditions (baseline, coarse taint, fine taint, combined). The Claude
  Agent SDK slice stays Sonnet-only. D11 decision 3 still applies to Sonnet's
  seven conditions: pinning and spotlighting run only against their target
  classes.
- **SPEC §6.4 amended:** the full factorial becomes a sampled design of about
  1,000 trials on Sonnet 5.5. The sampling scheme is proposed here at M2 and
  frozen in `PREREG.md`. `docs/SPEC.md` §6.4 now says so.
- **Manifest (supersedes D5's iteration tier):** there is no local tier;
  iteration runs on Sonnet 5.5. The frontier ids and prices are unchanged:
  `claude-sonnet-5-5` (\$2 in / \$10 out per Mtok) and `claude-opus-5-5`
  (\$4 / \$20). D5 is left as written (this file is append-only); this entry is
  the current manifest.
- **Cost measurement.** Real per-trial cost is measured during M1 and M2. M2–M4
  is re-projected before `PREREG.md` is frozen, and work stops for an author
  decision if the projection exceeds \$375.
- **Key handling.** The harness alone reads the API key from `.env`
  (`src/mcpredteam/config.py`). It is never exported to a shell or passed to
  Claude Code itself. Scaffold subprocesses receive it from the runner in their
  own environment only.
- **Interpretation (CLAUDE.md #10).** SPEC §6.4 says "Frontier-model numbers come
  only from this suite" (the published suite). The sampled factorial now runs on
  a frontier model, so that sentence is read as *headline* numbers: factorial
  results are exploratory and never quoted as findings (SPEC §8.3).
- **Open for PREREG:** D11's resume line names one model (`<<MODEL>>`). With Opus
  now on the held-out sweep, whether the headline names both models is decided
  when `PREREG.md` is frozen.

## D20. M1 design: proxy, era forcing, record/replay, adapter
Built and tested before any live model call. Each choice has its rejected
alternative.

**Proxy shape.** A JSON-RPC message-level relay (`src/mcpredteam/proxy/relay.py`),
one `Relay` per (trial, upstream server). The agent-facing transport ends at the
proxy: a Streamable HTTP hub at `/t/<trial>/<server>/mcp`
(`proxy/http_app.py`), or one stdio subprocess per server (`proxy/stdio.py`).
Upstream mocks run in-process as fresh `MCPServer` apps with `json_response=True`,
reached over Streamable HTTP via `httpx2.ASGITransport`.
- *Rejected:* a byte-level HTTP reverse proxy, which would not work for stdio
  and would make interceptors parse framing. Also rejected: mirroring tools
  through a high-level `MCPServer`, which would let the SDK re-negotiate and
  hide the wire from the log.

**Era forcing (implements D16).**
- In a handshake-only run, `server/discover` is refused with HTTP 404 /
  JSON-RPC `-32601`. The mcp 2.3.0 negotiator treats any such error as "fall
  back to `initialize`" (`mcp/client/_probe.py`).
- In a stateless-only run, `initialize` is refused the same way. A client that
  falls back therefore fails to connect instead of changing era.
- Each connection records the era it actually negotiated. A trial passes the era
  check only if every connection negotiated exactly the forced era.
- *Rejected:* refusing with `-32022`. With an empty or modern-only `supported`
  list it aborts the connect outright, so it can't force a fallback.

**Wire details.**
- Upstream headers are rebuilt from the message body rather than copied, so an
  interceptor that renames a tool can't create a header/body mismatch (`-32020`).
- GET gets 405: the mocks never push messages, and the client gives up after two
  retries.
- DELETE ends the upstream session. Notifications get 202. JSON-RPC batches are
  refused.
- *Limitation:* `Mcp-Param-*` headers are not forwarded. The mocks have no
  `x-mcp-header` tools; revisit at M2 if an attack needs them.

**Logging.** `proxy.<server>.jsonl` holds every message, keyed by run and trial.
`dir` is `c2s` or `s2c`; `stage` is `in` (as received) or `out` (as forwarded,
after interceptors). Tool calls for scoring are read from `c2s`/`in`, which is
what the agent issued.

**Upstream record/replay.** `upstream.<server>.jsonl` holds request, responses
and HTTP status.
- Replay matches by (method, params without `_meta`) in recorded order, rewrites
  response ids, and returns the recorded status.
- Unmatched requests raise `ReplayDivergence`.
- In replay mode no upstream object is created at all.

**Model record/replay.** The adapter's `httpx2` transport wraps the model client
and writes `llm.jsonl` with request and response bodies only. No headers are
stored, so the API key can't reach a recording.
- Replay serves model calls in order and refuses any request whose canonical
  body differs from the recording.
- A socket guard in the replay adapter refuses every non-loopback connection or
  DNS lookup and counts attempts.
- Agents SDK tracing, which would POST to OpenAI, is disabled four ways: the
  env var, `set_tracing_disabled`, `set_trace_processors([])`, and
  `RunConfig(tracing_disabled=True)`.

**Replay equality (SPEC §9.1).** The criteria:
- same tool-call sequence, as (server, tool, canonical arguments) in arrival
  order
- same verdict
- zero live calls: no model call left the adapter, the socket guard blocked
  nothing, and no proxy reached an upstream
- every recorded model call served
- the era check passes in both runs

Final-answer equality is reported but not required. That is the SPEC's own
definition.

**M1 verdict (interpretation, CLAUDE.md #10).** The "oracle verdict" in §9.1 is,
at M1, the benign task's deterministic checker (`tasks/benign.yaml`). The
attack oracle (SPEC §7.2) arrives at M2 and joins the comparison then.

**Adapter (OpenAI Agents SDK 0.23.1, `scaffolds/openai-agents/`).**
- `OpenAIChatCompletionsModel` with `AsyncOpenAI(base_url=Anthropic's
  OpenAI-compatible endpoint, max_retries=0)`. The D13 path; the LiteLLM extra
  is not needed.
- `cache_tools_list=False`, fixed server names, and a fixed system instruction
  (in `adapter.py`).
- `failure_error_function=None`, so a proxy or MCP fault aborts the trial
  loudly instead of becoming a generic tool error. Revisit at M2, where
  mitigations block calls.
- The key reaches the adapter only as `MCPR_ANTHROPIC_API_KEY` in a scrubbed
  subprocess environment, with no `OPENAI_*` variables.

**Model settings.**
- `parallel_tool_calls=false`. Tool calls are sequential, so arrival order
  across servers is deterministic and comparable. *Rejected:* the default
  parallel calls. Cross-server arrival order could then vary between record and
  replay.
- `max_tokens=8192`. Hidden thinking counts toward it, and the compat endpoint's
  default is undocumented.
- `seed` can't be fixed: Anthropic ignores it (SPEC §5.2 item 4 requires
  reporting this).
- Whether `temperature=0` is accepted is settled by a live check in D21.

**Interceptor chain (SPEC §5.1).**
- Request and response hooks run in list order. `on_discover` receives the full
  discover result.
- `on_final_answer` will be called by the runner at M2, since it sees the final
  answer and the proxy does not.
- The chain is empty at M1. The plumbing is exercised when attacks land.

**Offline pipeline test.** `tests/test_pipeline_fake_model.py` runs the real
adapter against a scripted OpenAI-compatible fake model, in both transports and
both eras. It records, replays and asserts replay equality, with no API spend.
It's a candidate for M3's CI.
