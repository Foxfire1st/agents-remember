---
name: c-09-git-worktree-manager
description: "Create, attach to, report on, integrate, finalize, and clean up Agents Remember worktree-backed tasks while preserving delegated/human approval gates and external-memory compatibility."
---

# c-09-git-worktree-manager Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper.

The `c-09-git-worktree-manager` skill wraps the existing light-task or external workflow (a build that fits one session still rides a THIN `w-02-light-task-workflow` doc — chat is never a build route, per the `l-01-agent-lifecycles` invariant). It owns Git worktree state, series contracts, leaf enclosures, external-memory compatibility checks, integration, lifecycle finalization, and cleanup. It does not replace the workflow that performs the actual implementation.

For closeout, use the `c-12-closeout` skill. The `c-09-git-worktree-manager` skill only supplies the worktree-specific
contract path and integration/finalization follow-up rules.

## MCP Tools

Use the Agents Remember MCP worktree tools as the normal installed runtime
entry point:

> **Preview first.** `worktree_start`, `worktree_integrate`, `worktree_cleanup`,
> and `lifecycle_finalize_task` now **apply by default**. Run each once with `dry_run=true`
> to inspect the plan, confirm, then run the real apply (omit `dry_run`).

```text
worktree_start(repo_id="<repo-id>", task_name="<task>", worktree_name="<leaf-worktree>", leaf_id="<leaf-id>", workflow_kind="light-task")
worktree_enclosure_adopt(contract_path="<stable series-contract.md>", expected_worktree_group="<exact root>", rationale="<audit reason>", dry_run=true)
worktree_attach(repo_id="<repo-id>", task_name="<task>", leaf_id="<leaf-id>")
worktree_status(repo_id="<repo-id>", task_name="<task>", leaf_id="<leaf-id>")
worktree_sync(contract_path="<enclosure series-contract.md>")
closeout_door(request={action:"declare|status|defer|resume|withdraw|update-provenance", contract_path:"<enclosure series-contract.md>", ...})
closeout_queue(request={action:"status|rebuild", sprint_task_document_ref:{repository:"<repo-id>", path:"<sprint task.json>"}})
worktree_closeout_preview(contract_path="<enclosure series-contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<enclosure series-contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_operation_control(contract_path="<enclosure series-contract.md>", operation_kind="closeout|integrate|direct-landing", action="retry|recover|cancel|revise|retire|supersede", expected_generation=<generation>, intent_note="<audit intent>")
worktree_legacy_operation(contract_path="<enclosure series-contract.md>", operation_kind="closeout|integrate|direct-landing", action="inspect|migrate|archive", ...)
worktree_integrate(contract_path="<enclosure series-contract.md>", strategy="ff-only")
worktree_cleanup(contract_path="<enclosure series-contract.md>", teardown_providers=true)
worktree_abandon(contract_path="<enclosure series-contract.md>", force=false)
task_reopen(contract_path="<enclosure series-contract.md>", dry_run=true)
lifecycle_finalize_task(contract_path="<enclosure series-contract.md>", task_doc_path="<task.json>", master_doc_path="<parent task.json>", subtask_number="<N>", dry_run=true)
```

Callers identify repositories by configured MCP `repo_id`. The MCP server owns
workspace root, coordination root, provider setup settings, and path containment.
The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Stable Address, Enclosure Root, And Lifecycle Resume

The stable public address is the leaf's configured `series-contract.md`; it is not the live
operation store. `worktree_start` reserves one strict address-only locator for that contract and
publishes an immutable manifest under the worktree enclosure root's dedicated `.lifecycle/`
directory. The same root-local metadata directory owns the canonical live operation journals and
history. The locator binds the exact contract address, root, manifest digest, and publication
generation; it contains no task state, queue row, attempt, worker, commit, certification, or Git
truth.

Lifecycle reads have exactly two strict, state-disjoint routes:

```text
live operation
  configured contract address
  -> exact independent locator
  -> immutable enclosure-root manifest
  -> canonical root-local journal/history

terminal cleanup
  configured contract address
  -> exact terminal locator
  -> exact external archive + receipt
  + surviving configured contract truth
```

The live route is valid only while the locator is addressable and the enclosure root still owns the
manifest and journal. A `terminal-archived` locator never falls through to that root path: its exact
external archive and receipt preserve the collected manifest/journal/history, while the configured
contract supplies the surviving cleanup truth. Neither route scans task documents, worktree
folders, names, or `reports/`, and neither accepts a caller-supplied worktree group as authority. A
legacy enclosure created before locators existed is addressable only after the explicit audited
`worktree_enclosure_adopt` MCP tool validates the exact contract/root pair and publishes its
receipt. Normal readers never become compatibility readers.

`worktree_status` is the public status action for both routes. Before terminal archival it reports
the live journal generation and its executable controls. After terminal archival it must report
the archive-ready state plus surviving contract cleanup state, not claim that the deleted or
partially deleted live root remains addressable. `terminal-archived` is only archive-ready: cleanup
is complete only after the destructive tail succeeds and the contract records `cleanup=completed`
or `cleanup=abandoned`. If archive proof exists but that contract publication is still incomplete,
retry the exact accepted public disposition with its original arguments: `worktree_cleanup` owns
the accepted `teardown_providers` value and `worktree_abandon` owns the accepted `force` value.
The terminal archive binds that typed `cleanupArguments` object into its request identity.
`worktree_status` and terminal-request conflicts return the same `cleanupArguments` plus the exact
public retry `nextArgs`; execute those bytes rather than reconstructing a call. A retry with a
different argument value refuses. Omission/default replay is not a recovery fallback. This terminal
retry is not a `worktree_operation_control` action.

`worktree_start` still promotes the session's fleeting lifecycle to persistent and records the
binding in the configured contract. `worktree_attach` resumes that lifecycle through the proven
address chain, so the model never passes a lifecycle or operation id. Attaching while holding an
unsaved fleeting lifecycle still requires the explicit `on_unsaved="save"` or
`on_unsaved="discard"` choice; unsaved work is never dropped silently.

## Pre-Worktree Intake

The `c-09-git-worktree-manager` skill starts after the normal task intake and onboarding gate, not before them.

The intended order is:

1. run the `c-08-ar-coordination-context-resolver` skill for the target repository
2. run the `c-02-memory-quality-control` skill's task-start drift check and follow the existing AGENTS Gate 3/4 choice point
3. when onboarding is refreshed, commit the memory content and ledger before starting any worktree
4. place every build in a leaf under a master, even when the leaf is tiny and one owner wears the
   backend hats; “short root” changes orchestration depth, not the task topology
5. read the repository's `system/git-workflow.md` and identify the parent branch
   edge. The master owns a root `series-contract.md`
   and a pushable integration branch first; each leaf enclosure branches from that
   integration branch and integrates back into it. For a nested master, create
   the child integration branch from the parent integration branch.
6. choose or review the task slug and workflow variables
7. establish the applicable worktree-start authority: for a new master/leaf plan, hand off the
   **Worktree Intent Gate** for explicit developer approval; for subordinate leaves/edges inside an
   accepted orchestrated series, record the accepted-series authority and continue without a new
   developer hand-off
8. create the durable task wrapper when one is needed
9. request the `worktree_start` MCP tool only after the task identity is stable, the
   correct landable `source_branch` is selected, external memory is clean, and
   the applicable authority has been recorded

The Worktree Intent Gate must name:

1. target repo and build mode
2. discovered branch policy from `system/git-workflow.md`
3. proposed protected/source branch and, for a master series, the integration branch
4. proposed leaf work branch, worktree name, and `leaf_id`
5. memory mode and memory branch behavior
6. intended landing path from closeout through integration, PR/merge when needed,
   memory carryover, and lifecycle finalization
7. material risks, unusual choices, or unresolved branch-policy questions

If the repo is PR-gated, the intent packet must make the protection boundary
visible: leaf work branches integrate into the pushable integration/source branch
recorded by their enclosure; protected targets are reached later through the
repo's PR flow.

For developer-gated starts, run the applicable dry-run/preflight first, then **hand off**: call
`lifecycle_turn_end_notification(summary={…the intent packet + the approve/revise ask…})` as the **last
tool call**, then deliver the intent packet as your final prose and **STOP / end your turn**. The
notification sets the `awaiting-developer` lifecycle state, surfaces a
dashboard attention item, and returns immediately (no wait, no inbox). The developer approves from the
dashboard or in the leaf's attached chat; the **first AR tool call of your next turn** auto-resumes the
lifecycle (`running`), clears the attention item, and proceeds to `worktree_start` — you send no explicit
`lifecycle_resume`.

For subordinate orchestrated-series starts, do the same dry-run/preflight, record the accepted
planner/series authority in the task decision log or worktree intent note, and continue. Do not add
a developer stop for every leaf worktree.

For `w-02-light-task-workflow` task documents, the durable master artifact shape is
`<task-root>/<task-slug>/task.md`. Each build leaf stores its enclosure at
`<master-task-folder>/enclosures/<leaf-id>/series-contract.md`; the master stores its integration
contract at `<master-task-folder>/series-contract.md`.

## Start / Attach / Status

The `worktree_start` MCP tool resolves `c-08-ar-coordination-context-resolver` context, creates or
loads the leaf `series-contract.md`, prepares the code worktree first, and then prepares
external-memory state when enabled. Before exposing the checkout it publishes the reserved locator,
the strict enclosure-root manifest, and the exact initial contract generation in their declared
order. A crash at any cut is recovered through the same stable contract address; an exact retry
converges and a conflicting reservation refuses. If the task root is a master and no root series
contract exists yet, start first creates the master integration branch and root
`series-contract.md`, then starts the leaf from that integration branch. External-memory start
refuses to continue when the source memory repo has uncommitted changes; refreshed onboarding and
the ledger must be committed first so the new worktree starts from an auditable memory baseline.

Before start, attach, reopen, task-bound terminal assignment, or hosted role spawn can expose a
checkout to an agent, the control plane resolves **transitive source lineage from task identity**.
A master must contain its super integration branch; a leaf must contain its master, whose branch
must in turn contain super. The same edges are required for external memory. Missing contracts,
missing/mismatched branches, and incomparable Git histories are `source-lineage-unavailable`;
behind or diverged descendants are `source-lineage-stale`. Both fail closed before session/process
creation or lifecycle mutation. The projection names relations, branches, ahead/behind counts, and
ordered `worktree_sync` contract paths, but never asks an agent to retain a commit id.

This is distinct from remote stale-base policy. `stale_base_choice="proceed-stale"` can override a
remote-tracking freshness choice, but never structural super → master → leaf ancestry. Resuming a
thematic master after other masters landed is expected to surface this gate—sync that existing
master rather than splitting follow-up work into artificial new masters. `worktree_status` and the
Engine Room expose `sourceLineage` so the reason and recovery remain visible.

Start runs a **stale-base preflight** (GitHub #54) before any worktree exists:
when the code or memory source branch is `behind` or `diverged` from its remote
tracking branch, start blocks with `choose_stale_base_recovery` — a stale base
produces wrong code and silently converts the provider seed fast-path into a
multi-minute reindex. Recoveries: re-run with
`stale_base_choice="fast-forward"` (the tool fast-forwards the stale local
branches, then starts) or `stale_base_choice="proceed-stale"` (explicit
override). Offline (`unknown`) and `no-upstream` states never block. A missing
external-memory source branch is no longer a manual step: start auto-creates it
at the official memory tip using the code source branch name as template
(reported as `memorySourceBranch` in the result).

The recorded leaf `source_branch` is not merely the base branch. It is the branch
that `worktree_integrate` will later fast-forward or replay into. In a master
series this is the master integration branch; in a nested master it is the
nearest parent integration branch. Protected targets are handled after the
integration branch lands.

When external memory is enabled, the `c-09-git-worktree-manager` skill validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. Missing external memory is not a `c-09-git-worktree-manager` bootstrap path; run the `c-00-initialize-memory-repo` skill first. If no compatible memory state exists, the `c-09-git-worktree-manager` skill stops and reports the allowed human choices:

1. `reconciliation`
2. `disabled-memory`
3. `custom`

The common trigger is starting a worktree off a **freshly-merged gated branch**: the PR merge commit
lands on top of the verified tip with a new SHA the ledger has not mapped. Running
`c-11-memory-carryover-from-branch` against the merged spear *after* the PR merges maps that merge
commit automatically — even when nothing else needs carrying — so the next worktree starts cleanly
without needing `reconciliation`.

For a live locator, `worktree_attach` and `worktree_status` resolve the configured contract address
through the locator and root manifest, then report recoverable state without mutating Git. A
malformed, moved, or deleted task document cannot hide a live journal. Conversely, a missing or
mismatched locator/manifest is a typed addressability refusal, never permission to scan or infer
another root. In this live route, `worktree_status` includes the current operation generation,
phase, task-addressed legal next actions, dirty worktree flags, and a fetch-free `freshness` block
comparing the contract's recorded base commits against the current local source branch tips. When
behind, it carries the exact `worktree_sync` route.

`worktree_attach` refuses a terminal locator because there is no live workbench to resume.
`worktree_status` instead takes the terminal archive/receipt plus surviving-contract route and
reports archive-ready separately from cleanup-completed or abandoned, including the exact accepted
`cleanupArguments` and `nextArgs` for the required `worktree_cleanup` / `worktree_abandon` retry.
Queue presence is neither required nor consulted on either route.

## Mid-Task Sync

A live worktree's base pair decays while parallel cycles land (a sibling leaf may
advance the integration/source branch; carryover may advance official memory). `worktree_sync`
(GitHub #54) pulls the moved official line in **atomically**: it fetches the
source upstreams, requires the new code tip to be ledger-mapped at the official
memory tip (a mid-cycle official line blocks with guidance to run
`c-11-memory-carryover-from-branch` first), merges the source branch into the
code work branch (conflicts abort cleanly), fast-forwards the memory work
branch, and advances the contract's recorded base pair with a `sync_log` entry.
Preview with `dry_run=true` first.

**Sync early — before memories are written.** With parked memory the sync is a
pure fast-forward: the other cycle's sidecars and ledger rows end up beneath
this task's future memory work, closeout appends on top, and end-of-series
integration stays `ff-only` with no carryover reconciliation. If the memory
work branch already has local commits and official memory moved, sync blocks
with `memory_sync_choice` recoveries: `merge-memory` (merge attempted; ledger
conflicts abort — the ledger is never auto-merged) or `skip-memory` (memory
deferred to end-of-task carryover; only the code base advances).

## Worktree Closeout

Use the `c-12-closeout` skill for worktree closeout. The `c-12-closeout` skill owns the approval gate,
missing-onboarding check, code commit, onboarding and entity refresh, memory
quality gate, memory content commit, ledger update, and ledger commit.

Closeout scheduling and closeout execution have different owners:

1. The `closeout_door` MCP tool publishes one exact contract-owned generation after current task,
   review, memory, ledger, admission, source, and priority evidence is complete. Its disposition is
   `waiting`, `deferred`, `withdrawn`, or `claimed`.
2. The `closeout_queue` MCP tool is only the sprint's source-fingerprinted ordering projection of
   current `waiting` generations. It has `status` and `rebuild`; it has no declare, select, claim,
   retry, recover, certify, integrate, replan, or drain action.
3. `worktree_closeout_apply` revalidates that the exact waiting generation is first ready and uses
   one short claim CAS over the accepted task/door revision. The CAS ends before worker execution,
   quality, or Git mutation. Acceptance transfers authority into the enclosure-root operation
   journal; no durable task or queue lock is created.

Task documents remain authoritative during every closeout phase. An intrinsically valid task or
door mutation publishes first, then every scope in its before/after governing-sprint union becomes
non-admitting `invalid-empty` and rebuilds from current task plus current waiting-door facts.
`task_doc` returns a machine-readable `projectionEffects` entry for each affected scope and an exact
`nextAction` whenever a rebuild did not finish. Agents execute that rebuild hint; they never roll
back or postpone the accepted task write, patch an old candidate row, or wait for operation
completion. Unrelated sprints and repositories retain their projection revisions.

For worktree-backed tasks, pass the configured leaf `series-contract.md` to
`worktree_closeout_preview` / `worktree_closeout_apply`. Every enabled commit leg requires its own
explicit nonblank message before authority is acquired. The accepted input is immutable per
generation. A pre-output failure may retry the same input, cancel, or revise through a successor;
ambiguous or proven output must reconcile/recover the same generation. Execute only an advertised
task-addressed action through `worktree_operation_control`; never repeat Git directly or use queue
state as recovery evidence.

If the recorded code or external-memory source branch moves, admission refuses that landing edge
with the exact `worktree_sync`/provenance-republication route. The moved source does not veto task
authoring and does not erase the journal or door generation.

## Integration

Integration runs only after closeout completed and is authority-gated by context. It lands the
closed task branches back onto the recorded source branches and records the landed commits
separately from the closeout commits in the operation journal. A queue projection may be absent or
invalid-empty throughout integration; `worktree_status` and `worktree_operation_control` remain
task-addressed through the locator/manifest/journal chain. If a crash occurs before or after the
protected ref moves, recovery reconciles the live ref and the recorded accepted base pair before
advertising a next action. A later landing may not pass the same target until that exact owner is
reconciled, but the landing exclusion never blocks task-document mutation. In an accepted
orchestrated run, dependency-ordered
leaf→master and master→super integrations ride the series' standing approval (the developer's
portfolio-gate approval recorded in the planner master) — the developer hand-off concentrates at
the super PR/carry-over gate per the `l-01-agent-lifecycles` loop/orchestrator doctrine. A raised
durable `integration-approval` gate still awaits the developer.

On an orchestrated master's exit (master → super integration) the integrate step additionally enforces the delegated `master-handover-approval` seam: an undecided or policy-invalid handover gate addressed to the master (by `enclosure` = master task name) returns `handover-gate-blocked` instead of landing — decide the gate per the `l-01-agent-lifecycles` seam doctrine, then rerun. When no gate addresses the integrating master but open `master-handover-approval` gates exist elsewhere, integrate still proceeds and its result carries a `handover_gate_warning` naming them — treat it as a spelling check on the raised gate's `enclosure`.

Run `worktree_integrate(..., dry_run=true)` first. For subordinate accepted-series integrations,
record the standing series authority and then run the real integration without a developer stop.
For developer-gated integrations, **hand off**: call
`lifecycle_turn_end_notification(summary={…the integration plan…})` as the **last tool call**, then
deliver the integration preview as your final prose and **STOP**.
The developer approves on the dashboard or in chat; the first AR tool call of your next turn auto-resumes
and runs `worktree_integrate`; the agent never self-approves a human-pinned durable gate.

Before previewing integration, check out the recorded code and memory `source_branch` in their source repositories; `worktree_integrate` requires those active checkouts even for `dry_run=true`.

Integration always lands into the recorded `source_branch`. It does not open a
PR and it does not discover protected-branch policy on its own; that policy must
be reflected in the branch choice made before `worktree_start`.

Strategies:

1. `ff-only`: require current code and memory source branches to be ancestors of the closeout commits, then fast-forward both source branches.
2. `replay`: when source branches moved because parallel work landed first, replay the code task commit onto current code source, replay only the memory content commit onto current memory source, regenerate `memory.md` for the final landed code and memory content commits, then fast-forward both source branches.

Conflict rule: if code replay or memory-content replay conflicts, stop before moving source branches. The agent must discuss the resolution with the developer and decide what is true before continuing. Do not replay an old ledger commit over current memory main; always regenerate the ledger row after memory content has been mediated.

After successful integration, complete any repo-specific landing tail first: push/PR/merge for PR-gated code, pull the protected target back locally, and carry memory forward until the official memory branch maps the landed code commit. Then use `lifecycle_finalize_task` for the terminal edge.

## Lifecycle Finalization And Cleanup

Lifecycle finalization runs only after closeout, integration, and any PR/carryover tail are
complete, and its approval authority follows the same series boundary. For subordinate
accepted-series leaf/master edges, the owning manager/orchestrator may finalize and clean up after
the dry-run proves the landed edge. For final super→main cleanup, standalone work, or a deliberately
raised `cleanup-approval` gate, stop for developer approval.

Terminal cleanup has an additional evidence boundary because the enclosure root contains the live
manifest and journal. Before deleting any part of that root, the terminal operation:

1. proves the exact operation generation is terminal and no active or ambiguous worker/Git evidence
   remains;
2. archives the canonical manifest, journal, and history outside the deletion target;
3. reads the archive back and verifies its exact bytes;
4. publishes a compact external terminal receipt and advances the address-only locator to
   `terminal-archived`;
5. only then removes worktrees, merged branches, disposable reports, and the enclosure root.

Until the locator advances to `terminal-archived`, the live route remains authoritative even when
the exact external archive/receipt bytes were already published and read back. A crash before that
locator advance retries the exact accepted `worktree_cleanup` or `worktree_abandon` call, reuses
those bytes, and finishes terminal-locator publication. A crash after the locator advance but
before deletion or contract publication leaves archive-ready, not cleanup-completed, truth; the
same accepted disposition and exact archived `cleanupArguments` must finish the destructive tail.
Use `worktree_status` to observe which state survives and execute its exact `nextArgs`. A changed
`teardown_providers` or `force` value is a request conflict, not a revised cleanup generation.
Missing, unreadable, or mismatched archive/receipt proof refuses deletion.
`reports/` files are not canonical lifecycle evidence and are not copied as a substitute. Active
or ambiguous journals are never collectable. After deletion, deliberate root absence is accepted
only through the exact external receipt plus the surviving contract state; accidental absence
remains a typed failure.

Finalization separately proves the current parent-child branch edge and updates task documents.
Task completion is not the evidence that authorizes enclosure deletion, and deletion is not allowed
to erase the evidence that proves task completion.

Run `lifecycle_finalize_task(..., dry_run=true)` first. For subordinate accepted-series cleanup,
record the standing authority and run the real finalizer. For developer-gated cleanup, **hand off**: call
`lifecycle_turn_end_notification(summary={…what cleanup removes…})` as the **last tool call**, then relay
the landed-commit proof, cleanup plan, and task-document updates as your final prose and **STOP**. The developer approves
on the dashboard or in chat; the first AR tool call of your next turn auto-resumes and runs
`lifecycle_finalize_task`; a model-attributed decision is never developer approval for a
human-pinned durable gate.

`lifecycle_finalize_task` proves one immediate edge: the contract's landed code
commit (`integrated_code_commit` when present, otherwise `code_commit`) must be an
ancestor of the recorded local `code_source_branch`, and external-memory carryover
must already be done. This handles leaf-to-parent and parent-to-parent chains the
same way; for a PR-gated edge, the model first finishes the PR workflow and pulls
the target branch locally, then the finalizer sees the same local branch
relationship as a direct edge. The tool does not infer squash equivalence by
default; squash merges are emergency/manual recovery because they erase commit
lineage and can invalidate memory lookup history.

The finalizer resolves the leaf from the contract's task root and leaf id. An omitted
`task_doc_path` adopts that exact document; a supplied path is an assertion and must
match it. Before any cleanup (including a dry-run cleanup preview), every declared
top-level step and nested substep must be `done`. Use `task_doc.skip_step` with an
exact id and nonblank reason for an intentional skip; cleanup/finalization never
auto-checks work. When the bound leaf declares an existing immediate parent, the
finalizer always derives that parent and reconciles its exact row to `Completed`, even
when both optional parent assertions are omitted. `master_doc_path` and
`subtask_number` are independent identity assertions; when supplied, each must match
that derived edge. Standalone/no-parent leaves remain supported. The finalizer does
not mark the parent task itself complete or recursively complete ancestors; each
parent-child edge is finalized separately.

Standalone `worktree_cleanup` is deliberately non-terminal for task documents. If a
declared final step includes cleanup, run standalone cleanup first, then mark that
exact step `done`, and finally run `lifecycle_finalize_task` against the already-clean
contract. Do not mark a self-referential "make this task Completed" step prematurely;
split or reword it as the concrete cleanup/preparation work.

Cleanup is idempotent only against the same proven terminal generation. If the worktrees,
merged branches, or enclosure root are already gone, the external terminal receipt must prove that
their absence was deliberate before the tool reports the already-clean state. If Git refuses to
delete an unmerged branch, cleanup leaves that branch and the exact terminal archive/receipt
evidence in place and reports it for developer review.

## Reopening A Completed Leaf

Reopening reuses the EXACT same leaf id — never mint a suffixed leaf (`…-r1`).
`task_reopen(contract_path=…)` is a state reset, not a worktree creator: it refuses
anything but a fully landed leaf (closeout, integration, and cleanup completed, worktrees
gone), then resets the contract's review/closeout/integration state, clears the stale
lifecycle binding, marks `cleanup: reopened`, and puts the leaf's task document back to
`planning` (master index entry flipped, audit decision appended). Preview with
`dry_run=true` first. Afterwards: edit the doc's steps via `task_doc` (add, change, or
untick work), then run a normal `worktree_start` with the same leaf id. Start may reserve a new
generation at the same stable locator address only after the prior terminal archive and exact
restartable predecessor contract are proven under one short CAS. The successor manifest carries
the typed immediate-predecessor archive link; exact retries converge, conflicting successors
refuse, and the prior archive remains independently readable. After successor reservation, the
stable contract address may contain only the exact accepted predecessor tombstone bytes or the
already accepted successor bytes. The reservation atomically replaces only those exact predecessor
bytes with the accepted successor contract; an identical observation converges and every other
byte state refuses. This is neither a generic contract overwrite nor a compatibility reader. Start
then recreates the worktrees off current source tips, promotes/mints a fresh lifecycle, and
restamps the document's `lifecycleId`. The same rule applies to a sanctioned successor after
abandonment. A live, ambiguous, or merely cleaned-without-receipt locator can never be overwritten.

## Boundaries

1. The `c-09-git-worktree-manager` skill may create or reuse worktrees, root series contracts, and leaf enclosure contracts.
2. The `c-09-git-worktree-manager` skill does not initialize memory roots; use the `c-00-initialize-memory-repo` skill before starting external-memory worktrees.
3. Closeout belongs to the `c-12-closeout` skill; the `c-09-git-worktree-manager` skill only supplies worktree contract context.
4. The `c-09-git-worktree-manager` skill must not use divergent memory as semi-trusted reference context.
5. The `c-09-git-worktree-manager` skill must not bypass the `c-12-closeout` skill's applicable
   closeout authority gate.
6. The `c-09-git-worktree-manager` skill must not create closeout commits outside the `c-12-closeout` skill's code-memory-ledger sequence.
7. The `c-09-git-worktree-manager` skill must not call `worktree_start` until
   the applicable authority has been recorded: developer-approved Worktree Intent Gate for a new
   master/leaf plan, or accepted-series authority for subordinate orchestrated work.
8. The `c-09-git-worktree-manager` skill must not move source branches during integration until
   replay/preflight has produced fast-forwardable code and memory commits and applicable
   integration authority exists.
9. The `c-09-git-worktree-manager` skill must not finalize or clean up without applicable
   cleanup/finalization authority and proven external terminal archive/readback/receipt.
10. The `c-09-git-worktree-manager` skill must not treat squash-merged content as a normal landed edge.
11. The `c-08-ar-coordination-context-resolver` skill remains the facts-only resolver; the `c-09-git-worktree-manager` skill owns worktree and lifecycle mutation.
12. No queue, lane, operation, locator, or blocker state may refuse an intrinsically valid
    `task_doc` mutation; the mutation's projection effects own invalid-empty rebuild guidance.
13. The closeout queue owns no lifecycle or commit evidence, and no recovery path may use an old
    queue row, raw Git, a task/worktree scan, naming inference, or a reports-path journal.
14. The address-only locator must not duplicate task, door, operation, worker, commit,
    certification, or Git truth.
15. A terminal archive must bind the exact accepted `teardown_providers` or `force` argument and
    return that value in every retry action; changed terminal arguments never fall back or replay.
16. A successor reservation may replace only the exact accepted predecessor tombstone at the
    stable contract address; no mismatch is overwritten or interpreted through compatibility code.
