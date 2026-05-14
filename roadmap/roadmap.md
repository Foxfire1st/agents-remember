# Agents Remember Bootstrap Runtime Spec

## Status

Capture-first draft. This note records the current direction from
`agents-remember-md/roadmap/roadmap.md` and the immediate interpretation of that
direction. It is intentionally not yet a full design.

Created: 2026-05-13

## Purpose

The current bootstrap and runtime layout asks agents and users to reason across
too many filesystem surfaces:

- the `agents-remember-md` checkout
- the active `ar-coordination` folder
- harness-specific skill installation folders

The roadmap proposes reducing that operational surface so `ar-coordination`
becomes the installed runtime location agents actually use during work.

## Current Goal

Make the Agents Remember bootstrap procedure move or install all relevant
runtime files from the checkout into the `ar-coordination` folder.

The intended result is that agents mostly operate from one coordination-owned
runtime tree instead of mixing instructions, scripts, skills, examples, and
harness links across multiple roots.

The important interpretation of "move" is runtime-oriented: skills and Python
files should be rewritten so their natural execution spot is the coordinator
runtime, not the original checkout folder. The checkout may still package or
publish the source copies, but installed behavior should assume
`ar-coordination` as home.

## Scope For This Design Batch

This pass is about structure and runtime behavior, not a broad feature buildout.

Major goals:

- restructure the repository and coordinator layout with clean `AGENTS.md`
  separation
- make runtime features stop depending on execution from the checkout branch
- copy skills and scripts into `ar-coordination`, then expose them to harnesses
  through symlinks
- make sure existing Python scripts still work when placed under
  `ar-coordination`
- refactor the resolver only where needed for the coordinator-native runtime

Non-goals for this pass:

- verifier tooling
- install manifests
- migration machinery for old alpha layouts
- broad Python helper refactors unrelated to the new runtime home
- a full installer product or hidden orchestration wrapper
- exhaustive public documentation polish

## Current Pain

Executable and behavioral pieces are currently split across more places than
the system wants:

- skills live in the checkout but must be exposed to different code harnesses
- scripts live in the checkout but are already installed by symlink
- coordination artifacts live under `ar-coordination`
- public repository files such as docs and roadmap are useful for GitHub readers
  but add noise when treated as workspace runtime context

This makes setup and ongoing use feel heavier than the underlying task should
require.

## Target Shape

The repo checkout should keep public, development, and install-source concerns:

- public README and GitHub-facing documentation
- roadmap material
- source copies of installable runtime assets
- bootstrap scripts
- examples and templates needed to build a new coordination runtime

The `ar-coordination` folder should become the runtime home for:

- active workflow instructions
- coordinator-level `AGENTS.md` routing
- memory repo rules
- task and worktree rules
- installed skills
- installed scripts
- notes and operator-local design material
- user-owned system configuration

Harness-specific skill locations should receive symlinks that point into the
installed coordination runtime, not directly back into the checkout.

The existing skill symlink script can remain the harness exposure mechanism, but
its source should become the installed coordinator skill tree once that runtime
tree exists.

Install order:

1. Copy package-owned skills into `ar-coordination/skills/`.
2. Copy package-owned scripts into `ar-coordination/scripts/`.
3. Use symlinks from each code harness skill root into the coordinator copy.

Harnesses should never symlink directly back to the checkout as the normal
runtime path.

## Installation Direction

The installation should be scripted instead of relying on the model to perform
straightforward filesystem construction manually.

Initial installation must not depend on Agents Remember skills already working.
Before skills are installed into the coordinator and exposed to the harness,
there is a chicken-and-egg problem: the bootstrap skill itself may not be
available.

Therefore the install path should be two direct CLI calls:

1. install the runtime into `ar-coordination`
2. expose the installed coordinator skills to the harness with `install-skills.sh`

The user can run those commands manually, or ask an already-capable agent to run
them. The design should not assume the bootstrap skill exists before this is
done.

The runtime installer should stay minimal. Its input is the target coordination
root path.

Example shape:

```bash
python installer/install-runtime.py /path/to/ar-coordination
```

Then expose the installed skills with the existing skill installer:

```bash
scripts/install-skills.sh \
  --source /path/to/ar-coordination/skills \
  --install-root /path/to/harness/skills \
  --layout tree
```

Only change `install-skills.sh` where necessary to make that source work
cleanly. Do not replace it with a new harness installer in this pass.

The repo already has a script for exposing skills through symlinks. The missing
piece is a script that builds or updates the coordination runtime itself.

Installer concerns should be split into small focused scripts rather than a
single large Python script.

Preferred direction:

```text
agents-remember-md/
  installer/
    install-runtime.py
  scripts/
    install-skills.sh
```

Each script should own one concern:

- `install-runtime.py` copies or reconciles package-owned runtime assets into
  `ar-coordination`.
- `install-skills.sh` creates or refreshes harness skill symlinks that point to
  `ar-coordination/skills`.

No orchestration wrapper is needed. The user or agent can call the focused
scripts one by one, preserving clear responsibility boundaries without hiding
the phases.

Rule: separate install concerns for maintainability; avoid one giant bootstrap
script, avoid adding a wrapper that hides the phases, and avoid optional
installer luxuries in this pass.

## AGENTS.md Packaging Direction

`AGENTS.md` files should stop being tied only to their checkout locations.
Instead, the checkout should catalog installable agent instruction files under
an `agents-files/` package area.

Proposed checkout package layout:

```text
agents-files/
  memory-repos/
    AGENTS.md
  notes/
  scripts/
    AGENTS.md
  skills/
    AGENTS.md
  system/
    AGENTS.md
  tasks/
    AGENTS.md
  worktrees/
    AGENTS.md
  AGENTS.md
```

Proposed installed coordination layout:

```text
ar-coordination/
  memory-repos/
    AGENTS.md
  notes/
  scripts/
    AGENTS.md
  skills/
    AGENTS.md
  system/
    AGENTS.md
  tasks/
    AGENTS.md
  worktrees/
    AGENTS.md
  AGENTS.md
```

The root installed `AGENTS.md` should do entry routing. Deeper `AGENTS.md` files
should let agents progressively discover only the rules relevant to the area
they are entering.

## Resolver Direction

If more runtime state moves into `ar-coordination`, the resolver can likely be
simplified because fewer active files need to be discovered through the checkout.

Complete resolver removal is probably not realistic because coordination roots
may live outside the workspace, and agents still need one authoritative way to
resolve:

- the active coordination root
- memory roots
- task roots
- worktree context
- cross-repo permissions
- harness/runtime installation facts

The useful direction is therefore simplification, not assuming the resolver can
vanish entirely.

## Runtime Interpretation

The user-facing model should stay simple:

- `ar-coordination` is where the runtime lives
- harness skill roots expose that runtime to code harnesses

The earlier ambiguity around "move" is resolved at the execution level:
installed skills, helper scripts, and Python files should behave as if
`ar-coordination` is their normal home. They should not need to reach back into
the checkout for ordinary task execution.

The checkout remains the development and installation source, but public
documentation does not need to make users think in terms of "source package"
internals.

The project is still alpha, so compatibility and migration should not constrain
the design. Breaking the current layout is acceptable when it produces a cleaner
runtime model.

There is still a packaging question about how checkout assets become the
coordinator runtime. That question should be answered for clarity and
repeatability, not by building extra installer product features in this pass.

## Alpha Reset Assumption

The next implementation can choose a clean-state layout and make existing alpha
installations rebuild around it.

Design implications:

- Prefer the cleanest steady-state model over incremental compatibility.
- Do not spend design effort preserving old checkout-based execution paths.
- Do not build migration machinery unless it helps with current development
  testing.
- Let the bootstrap script fail loudly or require a clean reinstall when it
  detects an obsolete alpha layout.
- Treat documentation and installer clarity as more important than preserving
  old behavior.

## Current Behavior Checked

Current behavior was checked against the existing skills, helpers, onboarding,
and local coordinator shape while drafting this spec.

Current scaffold behavior:

- `C-00-initialize-coordination-root` is the first-run or repair scaffold skill.
- Default C-00 setup creates repo-local `ar-memory/` plus local
  `ar-coordination/`.
- Explicit external-memory C-00 setup can create or repair a per-repo external memory root
  under `ar-coordination/memory-repos/ar-<repo-name>/`.
- C-00 creates missing directories and starter files only; it must not overwrite
  existing coordination or memory files without approval.
- C-00 does not create onboarding content; `C-03` owns generated onboarding.
- Current C-00 coordinator directories are `system/`, `memory-repos/`,
  `tasks/`, `notes/`, and `worktrees/`.

Current resolver behavior:

- `C-08` resolves active context and reports roots, storage rules, task roots,
  worktree facts, and cross-repo allowances.
- `C-08` does not create missing scaffold or Git worktrees.

Current task and worktree behavior:

- W-02 creates durable task wrappers under the C-08 resolved task root as
  `<task-root>/<task-slug>/task.md`.
- C-09 places `contract.md` beside `task.md` when a task becomes
  worktree-backed.
- C-09 derives worktree groups under
  `ar-coordination/worktrees/<repo-name>/<worktree-name>-ar/`.
- C-09 may create or reuse worktrees and task contracts.
- C-09 cleanup is explicit, human-gated, and idempotent.

Current memory behavior:

- C-09 external-memory start blocks when no compatible memory state exists and
  asks for an explicit recovery choice.
- C-09 can bootstrap an external memory repo only when explicitly requested, such
  as `bootstrap-memory` or `start --memory-choice clean-start`.
- C-09 memory bootstrap initializes the repo if needed, creates `onboarding/`,
  `docs/`, and `system/`, writes missing starter system files, commits memory
  content, then writes and commits `memory.md`.
- Existing `memory.md` is treated as authoritative; C-09 reports
  `already-ledgered` instead of overwriting it.

Current coordinator gap:

- The local coordinator currently has the core folders and live memory/task
  state.
- It does not yet have installed coordinator-level `AGENTS.md` files.
- Skills and scripts still execute from the checkout today. Moving them into a
  coordinator-native runtime is the proposed alpha cleanup.

Design implication: the clean package bootstrap must not erase the existing
workflow boundaries. It should separate package/runtime installation from
repo-specific memory setup, task creation, worktree creation, and user-owned
configuration.

## Proposed Checkout Layout

The checkout should organize public documentation, development files, and
installable runtime assets.

```text
agents-remember-md/
  README.md
  AGENTS.md
  docs/
  roadmap/

  installer/
    install-runtime.py

  scripts/
    install-skills.sh

  runtime/
    agents-files/
      AGENTS.md
      memory-repos/AGENTS.md
      notes/AGENTS.md
      scripts/AGENTS.md
      skills/AGENTS.md
      system/AGENTS.md
      tasks/AGENTS.md
      worktrees/AGENTS.md

    scripts/
      ...

    skills/
      U-01-core-skills/
      W-01-heavy-task-workflow/
      W-02-light-task-workflow/
      W-03-chat-task-workflow/

    system/
      defaults/
        coordinator/
          settings.md
          settings.json
          sources.md
          tools.md
        memory-repo/
          settings.md
          settings.json
          sources.md
          tools.md
```

Repository-root `AGENTS.md` should describe how to work on the checkout
itself. Runtime `AGENTS.md` files should live under `runtime/agents-files/` so
the installer can place them into the coordinator runtime.

## Public Documentation Boundary

Public repository documentation should stay professional and focused on the
usable installation model.

Do not make the README explain that users are looking at a "source package" on
GitHub. The user-facing model is only:

- `ar-coordination` is the runtime
- harness skill roots expose that runtime to the user's code harness

Do not label the README with an `ALPHA` warning. The internal design can keep
the freedom to break old layouts, but public documentation should focus on the
current intended install and usage path.

## Proposed Installed Runtime Layout

The coordinator should become the natural execution home for Agents Remember.

```text
ar-coordination/
  AGENTS.md

  system/
    AGENTS.md
    settings.md
    settings.json
    sources.md
    tools.md

  scripts/
    AGENTS.md
    ...

  skills/
    AGENTS.md
    U-01-core-skills/
    W-01-heavy-task-workflow/
    W-02-light-task-workflow/
    W-03-chat-task-workflow/

  memory-repos/
    AGENTS.md
    ar-agents-remember-md/
      onboarding/
      docs/
      system/
      memory.md

  tasks/
    AGENTS.md

  worktrees/
    AGENTS.md

  notes/
    AGENTS.md
    ...

  temp/
```

Runtime-owned files should be installed from the package into this tree.
User-owned coordination content, such as system configuration, notes, task
state, memory repo content, and temporary files, should remain
coordinator-local data.

## Proposed Harness Adapter Layouts

Harness skill roots should become symlink-only adapters into the coordinator
runtime.

Tree or namespace-oriented harnesses:

```text
<codex-skills-root>/
  agents-remember-md -> /path/to/ar-coordination/skills

<claude-skills-root>/
  agents-remember-md -> /path/to/ar-coordination/skills
```

Flat skill-root harnesses:

```text
<windsurf-skills-root>/
  c-08-ar-coordination-context-resolver -> /path/to/ar-coordination/skills/U-01-core-skills/C-08-ar-coordination-context-resolver
  ...
```

The existing skill symlink script can remain responsible for this adapter step,
but it should use the installed coordinator skill tree as its source.

This is a two-step rule:

1. The installer copies skills into `ar-coordination/skills/`.
2. The harness adapter script symlinks from harness skill roots to
   `ar-coordination/skills/`.

The current checkout-based symlink source is an alpha implementation detail to
replace.

## Ownership Split

The clean model is:

```text
agents-remember-md/runtime/   # checkout-owned runtime assets to install
ar-coordination/              # installed live runtime
harness skill roots/          # symlink-only adapters
```

This split should guide future script rewrites:

- Skills and helper scripts execute from `ar-coordination`.
- Harnesses only discover symlinked skills.
- Harness symlinks point to coordinator-owned copies, not checkout sources.
- The checkout is not needed for ordinary task execution after bootstrap.
- The checkout remains needed for development, publishing, and reinstalling or
  updating the runtime.

## Python Script Relocation Boundary

Python helpers should not be broadly refactored in this pass.

The task is to place the scripts in their new coordinator home and verify that
they still work there. If a script breaks because it assumed the checkout as its
natural location, fix only the path assumption needed for coordinator-native
execution.

Allowed changes:

- update path assumptions that point at the old checkout location
- make scripts work from `ar-coordination/skills/` or
  `ar-coordination/scripts/`
- adjust tests or smoke checks to exercise the installed coordinator location
- refactor C-08 where needed because the resolver is the authoritative path and
  topology boundary

Avoid:

- blank-slate Python rewrites
- unrelated helper abstractions
- broad restructuring outside path/runtime functionality
- changing workflow behavior that already works today

## Idempotent Installation Contract

Bootstrap and update should be idempotent. Running the installer multiple times
with the same inputs should converge the coordinator to the same runtime state.

Idempotency should apply beyond the first install:

- missing runtime-owned files are created
- outdated runtime-owned files are replaced
- user-owned coordination data is left alone
- interrupted installs can be rerun without hand repair

This contract turns installation into a repeatable reconciliation step instead
of a one-time fragile setup action.

## Runtime Ownership Model

Runtime-owned assets are shipped by `agents-remember-md` and may be replaced by
bootstrap or update during this clean-structure pass.

Runtime-owned assets include:

- installed `skills/`
- skill-local Python helpers and shared Python modules
- coordinator-facing workflow scripts
- installed coordinator `AGENTS.md` files
- default system templates and examples shipped as reference material
- templates or examples needed by bootstrap and workflow creation

User-owned runtime data lives in `ar-coordination` but is not owned by the
package installer.

User-owned runtime data includes:

- `memory-repos/*`
- onboarding content
- `tasks/*`
- `worktrees/*`
- `notes/*`
- `temp/*`
- live `system/settings.md`
- live `system/settings.json`
- live `system/sources.md`
- live `system/tools.md`

This ownership split should guide every script:

- installer scripts reconcile package-owned runtime assets
- workflow scripts assume the coordinator-native runtime layout exists
- no script should treat checkout paths as required for ordinary task execution

## Notes Boundary

`ar-coordination/notes/` is user-owned workspace space.

Current workflows do not depend on note contents from `agents-remember-md`.
Therefore bootstrap should scaffold the folder when missing, but should not
install, overwrite, clean up, or depend on ordinary note Markdown files.

The only package-owned file that may belong under notes is an optional
`notes/AGENTS.md` if the system later needs local behavior rules for how agents
should treat notes.

Default rule:

```text
ar-coordination/notes/
  AGENTS.md    # optional package-owned behavior rule
  *.md         # user-owned
  daily/       # user-owned
```

Public docs, roadmap notes, and package design documentation should stay in the
checkout under package-owned documentation areas such as `docs/` and `roadmap/`.

## Memory Repos Boundary

Bootstrap should prepare the memory repository area, but it should not create or
overwrite repo-specific memory repositories automatically.

Package-owned top-level behavior:

```text
ar-coordination/memory-repos/
  AGENTS.md
```

User and workflow-owned memory data:

```text
ar-coordination/memory-repos/
  ar-<repo-name>/
    onboarding/
    docs/
    system/
    memory.md
```

Bootstrap responsibilities:

- create `ar-coordination/memory-repos/` when missing
- install or update `memory-repos/AGENTS.md`
- leave all `ar-<repo-name>/` folders alone

Repo-specific memory setup belongs to explicit workflows:

- `C-00` initializes memory scaffolding
- `C-03` can generate onboarding when desired
- `C-10` can adopt existing onboarding as the memory baseline

Rule: bootstrap prepares the memory-repo area; repo-specific memory content is
created only by explicit user or workflow action.

## Tasks And Worktrees Boundary

Bootstrap should prepare the task and worktree areas, but it should not create,
modify, or clean up task-specific or worktree-specific state.

Package-owned top-level behavior:

```text
ar-coordination/tasks/
  AGENTS.md

ar-coordination/worktrees/
  AGENTS.md
```

Workflow-owned state:

```text
ar-coordination/tasks/
  <repo-name>/<task-name>/

ar-coordination/worktrees/
  ...
```

Bootstrap responsibilities:

- create `ar-coordination/tasks/` when missing
- create `ar-coordination/worktrees/` when missing
- install or update `tasks/AGENTS.md`
- install or update `worktrees/AGENTS.md`
- leave existing task folders alone
- leave existing worktree state alone

Task and worktree contents should only be created or changed by explicit
workflows, especially task workflows and `C-09`.

## System Configuration Boundary

Live coordinator system configuration is user space.

The installer should scaffold the `ar-coordination/system/` folder, but it
should not silently create, decide, own, or overwrite the live configuration
files that control a user's workspace.

User-owned live files:

```text
ar-coordination/system/
  settings.md
  settings.json
  sources.md
  tools.md
```

Package-owned defaults and examples can still exist as reference material:

```text
agents-remember-md/runtime/system/defaults/
  coordinator/
    settings.md
    settings.json
    sources.md
    tools.md
```

The bootstrap script should do deterministic structure work:

- create `ar-coordination/system/` when missing
- leave existing live config alone
- report which live config files are missing

The bootstrap skill should handle the human-facing initialization:

- detect missing system files
- explain what each file controls
- ask the user the few necessary questions
- create initial user-owned files from templates or answers
- avoid overwriting live files later without explicit approval

Rule: shell bootstrap scaffolds structure; the bootstrap skill initializes user
configuration; the installer never silently owns live system settings.

## AGENTS.md Ownership

Installed `AGENTS.md` files are package-owned system behavior.

They are not user-owned configuration, because the system depends on them for
correct routing, workflow boundaries, and progressive rule discovery. If they
are missing or stale, the app can break or agents can follow the wrong
procedure.

Package-owned installed files:

```text
ar-coordination/AGENTS.md
ar-coordination/memory-repos/AGENTS.md
ar-coordination/notes/AGENTS.md
ar-coordination/scripts/AGENTS.md
ar-coordination/skills/AGENTS.md
ar-coordination/system/AGENTS.md
ar-coordination/tasks/AGENTS.md
ar-coordination/worktrees/AGENTS.md
```

User-specific behavior, preferences, and workspace notes belong in user-owned
Markdown and configuration files instead of modifying installed `AGENTS.md`
files.

Examples:

```text
ar-coordination/system/settings.md
ar-coordination/system/settings.json
ar-coordination/system/sources.md
ar-coordination/system/tools.md
ar-coordination/notes/*.md
```

The installer may replace package-owned `AGENTS.md` files during bootstrap or
update. If users need a customization hook, the package-owned `AGENTS.md` files
should route them to user-owned configuration or notes rather than being edited
directly.
