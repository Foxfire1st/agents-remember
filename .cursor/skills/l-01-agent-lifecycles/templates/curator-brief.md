# Template — Curator Brief

The dispatch packet the **manager** (or the architect in a flat series) compiles for a **curator**,
spawned fresh per leaf after builder code exists and the reviewer verdict is available. **The brief
is the curator's entire session start** — it replaces the front half the spawner already ran. This
is the change-set and intent feeding contract: the curator never infers either from transcript
memory. It is FED the landed change set, existing intent anchors, the leaf task doc, approved
developer/design rulings, the manager's immediately preceding current source-lineage projection,
and notes/ as inputs. The control plane repeats that lineage proof before process creation.

Dispatch with `dispatch_agent(task_document_ref=<canonical leaf document>, role="curator",
brief=<this complete brief>)`. The control plane claims the `(leaf document, curator)` seat and
privately binds its current occupant; the brief never carries a runtime address.

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
- Requirement adjudication: `<reviewer-verdict-path>` — attach the reviewer's independent
  `accepted | rejected` row for every revision above. A rejected or worker-blocked revision is an
  unresolved blocker to report, never ruled current intent to write into onboarding. Preserve the
  reviewer's exact failure class; only `requirement contradiction/overconstraint` denotes a
  semantic contradiction.
- notes/: `<series-notes-path>` — the builder turn report
  (`notes/reports/<leaf-id>-worker-report.md`), the mandatory route-review verdict, and
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
- Reviewer verdict: `<path or flat/solo review evidence>`.
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
- `c-02-memory-quality-control` for the pre-closeout missing-onboarding and quality worklist.
- `c-05-create-or-update-onboarding-files` skill workflows for sidecars and entity catalogs.
- `memory_quality_check` for the complete checklist and `route_index_refresh` only to apply stale
  indexes named by it — always with
  `contract_path="<enclosure-contract-path>"`.
- `curator_coherence` for `prepare` → exact agent-owned judgments → atomic `publish` → `validate`,
  always with the same contract path.
- Inbox for one clarification row back to <owning-seat contact> if the fed change set is missing or
  ambiguous — never invent a change set from memory.
- No `worktree_*`, `lifecycle_*`, `task_doc`, `gate_*` tools, no code edits. The bounded
  `curator_coherence` publication is the sole task-local authority write owned by this role.

## Self-check (before you report — your output is checked at closeout)
Own the complete pre-closeout memory worklist. As the final intake action, run the full leaf-scoped
quality check and open its `reportPath` at
`<worktree-enclosure>/reports/curator-memory-quality.md`. That single atomically overwritten file
combines current-additions coverage, full quality, source-change candidates, and route-index drift.
Repair everything it reports that can be made true from the fed dirty worktree, rerun the same full
call, and keep iterating until `curatorActionableCount=0` and
`qualityChecklistStatus=ready-for-closeout`. At that point a combined
`checklistStatus=coherence-required` is the expected transition to publication. Never create a
timestamped copy or second checklist, and do not publish while any curator-owned finding remains.

The contract-scoped quality call temporarily compares unstamped cards from the leaf's code-base
commit to the dirty worktree, so changed claims surface before a closeout commit exists. Repair every
enforced `citation_claim_reopened`, unresolved range, absent anchor, shape/history/entity defect, and
missing sidecar. Only explicitly report-only/surfaced claim-review findings may remain without an
edit. The real code-commit stamp and commit-derived entity fingerprint remain closeout-owned; never
fabricate them. Expected dirty-source drift or missing real-commit verification may be reported only
after proving there is no underlying curator-actionable defect.

1. Run `memory_quality_check(request={"mode":"sync", "repo_id":"<repo-id>", "contract_path":"<enclosure-contract-path>"})`
   before editing and use the returned file as the exact worklist.
2. Create/update every required sidecar and repair every enforced
   content/citation/shape/history/entity finding. Apply
   `route_index_refresh(repo_id="<repo-id>", contract_path="<enclosure-contract-path>")` only when
   the checklist names stale indexes.
3. Rerun the same full quality call until its curator gate is zero; then call
   `curator_coherence prepare` and disposition every exact source-change tuple it returns.
4. Run `git diff --check` in the memory worktree, plus any other check named above.
5. Map every onboarding contract change to the exact approved requirement revision and the
   reviewer's accepted adjudication. Preserve blocked/rejected deltas as report blockers; do not
   promote them into current intent.
6. Publish with the exact prepared identities, semantic revision, separate delivery attempt, and
   one judgment per tuple. Use only `code:`, `memory:`, or `task:` evidence references, then call
   `validate`. An unchanged full quality rerun preserves deterministic attestation bytes; changed
   quality input stales the authority and requires a new prepare/publish cycle.

A `cit:(...)` wrapped in backticks is read as a QUOTATION of the citation grammar — which is how
these documents document it — so it is not checked; write a real citation unbackticked.

Drop `contract_path` and both resolve the OFFICIAL memory repo instead: `route_index_refresh`
WRITES, so an unscoped call dirties a repo you do not own and blocks the next `worktree_start`.
Confirm `onboardingRoot` in each response is `<memory-worktree-path>/onboarding` and `reportPath`
is inside this leaf's enclosure `reports/` directory. A finding count
implausible for this change set is a measurement problem to investigate and escalate, not permission
to pass incomplete onboarding. Closeout's post-commit rerun is the hard gate, not the first time the
curator learns about repairable work.

## Structured coherence authority (mandatory, last act)
Do not write `<leaf-id>-curator-report.md`, `-v2.md`, or any other hand-versioned coherence file.
`curator_coherence prepare` captures the exact code tree, memory tree, task topology, structured
quality attestation, predecessor, and candidate list. Supply one semantic disposition, rationale,
and explicit evidence reference for every returned tuple; normally cite the reconciled onboarding
as `memory:onboarding/<onboardingFile>`. The tool records evidence digests, atomically selects one
content-addressed structured record, renders its Markdown projection, and optionally freezes the
attempt snapshot. Finish with `validate` and report its canonical path, record/report/snapshot
paths, candidate trees, attestation digest, report digest, and validation result. If evidence or
tooling prevents publication, send the typed blocker instead of a competing report.
```

---

**Compiler notes for the manager.**

- Fill every `<placeholder>`; a brief with an unresolved placeholder is not dispatchable.
- Immediately before compiling this brief, call `worktree_status` for the canonical leaf and require
  `sourceLineage.state=current`. If it is stale or unavailable, synchronize and reconcile before
  curator dispatch. `dispatch_agent` repeats the proof and refuses before process creation if the
  lineage moves between the manager's check and the dispatch transaction.
- `<enclosure-contract-path>` is the leaf's `series-contract.md` under the master's
  `enclosures/<leaf-id>/`. Without it the curator cannot run the closeout check on its own work,
  and its `route_index_refresh` writes into the official memory repo.
- Pull the change-set counters/paths from the leaf's actual landed range (the leaf contract's
  recorded base commit through the builder's current HEAD/worktree state) — do not hand the curator
  a stale or guessed diff.
- Attach the builder turn report and the candidate-bound route-review verdict as the notes/
  inputs; the curator does not re-request evidence that already exists in `notes/reports/`.
- Deliver as an echo-confirmed paste; only count delivery on a post-boot echo.
- This brief runs strictly AFTER builder code exists and `task_doc.record_route_review` has bound
  the reviewer verdict to the current candidate tree — never before, and never in place of either.
