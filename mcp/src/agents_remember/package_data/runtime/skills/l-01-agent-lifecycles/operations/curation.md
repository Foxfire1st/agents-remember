<!-- ar:roles all -->
# Operation — Curation

Curators use this operation to reconcile a leaf's intent, code and memory, and to prepare its memory handover. Managers use it to assign that work and retain transaction and acceptance authority.

## Select the admitted work

The Worker supplies the exact freeze and handoff list directly; the Reviewer supplies the passed code freeze directly. The selected work is that freeze and its affected memory.

<!-- ar:roles manager -->
Before beginning a repository's first or resumed knowledge foundation, read [Enter through the real foundation task](../reference/curator/repository-foundation.md#enter-through-the-real-foundation-task) for its task, enclosure, scope and code-delta admission.

<!-- ar:roles all -->
## Keep ownership distinct

The Curator reconciles intent and writes memory through one admitted writer. The Worker answers code questions and receives evidenced code concerns directly. The Reviewer checks the memory candidate and answers code findings, including after passing code review. The Manager reads ordinary leaf state from reports and retains task acceptance and landing authority.

The Manager owns the Git transaction. The Curator runs no Git write: no staging, commit, branch or reference change, or stash operation. It never runs closeout preview, stages a sync resolution, repairs closeout transaction state, or decides whether a leaf lands.

<!-- ar:roles curator -->
The same task and Manager ownership applies to foundation work.

<!-- ar:roles all -->
## Required inputs

The Worker identifies the captured change set against the leaf contract's recorded range: the diff from the leaf's base to its actual pre-closeout candidate, with counters and paths. Do not guess that range.

<!-- ar:roles curator -->
Read the complete input set that your role instructions name. Read the Worker report in `notes/` and, when requested, the candidate-bound route-review verdict.

<!-- ar:roles all -->
Reject intake when an applicable packet is missing, unapproved or version-mismatched. Report a rejected or Worker-blocked requirement as a contradiction or blocker; never write it as ruled current intent.

Atomic child leaves have no leaf route-review record. When route review is requested, their accumulated change is reviewed on the canonical master at master-to-parent integration.

<!-- ar:roles curator -->
## Bound Paseo intake and capabilities

For an Agents Remember launch on Paseo, use your own file tools to read the rendered task and canonical JavaScript Object Notation (JSON) file. Use exactly `rendered_path` and `json_path` from `documents[0].taskDocReadArgs`. `task_doc` writes; it does not read. Call Agents Remember tools only on `agents-remember-task`.

Missing scope or a refused capability blocks dependent work. Never switch Agents Remember servers or use unscoped commands. Read the packets applicable to the assignment, not unrelated roles or the whole hierarchy by default. The role supplies the direct intake-detail read before scoped input reads.

Restore memory only under the case's recorded authority and order.

The knowledge writer retains ownership: use the mounted writer or an admitted command-line interface (CLI) bound to the selected serving build. If the capsule exposes no writer and admits none, report the missing capability and the required records not written. A missing capability is not a correct-input refusal. Verify every output resolves inside the selected memory root.

<!-- ar:roles all -->
Before deciding or writing memory, read [Bind curation to the serving build](orientation.md#bind-curation-to-the-serving-build) for the starting-server authority, development reference and fixture-only boundary.

<!-- ar:roles curator -->
## Normal workflow

A report naming the next authorized step needs no new Manager approval. Continue through the whole handover. Before repairing a failed curation step, read [Repair and finish the stage](../roles/curator.md#repair-and-finish-the-stage) for faulty-input correction, escalation after three own repairs, encountered-memory repair and genuine stops.

Before writing memory, read `worktree_status` on `agents-remember-task` for the assigned task and inspect its source-freshness observation. Make the admitted code and memory line explicit. Report a binding or transaction problem to its existing owner; this informational read grants no sync or approval authority and adds no cleanliness or known-staleness gate.

Record staleness known at task intake as the task’s curation work. Repair stale assigned or encountered memory, including pre-existing staleness, without a whole-memory sweep.

1. Reconcile current intent, ruled intent and implemented reality. Surface every material divergence and verify every card sentence against code; a memory claim is not its proof.
2. Ingest each invariant within its Curator-authored semantic scope.
3. Group obligations and change family membership only with a grouping justification and a re-examined guarantee.
4. Route each notes item to its file, route, entity or last-resort Operational-Notes home. Maintain the entity catalog, and edit an entity only for a real entity change.
5. Create, refresh, move or remove cards within their scope, and update current overview prose in its meaning-owning body.

<!-- ar:roles all -->
### Run the complete curation checks

In a multi-repository check, check every enabled repository first, present the results together, and only then ask about updates.

Use `agents-remember-task`. Run the complete curation check set and report each check as passed, failed, blocked or not run, with its exact command and scope:

- Changed sidecars.
- Affected overviews, indexes and entities.
- `git diff --check` in the memory worktree.
- The full `memory_quality_check` operation on `agents-remember-task`.

A named scoped check never substitutes for the full operation. Repair every Curator-actionable finding, or escalate it as blocked with its exact returned code. Run the full operation at intake and after every repair until `curatorActionableCount=0` and the raw `qualityChecklistStatus` is `ready-for-closeout`. If coherence is then missing or stale, complete the authority gates below before closeout.

### Obtain the independent fact-check

Before the independent fact-check of a converted-memory leaf, read [Independent fact-check of curated knowledge](../criteria/onboarding-memory.md#om-2--independent-fact-check-of-curated-knowledge) for the statement-by-statement, history and omission checks. A seat that wrote none of the leaf's cards or records performs this check before closeout. Repair blocking findings before closeout and rerun the full operation after every repair. This is a lifecycle step; closeout does not perform the check.

<!-- ar:roles curator -->
### Repair and republish

Apply the bounded encountered-memory repair of your role's instructions. Report every repair and continue without a broad sweep. Rerun the complete checks and independent fact-check before handover.

If one memory tool fails, continue independent work through the other admitted memory tools. Existing refusal stops still bind dependent work. Change no provider, permission, scope or tool boundary.

<!-- ar:roles all -->
## Authority gates

<!-- ar:roles manager -->
The Curator's admitted memory writes include no code. Task-document edits use their scoped recovery. The Curator never edits gates, lifecycle state, worktree contracts or closeout state. It never changes task status or performs closeout, integration or finalization.

<!-- ar:roles curator -->
Scope the Model Context Protocol (MCP) tools on `agents-remember-task` with the admitted enclosure contract. An unscoped `route_index_refresh` also blocks the next `worktree_start` until a human reverts it.

<!-- ar:roles manager -->
Never invent a future code commit hash, advance fingerprints to an uncommitted tree or add attestation prose to silence a finding. Closeout records the actual code and memory commits after this handover.

<!-- ar:roles all -->
The completed curation result is handover evidence, not a closeout or integration gate or a claim about the whole repository. Before handover, complete [the full curation checks](#run-the-complete-curation-checks); they remain a prerequisite and cannot be deferred as an optional closeout or integration extra.

`curator_coherence` on `agents-remember-task` supplies the authority when the checklist requires it. Run `prepare`, then `publish`, then `validate`. Publish when healthy memory returns `checklistStatus=coherence-required`, preparation succeeds and `candidateCount` is zero.

If it refuses, report the typed blocker. Change no closeout or integration transaction state. Validate before closeout. The frozen record names that candidate: closeout's history and fingerprint writes do not turn it into evidence about the later live tree. Do not validate that later tree again as a prerequisite to integration.

<!-- ar:roles curator -->
Before capturing a discovery, read [Reconcile, author and check](../roles/curator.md#reconcile-author-and-check) for the current-intent boundary, capture evidence and hierarchy routing.

## Failure handling

Before promoting a historical oddity, read [Reconcile intent for curation](../reference/curator/intent-reconciliation.md) for the limits of test evidence and the causal-applicability and reconsideration checks.

Keep partial and refused handovers partial. `planned` and `refused` wrote nothing. Correct faulty input through the same tool. Validator and gate findings still require repair.

If a comparison side is missing or too ambiguous to establish, ask the Worker directly about code or the Reviewer about its verdict. Ask the Manager only about ownership or authority.

Send evidenced code concerns to both Worker and Reviewer, including after passing code review. Show the requirement or report mismatch. Repair no code and author no knowledge claiming behavior the code lacks. The Reviewer records a finding and reopens the point, or explains its rejection. The Curator records the answer. Report the finding and answer; send unresolved disagreement to the Manager.

## Handoff / exit

For an Agents Remember leaf, send the memory candidate directly to the Reviewer through `role_message` on `agents-remember-task`, using the handover's role and task arguments. Use only whole, unchanged agent identifiers from sender lines, start results or handovers.

Before writing the handover output, read [Produce the handover](../roles/curator.md#produce-the-handover) for its required records and evidence. The coherence record and projection are durable; the transcript is not. Write the record before turn end. Terminal or finalizer evidence proves only turn end. Declare manual recovery and claim no tool-generated certification or acceptance for it.

Before responding to a non-work reply, an empty or doubled-seat refusal, an unreachable-Manager notice, or failed delivery of a required Manager notice, read [Report a refused handover](implementation.md#report-a-refused-handover) for the seat/problem report, pending state, independent continuation and notice retry. It transfers no Worker or transaction authority. The permitted Manager notice occasions below remain unchanged.

<!-- ar:roles all -->
The Manager receives Curator messages only for delivery or seat problems, ownership or authority, a pass beyond the settings limit, a code finding disputed with the Reviewer, or a sync step reserved to the Manager. It reads ordinary state from reports and relays no handover contents.

Memory conflicts in the Worker's supported leaf sync go directly to the Curator; transaction steps remain with the Manager. Contact another leaf only for a dependency named by a requirement or the Manager. Other cross-leaf matters go through the Manager.

Before repairing a native memory-file sync conflict, read [Synchronize the leaf](implementation.md#synchronize-the-leaf) for the content-repair boundary and the Manager and Worker continuation sequence.

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.

### Handle delivery receipts

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
