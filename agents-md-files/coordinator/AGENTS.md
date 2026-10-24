# AGENTS.md

## Start Here — Route By Role

Sessions route by role through the `l-01-agent-lifecycles` skill. A **role
agent** that the product started follows its handover, and the rest of this
section is not addressed to it. A **developer-facing session** is the **free
chat** — a launcher, not a role seat (ruled 2026-07-09): answer a question
inline; for work on a managed repository, create or resolve the sprint document,
write the request of the developer into it, start an Architect with
`role_start` (the call for an Architect names no document), and tell the
developer that the agent is in the dashboard and which sprint document holds the
request. Another role is started this way only when the developer names the role
and the task. The call carries no brief: what the new agent must know stands in
the task document before the start, and the developer names the sprint document
to the Architect in the dashboard. The started architect enters
`skills/l-01-agent-lifecycles/roles/architect.md` and owns the developer
conversation from there; the developer speaks with it in the dashboard.

For a first sprint, free chat first uses the durable task workflow to create the
master and its first leaf; this bounded bootstrap supplies immutable scope data
without creating a global architect identity.

During an already-running session, the agent must stay aware of managed-repo
boundaries. If a new turn or tool target may cross from outside Agents Remember
scope into a managed repository, enter the architect lifecycle first.

---

**IMPORTANT:**
Do not change code without entering the architect lifecycle and clearing its plan gate.
Do not change task plan items without approval.
Do not randomly commit. Use the `w-02-light-task-workflow` skill instead!

---

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Installed AGENTS.md Routing

This coordinator file is the workspace entrypoint. Read these installed
`AGENTS.md` files when their scope becomes relevant:

- `tasks/AGENTS.md` — task collaboration doctrine (applied up front in the `l-01-agent-lifecycles` architect lifecycle's
  reframe-research phase; see _Start Here — Route By Role_ above).

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected. Route retrieval through `c-04-retrieval-strategy-router` (see
_Memory Retrieval Strategies_ below) before relying on onboarding, providers, or
repository source.

Until the build decision, route managed-repo source reads through the `read_ar_files` MCP tool
rather than going straight to the native read tool — it names where each file is and keeps
the read trail observable, but it carries no file text: read the named files with the native
read tool. Native read is the edit precondition once building begins.
Count your `read_ar_files` calls as retrieval evidence alongside your native searches and the files you read.

### Developer Clarifications

When a developer clarifies an important concept, invariant, boundary, or
current-state behavior, use `c-05-create-or-update-onboarding-files`. Ask whether the verified
clarification should be documented in onboarding.

Do not copy the clarification into onboarding verbatim. Verify it against the
relevant code, onboarding, and supporting context first. If code reality
contradicts the clarification or only partially supports it, surface the
mismatch and discuss it before propagating anything through
`c-05-create-or-update-onboarding-files`.

---

## Ar-coordination & Memory Layer Resolver

Infer which code repository is supposed to be worked on for a given task from the developer prompt. Ask the developer in case its unclear. That inferred repository is the code repository for resolver inputs.

Resolve the active memory and coordination context for the code repository before relying on onboarding, task files, docs, or tools. Use `c-08-ar-coordination-context-resolver` as the normal resolver entry point: pass `code_repository_name` or `code_repository_root` and consume the returned local or shared context.

After the `c-08-ar-coordination-context-resolver` skill resolves the target repository and coordination root, prefer the
Agents Remember `context_packet` MCP tool when that server is configured.
Provider authority comes from the MCP settings file.

If the MCP settings configure providers, run:

```text
context_packet(repo_id="<repo-id>")
```

(`context_packet` takes no provider argument; provider state comes from the settings
and the provider operations below, never from the packet.) If the settings report
stopped or degraded providers, report that state and use
the MCP provider/runtime operations that match the requested work: `reconnect` (with
`force: true` to switch accounts) for hosted connector accounts, `authorize` for local
MCP server OAuth, and `install`/`enable` for local MCP servers from the catalog.

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
- After the `c-08-ar-coordination-context-resolver` skill resolves a `memory_root`, read that memory layer's `system/settings.md`
  and `system/tools.md`; also read `system/sources.md`,
  `system/coding-guidelines.md`, and `system/task-publication.md` when present.
- Read `system/task-publication.md` (when present) **before any commit, push, PR, or
  release** — it owns the repo's landing and publication flow.
- Before committing read the `w-02-light-task-workflow` skill!

### Memory Repo User Settings, Instructions, and Guidelines

- Memory repos are not expected to provide a root-level `AGENTS.md`; repo-specific
  guidance belongs in the memory layer's `system/*` files.
- `system/settings.md` for human and agent instructions.
- `system/settings.json` for storage, path-rule, and cross-repo policy.
- `system/tools.md` for repo-specific test, lint, typecheck, build,
  smoke-check, and local command notes.
- `system/task-publication.md` when present for the repo's landing and publication
  flow: the landing-workflow setting, the spear branch, commit/push gates, the
  pull-request publication steps, and the release/tag flow.
- `system/sources.md` for domain documentation and external references.
- `system/coding-guidelines.md` when present for repo-specific coding rules.

### Memory Retrieval Strategies

- `Semantics` (retired — no semantic-query provider): a fuzzy concept is known, but structure,
  route, or file location is unknown. Answer it with route-index and onboarding search, then
  bounded native reads.
- `Relationship` (retired — no relationship-query provider): an anchor is known, but callers,
  callees, dependencies, ownership, inheritance, impact paths, or neighboring code are unknown.
  Answer it with native search from the anchor symbol.
- `Intent`: an anchor/location + relationships are known, but hidden contracts, invariants,
  branch-valid truths, behavioral expectations, or code intent are unknown. Use the
  `read_ar_files` MCP tool to name the onboarding for bounded source confirmation: one call
  names each source file's card or record; then read the named sidecars and source with the
  native read tool, adding the repository and governing route overviews only when a file's
  role is unclear.

Use `c-04-retrieval-strategy-router` to understand the full benefit of the strategies as they allow you to complete the task faster.

### Branch And Workflow Notes

Repo-specific branching, landing and publication strategies belong in
`system/task-publication.md` when present (otherwise `system/tools.md`), so agents
discover them **before committing, pushing, opening a PR, or using worktree
integration commands**. Read `task-publication.md` before landing changes on a gated
branch (e.g. a PR-gated `main`). If a workflow helper has generic integration behavior, prefer the
repository-specific landing notes when they are more restrictive.

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

After the `c-08-ar-coordination-context-resolver` skill resolves context, use the resolved memory layer's `system/tools.md`
for repository-specific test, lint, typecheck, build, smoke-check, branch, and
local command guidance. Use `system/coding-guidelines.md` when present for
repo-specific coding rules. Use `system/code-quality-report-template.md`
as a template for reporting code quality results after implementation work
changes source code.
