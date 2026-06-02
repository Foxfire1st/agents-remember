# l-01-session-job-lifecycle Lifecycle — The Spine

One shared spine carries every session. The job type (see `job-variants.md`) only tunes the opening
move, the retrieval lean, and the `decide` default — it never adds or removes phases.

```
0 Orient -> 1 Ground -> 2 Frame -> 3 Decide -> 4 Build -> 5 Close
                                        |
                                        +-- read-only exit (no worktree, no closeout)
```

---

## 0 — Orient

Establish the ground truth before reasoning about the work.

1. Resolve coordination/memory context with `c-08-ar-coordination-context-resolver` for the inferred
   code repository (ask the developer if the target is unclear).
2. Pull `context_packet(repo_id=..., include_providers=true)` once. Read two things from it:
   - **onboarding freshness** — whether onboarding has drifted from the code since last verified;
   - **provider readiness** — whether the semantic (grepai) and relationship (cgc) providers are up.
3. If providers are stopped or degraded, surface that now and use the matching provider/runtime
   operations rather than discovering it mid-task.

Orient is a read; it changes nothing.

---

## 1 — Ground

Build a trustworthy picture of current state from committed memory.

1. Run `c-02-memory-quality-control` **once** for the repository as the task-start drift gate. Apply
   its clean-source vs dirty-source classification:
   - Do **not** plan against clean-source **drifted**, **missing-verification**, or **orphaned**
     pre-existing onboarding until those approved update candidates have been refreshed through
     `c-05-create-or-update-onboarding-files`.
   - **Leave dirty-source drift alone** as active work-in-progress unless the developer explicitly
     takes ownership of it in this job.
   - Do not re-trigger this gate later just because this job goes on to create or modify files.
2. Read **committed-state** onboarding for the in-scope anchors. A file that is dirty in another chat
   is still valid for HEAD and *more* worth comparing — read it; do not treat its drift as a
   maintenance target. (Dirty ≠ ignore.)

---

## 2 — Frame

Turn a developer statement into a defined piece of work. The `tasks/AGENTS.md` collaboration doctrine
applies here, in plain chat, before any task file or format exists.

1. **Reframe** the request: find the true scope, surface what could break, expose hidden variables
   through back-and-forth. Do not rush a statement into a plan.
2. **Pull the evidence** the reframe needs through `c-04-retrieval-strategy-router`. Pick the strategy
   by the question:
   - *Semantics* (grepai over onboarding) — "where does X live / what handles Y."
   - *Relationship* (cgc) — callers/callees/dependencies/impact.
   - *Intent* (onboarding + bounded source confirmation) — hidden contracts, invariants,
     branch-valid truths, behavioral expectations. This is the modernized form of the retired chat
     workflow's paired source+onboarding read: read the source file together with its verified onboarding, and
     when this job already changed that pair after the gate, read the current working versions and
     treat them as pending verification.
3. Run the **job opening move** for the job lens (see `job-variants.md`) and name the **truth gaps**
   that remain.
4. Continue until the developer agrees the design is defined well enough to write down, then produce
   the **plan**: the steps, and a **code example for every distinct change** you intend to make.

**Plan gate:** stop and wait for explicit developer approval before changing any code. No
implementation begins before this approval.

---

## 3 — Decide (build mode)

One decision: does this job change code?

- **No → read-only exit.** Deliver the answer/assessment. No worktree, no task artifact, no closeout.
  A read-only job may recommend or spawn a follow-up build job; it does not perform one itself.
- **Yes → always a worktree.** Open it with `c-09-git-worktree-manager`. Then pick the build mode:
  - **Chat build** — small enough to carry inline this session: worktree-backed, **no** `task.md`.
  - **Durable task build** — hand off to `w-02-light-task-workflow`: `task.md`, checklist, decision
    log, proposed code examples. Escalate to a master + light sub-task series when the work outgrows a
    single-page plan.

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

Incremental, pushable commits keep the work-loss window small. Each closeout below is one such commit.

---

## 5 — Close

Land the work. **Implementation approval is not commit approval.**

1. Run the `c-09-git-worktree-manager` closeout **preview** for the worktree (`worktree_closeout_preview`) — or
   `direct_closeout_preview` only if the repo's `git-workflow.md` permits a direct-checkout build.
   Relay the proposed code, memory, and ledger commit messages.
2. **Commit gate:** stop for explicit developer commit approval before any real commit or closeout
   apply. If required onboarding is missing, run the `c-05-create-or-update-onboarding-files` skill for the affected file and re-run the preview.
3. On approval, the `c-09-git-worktree-manager` skill owns the external-memory invariant in order: commit code → refresh affected
   onboarding metadata to the new code commit → run memory quality control → commit memory content →
   update and commit the ledger.
4. **Land** per `system/git-workflow.md`: on a PR-gated branch, push the work branch, open the PR,
   wait for green checks, merge per the repo convention; never push a protected branch directly. The
   agent does not push on its own authority.
5. **Cleanup + carryover:** reclaim the worktree/provider stack and bring the parked memory home.
   When the worktree memory branch diverged or the code PR squash-merged, use
   `c-11-memory-carryover-from-branch`; when it is a clean linear descendant of main-memory, a
   fast-forward + push is enough.
6. **Map the ledger to the landed commit.** A PR merge usually lands a **merge commit** on top of the
   work — tree-identical to the verified tip but a new SHA the ledger does not yet map. Ensure the
   ledger maps that merge commit so the next worktree can base off the merged branch without a manual
   reconciliation. `system/git-workflow.md` owns this step.

A read-only exit skips this phase entirely.
