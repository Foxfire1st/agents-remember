<!-- ar:roles all -->
# Operation — Planning

Shape a bounded, approved plan from the developer's outcome and current knowledge.

## Prepare requirements and recover approval

Assign stable requirement ID/version with 1 packet per falsifiable obligation ([merge/split](../reference/coordination/shared/requirement-design.md#plan-a-topic-merge-or-propose-a-split)); list other topics as dependencies/constraints. Get developer approval before task topology; honor recorded approvals without re-asking.

<!-- ar:roles architect -->
## Cross-packet alignment sweep

Before releasing a new/revised packet into a master with open leaves (not retired/`Completed`/`abandoned`): non-drafter compares bidirectionally with every open leaf's packet (contradictions, staleness, counts, paths, IDs, order, premises) before `"go, or review first?"` (repeat before `go` if revised), and records the report (including no-findings). Revise stale facts within promise; escalate beyond developer words.
<!-- ar:roles all -->

## Establish ownership and prepared context

Follow [owner](coordination.md#choose-the-coordination-owner), keep 1 owner/task and real IDs, and populate Prepared context (records via `knowledge_read` `impact`/`search`, files/lines/unattributed callers, traps) before starting seats.

<!-- ar:roles architect -->
## Prepare knowledge effects and intent review

Declare packet-named invariant/family effects in `expectedKnowledgeEffects` via `task_doc` before implementation (else none); reject detectors lacking closeout gates and triggers. Ask `"go, or review first?"` once after creating a leaf doc (up-front review uses planned comparison without a task workspace; explainer workspace creates on first Explain), and record `review-first`/`go` + `agreedRevision` in `intentReview` via `task_doc` (`intent-review-open` until `go`). Before cold reads, load governing cards/families/invariants (all pages) and unattributed code into Prepared context. Packets adding row kinds/dispositions/writer fields/gates state how curation completes on installed build (prove on fixtures/scratch).

## Deliver a direct-coordination review intake

<!-- ar:gives leaf-review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, tools-file -->
Under developer-chosen direct coordination only, start a leaf Reviewer on the leaf with sprint/master and send its first `role_message` intake (mode, task/purpose, seam, requirement IDs/versions/packets + approval record, `system/tools.md`). Take plan-review/super-exit to developer with sprint-level Reviewer idea. Report any small item costing ~1h or 2nd round/freeze that day with hours & proposal.
<!-- ar:roles all -->

## Complete the plan and preserve its boundaries

Name deliverables, roots, dependencies, evidence class, review, curation, and publication authority. Escalate contradictions, promise/scope changes, or high-impact decisions; keep issues in originating leaf unless another open leaf's charter covers them. Call UI fixes working only after seeing them on developer's installation.
