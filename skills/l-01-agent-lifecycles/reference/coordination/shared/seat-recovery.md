# Recover seat work

This file gives managers, Architects and Orchestrators acting within their assigned scope the rules for recovering idle, unfinished, stopped or unavailable-provider work. It grants no additional authority.

## Recover an idle or unfinished seat

When every agent of a leaf is idle, read each agent's last lines. If a turn ended silently while helpers or a process it started still run, tell that seat to collect its results and finish. A valid declared helper or background wait (`WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:`) remains a wait until its next undeclared end.

## Preserve stopped seats and replacement work

Restart or replace a developer-stopped or archived seat only on the developer's explicit word.

Replacement never discards a seat's work. The successor saves the worktree state as evidence, keeps what is right and repairs the rest. Its freeze names each change kept, rewritten or removed.

## Recover unavailable-provider work through the authorized role set

When a provider is unavailable, move its work in the same turn to an available model through an already authorized fallback role set. Start replacement seats with `role_start` on `agents-remember-task`. Preserve their work as [Preserve stopped seats and replacement work](#preserve-stopped-seats-and-replacement-work) requires, and recover pending work and messages.

For a defaults switch, the Manager tells the Architect through its existing parent route. Under direct coordination, the Architect uses its own developer-decision route. Do not wait for a reset or run a probe loop.

A developer-specified model or an unapproved substitute needs the developer's decision through the upward route before substitution. Handle a safety refusal through the same model-change authority boundaries instead of retrying the refused provider model; the work does not wait for that provider.

## Retire child scope and active turns

Call `retire_child` to archive another seat only within your scope. Architects cover all scopes; Managers cover their master and leaves; Orchestrators cover their sprint, masters and leaves.

- **`outside-scope`**: Nothing is archived when the target seat lies outside your scope. Route the retirement request to a seat whose scope covers the target (its owning Manager, Orchestrator or Architect) instead of retrying from an unauthorized seat.
- **`agent-in-turn`**: Nothing is archived when the target seat has an active turn or running sub-agents. Either wait for the turn to settle, or pass `interrupt_turn=true` when the seat's work is already finished (for example, an Investigator whose reporting turn is still active) or must stop immediately.
- **`open-children`**: Nothing is archived while live, unarchived child seats remain under the target. Retire those descendant seats first (deepest first), or replace the parent seat through its authorized replacement route instead of retiring it with open children.
- **Unresolved launches and transient failures**: When `retire_child` returns `launch-unresolved`, let the launch settle or have its starter reconcile the original request before retiring. When it returns `host-unreachable`, `archive-refused` or `retire-record-failed`, retry the identical retirement once the refusal or connectivity condition clears so the receipt reconciles cleanly.
