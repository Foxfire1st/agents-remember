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
For every schedulable candidate, re-derive exactly one effective priority row: use its
candidate-specific row when one exists, otherwise use the owning master's row as the inherited
default. A candidate row overrides rather than combines with the master default, and duplicate
current rows for the same subject are invalid. Portfolio-wide comparison remains an orchestrator
judgment; graph order and stable task order are only equal-grade tie-breaks.

### PR-4 — Order-respects-edges

**Validate the adopted topology choice and derive its execution mechanically.** In both modes,
every commanded master has an explicit `executionNature`, the typed subTasks `masterRef` rows agree
exactly with `orchestrates`, and the plan carries evidence-backed dependency, priority, and
coherence judgments (`task_doc.linkage_report` / `linkageFacts` on `task_doc.get` re-derives the
linkage facts; a finding it already reports is still a plan finding, not an excuse).

When an `executionGraph` exists, its node set must also equal `orchestrates` and the classified
master set. Every predecessor edge must be supported by relation evidence, no cycle may exist, and
the displayed waves must equal the stable topological derivation rather than a persisted/manual
position. An atomic blocker must wait for every predecessor, expose no intermediate state, and
release successors only after its one landing. Stable node order may break an equal-priority tie
but must never masquerade as priority.

When no `executionGraph` exists, require an explicit, evidence-backed choice of the graph-less
atomic-sequential default: canonical commanded-master order is the stable tie-break, while exact
source-pair activation exposes only one atomic master at a time. Selecting another master may
logically pause the former without retiring its durable work, so graph absence must not be
misrepresented as a dependency requiring full integration before another master can be selected.
Graph absence does not excuse missing classifications or planning judgments, and the author must
not fabricate an edge merely to prove that planning occurred.

### PR-5 — Honesty of the findings section

**Verify the plan's failure claims both ways.** Every "unplannable as scoped" finding must be
genuine (the leaf can name neither existing surfaces nor the parent anchoring of its additions —
a merely not-yet-existing surface is NOT unplannable); and thin leaf scopes the plan silently
guessed around instead of flagging are themselves findings. Quo-vadis contradictions must be
flagged at the top of the coherence findings, not buried.

### PR-6 — Detection/judgment boundary and runtime ownership

**Facts and judgments must remain distinguishable and owned.** Recompute a sample of mechanical
facts from task docs, route indexes, source graphs, and lineage. Check that execution nature,
priority, dependency meaning, and blocker placement are explicit judgments with evidence rather
than code-invented policy. The plan must assign ordinary ready-frontier recomputation and bounded
reprioritization to the orchestrator, substantial graph/classification reshapes to a proposed
strategist pass through the architect, and master-local readiness reporting to managers. A manager
that ranks other masters, or an algorithm that silently invents priority, blocks the plan.

### PR-8 — Review independence and evidence-type matching *(added 260815-DAG-L15)*

**The reviewer of a plan is never its author, and every requirement verdict matches its evidence class.**

1. **No self-review.** The plan's builder/author seat may not review it; a distinct reviewer seat
   runs the review, and a self-signed requirement is a blocking finding. Catching class:
   260815-DAG — L7/L8/L9 route reviews were orchestrator self-reviews (review reports r2 F7,
   r6 F12).
2. **Evidence matches the requirement's type.** A rendering/visibility requirement needs
   mounted-UI proof (a component reachable from the shell, a test-id, a story, or a scenario —
   projecting a field is NOT rendering); a scheduling/ordering requirement needs operation-level
   proof (drive the queue/scheduler operation and observe the order); a data-model requirement
   needs artifact-level proof (the persisted/parsed shape). Evidence of the wrong class is
   verdict-laundering, never a pass. Catching class: 260815-DAG — L8-R3 was passed on
   projection-only evidence (review reports r2 F7, r4 F6, r6 F8/F9).

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
