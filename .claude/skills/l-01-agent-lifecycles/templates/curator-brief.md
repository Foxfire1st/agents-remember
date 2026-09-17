# Template — Curator Brief

The dispatch packet the **manager** (or the architect in a flat series) compiles for a **curator**,
spawned fresh per leaf after builder code exists and, when requested, the reviewer verdict is
available. **The brief
is the curator's entire session start** — it replaces the front half the spawner already ran. This
is the change-set and intent feeding contract: the curator never infers either from transcript
memory. It is FED the landed change set, existing intent anchors, the leaf task doc, approved
developer/design rulings, the manager's immediately preceding current source-lineage projection,
and notes/ as inputs. The control plane repeats that lineage proof before process creation.

Dispatch with `dispatch_agent(task_document_ref=<canonical leaf document>, role="curator",
brief=<this complete brief>)`. The control plane claims the `(leaf document, curator)` seat and
privately binds its current occupant; the brief never carries a runtime address.

**This template feeds inputs; it does not author rules.** The curator's duties live in
`../roles/curator.md`, the coherence procedure in `../operations/curation.md`, and the truth
boundary in `../core/acceptance.md`. The packet restates the routing rule and the tool surface
because the curator must apply them without leaving its seat; where wording differs, those files
win.

---

```md
ROLE BRIEF — curator

# CURATOR COHERENCE BRIEF — <leaf-id> · <leaf title>

You are the CURATOR for leaf `<leaf-id>` of master `<master>` (repo: <repo-id>). Your lifecycle is
`skills/l-01-agent-lifecycles/roles/curator.md`; this brief is your session start. Perform the
leaf's conservative three-way intent reconciliation and write its coherence pass from the inputs
below, then stop.

## Worktrees
- Code:   `<code-worktree-path>` (branch `<work-branch>`, base `<base-commit>`) — read-only for you.
- Memory: `<memory-worktree-path>` (branch `<memory-work-branch>`) — your only write surface.
- Enclosure contract: `<enclosure-contract-path>` — pass this as `contract_path` to every memory
  tool below; it is what scopes them to this leaf instead of the official memory repo.
- Pre-curator lineage: `<worktree_status sourceLineage projection>` — captured by the manager
  immediately before dispatch and `state=current` across every applicable super → master → leaf
  code and external-memory edge. This is evidence, never a caller-supplied commit-id authority.

## The landed change set (fed, not inferred)
- Code diff: `<base-commit>..<worker-head-commit-or-HEAD>` in the code worktree — <changed-path
  list, or the dashboard change-set view ref (`/api/changeset/task` scope, or the leaf's
  `committed`/`working` change-set) the manager pulled it from>.
- Memory diff (if any pre-existing memory-worktree changes carry forward): `<memory-base>..<HEAD>`.
- Counters: `<files changed / insertions / deletions>` from the change-set the manager attached —
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
  review exists, preserve these judgments and do not discover, add, reopen, or broaden findings.
- notes/: `<series-notes-path>` — the builder turn report
  (`notes/reports/<leaf-id>-worker-report.md`), the optional route-review verdict, and
  any other task-local notes naming a factual current-state clarification.

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

## Routing rule (mgmt-L4 design — apply this before writing anything)
Route each piece of the change set and each notes/ item to the RIGHT onboarding home:
1. A concrete source file's own sidecar, when the change is about that file's behavior.
2. The nearest governing route-local overview, when the change is about route/package shape or
   crosses several files in one route.
3. The repo entity catalog, only for a real load-bearing cross-layer entity change.
4. The L3 Operational-Notes target is LAST RESORT ONLY — use it when a finding is real but has no
   file, route, or entity home; never as the default drop point for convenience.
Overview-dumping (writing everything into the nearest overview because it is easiest) is rejected.
Task-log-dumping (repeating a leaf id and generic delta in every touched card) is also rejected.
Reconcile existing, ruled, and implemented intent first. Current onboarding bodies hold compact
contracts and negative knowledge; citations/fingerprints/indexes hold integrity evidence; Update
History holds semantic transitions, not raw implementation-round chronology. A mechanical consumer
change with no contract impact receives a precise reviewed no-impact entry rather than invented
architecture prose.

If the pass reveals an incident, opportunity, or alternate frame, keep it out of current intent
unless it is already ruled and proven. Use the coherence judgment's `capture-candidate`
disposition with explicit evidence; do not invent a register or silently promote speculative
forward learning into repository truth.

## Tool surface
- Native reads in the code worktree; native reads/edits in the memory worktree.
- `c-05-create-or-update-onboarding-files` skill workflows for affected sidecars, overviews,
  indexes, and entity catalogs.
- `git diff --check`, the full `memory_quality_check` operation for this leaf, and any other check the
  manager brief names — all scoped with `contract_path="<enclosure-contract-path>"`.
- `curator_coherence` whenever the checklist requires it, always with
  `contract_path="<enclosure-contract-path>"`.
- Inbox for one clarification row back to <owning-seat contact> if the fed change set is missing or
  ambiguous — never invent a change set from memory.
- No `worktree_*`, `lifecycle_*`, `task_doc`, `gate_*` tools, no code edits.

## Checks — curation is complete
Curation is always complete: run the full memory-quality operation as part of curation — never a
subset, and never a named scoped check standing in for it. Every curator-actionable finding the
operation returns is either repaired or escalated as blocked with its exact returned code. Tests and
code-quality checks may be scoped to the change set; curation may not.

Repair the affected onboarding files named by the brief. Run `git diff --check` in the memory
worktree and the full `memory_quality_check` operation after repairs and before handoff. Run it at
intake and after every repair until `curatorActionableCount=0` and
`checklistStatus=ready-for-closeout`; when it then reports `coherence-required`, publish the
coherence authority before handoff. Record the exact commands, scope, and
passed/failed/blocked/not-run result. No subset result may stand in for the full operation, and
closeout and integration carry this evidence as a prerequisite.

The actual code commit, memory commit, and any commit-derived fingerprints belong to
the closeout transaction. The ledger cache is derived from those commits and is never a third output. Never fabricate a future hash or call a subset result full green.

A `cit:(...)` wrapped in backticks is read as a QUOTATION of the citation grammar — which is how
these documents document it — so it is not checked; write a real citation unbackticked.

Drop `contract_path` and both resolve the OFFICIAL memory repo instead: `route_index_refresh`
WRITES, so an unscoped call dirties a repo you do not own and blocks the next `worktree_start`.
Confirm `onboardingRoot` in each response is `<memory-worktree-path>/onboarding` and `reportPath`
is inside this leaf's enclosure `reports/` directory. A finding count
implausible for this change set is a measurement problem to investigate and escalate, never
permission to pass incomplete onboarding. Closeout and integration carry this full-memory-quality
handoff as a prerequisite; the closeout transaction owns the real code and memory commits and does
not rerun the operation automatically.

## Curator handoff (last act)
Return the changed onboarding paths, current-intent reconciliation, exact full-operation
commands/results, and every finding with its repair or blocked-escalation code. Do not write a
hand-versioned certification file: `curator_coherence` is the authority a curator produces when the
checklist requires it. A healthy memory reports `checklistStatus=coherence-required` and a
successful `prepare` with `candidateCount 0`; publish the coherence record then, and expect closeout
and integration to carry it.
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
- Pull the change-set counters/paths from the leaf's actual landed range (the leaf contract's
  recorded base commit through the builder's current HEAD/worktree state) — do not hand the curator
  a stale or guessed diff.
- Attach the builder turn report and, when review was requested, the candidate-bound route-review
  verdict as the notes/ inputs; the curator does not re-request evidence that already exists in
  `notes/reports/`. For an atomic child, attach the master-integration review scope only when the
  owning manager supplies it; the curator does not create a per-leaf route-review record.
- Deliver as an echo-confirmed paste; only count delivery on a post-boot echo.
- This brief runs strictly AFTER builder code exists. When review was requested, the owner calls
  `task_doc(operation="begin_review")` before reviewer work, then
  `task_doc(operation="record_review")` or the existing
  `task_doc(operation="record_route_review")` after the result for standalone/organizational
  leaves. Atomic child leaves proceed without a per-leaf route-review record, and the canonical
  master binds a requested review only at master-to-parent integration.
