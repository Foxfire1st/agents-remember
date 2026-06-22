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

## Gate Protocol — Report Turn, Then Action Turn

Every lifecycle gate — reframe agreement, plan gate, worktree intent, commit
approval, push approval, integration, cleanup — is **two turns, never one**:

1. **Report turn.** Deliver the complete gate report as plain assistant output
   (the reframe, the plan, the intent packet, or the closeout relay with
   preview facts, quality results, proposed commit messages, and
   attestations), and end the turn with the approval question as the last
   line of prose.
2. **Action turn.** Invoke the gated tool only after the developer replies.

The report turn must not contain anything that can raise an approval mechanism
over the text: no structured question widget, no mutating tool call, no
permission-triggering operation. Harnesses render approval prompts over or
instead of same-turn prose, so a report attached to its own approval prompt is
a report the developer never sees.

## Lifecycle Signals — Making The Session Observable

Every session in a managed repo is a **lifecycle**: the six `lifecycle_*` signals
record where it is and what it is waiting on, so the work is observable and
resumable across chat deaths (design `docs/design/observable-lifecycle.md`). The
model **never handles a lifecycle id** — identity is server-side, anchored in the
worktree contract.

| When (phase) | Signal | Why |
| --- | --- | --- |
| Trust Checkpoint passes (managed repo) | `lifecycle_start` | Begin a **fleeting** lifecycle (guarded: one per session; takes no id). |
| Entering each phase | `lifecycle_phase` | Move the orthogonal phase axis (`request`/`trust-checkpoint`/`reframe-research`/`decide`/`build`/`close`). |
| At a gate (reframe, plan, commit, …) | `lifecycle_block` (+ optional ask) then `lifecycle_resume` | Record the wait on the developer and its resolution. |
| `worktree_start` (Decide → Build) | *(promotion — automatic)* | The fleeting lifecycle becomes **persistent**, anchored in the contract; no separate signal. |
| Resuming an existing task | `worktree_attach` | Re-adopts the contract's lifecycle (contract-resolved); the model passes no id. |
| Leaving unsaved fleeting work | `switch_lifecycle` (`on_unsaved=save`\|`discard`) | The save gate: promote it or abandon it — never dropped silently. |
| Close | `lifecycle_end` (`completed`\|`abandoned`) | The terminal record. |

Rules: `lifecycle_start` is guarded (one active lifecycle, no id). `paused` is
**system-owned** — there is no pause signal. A tool call outside any lifecycle is
**dropped, never misattributed**. `worktree_start` **promotes** the current
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

**Plan gate:** stop and wait for explicit developer approval before changing any code. No
implementation begins before this approval.

---

## 3 — Decide (build mode)

One decision: does this job change code?

- **No -> research-only exit.** Deliver the answer/assessment. No worktree, no task artifact, no closeout.
  A research-only job may recommend or spawn a follow-up build job; it does not perform one itself.
- **Yes -> worktree intent gate, then always a worktree.** Read `c-09-git-worktree-manager`
  and `system/git-workflow.md`, present the worktree intent packet, and wait for
  explicit developer approval before `worktree_start`. Then open it with
  `c-09-git-worktree-manager` and pick the build mode:
  - **Chat build** — small enough to carry inline this session: worktree-backed, **no** `task.md`.
  - **Durable task build** — hand off to `w-02-light-task-workflow`: `task.md`, checklist, decision
    log, proposed code examples. Escalate to a master + light sub-task series when the work outgrows a
    single-page plan.

The worktree intent packet names the target repo, build mode, discovered branch policy, proposed
`source_branch`, proposed work branch/worktree name, memory mode, landing path, and material risks.
On PR-gated repos, the packet must prove that the recorded `source_branch` is pushable and that
protected targets are reached later through the repo's PR flow.

Worktree granularity = the task unit: a single task gets its own worktree; a master multi-task gets
**one** worktree for the whole series (never one per sub-task); a chat build gets its own worktree
without a task artifact. The git-landing decision (direct vs PR-gated) is deferred to the repo's
`system/git-workflow.md` — read it before landing on a gated branch.

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
2. **Commit gate:** stop for explicit developer commit approval before any real commit or closeout
   apply. If required onboarding is missing, run the `c-05-create-or-update-onboarding-files` skill for the affected file and re-run the preview.
   When the lifecycle is dashboard-connected, this gate is also enforced **server-side**: a durable
   developer-attributed `closeout-approval` gate is the un-forgeable record of approval, and
   `worktree_closeout_apply` refuses unless it is approved by the developer — an agent self-approval
   never satisfies it. The `c-12-closeout` skill owns the opt-in gate choreography (`gate_create` /
   `gate_wait`); gateless lifecycles keep this chat commit gate unchanged.
3. On approval, the `c-09-git-worktree-manager` skill owns the external-memory invariant in order: commit code → refresh affected
   onboarding metadata to the new code commit → run memory quality control → commit memory content →
   update and commit the ledger.
4. **Integrate + land** per `c-09-git-worktree-manager` and `system/git-workflow.md`: integrate the
   worktree branch into the approved source/integration branch, then on a PR-gated repo push that
   source branch, open the PR, wait for green checks, and merge per the repo convention. Never push a
   protected branch directly. The agent does not push on its own authority.
5. **Cleanup + carryover:** reclaim the worktree/provider stack and bring the parked memory home.
   When the worktree memory branch diverged or the code PR squash-merged, use
   `c-11-memory-carryover-from-branch`; when it is a clean linear descendant of main-memory, a
   fast-forward + push is enough.
6. **Map the ledger to the landed commit.** A PR merge usually lands a **merge commit** on top of the
   work — tree-identical to the verified tip but a new SHA the ledger does not yet map. Ensure the
   ledger maps that merge commit so the next worktree can base off the merged branch without a manual
   reconciliation. `system/git-workflow.md` owns this step.

A research-only exit skips this phase entirely.

## Relationship To Other Instructions

This skill extends the coordinator `AGENTS.md` and the repository memory layer; it does not replace
them. Read `job-variants.md` for the per-job lenses, and `deep-research-report-template.md` for the
deeper research report shape.
