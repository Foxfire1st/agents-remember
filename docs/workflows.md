# Workflows

Every session runs through the **`l-01-session-job-lifecycle` session job lifecycle**: orient → ground → frame → decide →
build → close. The job type (bug / feature / triage / research) is a lens during framing, not a gate.
The only task-format decision is the `l-01-session-job-lifecycle` skill's build-mode step.

## Shared Discipline

Every build keeps these rules:

1. Resolve the active context with `c-08-ar-coordination-context-resolver`.
2. Run drift detection before planning against onboarding.
3. Wait for developer approval before implementation (the `l-01-session-job-lifecycle` skill's `frame` plan gate).
4. Update onboarding only after approved changes, live per completed plan-section.
5. Run the checks listed in the resolved memory layer's `system/tools.md` when available.

## Build Modes

At `decide`, the `l-01-session-job-lifecycle` skill picks one of:

### Read-only exit

The job answers a question or assesses something and changes no code — no worktree, no task file, no
closeout. Research and triage jobs usually exit here; they may recommend or spawn a build job.

### Chat build

A code change small enough to carry inline this session: worktree-backed, with no durable task file.
After approval, small external-memory edits close through `c-12-closeout` direct closeout, which
commits code first, refreshes onboarding metadata to that code commit, commits memory, then updates
the ledger. (This is the path the retired chat workflow used to own.)

### Durable task (`w-02-light-task-workflow`)

Use `w-02-light-task-workflow` when the work needs a durable task file but still fits a compact plan:

```text
ar-coordination/tasks/<repo>/<task-slug>/task.md
```

The task file holds requirements, implementation steps, proposed examples, decisions, open questions,
and references; the checklist is the live execution state. Use it for documentation restructures,
medium refactors, multi-step cleanup, and work that might survive more than one session.

When the work outgrows a single-page plan — broad cross-repo or high-risk changes, or several distinct
slices that each need their own checklist and commit — escalate to a **master + light sub-task series**
(`master-template.md`): one wrapper folder with a master `task.md` plus flat, numbered sub-task files,
one shared worktree, a commit per slice, and a single release at the end. This composition replaces the
retired heavy workflow.

## Worktree-Backed Tasks

Every build is worktree-backed (only read-only exits skip the worktree). The `c-09-git-worktree-manager` skill creates the task
worktree; a worktree-backed task has a `contract.md` beside the task file. The `c-09-git-worktree-manager` skill owns the worktree
lifecycle, integration, and cleanup; `c-12-closeout` runs the closeout itself. A master series runs in
**one** shared worktree for the whole series.

Closeout and commit approval are separate from implementation approval. The agent presents a dry-run
preview before any `c-12-closeout` closeout creates commits. The git-landing flow (direct vs PR-gated) is
deferred to the repo's `system/git-workflow.md` when present.

## Direct Closeout

The normal 2.0.0 build path is worktree-backed (only read-only exits skip it). The
`direct_closeout_preview` / `direct_closeout_apply` MCP tools remain available as a
**controlled exception** for current-checkout maintenance — small memory or doc updates
where spinning up a worktree adds unnecessary ceremony. Use them only when the developer
explicitly approves a current-checkout closeout; for normal code changes, prefer the
worktree-backed path above.

## Choosing A Build Mode

| Situation | Build mode |
| --- | --- |
| Answer or assessment, no code change | Read-only exit |
| One-session code change, low risk | Chat build (worktree, no task file) |
| Needs a durable plan or checklist | `w-02-light-task-workflow` light task |
| Outgrows a single-page plan, or broad/high-risk | Master + light sub-task series |
| Parallel implementation or explicit closeout tracking | Any build mode — all builds are worktree-backed via the `c-09-git-worktree-manager` skill |
