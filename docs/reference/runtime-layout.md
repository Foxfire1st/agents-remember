# Runtime Layout

The source checkout keeps canonical skills at repository root and packages
runtime and benchmark assets under `mcp/src/agents_remember/package_data/`. The
MCP `runtime_install` tool reconciles runtime assets into `ar-coordination/`; skills
remain package-served MCP resources and are never installed into that root or a harness.

## Source Checkout

```text
agents-remember/
  skills/                         # canonical skill source tree
  scripts/
    sync-skills.py                # regenerates the package-served skill tree
  mcp/
    src/agents_remember/
      install/
      package_data/
        runtime/
          agents-md-files/
            coordinator/AGENTS.md
            system/AGENTS.md
            tasks/AGENTS.md
          providers/
            requirements/
            patches/
          skills/
            c-00-initialize-memory-repo/   # generated copy of root skills/
            l-01-agent-lifecycles/
            w-02-light-task-workflow/
          system/defaults/examples/
        benchmarks/
      providers/
      mcp/
```

Edit `skills/` directly. Do not edit the generated skill tree in
`mcp/src/agents_remember/package_data/runtime/skills/` by hand; run `python3 scripts/sync-skills.py` after root skill edits.

## Installed Runtime

```text
ar-coordination/
  AGENTS.md
  providers/
    requirements/
    patches/
    runners/
      <provider>/<instance-id>/
    data/
      <provider>/
    logs/
      <provider>/
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

`runtime_install` reconciles these package runtime assets:

- installed coordinator `AGENTS.md` templates
- installed provider defaults, such as pinned requirement files and patch
  assets
- optional benchmark fixtures when `--include-benchmarks` is passed

After those steps it provisions the configured build-pinned host and product
Node from the packaged contract and whole npm lock, preserving a running host
when a transition requires a stop. It reads the shared host authority per call
and never writes live settings. It does not own notes, tasks, worktrees, normal
memory repo content, onboarding content or provider databases.

`ar-coordination/providers/` is leftover provider runtime state from older
installations. Both former providers are retired (MIK-R112), so no product path
installs, starts, indexes, or queries them; the tree survives only where an
older install left it. The source installer still reconciles package-owned
defaults from `agents_remember/package_data/runtime/providers/`, and a
dependency-skipped install preserves whatever Docker runner instances, image
build roots, data, and logs are already there. `runtime_install` never deletes
the provider tree: it reports the leftover folders with their sizes, and only
the explicit `agents-remember providers-remove` command removes them. Durable
provider data lives under `ar-coordination/providers/data/` until that removal.

Provider Docker topology is source-controlled package data. Stable Dockerfiles,
base Compose files, and override templates live under
`agents_remember/package_data/runtime/providers/`. Before retirement, lifecycle
code filled those templates with MCP-derived paths, ports, image tags, container
names, and repo roots at command time. Rendered Compose overrides were passed to
`docker compose -f <package-base> -f - ...` through stdin and were execution
input only: never written into `ar-coordination/` or the model workspace, with
status/debug payloads reporting only the rendered override hash plus
Compose/image/container state.

Provider artifacts from that period are coordination-owned runtime state under
`providers/runners/<provider>/`. For GrepAI, provider settings expanded memory
roots into workspace projects; managed mode mirrored those roots into
`providers/runners/grepai/index-roots/` before launching GrepAI, so its
per-project `.grepai/` config and symbol files stayed under provider-owned
runtime paths. GrepAI workspace config, runtime state, cache, and mirrors lived
under `providers/runners/grepai/`, user-facing logs under
`logs/providers/grepai/`, and all roots shared one lifecycle-owned
PostgreSQL/pgvector Docker backend whose persistent data root is
`providers/data/grepai/postgres/`. For CodeGraphContext, provider settings built
an `agents-remember/codegraphcontext:<pin>` runner image, ran bounded CGC
commands and watchers through Docker Compose as the host user when supported,
and expanded code roots into per-repo instance roots under
`providers/runners/codegraphcontext/<repo-id>/.codegraphcontext/` so `.env`,
`config.yaml`, `.cgcignore`, logs, and state stayed outside indexed source
repositories. Those repo instances shared one lifecycle-owned FalkorDB Docker
backend on the shared CGC Docker network whose persistent data root is
`providers/data/codegraphcontext/falkordb/`. Removal is explicit and reports
first: `providers-remove --dry-run` names the folders and their sizes and stops
nothing, while the same command without `--dry-run` removes the provider
folders, printing each folder's size and the total bytes freed.

The Python provider lifecycle code, provider setup, and benchmark runner behavior
live under package-owned MCP modules; they are not installed into coordinator
runtimes and no parallel source-level `scripts/` execution route is kept. No
provider install, status, or start flow remains. Three callers ask for a provider
stop, and none of them starts one:

- the old-watcher stop inside `runtime_install`, before the runtime tree is
  refreshed;
- the explicit `providers-remove` cleanup;
- the provider-degradation safety loop, which the dashboard's serving process
  runs for as long as the dashboard is up: every 30 seconds it samples the Docker
  state of the labelled provider containers, records the sample under
  `logs/observer/providers/` and compacts that log, evaluates the recent window,
  and — on the transition into `critical`, and only while
  `providerDegradation.failSafeEnabled` is true — calls the same watcher stop. It
  is not a revival: it starts nothing, installs nothing, indexes nothing and
  queries nothing, and it is independent of any install or cleanup.

All three read the same settings document for that decision: the authority
settings file's own `contextProviders` block, which still parses legacy provider
keys. A legacy settings file that enables a provider therefore gets that provider
stopped, whichever of the three callers runs — `runtime_install` before the
runtime tree is refreshed, `providers-remove` before it deletes the folders, or
the safety loop on the transition into `critical` under
`providerDegradation.failSafeEnabled`.

The generated lifecycle document is not that document: it still declares no
providers (`{"enabled": false, "providers": {}}`), because a generated install
must not re-create the retired provider block. It is what an install writes, not
what a stop reads. When the authority settings declare no provider, a stop selects
nothing, stops nothing, and the safety loop still records the attempt and its
result in the degradation event; `providers-remove` additionally refuses while a
live process still holds a provider folder.

When benchmark installation is enabled, `runtime_install` reconciles package-owned benchmark content under `ar-coordination/benchmarks/` and preserves only user-generated outputs under `ar-coordination/benchmarks/user-runs/`. Source benchmark content includes case manifests, prompts, author results, docs, and workspace templates, not pre-created workspaces. Generated benchmark workspaces are resettable state; normal user memory under `ar-coordination/memory-repos/` is not touched.

Each generated benchmark case workspace has separate `source-only/` and `with-memory/`
environments under `workspaces/<case-id>/`. Both contain the same pinned source checkout
under `repos/<repo>/`, a rendered `AGENTS.md` and a benchmark root marker. The memory-enabled
environment also has `ar-coordination/memory-repos/ar-<repo>/` and a `.codex/config.toml`
registration for its package MCP server. The runner forwards that registration to the
child process. See [benchmark methodology](../benchmarks-methodology.md).

## Skill Resource Contract

The MCP server serves its generated package tree as resources. `skill://index.json`
lists available skills; selected skill and supporting-file URIs read bytes from that
same installed build. There is no installed skill root or skill-install operation.

Root `skills/` is the authored source. `scripts/sync-skills.py` regenerates only the
package-serving tree at `mcp/src/agents_remember/package_data/runtime/skills/`;
installation and benchmark preparation create no harness or coordination-root skill copy.
