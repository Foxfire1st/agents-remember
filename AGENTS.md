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

## Frame Before You Choose a Format

The moment you are thinking about building something, the collaboration doctrine
in `tasks/AGENTS.md` already applies — in plain chat, before any task file exists
and before a format is chosen below.

Do not rush a request into a plan. Use that doctrine to reframe the request, find
the true scope, surface what could break, and expose hidden variables through
back-and-forth. Pull the evidence it needs with `C-04-retrieval-strategy-router`.

Continue until the design is defined well enough to write down. Only then choose
a task format below.

## Task Format Routing

Choose one work format before changing files:

1. Use `w-03-chat-task-workflow` by default for small changes that can finish in
   the current session and do not need a durable task file.
2. Use `W-02-light-task-workflow` when the work needs a durable task file.
3. Use `W-01-heavy-task-workflow` only when the developer explicitly asks for a
   heavy task or the full phased workflow.

---

**IMPORTANT:**
Do not change code or documentation without following one of these workflows.
Do not change task plan items without approval. Think before acting.
Do not randomly commit. Use the `C12-closeout` procedure instead!

---

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

### Memory Retrieval Strategies

- `Semantics`: Fuzzy search use GrepAI to search over onboardings. Leads to code routes & files using 1-to-1 file mapping backward.
- `Relationship`: For code-relationsship questions use Code Graph Context (cgc).
- `Intent`: an anchor/location + relationships are known, but hidden contracts, invariants,
  branch-valid truths, behavioral expectations, or code intent are unknown. Use
  onboarding plus bounded source confirmation.

Use `C-04-retrieval-strategy-router` to understand the full benefit of the strategies as they allow you to complete the task faster.

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

After implementing Python code in this source checkout, run Ruff, Pyright, and
Radon from the `agents-remember-md/` source repository root. Use the resolved memory
layer's `system/tools.md` for the exact Ruff, Pyright, Radon, test, build, smoke-check,
branch, and local command guidance. Use `system/coding-guidelines.md` when
present for repo-specific coding rules. Use `system/code-quality-report-template.md`
as a template for reporting code quality results after implementation work
changes source code.
