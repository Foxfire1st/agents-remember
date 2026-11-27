<!-- ar:roles all -->
# Operation — Recovery

Resume execution after interruption or uncertainty (restores execution, never acceptance or publication). Task/worktree recovery applies only to task-bound roles; Bootstrap recovers in own chat; launcher sends no `role_message`.

## Restore the saved assignment

Restore role, task, agent IDs, request ID, recipient, candidate refs, and report from the handover artifact and durable records; inspect Agents Remember (AR) state via AR tools before acting; never repeat completed work or create a second owner after compaction.

<!-- ar:roles architect,orchestrator,manager -->
Reconcile uncertain `role_start` with the same request ID ([start role](coordination.md#start-one-canonical-role-assignment)); query unknown message delivery with a new `role_message` to the same agent (preserve unknown outcomes with IDs).
<!-- ar:roles worker,curator,consolidator,investigator,reviewer -->
You start no role; query the same agent or starter on uncertain starts/messages without creating a new owner (preserve unknown outcomes with IDs).
<!-- ar:roles architect,orchestrator,manager,worker,curator,consolidator,investigator,reviewer -->
Preserve the leaf's admitted code/memory/contract pair ([start role](coordination.md#start-one-canonical-role-assignment)).
<!-- ar:roles all -->

Create no alternate transport, poller, private store, or second owner; report refusal prerequisites or missing fields to their owner.

## Recover after synchronization

After clean sync, the reviewed result stands; follow [Synchronize the leaf](../reference/worker/sync-and-repairs.md#synchronize-the-leaf).

<!-- ar:roles curator -->
Follow [serving-build binding](orientation.md#bind-curation-to-the-serving-build) before resuming memory writes.
<!-- ar:roles architect,orchestrator,manager,worker,curator,consolidator,investigator,reviewer -->
On full-store refusal: [full-store](../reference/message-handover.md#handle-a-full-store-refusal).
<!-- ar:roles all -->
