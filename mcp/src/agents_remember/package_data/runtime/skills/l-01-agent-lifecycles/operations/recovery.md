<!-- ar:roles all -->
# Operation — Recovery

Use this operation to resume role execution after interruption or uncertainty. Its task and worktree recovery instructions apply only to task-bound roles. Recovery restores execution; it does not establish acceptance or publication.

Bootstrap recovers in its own chat, using only its own permitted tools. The launcher has no role binding and sends no message through `role_message` on `agents-remember-task`.

## Restore the saved assignment

Restore the same role, task, agent identifiers and report from the handover artifact and durable records. Reload the handover artifact when the assignment is no longer in context. Inspect current AR state before acting. Do not repeat a completed action or create a second owner because context was shortened.

For task-bound work, also recover the exact saved request identifier, recipient and candidate references. Use Agents Remember (AR) task and worktree tools on `agents-remember-task` only for their canonical data and paired workspace facts.

<!-- ar:roles architect,orchestrator,manager -->
## Reconcile an uncertain start or message

An interrupted or uncertain start or message does not authorize a new owner. Before reconciling that start, read [Start one canonical role assignment](coordination.md#start-one-canonical-role-assignment) for the same-request-identifier procedure of `role_start` on `agents-remember-task`.

If message delivery is unknown, ask about it with a new `role_message` on `agents-remember-task` to the same agent. Do not replace that agent. If the outcome stays unknown, preserve it as unknown and report the identifiers.

<!-- ar:roles worker,curator,investigator,reviewer -->
## Ask about an uncertain start or message

A Worker, Curator, Reviewer or Investigator starts no role. If you are unsure whether a start or message happened, ask again with a new message to the same agent, or ask your starter. Never create a new owner. If the outcome stays unknown, preserve it as unknown and report the identifiers.

<!-- ar:roles architect,orchestrator,manager,worker,curator,investigator,reviewer -->
Before restoring an existing AR leaf, read [Start one canonical role assignment](coordination.md#start-one-canonical-role-assignment) for the admitted code, memory and contract pair and the paired-enclosure boundary.

<!-- ar:roles all -->
## Preserve authority while recovering

Do not create an alternate transport, background poller, private store or second owner. Report a refusal's real prerequisite. Report a missing semantic fact with its exact task or requirement field and return it to its owner.

Resuming an agent restores execution, not AR acceptance or Git publication.

## Recover after synchronization

After a clean synchronization, the reviewed result stands. Before recovering after that sync, read [Synchronize the leaf](implementation.md#synchronize-the-leaf) for the Worker's focused rerun, Manager closeout preview and same-Reviewer repair procedure.

<!-- ar:roles curator -->
## Bind curation to the serving build

Before resuming a memory decision or write, read [Bind curation to the serving build](orientation.md#bind-curation-to-the-serving-build) for the starting-server authority, development reference and fixture-only boundary.

<!-- ar:roles architect,orchestrator,manager,worker,curator,investigator,reviewer -->
## Resume message handovers

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
<!-- ar:roles all -->
