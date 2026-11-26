# Closeout recovery, projections, and coordination notices

Architects, Orchestrators and Managers read this file when recovering an unfinished or blocked closeout, rebuilding task projections after edits, running flat-mode direct closeout, handling an Orchestrator recipient switch, or relaying a child's permission notice.

## Recover an unfinished closeout

Read the result of `worktree_closeout_apply` for the state reached and any blocker. If closeout was interrupted, inspect the per-leg transaction evidence. After correcting a blocker, rerun `worktree_closeout_apply` with the applicable authority and explicit messages for enabled legs.

When closeout, coherence or landing returns a gate refusal (`closeout-gate-blocked`, `curator-coherence-knowledge-gate-refused`, `closeout-door-route-review-blocked`, `knowledge-gate-refused` or `direct-landing-knowledge-gate-refused`), send the exact findings to the Curator or owning seat for repair and retry the owning route on the corrected pair. Bypass no gate.

For a direct landing, read `worktree_status` for the current generation and advertised actions. Use `worktree_operation_control` with that generation for an advertised direct-landing action, and read saved journal records for audit.

The Manager applies routine paired closeout under its standing leaf authority without Orchestrator release. Send the coordinating Orchestrator the result or refusal, leaf, master and sprint references, blockers and acceptance facts. Report facts only; do not rank other masters.

## Apply task edits and rebuild projections

Task documents remain authoritative during every scheduling and operation phase. Task edits never wait for closeout scheduling. Apply every intrinsically valid `task_doc` mutation first and inspect its machine-readable `projectionEffects`. These name every sprint in the before/after union and the exact rebuild action for any projection left invalid-empty. Relay every incomplete effect's exact `nextAction` to the Orchestrator for sprint-addressed rebuild.

Never whitelist task operations, patch a stale queue row, or treat queue or operation state as a task lock. If the edit affects evidence used for closeout, re-prove that evidence before retrying `worktree_closeout_apply`.

## Flat-mode direct paired closeout

Flat mode means only the developer-chosen direct-coordination exception where neither a Manager nor an Orchestrator owns closeout. In that case, the owning Architect may coordinate the paired closeout (`worktree_closeout_preview` and `worktree_closeout_apply`) only with the developer's explicit commit approval for that closeout, or with authority the developer delegated for the series and recorded in the task document, after independent review and required curation evidence exist.

Call the paired closeout yourself and start no Manager for that step alone. This grants no other execution authority. Human-pinned approvals remain human; never substitute raw Git or self-approval.

## Orchestrator recipient switch and closeout notices

When a second master starts while a Manager works directly under the Architect, the Architect starts one Orchestrator on the sprint and passes it the existing Manager's exact agent identifier, canonical master reference and current state so the Orchestrator reuses that Manager and starts no duplicate. The Architect names the Orchestrator's agent identifier to the existing Manager in one message.

The Manager records that instruction and the Orchestrator's agent identifier in its report and uses it as its reporting recipient. The Manager's launch parent stays fixed; only that Architect instruction changes its reporting recipient.

For a closeout-result notice to the coordinating Orchestrator, send the exact references, result or refusal, blockers and acceptance facts as [Recover an unfinished closeout](#recover-an-unfinished-closeout) specifies. This is the closeout-result exception; other routine status-only notices remain excluded.

## Relay a child's permission notice

If `role_message` returns `held` because a child awaits a permission prompt, or a wait returns `permission-pending`, tell your parent immediately. Without a parent, tell the developer in your own chat.

Name the child's role, task number, what the task does, agent identifier, and the permission as supplied, or "not supplied" if none was named. Each parent passes the notice upward unchanged. At the top, tell the developer which chat to open.

Whenever you say agents await the developer, list each role, task number, what the task does and agent identifier, plus a dashboard chat number if shown. Never give only a count. Do not ask the developer to confirm an answered prompt.

Before deciding a formal permission, follow [Formal permissions](../../../operations/orientation.md#formal-permissions) for the Architect's own-seat answers and the prompts the developer decides. Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](../../../operations/orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.
