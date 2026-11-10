# Recover seat work

This file gives managers, Architects and Orchestrators acting within their assigned scope the rules for recovering idle, unfinished, stopped or unavailable-provider work. It grants no additional authority.

## Recover an idle or unfinished seat

When every agent of a leaf is idle, read each agent's last lines. If a turn ended silently while helpers still run, tell that seat to collect its results and finish. A valid declared helper wait remains a wait.

## Preserve stopped seats and replacement work

Restart or replace a developer-stopped or archived seat only on the developer's explicit word.

Replacement never discards a seat's work. The successor saves the worktree state as evidence, keeps what is right and repairs the rest. Its freeze names each change kept, rewritten or removed.

## Recover unavailable-provider work through the authorized role set

When a provider is unavailable, move its work in the same turn to an available model through an already authorized fallback role set. Start replacement seats with `role_start` on `agents-remember-task`. Preserve their work as [Preserve stopped seats and replacement work](#preserve-stopped-seats-and-replacement-work) requires, and recover pending work and messages.

For a defaults switch, the Manager tells the Architect through its existing parent route. Under direct coordination, the Architect uses its own developer-decision route. Do not wait for a reset or run a probe loop.

A developer-specified model or an unapproved substitute needs the developer's decision through the upward route before substitution. Handle a safety refusal through the same model-change authority boundaries instead of retrying the refused provider model; the work does not wait for that provider.
