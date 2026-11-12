# Criteria catalog: plan review

The Reviewer uses this catalog to examine the sprint plan before it reaches the developer's drawing board.

Use the retained read-only naming tools: `read_ar_files`, `knowledge_read` and `knowledge_diff`. Re-derive mechanical claims through the tools, then open the named files with the harness's file tools.

Before starting a baseline or fix-verification, read the [shared catalog review rules](code-seam.md#shared-catalog-review-rules). They define standing checks, baseline exploration, fixed-list verification and criterion promotion, demotion and automation. Fix-verification retains only the sealed evidence needed for outstanding plan-issue identifiers, subject to that section's two sanctioned routes for adding findings after sealing.

## Standing criteria

### PR-1 — Refute uncited edges

Every dependency edge in the evidence relation list and canonical `executionGraph` needs supporting evidence. This may be a file, named native-search result, decision-log entry or design-section citation. For a new surface, it may be a declaration cross-reference naming both leaf declarations.

Report an edge with no evidence, or evidence that does not support its direction or kind, as a finding.

### PR-2 — Missed-shared-surface hunt

Independently recompute pairwise intersections of the plan's per-leaf touch surfaces, including existing and declared-new surfaces. Find intersections omitted from the edge list, including conflict risk where two leaves add to a shared parent route, wiring point or registry. A listed surface with an omitted intersection leaves the plan open to collisions.

### PR-3 — Classification, blast-radius and priority re-derivation

Challenge every atomic classification and high or critical priority judgment. Partial exposure must be demonstrably invalid or unsafe; size alone is insufficient.

Re-derive at least the high-blast-radius entries: name the implicated dependents through `read_ar_files` and open them natively, and spot-check a sample of the low and medium entries.

Report a finding when a register entry cannot be re-derived or its classification omits the combined impact on instruction propagation, migration or user-visible behavior. The register determines downstream loop tiers, so an understated impact can cause too little review. A priority row also needs a benefit and urgency rationale, affected dependents, evidence, author and confidence.

For every schedulable candidate, re-derive exactly one effective priority row. Use its candidate-specific row when present; otherwise inherit the owning master's row. The candidate row replaces, rather than combines with, the master default. Duplicate current rows for one subject are invalid.

Portfolio-wide comparison remains the Orchestrator's judgment. Graph order and stable task order are only tie-breaks between equal grades.

### PR-4 — Order-respects-edges

Validate the chosen topology and mechanically derive execution. In both graph modes, every commanded master needs an explicit `executionNature`. Typed `subTasks` `masterRef` rows must agree exactly with `orchestrates`. The plan must carry evidence-backed dependency, priority and coherence judgments. `task_doc.linkage_report` re-derives linkage facts; a finding it already reports is still a plan finding.

When `executionGraph` exists, check all of the following:

- Its node set equals both `orchestrates` and the classified master set.
- Relation evidence supports every predecessor edge, and the graph has no cycle.
- Displayed waves equal the stable topological derivation, not a persisted or manual position.
- An atomic blocker waits for every predecessor, exposes no intermediate state and releases successors only after its one landing.
- Stable node order breaks only equal-priority ties; it is not described as priority.

When `executionGraph` is absent, require an explicit, evidence-backed choice of the graph-less atomic default. Canonical commanded-master order is the stable tie-break, and nothing serializes the masters. A graph-less sprint declares no dependencies: independent atomic masters proceed concurrently, and selecting one does not hold another.

Do not describe graph absence as a dependency requiring one master to finish integration before another proceeds. Missing graphs do not excuse missing classifications or planning judgments. Do not invent an edge merely to show planning occurred.

### PR-5 — Honesty of the findings section

Check the plan's failure claims in both directions. An “unplannable as scoped” finding is valid only when the leaf can name neither existing surfaces nor the parent anchoring its additions. A surface that does not yet exist is not by itself unplannable.

Also report thin leaf scopes the plan silently guessed around instead of flagging. Put contradictions about the project's direction at the top of the coherence findings, not below other findings.

### PR-6 — Detection, judgment and runtime ownership

Keep facts and judgments distinct and assign their owners. Recompute a sample of mechanical facts from task documents, route indexes, source graphs and lineage. Execution nature, priority, dependency meaning and blocker placement must be explicit judgments with evidence, not policy invented by code.

The plan assigns ordinary ready-frontier recomputation and bounded reprioritization to the Orchestrator. Managers report readiness within their own masters. A Manager ranking other masters, or an algorithm silently inventing priority, blocks the plan.

### PR-8 — Review independence and evidence-type matching

The plan's Reviewer must be distinct from its author. The builder or author cannot review it; a self-signed requirement is a blocking finding.

Every requirement verdict needs evidence of its required class:

- Rendering or visibility needs mounted user-interface proof: a component reachable from the shell, a test identifier (`test-id`), a story or a scenario. Projecting a field does not demonstrate rendering.
- Scheduling or ordering needs operation-level proof that drives the queue or scheduler and observes order.
- A data model needs artifact-level proof of the persisted, parsed shape.

Wrong-class evidence cannot support PASS and is verdict laundering.

## Candidate criteria

Run candidates under baseline exploration. For promotion, follow the [Promotion Ratchet](code-seam.md#promotion-ratchet).

### PR-7 — Scaling and reclamation at design time

This candidate has one catch. A plan introducing or changing a store, loop over a store, queue or append-only log must name its cap, budget and compaction or reclamation owner before code exists. Challenge all three design claims:

1. **Stability.** At 10 or 100 times the fleet size, could worst-case resource use threaten the underlying system? Find the budget, backpressure or load-shedding path that drops the signal before exhausting the system.
2. **Bounded resources.** Establish planned worst-case time, disk space and memory. Find the per-cycle cap and store cap and eviction. Check whether any layer rereads a growing store per item.
3. **Reclamation.** Identify the owner, whether the same plan lands reclamation with the data, and how at least two input sizes will prove scaling. A single-size smoke test is insufficient.

A plan introducing a store, loop or log without its cap, budget and compactor fails this criterion.

## Automated part of plan review

The task-document model performs the local graph-shape checks of [PR-4](#pr-4--order-respects-edges). Evidence fidelity remains the Reviewer's work.
