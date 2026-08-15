# Master-Handover-Packet Template

The artifact a **manager** hands to the **orchestrator** at master exit (`roles/manager.md`), after the
master-exit adversarial seam. The artifact is durable and terminal/finalizer truth wakes the current
orchestrator. It tells the orchestrator which completion boundary is ready: the final accumulated
organizational candidate containing prior landed contributions plus the proposed final leaf, or
one isolated atomic branch ready to land.

## Rules

1. Post it only **after** the master-exit verdict exists — the verdict reference is a required slot.
2. Name `executionNature` and the exact scope. An organizational master names the
   prior landed leaf commits plus the proposed final leaf
   and the exact proposed final super candidate; it has no
   master branch. An atomic master names its isolated branch.
3. State code/memory ancestry and ledger facts. Carry-over appears only when actual divergence
   required the recovery; it is not the normal landing plan.
4. Do not address an orchestrator occupant. `message_parent` is available for clarification or a
   blocking issue; ordinary completion comes from the packet plus terminal/finalizer truth.

## Shape

```md
# Master Handover — <master id> · <master title>

| Field              | Value                                        |
| ------------------ | -------------------------------------------- |
| master             | <master id / task_doc path>                  |
| manager seat       | <master task_doc path> + manager             |
| execution nature   | <organizational or atomic>                 |
| completion scope   | <organizational prior landed leaf refs plus proposed final leaf; atomic branch ref> |
| proposed candidate | <exact organizational super candidate ref/tree; atomic branch ref/tree> |
| handover evidence  | <review verdict / delegated decision ref>    |
| super source       | <canonical sprint document / plane-owned current edge> |
| full gate boundary | <before final organizational leaf moves super; atomic block landing> |
| verdict            | <master-exit verdict artifact ref>           |
| verdict outcome    | <pass or pass-with-notes>                    |
| written            | <YYYY-MM-DDTHH:MM>                            |

## Change-Set Summary
- <what this master delivered, master-granular>
- Leaves landed: <leaf id> → <one-line outcome>, …

## Requirements / Steps Completion
- All master requirements addressed: yes | with justified deltas (decision-log refs: …)

## Code / Memory / Ledger State
- Ancestry-compatible fast-forward/replay: yes | no:<exact divergence>
- Memory rows carried as explicit recovery: <summary> | none
- Ledger maps every leaf commit: yes | <gap>
- Single-siding notes after unavoidable overlap: <which memory to defer / dedup> | none

## Known Follow-Ups
- <fix leaf the verdict named but scoped as post-integration> | none

## Reachability
- The `(master task_doc, manager)` seat remains structurally reachable across occupant replacement.
```
