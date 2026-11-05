<!-- ar:roles all -->
# Operation — Implementation

The Worker implements the selected leaf's primary requirement and explicit constraints in the supplied code root. Read the rendered task-document files named in your handover with your harness's own file tools before acting; use task_doc first for canonical task-document editing and your own completed step marking; edits to rendered copies are never read back and are lost on the next publication. Read [correct-write recovery]({{detail:reference/refused-write.md}}) when a covered writer or record tool refuses a correct write, not before. The owner's next task_doc publication refreshes the rendered copies. Its inputs are the selected requirement packet, rendered leaf task and code worktree for edits, with the memory root as read-only context. It does not expand scope or edit task status, contract, memory, or onboarding state.

## Outputs

Before each freeze, pass the coding guidelines’ handover checks on a clean copy of the final candidate. The freeze report names each check, the copy’s tree and its result; repair failures before freezing. These fast deterministic checks are distinct from full code-quality operations requiring the developer’s explicit request.

For measurement tooling, the simple handover names the candidate and case-set identity; the Manager returns the go on the same route. It is not a review freeze and starts no seat.

One report at the canonical task path records the change, files, requirement/invariant evidence, exact checks and results, the relevant checks not run and why, your agent ID as the `senderLine` of `role_message` on `agents-remember-task` names it (write "agent ID not known" when you sent no message), and open limitations. Do not invent report fields for a nonexistent data consumer.

A failure counts as pre-existing only when the base fails with the same cause, not only under the same test name.

Freeze the candidate and first tell the Manager to start the Reviewer. Once the Reviewer exists, hand the freeze directly to that Reviewer with `role_message` on `agents-remember-task`, naming the leaf, head and change hash in your report, report path, and whole next stretch. The freeze travels as a file: the message names the freeze report file, never its pasted text. This is a factual hand-over, not a review brief. The freeze report holds one entry per repaired finding: its ID, what was wrong, the files and line ranges changed, and the test that fails before and passes after, with its result; a change that answers no finding is listed too. A check or evidence run carries over when the files it exercises are byte-identical to those of its last run; the freeze report names each carried run with that comparison, and each re-run with the changed file behind it. Repair sealed findings and return a new freeze directly to the same Reviewer. After code PASS and the Manager has started the Curator, send your hand-off list for that freeze directly to the Curator. It names the same entries and the changed paths, so the changed-memory pass reads them. Use the sibling role and exact leaf task references in the handover; product-supplied IDs stay whole and unchanged. The Manager receives the first freeze notice to start the Reviewer, plus its authority or recovery matters, not routine report notices or relayed contents.

## Boundaries

Count a hand-over as taken only when the recipient's reply or report names the exact object and shows the requested work or its start.

Fix every measured defect inside the task’s aim. When a small fix has taken about an hour or another repair round, report how to bound its complete repair. Dropping applies only to polish that is not a defect.

Leave the candidate uncommitted. No Worker self-acceptance, Reviewer work, Curator writes, closeout, merge, or push. A Worker starts no role, including a Reviewer or Curator, and retains the same assignment after an uncertain result. Developer questions follow the role's developer-question rule.

When the master line moves, run the supported sync of your leaf. When no path was changed on both sides, your focused tests run once more and you tell the Manager, whose closeout preview decides; no new freeze goes to the Reviewer and no round is begun. Where a path was changed on both sides, or a focused test fails, repair exactly that and send the same Reviewer one look at the repair. Route memory-file conflicts to the Curator; ask the Manager only for a sync step the operation reserves to it.

A sealed finding in this task's own files is repaired directly in your worktree: no plan, account, census or approval goes ahead of the repair, and the Manager gives no go-ahead for it. The whole supported commit check and the freeze stay as today.

Report non-work replies and empty or doubled-seat refusals to the Manager once with the seat and quoted text; never pick a candidate yourself. You still send the hand-over yourself when the Manager gives the occasion; it relays none of its contents. Record and retry an unreachable-Manager notice rather than put that transport failure to the developer.

A native memory-file conflict goes directly to the Curator for content repair. The Manager captures, verifies and stages that resolution; the Worker then calls `worktree_sync` on `agents-remember-task` with the exact contract and `resolution_action="continue"`. The continuation refuses unmerged or unstaged content. Do not repair transaction authority, stage files or take the Manager's closeout writes.

Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.

Read [message handover]({{detail:reference/message-handover.md}}) when you reach a step another seat awaits, a handover, or a held/full-store receipt, not before.
