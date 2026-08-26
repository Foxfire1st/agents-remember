# Orchestration-Task Template

The reviewable sprint plan a **strategist** drafts for the **architect**
(`roles/strategist.md`). The architect proposes the strategist pass; the strategist drafts only
after developer approval; the architect rules the result; and the orchestrator adopts the ruled
plan into JSON-primary task documents. When the developer sanctions a strategist skip, the
orchestrator authors the same complete artifact. A skip never permits missing topology reasoning,
classification, or an implicit execution nature; it does not make a persisted graph mandatory.

The notes artifact shows the evidence and judgments. The task documents carry executable
identity: the sprint's `orchestrates` and `integrationBranch`, every commanded master's
`executionNature`, and `executionGraph` when the adopted topology uses one.

## Rules

1. **Separate facts from judgments.** Tool-derived task membership, paths, routes, seams,
   call/import relationships, lineage, cycles, readiness, and derived waves go in the fact table.
   Dependency meaning, `organizational` versus `atomic`, blast radius, priority, blocker placement,
   and reprioritization go in the canonical Judgment Register with rationale, evidence, author,
   confidence, and supersession history. The nature, blast-radius, priority, relation, blocker, and
   leaf-move sections are projections that cite their owning judgment row rather than becoming
   parallel judgment authorities. Every schedulable candidate resolves to one effective priority
   row: its candidate-specific row when present, otherwise the owning-master row as the inherited
   default. Candidate priority overrides rather than combines with that default, and duplicate
   current rows for one subject are invalid.
   Stable task/node order is a tie-break, never priority judgment.
2. **Shown work is required.** Every dependency edge carries evidence (tool query, file,
   decision-log entry, design citation, or declaration cross-reference) and cites the owning
   Judgment Register row. Raw observations and `INDEPENDENT` relations may remain fact-only, but
   no relation selected into `executionGraph` may omit its judgment id. Every blast-radius and
   priority row names its derivation. Every leaf move carries from→to plus rationale.
3. **Surfaces are two-sided.** Existing surfaces map to route indexes/onboarding. New surfaces map
   by declaration: parent route/location, intended shape, and wiring point. A leaf that names
   neither becomes `unplannable as scoped`; a merely new surface does not.
4. **An authored canonical graph is activity-on-node.** When adopted, its nodes are exact
   `TaskDocumentRef` objects and must match `orchestrates` exactly. Its edges are predecessor →
   successor with a nonblank reason. The control plane derives deterministic waves and refuses
   cycles; never persist manual wave or position fields. When the evidence does not justify an
   explicit graph, record the graph-less atomic-sequential choice and its rationale instead of
   manufacturing nodes or edges to make the plan look complete.
5. **Classification is dependency/risk-driven.** `organizational` means the master is a logical
   ownership group whose leaves can land independently on super. `atomic` means partial exposure
   is invalid or unsafe and the whole block must land once. Large size alone is insufficient. A
   common foundation required across masters is the canonical atomic predecessor example.
   Graph edges may place an atomic block first, between waves, or last. A disposable experiment
   that must not stall the sprint stays outside its graph and uses a standalone single-master path
   if it succeeds.
6. **Adoption is explicit.** The strategist writes this draft but does not edit task docs. The
   orchestrator adopts it through previewed `task_doc` operations and records the architect ruling:
   one `task_doc.attach_master` call per commanded master (the typed `masterRef` subTasks row,
   `orchestrates` membership, and nature assertion as one atomic batch); the operation also
   maintains the node only when the sprint already has an `executionGraph`. A graph-less adoption
   stops after those attachments, explicitly selects the atomic-sequential default, and states the
   evidence-backed reason. To adopt an explicit graph from graph-less state, complete every
   attachment first, then send one `task_doc.author_execution_graph` batch containing the exact
   full `add_node` set and its evidence-backed edges. Graph authoring bootstraps or edits the graph
   — it is never a runtime fallback and is not called to create ceremonial empty topology.
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
| Subject (candidate or owning-master default) | Grade (critical, high, normal, or low) | Affected dependents | Judgment id |
| -------------------------------------------- | ------------------------------------ | ------------------- | ----------- |

Resolve one effective row per candidate: use the candidate row when present, otherwise its
owning-master default. Never combine both grades; duplicate current rows for one subject are
invalid.

## Topology Choice And Canonical executionGraph Adoption Payload
Topology choice: `explicit executionGraph` | `graph-less atomic-sequential default`

Evidence-backed reason: <why the chosen topology fits this sprint>

One `task_doc.attach_master` call per commanded master (the row number is the sprint's master
index position) owns its typed row, `orchestrates` membership, and execution-nature assertion; if
the sprint already has an explicit graph, the same operation maintains its node. Attachment fields:
```json
{
  "masterRef": {"repository": "<repo>", "path": "<master-slug>/task.json"},
  "number": "<sprint row number>",
  "executionNature": "organizational | atomic",
  "judgmentId": "<Judgment Register row>"
}
```

The graph-less choice stops after every attachment. To adopt an explicit graph from that state,
complete all attachments first, then send one `task_doc.author_execution_graph` batch with one
`add_node` mutation per commanded master and the evidence-backed edges. Example with two masters:
```json
{
  "mutations": [
    {
      "op": "add_node",
      "ref": {"repository": "<repo>", "path": "<master-a>/task.json"}
    },
    {
      "op": "add_node",
      "ref": {"repository": "<repo>", "path": "<master-b>/task.json"}
    },
    {
      "op": "add_edge",
      "predecessor": {"repository": "<repo>", "path": "<master-a>/task.json"},
      "successor": {"repository": "<repo>", "path": "<master-b>/task.json"},
      "reason": "<evidence-backed dependency reason>",
      "judgmentId": "<Judgment Register row>"
    }
  ]
}
```

## Derived Waves And Blocker Walk
- Explicit graph: Wave <n> (mechanically derived, not persisted): <master refs>
- Explicit graph: Atomic blocker: <master ref> · predecessors <refs> · successors <refs> · blocker-placement judgment <id>
- Graph-less default: <canonical commanded-master tie-break; one source-pair-selected atomic master
  exposes implementation at a time; selection may logically pause and later resume durable work>
- Deterministic equal-priority tie-break: canonical graph node order when present; otherwise canonical commanded-master order

## Leaf Moves
| Leaf | From (master) | To (master) | Judgment id |
| ---- | ------------- | ----------- | ----------- |

## Coherence Findings
- QUO-VADIS (developer decision required): <directional contradiction> | none
- Unplannable as scoped: <leaf id — missing anchors> | none
- <duplicate work, vocabulary drift, or cross-master conflict — cited>

## Runtime Re-Evaluation Contract
- Task documents remain authoritative during every scheduling and operation phase. An intrinsically
  valid mutation publishes first; its `projectionEffects` names every before/after-union sprint and
  the exact rebuild action for any projection left invalid-empty.
- Closeout intent changes only through task truth or a contract-owned door generation's
  waiting/deferred/withdrawn disposition. The queue has only `status` and `rebuild`; it never owns
  declaration, claim, lifecycle, commit, recovery, certification, replan, or drain.
- Orchestrator recomputes after every task projection effect, door declaration/disposition or
  provenance change, landing, landing-blocker change, or accepted priority change. Rebuild derives
  solely from current task + current waiting-door facts; no old row is input.
- Ordinary readiness and bounded reprioritization: orchestrator judgment; no strategist required.
- Before a priority judgment affects door order, append its rationale, evidence, author,
  confidence, and superseded row to the sprint decision log and Judgment Register; then publish the
  new grade through the door owner and rebuild the projection.
- Claim transfers the exact generation to the enclosure-root operation journal. The operation
  stays task-addressable through locator -> manifest -> journal with the queue absent or
  invalid-empty; execute only its advertised `worktree_operation_control` action.
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
