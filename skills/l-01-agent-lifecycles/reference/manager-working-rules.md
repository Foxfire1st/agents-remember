# Manager working rules

## Bind closeout trees to the latest passing verdicts

Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, never a message.

## Recover silent turns waiting on helpers

When every agent of a leaf is idle, read each one's last lines. A turn that ends silently while its helpers still run has stopped: tell that seat to collect its results and finish. A valid declared helper wait remains a wait.

## Update the master after starts, handovers and landings

In the turn of each start, role handover and landing, update the master document's parts your act changes: its status note, counts, order state and In work now section with task, stage, agents and since time. Keep product-mirrored stage fields with their existing owner instead of writing a contradictory second status.

## Count failing test nodes and assign owners

Count a lane's failures from its FAILED, SUBFAILED and ERROR lines grouped by test node, and give every failing node an owner.

## task retirement; direct reference to reference/refused-write.md

Use the product tool first for task retirement or folder movement. On refusal of correct input, follow the D627/D631 ladder: record the refusal, tell the owner and perform the logged manual step. Do not retain a blanket ban on hand movement.

## Honor developer-stopped seats

Restart or replace a developer-stopped or archived seat only on the developer’s explicit word.

## Route collection and import regressions

A mark that a test file fails at collection or does not import is a regression of a landed leaf, not a mark: route its repair at once.

## Continue unfinished authorized work

After a report, check whether the sender is still in a turn; if it ended with unfinished authorized work and no valid named wait or stop, send a continuation at once.

## Preserve work when replacing a seat

Replacing a seat never discards its work: the successor saves the worktree state as evidence, keeps what is right, repairs the rest, and its freeze names each change kept, rewritten or removed.

## Finish documentation before master completion

Before completing a master, make sure its documentation task has changed or confirmed every document its tasks made stale. Do not report the master complete while that documentation remains stale.

## Escalate refused pushes

A refused push is never forced: --no-verify needs the developer's word for that occasion, and the failure goes to him through your parent.
