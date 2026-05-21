# Agents Remember

Path-derived, git-verified memory for coding agents.

Agents Remember helps coding agents carry project knowledge across sessions without relying on a vector store, a hidden service, or one giant context dump. The core idea is simple: when an agent touches a source file, it can find the matching onboarding note from the source path, verify that note against Git, and update it only after approved work lands.

```text
src/orchestrator/core_editor.py
ar-memory/onboarding/src/orchestrator/core_editor.py.md
```

That gives agents the context they usually lose: local invariants, cross-repo contracts, migration history, naming rules, and "this looks safe but is not" knowledge that rarely fits in a README or a docstring.

![Agents Remember system overview](agents-remember-infographic.png)

## Why It Exists

Modern coding agents can make clean, plausible edits while missing the project-specific rules that make those edits safe. A top-level instruction file can help, but it does not naturally reappear when the agent is deep in a file and deciding what to change.

Agents Remember makes that missing context local and verifiable. Instead of asking the agent to rediscover architecture from scratch, the repo carries small onboarding units that are versioned in Git and read beside the code they describe.

## Core Model

Agents Remember has three moving pieces:

- **Onboarding units:** Markdown notes derived from source paths. A file such as `src/foo/bar.ts` maps to `ar-memory/onboarding/src/foo/bar.ts.md` in the default repo-local mode.
- **Drift detection:** Before an agent trusts onboarding, `C-02-onboarding-drift-detection` checks whether the source changed since the onboarding was verified.
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

2. Install the runtime into `ar-coordination`.

   ```bash
   python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
   ```

   Reinstall recreates the package-owned runtime scaffold and installs
   dependencies for providers enabled in `ar-coordination/system/settings.json`.
   Use `--skip-provider-deps` only for a scaffolding-only repair.

   Benchmark fixtures are optional. To install or refresh them too:

   ```bash
   python3 agents-remember-md/installer/install-runtime.py ./ar-coordination --include-benchmarks
   ```

3. Expose the installed skills to your agent harness.

   ```bash
   ./ar-coordination/scripts/install-skills.sh \
     --install-root ./.agents/skills
   ```

   Use `--layout flat` only for harnesses that require direct
   `<skill-name>/SKILL.md` folders. Do not use flat layout for OpenClaw: keep
   skills linked to `ar-coordination/skills` and configure
   `skills.load.allowSymlinkTargets`. See [OpenClaw setup](docs/install/openclaw.md).

4. Add workspace instructions that point agents at the installed runtime.

   ```markdown
   # Workspace Agent Instructions

   Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
   Treat these rules as workspace instructions!

   @ar-coordination/AGENTS.md
   ```

5. Ask the agent to initialize memory for a target repository with `C-00-initialize-memory-repo`, then bootstrap initial onboarding with `C-03-repo-bootstrap`.

After that, normal work starts in chat mode. The agent resolves the active context with `C-08-ar-coordination-context-resolver`, checks drift with `C-02-onboarding-drift-detection`, reads relevant onboarding beside code, and updates onboarding after approved changes.

## Choose Your Agent

Different tools discover instructions and skills differently. Use the install page for your harness:

| Harness | Setup guide |
| --- | --- |
| Codex | [docs/install/codex.md](docs/install/codex.md) |
| Claude Code | [docs/install/claude-code.md](docs/install/claude-code.md) |
| Cursor | [docs/install/cursor.md](docs/install/cursor.md) |
| Windsurf | [docs/install/windsurf.md](docs/install/windsurf.md) |
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
- [Settings Reference](docs/reference/settings-json.md) - `system/settings.json` shape.
- [Skills Reference](docs/reference/skills.md) - the installed skill families.

## Repository Layout

```text
agents-remember-md/
  AGENTS.md                         # source checkout instructions
  README.md                         # public front door
  installer/install-runtime.py      # runtime installer
  runtime/
    agents-md-files/                # installed AGENTS.md templates
    scripts/install-skills.sh       # harness skill symlink adapter
    scripts/run-benchmarks.py       # optional benchmark runner
    skills/                         # installed skill source tree
    system/defaults/examples/       # scaffold examples used by initialization
  benchmarks/                       # optional benchmark package source
  docs/                             # user-facing documentation
  roadmap/                          # design notes and historical planning
```

The installed runtime lives in `ar-coordination/`, not in the source checkout:

```text
ar-coordination/
  AGENTS.md
  scripts/
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
