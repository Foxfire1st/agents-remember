# AGENTS.md

## Start Here — Enter the Job Lifecycle

Every session enters `l-01-session-job-lifecycle` — the canvas this coordinator
routes into.

During an already-running session, the agent must stay aware of managed-repo
boundaries. If a new turn or tool target may cross from outside Agents Remember
scope into a managed repository, enter the lifecycle at `l-01-session-job-lifecycle`.

---

**IMPORTANT:**
Do not change code without entering the lifecycle and clearing its `frame` plan gate.
Do not change task plan items without approval.
Do not randomly commit. Use the `c-12-closeout` skill instead!

---

## Memory System

This workspace uses a layered memory system. Make sure to read the below rules before performing actions.

### Installed AGENTS.md Routing

This coordinator file is the workspace entrypoint. Read these installed
`AGENTS.md` files when their scope becomes relevant:

- `tasks/AGENTS.md` — task collaboration doctrine (applied up front in the `l-01-session-job-lifecycle` skill's
  `frame` phase; see _Start Here — Enter the Job Lifecycle_ above).

### Onboarding Documentation

Onboarding files are companion context for source files. Their main purpose is
to be read alongside the code they describe, at the moment that code is
inspected. Route retrieval through `c-04-retrieval-strategy-router` (see
_Memory Retrieval Strategies_ below) before relying on onboarding, providers, or
repository source.

### Developer Clarifications

When a developer clarifies an important concept, invariant, boundary, or
current-state behavior, use `c-01-findings-capture`. Ask whether the verified
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
- After the `c-08-ar-coordination-context-resolver` skill resolves a `memory_root`, read that memory layer's `system/settings.md`
  and `system/tools.md`; also read `system/sources.md`,
  `system/coding-guidelines.md`, and `system/git-workflow.md` when present.
- Read `system/git-workflow.md` (when present) **before any commit, push, PR, or
  release** — it owns the repo's gated-branch landing flow.
- Before committing read the `c-12-closeout` skill!

### Memory Repo User Settings, Instructions, and Guidelines

- Memory repos are not expected to provide a root-level `AGENTS.md`; repo-specific
  guidance belongs in the memory layer's `system/*` files.
- `system/settings.md` for human and agent instructions.
- `system/settings.json` for storage, path-rule, and cross-repo policy.
- `system/tools.md` for repo-specific test, lint, typecheck, build,
  smoke-check, and local command notes.
- `system/git-workflow.md` when present for the repo's gated-branch landing flow:
  the spear branch, commit/push gates, the PR + merge convention, and the
  release/tag flow.
- `system/sources.md` for domain documentation and external references.
- `system/coding-guidelines.md` when present for repo-specific coding rules.

### Memory Retrieval Strategies

- `Semantics`: Fuzzy search use GrepAI to search over onboardings. Leads to code routes & files using 1-to-1 file mapping backward.
- `Relationship`: For code-relationsship questions use Code Graph Context (cgc).
- `Intent`: an anchor/location + relationships are known, but hidden contracts, invariants,
  branch-valid truths, behavioral expectations, or code intent are unknown. Use
  onboarding plus bounded source confirmation.

Use `c-04-retrieval-strategy-router` to understand the full benefit of the strategies as they allow you to complete the task faster.

### Branch And Workflow Notes

Repo-specific branching and landing strategies belong in `system/git-workflow.md`
when present (otherwise `system/tools.md`), so agents discover them **before
committing, pushing, opening a PR, or using worktree integration commands**. Read
`git-workflow.md` before landing changes on a gated branch (e.g. a PR-gated
`main`). If a workflow helper has generic integration behavior, prefer the
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
