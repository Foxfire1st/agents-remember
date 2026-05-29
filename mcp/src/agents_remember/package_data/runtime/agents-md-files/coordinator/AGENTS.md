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

**IMPORTANT:**
Do not change code without following one of the above workflows!
Do not change task plan items without approval.
Do not randomly commit. Use the `C12-closeout` procedure instead!

---

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Installed AGENTS.md Routing

This coordinator file is the workspace entrypoint. Read these installed
`AGENTS.md` files when their scope becomes relevant:

- Do not rush on every dev statement to change the whole plan. Instead follow `tasks/AGENTS.md` doctrine
  when designing and planning a task. Help the developer through back and forth discussion in chat, to reframe
  their requests better, think through the problem, find deeper truths, and hidden variables. Do that until
  the developer beliefs that the design item is well enough defined to be written down.

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected.

Use `C-04-retrieval-strategy-router` before relying on onboarding, providers,
or repository source. C-04 owns Semantics, Relationship, and Intent routing
across optional providers, route indexes, onboarding, and bounded source
confirmation.

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

After C-08 resolves the target repository and coordination root, prefer the
Agents Remember MCP `context_packet` tool when that server is configured.
Provider authority comes from the MCP settings file.

If the MCP settings configure providers, run:

```text
context_packet(repo_id="<repo-id>", include_providers=true)
```

If the packet reports stopped or degraded providers, report that state and use
the MCP provider/runtime operations that match the requested work.

Skip this provider check when no MCP server is configured or the MCP settings
report no providers.

### Routing

- Use `system/settings.md` for global agent instructions, cross-repo defaults,
  layout, and operator notes.
- Use `system/settings.md` for model-facing coordinator notes. Machine-readable
  MCP authority settings live outside the coordinator root.
- Use `system/tools.md` for tools, commands, and code quality checks that are
  valid across all or many repositories.
- Use `system/sources.md` for workspace-wide source registries.
- Do not put rules that are valid for only one code repository in coordinator
  files; put them in that repository's memory layer.
- After C-08 resolves a `memory_root`, read that memory layer's `system/settings.md`
  and `system/tools.md`; also read `system/sources.md` and
  `system/coding-guidelines.md` when present.
- Before committing read the `C12-closeout` procedure!

### Memory Repo User Settings, Instructions, and Guidelines

- Memory repos are not expected to provide a root-level `AGENTS.md`; repo-specific
  guidance belongs in the memory layer's `system/*` files.
- `system/settings.md` for human and agent instructions.
- `system/settings.json` for storage, path-rule, and cross-repo policy.
- `system/tools.md` for repo-specific test, lint, typecheck, build,
  smoke-check, branch workflow, and local command notes.
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

- Do not run Git commands against `ar-coordination/` as a whole.
- Use Git only against the resolved `code_repository_root` or `memory_root`
  when those paths are Git repositories.
- Task files under `ar-coordination/tasks/` are local coordination artifacts
  unless a workflow explicitly says otherwise.
- Do not move protected branches unless the developer explicitly asks.
- Do not create, close out, integrate, push, or clean up worktrees without the
  approval gates required by the selected workflow.
- Implementation approval is not commit approval. After checks or closeout
  dry-runs, stop and ask for explicit commit approval before running any real
  commit, closeout apply, integration, push, or cleanup command.
- When coordinator-wide guidance and memory-layer guidance conflict, prefer the
  memory-layer rule for that repository.

## Code Quality Instructions

After C-08 resolves context, use the resolved memory layer's `system/tools.md`
for repository-specific test, lint, typecheck, build, smoke-check, branch, and
local command guidance. Use `system/coding-guidelines.md` when present for
repo-specific coding rules. Use `system/code-quality-report-template.md`
as a template for reporting code quality results after implementation work
changes source code.
