<!-- ar:roles all -->
# Operation — Planning

Use the developer's outcome and applicable current knowledge to shape a bounded plan. For new scope, assign a stable requirement ID/version and one canonical packet per independently falsifiable obligation; keep one primary requirement per executable leaf and list adjacent obligations as dependencies or preservation constraints. Give each new packet a cold read, then obtain developer approval before creating its task topology. For existing approved work, recover and honor the recorded approval without asking again. A task document projects approved scope; it does not replace the requirement packet.

The Architect's first delegation for one master is one Manager on that canonical master. For two or more masters worked on at the same time, it is one Orchestrator on the canonical sprint, which starts one Manager per master. This delegation rule applies only to the Architect; Designers and Strategists do not start a coordinating role. Direct coordination by the Architect is only the developer-chosen exception; in that case, the Architect directly coordinates distinct Workers, Reviewers, and Curators. The developer may also ask for an Orchestrator above a single master. Keep one explicit owner per task and preserve the agent IDs of started roles. Do not fabricate a sprint/master for a taskless project role or an agent identity to satisfy an old hierarchy.

<!-- ar:roles architect -->
When the leaf's packet names an effect on a recorded invariant or family, the Architect declares it in `expectedKnowledgeEffects` through `task_doc` on `agents-remember-task` before implementation. A leaf whose packet names none declares nothing. After a new leaf's task document exists, before any start, ask the developer once: "go, or review first?" (one-line suggestion optional), disclosing up-front review uses planned comparison (no task workspace) and makes the explainer workspace on first Explain; record `review-first` or `go` (`agreedRevision`) in `intentReview` via `task_doc` (`intent-review-open` until `go`).
<!-- ar:roles all -->

The plan names deliverables, code/memory/report roots, dependencies, evidence class, requested independent review, curation needs, and publication authority. A requirement contradiction, scope expansion, or high-impact unresolved decision goes to the developer before affected work proceeds. Replan only through the owner and preserve unaffected requirement versions.

Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.
