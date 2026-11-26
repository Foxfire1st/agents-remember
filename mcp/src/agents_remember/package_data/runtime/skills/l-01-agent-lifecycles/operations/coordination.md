<!-- ar:roles all -->
# Operation — Coordination

Start/message Agents Remember (AR) roles via `role_start`/`role_message` with canonical refs (server binding = sender/parent; launcher sends no `role_message`).

## Choose the coordination owner

1 master -> 1 Manager; 2+ concurrent masters -> 1 sprint Orchestrator (1 Manager/master). Direct Architect coordination or single-master Orchestrator needs developer instruction; report refused starts without fallback.

## Start one canonical role assignment

Call `role_start` with role, refs, and request ID (Architect: all 6; Orchestrator: all except Orchestrator, sprint; Manager: Worker/Reviewer/Curator/Investigator, master). Keep AR-paired enclosure (no Paseo worktree/global memory); save IDs/paths; reconcile uncertain starts via same request ID.

<!-- ar:roles architect,orchestrator,manager -->
## Start leaf stages and deliver the first intake

The Manager calls `role_start` once per stage: Worker first, Reviewer at the freeze, and Curator when curation begins. Start only the Worker at build. The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager first. Ordinary curation follows code `PASS` under Manager's master; once Reviewer exists, Worker sends freeze directly.

Separate cases: (1) assigned packet cold read without an early normal leaf Reviewer; (2) when an approved packet's prerequisite sync reaches Curator-owned memory reconciliation, the Manager starts the Curator for that reconciliation (ordinary delivery curation and knowledge check still follow code `PASS`).

Send intake before inspection (mode, task/purpose, seam, req set, tools; candidate/attempts at freeze/exit). Leaf seats message directly via `role_message` (freeze->Reviewer->Worker; passed freeze & lists->Curator<->Reviewer; post-`PASS` final checks->Manager & Reviewer; Curator code concerns: [exceptions](../reference/curator/exceptions-and-recovery.md#memory-tool-failures-code-concerns-and-sync-conflicts)).

<!-- ar:roles architect,orchestrator -->
Manager decides gates and retains closeout/integration/status/acceptance/landing order without relaying freeze/finding/verdict/memory changes.

<!-- ar:roles architect,orchestrator,manager -->
## Preserve landing order and review records

Keep serial landing order (sync once to prior landing); see [rounds](review.md#rounds), [memory](review.md#boundaries).

<!-- ar:roles manager -->
<!-- ar:gives leaf-review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, tools-file -->
<!-- ar:gives review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, candidate-identity, attempt-records, tools-file -->
## Deliver the Manager's first Reviewer intake

Receive only admitted notices; never relay handovers or retry `intent-review-open` before Architect `go`. After `role_start`, send Reviewer's intake via `role_message` (mode, purpose, `system/tools.md`, req IDs/versions/packets, approval record); it inspects nothing earlier. For integration/master-exit, start on master's acceptance/delivery-proof leaf (ask Architect if unnamed) and add candidate & attempts.

<!-- ar:roles architect,orchestrator,manager -->
## Address and deliver a message

Send via `role_message` by ID or role+task refs (role != self; invent no IDs). Empty seats report upward; 2 occupants refuse naming both; active turns may `held` or cancel (never into permission prompts); archived seats refuse (use successor). `wait` returns text, failure/cancel, `permission-pending`, timeout, or `accepted`; never mutually wait.

## Message files and receipts

`body` unlimited; over-limit `text` refuses; `attach` hashes files (Secure Hash Algorithm 256-bit (SHA-256)); reply to return address ([receipts](../reference/message-handover.md#find-filed-messages-and-receipts)).

## Recover an unfinished closeout
`worktree_closeout_apply`/`worktree_status`/`worktree_operation_control`: [recovery](../reference/coordination/shared/closeout-recovery.md#recover-an-unfinished-closeout).

## Apply task edits and rebuild projections
Apply `task_doc` edits (`projectionEffects`): [rebuilds](../reference/coordination/shared/closeout-recovery.md#apply-task-edits-and-rebuild-projections).

## Relay a child's permission notice
Permission `held`/`permission-pending`: [relays](../reference/coordination/shared/closeout-recovery.md#relay-a-childs-permission-notice). Reassess canonically; never poll.
<!-- ar:roles all -->
