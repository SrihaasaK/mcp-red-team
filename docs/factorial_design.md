# Sampled factorial design (DRAFT for DECISIONS.md at M2; frozen later in PREREG.md)

Status: proposal only. Nothing here is executed or frozen. Authority: DECISIONS.md
D19, D25, D26, D27 and SPEC §6.4 (amended 2026-10-06). This revision replaces the
first draft and applies the author's calls: dev-split base configs only, language
excluded, OpenAI scaffold, classes 4/6/8 dropped, `SeedSequence` sampling.

## 0. Scope (fixed by D25-D27)

| Decision | Effect on this design |
|---|---|
| D27: base configs from the **dev split only** | Base-config pool = the 28 dev configs of `split.yaml` (proposal), never a held-out config. |
| D25/D27: **language excluded** from the factorial | No `label_*` level. Language configs are held-out anyway, so the factorial never touches them. |
| D27: runs on **OpenAI**; **stateless** era for all classes **except class 3**, which runs **handshake** | Era is assigned per class, not crossed. 1, 2, 5, 7 stateless; 3 handshake. |
| D27: classes N/A on OpenAI (**4, 6, 8**) are **dropped, not reallocated** | Classes in the design: **1, 2, 3, 5, 7** (5 classes). |
| D26: sampling via `numpy.random.SeedSequence(base_seed).spawn(n)`, `base_seed` via args | Section 4. |
| D19/SPEC §8.3: results are **exploratory only** | Section 5. |

Dev base configs available per run class (from `split.yaml`):

| Class | Dev configs | Era |
|---|---|---|
| 1 indirect injection | 5 | stateless |
| 2 tool-description poisoning | 4 | stateless |
| 3 rug pull | 4 | handshake |
| 5 shadow tool | 4 | stateless |
| 7 cross-server exfil chain | 5 | stateless |
| **Total** | **22** | |

(Dropped, with their dev counts: 4 -> 2, 6 -> 2, 8 -> 2. They report N/A on OpenAI, never 0%.)

## 1. Factors and the full factorial

| Factor | Levels | Count |
|---|---|---|
| A. Attack class | 1, 2, 3, 5, 7 | 5 |
| B. Mutator family | `plain` (encoding null, framing null), `encoding`, `framing` | 3 |
| C. Placement | `prefix`, `suffix`, `mid` | 3 |

`language` is not a family here (excluded, D25/D27). Placement is crossed with B as its
own factor (D26); SPEC §6.3 also lists it as a mutator family, which this design does
not count as one.

Within a non-plain family the specific mutator is a **nested** level (one active at a
time), using the final registry names in `attacks/mutators.py`:

- encoding: base64, zero_width, homoglyph, html_comment, md_comment (5)
- framing: system_tag, tool_call, urgency, roleplay (4)
- plain: 1

So there are 1 + 5 + 4 = **10 mutator levels**.

Each trial also needs a base config (target, payload intent, compatible tasks). The
factorial re-templates the base config's payload with the cell's mutator and placement
and keeps its target, intent and compatible tasks.

Cell counts:

- Family-level strata (A x B x C): 5 x 3 x 3 = **45**
- Mutator-level cells (A x 10 levels x C): 5 x 10 x 3 = **150**
- Fully expanded with dev base config (22 configs, summed over classes): 22 x 10 x 3 = **660 cells**

Full factorial trial counts (for scale only):

- 1 trial per fully expanded cell: 660
- n = 5 per cell (SPEC §8.3 dev-cell n): 3,300
- n = 10 per cell (SPEC §8.3 held-out cell n): 6,600

## 2. Sampling scheme: stratified by class x family x placement

**Stratum = one A x B x C cell (45 strata). Every stratum gets n = 22 attack trials.**
n is chosen so the total lands on the D19 budget of about 1,000.

Allocation arithmetic:

- Attack trials: 45 strata x 22 = **990**
  - per class: 3 families x 3 placements x 22 = 198
  - per family: 5 classes x 3 placements x 22 = 330
  - per placement: 5 classes x 3 families x 22 = 330
- No-attack control trials (SPEC §7.3): 5 per class x 5 classes = **25** (class-3
  controls run in the handshake era, the rest stateless). Each control uses the class's
  compatible tasks round-robin with the proxy passing everything through.
- **Total = 990 + 25 = 1,015.** (n = 21 would give 945 + 25 = 970; n = 22 is the
  smallest n at or above 1,000.)

Within a stratum the 22 trials are assigned deterministically; only orderings and
remainders use the seeded generator (section 4):

1. **Mutator level.** Levels are cycled without replacement.
   - encoding (5 levels): 22 = 5 x 4 + 2, so each level 4, and 2 levels (seeded) get 5.
   - framing (4 levels): 22 = 4 x 5 + 2, so each level 5, and 2 levels (seeded) get 6.
   - plain: 22 repeats of the same cell.
2. **Base config.** The class's dev base configs are cycled without replacement in a
   seeded order, so intent/target is balanced.
   - classes 1 and 7 (5 dev configs): 22 = 5 x 4 + 2, so each used 4 or 5 times.
   - classes 2, 3 and 5 (4 dev configs): 22 = 4 x 5 + 2, so each used 5 or 6 times.
3. **Task.** Drawn round-robin from the base config's `compatible_tasks` (SPEC §6.5), in a
   seeded order. Persistence and rug-pull base configs only ever pull two-turn tasks.

Every one of the 150 mutator-level cells gets at least 4 trials (encoding 4 or 5,
framing 5 or 6, plain 22), so no cell is empty. Sampling is over base config, task and
repetition within the cells, not over which cells exist.

Coverage and precision of the marginals (Wilson 95% half-width at p = 0.5; exploratory):

| Margin | n | Half-width |
|---|---|---|
| A x B x C stratum | 22 | +/- 0.19 |
| A x B (class x family) | 66 | +/- 0.12 |
| A x C (class x placement) | 66 | +/- 0.12 |
| B x C (family x placement) | 110 | +/- 0.09 |
| A (class) | 198 | +/- 0.07 |
| B (family) | 330 | +/- 0.05 |
| C (placement) | 330 | +/- 0.05 |
| single encoding level, pooled over A and C (5 x 3 x 4 or 5) | 60 to 75 | +/- 0.12 to 0.11 |

Per-stratum rates are weakly informative (+/- 0.19) and should only be tabulated, never
ranked or quoted. Class 3 has thin compatible-task sets for some dev configs (2 to 4
two-turn tasks), so its task repetition is higher than the other classes.

## 3. Eras, scaffold and dropped classes

All trials run on the OpenAI Agents SDK scaffold (D27). Per D22 and D8:

| Class | In this design | Era |
|---|---|---|
| 1, 2, 5, 7 | yes | stateless |
| 3 rug pull | yes | handshake (the only class that is) |
| 4 cache poisoning | **dropped** (N/A on OpenAI, D22) | - |
| 6 resource injection | **dropped** (N/A on OpenAI, D24) | - |
| 8 MRTR | **dropped** (N/A on OpenAI, D22) | - |

Rules:

- Era is assigned per class and is not a crossed factor.
- Dropped classes report **N/A, never 0%** (SPEC §6.1). Their strata are **dropped, not
  reallocated** (D27): per-class n stays at 198 and the other classes are not inflated.
- The 1,015 total is the full design on this scaffold. There is no 8-class variant on
  OpenAI, so the earlier 1,000 / 750 branch no longer applies.

## 4. Seeds (D26)

- **No generated seeds.** No `random.seed()` default, `os.urandom`, or time-based seed
  anywhere in the sampler. `base_seed` is a required CLI argument (`--base-seed <int>`).
  The design is a pure function of `base_seed`, the corpus file hash, the split file hash
  and the tasks file hash.
- Derivation: `children = numpy.random.SeedSequence(base_seed).spawn(N + 1)` with
  `N = 1015` (total trials). `children[0]` seeds the design generator
  (`numpy.random.default_rng(children[0])`), which makes only the orderings and remainder
  choices in section 2. `children[i]` for `i = 1..N` is the sampling seed recorded for
  trial `i`.
- **Seeds control sampling only.** They do not make model outputs reproducible, because
  the compat endpoint ignores `seed` and temperature is unsettable (D21, D26).
  Reproducibility of a given trial rests on replay (SPEC §8.5, §9.1). `PREREG.md` records
  this.
- The design file (an explicit list of trials: stratum, class, era, base config, mutator
  level, placement, task, spawned seed) is written once and its hash recorded in
  `PREREG.md`. The same design file is replayed, never re-sampled.

## 5. Status of the results: exploratory only

- Factorial results are **exploratory** and are **never headline** (SPEC §8.3, D19). The
  headline remains pooled held-out ASR from the published suite.
- No factorial cell, marginal or interaction is quoted as a finding. No mitigation is
  tuned on factorial results. The factorial runs the **baseline (no mitigation)** only.
- Report raw and exposure-conditioned ASR for every table (SPEC §8.1, §8.2) and the
  exposure counts. Report the control arm's false-positive rate first.
- Because base configs are dev-only, the factorial cannot leak held-out information
  (SPEC §6.4). Language is excluded, so it cannot leak the held-out-only family either.

## 6. Cost

D19's projection is about 1,000 trials at the **measured Sonnet** $0.0070 per trial, x 2
(base) or x 4 (high): 1,015 x $0.0070 = $7.11, so about **$14.21 to $28.42**. That figure
was measured on a different model and scaffold than D27's OpenAI run. Re-measure the
per-trial cost on the OpenAI scaffold before PREREG and scale this line accordingly.

## Open points

- **Q1.** SPEC §6.3 counts placement as a mutator family; this design treats it as its own
  factor (D26 confirms placement stays a factor). If the families were fully crossed
  instead (encoding 6 incl. null x framing 5 incl. null = 30 combos x 3 placements x 5
  classes = 450 mutator-level cells), 1,015 trials would be about 2.3 per cell; the
  stratified fractional design here avoids that.
- **Q2.** `mid` placement does not apply cleanly to every target (a short tool
  description, a one-line result). The generator must define it for those, or those cells
  reduce to prefix/suffix and the 3-level placement factor is uneven.
- **Q3.** Choice of n = 22 (1,015 total) versus n = 21 (970). Say if the budget should be
  exactly 1,000 and the remainder dropped from the controls.
- **Resolved by the author's calls:** base-config pool (dev only, D27), language
  (excluded, D25/D27), seed form (`SeedSequence`, D26), scaffold and eras (OpenAI,
  D27).
