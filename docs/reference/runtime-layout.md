# Runtime Layout

The source checkout packages runtime assets under `runtime/`. The MCP `runtime_install` tool reconciles those assets into `ar-coordination/`.

## Source Checkout

```text
agents-remember-md/
  mcp/
    src/agents_remember/
      benchmarks/
      install/
      providers/
      mcp/
  runtime/
    agents-md-files/
      coordinator/AGENTS.md
      skills/AGENTS.md
      system/AGENTS.md
      tasks/AGENTS.md
    providers/
      requirements/
      patches/
    skills/
      U-01-core-skills/
      W-01-heavy-task-workflow/
      W-02-light-task-workflow/
      W-03-chat-task-workflow/
    system/defaults/examples/
  benchmarks/
```

## Installed Runtime

```text
ar-coordination/
  AGENTS.md
  providers/
    requirements/
    patches/
    _venvs/
    _bin/
    runners/
      <provider>/<instance-id>/
    data/
      <provider>/
    logs/
      <provider>/
  skills/
  system/
    AGENTS.md
  tasks/
    AGENTS.md
  memory-repos/
  benchmarks/
  notes/
  worktrees/
  temp/
```

## Runtime Install Contract

`runtime_install` owns package runtime assets only:

- installed coordinator `AGENTS.md` templates
- installed skills
- installed provider defaults, such as pinned requirement files and patch
  assets
- optional benchmark fixtures when `--include-benchmarks` is passed

It does not own live settings, notes, tasks, worktrees, normal memory repo content, temp files, onboarding content, or provider databases.

`ar-coordination/providers/` is provider runtime state. The source installer
reconciles package-owned defaults from `runtime/providers/` and preserves live
provider binaries, venvs, runner instances, data, and logs. Provider dependency
installation and full provider reinstall are MCP-owned operations driven by
MCP authority settings. Pinned requirements, patches, provider venvs, installed
binaries, logs, and per-instance runtime state must be recoverable. Durable
provider data lives under `ar-coordination/providers/data/`.

Provider dependencies and artifacts are coordination-owned runtime state. Package defaults live under `runtime/providers/` and install into `ar-coordination/providers/`, while live provider installs use runtime-owned binaries under `providers/_bin/`, Python venvs under `providers/_venvs/<provider>/`, and provider-family artifacts under `providers/runners/<provider>/`. For GrepAI, MCP-derived provider settings expand memory roots into workspace projects; managed mode mirrors those roots into `providers/runners/grepai/index-roots/` before launching GrepAI so its unavoidable per-project `.grepai/` config and symbol files stay under provider-owned runtime paths. GrepAI workspace config, runtime logs, state, cache, and mirrors live under `providers/runners/grepai/`, while user-facing logs live under `providers/logs/grepai/` and all roots share one lifecycle-owned PostgreSQL/pgvector Docker backend whose persistent data root is under `providers/data/grepai/postgres/`. For CodeGraphContext, MCP-derived provider settings expand code roots into per-repo instance roots under `providers/runners/codegraphcontext/<repo-id>/.codegraphcontext/` so `.env`, `config.yaml`, `.cgcignore`, logs, and state remain outside indexed source repositories. Those repo instances share one lifecycle-owned FalkorDB Docker backend whose persistent data root is under `providers/data/codegraphcontext/falkordb/`, and reinstall/update must preserve `providers/data/` and `providers/logs/` unless an explicit destructive lifecycle command is requested.

The Python provider lifecycle, provider setup, and benchmark runner behavior
lives under package-owned MCP modules; they are not installed into coordinator
runtimes and no parallel source-level `scripts/` execution route is kept.
Normal provider install/status/start flows go through MCP tools and
package-local provider modules.

When benchmark installation is enabled, `runtime_install` reconciles package-owned benchmark content under `ar-coordination/benchmarks/` and preserves only user-generated outputs under `ar-coordination/benchmarks/user-runs/`. Source benchmark content includes case manifests, prompts, author results, docs, and workspace templates, not pre-created workspaces. Generated benchmark workspaces are resettable state; normal user memory under `ar-coordination/memory-repos/` is not touched.

Each generated benchmark case workspace has one shared code checkout area under `workspaces/<case-id>/repos/` and one benchmark-local coordination root under `workspaces/<case-id>/ar-coordination/`. `prepare` renders `workspaces/<case-id>/AGENTS.md` from the benchmark template, then clones both pinned code repositories and pinned memory repositories into those resettable workspace locations. Variants are execution modes and result groups, not duplicated workspace trees.

## Skill Install Contract

`skills_install` copies packaged skills into a harness skill root. It is MCP-owned and does not create symlinks.

Default tree layout:

```text
<install-root>/agents-remember-md/
```

Flat layout:

```text
<install-root>/<skill-name>/
```

Use flat layout when a harness requires the folder containing `SKILL.md` to match the skill's lowercase frontmatter name.
