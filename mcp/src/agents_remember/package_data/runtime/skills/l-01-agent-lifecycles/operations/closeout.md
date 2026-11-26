# Operation — Authorized Closeout

The Manager or Orchestrator uses this operation to coordinate an existing Agents Remember paired closeout. It defines evidence and authority. The AR tools `worktree_closeout_preview` and `worktree_closeout_apply` on `agents-remember-task` own the transaction.

## Confirm the authorized owner

The Manager uses its standing leaf authority for routine paired closeout. An Orchestrator uses its supplied task authority.
In a flat run with neither Manager nor Orchestrator, the owning Architect may coordinate the same transaction only with the developer's explicit commit approval for that closeout, or with authority the developer delegated for the series and recorded in the task document.

For routine Manager leaf closeout, a clean preview returns `would-closeout`, `commit-approval-pending` and `request_commit_approval`. If `commit_approval_required` is true, use the standing leaf authority as the `intent_note` for `worktree_closeout_apply`, with the previewed messages for enabled legs on `agents-remember-task`. Holds, refusals and closeout exceptions keep their existing routes.

Closeout is never a Worker, Reviewer or Curator task.

## Bind the accepted inputs and evidence

Before calling the existing closeout owner, read its current preview and authority contract. Bind the exact code and memory worktrees, canonical task and contract, candidate tips, changed paths, check results, and review and curation reports.

When clean sync changes the code or memory candidate, obtain the existing Curator’s refreshed dependent-document read for the resulting pair before a routine Manager closeout. Use the existing review.

Carry targeted-check results under the closeout preview's accepted code and memory input. Keep failures, unrun checks and missing evidence visible to the owner. Do not replace them with a blanket full-suite gate.

Use the Worker's report of listed final checks run once on the tree that passed code review. After a failure, read the repaired tree's targeted results beside it. Closeout runs no final checks. If the requirement lists none, no final-check report is due.

Required curation and coherence, and actual conflict and authority checks, still govern the transaction. Requested independent review remains separate evidence.

The owner validates and applies the paired transaction. Do not reproduce its logic through raw Git commands.

## Judge the exact candidate at each publication step

For converted memory:

- Closeout preview and apply judge the exact candidate code and memory trees through the invariant gate.
- The closeout also refuses an entry at a path in the task's own code diff that is not current, as the master's landing does.
- Leaf integration judges the closeout pair again.
- A master or checkpoint landing validates the master's memory commit against the parent line's memory tip. It also checks that entries at paths in the master's net code diff are current at its code commit.
- Final master landing proves that every non-abandoned leaf landed. A checkpoint proves the chain already landed.
- Direct landing judges its exact candidate pair before publication.

## Repair a gate refusal through its owner

A gate refusal prevents publication and names the findings. Closeout and integration report `knowledge-gate-refused`; direct landing reports `direct-landing-knowledge-gate-refused`.

Send the findings to the Curator for repair, then retry the owning route on the corrected pair. Bypass no gate.

## Verify the result before claiming completion

A finished agent turn is not closeout. Claim commit, integration, push or acceptance only after reading the owning tool's result back and verifying the exact code and memory outcome. Human-pinned approvals remain with their named authority.

### Handle delivery receipts

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
