<!-- ar:roles all -->
# Operation — Planning

Use the developer's outcome and applicable current knowledge to shape a bounded plan. For new scope, assign a stable requirement ID/version and one canonical packet per independently falsifiable obligation; keep one primary requirement per executable leaf and list adjacent obligations as dependencies or preservation constraints. Before a new or revised packet is released, sweep it against every open leaf's packet of its master, both ways, for contradictions and stale facts (D391). An amendment that goes beyond the developer's words goes to the developer first (D638). Obtain developer approval before creating its task topology. For existing approved work, recover and honor the recorded approval without asking again. A task document projects approved scope; it does not replace the requirement packet.

<!-- ar:roles architect -->
## Cross-packet alignment sweep

An open leaf is a leaf of the same master whose status is not in `RESOLVED_MASTER_ROW_STATUSES` and that is not retired. A new leaf is released by its first role start. A revision of a started leaf's packet is released when it is registered and handed to the leaf's seats.

- **When.** A new packet, or a new revision of one, joins a master that has other open leaves.
- **What.** Before the leaf is released, its text is read against the packets of every open leaf of that master, in both directions:
  - Does the new text contradict an open packet?
  - Does it make one stale: a count, a path, a leaf id, an order or a premise that no longer holds?
  - Does an open packet contradict the new one?
- **Who and when.** The Architect has the sweep done by a reader other than the drafter. It is done before the intent-review question "go, or review first?" for the new leaf, and again before the go for a packet revised during the review.
- **The result.**
  - **A contradiction about what the developer wants,** or a premise that changes a requirement's promise, goes to the developer before either leaf proceeds.
  - **A stale fact within the promise** is fixed by amending the older packet the way revisions are made: drafted and registered, with the older leaf's task document updated. An amendment that goes beyond the developer's words goes to the developer first.
  - **No finding** is recorded with the reader's report.
- **The default stays.** An issue is treated in the leaf where it was raised. Another open leaf takes it only when that leaf's charter covers it anyway.
<!-- ar:roles all -->

The Architect's first delegation for one master is one Manager on that canonical master. For two or more masters worked on at the same time, it is one Orchestrator on the canonical sprint, which starts one Manager per master. This delegation rule applies only to the Architect; Designers and Strategists do not start a coordinating role. Direct coordination by the Architect is only the developer-chosen exception; in that case, the Architect directly coordinates distinct Workers, Reviewers, and Curators. The developer may also ask for an Orchestrator above a single master. Keep one explicit owner per task and preserve the agent IDs of started roles. Do not fabricate a sprint/master for a taskless project role or an agent identity to satisfy an old hierarchy.

Before any seat starts, give the task document a Prepared context: records with IDs and state, code with files, lines and unattributed callers, and notes on traps met.

<!-- ar:roles architect -->
When the leaf's packet names an effect on a recorded invariant or family, the Architect declares it in `expectedKnowledgeEffects` through `task_doc` on `agents-remember-task` before implementation. A leaf whose packet names none declares nothing. Do not accept a knowledge detector or report without a closeout gate and precise triggers. After a new leaf's task document exists, before any start, ask the developer once: "go, or review first?" (one-line suggestion optional), disclosing up-front review uses planned comparison (no task workspace) and makes the explainer workspace on first Explain; record `review-first` or `go` (`agreedRevision`) in `intentReview` via `task_doc` (`intent-review-open` until `go`).

A packet goes to its cold read with the governing cards, families and invariants read for it, each knowledge answer read to its last page, and the code they leave unattributed; the leaf's task document carries that list as its Prepared context before any seat starts.

A packet that adds a row kind, a disposition, a writer field or a gate says, in the rule itself, how the leaf's own curation completes on the installed build, which lacks it until the leaf lands; the new behaviour is proven on fixtures and scratch copies only.

A check you report as complete, or as the gate, names what it ran and what it left out; a failure you call pre-existing has the same cause on the base, compared by message and subtest, not by test name.

<!-- ar:gives leaf-review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, catalogs, tools-file -->
Under developer-chosen direct coordination only, start a leaf Reviewer on the reviewed leaf, with its sprint and master. Send its intake as your first `role_message` on `agents-remember-task` to the returned agent ID: review mode, canonical task and review purpose, seam, requirement set (IDs, versions, packets, durable corpus ruling), bound catalogs and resolved `system/tools.md`.

Plan-review and super-exit have no startable Reviewer seat. Bring either review to the architect, who takes it to the developer with the sprint-level Reviewer idea.

When one small item has taken about an hour, or a second repair round or freeze, tell the developer that day: hours, item, and a proposal to drop, carry or bound it.
<!-- ar:roles all -->

The plan names deliverables, code/memory/report roots, dependencies, evidence class, requested independent review, curation needs, and publication authority. A requirement contradiction, scope expansion, or high-impact unresolved decision goes to the developer before affected work proceeds. Replan only through the owner and preserve unaffected requirement versions. An issue stays in the leaf where it was raised; another open leaf takes it only when its own charter already covers it.

Call a UI fix working only once it has been seen working on the developer’s installation; otherwise report landed, not yet verified there.

Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.

Read [message handover]({{detail:reference/message-handover.md}}) when you reach a step another seat awaits, a handover, or a held/full-store receipt, not before.
