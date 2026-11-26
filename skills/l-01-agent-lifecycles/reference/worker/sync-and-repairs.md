# Leaf synchronization, repairs, and non-routine handovers

Workers, Managers, Curators and Reviewers read this file when a leaf must synchronize with a moved master line, hit a repair or post-pass final-check exception, send a measurement handover, handle a refused handover, or record out-of-scope observations.

## Synchronize the leaf

When the master line moves, the Worker runs `worktree_sync` for its own leaf.

- **Clean sync (no path changed on both sides)**: The Worker reruns focused tests and reports the updated code and memory pair to the Manager. Product tools derive mechanical checks again. When clean sync changes the code or memory pair, the Manager obtains the Curator's refreshed dependent-document read before routine closeout. Send no new freeze to the Reviewer, open no review round, and request no Reviewer confirmation or receipt.
- **Overlapping paths or failing focused tests**: When a path changed on both sides or a focused test fails after sync, the Worker repairs exactly that conflict or failure and sends the same Reviewer one look at the repair.
- **Native memory-file conflicts (`sync-resolution-required`, `memory-sync-choice-required`, `sync-active`)**: Route memory-file conflicts directly to the Curator, and ask the Manager only for a sync step reserved to it. The Curator repairs only memory content inside its admitted write surface and stages no files or transaction authority. The Manager captures, verifies and stages the resolution. The Worker then calls `worktree_sync` with the exact contract and `resolution_action="continue"`. Continuation refuses unmerged or unstaged content. The Worker repairs no transaction authority, stages no files, and takes none of the Manager's closeout, integration, status or acceptance writes.

## Focused test selection and freeze report details

Run every candidate command in the worktree's own environment (`system/tools.md`). Select focused tests by searching tracked tests for touched paths, bare names, rules, tools and record IDs, plus test-support, knowledge proofs and new tests (apply the same search on repairs; full suites require a developer request; `## Final checks` run only after code PASS). Prove whole-test-tree collection before freezing any product-code removal or rename.

In the freeze report (using `../../templates/turn-report.md` and `system/code-quality-report-template.md` after source edits), record the `role_message` `senderLine` ID (or `"agent ID not known"` when no message was received), own/instrument repairs made, byte-identical carry-overs, rerun causes, per-finding repair entries (ID, files/lines, before/after test) and any non-finding changes. Map numbered evidence items to report files at intake, freeze and final checks, and name any gaps.

## Worker stops, unfinished turns, and step marking

Stop for an open permission prompt, a standing refusal outside covered-write recovery, a question about what the requirement means, or an effect outside the assigned worktree, scratch folder, and task. If you have a parent and remain unfinished at turn end, tell that parent what and whom you await via `role_message`. Mark only your own completed step; if no step names your part, report that and mark nothing. Incomplete, blocked, progress and escalation reports never mark unfinished work done.

## Repair limits, costly small repairs, and record-only repairs

- **Three-repair stop**: When the same check or finding still fails after three of the Worker's repairs, stop repairing. Write what you tried and what still fails, then tell the Manager; the Manager steps the seat up or answers otherwise.
- **Generalize persistent failures**: When a finding stays open after a repair, the next repair must not merely answer the new case. Write down the property for every case, either show why the construction holds it or change the construction, and state which you did in the report.
- **Costly small repairs**: Fix every measured defect within the task's aim. When one small item costs about an hour, a second repair round or a second freeze, tell the Manager immediately with the hours and a proposal to carry or bound its complete repair, then continue the rest. Dropping applies only to nondefect polish.
- **Record-only repairs**: When every candidate finding is closed and only a finding on the Worker's record (report, evidence table, hash or anchor) remains, repair the record. The candidate stays unchanged, and the same Reviewer checks the record on its last recorded round, without opening a new round.

## Handle post-PASS final-check failures

A final check that fails on the passed tree is not a round's finding. The Reviewer seals it through the third post-seal route in [Post-seal routes](../review/rounds-and-late-findings.md#post-seal-routes).

The Worker repairs the failure, the same Reviewer reviews that point, and the failed check together with the repair's focused tests run on the new tree; the full run is not repeated. For a failed multi-step run, the step that failed is the check, and that step with the repair's focused tests is the successor's evidence, recorded beside the original report rather than in a second full run.

## Make the measurement handover

For measurement tooling, the Worker sends the candidate and case-set identity to the Manager and awaits the Manager's go on the same route. This handover is not a review freeze and starts no seat.

## Report a refused handover

Report non-work replies and empty-seat or doubled-seat refusals to the Manager once, naming the seat and quoting the refusal text. Never pick a candidate yourself. When the Manager gives the occasion, send the handover yourself; the Manager relays none of the contents.

When a required Manager notice fails delivery for a Worker, Curator or Reviewer, record the notice as pending, continue independent work, and retry the notice through `role_message`. Never turn a delivery failure into a question to the developer in your own chat.

## Record out-of-scope weaknesses and dependent documents

When another task or an out-of-scope manifestation is affected, report its exact path and task reference to the owner; do not edit that sibling as part of this leaf.

In the handover, name code weaknesses encountered outside this task's scope and documents describing code you write or change, using the two record kinds in [producer record kinds](../../templates/curator-handoff-list.md#the-two-record-kinds-a-producer-may-name):

- **Weakness**: file and symbol, kind (`context`, `compute`, `storage`, `time`, `fault` or `debt`), one-sentence description and any measurement.
- **Dependent document**: document path and every place it stands on.

Repair a weakness inside this task's scope or leave it as a review finding; do not record it instead. Report a fault to the Manager immediately if it makes this task's result wrong, loses data or opens the installation.

A recorded weakness is not a finding and blocks nothing. Changing code creates no obligation to change its documents. Record only what you meet while working; do not search for weaknesses merely to populate the list.
