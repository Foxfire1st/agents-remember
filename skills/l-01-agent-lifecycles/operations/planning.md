<!-- ar:roles all -->
# Operation — Planning

Planning roles use the developer's outcome and applicable current knowledge to shape a bounded plan. This operation defines requirement preparation, alignment and approval before task execution.

## Prepare requirements and recover approval

For new scope, assign a stable requirement identifier and version, with one canonical packet per independently falsifiable obligation. Before grouping primary and additional same-topic packets, read [Plan a topic merge or propose a split](../reference/coordination/shared/requirement-design.md#plan-a-topic-merge-or-propose-a-split) for the grouping and split rules. List obligations from other topics as dependencies or preservation constraints.

Obtain developer approval before creating the task topology.

For existing approved work, recover and honor the recorded approval without asking again. A task document projects approved scope; it does not replace the requirement packet.

<!-- ar:roles architect -->
## Cross-packet alignment sweep

An open leaf belongs to the same master, is not retired, and has a status other than `Completed` or `abandoned`. A new leaf is released by its first role start. A revised packet for a started leaf is released when registered and handed to its seats.

Before releasing a new packet or revision into a master with other open leaves:

1. Have a reader other than the drafter compare it with every open leaf's packet in both directions.
2. Ask whether the new text contradicts an open packet, makes one stale, or is contradicted by one. Check counts, paths, leaf identifiers, order and premises.
3. Complete the sweep before the new leaf's intent-review question, "go, or review first?" Repeat it before the go for a packet revised during review.
4. Record the reader's report, including a result with no findings.

Fix a stale fact within the promise by the usual packet revision: draft and register the older packet's amendment and update its leaf task document. An amendment beyond the developer's words goes to the developer first.

Before assigning an issue to another open leaf, follow [Complete the plan and preserve its boundaries](#complete-the-plan-and-preserve-its-boundaries) for the originating-leaf default and charter exception.
<!-- ar:roles all -->

## Establish ownership and prepared context

Before delegating coordination, read [Choose the coordination owner](coordination.md#choose-the-coordination-owner) for the Architect's first Manager or Orchestrator and the developer-chosen direct-coordination exception.

<!-- ar:roles all -->
Keep one explicit owner per task and preserve started agents' identifiers. Do not fabricate a sprint, master or agent identity for a taskless project role.

Before any seat starts, give the task document a Prepared context: records with identifiers and state; code with files, lines and unattributed callers; and notes on traps met.

<!-- ar:roles architect -->
## Prepare knowledge effects and intent review

When a packet names an effect on a recorded invariant or family, declare it in `expectedKnowledgeEffects` through `task_doc` on `agents-remember-task` before implementation. A packet that names no such effect declares nothing. Do not accept a knowledge detector or report without a closeout gate and precise triggers.

After creating a new leaf's task document and before any start, ask the developer once: "go, or review first?" You may add a one-line suggestion. Explain that up-front review uses planned comparison with no task workspace and creates the explainer workspace on first Explain.

Record `review-first` or `go` with `agreedRevision` in `intentReview` through `task_doc` on `agents-remember-task`. The state is `intent-review-open` until `go`.

Before a packet's cold read, read its governing cards, families and invariants, every knowledge answer through its last page, and the code they leave unattributed. Put that list in the leaf task document's Prepared context before any seat starts.

## Plan the packet’s own curation

A packet adding a row kind, disposition, writer field or gate must explain in the rule itself how its own curation completes on the installed build. That build lacks the new behavior until the leaf lands. Prove the new behavior only on fixtures and scratch copies.

## Deliver a direct-coordination review intake

<!-- ar:gives leaf-review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, tools-file -->
Only under developer-chosen direct coordination, start a leaf Reviewer on the reviewed leaf with its sprint and master. Send its intake as your first `role_message` on `agents-remember-task` to the returned agent identifier. Include the review mode, canonical task and review purpose, seam, exact requirement set with identifiers, versions, packets and the durable [approval record](../core/every-role.md#requirements-and-limits), and resolved `system/tools.md`.

Plan-review and super-exit have no startable Reviewer seat. Take either review to the developer yourself, with the sprint-level Reviewer idea.

## Report a costly small repair

When one small item has taken about an hour, or a second repair round or freeze, tell the developer that day: the hours, the item, and a proposal to carry or bound it. Only polish that is no defect may be dropped.
<!-- ar:roles all -->

## Complete the plan and preserve its boundaries

Name the deliverables, code, memory and report roots, dependencies, evidence class, requested independent review, curation needs and publication authority.

Send a requirement contradiction, a premise that changes a requirement's promise, scope expansion or a high-impact unresolved decision to the developer before affected work proceeds. Replan only through the owner, preserving unaffected requirement versions. Keep issues in their originating leaf unless another open leaf's charter already covers them.

Call a user-interface fix working only after seeing it work on the developer's installation. Otherwise report it as landed but not yet verified there.

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.

### Handle delivery receipts

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
