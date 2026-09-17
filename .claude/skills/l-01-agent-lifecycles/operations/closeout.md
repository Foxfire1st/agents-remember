# Operation — Authorized Closeout

**What it covers:** the authorized Git code/memory transaction that lands a leaf or master — the
check evidence it consumes, the door/projection path that releases it, and the integration mechanics
that follow. It also owns the **targeted-check contract** every builder's Checks section must
satisfy.

**When it is selected:** a seat is preparing, releasing, or executing closeout/integration for an
in-scope candidate under the accepted series authority.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| manager | owns the leaf lifecycle end-to-end: previews and applies the closeout transaction, integrates the released candidate, finalizes; publishes closeout-door facts rather than ranking the portfolio |
| orchestrator | decides the manager handover gate, releases the exact first-ready generation, lands one atomic block on super, finalizes/cleans up subordinate edges |
| worker | supplies the **input**: targeted checks run and truthfully reported, plus the turn report |

Closeout is **not** the worker's, curator's, reviewer's, or designer's operation. A builder never
commits.

## The targeted-check contract (what closeout consumes as evidence)

This is the single home for the check duty a builder owes and an owner consumes.

1. Before handing a code implementation or fix to the supervising owner, **select and run the
   relevant targeted tests** and the applicable repository-prescribed lint, formatting, typing, and
   structural checks, using the **resolved** repository tools and environment.
2. **Record the exact commands, the selected scope, and the results** in the turn report. Explicitly
   list any relevant test or check **not run and why**, and report failures accurately.
3. After a failure and a code fix, **rerun the failed tests and every affected targeted check**, then
   document the final results before handoff. If one is not rerun, record why.
4. These are diagnostic worker checks. They are **separate from certification** and do not consume a
   review round. Applicable non-code checks follow repository policy.
5. **Do not run or claim a full suite / full quality result** unless the developer or the task brief
   explicitly requests that operation — curation is the one exception, because the curator always
   runs the full memory-quality operation as part of curation.
6. The resolved memory layer owns the concrete test implementation, permitted environment, arguments,
   and evidence contract — especially `system/git-workflow.md`, `system/coding-guidelines.md`, and
   `system/tools.md`. **Do not substitute a familiar runner or invent a fallback.**
7. A **red targeted check you cannot fix inside the leaf's scope is an escalation, not a workaround.**
   A failed or not-run check is reported; it never becomes a claim of full green.

## Required inputs

- Builder completion: the turn report with its acceptance block, the targeted-check report, and (when
  memory changed) the curator's affected-onboarding/scoped-check handoff.
- Current task/source/memory provenance and current lineage (`worktree_status` for the canonical
  leaf), with task-derived code and external-memory `sourceLineage` current. If the source parent
  advanced, run the contract-addressed `worktree_sync` and reconcile the landed code first.
- The configured leaf contract path, the canonical leaf/master/sprint refs, `executionNature`, the
  accepted priority grade, the exact candidate tree, and the routes/seams.

## Normal workflow

1. **Prepare** the exact code and memory legs. Memory commit trailers supply attribution; the
   computed ledger cache is diagnostic and never supplies landing authority or an additional commit
   leg.
2. **Publish closeout-door truth.** Declare the door generation with
   `closeout_door(request={action:"declare", contract_path:...})` and the complete admission evidence
   — not as a queue row and not as a chat-only readiness claim. Declaration retries with the same
   intent converge. The resulting `waiting` generation is source truth; the closeout queue is only
   its current schedulable projection.
3. **Release only the exact first-ready generation** from a `valid-built` projection, ordered by the
   accepted grade and the stable graph tie-break. A later source move requires `worktree_sync`, any
   necessary delta review/curation, and
   `closeout_door(request={action:"update-provenance", ...})`; it does not mutate an old queue row or
   justify carry-over by default.
4. **Close out and land** through the task-bound worktree tools. An `organizational` leaf lands
   directly into the current super line (no master branch is merged because none exists); an `atomic`
   leaf lands only into its atomic master branch, exposing no intermediate leaf to super, and the
   completed block lands on super once.
5. **Map the external-memory edge to the code edge.** Prefer an ancestry-preserving fast-forward and
   reserve `replay`/carry-over (`c-11-memory-carryover-from-branch`) for unavoidable divergence — a
   recovery, never the normal scheduling strategy.
6. **Observe the operation, don't re-drive it.** Once claim transfers the generation into the
   enclosure-root operation journal, observe it only through `worktree_status` and advertised
   `worktree_operation_control` actions. Queue absence, invalid-empty state, or later task edits
   never erase or strand that operation.
7. **Finalize and clean up.** `lifecycle_finalize_task` retries the default-on completion cleanup for
   report-bearing worker/reviewer/curator seats of its exact leaf; managers and orchestrators are
   never automatic cleanup targets. `retire_child` handles a stuck/abandoned leaf execution seat, and
   server policy bounds who may retire what.

## Authority gates

- **This is a Git code/memory transaction and nothing more.** It publishes only the explicitly
  authorized code and prepared memory commits/merges, with source/destination refs, conflict checks,
  and recovery evidence.
- **It does not launch** automatic code-quality checks, full test suites, or independent review.
  **Full code quality and full tests run only after an explicit developer request.** Curation is
  never deferred that way: the curator has already run the complete memory-quality operation as part
  of curation, and this transaction carries that result as a prerequisite instead of rerunning it.
- **Human-pinned gates stay human**: `integration-approval`, `push-approval`, `cleanup-approval`.
  Absent a durable raised gate, the series' standing approval governs — the developer's
  portfolio-gate approval of this series, recorded in the planner master's decision log, covers
  orchestrator-released integrations.
- **Owner-never-self-approves** holds; a recorded retry of a declaration with the same intent is
  idempotent, not a new approval.
- **Never `git commit`, merge, or push outside this transaction**, and never move a protected branch
  without an explicit developer request.
- The final super → main landing follows the resolved `system/git-workflow.md`: PR to gated main,
  remote merge, any needed onboarding carry-over, then push — **push only after the architect returns
  the developer's approval**. Unchanged memory keeps its actual commit; do not create a
  mapping-only commit for the merge SHA.

## Failure handling

- **Ancestry conflict or divergence** → contract-addressed `worktree_sync`; a retained mechanically
  derivable merge conflict is resolved through the advertised continuation. A semantic conflict
  follows the ordinary escalation path and is never silently converted into abandonment.
- **A candidate that no longer sits on the current source** produces a new targeted closeout after
  the moved source is propagated downstream into its worktrees.
- **Integration-branch conflicts** return to the leaf that owns the change as a scoped fix, then
  re-enter the door/projection path with new proven provenance.
- **Worker/curator check results stay attached as truthful evidence.** Failures and not-run checks are
  reported and never relabeled as full green.
- **Interrupted transaction** → resume the exact generation through the advertised control action;
  the transient landing lock and the closeout queue are never recovery evidence.

## Handoff / exit

The transaction's durable record is the enclosure-root operation journal plus the task/door
dispositions the run publishes. The manager's handover to the orchestrator is the
master-handover packet (`../templates/master-handover-packet.md`); the orchestrator's handover to the
architect offers a reviewable environment with demo notes. Each packet is written before the turn
ends, and the owner that wakes validates it (see `../core/acceptance.md`).
