---
name: c-12-closeout
description: "Close out approved Agents Remember edits by preserving approval authority, mandatory strict code quality before code commit, missing-onboarding checks, external-memory refresh, memory quality, ledger alignment, and no automatic push."
---

# c-12-closeout Closeout

Use this skill when approved Agents Remember edits in an external- or
internal-memory worktree need to be committed.

The `c-12-closeout` skill owns closeout sequencing for worktree-backed tasks.
**Closeout is worktree-only:** every change affecting the code repo runs through a
`c-09-git-worktree-manager` dual worktree (code + memory) — there is no
direct-checkout closeout path. Use the `c-09-git-worktree-manager` skill for
worktree start, attach, status, integration, lifecycle finalization, and cleanup;
use this skill for the closeout gate and code-memory-ledger commit order.

**Seat note (manager -> builder -> reviewer -> curator chain):** in that chain, the builder produces
code and a turn report only — it does not author onboarding. The dedicated curator seat
(`l-01-agent-lifecycles` `roles/curator.md`) runs the `c-05-create-or-update-onboarding-files` skill
as its own fresh pass, fed the leaf's landed change set, task doc, and notes/, BEFORE the owning
seat (the manager) runs this skill's closeout preview. Everywhere below that says "create" or
"refresh" onboarding, that authoring already happened in the curator's pass; the seat running
closeout **verifies** the curator's output against the checks in this skill, it does not author
onboarding inline to make a failing check pass. A check that still fails after the curator pass is a
closeout failure — respawn/rerun the curator, do not patch onboarding from the closeout seat. This
distinction does not apply outside that chain (e.g. a solo flat session with no separate curator
seat still runs `c-05-create-or-update-onboarding-files` itself before closing out).

## MCP Tools

Use the worktree closeout tools against the task contract:

```text
worktree_closeout_preview(contract_path="<enclosure series-contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<enclosure series-contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
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
under delegated series authority after the preview/checks are clean. Managers govern leaf commits;
the orchestrator governs manager/master edges and direct flat work when it is wearing the manager
or worker hat itself. Do not stop for the developer merely because closeout will create code,
memory, and ledger commits. The `intent_note` records the authority source, e.g. the accepted
planner/series task and the owning seat's review of the preview.

Closeout still stops for the developer when the work reaches the final completed super branch /
PR-carryover gate, when a `closeout-approval` gate has been deliberately raised, when the change is
outside the accepted scope, when checks remain red outside the task, when onboarding/memory quality
cannot be repaired inside the leaf, or when a quo-vadis decision is required.

Real closeout uses the matching apply tool with an `intent_note`. The note records the applicable
authority: either explicit developer commit approval or delegated accepted-series authority. Agents
must not treat a vague "looks good" or their own preference as authority.

Approval remains outside and before apply: preview, relay, and the applicable explicit or delegated
authority must be complete before `worktree_closeout_apply`. Once apply begins, it reruns its
read-only validations and — when code would commit **and this checkout carries the project-owned
quality wrapper** — resets the index, stages the whole task worktree, and runs the leaf
change-set-scoped quality contract (`--targeted`) over exactly that staged content as the first
apply-time gate, before any code, memory, ledger, contract, or applied-gate **commit**. That index
write is the one mutation that precedes the gate, and it is why the gate can see files the task
created rather than only the ones it edited: every rail of the wrapper reads the index, and closeout
commits with `git add -A`, so anything not in the index was committed unread.

**Quality altitude ladder (260731-EFA-L17).** Leaf-edge checks stay mandatory but are
change-set-scoped: the pre-push tier and the closeout staged gate run the wrapper with
`--targeted` (ruff over changed files, pyright over changed files plus the reverse-import closure,
pytest over the derived test subset, coverage/CRAP over changed production modules, and the
changed-lines coverage floor). The **full** wrapper runs exactly once per master, at the master
integration gate, inside `worktree_integrate` itself, with host-managed RAM and swap by default.
Constrained CI may opt into `orchestration.qualityGate.memoryCapBytes` (systemd MemoryMax scope or
the rlimit fallback). It is
not a leaf gate. `memory_quality_check` is explicitly carved out: it stays a per-leaf closeout
gate. A leaf closeout that tries to skip its required checks — a changed production module with no
derived test subset, a failed targeted run, or a missing wrapper — is refused loudly, never passed
silently.

For Agents Remember itself, acceptance is Dagger-only. The closeout gate invokes the
pinned Ubuntu graph in `mode=targeted` with the contract's recorded code base as the
mandatory `diff-base`; master integration invokes `mode=full` with the recorded super
base. Host pytest and direct wrapper commands are diagnostics, not substitutes and not
handoff evidence. Use `dagger call quality --help` when the public invocation needs to be
inspected; do not reconstruct its arguments from memory.

Every completed strict wrapper invocation atomically replaces the enclosure-owned
`reports/test-results.md` with its full output, including pytest. Passing closeout and integration
payloads expose that path as `reportPath`; a failing gate writes it before refusing and includes the
path in the refusal. The previous completed report remains until the next run completes, so an
interrupted retry cannot erase the last usable evidence. Do not copy these operational reports into
the code or memory worktree: cleanup and abandon remove the enclosure's whole `reports/` directory.

The wrapper runs its cheap deterministic rails before pytest. When pytest itself passed but a
coverage-derived rail refused, a retry may consume the wrapper's content-addressed proof: an
exact tree skips pytest, while a test-only change reruns only the changed test modules after
their old Coverage.py contexts are removed. This is automatic wrapper behavior. Source,
configuration, selected-suite, runtime/environment, or artifact drift forces the ordinary
derived suite, and an inconclusive delta automatically falls back to one full pytest selection.
CI never reuses this local proof. `AR_QUALITY_NO_RETRY=1` forces a fresh diagnostic run.

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

Two refusals guard that staging step, and because they guard it they run exactly where the gate
runs. With the wrapper present, closeout refuses outright, before staging anything, when the code
checkout is **not** a task worktree (git reports the same `--git-dir` and `--git-common-dir`, which
is what the repository's own checkout looks like; a series/master contract records that path), and
when the code worktree has unresolved merge conflicts. A consuming repository carrying **no** wrapper runs
no gate, so neither refusal applies to it: its closeout stages nothing early and reaches the
ordinary commit step's own `git add -A` exactly as it always has. The preview reports that state as
`wrapper-unavailable` rather than passing it off as checked. At leaf altitude the CRAP threshold is
mandatory over the changed production modules; at master altitude it binds over the whole diff.

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

## Server-Side Gate Enforcement (parked fallback)

This block-and-wait gate is the **parked fallback**, not the active path: the active closeout hand-off is
the notify-and-continue `lifecycle_turn_end_notification` above. `lifecycle_gate`, the operator inbox,
and the dashboard GateResponder still exist and still enforce when you **deliberately** raise the durable
gate, but nothing routes toward them automatically (`next_step.py` repoints every gate moment to the
notification). Use the path below only when you need a durable, developer-attributed, mutation-blocking
approval record.

The chat approval hand-off above is the floor. When the lifecycle is connected to
the dashboard and a `closeout-approval` gate is explicitly raised, closeout is **also** enforced
server-side through that durable gate, so a developer can approve from the cockpit and the
mutating tool — not a UI button — is the security boundary.

`closeout-approval` **is** the human commit gate when it is deliberately raised — closeout is the
single commit-of-record for code, memory, and ledger, so there is no separate `commit-approval`
kind. Subordinate orchestrated-series closeouts normally do not raise this gate; they use the
accepted-series authority recorded in the `intent_note`. The dashboard junction uses the
preview/dry-run -> chat report -> `lifecycle_gate` order above.

How it binds:

1. To route approval through the dashboard, raise the closeout junction with the
   durable gate kind, developer-facing ask, and preview packet in one operation:

   ```text
   lifecycle_gate(
     kind="closeout-approval",
     ask={"kind": "decision", "prompt": "<the commit ask>", "options": ["approve", "revise"]},
     packet={ ...preview facts... },
   )
   ```

2. The developer approves (or rejects / requests revision) from the dashboard.
   Only the dashboard writes a **developer-attributed** decision
   (`decidedBy="developer"`); the agent's own `gate_decide` is recorded
   `decidedBy="model"` and never counts as approval.

3. On the developer's resolution reaching the agent, **clear** the ambient block
   with `lifecycle_resume()`, then run `worktree_closeout_apply`. A chat "approved"
   does not propagate itself; the agent always sends the clear. The apply step
   reads the lifecycle's gate and **refuses** unless it is `approved` by the
   developer — an `open`, `rejected`, `revision-requested`, already-`applied`, or
   **model-approved** gate blocks the closeout; on success the tool appends an
   `applied` snapshot so one approval cannot be replayed.

Rules:

1. **Never self-approve a human-pinned gate.** A model-attributed approval is rejected by
   enforcement. Wait for the developer's dashboard decision or chat response when a
   `closeout-approval` gate exists, and never pass your own judgment off as developer approval.
   Delegated-series closeout without a raised gate is different: it records the accepted series
   authority and the owning seat's review in `intent_note`.
2. **Opening a gate is opt-in and deliberate.** Open a `closeout-approval` gate
   **only** when a developer is driving approval from the dashboard. Do **not**
   open one in a pure-chat session with no cockpit watching — an `open` gate blocks
   your own closeout until it is decided.
3. **Gateless lifecycles use the applicable authority.** With no `closeout-approval` gate,
   standalone/final work still needs explicit developer commit approval, while accepted
   orchestrated-series subordinate work may proceed under delegated series authority. Enforcement is
   additive, never a requirement to raise a gate on every closeout.
4. The closeout preview/apply payload carries a `closeout_gate` block
   (`enforced` / `permitted` / `gateId` / `reason`); relay it at the commit-approval
   gate so the developer sees whether a dashboard gate is open, approved, or absent.

## Preconditions

The `c-12-closeout` skill resolves or consumes the current
`c-08-ar-coordination-context-resolver` context. External-memory closeout
requires the code checkout/worktree and memory repo/worktree to be on the same
selected branch; internal-memory closeout commits its memory changes with the
code worktree.

Ledger compatibility is based on code-to-memory commit mappings, not branch
metadata.

Before committing code, run the package-local missing-onboarding check against
current additions:

```text
python -m agents_remember.memory_quality.integrity.check_missing_onboarding --code-repository-root "<code-root>" --onboarding-root "<resolved-onboarding-root>"
```

The check only evaluates files that are new in the current checkout or
worktree, not the whole historical repository. In the manager -> builder ->
reviewer -> curator chain, this check is expected to already pass by the time the owning seat runs
it, because the curator's memory pass created those sidecars through the
`c-05-create-or-update-onboarding-files` skill before this precondition is checked; running the
check here confirms that pass, it is not the trigger to author onboarding from the closing seat. If
it still reports missing onboarding, do not create the sidecars inline — escalate to run (or rerun)
the curator's memory pass, then rerun this check. After the code commit exists, refresh the new
sidecars' verification metadata to that commit during the normal post-code-commit memory refresh.

Changed (already-onboarded) source files have a parallel requirement: their
sidecar content must be updated to approved current state before closeout. The
closeout gate rejects any changed source file whose existing sidecar body was
not modified in the current task, because advancing verification metadata over
stale content defeats the commit-hash-based drift check. In the curator chain, changed sidecars are
updated during the curator's memory pass, not at the metadata-refresh step, and not by the builder
during implementation.

The change set also reads against the resolved memory layer's
`system/coding-guidelines.md` (when present) before the closeout preview. The quality wrapper
certifies lint, types, tests, coverage, and CRAP; it does not read for guideline adherence — a
task identifier in a shipped comment, a new positional boolean flag, an `object`-typed boundary
parameter, or an already-oversize file growing again all pass every rail. Read the change set's
added lines against the guideline file- and function-size budgets, the responsibility and
anti-pattern rules, the source-comment scope, the typed-boundary (DTO) rules, and the D1/D2/D3
stability doctrine; repair what falls inside the task's scope and relay everything else as named
findings at the commit-approval gate. A guideline contradiction that lands unmentioned is a
closeout failure, and in the manager -> builder -> reviewer -> curator chain this read is part of
the reviewer seat's evidence, not something to patch silently at closeout time.

The closeout worklist covers the working tree plus the leaf contract-recorded
committed range: every path changed between the last verified commit (the
contract's recorded closeout commit, falling back to the task base) and the
work branch HEAD, scoped by the recorded base so synced-in parallel work and
previously closed-out slices never re-gate. Already-onboarded artifacts —
sidecars, route overviews, entity fingerprints — gate on every transported
change regardless of who authored it, merge requests included. Committed-range
paths without onboarding are reported as `unonboarded` (count plus capped
sample) and never block; never-onboarded files are not blanket-onboarded at
closeout. Relay the `unonboarded` count and sample to the developer at the
commit-approval gate so important transported files can be onboarded
deliberately through the `c-05-create-or-update-onboarding-files` skill.

## External-Memory Order

External-memory closeout order is:

1. run `check_missing_onboarding` against current additions (in the curator chain, this confirms the
   curator's pass already covered them — it is not the cue to author onboarding here)
2. if onboarding is still missing, escalate to run/rerun the curator's memory pass through the
   `c-05-create-or-update-onboarding-files` skill before committing code (solo flat sessions with no
   separate curator seat create it directly)
3. after preview and the applicable commit authority are complete, call
   `worktree_closeout_apply`; its initial checks are read-only
4. the citation gate runs BEFORE the leaf targeted quality contract and the code commit: `range_resolution` and
   `claim_reopen` over the working tree — a changed construct whose citation is current is only
   the report-only review surface, while a stale pointer, an absent or ambiguous anchor, or
   unverifiable provenance refuses in seconds. The curator clears the same
   `memory_quality_check` during the leaf, so findings here are the exception, not the rule
5. when code would commit and the checkout carries the wrapper, reset the index, stage the whole
   task worktree, and run the leaf change-set-scoped quality contract (`--targeted`) over exactly
   that staged content, before any commit; a refusal leaves the worktree staged and commits
   nothing, and the next run's reset means it starts from the working tree either way; mandatory
   CRAP enforcement fails every score at or above the configured threshold over the changed
   production modules. The full wrapper is NOT a leaf gate — it runs once per master at the master
   integration gate. A checkout with no wrapper runs no gate and commits as it always has.
6. commit code changes and capture `C2` plus its commit date
7. run the `c-02-memory-quality-control` skill's drift check against `C2` to produce the full memory update worklist
8. verify each changed source file's sidecar content was updated in this task (by the curator's pass
   in the chain above), then refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`; a changed source file with an unmodified sidecar body fails the closeout instead of receiving a metadata-only refresh
9. refresh affected repo entity catalog `git-blob-set-v1` fingerprints against `C2` when changed source paths are listed as entity evidence
10. refresh affected route overview `lastVerifiedCommitHash` / `lastVerifiedCommitDate` metadata to `C2`
11. refresh generated route indexes so `overview.index.json` matches the updated onboarding tree
12. run MCP `memory_quality_check` (the post-refresh sanity phase: drift, document shape, history order); fix reported memory findings before continuing
13. commit memory-content changes and capture `M2`
14. prepend `C2 | M2` to `memory.md`
15. commit the ledger update as `L2`
16. update the task contract closeout state

## Internal-Memory Order

Internal-memory closeout order is:

1. run the same missing-onboarding and changed-sidecar preconditions before preview
2. complete preview and the applicable explicit or delegated commit authority
3. call `worktree_closeout_apply`; its initial validations are read-only
4. when code would commit and the checkout carries the wrapper, reset the index, stage the whole
   task worktree, and run the leaf change-set-scoped quality contract (`--targeted`) over exactly
   that staged content, before any commit — a refusal leaves the worktree staged and commits
   nothing, and the next run's reset restages from the working tree regardless — including
   mandatory failure for every CRAP score at or above the configured threshold over the changed
   production modules. A checkout with no wrapper runs no gate and commits as it always has.
5. commit the code and internal-memory changes together
6. update the task contract closeout state

Entity fingerprints must be refreshed after the code commit and before the
memory-content commit because `git-blob-set-v1` uses `HEAD:<path>` Git blobs.
Reviewing the entity prose can happen before closeout, but the final
fingerprint values must be written in the code-commit-to-memory-commit window.

Route overview metadata and generated route indexes are memory-content changes.
They must be refreshed before `memory_quality_check`, and `memory_quality_check`
must be clean before creating the memory content commit.

Push behavior is not automatic. Closeout commits code, memory, and ledger only;
it never pushes. Pushing the integration branch is part of the landing tail the
`c-09-git-worktree-manager` skill owns: call
`lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then present the push intent as
your final prose, and **STOP**; push only after the developer approves and
your next turn auto-resumes. (Parked fallback: the durable
`lifecycle_gate(kind="push-approval", ...)` + `lifecycle_resume` still works if deliberately raised.)

Closeout does not mark the task `Completed`. After closeout, integration, any
PR-gated merge/pull, and memory carryover are done, use
`lifecycle_finalize_task` from the `c-09-git-worktree-manager` skill to prove the
landed parent-child branch edge, run or verify cleanup, and update the current
task plus immediate parent row.

## Failure Conditions

Closeout fails without mutation when required onboarding is missing,
verification metadata is missing, external memory is not resolved, the code and
memory checkouts are on different selected branches, or no code or memory
changes exist.

For an Agents Remember code commit, closeout also fails without any commit when
the leaf change-set-scoped quality contract cannot run or exits non-zero. This
includes any CRAP score at or above the configured threshold over the changed
production modules, and a changed production module with no derived test subset.
It is "without any
commit" rather than "without mutation": closeout resets the index and stages the
whole task worktree before the gate so the gate can see created files, and
**leaves it staged** when the gate refuses. Nothing needs undoing — the next run
resets and restages from the working tree, so it reaches the index a first run
would have reached, and `commit_if_dirty` stages everything regardless. Fix the
reported source, test, coverage, or environment issue, rerun the strict wrapper
and closeout preview, and only then retry apply; never bypass the failure with a
direct commit.

The next two refusals are preconditions of that staging step, so they run where
the gate runs — that is, when code would commit and this checkout carries the
wrapper at `mcp/src/agents_remember/code_quality/check.py`. They are not
closeout-wide preconditions: a consuming repository that carries no wrapper runs
no gate, is not staged early, and reaches the ordinary commit step's own
`git add -A` exactly as it did before the gate existed. The preview reports that
as `wrapper-unavailable`.

Where the gate runs, closeout refuses before staging anything when the code
checkout is not a task worktree. The test is git's own: in a linked worktree
`--git-dir` and `--git-common-dir` differ, and in a repository's own checkout
they are the same path. `default_series_contract` records `code_worktree` as the
repository path itself, so a series/master contract reaching
`worktree_closeout_apply` would otherwise stage in a checkout a person works in —
overwriting a partial `git add -p` selection, staging files deliberately held
back, and resolving any merge in progress to whatever is on disk. Close out the
leaf contract whose `code_worktree` is the task worktree instead.

Where the gate runs, closeout also refuses before staging anything when the code
worktree has unresolved merge conflicts (an in-progress merge, rebase,
cherry-pick, or revert with unmerged index entries). The refusal names the
conflicted paths. This is a deliberate refusal, not an incidental one:
`git add -A` over an unmerged index resolves every conflict to whatever the
working tree holds, so without this check closeout committed the `<<<<<<<`
markers. Both refusals run before the reset as well as before the add — a
`git reset` drops the unmerged entries and `MERGE_HEAD`, so running it first
would erase the very state the conflict check reads. Resolve the conflicts, stage
the resolutions, then rerun closeout.

Closeout also fails without mutation when a changed source file's existing
sidecar body was not updated in the current task, so verification metadata is
never advanced over stale onboarding content. This applies to committed-range
paths exactly as to working-tree paths — who authored the change does not
matter. Committed-range paths without existing onboarding are the one
exception: they do not fail closeout and are surfaced as `unonboarded` in the
preview and apply payloads for the commit-approval relay.

Worktree closeout also fails when the recorded code or external-memory source
branch moved since task start.

Missing onboarding is the expected hard failure when the required onboarding file was not produced —
in the manager -> builder -> reviewer -> curator chain that means the curator's memory pass did not
cover it. The next step is to run (or rerun) the curator's `c-05-create-or-update-onboarding-files`
pass for that source file, then rerun the closeout preview; a solo flat session with no separate
curator seat runs that skill itself.

## Boundaries

1. The `c-12-closeout` skill owns closeout approval and code-memory-ledger commit sequencing.
2. The `c-12-closeout` skill does not create worktrees, integrate worktrees, finalize lifecycles, or clean up worktrees.
3. The `c-12-closeout` skill does not initialize memory roots; use the `c-00-initialize-memory-repo` skill.
4. The `c-12-closeout` skill must not commit without the applicable authority after a closeout
   preview: explicit developer commit approval for standalone/final work, or recorded delegated
   series authority for subordinate accepted-series work.
5. The `c-12-closeout` skill must not create an Agents Remember code commit until the leaf
   change-set-scoped quality contract (`--targeted`), including mandatory CRAP enforcement over
   the changed production modules, passes in the current worktree. The full wrapper belongs to the
   master integration gate only.
6. The `c-12-closeout` skill must not defer or skip `memory_quality_check`; it stays a per-leaf
   closeout gate even though the full quality wrapper moved to the master integration gate.
7. The `c-12-closeout` skill must not create a memory content commit whose affected onboarding metadata still points at pre-closeout code.
8. The `c-12-closeout` skill must not create a memory content commit before route overview metadata, generated route indexes, and `memory_quality_check` are clean for the new code commit.
9. The `c-12-closeout` skill must not push automatically.
10. The `c-12-closeout` skill must not advance `lastVerifiedCommitHash` / `lastVerifiedCommitDate` for a changed source file whose sidecar content was not updated in the current task; a metadata-only refresh that masks drift is prohibited.
11. The `c-12-closeout` skill must not close out a change set that contradicts the memory layer's `system/coding-guidelines.md` without the contradiction being repaired in scope or named at the commit-approval relay; the wrapper's green rails are not evidence of guideline adherence.
