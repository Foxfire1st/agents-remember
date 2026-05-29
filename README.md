# Agents Remember

Durable, git-verified repo memory for coding agents.

**Agents Remember makes hard-earned lessons first-class infrastructure** — the local invariants, naming rules, migration scars, cross-repo contracts, and "this looks safe but is not" facts that live in people's heads, old PRs, and team habits, exactly where coding agents miss them. It keeps that knowledge as versioned Markdown beside the code, drift-checked against Git and updated only after approved work lands.

```text
src/orchestrator/core_editor.py
ar-memory/onboarding/src/orchestrator/core_editor.py.md
```

Agents reach that memory three ways:

- **By path** — a source file's own note, found directly from its path (as above). Needs nothing extra.
- **By meaning** — semantic search across the memory when you know the concept but not the file.
- **By relationship** — a code graph for callers, callees, and dependencies.

The by-path notes are the core; meaning and relationship are opt-in providers (see [Concepts](docs/concepts.md) and [Providers](docs/guides/providers.md)).

## Why It Exists

Modern coding agents can make clean, plausible edits while missing the project-specific rules that make those edits safe. A top-level instruction file can help, but it does not naturally reappear when the agent is deep in a file and deciding what to change.

Agents Remember fixes that: the matching note is reachable at the moment of the edit — most often by the very path the agent is already working in — so project rules surface exactly when a change is being made, not buried in a top-level file.

## Core Model

The memory layer rests on a small, strict discipline:

- **Onboarding units:** Markdown notes derived from source paths. A file such as `src/foo/bar.ts` maps to `ar-memory/onboarding/src/foo/bar.ts.md` in the default repo-local mode.
- **Memory quality control:** Before an agent trusts onboarding, `C-02-memory-quality-control` checks whether the source changed since the onboarding was verified. During closeout it also covers new-file onboarding and final memory quality checks.
- **Approval-gated updates:** Onboarding records approved current state, not guesses or plans. Task-local notes stay task-local until the developer approves implementation.

The default setup stores durable memory in the target repository under `ar-memory/`. Teams that need separate memory repositories can use external memory under `ar-coordination/memory-repos/ar-<repo>/`.

## Quickstart

This is the short path for a new workspace. The detailed walkthrough lives in [Getting Started](docs/getting-started.md).

1. Clone this repository beside the projects you want to onboard.

   ```text
   projects/
     agents-remember-md/
     my-app/
     ar-coordination/
   ```

2. Configure the MCP server with `coordinationRoot` pointing at `ar-coordination`, then request the MCP runtime install tool.

   ```text
   runtime_install(dry_run=false)
   ```

   Reinstall reconciles package-owned runtime scaffold files from the MCP
   package. Provider dependencies and provider runner reinstall are MCP-owned
   operations driven from MCP settings outside the coordinator root.

   Benchmark fixtures are optional. To install or refresh them too:

   ```text
   runtime_install(dry_run=false, include_benchmarks=true)
   ```

3. Expose the packaged skills to your agent harness.

   When the MCP settings file lives under a harness registration folder such as
   `.codex/mcp/`, the install target is inferred as the sibling `skills/`
   folder. Then request:

   ```text
   skills_install(dry_run=false)
   ```

   Use `layout="flat"` only for harnesses that require direct
   `<skill-name>/SKILL.md` folders. The MCP tool copies skills; it does not
   create symlinks.

4. Add workspace instructions that point agents at the installed runtime.

   ```markdown
   # Workspace Agent Instructions

   Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
   Treat these rules as workspace instructions!

   @ar-coordination/AGENTS.md
   ```

5. Ask the agent to initialize memory for a target repository with `C-00-initialize-memory-repo`, then bootstrap initial onboarding with `C-03-repo-bootstrap`.

After that, normal work starts in chat mode. The agent resolves the active context with `C-08-ar-coordination-context-resolver`, checks memory quality with `C-02-memory-quality-control`, reads relevant onboarding beside code, and updates onboarding after approved changes.

## Choose Your Agent

Different tools discover instructions and skills differently. Use the install page for your harness:

| Harness | Setup guide |
| --- | --- |
| Codex | [docs/install/codex.md](docs/install/codex.md) |
| Claude Code | [docs/install/claude-code.md](docs/install/claude-code.md) |
| Cursor | [docs/install/cursor.md](docs/install/cursor.md) |
| Antigravity | [docs/install/antigravity.md](docs/install/antigravity.md) |
| VS Code + GitHub Copilot | [docs/install/vscode-copilot.md](docs/install/vscode-copilot.md) |
| Hermes.md | [docs/install/hermes.md](docs/install/hermes.md) |
| Pi.dev | [docs/install/pi.md](docs/install/pi.md) |
| OpenClaw | [docs/install/openclaw.md](docs/install/openclaw.md) |

## Documentation

- [Getting Started](docs/getting-started.md) - a fuller first-run setup.
- [Concepts](docs/concepts.md) - onboarding units, memory roots, drift, and approval gates.
- [Architecture](docs/architecture.md) - runtime, coordination, internal memory, and external memory.
- [Workflows](docs/workflows.md) - chat, light task, heavy task, and when to use each.
- [Benchmark Methodology](docs/benchmarks-methodology.md) - how paired `codex exec --json` runs are captured and compared.
- [FAQ](docs/FAQ.md) - design principles, objections, and comparisons.
- [External Memory Guide](docs/guides/use-external-memory.md) - separate memory repos for selected code repos.
- [Cost-aware Bootstrap](docs/guides/cost-aware-bootstrap.md) - model and wave-sizing choices for token-heavy repository bootstrap.
- [Settings Reference](docs/reference/settings-json.md) - memory-layer `system/settings.json` and MCP authority settings.
- [Skills Reference](docs/reference/skills.md) - the installed skill families.

## Repository Layout

```text
agents-remember-md/
  AGENTS.md                         # source checkout instructions
  README.md                         # public front door
  mcp/                              # package-local MCP server and services
    src/agents_remember/package_data/
      runtime/
        agents-md-files/            # installed AGENTS.md templates
        skills/                     # installed skill source tree
        system/defaults/examples/   # scaffold examples used by initialization
      benchmarks/                   # optional benchmark package source
  docs/                             # user-facing documentation
  roadmap/                          # design notes and historical planning
```

The installed runtime lives in `ar-coordination/`, not in the source checkout:

```text
ar-coordination/
  AGENTS.md
  skills/
  system/
  memory-repos/
  benchmarks/                       # optional, installed with --include-benchmarks
  tasks/
  notes/
  worktrees/
  temp/
```

## Status

Agents Remember is an active greenfield project. The public docs describe the current intended setup: install the runtime into `ar-coordination`, expose installed skills to your harness, and let repository memory live either in repo-local `ar-memory/` or in selected external memory repos.

## Contributing

Contributions should make the memory layer clearer, safer, and easier to apply consistently. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and keep the core rules intact: drift check before planning, approval before implementation, and onboarding updates only after approved changes.
