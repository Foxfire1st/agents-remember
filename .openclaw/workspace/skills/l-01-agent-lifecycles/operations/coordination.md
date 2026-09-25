# Operation — Coordination

Use native Orca as the execution owner. Consult the active runtime's `orca-cli` and `orchestration` skill before invoking Run, Task/Spec, Dispatch, terminal reuse, or messaging. Establish a native Run when the work requires a durable coordinator; bind each native task to its exact canonical AR task and requirement. Orca's sender identity is runtime-provided. Address only actual native recipients/IDs returned by Orca.

Choose a flat graph for small work: Architect directly assigns distinct Workers, Reviewers, and Curators. For a larger sprint, an Orchestrator may coordinate; a Manager may own one selected master. These roles are optional according to scale. A WorkerStart that serves an AR leaf must reuse the already configured native terminal/worktree; do not create an independent Orca Git worktree for the leaf. Keep paired code/memory/report paths and exact task references in each handover.

Use `orchestration.send`, `check`, and `reply` only as documented by the installed native skill. Preserve message, Run, Dispatch, task, and recipient IDs in the durable handover. Continue Orca's own check/wait and response loop until assigned work is complete, blocked, or a true developer decision is needed; do not dispatch and stop. On an uncertain peer delivery, reconcile the same operation; do not guess a parent or launch duplicate work. A manual role can work directly with the current user without a parent reference.

Re-evaluate work from canonical AR task facts and actual evidence when invoked. Do not create background polling or a second scheduler. A native task/run is execution metadata, never a second requirements, review, curation, or acceptance ledger.
