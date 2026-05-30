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

Ask your agent to:

1. **Wire the MCP server** — Install Agents Remember MCP from [PyPI](https://pypi.org/project/agents-remember-mcp/) with `uvx`:

   ```text
   uvx agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
   ```

   Have your agent follow the PyPI link for setup details and help you author the settings file. Then **restart the harness** so it loads the server.
2. **Install Agents Remember** — Run the mcp tool `runtime_install`, then `skills_install` (scaffolding, skills, and provider images when providers are enabled). Then **restart the harness** again so it discovers the skills `skills_install` just wrote (some harnesses hot-reload skills and skip this; restarting is the safe default).
3. **Onboard your project** — Run the skill `C-13-install-and-onboard`. It pre-checks the setup, installs the start hook (or places the directive for harnesses without one), sets up the memory repo (it asks: scaffold a new one or use an existing one), bootstraps onboarding, and starts the providers indexing.

Those two restarts are the only hands-on steps; between and after them, your agent continues on its own.

After that, normal work starts in chat mode. The agent resolves the active context with `C-08-ar-coordination-context-resolver`, checks memory quality with `C-02-memory-quality-control`, reads relevant onboarding beside code, and updates onboarding after approved changes.

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
        providers/                  # provider runtime assets (images, runners)
        system/defaults/examples/   # scaffold examples used by initialization
      benchmarks/                   # optional benchmark package source
  docs/                             # user-facing documentation
```

The installed runtime lives in `ar-coordination/`, not in the source checkout:

```text
ar-coordination/
  AGENTS.md
  skills/
  system/
  memory-repos/
  providers/                        # provider runtimes (images, runners, indexes)
  benchmarks/                       # optional, installed with --include-benchmarks
  tasks/
  notes/
  worktrees/
  temp/
```

## Status

Agents Remember is at `0.9.1` (pre-1.0) and actively developed. The core path — by-path onboarding, drift checks, and approval-gated updates — is in real use and stable enough to rely on. Interfaces below that core (MCP tool names and arguments, skill set, runtime layout, and the optional semantic/relationship providers) may still change between releases, so pin a version and read the release notes before upgrading. The Claude Code path is the most exercised; other harnesses are supported but less battle-tested.

## Contributing

Contributions should make the memory layer clearer, safer, and easier to apply consistently. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and keep the core rules intact: drift check before planning, approval before implementation, and onboarding updates only after approved changes.
