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
cd <coordination_root>/memory-repos
grepai status --no-ui
grepai watch --status
grepai search "<query>" --json --compact --limit 5

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
  --coordination-root <coordination_root> \
  --repo-id <repoId> \
  --json \
  run -- analyze callers <symbol>
```

The aggregate `watchers` command reads enabled providers from
`<coordination_root>/system/settings.json`; `watchers start` starts the GrepAI
memory watcher and all configured CGC code watchers, and `watchers shutdown-all`
stops the managed watchers it owns.

`provider-setup.py prepare` installs enabled provider dependencies, refreshes
GrepAI memory when enabled, and for CGC first tries to export a `.cgc` bundle
from the source coordinator, rewrite indexed paths to the target repo root, and
load the rewritten bundle into the target backend. It falls back to
`cgc refresh-all` only when seeding is not configured or cannot be used.
Worktree starts should pass an isolated CGC runtime root so the worktree uses
its own FalkorDB backend instead of mutating the main coordinator backend.

The CGC lifecycle command reads `contextProviders.providers.codegraphcontext-code`,
expands its `roots` array into per-repo runtime instances, ensures the shared
FalkorDB Docker backend is healthy, applies `processEnvTemplate` for the selected
repo, and records resolved ports plus browser URL in provider state.
The lifecycle script defaults to `<coordination_root>/system/settings.json`.
`--from-settings` is only a debug override for testing an alternate settings
file. Running `cgc start` without `--repo-id` starts every configured CGC root;
pass `--repo-id` only for a single repo. Running `cgc stop`, `cgc stop-all`, or
`cgc shutdown-all` stops every configured CGC root; pass `--repo-id` to
`cgc stop` only for a single repo.
Running `cgc ... run -- <native cgc args>` executes a native CGC query with the
managed provider environment for the selected repo. Put lifecycle options such
as `--coordination-root`, `--repo-id`, and `--json` before `run`; arguments
after `--` are passed to CGC.

Patch and containment checks are part of provider health. A CGC provider should
not be used in managed mode if indexing creates `.cgcignore`,
`.codegraphcontext`, reports, databases, or logs inside the indexed source
repository.

Provider output is discovery evidence only. Source files, verified onboarding,
drift checks, branch validity, and approved memory promotion remain the proof
layer.

## Notes

Agents should resolve the target repository with C-08 before choosing task,
worktree, memory, or validation paths. Prefer memory-layer tool instructions
when a command is repository-specific.
