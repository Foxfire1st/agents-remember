---
name: c-12-closeout
description: "Close out approved Agents Remember edits by preserving explicit commit approval, missing-onboarding checks, external-memory onboarding refresh, memory quality, ledger alignment, and no automatic push for worktree-backed tasks."
---

# c-12-closeout Closeout

Use this skill when approved Agents Remember edits need to be committed and the
repository uses external memory.

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

## Approval Hand-off

Closeout is explicitly human-gated. Agents must request the matching preview
tool first, relay the proposed code, memory, and ledger commit messages to the
developer, and ask for explicit commit approval.

Real closeout uses the matching apply tool with an `intent_note`. The note
records the developer's explicit commit approval. Agents must not treat
implementation approval, a previous "looks good", or their own judgment as
commit approval.

The relay follows the `l-01-agent-lifecycles` orchestrator hand-off protocol: run the
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

`closeout-approval` **is** the commit gate — closeout is the single
commit-of-record for code, memory, and ledger, so there is no separate
`commit-approval` kind; every commit routes through this gate. The dashboard
junction uses the preview/dry-run -> chat report -> `lifecycle_gate` order above.

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

1. **Never self-approve.** A model-attributed approval is rejected by
   enforcement. Wait for the developer's dashboard decision or Chat response, and
   never pass your own judgment off as commit approval.
2. **Opening a gate is opt-in and deliberate.** Open a `closeout-approval` gate
   **only** when a developer is driving approval from the dashboard. Do **not**
   open one in a pure-chat session with no cockpit watching — an `open` gate blocks
   your own closeout until it is decided.
3. **Gateless lifecycles are unchanged.** With no `closeout-approval` gate the chat
   commit gate (`intent_note` after an explicit "commit") governs exactly as before;
   enforcement is additive, never a new requirement on every closeout.
4. The closeout preview/apply payload carries a `closeout_gate` block
   (`enforced` / `permitted` / `gateId` / `reason`); relay it at the commit-approval
   gate so the developer sees whether a dashboard gate is open, approved, or absent.

## Preconditions

The `c-12-closeout` skill resolves or consumes the current `c-08-ar-coordination-context-resolver` context, requires external memory
mode, and requires the code checkout/worktree and memory repo/worktree to be on
the same selected branch.

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
3. commit code changes and capture `C2` plus its commit date
4. run the `c-02-memory-quality-control` skill's drift check against `C2` to produce the full memory update worklist
5. verify each changed source file's sidecar content was updated in this task (by the curator's pass
   in the chain above), then refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`; a changed source file with an unmodified sidecar body fails the closeout instead of receiving a metadata-only refresh
6. refresh affected repo entity catalog `git-blob-set-v1` fingerprints against `C2` when changed source paths are listed as entity evidence
7. refresh affected route overview `lastVerifiedCommitHash` / `lastVerifiedCommitDate` metadata to `C2`
8. refresh generated route indexes so `overview.index.json` matches the updated onboarding tree
9. run MCP `memory_quality_check`; fix reported memory findings before continuing
10. commit memory-content changes and capture `M2`
11. prepend `C2 | M2` to `memory.md`
12. commit the ledger update as `L2`
13. update the task contract closeout state

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
4. The `c-12-closeout` skill must not commit without explicit commit approval after a closeout preview.
5. The `c-12-closeout` skill must not create a memory content commit whose affected onboarding metadata still points at pre-closeout code.
6. The `c-12-closeout` skill must not create a memory content commit before route overview metadata, generated route indexes, and `memory_quality_check` are clean for the new code commit.
7. The `c-12-closeout` skill must not push automatically.
8. The `c-12-closeout` skill must not advance `lastVerifiedCommitHash` / `lastVerifiedCommitDate` for a changed source file whose sidecar content was not updated in the current task; a metadata-only refresh that masks drift is prohibited.
