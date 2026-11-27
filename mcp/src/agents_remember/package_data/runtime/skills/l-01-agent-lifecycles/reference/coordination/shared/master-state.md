# Maintain master state

This file gives Managers, Architects and Orchestrators acting within their assigned scope the rules for updating master state through completion. It grants no additional authority.

## Update the master after starts, handovers and landings

In the same turn as each start, role handover and landing, update the master document's parts changed by that act: its status note, counts, order state and **In work now** section. Include the task, stage, agents and time since that state began. Leave product-mirrored stage fields with their existing owner; do not write a contradictory second status.

At each status read, compare the master page with the Manager's last handover, renew lagging parts and tell the Manager. Identify stalls from task progress: fifteen minutes without an active turn, or a ready task left unstarted.

## Finish documentation before master completion

Before completing a master, check that its documentation task has changed or confirmed every document made stale by the master's tasks. Do not report the master complete while that documentation remains stale.

## End a standalone master

A standalone master (a series that no sprint commands) whose pull request has merged onto the target branch ends in three steps, run in order by the Manager:

1. Call `worktree_record_landing` on `agents-remember-task` with the merged code commit (and the merged memory commit where external memory is configured). This records the pull-request landing and refuses an abandoned contract or task document.
2. Call `lifecycle_finalize_task` on `agents-remember-task` on the series contract. This cleans up the series branch and worktree folder, releases the series activation, and marks the master document `Completed`. Do not call `task_doc set_status` before finalization.
3. Call `task_doc` with `operation="retire_master"` on `agents-remember-task` (dry run first) to archive the completed master folder into `0_archive/`. Finalization never archives a series master.

## Abandon a master and settle a landing's debt

Set an atomic master's document to `abandoned` or `Completed` before `worktree_abandon`: the call refuses `abandon-master-not-terminal` while the document reads anything else, and it writes `abandoned` there, whatever it read. A master whose work already stands on its source line may still be abandoned; the answer measures that by ancestry, not by the recorded landing cells, and carries `landedWork` as `{state, branch, line}` with a sentence in its `summary` that nothing of the work is lost.

A recorded landing of a master that leaves entries stale at a path the master changed answers `staleEntries`: the count, the entries' own sentences, the record of each entry and the one remedy, a knowledge-maintenance leaf that re-anchors each entry. The contract keeps them as the master's debt. A cleanup that refuses because the integration is incomplete names `worktree_record_landing` as its next step, and a leaf closeout still refuses such an entry.
