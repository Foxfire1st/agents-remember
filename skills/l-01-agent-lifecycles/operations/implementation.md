<!-- ar:roles all -->
# Operation — Implementation

The Worker uses this operation to implement the selected leaf's primary requirement and explicit constraints in the supplied code root. It defines the implementation report, freeze and direct handover procedure.

## Establish the inputs

Before reading the task inputs, follow [Establish the assignment](../roles/worker.md#establish-the-assignment) for the exact handover file locations and native file reads. The inputs are the selected requirement packet, rendered leaf task and code worktree for edits. The memory root is read-only context.

Before editing a canonical task document or marking your own completed step, read [Confirm the assignment](orientation.md#confirm-the-assignment) for the primary tool, `task_doc` on `agents-remember-task`, and rendered-copy rules.

Implementation does not expand scope or edit task status, the contract, memory or onboarding state.

## Outputs

### Verify the final candidate

Before each freeze, pass the coding guidelines' handover checks on a clean copy of the final candidate. Name each check, the copy's tree and its result in the freeze report. Repair failures before freezing.

Choose a freeze's focused tests by searching the repository's tracked test files for each touched path or bare name and each touched rule, tool or record identifier. Add tests using a matching test-support file, the knowledge-lookup proofs, and yours. Record names and files in the checks and run them; repairs follow the same rule.

These fast deterministic checks are distinct from full code-quality operations, which require the developer's explicit request.

### Make the measurement handover

For measurement tooling, send the candidate and case-set identity to the Manager. Await the Manager's go on the same route. This simple handover is not a review freeze and starts no seat.

### Write the report

Write one report at the canonical task path. Include:

- the change and changed files;
- requirement and invariant evidence;
- exact checks and results;
- relevant checks not run and why;
- your agent identifier as the `senderLine` of `role_message` on `agents-remember-task` names it, or "agent ID not known" if you sent no message;
- open limitations.

Do not invent fields for a nonexistent data consumer.

For every repaired finding, record its identifier, what was wrong, the files and line ranges changed, and the test that fails before the repair and passes after it, with the result. List a change that answers no finding too.

A check or evidence run carries over only when the files it exercises are byte-identical to those of its last run. Name each carried run with that comparison. For each rerun, name the changed file that required it.

### Hand over the frozen candidate

1. Freeze the candidate and tell the Manager to start the Reviewer.
2. Once the Reviewer exists, send it the freeze directly with `role_message` on `agents-remember-task`. Name the leaf, the report path and the whole next stretch of work. The report names the head and change hash.
3. Repair sealed findings and return a new freeze directly to the same Reviewer.
4. After code review returns `PASS` and the Manager has started the Curator, send the Curator your hand-off list for that freeze directly. Name the same entries and changed paths so the changed-memory pass reads them.

The freeze travels as a file: the message names the freeze report file, never its pasted text. This is a factual handover, not a review brief. Use the sibling role and exact leaf task references in the handover; keep product-supplied identifiers whole and unchanged.

The Manager receives the first-freeze notice so it can start the Reviewer, plus its authority or recovery matters. Do not send it routine report notices or ask it to relay handover contents.

## Boundaries

### Keep the Worker role

Before handing over or attempting a role-start, acceptance or publication action, read [Preserve role and task boundaries](../roles/worker.md#preserve-role-and-task-boundaries) for the Worker's restrictions. An uncertain result does not change the assignment. Follow the role's developer-question rule.

### Repair defects and report a refused handover

#### Report a costly small repair

Fix every measured defect within the task's aim. When one small item costs about an hour, a second repair round or a second freeze, tell the Manager immediately. Give the hours and a proposal to carry or bound its complete repair, then continue the rest. Dropping applies only to nondefect polish.

#### Repair a sealed own-file finding

A sealed finding in this task's own files is repaired directly in your worktree: no plan, account, census or approval goes ahead of the repair, and the Manager gives no go-ahead for it. The full supported commit check and the freeze still apply. The freeze report carries one entry per repaired finding, with its failing-before and passing-after test.

#### Report a refused handover

Report non-work replies and empty or doubled-seat refusals to the Manager once, with the seat and quoted text. Never pick a candidate yourself. When the Manager gives the occasion, send the handover yourself; it relays none of the contents.

For a Worker, Curator or Reviewer whose required Manager notice fails delivery, record it as pending, continue independent work and retry the notice through `role_message` on `agents-remember-task`. Do not turn that delivery failure into a question to the developer in your own chat.

### Synchronize the leaf

When the master line moves, run the supported sync of your leaf.

- When no path changed on both sides, run the focused tests once more and tell the Manager. The tools derive the mechanical checks again, and the Manager's closeout preview decides. Send no new freeze to the Reviewer and begin no round. Request no Reviewer confirmation or receipt.
- When a path changed on both sides or a focused test fails, repair exactly that and send the same Reviewer one look at the repair.

Route memory-file conflicts to the Curator. Ask the Manager only for a sync step the operation reserves to it.

For a native memory-file conflict, the Curator repairs only memory content inside its admitted write surface. The Manager captures, verifies and stages the resolution. Then the Worker calls `worktree_sync` on `agents-remember-task` with the exact contract and `resolution_action="continue"`. The continuation refuses unmerged or unstaged content. The Curator stages no files and changes no transaction authority. The Worker repairs no transaction authority, stages no files and takes none of the Manager's closeout writes.

### Respect permissions and the message procedure

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.

### Handle delivery receipts

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
