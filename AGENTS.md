# Agents Remember Source Checkout Instructions

This repository is the source package for Agents Remember. It is not the live
coordination runtime after installation.

If this file is being read from a workspace-level pointer while you are working
on a sibling repository, use the installed runtime instructions instead:

```text
<workspace>/ar-coordination/AGENTS.md
```

When working on this repository itself, use `agents-remember-md` as the target
code repository for resolver, onboarding, workflow, and closeout commands.

## Task Format Routing

Choose one work format before changing files:

1. Use `w-03-chat-task-workflow` by default for small changes that can finish in
   the current session and do not need a durable task file.
2. Use `W-02-light-task-workflow` when the work needs a durable task file.
3. Use `W-01-heavy-task-workflow` only when the developer explicitly asks for a
   heavy task or the full phased workflow.

Do not change code or documentation without following one of these workflows.
Do not change task plan items without approval. Think before acting.

## Memory And Onboarding

Before relying on onboarding, task files, docs, or tools, resolve the active
Agents Remember context with `C-08-ar-coordination-context-resolver`.

For this source checkout, the normal resolver input is:

```text
code_repository_name = agents-remember-md
```

After C-08 resolves the target repository and coordination root, prefer the
Agents Remember MCP `context_packet` tool when that server is configured.
Provider authority comes from the MCP settings file.

If the MCP settings configure providers, request:

```text
context_packet(repo_id="agents-remember-md", include_providers=true)
```

Skip this provider check when no MCP server is configured or the MCP settings
report no providers.

Then run `C-02-memory-quality-control` for the resolved context before
reasoning from onboarding or source files.

After C-08 resolves `memory_root`, read that memory layer's repository-specific
guidance:

- `system/settings.md`
- `system/settings.json`
- `system/tools.md` for repo-specific tools, commands, and code quality checks
- `system/sources.md`
- `system/coding-guidelines.md`, when present

Do not assume this source checkout has active root-level `system/` settings.
Runtime settings examples live under `runtime/system/defaults/`, and installed
runtime settings live under the selected `ar-coordination/` or memory root.

## Source Layout

- `mcp/` contains the MCP server, tool surface, and package-owned runtime
  services.
- `runtime/agents-md-files/` contains package-owned `AGENTS.md` templates for
  the installed coordinator runtime.
- `runtime/skills/` contains the package-owned skill source tree.
- `runtime/system/defaults/` contains starter examples that initialization
  skills may use when creating user-owned settings.
- `README.md` documents the current user-facing install and usage model.
- `roadmap/` contains future design notes, not active runtime behavior.

## Boundaries

- Keep this root `AGENTS.md` scoped to working on the source checkout.
- Keep installed coordinator instructions in `runtime/agents-md-files/`.
- Keep user-specific behavior, project notes, and repo policy in the resolved
  memory layer, not in package-owned installed `AGENTS.md` templates.
- Do not create, close out, integrate, push, or clean up worktrees without the
  approval gates required by the selected workflow.
- Implementation approval is not commit approval. After checks or closeout
  dry-runs, stop and ask for explicit commit approval before running any real
  commit, closeout apply, integration, push, or cleanup command.
- Do not move protected branches unless the developer explicitly asks.

## Code Quality Instructions

After implementing Python code in this source checkout, run Ruff and Radon from
the `agents-remember-md/` source repository root. Use the resolved memory
layer's `system/tools.md` for the exact Ruff, Radon, test, build, smoke-check,
branch, and local command guidance. Use `system/coding-guidelines.md` when
present for repo-specific coding rules.
