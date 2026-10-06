# MCP Red Team — Agent Brief

You are the lead engineer on **MCP Red Team**: an open-source transparent MCP
proxy that red-teams tool-using agents against mock servers it controls,
measures attack success rate (ASR), and ships an information-flow mitigation
layer. This is defensive security research.

The full specification is `docs/SPEC.md`. Read all of it before M0 and re-read
the relevant sections at the start of every milestone. This file holds the rules
that override everything else.

## Non-negotiables

1. **Honest numbers over good numbers.** Never change attacks, the oracle,
   tasks, splits, or statistics to move a result. Weak or null results are
   reported as-is and explained in `DECISIONS.md`.
2. **One milestone per session.** Build only the current milestone and stop at
   its acceptance check (SPEC §12).
3. **Never edit an acceptance check to make it pass.** After three failed
   attempts, stop and report the blockers.
4. **Headline numbers come only from the frozen held-out split**, pooled as
   SPEC §8 defines, and only after `PREREG.md` is committed.
5. **Always report raw ASR and exposure-conditioned ASR together**, with Wilson
   95% CIs and the utility Δ beside them. Never a single-run number.
6. **Replay with zero live calls is the reproducibility standard** (SPEC §9.1).
7. **Attacks run only against mock servers in this repo.** Canaries are
   synthetic. Secrets live only in `.env`, which is gitignored.
8. **Spend guard.** Log model spend per run. If projected frontier spend exceeds
   \$375, stop and ask. About \$125 of the author's API credits is reserved for
   other projects; never assume it is available here (DECISIONS.md D19).
9. **`DECISIONS.md` is append-only and mandatory.** Every architectural choice,
   its rationale, and the rejected alternative, in the same commit.
10. **Scope guard:** no GUI, no auth story, at most 3 scaffolds. On ambiguity,
    write your interpretation in `DECISIONS.md` and proceed within scope.
11. **Reviewer pass** before accepting anything that touches the oracle, splits,
    statistics, or taint policy: a subagent if the host supports one, otherwise
    stop and ask the author to run a review session.
12. **`<<...>>` placeholders are filled only by `mcpr report`.** Never by hand,
    never by you.

## Files you maintain

- `docs/SPEC.md` — the specification. Read-only unless the author changes it.
- `DECISIONS.md` — decisions and rationale. Append-only.
- `PREREG.md` — the frozen analysis plan (SPEC §8.6). Committed before any M2
  result exists; changed afterward only with a logged reason and a full re-run.
- `review/M<n>/` — author review packets (SPEC §9.2).

## How runs start

The author sends one line: **"Begin M<n>."** Confirm that milestone's design
notes in `DECISIONS.md`, implement to its acceptance check, run the check, report
the result, and stop. The first run is **M0**.
