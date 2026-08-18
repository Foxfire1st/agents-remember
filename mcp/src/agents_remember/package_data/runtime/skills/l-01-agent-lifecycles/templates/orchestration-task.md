# Orchestration-Task Template

The reviewable sprint plan a **strategist** drafts for the **architect**
(`roles/strategist.md`). The architect proposes the strategist pass; the strategist drafts only
after developer approval; the architect rules the result; and the orchestrator adopts the ruled
plan into JSON-primary task documents. When the developer sanctions a strategist skip, the
orchestrator authors the same complete artifact. A skip never permits missing topology or an
implicit execution nature.

The notes artifact shows the evidence and judgments. The task documents carry executable
identity: the sprint's `orchestrates`, `integrationBranch`, and `executionGraph`, plus every
commanded master's `executionNature`.

## Rules

1. **Separate facts from judgments.** Tool-derived task membership, paths, routes, seams,
   call/import relationships, lineage, cycles, readiness, and derived waves go in the fact table.
   Dependency meaning, `organizational` versus `atomic`, blast radius, priority, blocker placement,
   and reprioritization go in the canonical Judgment Register with rationale, evidence, author,
   confidence, and supersession history. The nature, blast-radius, priority, relation, blocker, and
   leaf-move sections are projections that cite their owning judgment row rather than becoming
   parallel judgment authorities.
   Stable task/node order is a tie-break, never priority judgment.
2. **Shown work is required.** Every dependency edge carries evidence (tool query, file,
   decision-log entry, design citation, or declaration cross-reference) and cites the owning
   Judgment Register row. Raw observations and `INDEPENDENT` relations may remain fact-only, but
   no relation selected into `executionGraph` may omit its judgment id. Every blast-radius and
   priority row names its derivation. Every leaf move carries from→to plus rationale.
3. **Surfaces are two-sided.** Existing surfaces map to route indexes/onboarding. New surfaces map
   by declaration: parent route/location, intended shape, and wiring point. A leaf that names
   neither becomes `unplannable as scoped`; a merely new surface does not.
4. **The canonical graph is activity-on-node.** Its nodes are exact `TaskDocumentRef` objects and
   must match `orchestrates` exactly. Its edges are predecessor → successor with a nonblank reason.
   The control plane derives deterministic waves and refuses cycles; never persist manual wave or
   position fields.
5. **Classification is dependency/risk-driven.** `organizational` means the master is a logical
   ownership group whose leaves can land independently on super. `atomic` means partial exposure
   is invalid or unsafe and the whole block must land once. Large size alone is insufficient. A
   common foundation required across masters is the canonical atomic predecessor example.
   Graph edges may place an atomic block first, between waves, or last. A disposable experiment
   that must not stall the sprint stays outside its graph and uses a standalone single-master path
   if it succeeds.
6. **Adoption is explicit.** The strategist writes this draft but does not edit task docs. The
   orchestrator adopts it through previewed `task_doc` operations and records the architect ruling.
   `task_doc.migrate_execution_topology` is a finite legacy cutover, not a runtime fallback.
7. **Review before adoption.** Run `../criteria/plan-review.md`; revisions append round sections
   rather than erasing history. The artifact remains standing scope after adoption.

## Shape

````md
# Orchestration Task — <sprint label> · <repo(s)>

| Field              | Value |
| ------------------ | ----- |
| strategist seat    | <sprint task_doc path> + strategist |
| sprint document    | <canonical JSON-primary sprint task document ref> |
| masters in scope   | <exact commanded master document refs> |
| integrationBranch  | <exact super branch persisted on the sprint document> |
| status             | <draft, in-review, round-n, or adopted> |
| round              | <n> (3-round cap; drawing board is escalation) |
| written            | <YYYY-MM-DDTHH:MM> |

## Sprint Scope
- IN: <master ref> — <why it is in this sprint>
- OUT: <master ref> — <why it waits>

## Mechanical Fact Inventory
| Fact id | Kind (membership, route, seam, call/import, lineage, readiness, or graph) | Subject | Observed value | Evidence/tool ref | Observed at |
| ------- | -------------------------------------------------------------------------- | ------- | -------------- | ----------------- | ----------- |

## Judgment Register (canonical judgment authority)
| Judgment id | Kind (dependency meaning, execution nature, blast radius, priority, blocker placement, reprioritization, or leaf move) | Subject | Decision | Rationale | Evidence/fact refs | Author | Confidence | Supersedes |
| ----------- | -------------------------------------------------------------------------------------------------------------------------- | ------- | -------- | --------- | ------------------ | ------ | ---------- | ---------- |

## Touch Surfaces (per leaf — two-sided)
| Leaf | Existing surfaces (route-map-mapped) | New surfaces (parent route + shape + wiring point) |
| ---- | ------------------------------------ | -------------------------------------------------- |

## Evidence Relations
| From (leaf/master) | To | Relation (ORDER, CONFLICT, or INDEPENDENT) | Evidence/fact refs | Judgment id (required when selected into executionGraph) |
| ------------------ | -- | ----------------------------------------- | ------------------ | -------------------------------------------------------- |

## Master Execution Nature (explicit judgment)
| Master document ref | Nature (organizational or atomic) | Partial-exposure invariant | Judgment id |
| ------------------- | -------------------------------- | -------------------------- | ----------- |

## Blast-Radius Register
| Leaf | Radius (low, medium, or high) | Routes/seams | Judgment id |
| ---- | ---------------------------- | ------------ | ----------- |

## Priority Register (explicit judgment)
| Candidate/master | Grade (critical, high, normal, or low) | Affected dependents | Judgment id |
| ---------------- | ------------------------------------ | ------------------- | ----------- |

## Canonical executionGraph Adoption Payload
```json
{
  "nodes": [
    {"repository": "<repo>", "path": "<master-slug>/task.json"}
  ],
  "edges": [
    {
      "predecessor": {"repository": "<repo>", "path": "<foundation>/task.json"},
      "successor": {"repository": "<repo>", "path": "<dependent>/task.json"},
      "reason": "<evidence-backed dependency reason>"
    }
  ]
}
```

## Derived Waves And Blocker Walk
- Wave <n> (mechanically derived, not persisted): <master refs>
- Atomic blocker: <master ref> · predecessors <refs> · successors <refs> · blocker-placement judgment <id>
- Deterministic equal-priority tie-break: canonical graph node order

## Leaf Moves
| Leaf | From (master) | To (master) | Judgment id |
| ---- | ------------- | ----------- | ----------- |

## Coherence Findings
- QUO-VADIS (developer decision required): <directional contradiction> | none
- Unplannable as scoped: <leaf id — missing anchors> | none
- <duplicate work, vocabulary drift, or cross-master conflict — cited>

## Runtime Re-Evaluation Contract
- Orchestrator recomputes after candidate declaration/invalidation, landing, blocker change, or accepted priority change.
- Ordinary readiness and bounded reprioritization: orchestrator judgment; no strategist required.
- Before a queue judgment affects selection, append its rationale, evidence, author, confidence,
  and superseded row to the sprint decision log and Judgment Register.
- New dependency, changed atomic boundary, invalid priority model, or multi-master reshape:
  architect proposes a fresh strategist pass.
- Out-of-sprint addition: next sprint unless scope is explicitly changed.

## Open Risks
- <risk plus evidence limit>

## Evidence Inventory
- cgc queries:
- grepai queries:
- read_ar_files and route indexes:
- task docs, decisions, and design citations:
````
