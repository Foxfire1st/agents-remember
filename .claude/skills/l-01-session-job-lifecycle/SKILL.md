---
name: l-01-session-job-lifecycle
description: "The session job lifecycle the coordinator routes every session into: request -> context/trust checkpoint -> developer-agreed reframe -> deeper research -> decide build mode -> build -> close. Owns research-only exits, the build-mode decision (chat build vs durable w-02-light-task-workflow task), and the job lens (bug/feature/triage/research). Supersedes the retired chat workflow by migrating and modernizing its doctrine."
---

# l-01-session-job-lifecycle Session Job Lifecycle

The `l-01-session-job-lifecycle` skill is the **canvas** the coordinator routes into at the start of every session.
It is not a task format. A task format is one outcome of a single step inside it.

The lifecycle is one shared spine with thin per-job lenses. Its front half is a
developer/model collaboration loop: the developer states the request, the model
runs mandatory MCP grounding checks, the model presents an evidence-backed
reframe, and the developer agrees or revises that reframe before deeper research
or build-mode decisions proceed.

## Companion Files

1. `job-variants.md` — the four thin job lenses (bug / feature / triage / research).
2. `deep-research-report-template.md` — the reusable report and evidence-ledger shape for deeper research.

```
0 Request -> 1 Trust Checkpoint -> 2 Reframe + Research -> 3 Decide -> 4 Build -> 5 Close
                                                   |
                                                   +-- research-only (e.g. investigation, code questions...)
```

## Hand-off Protocol — Dry-run action => "Notify-and-stop" Action => "Report" Action

Every developer hand-off / gate moment — reframe agreement, plan gate, worktree intent, commit
approval, push approval, integration, cleanup/finalization, regular turn-end — is **notify-and-continue**:
surface an attention item, present the result, and **end your turn**; the developer responds and your
**next** turn picks up automatically. It is **three actions, never one**:

1. **Dry-run action.** Run applicable MCP tools in `dry-run` for the hand-off. If dry run succeeds,
   proceed with the notify-and-stop action. If dry run fails, try to ammend the issue first before reporting failure.
2. **Notify-and-stop action.** Call `lifecycle_turn_end_notification(summary=…)`. It sets the new
   **`awaiting-developer`** lifecycle state, surfaces a dashboard attention item, and **returns
   immediately — no wait, no inbox.** This is the **last tool call of the turn** — the report prose
   still follows it; do not STOP here. The developer responds on the
   dashboard or in the leaf's attached chat, and the **first AR tool call of your next turn**
   automatically resumes the lifecycle (`running`) and clears the attention item — you send **no**
   explicit resume.
3. **Report action.** Deliver the complete hand-off report as plain assistant output
   (the reframe, the plan, the intent packet, or the closeout relay with
   preview facts, quality results, proposed commit messages, and
   attestations), and make the decision you are handing to the developer the last
   line of prose. **The report is the last thing in the turn — then STOP / end your turn.**

Use the `dry-run` step to verify that the planned tool call (for example the closeout apply) will
succeed before you hand off, and self-fix any failure before reporting it.

`lifecycle_turn_end_notification` returns immediately and does **not** render a prompt over your prose,
so the report you deliver after it is exactly what the developer sees — call the
notification, then deliver the report as your final prose, then stop. Keep the report turn free of any mutating or permission-triggering operation so
nothing obscures the report.

Junction → Dry-run → notify (`lifecycle_turn_end_notification`) → Report. Every junction below now hands
off through that one notification; the named `kind` is the **parked** durable-gate label the fallback
would use (see "Parked fallback" below), kept so the dashboard can still classify each hand-off:

| Junction                         | Parked durable gate `kind` | Skill that hands off                                       |
| -------------------------------- | -------------------------- | ---------------------------------------------------------- |
| plan gate                        | `plan-approval`            | `l-01-session-job-lifecycle`                               |
| worktree intent                  | `worktree-intent`          | `c-09-git-worktree-manager`                                |
| commit / closeout                | `closeout-approval`        | `c-12-closeout`                                            |
| push                             | `push-approval`            | `l-01-session-job-lifecycle` / `c-09-git-worktree-manager` |
| integration                      | `integration-approval`     | `c-09-git-worktree-manager` / `c-12-closeout`              |
| cleanup / lifecycle finalization | `cleanup-approval`         | `c-09-git-worktree-manager` / `c-12-closeout`              |
| any other dev-wait               | `agent-question`           | `l-01-session-job-lifecycle`                               |

`closeout-approval` **is** the commit hand-off — there is no separate
`commit-approval`. Closeout is the single commit-of-record for code, memory, and
ledger, so every commit (including a singular one) routes through the closeout
hand-off. `agent-question` is the non-exhaustive catch-all so any dev-wait that is
not a structured junction is still observable.

### Parked fallback — block-and-wait `lifecycle_gate`

The block-and-wait `lifecycle_gate(kind=…)` junction (which creates a durable gate and waits for a
developer decision), the operator inbox, and the dashboard GateResponder **still exist and still work**
when explicitly raised — but they are **no longer the active path**, and nothing routes toward them:
`next_step.py` repoints every gate moment to `lifecycle_turn_end_notification`. Reach for the
server-enforced `lifecycle_gate` (followed by `lifecycle_resume` after the developer decides) only when
you deliberately need a durable, developer-attributed, mutation-blocking approval record; its `kind`
values are the table above. If you ever do raise it, it renders an approval prompt **over** your prose,
so the report must land in an earlier turn — which is exactly why notify-and-stop is preferred.

## Lifecycle Signals — Making The Session Observable

Every session in a managed repo is a **lifecycle**: the six `lifecycle_*` signals
record where it is and what it is waiting on, so the work is observable and
resumable across chat deaths (design `docs/design/observable-lifecycle.md`). The
model **never handles a lifecycle id** — identity is server-side, anchored in the
worktree contract.

| When (phase)                             | Signal                                                      | Why                                                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Trust Checkpoint passes (managed repo)   | `lifecycle_start`                                           | Begin a **fleeting** lifecycle (guarded: one per session; takes no id).                                                                                                                                                                                                                                          |
| Entering each phase                      | `lifecycle_phase`                                           | Move the orthogonal phase axis (`request`/`trust-checkpoint`/`reframe-research`/`decide`/`build`/`close`).                                                                                                                                                                                                       |
| At a hand-off (reframe, plan, commit, …) | `lifecycle_turn_end_notification(summary=…)` (last tool call), then the report prose, then **STOP** | Set `awaiting-developer`, surface the dashboard attention item, and return immediately (no wait, no inbox); the next turn's first AR tool call auto-resumes (`running`) and clears the item. Block-and-wait `lifecycle_gate(kind=<junction-kind>, ask=…, packet=…)` + `lifecycle_resume` is the parked fallback. |
| `worktree_start` (Decide → Build)        | _(promotion — automatic)_                                   | The fleeting lifecycle becomes **persistent**, anchored in the contract; no separate signal.                                                                                                                                                                                                                     |
| Resuming an existing task                | `worktree_attach`                                           | Re-adopts the contract's lifecycle (contract-resolved); the model passes no id.                                                                                                                                                                                                                                  |
| Leaving unsaved fleeting work            | `switch_lifecycle` (`on_unsaved=save`\|`discard`)           | The save gate: promote it or abandon it — never dropped silently.                                                                                                                                                                                                                                                |
| Close                                    | `lifecycle_end` (`completed`\|`abandoned`)                  | The terminal record.                                                                                                                                                                                                                                                                                             |

Rules: `lifecycle_start` is guarded (one active lifecycle, no id). `paused` is
**system-owned** — there is no pause signal. `awaiting-developer` is set by
`lifecycle_turn_end_notification` and auto-cleared back to `running` by the next
turn's first AR tool call — you never resume it by hand. A tool call outside any
lifecycle is **dropped, never misattributed**. `worktree_start` **promotes** the current
lifecycle (fleeting → persistent); `worktree_attach` **resumes** the contract's
lifecycle; both keep identity server-side.

## 0 — Request

Receive the developer's raw request and identify the active repository.

1. Treat the developer's statement as raw input, not yet as an implementation plan.
2. Infer the target code repository from the request and local context. Ask the
   developer if the target is unclear.

Request intake changes nothing. It only establishes which repository the next
checkpoint must inspect.

The upcoming `Trust Checkpoint` reveals whether or not the request is related to repositories managed by Agents Remember. If not, then the lifecycle exits early and the agent can work as usual.

However, if later work is to enter the boundary of repositories being managed by Agents Remember,
the lifecycle must be re-entered.

---

## 1 — Trust Checkpoint

Establish whether memory and providers are trustworthy enough to use.

1. For the target repository, resolve coordination/memory context with
   the MCP tool call:

   ```text
   context_packet(repo_id="<repo-id>", include_providers=true, include_drift=true, include_freshness=true)
   ```

2. Report the packet facts before relying on memory or providers:
   - repository, branch, and dirty state
   - memory root and onboarding root
   - provider state
   - drift status and actionable drift count
   - branch freshness: whether the code and memory checkouts are current with
     their upstreams (`behind`/`diverged` means the local official line is
     stale — fast-forward it before trusting analysis or basing work on it)
     and whether the ledger maps code HEAD (`ledgerMapsCodeHead=false` means
     the memory checkout does not match the code state; run carryover or
     check out the right memory branch first)
3. If onboarding for committed source is drifted, missing verification, or
   orphaned, and the corresponding source file is not dirty in the code
   worktree, stop and ask the developer whether or not to refresh it through
   `c-05-create-or-update-onboarding-files`. Drift handling is approval-gated!
4. If drift is tied to dirty source, report it as active work-in-progress. Do
   not adopt it as maintenance or silently trust it as current state unless the
   developer explicitly says this job owns it.
5. If providers are stopped or degraded, use the matching MCP provider/runtime
   operations, then re-run the provider check. If providers are ready, report
   readiness and continue. If issues persist, report it to the developer and
   wait for instructions. If the packet's providers summary lists `indexing`
   targets, report them to the developer: those providers are healthy but
   busy, and their results may be partial until the scan completes.
6. After the trust checkpoint passes, read committed-state onboarding for the
   in-scope anchors as needed. A file dirty in another chat is still valid for
   HEAD and worth comparing, but its dirty-source drift remains active work.

---

## 2 — Reframe And Research

Turn the developer's raw request into an agreed piece of work, then perform the
deeper research that the agreed frame requires. The `tasks/AGENTS.md`
collaboration doctrine applies here in plain chat, before any task file or task
format exists.

**Read tool for this phase.** Until the build-mode decision (Phase 3), read managed-repo source
through the `read_ar_files` MCP tool, not the harness's native read — it returns paired
source+onboarding plus the repository/route overviews in one observable, lifecycle-attributed
call, and you keep a running count of those calls as evidence. Native read is the edit
precondition in Phase 4.

1. **Gather evidence for the reframe** through reading the
   `c-04-retrieval-strategy-router` skill. Pick the strategy by the question:
   - _Semantics_ (grepai over onboarding) — "where does X live / what handles Y."
   - _Relationship_ (cgc) — callers/callees/dependencies/impact.
   - _Intent_ (onboarding + bounded source confirmation) — hidden contracts, invariants,
     branch-valid truths, behavioral expectations. This is a workflow of paired
     source+onboarding reads via the `read_ar_files` MCP tool: one call pairs each source
     file with its verified onboarding and auto-attaches the repository overview + governing
     route-overview chain (the bird's-eye view).
2. **Reframe** the request through `tasks/AGENTS.md`: distinguish the surface
   request, deeper objective, highest-leverage framing, assumptions, boundaries,
   invariants, and truth gaps. Do not rush a statement into a plan.
3. Present the reframe to the developer. If the developer disagrees, discuss and
   revise the reframe. If the developer agrees, proceed to deeper research.
4. **Perform deeper research** for the agreed frame. This research still uses
   `c-04-retrieval-strategy-router`, but it is now scoped by the developer-agreed
   frame rather than by the model's first guess. Use
   `deep-research-report-template.md` for the report shape; the lifecycle owns
   the required proof categories, while the template owns evidence formatting.
5. The deeper research report must list its proof and tie evidence to the claim it supports:
   - `read_ar_files` calls (paired source+onboarding reads — the running count)
   - onboarding docs read
   - semantic queries performed
   - code graph queries performed
   - source files inspected
   - remaining truth gaps
6. Run the **job opening move** for the job lens (see `job-variants.md`) and use
   the deeper research to name the truth gaps that remain.
7. Continue until the developer agrees the design is defined well enough to write
   down, then produce the **plan**: the steps, and a **code example for every
   distinct change** you intend to make.

**Plan gate:** **hand off** before changing any code. No implementation begins
before the developer approves. Call
`lifecycle_turn_end_notification(summary={…the plan + the developer-facing approval ask…})` as the
**last tool call**, then present the plan as your final prose and **STOP**;
the developer approves on the dashboard or in chat and your next turn auto-resumes to begin
implementation. (Parked fallback: the server-enforced
`lifecycle_gate(kind="plan-approval", ask=…, packet=…)` + `lifecycle_resume` still exist if you
deliberately need a mutation-blocking approval record.)

---

## 3 — Decide (build mode)

One decision: does this job change code?

- **No -> research-only exit.** Deliver the answer/assessment. No worktree, no task artifact, no closeout.
  A research-only job may recommend or spawn a follow-up build job; it does not perform one itself.
- **Yes -> worktree intent hand-off, then always a worktree.** Read `c-09-git-worktree-manager`
  and `system/git-workflow.md`, call
  `lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then present the worktree
  intent packet as your final prose and **STOP**. The developer approves and your next turn
  auto-resumes to `worktree_start`. Then open it with
  `c-09-git-worktree-manager` and pick the build mode:
  - **Chat build** — small enough to carry inline this session: worktree-backed, **no** `task.md`.
  - **Durable task build** — hand off to `w-02-light-task-workflow`: `task.md`, checklist, decision
    log, proposed code examples. Escalate to a master + light sub-task series when the work outgrows a
    single-page plan.

The worktree intent packet names the target repo, build mode, discovered branch policy, proposed
`source_branch`, proposed work branch/worktree name, memory mode, landing path, and material risks.
On PR-gated repos, the packet must prove that the recorded `source_branch` is pushable and that
protected targets are reached later through the repo's PR flow.

Worktree granularity = the leaf task unit. A single task gets its own leaf enclosure/worktree. A
master multi-task owns a root `series-contract.md` and integration branch, while each active leaf
sub-task gets its own `enclosures/<leaf-id>/series-contract.md`, branch, and worktree. A chat build
gets its own leaf worktree without a task artifact. The git-landing decision (direct vs PR-gated) is
deferred to the repo's `system/git-workflow.md` — read it before landing on a gated branch.

---

## 4 — Build

Implement inside the worktree, keeping memory and tests in lockstep with the code.

1. Apply the approved code changes.
2. **Refresh the matching onboarding in the same editing pass**, per completed plan-section — never
   deferred to the end of the job. When a change affects durable current-state knowledge, the sidecar
   is updated alongside it through `c-05-create-or-update-onboarding-files`.
   - For **changed** (already-onboarded) source files, update the sidecar **body** now: the closeout
     gate rejects a changed source file whose existing sidecar was not modified this job, because
     refreshing `lastVerifiedCommitHash` over stale content silently defeats the drift check.
   - For **new** source files, run `check_missing_onboarding` before the commit and create the
     reported missing sidecars through the `c-05-create-or-update-onboarding-files` skill; the post-code-commit memory refresh stamps them with
     the real code commit hash and date.
3. Run the checks from the `c-08-ar-coordination-context-resolver` resolved `system/tools.md` (lint, typecheck, complexity, tests) and
   get them **green before each incremental commit**. Testing is never deferred to a final task; the
   pre-commit/pre-push hooks enforce it.
4. **Watch the official line and sync early.** `worktree_status`'s `freshness` block reports when the
   recorded base pair fell behind the local source branch tips (a parallel cycle landed). Pull the
   moved official line in with `worktree_sync` — preferably **before memories are written**: with
   parked memory the sync is a pure fast-forward, the other cycle's sidecars and ledger rows end up
   beneath this task's future work, and end-of-series integration stays `ff-only` with no carryover
   reconciliation.

Incremental, pushable commits keep the work-loss window small. Each closeout below is one such commit.

---

## 5 — Close

Land the work. **Implementation approval is not commit approval.**

1. Run the `c-09-git-worktree-manager` closeout **preview** for the worktree (`worktree_closeout_preview`).
   Every build closes through its worktree — there is no direct-checkout closeout.
   Relay the proposed code, memory, and ledger commit messages.
2. **Commit gate:** run the closeout **preview** (dry-run), then **hand off** before any real commit or closeout
   apply. If required onboarding is missing, run the `c-05-create-or-update-onboarding-files` skill for the affected file and re-run the preview.
   Call `lifecycle_turn_end_notification(summary={…the preview facts + the commit ask…})` as the
   **last tool call**, then deliver the preview facts as your final prose and **STOP**;
   the developer approves on the dashboard or in chat and your next turn auto-resumes to
   `worktree_closeout_apply`. The `c-12-closeout` skill owns this hand-off. Parked fallback: when you
   deliberately raise the durable `closeout-approval` `lifecycle_gate`, it is enforced **server-side** —
   `worktree_closeout_apply` refuses unless it is approved by the developer, and an agent self-approval
   never satisfies it.
3. On approval, the `c-09-git-worktree-manager` skill owns the external-memory invariant in order: commit code → refresh affected
   onboarding metadata to the new code commit → run memory quality control → commit memory content →
   update and commit the ledger.
4. **Integrate + land** per `c-09-git-worktree-manager` and `system/git-workflow.md`: integrate the
   worktree branch into the approved source/integration branch, then on a PR-gated repo push that
   source branch, open the PR, wait for green checks, and merge per the repo convention. Never push a
   protected branch directly. The agent does not push on its own authority — call
   `lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then present the push intent
   as your final prose, and **STOP**; push only after the developer
   approves and your next turn auto-resumes. The `c-09-git-worktree-manager` skill makes the matching
   integration and cleanup/finalization hand-offs at its landing tail (parked durable kinds
   `integration-approval` / `cleanup-approval`).
5. **Map the ledger to the landed commit.** A PR merge usually lands a **merge commit** on top of the
   work — tree-identical to the verified tip but a new SHA the ledger does not yet map. Ensure the
   ledger maps that merge commit so the next worktree can base off the merged branch without a manual
   reconciliation. `system/git-workflow.md` owns this step.
6. **Finalize the lifecycle:** run `lifecycle_finalize_task(..., dry_run=true)` once the local parent
   branch contains the landed commit and memory carryover is done. Call
   `lifecycle_turn_end_notification(summary=…)` as the **last tool call**, then relay its landed-commit
   proof, cleanup plan, and task-document updates as your final prose, and
   **STOP**. The developer approves cleanup/finalization and your next turn auto-resumes to run the real
   finalizer. The tool proves one parent-child branch edge,
   reclaims the worktrees, and marks the leaf task plus immediate parent row `Completed` when those
   task-doc paths are supplied. It does not recursively complete ancestors; repeat the edge at the next
   parent level when that parent task lands.
7. **Keep squash out of the normal path.** A PR-gated edge is structurally the same as a direct edge
   after the model finishes the PR merge and pulls the target branch locally. Do not use squash-merge
   equivalence as a default finalization proof; squash is an emergency/manual recovery path because it
   erases commit lineage and can make memory lookup history wrong.

A research-only exit skips this phase entirely.

## Relationship To Other Instructions

This skill extends the coordinator `AGENTS.md` and the repository memory layer; it does not replace
them. Read `job-variants.md` for the per-job lenses, and `deep-research-report-template.md` for the
deeper research report shape.
