# Agents Remember Documentation

*Drift-aware repository memory for coding agents in complex codebases. Captures what code can't say on its own! Retrieves memory by path, semantics, and relationsship (code-graph).*

## Core Features

**Agents Remember gives coding agents project memory they can verify and act on.** It turns local invariants, naming rules, migration scars, cross-repo contracts, and "this looks safe but is not" facts into versioned Markdown beside the code, checks that memory against Git before use, and updates it only after approved work lands.

```text
src/orchestrator/core_editor.py
ar-memory/onboarding/src/orchestrator/core_editor.py.md
```

- **Path-addressed memory:** A source file's note lives at a deterministic mirror path, so an agent holding a file can reach the right context without search, ranking, or guesswork.
- **Git-proven freshness:** File notes, route overviews, and entity catalogs are drift-checked against source commits, route scopes, or deterministic fingerprints before they are trusted.
- **Search that finds, not decides:** Optional semantic memory and code-graph providers help locate relevant files, callers, dependencies, and concepts, but verified Markdown and source code remain the truth.
- **Memory that lands with code:** External memory repos use a `memory.md` ledger, isolated dual worktrees, preview/apply closeout, and all-or-nothing integration so code and memory stay synchronized.
- **Repo-owned agent behavior:** Each memory repo carries `system/` files for path rules, tools, coding guidelines, documentation sources, branch policy, and reporting shape, so the same project rules load across harnesses.
- **Harness-ready first run:** Starter packages for Claude Code, Codex, Cursor, Antigravity, VS Code Copilot, Hermes, Pi.dev, and OpenClaw carry the native MCP, skills, hooks, rules, and instruction files each harness needs.

The default setup stores durable memory in the target repository under `ar-memory/`. Teams that need separate memory repositories can use external memory under `ar-coordination/memory-repos/ar-<repo>/`. For the full tour, see [Features](docs/features.md).

## Start Here

- [Getting Started](getting-started.md) - the agent-driven setup path: copy the harness starter package, wire the MCP with `uvx`, restart once, then let `c-13-install-and-onboard` run `runtime_install` and set up memory, onboarding, and providers.
- [Features](features.md) - the concentrated product tour: by-path memory, drift, providers, worktrees, harness setup, and operational guardrails.
- [Concepts](concepts.md) - the memory model and the terms used throughout the project.
- [Architecture](architecture.md) - how the source checkout, installed runtime, coordination root, and memory roots fit together.
- [Workflows](workflows.md) - the `l-01-session-job-lifecycle` lifecycle, its build modes, and worktrees.
- [Benchmark Methodology](benchmarks-methodology.md) - paired benchmark runs, metrics, validity checks, and limitations.
- [FAQ](FAQ.md) - design principles, objections, and comparisons.

## Install Guides

- [Codex](install/codex.md)
- [Claude Code](install/claude-code.md)
- [Cursor](install/cursor.md)
- [Antigravity](install/antigravity.md)
- [VS Code + GitHub Copilot](install/vscode-copilot.md)
- [Hermes.md](install/hermes.md)
- [Pi.dev](install/pi.md)
- [OpenClaw](install/openclaw.md)

## Guides

- [Onboard an Existing Repo](guides/onboard-existing-repo.md)
- [Use External Memory](guides/use-external-memory.md)
- [Cost-aware Bootstrap](guides/cost-aware-bootstrap.md)
- [Refresh Stale Onboarding](guides/refresh-stale-onboarding.md)
- [Adopt Existing Memory](guides/adopt-existing-memory.md)
- [Carry Memory From a Branch](guides/carryover-from-branch.md)
- [Providers](guides/providers.md)

## Reference

- [MCP Tool Reference](reference/mcp-tools.md)
- [Runtime Layout](reference/runtime-layout.md)
- [settings.json](reference/settings-json.md)
- [Path Rules](reference/path-rules.md)
- [Skills](reference/skills.md)
- [c-08-ar-coordination-context-resolver Resolver](reference/resolver-c08.md)
- [c-02-memory-quality-control Memory Quality Control](reference/drift-c02.md)
- [c-09-git-worktree-manager Worktrees And Closeout](reference/worktrees-c09.md)
- [Release Checklist](release-checklist.md) - pre-release quality, version-sync, and smoke gate.
