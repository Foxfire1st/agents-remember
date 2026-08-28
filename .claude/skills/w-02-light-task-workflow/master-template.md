# Master Task Template (task series)

Use this when a `w-02-light-task-workflow` task outgrows a single-page plan and is better run as a
**master + light sub-task series**: one master `task.md` that strings several light sub-task files.
This is the composition that replaces the retired heavy workflow — low-ceremony, append-friendly, and
built to grow as the work unfolds.

## When to escalate to a series

The `l-01-agent-lifecycles` architect lifecycle's `decide` step escalates a single task to a series once its size is apparent — the
implementation plan no longer fits on a single page, or the work splits into distinct slices that each
deserve their own checklist and commit. You can also start single and escalate later: drop in the
master `task.md` and move the existing plan into the first `NN_<name>.md`.

## The series convention

- **One wrapper folder** holds the master `task.md` plus flat, numbered, descriptively-named sub-task
  files: `NN_<name>.md` (e.g. `01_job-lifecycle.md`, `02_task-format-reshape.md`). No nested phase
  folders.
- **Append-friendly:** the next sub-task is just the next `NN_<name>.md` dropped into the same folder.
  File numbers are stable creation IDs; the master's **Sub-tasks** list is the authoritative execution
  order (a later-numbered sub-task may run first).
- **One master integration branch, one leaf enclosure per active sub-task.** The master root
  `series-contract.md` represents the integration branch and is not itself worktree material. Each
  sub-task slice gets `enclosures/<leaf-id>/series-contract.md`, its own worktree branch, and its own
  worktree.
- **Each leaf lands into the master branch.** Each sub-task slice is committed via its own
  `c-09-git-worktree-manager` closeout behind an explicit commit gate, integrated into the master
  integration branch, and finalized for that parent-child edge.
- **The master owns the single release:** the version bump and any tag/release packaging happen once,
  at series end, after all sub-task commits exist. Sub-tasks never bump the version.
- **Each slice is test-verified before its commit:** run the repo test suite + the `system/tools.md`
  checks green before each incremental commit; testing is never deferred to the final slice.

## Master `task.md` scaffold

````markdown
# Task: <Master Title>

**Status:** planning | inProgress | Completed
**Repo:** <primary repo>
**Type:** Master (<Skill | Docs | Code | ...>)
**Created:** <YYYY-MM-DDTHH:MM>

---

## Objective

<The one operational outcome the whole series delivers — e.g. a single minor release.>

---

## Filtered Requirement Projection

| Stable ID | Version | Canonical packet | Manifestation sub-task(s) |
| --------- | ------- | ---------------- | ------------------------- |
| R01 | v1 | [packet](requirements/R01-v1-<slug>.md) | <01, 03, ...> |

Corpus approval: <durable developer ruling citation>. The master summarizes thematic goals; this
table is a filtered projection and never rewrites the linked requirement contracts. IDs are never
reused or silently renumbered. Every applicable ID + version must appear as the owned primary of
at least one manifestation; each owning leaf supplies its builder acceptance envelope and
independent reviewer adjudication.

---

## Requirement Attempt Summary (rebuildable projection — never a gate)

| Requirement revision | Leaf manifestation | Attempts | Rejections | Current state | Dominant open failure class | Authoritative leaf journal refs |
| -------------------- | ------------------ | -------- | ---------- | ------------- | --------------------------- | ------------------------------- |
| R01@v1 | <leaf-id>/R01@v1 | <A001, A002> | <count + rejected attempt ids> | <state> | <class or none> | <worker attempt anchors + reviewer record anchors> |

Current state is one of `unattempted`, `pending-review`, `accepted`, `rejected`, `blocked`, or
`invalidated`. An open finding's class is exactly `implementation defect`, `evidence gap`,
`requirement contradiction/overconstraint`, `test/tool defect`, or `external blocker`.

Rebuild this table from the detailed append-only leaf worker/reviewer records. It preserves attempt
and rejection history for observability but is not authority: it never gates task authoring,
lifecycle, closeout, integration, or queue operations. If missing, stale, or contradictory, the
leaf journals win and this projection is regenerated.

---

## Sub-tasks (execution order)

> File numbers are stable creation IDs; **this list is the authoritative execution order**.

1. **<Sub-task A>** · `01_<name>.md` — <scope>
2. **<Sub-task B>** · `02_<name>.md` — <scope>

Dependencies: <what must land before what>.

---

## Single Release (the master owns the final bump + tags)

- Sub-tasks commit **incrementally** (one `c-09-git-worktree-manager` closeout per slice, behind a commit gate);
  each leaf integrates into the master integration branch and finalizes its own edge.
- The master owns the **final release step only**: the version bump and any tag/release packaging,
  once every sub-task commit exists.

---

## Shared Decisions

| Date-Time | Decision | Rationale |
| --------- | -------- | --------- |

---

## Open Questions

- <cross-cutting questions; per-slice questions live in the sub-task files>

---

## References

- Sub-task files: `NN_<name>.md`
````

## Sub-task `NN_<name>.md` scaffold

Each sub-task file is a focused light-task plan (a slice of the master). It follows `template.md` but
is scoped to one slice and points back at the master:

````markdown
# Task: <Sub-task Title> (Sub-task <X>)

**Status:** planning | Implemented | Completed
**Repo:** <repo>
**Type:** <Skill | Docs | Code | ...>
**Created:** <YYYY-MM-DDTHH:MM>
**Master:** `task.md`

## Objective
## Primary Requirement Revision
- `<stable ID> @ <version>` — [complete canonical packet](requirements/<packet>.md)
## Adjacent Requirement Constraints
- Dependency: `<ID>@<version>` — [packet](requirements/<packet>.md)
- Preservation: `<ID>@<version>` — [packet](requirements/<packet>.md)
## Implementation Steps        ← checkbox checklist for this slice
## Proposed Code Examples      ← when code changes are in scope
## Decision Log                ← slice-local decisions; cross-cutting ones live in the master
## References
````

## Usage rules

1. After the requirement corpus passes cold-read and developer approval, create the master `task.md`
   and the first sub-task file together in its existing planning wrapper.
2. Keep sub-task files flat and numbered; do not nest phase folders.
3. Run the series through the master integration branch; create one leaf enclosure/worktree per active slice.
4. Only the master records the version bump and release; sub-tasks never bump.
5. Each slice runs the repository-prescribed, change-set-scoped acceptance checks plus its listed
   requirement checks green before its commit. The full-repository gate runs once at the master
   integration boundary, not at every leaf.
6. Decision logs are append-only in both the master and the sub-task files.
7. Any slice that introduces or retains durable test evidence records a registered stable executable
   contract or an exact expiry/retirement event in that slice; the master may not hide this decision
   in a generic shared note.
8. Every worker brief identifies exactly one owned primary requirement ID and separately lists
   adjacent master/leaf dependencies or preservation constraints. Every builder handoff records one
   `satisfied | blocked | approved-change` envelope for its primary with delivery and verification
   rationales/citations plus exact evidence; exceptions cite the durable developer ruling. Every
   independent verdict adjudicates that primary `accepted | rejected` after inspecting the
   artifacts. Across the manifestation set, every applicable master requirement must have an owner.
   The master cannot pass while any ID is rejected or while an accepted adjudication still carries
   worker status `blocked`.
9. Keep requirement acceptance and durable-evidence promotion separate. A stable-contract-or-expiry
   row cannot replace implementation/verification evidence for a requirement, and a satisfied
   requirement cannot waive the artifact-lifecycle decision.
10. Compile, cold-read, and obtain developer approval for the canonical requirement corpus before
    creating this master or any leaf. Masters and leaves carry ID + version + packet-link
    projections only; every version-addressed packet carries the matching durable approval
    citation, and task prose never creates rewritten requirement contracts.
11. Each leaf owns exactly one primary requirement revision. One revision may map to several leaves
    for independently executable manifestations. Adjacent requirements may be dependency or
    preservation constraints only, and a leaf that would close multiple independently falsifiable
    requirements must be split.
12. When a requirement changes, increment its version, cite the durable developer ruling,
    invalidate affected acceptance state, update affected projections, and rebrief affected leaves.
13. Keep requirement versions separate from attempts. A worker advances an attempt only when an
    exact candidate is handed to independent review, or after reviewer rejection when a successor
    is handed off. Internal implementation/test/evidence reruns remain separate experimental
    protocol events. Before review handoff, each worker appends an immutable, candidate-bound,
    requirement-specific record with a content-addressed reference to frozen expanded evidence;
    do not duplicate the complete master envelope or experimental-run body per record. Each
    independent reviewer appends a separate acceptance/rejection record against that exact attempt
    and candidate without editing the worker record.
    Validate the complete worker record before append. Append plus exact-candidate review handoff is
    one logical formal-attempt boundary. Preserve a malformed pre-handoff row with an append-only
    `non-attempt-correction`/void reference and no attempt-ID consumption; a malformed handed-off
    row requires independent reviewer rejection before a successor handoff.
14. Rejection produces a successor attempt citing predecessor findings. Classify every blocked or
    rejected finding as exactly `implementation defect`, `evidence gap`, `requirement
    contradiction/overconstraint`, `test/tool defect`, or `external blocker`; only the developer
    may approve a semantic requirement revision.
15. Accepted attempts remain closed unless an independent reviewer proves direct regression and the
    owning manager (architect in a flat run) records the bounded invalidation, or an approved new
    requirement version affects the manifestation. An unrelated later candidate does not reopen an
    accepted attempt.
16. Maintain the Requirement Attempt Summary above from authoritative leaf journals, showing
    attempts, rejection history/count, current state, dominant open failure class, and leaf refs.
    It is rebuildable and never a task/lifecycle/closeout/integration/queue gate or authority.
