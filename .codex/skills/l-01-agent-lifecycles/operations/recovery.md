# Operation — Recovery

**What it covers:** resuming, re-synchronizing, repairing, or retiring a run whose state moved out
from under it. Recovery is an explicit, bounded state transition — never a quiet fallback.

**When it is selected:** a resync is needed, a refusal names a recovery, an operation is interrupted,
a session is resumed or reconnected, a deliverable came out wrong during baseline execution, or a
provider degradation is in progress.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| manager | runs the contract-addressed code/memory resync and reconciles landed code before curator handoff; drives closeout recovery through advertised control actions |
| orchestrator | recovers lineage refusals, reopens-and-reshapes failed deliverables, drives integration/landing recovery, and owns the provider-degradation response |
| worker | reports a red targeted check as an escalation instead of working around it |
| curator | reports scoped onboarding blockers back to the owning seat; never repairs transaction conflicts |
| system-specialist | investigates the degradation and reports before any fix |

## The recovery moves, and when each applies

| Trigger | Exact move |
| --- | --- |
| `source-lineage-stale` / `source-lineage-unavailable` on dispatch | No child was created. Run the refusal's ordered, **contract-addressed** `worktree_sync`, resolve any retained mechanically derivable merge conflict through the advertised continuation, then dispatch the same document + role again. A genuinely semantic conflict follows the ordinary escalation path. |
| Source parent advanced before a handoff | `worktree_status` for the canonical leaf; if lineage is not current, run the contract-addressed `worktree_sync` and reconcile the landed code **before** the curator or closeout consumes it. |
| A later source move after a door declaration | `worktree_sync`, any necessary delta review/curation, then `closeout_door(request={action:"update-provenance", ...})`. Never mutate an old queue row and do not reach for carry-over by default. |
| An interrupted closeout / integration / direct-landing | Resume the exact generation through the advertised `worktree_operation_control` action for that operation kind. The transient landing lock and the closeout queue are **never** recovery evidence. |
| A non-admitting closeout projection (missing, malformed, source-mismatched, `invalid-empty`) | Execute its exact task- or sprint-addressed `rebuild` action and read status again. |
| A wrong deliverable during **baseline** execution | `task_reopen` the leaf **under its own id** and reshape its document; the decision log preserves the journey. New leaves are only for genuinely new change. |
| A wrong deliverable during `reviewMode=fix-verification` | Only the existing owner and the sealed outstanding IDs may drive the repair; an outside-list change goes to the developer. |
| An unavoidable code/memory divergence | `c-11-memory-carryover-from-branch` — a recovery for divergence, not a scheduling strategy. |
| A resumed or reconnected session | `worktree_attach` re-adopts the contract's lifecycle (contract-resolved); the next turn's first AR call auto-resumes a developer hand-off. |
| Unreadable or unreachable owner at a boundary | Fail closed **only at that boundary**, report the task-addressed repair, and never approximate the missing primitive. |
| Provider degradation | Pause provider **starting** (no setup, no `provider_watchers start`, no watcher restart, no `retry_provider_setup`); continue valid providerless work; route investigation to the system-specialist and the stop decision to the orchestrator. |

## Authority gates

- **Recovery is explicit and contract-addressed.** A seat runs the recovery its refusal names; it
  does not invent a recovery path, and it never converts a refusal into a silent fallback.
- **Never approximate a missing primitive** with direct Git, task freezes, queue lifecycle rows,
  scanned journals, or compatibility readers.
- **A repair capability is not a rewrite licence.** `task_reopen` cannot reset a sealed baseline or
  authorize a new issue, route, or scope. A fix-verification successor routes only sealed
  outstanding IDs to their existing owners and cannot create a new fix leaf.
- **Never clear a viable existing occupant or its durable queued brief** merely to make a retry look
  fresh; a retry after an advertised recovery is idempotent.
- **A human-pinned gate that is actually raised is not a recovery step.** It awaits the developer.
- **Retirement is bounded**: a manager may retire only worker/reviewer/curator seats of its **own**
  master (leaf execution seats through the leaf address, plus the master-exit reviewer through the
  master document); the orchestrator holds the portfolio-wide authority for exceptional
  stuck/abandoned/duplicate seats. Owner-never-self-retires always holds, and transcripts are never
  deleted — retiring terminates the session and marks the catalog row.

## Failure handling

- **A red check you cannot fix inside scope is an escalation, not a workaround.**
- **An unresolved transaction conflict** is a developer decision, not a workaround opportunity.
- **A blocked seat** reports status `blocked` with the checks result, an exact escalation, and its
  respawn/recovery state — never a green claim.
- **A missing or stale rebuildable summary never blocks work**: the leaf's own records win and the
  summary is rebuilt from them.
- **Loss of terminal truth** is a runtime failure identified by observer health, not an artifact
  judgement — see `../core/acceptance.md`.

## Handoff / exit

Recovery exits into the operation it repaired: the re-dispatched seat, the resynced worktree, the
resumed operation generation, the reopened-and-reshaped leaf, or the reported degradation. Every
recovery leaves the same durable trail as the operation it restores — a task/door disposition, an
operation-journal generation, or the seat's own artifact — so a successor can reconstruct the state
without a transcript.

**Completion truth** (`../core/acceptance.md`): terminal/finalizer truth attests only that this turn
ended; the owner the recovery exits into opens and validates the artifact itself.
