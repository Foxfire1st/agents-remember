---
name: l-01-session-job-lifecycle
description: "The session job lifecycle the coordinator routes every session into: orient -> ground -> frame -> decide build mode -> build -> close. Owns the read-only exit, the build-mode decision (chat build vs durable w-02-light-task-workflow task), and the job lens (bug/feature/triage/research). Supersedes the retired chat workflow by migrating and modernizing its doctrine."
---

# l-01-session-job-lifecycle Session Job Lifecycle

The `l-01-session-job-lifecycle` skill is the **canvas** the coordinator routes into at the start of every session.
It is not a task format. A task format is one outcome of a single step inside it.

The lifecycle is one shared spine with thin per-job lenses:

```
0 Orient   pull context_packet — onboarding freshness + provider readiness (surface gotchas up front)
1 Ground   read committed-state onboarding for the in-scope anchors (dirty != ignore)
2 Frame    tasks-doctrine: reframe, pull evidence via the c-04-retrieval-strategy-router skill, expose truth gaps + run the job opening move
3 Decide   changes code? no -> read-only exit · yes -> ALWAYS a c-09-git-worktree-manager worktree; then pick the build mode
4 Build    implement in the worktree; refresh onboarding per completed plan-section; checks green per commit
5 Close    worktree closeout -> land per system/git-workflow.md -> cleanup -> c-11-memory-carryover-from-branch carryover (commit-gated)
```

## Companion Files

1. `lifecycle.md` — the spine in detail: what each phase does, the doctrine it carries, and its gates.
2. `job-variants.md` — the four thin job lenses (bug / feature / triage / research).

## When To Use

Every session. The coordinator routes here first; classify the job as a lens during `frame`, not as a
gate. The lifecycle owns the whole arc from a developer's first statement to a landed change (or a
read-only answer). It does not replace the memory/core skills it calls (the `c-02-memory-quality-control`,
`c-04-retrieval-strategy-router`, `c-05-create-or-update-onboarding-files`, `c-08-ar-coordination-context-resolver`,
`c-09-git-worktree-manager`, and `c-11-memory-carryover-from-branch` skills); it sequences them.

## The Build-Mode Decision (the only task-format call)

Format routing is no longer a top-level choice. It is the `decide` step of this lifecycle:

1. **Read-only exit** — the job answers a question or assesses something and changes no code. No
   worktree, no task artifact, no closeout. May spawn a build job later.
2. **Chat build** — a code change small enough to carry inline this session. Worktree-backed, **no
   durable task artifact**. (This path is the one the retired chat workflow used to own.)
3. **Durable task build** — hand off to `w-02-light-task-workflow`: a `task.md` artifact, checklist,
   decision log, and proposed code examples. Escalate to a master + light sub-task series when the
   work outgrows a single-page plan.

**Build always means a worktree.** The git-landing decision (direct vs PR-gated) is deferred to the
repo's `system/git-workflow.md`.

## Invariants

1. Every session enters the `l-01-session-job-lifecycle` skill; the job type is a lens, re-pickable, never a gate.
2. `orient` runs `context_packet` once at the start so onboarding-freshness and provider-readiness are
   known before any planning — no mid-task surprises.
3. `ground` reads **committed-state** onboarding; a file dirty in another chat is still valid for HEAD
   and *more* worth comparing — read it, do not adopt its drift as a maintenance target.
4. Retrieval routes through `c-04-retrieval-strategy-router` (Semantics / Relationship / Intent), not a
   blanket "read all onboarding."
5. `build => worktree`. `durable task => worktree + task.md`. `chat build => worktree, no artifact`.
   `read-only => no worktree`.
6. No implementation begins before explicit developer approval (the `frame` plan gate).
7. Implementation approval is **not** commit approval. Closeout is a separate, explicit commit gate
   after a preview.
8. Onboarding is refreshed **live, per completed plan-section** during `build`, never deferred to the
   end of the job.
9. Checks from the `c-08-ar-coordination-context-resolver` resolved `system/tools.md` run green before **each** incremental commit; the
   pre-commit/pre-push hooks enforce it.
10. The agent never pushes a protected branch on its own authority; landing follows
    `system/git-workflow.md` and its gates.
11. The `l-01-session-job-lifecycle` skill covers everything the retired chat workflow did — the `c-02-memory-quality-control` task-start gate, paired
    source+onboarding reads, the approval gate, and `c-09-git-worktree-manager` closeout — plus the job lens and the
    read-only exit. No regression of the default path.

## Relationship To Other Instructions

This skill extends the coordinator `AGENTS.md` and the repository memory layer; it does not replace
them. Read `lifecycle.md` for phase behavior and `job-variants.md` for the per-job lenses.
