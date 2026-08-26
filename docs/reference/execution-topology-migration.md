# Execution-topology authoring (operator guide)

This is the operator-facing procedure for the explicit execution topology
(`executionNature` on commanded masters, `executionGraph` on orchestration sprints).
A sprint without an `executionGraph` is not an error: it runs the atomic-sequential
default — every commanded master executes atomically regardless of any declared
nature, and exact source-pair activation exposes one selected master at a time.
Selecting another master automatically and logically pauses the former; it does not
require the former to integrate, retire its contract, or terminate its agent/worktree.
Canonical commanded-master order is only the stable tie-break where priority is equal.
Authoring a graph is the explicit opt-in to dependency-aware scheduling; there is no
separate migration operation.

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

## 2. Author the graph (atomic batch)

```text
task_doc.author_execution_graph   # one validated mutation batch per call
  fields = {
    mutations: [
      { op: 'add_node',    ref: {repository, path} },                  # per commanded master
      { op: 'set_nature',  ref: {repository, path}, executionNature, judgmentId },
      { op: 'add_edge',    predecessor, successor, reason, judgmentId }  # optional
    ]
  }
```

On a graph-less sprint the first `add_node` batch bootstraps the graph (the result
reports `bootstrapped: true`). Final validation requires exact `orchestrates`
membership and an explicit nature for every commanded master — a `set_nature`
mutation in the same batch covers a master document that lacks one. Judgment-bearing
mutations (edges, segmentation, nature) require a `judgmentId` row in the sprint's
canonical Judgment Register; sprint creation scaffolds the empty Judgment and
Priority Register sections so the register is never absent. `dry_run` previews every
affected JSON/Markdown pair, the ordered classifications, and the derived waves.

Classification rule (preserves current behavior):

- a master with an existing `ar/<slug>` branch is recorded `atomic`;
- everything else is recorded `organizational` (direct-super ancestry).

## 3. Defaults and fail-closed seams

- No `executionGraph`: the atomic-sequential default schedules the sprint; the
  closeout queue reports `mode: "atomic-sequential"` plus waiting reasons derived
  from the strict source-pair activation snapshot (`active`, `reconciling`, paused by
  the selected master, or vacant). Contract presence never elects a master, and the
  queue owns no activation transition or lifecycle operation.
- Manager/worker dispatch and atomic `worktree_start`/`worktree_attach` are selecting
  operations. They publish `reconciling` before source sync and `active` only after
  both exact recorded bases are current. Reviewer/curator inspection does not switch
  selection. Explicit sync cancellation publishes durable `vacant`.
- A malformed activation snapshot fails closed only for the affected projection and
  implementation admission. The next exact selecting operation archives the bytes
  with evidence and replaces the snapshot; task-document authoring never reads or is
  blocked by activation state.
- A commanded master with no `executionNature` under an authored graph stays a hard
  refusal (`task-execution-topology-migration-required`) naming `set_nature`.
- Malformed canonical registers degrade reads to facts; the write path
  (`task_doc.set_section`/`replace`/`create`) validates the register shape.

## 4. Served-build preflight (blocks the rc7 failure class)

The 3.0.0rc7 cutover failure — the deployed build could not parse
`executionNature`/`executionGraph` and the tree had to be restored from a snapshot —
is prevented mechanically: every graph authoring/migration write
(`author_execution_graph`, `attach_master`/`detach_master`, and
`set_field`/`replace`/`create` edits that emit topology fields) runs the served-build
preflight and refuses with upgrade guidance when the serving runtime predates the
topology schema (`task-execution-topology-serving-build-unsupported`).

The preflight checks two legs:

1. **Model self-probe**: the process running the tool (the MCP server serving the
   tree) must declare `executionNature`/`executionGraph` on `TaskDocument`.
2. **Installed distribution**: when the resolved `agents-remember-mcp` distribution
   is a non-editable wheel older than the documented floor (`3.0.0rc8`), refuse even
   if the checkout code on `sys.path` is current. An editable install, or a
   source-tree `*.egg-info` (the checkout dev layout), proves the checkout code
   serves and passes.

Operator contract: run authoring through the **deployed serving server** (in-process
invocation), never from a checkout CLI whose server is a different build — the
preflight cannot see another venv. Refresh the served runtime (e.g. replace the rc7
venv) before authoring, exactly as the original cutover required as a post-deploy
step.

## 5. Rollback

Rollback restores a snapshot — it does not re-enable a compatibility path.

1. Snapshot `tasks/<repo>/` before authoring (e.g. `git add -A && git commit` in the
   coordination tree, or a tarball).
2. If an authored graph is wrong, restore that snapshot or edit the graph back with
   `author_execution_graph`.
3. Re-run the inventory to confirm the restored state matches expectations.

A branch that was already recorded `atomic` is only reclassified by an accepted
strategist/orchestrator ruling, never by the authoring mechanism itself.

## 6. Release notes

- Persistent sprints and commanded masters are explicitly typed (`executionNature`)
  and graph-joined (`executionGraph`).
- Graph authoring/migration writes run a served-build preflight and refuse with
  upgrade guidance when the serving runtime predates the topology schema.
- A missing graph selects the source-pair-selected atomic-sequential default, not a
  refusal or a full-integration-before-switch rule; a missing nature under an
  authored graph remains a hard refusal.
- Rollback is snapshot-based; no dual-reader or feature-switch fallback remains.
