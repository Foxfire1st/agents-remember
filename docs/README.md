# Agents Remember Documentation

Use this directory when the README is not enough. The root README explains the product; these docs teach setup, concepts, workflows, and reference details.

## Start Here

- [Getting Started](getting-started.md) - the agent-driven setup path: wire the MCP with `uvx`, install the runtime and skills, then let `c-13-install-and-onboard` set up memory, onboarding, and providers.
- [Concepts](concepts.md) - the memory model and the terms used throughout the project.
- [Architecture](architecture.md) - how the source checkout, installed runtime, coordination root, and memory roots fit together.
- [Workflows](workflows.md) - the `l-01-session-job-lifecycle` lifecycle, its build modes, worktrees, and direct closeout.
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
