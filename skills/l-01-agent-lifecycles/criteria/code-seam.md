# Code-seam criteria and shared catalog review rules

The Reviewer uses this file for the shared procedure governing all five criteria catalogs and for code-seam criteria when a change connects consumers, enforcement points, gates, addresses or tool contracts. The [Reviewer binding table](../roles/reviewer.md#bind-the-seam-and-catalogs) selects the catalogs at master-exit, super-exit and code-touching loop reviews; the shared rules select no additional catalog.

## Shared catalog review rules

These rules govern the review procedure for all five criteria catalogs. They do not select additional catalogs.

The baseline review runs every standing criterion in each bound catalog. Do not invent criteria during intake. In fix-verification, use only the sealed baseline evidence needed for the outstanding identifiers; do not repeat the catalog census or add a criterion.

Only two routes may add a finding after sealing: a valid code finding from the Curator, or a valid finding from an advisory review the Architect commissioned and forwarded in its questions file. Before adding a sealed finding or reopening the verdict through either route, follow [the review operation](../operations/review.md#boundaries).

### Explore during the baseline

For each bound catalog, the Reviewer must examine new ways the change could fail that the catalog does not yet name. The brief sets the number of new review lenses for that catalog; the default is two. Apply the lenses to the code, doctrine, memory, plan or report that catalog reviews.

Propose every new finding class that survives refutation as a catalog amendment in the verdict. Fix-verification has no new-lens or new-catalog duty. Other observations outside the sealed issue list cannot become findings in that round. Send every other new observation through your role's developer-question route.

### Promotion Ratchet

The promotion ratchet is the procedure below for promoting, demoting and automating criteria.

A candidate criterion that catches a real defect in at least two separate first-review engagements is promoted to standing, with its catching evidence cited. This keeps escaped failures as lasting checks.

A standing criterion that catches nothing for N consecutive engagements is demoted to spot-check; N defaults to five. A criterion explicitly protected from this process retains its stated developer-ruling requirement.

A criterion that can be automated moves out of the catalog into a scoped diagnostic.

The Reviewer proposes promotion, demotion or automation in the verdict; it takes effect on the loop owner's acceptance.

## Standing criteria

### CS-1 — Production-wiring walk

Trace the production call path behind every claim that a consumer or enforcement point exists: the application entry point, tool registration or store fold. A test that manually aligns the pieces is insufficient; it can pass while the production consumer does nothing.

### CS-2 — Fail-open hunt

For every identity, address, key or configuration value the seam matches, supply both an absent value and a near miss. Confirm that the seam refuses rather than silently proceeding unmatched.

### CS-3 — Validate-then-mutate

Every operation that records state must run its refusal checks before any durable effect. This includes raising a gate, appending to a store and writing a contract. Mutation before validation can leave partial state after refusal.

### CS-6 — Scaling and reclamation

This criterion is standing. For every loop, store, log or queue the change touches, examine all four properties:

1. **Stability.** At 10 or 100 times the fleet size, could worst-case resource use threaten the underlying system? Find the budget, backpressure or load-shedding path that drops the signal before exhausting the system.
2. **Bounded resources.** Establish worst-case time, disk space and memory. Find the per-cycle cap and the store's cap and eviction. Check for an O(n) reread per item that becomes quadratic across layers.
3. **Reclamation.** Identify who reclaims the data, whether reclamation lands with this change, and the scaling evidence from at least two input sizes. A single-size smoke test is insufficient.
4. **Quiescence.** With every external input at zero—no acknowledgements, live seats or operator—check that every feedback actor reaches a fixed point. If an actor's output belongs to its own input class, require a multi-cycle, zero-input convergence simulation. Examples include an escalation about an escalation, a retry row that is itself retryable, or an event emitted while folding events. A per-cycle budget limits rate but does not prove convergence. Separate zone verdicts cannot detect a loop crossing zone boundaries.

No event, message or row outranks system health. A recurring notification condition updates its one existing row's date, tries and attempt; it never appends a sibling. Every store is purgeable: pending rows age out, and hard caps evict oldest first. The artifact on disk is the durable record, not the queue row. Report “keep it all” semantics on a queue-like store as a defect.

A green complexity-and-coverage diagnostic (CRAP) result is not delivery acceptance. Stability, bounded resources and reclamation become a gate once a reusable repository-wide scaling-test helper exists. The reusable quiescence check is the multi-cycle simulation with zero external input.

## Candidate criteria

Run these under the baseline's exploratory work. Apply the promotion rules above and retain each candidate's stated catch count.

### CS-4 — Reused-primitive affordance parity

This candidate has one catch. When a screen reuses a shared content primitive, verify every branch of the original's behavior still works: banners, status chips, truncation notices and error states. Tests of the new screen's successful paths can miss lost behavior.

### CS-5 — Cross-repository side-effect safety

This candidate has zero catches. For a step writing to another repository, check validation before mutation, partial failure and a dirty target. Inspect the order of validation and the external write, the state left if the write succeeds but its caller then fails, behavior against a dirty repository, and an exact format round trip through the external artifact's consumer.

For promotion, follow the [Promotion Ratchet](#promotion-ratchet).

## Reuse existing user-interface primitives

Treat a new user-interface control that duplicates an existing primitive as a finding. Name the primitive to reuse.
