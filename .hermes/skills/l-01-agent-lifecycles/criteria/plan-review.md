# Criteria Catalog — Plan Review (the strategist loop)

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run against an
**orchestration task** (`../templates/orchestration-task.md`) — the strategist's sprint plan —
before the developer's drawing board. This is the portfolio loop's review type: owner = architect,
builder = strategist when approved or orchestrator on a sanctioned strategist skip, reviewer =
this catalog. The reviewer holds the same read-only
analysis tools the strategist used (`cgc_*`, `grepai_*`, `read_ar_files`), so the plan's mechanical
claims are re-derivable, not just readable. Criteria are never made up on the spot: the standing
list below is the regression floor, run every time; the exploratory mandate and the promotion
ratchet keep the catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### PR-1 — Refute uncited edges

**An uncited dependency edge is refutable by default.** Every edge in the evidence relation list
and canonical `executionGraph` must
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

### PR-3 — Classification, blast-radius, and priority re-derivation

**Challenge every atomic classification and high/critical priority judgment.** Partial exposure
must be demonstrably invalid or unsafe; large size alone is not enough. Re-derive at least the
HIGH-blast-radius entries with the cgc tools (`cgc_dependencies` /
`cgc_callers` / `cgc_callees`, capped depth) — and spot-check a sample of the low/medium entries.
A register entry whose derivation cannot be reproduced, or whose classification drops the doctrine
propagation / migration / user-visible unions, is a finding; the register parameterizes every
downstream loop tier, so an understated radius under-reviews a leaf. A priority row without a
win/urgency rationale, affected dependents, evidence, author, and confidence is also a finding.

### PR-4 — Order-respects-edges

**Validate the canonical graph and derive its waves mechanically.** Its node set must equal the
sprint's `orchestrates` membership and the classified master set. Every predecessor edge must be
supported by the relation evidence, no cycle may exist, and the displayed waves must equal the
stable topological derivation rather than a persisted/manual position. An atomic barrier must wait
for every predecessor, expose no intermediate state, and release successors only after its one
landing. Stable node order may break an equal-priority tie but must never masquerade as priority.

### PR-5 — Honesty of the findings section

**Verify the plan's failure claims both ways.** Every "unplannable as scoped" finding must be
genuine (the leaf can name neither existing surfaces nor the parent anchoring of its additions —
a merely not-yet-existing surface is NOT unplannable); and thin leaf scopes the plan silently
guessed around instead of flagging are themselves findings. Quo-vadis contradictions must be
flagged at the top of the coherence findings, not buried.

### PR-6 — Detection/judgment boundary and runtime ownership

**Facts and judgments must remain distinguishable and owned.** Recompute a sample of mechanical
facts from task docs, route indexes, source graphs, and lineage. Check that execution nature,
priority, dependency meaning, and barrier placement are explicit judgments with evidence rather
than code-invented policy. The plan must assign ordinary ready-frontier recomputation and bounded
reprioritization to the orchestrator, substantial graph/classification reshapes to a proposed
strategist pass through the architect, and master-local readiness reporting to managers. A manager
that ranks other masters, or an algorithm that silently invents priority, blocks the plan.

## Candidate Criteria (seeded exploratory — one catching engagement each; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### PR-7 — Scaling & reclamation at design time *(candidate — 1 catch)*

**Any plan that introduces or changes a store, loop over a store, queue, or append-only log must name its cap, budget, and compactor/reclamation owner in the design, before code exists.** Challenge all three design claims:

1. (D1 — stability) At 10x/100x fleet, does the proposed mechanism's worst-case resource draw threaten the substrate? Where does the design name the budget, backpressure, or load-shed path that sheds the signal, not the system?
2. (D2 — bounded) What is the planned worst-case time and on-disk / in-memory size? Where are the per-cycle cap and the store's cap+eviction defined? Does any layer re-read a growing store per item?
3. (D3 — reclamation) Who owns reclamation, does the same plan land that reclamation with the data it creates, and how will scaling be proven across >=2 input sizes rather than a single-N smoke?

- Catching evidence: 260707-HFX2-L7/L8 — the plan surface had not made worst-case inbox fold cost, retention, or reclamation owner a required design-time question before an O(n^2) agent-notifier sweep over never-reclaimed dead-seat rows passed correctness gates and froze the heartbeat. A plan that introduces a store/loop/log without naming its cap, budget, and compactor fails PR-7.

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
  is the working example; PR-4's local graph-shape portion has graduated into the task-document
  model, while evidence fidelity remains reviewer work.
