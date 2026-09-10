---
name: c-12-closeout
description: "Close out approved Agents Remember edits as explicit code-memory-ledger Git transactions with conflict safety, ledger alignment, and no automatic push."
---

# c-12-closeout Closeout

Use this skill when approved Agents Remember edits in an external- or
internal-memory worktree need to be committed.

The `c-12-closeout` skill owns closeout sequencing for worktree-backed tasks.
**Closeout is worktree-only:** every change affecting the code repo runs through a
`c-09-git-worktree-manager` dual worktree (code + memory) — there is no
direct-checkout closeout path. Use the `c-09-git-worktree-manager` skill for
worktree start, attach, status, integration, lifecycle finalization, and cleanup;
use this skill for the explicit closeout authority and code-memory-ledger commit order. Closeout is a
Git transaction; it does not run or require code-quality checks, test suites, memory-quality checks,
curator certification, or independent review.

**Handoff note:** the worker reports targeted checks and the curator maintains affected onboarding
before handoff. The closeout seat consumes the prepared code and memory content; it does not author
onboarding or rerun the worker/curator checks. An independent review may be requested by the
developer or the task's approved plan, but its record is an input for context only and is never an
automatic closeout prerequisite. When a review is requested, the `l-01-agent-lifecycles` skill's
three-round monotonic rule applies: review 1 fixes the complete finding list, reviews 2 and 3 verify
only that list and require a shrinking remaining count, and a fourth round asks the developer.

## MCP Tools

Use the worktree closeout tools against the task contract:

```text
worktree_closeout_preview(contract_path="<enclosure series-contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<enclosure series-contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_status(repo_id="<repo-id>", contract_path="<enclosure series-contract.md>")
worktree_operation_control(contract_path="<enclosure series-contract.md>", operation_kind="closeout", action="cancel|resume", expected_generation=<generation>, intent_note="<audit intent>")
worktree_legacy_operation(contract_path="<enclosure series-contract.md>", operation_kind="closeout", action="inspect|migrate|archive", ...)
direct_landing(contract_path="<task-root series-contract.md>", code_commit="<verified branch HEAD>", memory_commit_message="<message>", ledger_commit_message="<message>", intent_note="<authority>", candidate_tree="<gated tree>")
```

Worktree closeout records closeout state in the contract the
`c-09-git-worktree-manager` skill created or attached; the
`c-09-git-worktree-manager` skill owns later integration, lifecycle finalization,
cleanup, and task-document completion.

## Approval Authority

Closeout is always authority-gated, but the authority is contextual.

For standalone work, final super-branch landing, or any closeout where the accepted task/series
authority is unclear, agents must request the matching preview tool first, relay the proposed code,
memory, and ledger commit messages to the developer, and ask for explicit commit approval.

For subordinate work inside an accepted orchestrated series, the owning seat may apply closeout
under delegated series authority after the transaction preview is coherent. Managers govern leaf commits;
the orchestrator governs manager/master edges and direct flat work when it is wearing the manager
or worker hat itself. Do not stop for the developer merely because closeout will create code,
memory, and ledger commits. The `intent_note` records the authority source, e.g. the accepted
planner/series task and the owning seat's review of the preview.

Closeout still stops for the developer when the work reaches the final completed super branch /
PR-carryover gate, when a `closeout-approval` gate has been deliberately raised, when the change is
outside the accepted scope, when Git inputs/conflicts prevent the transaction, or when a quo-vadis
decision is required. Worker or curator check failures remain reported evidence for the owning role;
they are not an automatic closeout quality gate.

Real closeout uses the matching apply tool with an `intent_note`. The note records the applicable
authority: either explicit developer commit approval or delegated accepted-series authority. Agents
must not treat a vague "looks good" or their own preference as authority.

Every contract-enabled code, memory, and ledger leg also requires its own explicit nonblank commit
message. Preview and apply normalize the same effective input before any claim, worker, journal, or
Git authority is acquired; a blank required cell is a typed no-effect refusal, not an immutable
half-operation. A typed not-applicable leg omits its message instead of receiving a synthesized
default.

Approval remains outside and before apply: preview, relay, and the applicable explicit or delegated
authority must be complete before `worktree_closeout_apply`. Apply validates the immutable
transaction input, stages and commits the enabled code, memory-content, and ledger legs through the
existing transaction owner, and records each resulting commit. It does not run or demand a
certification profile, code-quality check, test suite, memory-quality check, curator certificate, or
review record. Full quality or full-suite tools run only when the developer explicitly requests
them through their existing tools; their absence does not block this transaction.

The transaction owner may use only the existing bounded retry/recovery behavior. It must preserve
the accepted source/destination identities, refuse moved refs or unresolved Git conflicts before a
ref move, and publish recoverable journal evidence for every completed leg. A worker or curator
check result may be reported alongside the preview, but it is diagnostic evidence rather than a
closeout gate.

Staging is **not** undone if the gate refuses. The worktree stays fully staged, nothing is
committed, and that is the intended end state rather than a gap: the checkout being staged is the
task's own worktree, created by `worktree_start` and destroyed by `lifecycle_finalize_task`, so no
one is holding a partial staging in it — and a retry does not inherit that index, because each gate
run begins with `git reset` and restages from the working tree. The reset is what makes the retry
equivalent to a first run rather than an assertion that it is. `git add -A` on its own is not
enough: git applies ignore rules only to paths it does not already track or hold staged, so a file
staged by a refused attempt stays staged even after the retry adds it to `.gitignore`, and the
commit carries it. Resetting first recomputes what gets staged on every run under the ignore rules
in force at that moment, and `--mixed` is index-only, so no file content is touched.

Two refusals guard the transaction before staging or ref movement. Closeout refuses when the code
checkout is **not** the declared task worktree (unless the declared transaction route is a sanctioned
branch-direct landing) or when the code or memory worktree has unresolved merge conflicts. A missing
quality profile, certificate, review record, or suite result never creates a compatibility route and
never blocks an otherwise authorized transaction. Older tasks without code changes remain valid and
do not need a profile merely to be read or resumed.

For a developer-gated closeout, the relay follows the `l-01-agent-lifecycles` orchestrator hand-off protocol: run the
preview/dry-run first, then call
`lifecycle_turn_end_notification(summary={…the preview facts + the commit ask…})` as the **last tool
call**, then deliver the preview facts and proposed messages as plain
chat output ending with the commit ask, and **STOP / end your
turn**. The notification sets the `awaiting-developer` lifecycle state, surfaces a dashboard attention item, and
returns immediately (no wait, no inbox). The developer approves on the dashboard or in the leaf's
attached chat; the **first AR tool call of your next turn** auto-resumes the lifecycle (`running`),
clears the attention item, and runs `worktree_closeout_apply` — you send no explicit `lifecycle_resume`.
Never invoke `worktree_closeout_apply` in the same turn as the relay; the preview report is what the
developer sees.

Apply returns promptly after starting or observing the task-bound closeout generation. Use
`worktree_status` against the configured contract to read the current journal projection. Do not
wait on a queue row or retain an operation id. If the journal advertises `cancel`, it stops the
worker and preserves source changes and historical evidence. If it advertises `resume`, it accepts
the fixed transaction input and continues the unfinished commit leg; it does not reconstruct
certification history or rerun an acceptance suffix. Actual publication keeps its existing
transactional unfinished-leg protection. Execute only the advertised closeout action through
`worktree_operation_control`; never run Git directly, edit journal bytes, or use a queue row as
recovery evidence.

## Explicit Durable Closeout Gates

`closeout-approval` is the sole human commit gate for code, memory, and ledger when one is
explicitly raised. It gates only admission of the addressed closeout generation; it never freezes
the task document, another sprint, or an already accepted operation. Apply accepts only a current
developer-attributed approval and consumes it once. Open, rejected, revision-requested, applied,
or model-approved gates refuse closeout admission. A model never self-approves a human-pinned gate.

Do not create a durable gate as an incident workaround or compatibility route. Ordinary
subordinate closeouts use recorded accepted-series authority; standalone/final work uses the
developer hand-off above. The closeout preview/apply payload's `closeout_gate` block is evidence of
an explicitly existing gate, not a second lifecycle or recovery mechanism.

## Preconditions

The `c-12-closeout` skill resolves or consumes the current
`c-08-ar-coordination-context-resolver` context. External-memory closeout
requires the code checkout/worktree and memory repo/worktree to be on the same
selected branch; internal-memory closeout commits its memory changes with the
code worktree.

Ledger compatibility is based on code-to-memory commit mappings, not branch
metadata.

The transaction consumes the worker's targeted-check report and any curator
onboarding handoff as context; it does not rerun either one. The curator owns
affected onboarding and scoped memory checks before handoff. Full memory
quality is an explicit developer-requested operation through
`c-02-memory-quality-control`, not a closeout precondition.

The transaction does not inspect coding guidelines, certification profiles, or
historical quality artifacts. Those concerns remain available through their
own tools when the developer explicitly requests them. Closeout reports the
actual Git inputs and conflicts, then either performs the authorized
code-memory-ledger transaction or refuses before mutation.

## External-Memory Order

External-memory closeout order is:

1. Confirm the worker's targeted-check report and the curator's scoped onboarding
   handoff when those roles are present. Record failed or not-run checks as
   reported; do not reinterpret a subset as full green.
2. Preview the exact enabled code, memory-content, and ledger commit legs with
   their messages, source refs, destination refs, and current conflict/ref
   facts. Preview performs no quality, test, memory, certification, or review
   invocation.
3. After the applicable explicit or delegated authority is complete, call
   `worktree_closeout_apply`; it validates the same immutable Git input.
4. Commit the code changes, then the prepared memory-content changes, then
   prepend the resulting code/memory pair to `memory.md` and commit the ledger
   update. Keep the existing transaction owner, pair identity, and recoverable
   publication journal for each leg.
5. Refuse before mutation when a declared branch/ref moved, a worktree has an
   unresolved conflict, a required commit message is blank, or the code/memory
   pair cannot be reconciled. Name the concrete corrective action.
6. Update the task contract closeout state after the actual commit pair and
   ledger commit are recorded.

## Internal-Memory Order

Internal-memory closeout order is:

1. Confirm the worker and curator handoff facts when present; do not rerun
   their checks or require a review/certificate.
2. Complete preview and the applicable explicit or delegated commit authority.
3. Call `worktree_closeout_apply` and commit the code plus prepared internal
   memory changes in the existing transaction.
4. Record the actual resulting pair and update the task contract. Refuse only
   for concrete Git/input conflicts, missing authority, or incomplete
   transaction legs.

Push behavior is not automatic. Closeout commits code, memory, and ledger only;
it never pushes. Pushing the integration branch is part of the landing tail the
`c-09-git-worktree-manager` skill owns: call
`lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then present the push intent as
your final prose, and **STOP**; push only after the developer approves and
your next turn auto-resumes. A separately raised human-pinned `push-approval` gate, when present,
must be decided by the developer; it is not a closeout recovery route.

Closeout does not mark the task `Completed`. After closeout, integration, any
PR-gated merge/pull, and memory carryover are done, use
`lifecycle_finalize_task` from the `c-09-git-worktree-manager` skill to prove the
landed parent-child branch edge, run or verify cleanup, and update the current
task plus immediate parent row.

## Sanctioned Branch-Direct Landing

`direct_landing` is not a direct-checkout closeout path. It is the policy-gated,
branch-addressed counterpart for a **leaf delivered without its own worktree enclosure**, where a
code commit already exists at the exact series branch HEAD. A series contract is necessary address
authority for this route; it is not by itself evidence that an operation is direct execution.
Ordinary master/series closeout and the later master-to-parent `worktree_integrate` edge are not
branch-direct leaf delivery and must work while `directExecutionEnabled` is false.
The tool verifies that code commit and gated candidate tree, requires explicit nonblank memory and
ledger messages for enabled legs, and validates the complete effective input before acquiring
landing authority.

Apply persists a task/contract-addressed `direct-landing` operation generation before either Git
leg. Intent and proof for memory commit, ledger conflict detection, ledger staging, ledger commit,
and terminal publication are journaled independently. After interruption, read the same generation
through `worktree_status` and execute only its advertised action through
`worktree_operation_control(operation_kind="direct-landing", ...)`. Recovery reconciles exact
code/tree/memory/ledger evidence and reuses each already produced commit once; the queue is not an
input. A transient landing lock, synthesized subject, repeat-from-scratch, or raw Git is not
recovery.

## Explicit Legacy Operation Repair

Normal lifecycle readers accept only the current schema. For an exact historical schema-1 record,
use `worktree_legacy_operation(action="inspect")`, bind the returned digest, and then choose the
single supported audited transition: `migrate` fills only proven-missing unfinished memory/ledger
message cells for the known blank-input incident and preserves live code-output evidence in one
canonical generation; `archive` accepts only proven terminal/no-live-authority evidence. Canonical
`worktree_operation_control` then resumes the migrated generation. The legacy tool is explicit and
bounded; it is never called by status, normal journal reads, cleanup, or closeout apply.

## Failure Conditions

Closeout refuses before mutation when external memory is unresolved, the
declared code and memory checkouts do not identify the same transaction, a
required commit message is blank, no enabled leg has a change, a declared
source/destination ref moved, or a Git index contains unresolved conflicts.
The refusal names the exact input and corrective action. Missing quality
profiles, certificates, review records, and full-suite results are not refusal
reasons.

The code worktree must be the contract's task worktree unless the declared
route is the sanctioned branch-direct landing. Refuse before staging when it
is a normal checkout or when a merge, rebase, cherry-pick, or revert has
unmerged entries. This preserves Git's conflict state instead of allowing a
blind stage to commit conflict markers. Existing operation journals keep
recoverable per-leg evidence; after interruption, resume only the advertised
transaction action.

Every enabled blank/whitespace commit-message cell fails before operation authority and leaves no
claim, journal generation, worker, commit, or queue-lifecycle residue. A moved source or stale door
provenance refuses only the landing edge and returns the exact sync/republish route; it never locks
task authoring. Once a generation has been accepted, failures are classified from its journal and
live Git evidence and expose only evidence-safe task-addressed controls.

Worker and curator reports may identify missing onboarding or failed/not-run
checks. Route those reports to the owning role for repair or developer
direction; closeout does not rerun a full memory suite or certify the handoff.

## Boundaries

1. The `c-12-closeout` skill owns closeout approval and code-memory-ledger commit sequencing.
2. The `c-12-closeout` skill does not create worktrees, integrate worktrees, finalize lifecycles, or clean up worktrees.
3. The `c-12-closeout` skill does not initialize memory roots; use the `c-00-initialize-memory-repo` skill.
4. The `c-12-closeout` skill must not commit without the applicable authority after a closeout
   preview: explicit developer commit approval for standalone/final work, or recorded delegated
   series authority for subordinate accepted-series work.
5. The `c-12-closeout` skill must publish only the explicitly enabled code,
   memory-content, and ledger legs through their existing transaction owners.
6. The `c-12-closeout` skill must not invoke or require code-quality checks,
   test suites, memory-quality checks, curator certification, or independent
   review as a closeout prerequisite. Full checks run only after an explicit
   developer request through their existing tools.
7. The `c-12-closeout` skill must preserve the accepted code/memory pair,
   source/destination identity, commit messages, and recoverable per-leg journal
   evidence.
8. The `c-12-closeout` skill must refuse moved refs, unresolved conflicts,
   missing inputs, and ambiguous partial publication before unsafe ref movement.
9. The `c-12-closeout` skill must not push automatically.
10. The `c-12-closeout` skill must report worker/curator check failures or
    not-run checks without converting a subset into a full-green claim.
11. The `c-12-closeout` skill must not reconstruct historical certification or
    review state as a compatibility route.
12. The `c-12-closeout` skill must not acquire closeout authority until every enabled immutable
    input cell and the exact current door generation have validated.
13. The `c-12-closeout` skill must not resume from a queue row, direct Git, a reports file, a
    permanent compatibility reader, or synthesized commit-message input.
