# Workflows

Agents Remember has three task workflows. They share the same discipline and differ in how much ceremony the work needs.

## Shared Discipline

Every workflow keeps these rules:

1. Resolve the active context with `C-08-ar-coordination-context-resolver`.
2. Run drift detection before planning against onboarding.
3. Wait for developer approval before implementation.
4. Update onboarding only after approved changes.
5. Run the checks listed in the resolved memory layer's `system/tools.md` when available.

## Chat Mode

Use chat mode by default.

Chat mode fits tasks that can finish in the current session and do not need a durable task file. The agent reads onboarding, proposes the intended change, waits for approval, implements, updates onboarding if needed, and verifies.

Small external-memory edits in the current checkout can use `C-12-closeout` direct closeout after approval. Direct closeout commits code first, refreshes onboarding metadata to that code commit, commits memory, then updates the ledger.

## Light Task

Use `W-02-light-task-workflow` when the work needs a durable task file but still fits a compact plan.

Light tasks create:

```text
ar-coordination/tasks/<repo>/<task-slug>/task.md
```

The task file holds requirements, implementation steps, proposed examples, decisions, open questions, and references. The checklist is the live execution state.

Use light tasks for documentation restructures, medium refactors, multi-step cleanup, and work that might survive more than one session.

## Heavy Task

Use `W-01-heavy-task-workflow` only when the developer explicitly asks for it or when the task genuinely needs full phased review.

Heavy workflow is for migrations, cross-repo contracts, and changes where a plausible edit can be seriously wrong. It uses research, synthesis, design, planning, implementation, closure, and adversarial review checkpoints.

## Worktree-Backed Tasks

C-09 can create task worktrees when parallel work, external-memory closeout, or explicit lifecycle tracking is needed. A worktree-backed task has a `contract.md` beside the task file. C-09 owns the worktree lifecycle, integration, and cleanup; `C-12-closeout` runs the closeout itself.

Closeout and commit approval are separate from implementation approval. The agent should present a dry-run preview before any C-12 closeout creates commits.

## Choosing A Workflow

| Situation | Workflow |
| --- | --- |
| One-session work, low risk | Chat |
| Needs a durable plan or checklist | Light task |
| High-risk migration or cross-repo contract | Heavy task |
| Parallel implementation or explicit closeout tracking | Light or heavy task plus C-09 worktrees |
