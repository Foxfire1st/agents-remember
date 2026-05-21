# Runtime Layout

The source checkout packages runtime assets under `runtime/`. The installer reconciles those assets into `ar-coordination/`.

## Source Checkout

```text
agents-remember-md/
  installer/install-runtime.py
  runtime/
    agents-md-files/
      coordinator/AGENTS.md
      skills/AGENTS.md
      system/AGENTS.md
      tasks/AGENTS.md
    scripts/
      install-skills.sh
      provider-setup.py
      run-benchmarks.py
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
    <provider>/<instance-id>/
  provider-data/
    <provider>/
  scripts/
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

## Installer Contract

`installer/install-runtime.py` owns package runtime assets only:

- installed coordinator `AGENTS.md` templates
- installed skills
- installed scripts
- installed provider defaults, such as pinned requirement files and patch
  assets
- installed provider dependencies for enabled providers, unless
  `--skip-provider-deps` is passed
- optional benchmark fixtures when `--include-benchmarks` is passed

It does not own live settings, notes, tasks, worktrees, normal memory repo content, temp files, onboarding content, or provider databases.

`ar-coordination/providers/` is disposable provider scaffolding. Reinstall removes and recreates it from `runtime/providers/`, then installs dependencies for providers enabled in the live coordinator settings. Pinned requirements, patches, provider venvs, installed binaries, logs, and per-instance runtime state must be recoverable. Durable provider data lives outside that tree under `ar-coordination/provider-data/`.

Provider dependencies and artifacts are coordination-owned runtime state. Package defaults live under `runtime/providers/` and install into `ar-coordination/providers/`, while live provider installs use `providers/_venvs/<provider>/` and per-instance artifacts use `providers/<provider>/<instance-id>/`. For CodeGraphContext, `contextProviders.providers.codegraphcontext-code.roots` expands into per-repo instance roots under `providers/codegraphcontext/<repo-id>/.codegraphcontext/` so `.env`, `config.yaml`, `.cgcignore`, logs, and state remain outside indexed source repositories. Those repo instances share one lifecycle-owned FalkorDB Docker backend whose persistent data root is under `provider-data/codegraphcontext/falkordb/`, and reinstall/update must preserve `provider-data/` unless an explicit destructive lifecycle command is requested.

`scripts/provider-setup.py` is the shared provider orchestration layer. The
installer calls it for dependency installation only when live coordinator
settings enable providers, benchmark preparation calls it only when the
benchmark-local `settings.json` enables providers, and C-09 worktree start calls
it only when the target coordinator enables `codegraphcontext-code`. CGC preparation
first tries to seed the target backend by exporting a bundle from a source
coordinator, rewriting indexed paths to the target repository root, and loading
that bundle into the target backend; only an unconfigured or failed seed falls
back to `cgc refresh-all`. C-09 worktree preparation passes an isolated runtime
root under the worktree group, so the worktree gets its own provider runtime and
FalkorDB data under `worktrees/<repo>/<task>/provider-runtime/` while still
reusing the installed coordinator's CGC virtual environment, requirements, and
patches.

When benchmark installation is enabled, the installer reconciles package-owned benchmark content under `ar-coordination/benchmarks/` and preserves only user-generated outputs under `ar-coordination/benchmarks/user-runs/`. Source benchmark content includes case manifests, prompts, author results, docs, and workspace templates, not pre-created workspaces. Generated benchmark workspaces are resettable state; normal user memory under `ar-coordination/memory-repos/` is not touched.

Each generated benchmark case workspace has one shared code checkout area under `workspaces/<case-id>/repos/` and one benchmark-local coordination root under `workspaces/<case-id>/ar-coordination/`. `prepare` renders `workspaces/<case-id>/AGENTS.md` from the benchmark template, then clones both pinned code repositories and pinned memory repositories into those resettable workspace locations. Variants are execution modes and result groups, not duplicated workspace trees.

## Skill Adapter Contract

`runtime/scripts/install-skills.sh`, installed as `ar-coordination/scripts/install-skills.sh`, creates symlinks from harness skill roots back to the installed runtime.

Default tree layout:

```text
<install-root>/agents-remember-md -> <ar-coordination>/skills
```

Flat layout:

```text
<install-root>/<skill-name> -> <ar-coordination>/skills/<skill-directory>
```

Use flat layout when a harness requires the folder containing `SKILL.md` to match the skill's lowercase frontmatter name.
