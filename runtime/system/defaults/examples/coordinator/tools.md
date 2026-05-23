# Coordinator Tools Example

Use this file for tools and commands that are useful across all or many code
repositories attached to this coordinator.

Repo-specific checks, commands, branch workflow, and code quality tools belong
in the selected memory layer's `system/tools.md`, not here.

## Global Commands

No global commands configured yet.

When the Agents Remember MCP settings enable context providers, use MCP tools
for normal status and install flows. The MCP settings file is the authority for
allowed repos, providers, workspace paths, and runtime roots.

Use `runtime_install` for runtime installation and provider dependency install.
Use `context_packet` for startup status, including provider status and runner
integrity. Provider lifecycle Python scripts are source/package-owned mechanics,
not installed coordinator runtime scripts.

Expected provider setup and lifecycle command shapes:

```bash
# MCP tools
runtime_install(dry_run=true, include_benchmarks=false, install_provider_deps=true)
context_packet(repo_id="<repoId>", include_providers=true)

# GrepAI memory provider native query after MCP install/status
<coordination_root>/providers/_bin/grepai search "<query>" \
  --workspace agents-remember-memory --json --compact --limit 5
```

Manual provider debugging should run through source/package-owned tooling with
explicit generated settings, not through coordinator-local Python scripts. The
normal agent path is the MCP tool surface.

The GrepAI lifecycle command reads `contextProviders.providers.grepai-memory`,
expands its workspace roots into explicit projects, ensures the shared
PostgreSQL/pgvector Docker backend is healthy, writes GrepAI workspace config
under `providers/runners/grepai/home/.grepai/workspace.yaml`, mirrors indexed memory
roots under `providers/runners/grepai/index-roots/` when `mirrorRoots` is enabled, and
records runtime state under `providers/runners/grepai/state/`. GrepAI must be launched through the
runtime-owned binary at `providers/_bin/grepai`; managed mode should not fall
back to a globally installed `grepai`.

The CGC lifecycle command reads `contextProviders.providers.codegraphcontext-code`,
expands its `roots` array into per-repo runtime instances, ensures the shared
FalkorDB Docker backend is healthy, applies `processEnvTemplate` for the selected
repo, and records resolved ports plus browser URL in provider state.
The MCP derives the coordinator root from authority settings and passes
generated lifecycle settings internally. Running `cgc start`
without `--repo-id` starts every configured CGC root;
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
