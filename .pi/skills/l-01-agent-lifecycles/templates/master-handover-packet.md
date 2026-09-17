# Master-Handover-Packet Template

The artifact a **manager** hands to the **orchestrator** at master exit (`roles/manager.md`), after
the leaf handoffs and any explicitly requested master-exit review. The artifact is durable, and
terminal/finalizer truth — which attests only that the manager's turn ended — wakes the current
orchestrator, who validates the packet. It tells the orchestrator which completion boundary is
ready: the final accumulated organizational candidate containing prior landed contributions plus the
proposed final leaf, or one isolated atomic branch ready to land.

The truth boundary this template obeys is authored once in `../core/acceptance.md`.

## Rules

1. Post it after the prepared code/memory transaction inputs and complete handoff reports
   exist. Include an independent master-exit verdict only when review was explicitly requested;
   when present, its exact artifact reference must identify the proposed candidate it reviewed.
2. Name `executionNature` and the exact scope. An organizational master names the
   prior landed leaf commits plus the proposed final leaf
   and the exact proposed final super candidate; it has no
   master branch. An atomic master names its isolated branch.
3. Summarize code/memory ancestry and cite its canonical evidence: the immutable candidate tree,
   code ancestry, memory ancestry, and the accepted Git commit pair for every leaf. Attribution
   comes from memory commit trailers; the computed ledger cache is diagnostic only.
   A ref is an immutable tree id plus its canonical evidence location, a typed
   lifecycle evidence ref, or a repository-relative artifact path with a stable row id / JSON
   pointer — never a branch name or bare “yes.” Do not copy lineage or cache maps into the packet.
   Carry-over appears only when actual divergence required the recovery; it is not the normal
   landing plan.
4. The receiving orchestrator resolves every ref and revalidates its subject against the same
   proposed candidate before deciding the handover. A missing, stale, unresolvable, or
   candidate-mismatched Git/operation ref blocks; summaries never substitute for this evidence.
   Cache availability and attribution gaps for unchanged memory do not block a proven pair.
5. Do not address an orchestrator occupant. `message_parent` is available for clarification or a
   blocking issue; ordinary completion comes from the packet plus terminal/finalizer truth, and that
   terminal outcome attests only that the turn ended — rule 4's validation is what accepts it.

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
| worker checks      | <targeted commands/results, including failed or not-run> |
| curator checks     | <complete onboarding/full-operation commands/results, or N/A> |
| transaction legs   | <code commit · prepared memory commit · exact Git/operation refs> |
| verdict            | <independent master-exit verdict artifact ref, or none> |
| verdict outcome    | <pass or pass-with-notes, or N/A>             |
| written            | <YYYY-MM-DDTHH:MM>                            |

## Change-Set Summary
- <what this master delivered, master-granular>
- Leaves landed: <leaf id> → <one-line outcome>, …

## Requirements / Steps Completion
- All master requirements addressed: yes | with justified deltas (decision-log refs: …)

## Code / Memory State And Attribution
- Candidate-tree evidence ref: <canonical ref resolving the proposed candidate above>
- Code-ancestry evidence ref: <canonical contract/evidence ref + stable row/id/JSON pointer>
- Memory-ancestry evidence ref: <canonical contract/evidence ref + stable row/id/JSON pointer>
- Ancestry-compatible fast-forward: yes | no:<exact divergence>
- Memory content carried as explicit recovery: <summary> | none
- Attribution from memory commit trailers: <evidence refs or explicit gaps>
- Single-siding notes after unavoidable overlap: <which memory to defer / dedup> | none

| Leaf document ref | Exact canonical code/memory evidence ref |
| ----------------- | ------------------------------------------ |
| <leaf task ref>   | <Git/operation evidence path + stable row/id/JSON pointer> |

The table indexes canonical Git/operation evidence; it does not become a second commit map.

## Known Follow-Ups
- <fix leaf the verdict named but scoped as post-integration> | none

## Reachability
- The `(master task_doc, manager)` seat remains structurally reachable across occupant replacement.
```
