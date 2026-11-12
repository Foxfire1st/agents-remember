# Prepare an authored knowledge ingest

Curators read this topic before preparing or ingesting authored knowledge. It describes target explanations, the plan-to-write sequence and the semantic scope the Curator supplies.

The reconciliation's requirement-shaped items are authored knowledge, not onboarding prose. Before preparing the producer list for ingest, read [the producer entry contract](../../templates/curator-handoff-list.md#the-entry) for producer ownership and target rationale.

Before filling Curator-owned fields, read [the Curator's authored keys](../../templates/curator-handoff-ingestion.md#the-curators-authored-keys-beside-the-thirteen-fields) for resolution, validation and record actions.

Before authoring records for ingest, read [the writer's input sections](../../templates/curator-handoff-ingestion.md#the-writers-input-sections) for record formats and authoring requirements.

Every target needs its own authored `rationale`, explaining why that place carries the obligation; `role` is optional. The writer never generates a rationale and refuses an unexplained target.

Use the writer for sidecar JavaScript Object Notation (JSON). The writer writes records under `knowledge/`, `realizes` and `proves` entries in file sidecars, and the leaf's history file. Before a command-line run that decides or writes, read [serving-build binding](../knowledge/serving-build.md) for the admitted interpreter, package selection and identity comparison. Before interpreting the ingest result, read [the ingest report](publication-report.md) for its state, written paths and refusals.

The worklist command computes and persists the leaf's worklist beside its contract. The writer reads that worklist and the contract's code and memory roots.

Plan with `commit: false` until the plan returns without refusals, then write once with `commit: true`.

For an entry of an existing invariant whose meaning is unchanged, quote the record's current statement exactly.
