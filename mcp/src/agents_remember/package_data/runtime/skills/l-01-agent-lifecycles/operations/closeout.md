# Operation — Authorized Closeout

Manager or Orchestrator coordinates Agents Remember (AR) paired closeout via `worktree_closeout_preview` and `worktree_closeout_apply` (never Worker, Reviewer, or Curator).

## Confirm the authorized owner

Manager uses standing leaf authority for routine paired closeout; Orchestrator uses supplied task authority; flat-mode Architect direct closeout follows [flat-mode closeout](../reference/coordination/shared/closeout-recovery.md#flat-mode-direct-paired-closeout). On routine Manager closeout, clean preview returns `would-closeout`, `commit-approval-pending`, `request_commit_approval`; when `commit_approval_required` is true, pass standing leaf authority as `intent_note` to `worktree_closeout_apply` with previewed leg messages.

## Bind the accepted inputs and evidence

Bind exact code/memory worktrees, canonical task/contract, candidate tips, changed paths, checks, and review/curation reports. When clean sync changes the code or memory candidate, obtain the existing Curator's refreshed `knowledge_read` dependent-document read (`dependent(compact=true)` for triage; full rows when evidence needs them) for the resulting pair before routine Manager closeout (existing review stands). Carry targeted checks (no blanket full-suite gate; keep gaps visible) plus Worker's listed final checks run once on the code-`PASS` tree (repaired targeted results beside it after failures; closeout runs no final checks). Curation, coherence, conflict/authority checks, and independent review govern; never substitute raw Git.

## Judge the exact candidate at each publication step

Converted memory: preview/apply judge the exact candidate pair via the invariant gate and refuse non-current entries in the task's code diff; leaf integration re-judges; master/checkpoint landing validates master memory against parent tip and net code-diff currentness (final master proves all non-abandoned leaves landed; checkpoint proves landed chain); direct landing judges its exact pair.

## Repair a gate refusal through its owner

Send `knowledge-gate-refused` (closeout/integration) or `direct-landing-knowledge-gate-refused` (direct landing) findings to Curator for repair, then retry the owning route; bypass no gate.

## Verify the result before claiming completion

Read back tool results and verify exact code/memory outcomes before claiming commit, integration, push, or acceptance; human approvals stay human.
