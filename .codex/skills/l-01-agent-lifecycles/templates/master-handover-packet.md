# Master-Handover-Packet Template

The artifact a **manager** hands to the **orchestrator** at master exit (`roles/manager.md`), after the
master-exit adversarial seam. The artifact is durable and terminal/finalizer truth wakes the current
orchestrator. It tells the orchestrator which completion boundary is ready: the final accumulated
organizational candidate containing prior landed contributions plus the proposed final leaf, or
one isolated atomic branch ready to land.

## Rules

1. Post it only **after** the independent master-exit verdict exists — its exact artifact reference
   is a required slot and must identify the proposed candidate it reviewed.
2. Name `executionNature` and the exact scope. An organizational master names the
   prior landed leaf commits plus the proposed final leaf
   and the exact proposed final super candidate; it has no
   master branch. An atomic master names its isolated branch.
3. Summarize code/memory ancestry and ledger facts, and cite their canonical evidence: the
   immutable candidate tree, code ancestry, memory ancestry, and the exact ledger/commit row for
   every leaf. A ref is an immutable tree id plus its canonical evidence location, a typed
   lifecycle evidence ref, or a repository-relative artifact path with a stable row id / JSON
   pointer — never a branch name or bare “yes.” Do not copy lineage or ledger maps into the packet.
   Carry-over appears only when actual divergence required the recovery; it is not the normal
   landing plan.
4. The receiving orchestrator resolves every ref and revalidates its subject against the same
   proposed candidate before deciding the handover. A missing, stale, unresolvable, or
   candidate-mismatched ref blocks; summaries never substitute for this evidence.
5. Do not address an orchestrator occupant. `message_parent` is available for clarification or a
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
| proposed candidate | <immutable organizational super tree; immutable atomic branch tree> |
| candidate evidence | <canonical Git / operation evidence ref resolving that exact tree> |
| handover evidence  | <delegated decision / accepted-series authority ref> |
| super source       | <canonical sprint document / plane-owned current edge> |
| full gate boundary | <before final organizational leaf moves super; atomic block landing> |
| verdict            | <independent master-exit verdict artifact ref bound to proposed candidate> |
| verdict outcome    | <pass or pass-with-notes>                    |
| written            | <YYYY-MM-DDTHH:MM>                            |

## Change-Set Summary
- <what this master delivered, master-granular>
- Leaves landed: <leaf id> → <one-line outcome>, …

## Requirements / Steps Completion
- All master requirements addressed: yes | with justified deltas (decision-log refs: …)

## Code / Memory / Ledger State
- Candidate-tree evidence ref: <canonical ref resolving the proposed candidate above>
- Code-ancestry evidence ref: <canonical contract/evidence ref + stable row/id/JSON pointer>
- Memory-ancestry evidence ref: <canonical contract/evidence ref + stable row/id/JSON pointer>
- Ancestry-compatible fast-forward/replay: yes | no:<exact divergence>
- Memory rows carried as explicit recovery: <summary> | none
- Ledger maps every leaf commit: yes | <gap>
- Single-siding notes after unavoidable overlap: <which memory to defer / dedup> | none

| Leaf document ref | Exact canonical ledger/commit evidence ref |
| ----------------- | ------------------------------------------ |
| <leaf task ref>   | <ledger artifact path + stable row/id/JSON pointer> |

The table indexes canonical rows; it does not repeat their commit values or become a second map.

## Known Follow-Ups
- <fix leaf the verdict named but scoped as post-integration> | none

## Reachability
- The `(master task_doc, manager)` seat remains structurally reachable across occupant replacement.
```
