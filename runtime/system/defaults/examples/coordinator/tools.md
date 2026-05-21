# Coordinator Tools Example

Use this file for tools and commands that are useful across all or many code
repositories attached to this coordinator.

Repo-specific checks, commands, branch workflow, and coding tools belong in the
selected memory layer's `system/tools.md`, not here.

## Global Commands

No global commands configured yet.

When `system/settings.json` enables context providers, the provider lifecycle
tooling should expose bounded `status`, `start`, `stop`, `refresh`, and
`doctor` commands per provider instance. Until that tooling is installed,
agents may use configured providers manually only after checking the configured
root and keeping provider output small.

Use `provider-setup.py` for coordinator setup flows that need provider
dependencies, benchmark/worktree preparation, or CGC seed import. It is the
shared orchestration layer used by the installer, benchmark runner, and C-09
worktree manager; `provider-lifecycle.py` remains the lower-level per-provider
mechanic. Callers should enter provider setup only when the relevant
`settings.json` enables providers; otherwise they should skip provider setup.

Expected provider setup and lifecycle command shapes:

```bash
# Shared provider setup
python <coordination_root>/scripts/provider-setup.py install \
  --coordination-root <coordination_root>
python <coordination_root>/scripts/provider-setup.py prepare \
  --coordination-root <coordination_root> \
  --cgc-seed-source-coordination-root <source_coordination_root> \
  --cgc-seed-repo-id <repoId>

# GrepAI memory provider
python <coordination_root>/scripts/provider-lifecycle.py grepai backend-status
python <coordination_root>/scripts/provider-lifecycle.py grepai status
python <coordination_root>/scripts/provider-lifecycle.py grepai start
<coordination_root>/providers/_bin/grepai search "<query>" \
  --workspace agents-remember-memory --json --compact --limit 5

# CodeGraphContext relationship provider
python <coordination_root>/scripts/provider-lifecycle.py watchers status
python <coordination_root>/scripts/provider-lifecycle.py watchers start
python <coordination_root>/scripts/provider-lifecycle.py watchers shutdown-all

# CodeGraphContext-only debug/provider operations
python <coordination_root>/scripts/provider-lifecycle.py cgc apply-settings
python <coordination_root>/scripts/provider-lifecycle.py cgc status \
  --repo-id <repoId>
python <coordination_root>/scripts/provider-lifecycle.py cgc start
python <coordination_root>/scripts/provider-lifecycle.py cgc start \
  --repo-id <repoId>
python <coordination_root>/scripts/provider-lifecycle.py cgc shutdown-all
python <coordination_root>/scripts/provider-lifecycle.py cgc stop \
  --repo-id <repoId>
python <coordination_root>/scripts/provider-lifecycle.py cgc doctor \
  --repo-id <repoId>
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --repo-id <repoId> \
  --json \
  run -- analyze callers <symbol>
python <coordination_root>/scripts/provider-lifecycle.py cgc \
  --repo-id <repoId> \
  visualize --port 8000
```

The aggregate `watchers` command reads enabled providers from
`<coordination_root>/system/settings.json`; `watchers start` starts the GrepAI
memory workspace watcher and all configured CGC code watchers, and
`watchers shutdown-all` stops the managed watchers it owns.

`provider-setup.py prepare` installs enabled provider dependencies, refreshes
GrepAI memory when enabled, and for CGC first tries to export a `.cgc` bundle
from the source coordinator, rewrite indexed paths to the target repo root, and
load the rewritten bundle into the target backend. It falls back to
`cgc refresh-all` only when seeding is not configured or cannot be used.
Worktree starts should pass an isolated CGC runtime root so the worktree uses
its own FalkorDB backend instead of mutating the main coordinator backend.

The GrepAI lifecycle command reads `contextProviders.providers.grepai-memory`,
expands its workspace roots into explicit projects, ensures the shared
PostgreSQL/pgvector Docker backend is healthy, writes GrepAI workspace config
under `providers/grepai/home/.grepai/workspace.yaml`, mirrors indexed memory
roots under `providers/grepai/index-roots/` when `mirrorRoots` is enabled, and
records runtime state under `providers/grepai/state/`. GrepAI must be launched through the
runtime-owned binary at `providers/_bin/grepai`; managed mode should not fall
back to a globally installed `grepai`.

The CGC lifecycle command reads `contextProviders.providers.codegraphcontext-code`,
expands its `roots` array into per-repo runtime instances, ensures the shared
FalkorDB Docker backend is healthy, applies `processEnvTemplate` for the selected
repo, and records resolved ports plus browser URL in provider state.
The lifecycle script infers the coordinator root from its installed path and
defaults to `<coordination_root>/system/settings.json`. `--coordination-root`
is only needed for unusual runs against a different coordinator, and
`--from-settings` is only a debug override for testing an alternate settings
file. Running `cgc start` without `--repo-id` starts every configured CGC root;
pass `--repo-id` only for a single repo. Running `cgc stop`, `cgc stop-all`, or
`cgc shutdown-all` stops every configured CGC root; pass `--repo-id` to
`cgc stop` only for a single repo.
Running `cgc ... run -- <native cgc args>` executes a bounded native CGC query
with the managed provider environment for the selected repo. Put lifecycle
options such as `--repo-id`, `--json`, and any explicit `--coordination-root`
override before `run`; arguments after `--` are passed to CGC. Use
`cgc ... visualize --port <port>` for the long-running visualizer server; it is
a separate lifecycle command, not a native-query pass-through.

Long-running daemon actions such as `watchers start`, `watchers stop`,
`watchers shutdown-all`, `cgc start`, `cgc stop`, `cgc visualize`, and GrepAI
watcher start/stop/refresh must run from a durable host process namespace.
Lifecycle status reports `processNamespace` diagnostics, and daemon actions
refuse to run from sandboxes that advertise `--die-with-parent` because those
processes can be killed when the sandbox exits and host PIDs may not be visible
inside the sandbox.

Patch and containment checks are part of provider health. A CGC provider should
not be used in managed mode if indexing creates `.cgcignore`,
`.codegraphcontext`, reports, databases, or logs inside the indexed source
repository. A GrepAI provider should not be used in managed mode if indexing
creates `.grepai/` inside source repositories or durable memory roots.

Provider output is discovery evidence only. Source files, verified onboarding,
drift checks, branch validity, and approved memory promotion remain the proof
layer.

## Notes

Agents should resolve the target repository with C-08 before choosing task,
worktree, memory, or validation paths. Prefer memory-layer tool instructions
when a command is repository-specific.
