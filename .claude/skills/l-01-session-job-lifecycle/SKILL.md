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

```
0 Request  developer states the request; model infers the target repo and reports ambiguity
1 Trust    resolve via context_packet(include_providers=true, include_drift=true); handle drift and providers before relying on memory
2 Frame    gather c-04 evidence, reframe through tasks/AGENTS.md, get developer agreement, then do deeper research with proof
3 Decide   changes code? no -> research-only exit · yes -> approve worktree intent, then c-09 worktree; pick build mode
4 Build    implement in the worktree; refresh onboarding per completed plan-section; checks green per commit
5 Close    worktree closeout -> land per system/git-workflow.md -> cleanup -> c-11-memory-carryover-from-branch carryover (commit-gated)
```

Research-only answers, investigations, and code questions can exit after the
reframe/research phase without opening a worktree or entering closeout.

## Companion Files

1. `lifecycle.md` — the spine in detail: what each phase does, the doctrine it carries, and its gates.
2. `job-variants.md` — the four thin job lenses (bug / feature / triage / research).
3. `deep-research-report-template.md` — the reusable report and evidence-ledger shape for deeper research.

## When To Use

Every session. The coordinator routes here first; classify the job as a lens during `frame`, not as a
gate. The lifecycle owns the whole arc from a developer's first statement to a landed change (or a
research-only answer). It does not replace the memory/core skills it calls (the `c-02-memory-quality-control`,
`c-04-retrieval-strategy-router`, `c-05-create-or-update-onboarding-files`, `c-08-ar-coordination-context-resolver`,
`c-09-git-worktree-manager`, and `c-11-memory-carryover-from-branch` skills); it sequences them.

## The Build-Mode Decision (the only task-format call)

Format routing is no longer a top-level choice. It is the `decide` step of this lifecycle:

1. **Research-only exit** — the job answers a question or assesses something and changes no code. No
   worktree, no task artifact, no closeout. May spawn a build job later.
2. **Chat build** — a code change small enough to carry inline this session. Worktree-backed, **no
   durable task artifact**. (This path is the one the retired chat workflow used to own.)
3. **Durable task build** — hand off to `w-02-light-task-workflow`: a `task.md` artifact, checklist,
   decision log, and proposed code examples. Escalate to a master + light sub-task series when the
   work outgrows a single-page plan.

**Build always means a worktree.** Before `worktree_start`, read `c-09-git-worktree-manager`
and the repo's `system/git-workflow.md`, present the worktree intent packet,
and wait for explicit approval. The git-landing decision (direct vs PR-gated)
is deferred to the repo's `system/git-workflow.md`.

## Invariants

1. Every session enters the `l-01-session-job-lifecycle` skill; the job type is a lens, re-pickable, never a gate.
2. The model runs `context_packet(repo_id="<repo-id>", include_providers=true, include_drift=true)`
   before trusting onboarding, providers, task files, or source interpretation.
3. Clean-source onboarding drift is a developer choice point: refresh through
   `c-05-create-or-update-onboarding-files` before proceeding, or proceed with
   that onboarding explicitly marked untrusted. Dirty-source drift is reported as
   active work-in-progress and is not taken over unless the developer says so.
4. Degraded providers are handled with the matching MCP provider/runtime
   operations and then re-checked; ready providers are reported before evidence
   gathering continues.
5. Retrieval routes through `c-04-retrieval-strategy-router` (Semantics / Relationship / Intent), not a
   blanket "read all onboarding."
6. The model reframes the developer request through `tasks/AGENTS.md`, presents
   the reframe, and waits for developer agreement or revision before deeper
   research proceeds.
7. Deeper research reports use `deep-research-report-template.md` and list the onboarding docs read,
   semantic queries, code graph queries, source files inspected, and remaining truth gaps.
8. `build => worktree`. `durable task => worktree + task.md`. `chat build => worktree, no artifact`.
   `research-only => no worktree`.
9. Before `worktree_start`, the model presents a worktree intent packet naming
   repo, build mode, branch policy, source branch, work branch, memory mode,
   landing path, and risks; the developer approves or revises it.
10. No implementation begins before explicit developer approval (the `frame` plan gate).
11. Implementation approval is **not** commit approval. Closeout is a separate, explicit commit gate
   after a preview.
12. Onboarding is refreshed **live, per completed plan-section** during `build`, never deferred to the
   end of the job.
13. Checks from the `c-08-ar-coordination-context-resolver` resolved `system/tools.md` run green before **each** incremental commit; the
   pre-commit/pre-push hooks enforce it.
14. The agent never pushes a protected branch on its own authority; landing follows
    `system/git-workflow.md` and its gates.
15. The `l-01-session-job-lifecycle` skill covers everything the retired chat
    workflow did — task-start trust control, paired source+onboarding reads, the
    approval gate, and `c-09-git-worktree-manager` closeout — plus the job lens,
    developer-agreed reframe, proof-bearing research, and the research-only exit. No
    regression of the default path.

## Relationship To Other Instructions

This skill extends the coordinator `AGENTS.md` and the repository memory layer; it does not replace
them. Read `lifecycle.md` for phase behavior, `job-variants.md` for the per-job lenses, and
`deep-research-report-template.md` for the deeper research report shape.
