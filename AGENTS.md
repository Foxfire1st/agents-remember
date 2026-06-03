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

## Start Here — Enter the Job Lifecycle

Every session enters `l-01-session-job-lifecycle` — the lifecycle this checkout
routes into. The `l-01-session-job-lifecycle` skill owns the whole arc: orient → ground → frame → decide → build →
close. Classify the job (bug / feature / triage / research) as a *lens* during
framing — a hint, re-pickable, never a gate.

The only task-format decision is the `l-01-session-job-lifecycle` skill's **build-mode** step, taken at `decide`:

1. **Read-only exit** — answers or assessments that change no code: no worktree,
   no task file.
2. **Chat build** — a code change carried inline this session: worktree-backed,
   no durable task file.
3. **Durable task** — `w-02-light-task-workflow`: a `task.md` with checklist,
   decision log, and proposed code examples; escalates to a master + light
   sub-task series when the work outgrows a single-page plan.

The task-collaboration doctrine in `tasks/AGENTS.md` applies inside the `l-01-session-job-lifecycle` skill's
`frame` phase, in plain chat, before any task file or format is chosen.

---

**IMPORTANT:**
Do not change code or documentation without entering the lifecycle and clearing its `frame` plan gate.
Do not change task plan items without approval. Think before acting.
Do not randomly commit. Use the `c-12-closeout` skill instead!

---

## Memory And Onboarding

Before relying on onboarding, task files, docs, or tools, resolve the active
Agents Remember context with `c-08-ar-coordination-context-resolver`.

For this source checkout, the normal resolver input is:

```text
code_repository_name = agents-remember-md
```

After the `c-08-ar-coordination-context-resolver` skill resolves the target repository and coordination root, prefer the
Agents Remember `context_packet` MCP tool when that server is configured.
Provider authority comes from the MCP settings file.

If the MCP settings configure providers, request:

```text
context_packet(repo_id="agents-remember-md", include_providers=true)
```

Skip this provider check when no MCP server is configured or the MCP settings
report no providers.

Then run `c-02-memory-quality-control` for the resolved context before
reasoning from onboarding or source files.

After the `c-08-ar-coordination-context-resolver` skill resolves `memory_root`, read that memory layer's repository-specific
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

Use `c-04-retrieval-strategy-router` to understand the full benefit of the strategies as they allow you to complete the task faster.

## Source Layout

- `skills/` is the canonical skill source tree. Edit skills here first.
- `scripts/sync-skills.py` copies root `skills/` into the MCP package-data copy
  and all harness starter package skill folders. Run it after any skill edit.
- `mcp/` contains the MCP server, tool surface, and package-owned runtime
  services.
- `mcp/src/agents_remember/package_data/runtime/agents-md-files/` contains
  package-owned `AGENTS.md` templates for the installed coordinator runtime.
- `mcp/src/agents_remember/package_data/runtime/skills/` is the generated
  package-owned skill copy used by `runtime_install`.
- `mcp/src/agents_remember/package_data/runtime/system/defaults/` contains
  starter examples that initialization skills may use when creating user-owned
  settings.
- `README.md` documents the current user-facing install and usage model.
- `roadmap/` contains future design notes, not active runtime behavior.

## Boundaries

- Keep this root `AGENTS.md` scoped to working on the source checkout.
- Keep installed coordinator instructions in `runtime/agents-md-files/`.
- Do not edit generated skill copies directly; edit root `skills/` and run
  `python3 scripts/sync-skills.py`.
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
