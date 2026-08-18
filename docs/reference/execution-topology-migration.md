# Execution-topology migration (operator guide)

This is the operator-facing cutover procedure for the explicit execution topology
(`executionNature` on commanded masters, `executionGraph` on orchestration sprints). The
migration is finite and explicit: a pre-migration snapshot is the rollback mechanism; there
is no runtime compatibility path kept after cutover.

## 1. Inventory (read-only preview)

```text
task_doc.inventory_execution_topology
  coordination_root, repo_id, code_repository
```

Reports, without writing anything:

- every orchestration sprint and its commanded masters,
- the proposed explicit nature (atomic when an `ar/<slug>` branch already backs the master,
  organizational otherwise),
- the sprint graph state (present vs. missing), and
- the declared completion blockers.

Proposed edges are always empty (parallel) until a strategist/orchestrator ruling supplies
them. The inventory never infers edges from file order, names, or status.

## 2. Migrate (atomic write)

```text
task_doc.migrate_execution_topology   # one sprint per call
  fields = {
    masters:        [ { taskDocumentRef: {repository, path}, executionNature } ],
    executionGraph: { nodes: [ {repository, path} ], edges: [ {predecessor, successor, reason} ] }
  }
```

Validates that the master set exactly matches the graph nodes, then atomically authors the
sprint's `executionGraph` and each commanded master's `executionNature`. `dry_run` previews
every affected JSON/Markdown pair, the ordered classifications, and the derived waves.

Classification rule (preserves current behavior):

- a master with an existing `ar/<slug>` branch is recorded `atomic`;
- everything else is recorded `organizational` (direct-super ancestry).

## 3. After cutover: fail closed

The first topology consumer refuses a sprint with no `executionGraph` and a commanded master
with no `executionNature` (`task-execution-topology-migration-required`). There is no
implicit default and no inferred meaning.

## 4. Rollback

Rollback restores the pre-migration snapshot — it does not re-enable a compatibility path.

1. Snapshot `tasks/<repo>/` before migrating (e.g. `git add -A && git commit` in the
   coordination tree, or a tarball).
2. If a migration is wrong, restore that snapshot.
3. Re-run the inventory to confirm the restored state matches expectations.

A branch that was already recorded `atomic` is only reclassified by an accepted
strategist/orchestrator ruling, never by the migration itself.

## 5. Release notes

- Persistent sprints and commanded masters are now explicitly typed (`executionNature`) and
  graph-joined (`executionGraph`).
- Missing nature/graph is a hard refusal, not a default.
- Rollback is snapshot-based; no dual-reader or feature-switch fallback remains.
