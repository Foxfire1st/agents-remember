# Master-Handover-Packet Template

The artifact a **manager** hands to the **orchestrator** at master exit (`roles/manager.md`), after the
master-exit adversarial seam. The artifact is durable and terminal/finalizer truth wakes the current
orchestrator. It is what the
orchestrator integrates a completed master into the super branch from.

## Rules

1. Post it only **after** the master-exit verdict exists — the verdict reference is a required slot.
2. Name the integration branch precisely; the orchestrator bases its C-11 integration on it.
3. The carry-over state must let the orchestrator's `c-11-memory-carryover-from-branch` integration run
   without re-deriving what landed.
4. Do not address an orchestrator occupant. `message_parent` is available for clarification or a
   blocking issue; ordinary completion comes from the packet plus terminal/finalizer truth.

## Shape

```md
# Master Handover — <master id> · <master title>

| Field              | Value                                        |
| ------------------ | -------------------------------------------- |
| master             | <master id / task_doc path>                  |
| manager seat       | <master task_doc path> + manager             |
| integration branch | <branch ref the leaves landed on>            |
| handover evidence  | <review verdict / delegated decision ref>    |
| base               | <super branch @ commit the master based off> |
| verdict            | <master-exit verdict artifact ref>           |
| verdict outcome    | pass | pass-with-notes                        |
| written            | <YYYY-MM-DDTHH:MM>                            |

## Change-Set Summary
- <what this master delivered, master-granular>
- Leaves landed: <leaf id> → <one-line outcome>, …

## Requirements / Steps Completion
- All master requirements addressed: yes | with justified deltas (decision-log refs: …)

## Carry-Over State (for the orchestrator's master → super C-11)
- Memory rows parked / carried: <summary>
- Ledger maps every leaf commit: yes | <gap>
- Single-siding notes (if this master overlaps another strand): <which memory to defer / dedup>

## Known Follow-Ups
- <fix leaf the verdict named but scoped as post-integration> | none

## Reachability
- The `(master task_doc, manager)` seat remains structurally reachable across occupant replacement.
```
