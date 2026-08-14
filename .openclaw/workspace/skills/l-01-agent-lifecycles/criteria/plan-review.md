# Criteria Catalog — Plan Review (the strategist loop)

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run against an
**orchestration task** (`../templates/orchestration-task.md`) — the strategist's sprint plan —
before the developer's drawing board. This is the portfolio loop's review type: owner =
orchestrator, builder = strategist, reviewer = this catalog. The reviewer holds the same read-only
analysis tools the strategist used (`cgc_*`, `grepai_*`, `read_ar_files`), so the plan's mechanical
claims are re-derivable, not just readable. Criteria are never made up on the spot: the standing
list below is the regression floor, run every time; the exploratory mandate and the promotion
ratchet keep the catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### PR-1 — Refute uncited edges

**An uncited dependency edge is refutable by default.** Every edge in the dependency graph must
carry evidence — a cgc/grepai query, a file, a decision-log entry, a design-section citation, or
(for new surfaces) a declaration cross-reference naming both leaf declarations. An edge with no
evidence is challenged as a finding; so is evidence that does not actually support the edge's
direction or kind.

### PR-2 — Missed-shared-surface hunt (class-completeness on intersections)

**Re-intersect the surface lists independently.** Take the plan's per-leaf touch surfaces (both
existing and declared-new), recompute the pairwise intersections, and hunt pairs the edge list
omits — including CONFLICT-risk at a shared parent route where two leaves declare additions
(wiring points, shared registries). A plan that lists surfaces but misses one of their
intersections has a hole exactly where collisions happen.

### PR-3 — Blast-radius re-derivation

**Re-derive at least the HIGH-blast-radius entries with the cgc tools** (`cgc_dependencies` /
`cgc_callers` / `cgc_callees`, capped depth) — and spot-check a sample of the low/medium entries.
A register entry whose derivation cannot be reproduced, or whose classification drops the doctrine
propagation / migration / user-visible unions, is a finding; the register parameterizes every
downstream loop tier, so an understated radius under-reviews a leaf.

### PR-4 — Order-respects-edges

**Check the sprint order/waves against the edge list, mechanically.** No ORDER edge may run
backwards across the proposed sequence; every CONFLICT edge must be resolved by serialization or a
recorded leaf move (from→to + rationale); parallel waves may contain only INDEPENDENT pairs, and
the wave plan must acknowledge the landing-reconciliation cost.

### PR-5 — Honesty of the findings section

**Verify the plan's failure claims both ways.** Every "unplannable as scoped" finding must be
genuine (the leaf can name neither existing surfaces nor the parent anchoring of its additions —
a merely not-yet-existing surface is NOT unplannable); and thin leaf scopes the plan silently
guessed around instead of flagging are themselves findings. Quo-vadis contradictions must be
flagged at the top of the coherence findings, not buried.

## Candidate Criteria (seeded exploratory — one catching engagement each; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### PR-6 — Scaling & reclamation at design time *(candidate — 1 catch)*

**Any plan that introduces or changes a store, loop over a store, queue, or append-only log must name its cap, budget, and compactor/reclamation owner in the design, before code exists.** Challenge all three design claims:

1. (D1 — stability) At 10x/100x fleet, does the proposed mechanism's worst-case resource draw threaten the substrate? Where does the design name the budget, backpressure, or load-shed path that sheds the signal, not the system?
2. (D2 — bounded) What is the planned worst-case time and on-disk / in-memory size? Where are the per-cycle cap and the store's cap+eviction defined? Does any layer re-read a growing store per item?
3. (D3 — reclamation) Who owns reclamation, does the same plan land that reclamation with the data it creates, and how will scaling be proven across >=2 input sizes rather than a single-N smoke?

- Catching evidence: 260707-HFX2-L7/L8 — the plan surface had not made worst-case inbox fold cost, retention, or reclamation owner a required design-time question before an O(n^2) agent-notifier sweep over never-reclaimed dead-seat rows passed correctness gates and froze the heartbeat. A plan that introduces a store/loop/log without naming its cap, budget, and compactor fails PR-6.

## Exploratory Mandate

Beyond the standing list, the reviewer owes **novel lenses** (the brief sets N; default 2): ways
THIS plan could be wrong that the catalog does not name yet. Every novel finding-class that
survives refutation is proposed as a catalog amendment in the verdict.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests. Promotion is proposed in the verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a gate** — the closeout
  body gate (which catches history-only "refreshes" at commit time, where prose requests did not)
  is the working example; PR-4 is this catalog's mechanization candidate (a topological check over
  a structured edge list).
