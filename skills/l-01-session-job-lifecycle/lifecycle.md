# l-01-session-job-lifecycle Lifecycle — The Spine

One shared spine carries every session. The job type (see `job-variants.md`) only tunes the opening
move, the retrieval lean, and the `decide` default — it never adds or removes phases.

The front half is a developer/model collaboration loop. The developer states
the request and remains the state authority for whether the model's reframe
is correct. The model owns interpretation, evidence gathering, synthesis,
and reporting. MCP tools and onboarding provide auditable proof points that
the model did the required grounding before moving on.

```
0 Request -> 1 Trust Checkpoint -> 2 Reframe + Research -> 3 Decide -> 4 Build -> 5 Close
                                                   |
                                                   +-- research-only (e.g. investigation, code questions...)
```

---

## 0 — Request

Receive the developer's raw request and identify the active repository.

1. Treat the developer's statement as raw input, not yet as an implementation plan.
2. Infer the target code repository from the request and local context. Ask the
   developer if the target is unclear.

Request intake changes nothing. It only establishes which repository the next
checkpoint must inspect.

---

## 1 — Trust Checkpoint

Establish whether memory and providers are trustworthy enough to use.

1. For the target repository, resolve coordination/memory context with
   the MCP tool call:

   ```text
   context_packet(repo_id="<repo-id>", include_providers=true, include_drift=true)
   ```

2. Report the packet facts before relying on memory or providers:
   - repository, branch, and dirty state
   - memory root and onboarding root
   - provider state
   - drift status and actionable drift count
3. If onboarding for committed source is drifted, missing verification, or
   orphaned, and the corresponding source file is not dirty in the code
   worktree, stop and ask the developer whether to refresh it through
   `c-05-create-or-update-onboarding-files` before proceeding or to continue
   with that onboarding explicitly marked untrusted.
4. If drift is tied to dirty source, report it as active work-in-progress. Do
   not adopt it as maintenance or silently trust it as current state unless the
   developer explicitly says this job owns it.
5. If providers are stopped or degraded, use the matching MCP provider/runtime
   operations, then re-run the provider check. If providers are ready, report
   readiness and continue. If issues persist report it to the developer and
   wait for instructions.
6. After the trust checkpoint passes, read committed-state onboarding for the
   in-scope anchors as needed. A file dirty in another chat is still valid for
   HEAD and worth comparing, but its dirty-source drift remains active work.

---

## 2 — Reframe And Research

Turn the developer's raw request into an agreed piece of work, then perform the
deeper research that the agreed frame requires. The `tasks/AGENTS.md`
collaboration doctrine applies here in plain chat, before any task file or task
format exists.

1. **Gather evidence for the reframe** through reading the
   `c-04-retrieval-strategy-router` skill. Pick the strategy by the question:
   - *Semantics* (grepai over onboarding) — "where does X live / what handles Y."
   - *Relationship* (cgc) — callers/callees/dependencies/impact.
   - *Intent* (onboarding + bounded source confirmation) — hidden contracts, invariants,
     branch-valid truths, behavioral expectations. This is a workflow of paired
     source+onboarding reads: read the source file together with its verified onboarding.
     Use the memory-repo root overview.md file to gain a birds view of a code repo.
2. **Reframe** the request through `tasks/AGENTS.md`: distinguish the surface
   request, deeper objective, highest-leverage framing, assumptions, boundaries,
   invariants, and truth gaps. Do not rush a statement into a plan.
3. Present the reframe to the developer. If the developer disagrees, discuss and
   revise the reframe. If the developer agrees, proceed to deeper research.
4. **Perform deeper research** for the agreed frame. This research still uses
   `c-04-retrieval-strategy-router`, but it is now scoped by the developer-agreed
   frame rather than by the model's first guess.
5. The deeper research report must list its proof:
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

A research-only exit skips this phase entirely.
