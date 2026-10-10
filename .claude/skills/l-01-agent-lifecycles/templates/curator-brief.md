# Template — Curator Brief

The dispatch packet the **manager** compiles for a leaf's **curator**, started when the Worker's
first freeze exists. The Curator receives the passed freeze and hand-off list directly from the
Reviewer and Worker before writing its curation. **The brief
is the curator's entire session start** — it replaces the front half the spawner already ran. This
is the change-set and intent feeding contract: the curator never infers either from transcript
memory. The Worker feeds the frozen change set and hand-off list, and the Reviewer feeds its code
verdict. Existing intent anchors, the leaf task doc, approved
developer/design rulings, the manager's immediately preceding current source-lineage projection,
and notes/ complete the inputs. The control plane repeats that lineage proof before process creation.

Dispatch with `dispatch_agent(task_document_ref=<canonical leaf document>, role="curator",
brief=<this complete brief>)`. The control plane claims the `(leaf document, curator)` seat and
privately binds its current occupant; the brief never carries a runtime address.

**This template feeds inputs; it does not author rules.** The curator's duties live in
`../roles/curator.md`, the coherence procedure in `../operations/curation.md`, and the truth
boundary in `../core/acceptance.md`. The rows below carry the *values* for this leaf; if a value
here disagrees with those files, they win.

---

```md
ROLE BRIEF — curator

# CURATOR COHERENCE BRIEF — <leaf-id> · <leaf title>

You are the CURATOR for leaf `<leaf-id>` of master `<master>` (repo: <repo-id>). Your lifecycle is
`skills/l-01-agent-lifecycles/roles/curator.md`; this brief is your session start. Perform the
leaf's conservative three-way intent reconciliation and write its coherence pass from the inputs
below, then hand the memory change and report directly to the Reviewer.

## Worktrees
- Code:   `<code-worktree-path>` (branch `<work-branch>`, base `<base-commit>`) — read-only for you.
- Memory: `<memory-worktree-path>` (branch `<memory-work-branch>`) — your only write surface.
- Enclosure contract: `<enclosure-contract-path>` — pass this as `contract_path` to every memory
  tool below; it is what scopes them to this leaf instead of the official memory repo.
- Pre-curator lineage: `<worktree_status sourceLineage projection>` — captured by the manager
  immediately before dispatch and `state=current` across every applicable super → master → leaf
  code and external-memory edge. This is evidence, never a caller-supplied commit-id authority.

## The frozen change set (fed by the Worker, not inferred)
- Code diff: `<base-commit>..<worker-head-commit-or-HEAD>` in the code worktree — <changed-path
  list, or the dashboard change-set view ref (`/api/changeset/task` scope, or the leaf's
  `committed`/`working` change-set) the Worker records for the exact freeze>.
- Memory diff (if any pre-existing memory-worktree changes carry forward): `<memory-base>..<HEAD>`.
- Counters: `<files changed / insertions / deletions>` from the change-set the Worker handed over —
  do not re-derive this from your own guess at "what probably changed."

## Task inputs
- Leaf task doc: `<leaf-doc-path>` (read it first — objective, requirements, decision log).
- Approved requirement corpus ruling: `<durable developer approval citation>`.
- Primary requirement revision: `<stable-id>@<version>` — canonical packet `<packet-path>`.
- Worker delivery attempt: `<attempt-id>` — a candidate handoff identity separate from the
  semantic requirement revision and from every evidence digest.
- Adjacent preservation/dependency revisions: `<stable-id>@<version> — <packet-path> | none`.
- Requirement adjudication: `<reviewer-verdict-path | none when review was not requested>` —
  attach the reviewer's independent `accepted | rejected` row only when this leaf has an approved
  review. For an atomic child, any requested route-review adjudication is deferred to the
  accumulated canonical master integration review; retain worker evidence and report rejected or
  worker-blocked revisions as blockers, never current intent to write into onboarding.
- Review mode: `<baseline | fix-verification | none>`.
- Sealed review baseline / predecessor: `<sealed baseline ref> / <immediately preceding result or
  N/A>` when review was requested.
- Outstanding issue IDs: `<exact IDs and dispositions supplied by the reviewer | none>` — when
  review exists, preserve these judgments.
- notes/: `<series-notes-path>` — the builder turn report
  (`notes/reports/<leaf-id>-worker-report.md`), the optional route-review verdict, and
  any other task-local notes naming a factual current-state clarification.
- Producers' curator hand-off list: `<path or fenced-block location of the builder's list, plus the
  reviewer's own list when review was requested>` — the requirement-shaped items in the shape of
  `../templates/curator-handoff-list.md`, handed over unparaphrased as data. Producer fields
  (`id`, `statement`, `kind`, `target`, `found_at`, `disposition`, `disposition_source`, `evidence`,
  `authority`) arrive filled; the curator fields (`resolution`, `validated_at`, `record_action`,
  `supersedes`) are `null` and are yours to fill. If the list and a report prose summary disagree, the
  list governs — attach the list itself, never a re-telling of it.

## Three-way intent inputs

### Existing system intent
- Governing onboarding/entity paths: `<paths>`.
- Load-bearing tests, incidents, and negative knowledge: `<paths or none>`.
- Contracts expected to remain unchanged: `<contracts>`.

### Ruled change intent
- Developer decisions and approved design notes: `<paths + concise rulings>`.
- Task requirements that authorize a contract change: `<exact stable-id>@<version> — <canonical
  packet path>; repeat for each applicable revision`.
- Explicit non-goals: `<non-goals>`.

### Implemented reality
- Fed code range/diff source: `<exact base-to-head or reviewed working-tree evidence>`.
- Builder report: `<path>`.
- Reviewer verdict: `<path or flat/solo review evidence | none when not requested>`.
- Verification evidence: `<commands/artifacts>`.

## Routing rule
Route each change-set and notes/ item to its onboarding home as `../roles/curator.md` and the `c-05-create-or-update-onboarding-files` workflow state; the brief hands the inputs, not the procedure.

## Curator handoff values
The handover names the leaf, exact freeze head/change hash, memory change identity and report path, and follows the lane, ID and handoff rules of `../roles/curator.md`.
```

---

**Compiler notes for the manager.**

- Fill every `<placeholder>`; a brief with an unresolved placeholder is not dispatchable.
- Immediately before compiling this brief, call `worktree_status` for the canonical leaf and require
  `sourceLineage.state=current`. If it is stale or unavailable, synchronize and reconcile before
  curator dispatch. `dispatch_agent` repeats the proof and refuses before process creation if the
  lineage moves between the manager's check and the dispatch transaction.
- `<enclosure-contract-path>` is the leaf's `series-contract.md` under the master's
  `enclosures/<leaf-id>/`. Use it to scope every memory-quality and coherence call; an unscoped
  `route_index_refresh` writes into the official memory repo.
- Start the Curator when the Worker's first freeze exists. Supply task, intent and current-lineage
  anchors; the Worker sends its exact frozen change-set counters/paths and hand-off list directly,
  and the Reviewer sends the passed freeze and verdict directly. Relay neither handover.
- Name the Worker report and candidate-bound Reviewer verdict paths as notes/ inputs supplied
  directly by their producers; the Curator does not re-request evidence that already exists in
  `notes/reports/`.
- The Reviewer records the ordinary CODE round and its own result; the Manager keeps gate acceptance
  and records approval beyond the ordinary limit. The Curator's MEMORY review is sealed and counted
  separately and writes no code-review state.
