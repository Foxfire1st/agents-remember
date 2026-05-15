# AGENTS.md

## Task Format Routing

This workspace has exactly three task/work formats. Choose deliberately before creating or updating task artifacts.

### 1. Chat Mode

Use chat mode `w-03-chat-task-workflow` by default when the work is small enough to finish in the current session and does not need a durable task file.

### 2. Light Task Workflow

Use `W-02-light-task-workflow` whenever a task file is needed. This is the
standard durable-task format for planning and implementation work in this
workspace.

### 3. Heavy Task Workflow

Use `W-01-heavy-task-workflow` only when the developer explicitly asks for the
heavy task workflow, a heavy task, or the full phased workflow.

---

**IMPORTANT:** Do not change code without following one of the above workflows!

---

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Installed AGENTS.md Routing

This coordinator file is the workspace entrypoint. Read the sibling installed
`AGENTS.md` files when their scope becomes relevant:

- Read `system/AGENTS.md` before relying on onboarding or reasoning over
  repository source.
- Read `tasks/AGENTS.md` before creating or updating task artifacts, or when
  task framing, meta-questioning, or approval doctrine matters.
- Read `skills/AGENTS.md` before choosing memory, onboarding, findings,
  discovery, or worktree skills.

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is to be read alongside the code they describe, at the moment that code is
inspected. They can be found using the ar-coordination resolver.

Before trusting the onboarding documentation, check the [Memory Layer Instructions](system/AGENTS.md)

### Developer Clarifications

When a developer clarifies an important concept, invariant, boundary, or
current-state behavior, use `C-01-findings-capture`. Ask whether the verified
clarification should be documented in onboarding.

Do not copy the clarification into onboarding verbatim. Verify it against the
relevant code, onboarding, and supporting context first. If code reality
contradicts the clarification or only partially supports it, surface the
mismatch and discuss it before propagating anything through
`C-05-create-or-update-onboarding-files`.

---

## Ar-coordination & Memory Layer Resolver

Infer which code repository is supposed to be worked on for a given task from the developer prompt. Ask the developer in case its unclear. That inferred repository is the code repository for resolver inputs.

Resolve the active memory and coordination context for the code repository before relying on onboarding, task files, docs, or tools. Use `C-08-ar-coordination-context-resolver` as the normal resolver entry point: pass `code_repository_name` or `code_repository_root` and consume the returned local or shared context.

### Routing

- Use `system/settings.md` for global agent instructions, cross-repo defaults,
  layout, and operator notes.
- Use `system/settings.json` for machine-readable coordinator layout hints.
- Use `system/tools.md` for tools and commands that are valid across all or many
  repositories.
- Use `system/sources.md` for workspace-wide source registries.
- Do not put rules that are valid for only one code repository in coordinator
  files; put them in that repository's memory layer.
- After C-08 resolves a `memory_root`, read that memory layer's `system/settings.md`
  and `system/tools.md`; also read `system/sources.md` and
  `system/coding-guidelines.md` when present.

### Memory Repo User Settings, Instructions, and Guidelines

- Memory repos are not expected to provide a root-level `AGENTS.md`; repo-specific
  guidance belongs in the memory layer's `system/*` files.
- `system/settings.md` for human and agent instructions.
- `system/settings.json` for storage, path-rule, and cross-repo policy.
- `system/tools.md` for repo-specific checks, branch workflow, and local command
  notes.
- `system/sources.md` for domain documentation and external references.
- `system/coding-guidelines.md` when present for repo-specific coding rules.

### Branch And Workflow Notes

Repo-specific branching strategies belong in `system/tools.md` so agents can
discover them before using worktree integration commands. If a workflow helper
has generic integration behavior, prefer the repository-specific branch notes
when they are more restrictive.

Coordinator-wide guidance may still apply as a default, but this memory layer is
the more specific authority for its code repository.

### Boundaries

- Do not move protected branches unless the developer explicitly asks.
- Do not create, close out, integrate, push, or clean up worktrees without the
  approval gates required by the selected workflow.
- When coordinator-wide guidance and memory-layer guidance conflict, prefer the
  memory-layer rule for that repository.
