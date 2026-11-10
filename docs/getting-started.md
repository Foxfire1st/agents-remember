# Getting Started

Agents Remember setup starts with one call after the MCP server is installed and registered
with your harness. Follow your [harness install guide](install/README.md) to wire the server;
restart the harness if it needs that to discover the MCP connection.

## Start Setup

Before any runtime or memory step, Bootstrap reports a short pass-or-fail checklist: the MCP
server answers `server_info`, the intended code repository and its absolute root resolve, and
the coordination root and memory state are known. Each failed check names its exact fix, and
setup stops when the developer must act.

From your own chat, call:

```text
role_start(role="bootstrap", request_id="<fresh UUID>")
```

The call prepares a missing coordinator scaffold and host, verifies the product plug-in and
starts one instructed Bootstrap seat. Its answer names that seat and the dashboard/chat where
you continue. It creates no task or other role. If preparation refuses, read the named stage,
actual effects and remaining host state, resolve that reason, and retry the same request UUID.

The MCP setup tools are the normal installed entry points. `memory_init` creates durable
repository memory. `runtime_install` installs or refreshes the coordinator scaffold. Neither
creates onboarding content, task worktrees or skill folders. For any effectful run, call once
with `dry_run=true` to inspect the plan, confirm it, then run the real apply without `dry_run`.

No skill folder, starter package or renderer is required. The MCP server serves canonical
skills from its package as resources. Use the common server registration from the install guide.

## Example Workspace

```text
projects/
  agents-remember/       # source checkout when developing the product
  my-app/               # your code repository
  ar-coordination/      # runtime and local coordination
```

The runtime scaffold under the coordination root holds `tasks/` and `memory-repos/`. It carries
no `AGENTS.md` and no skills copy, so a missing skill folder is expected: the tool server serves
the skills from its package. The source checkout is not required by an installation built from
a wheel.

## MCP Registration

Use the [install guide](install/README.md) and adapt the
[common registration example](../examples/mcp/server.example.json) to your harness.
The tool-server settings live at `~/.config/agents-remember/agents-remember-settings.json`
(under `XDG_CONFIG_HOME` when set); use the absolute path in the registration.

The MCP settings name your absolute `workspaceRoot`, `coordinationRoot`, and repository
allow-list; see the [settings reference](reference/settings-json.md). This authority file is
separate from the user settings file that records the harness choice for each role.

Bootstrap creates a missing `paseoRuntime` block in the active MCP authority file. An existing
block stays unchanged; an invalid or unusable host is reported instead of silently replaced.
There is no renderer or instruction to move a host block into the user's role-settings file.

## Continue With Bootstrap

In the dashboard conversation, choose the repositories and the harness each role should use.
Harness-only choices leave the model and effort unset so that harness's own defaults apply.
Bootstrap previews the settings and exclusively creates an absent user settings file; an
existing file is reported without overwriting it. When the developer skips the settings
interview, setup confirms that the built-in defaults apply and says so.

External memory is the only supported topology: the memory repository lives under the
coordination root, never inside the code repository. Ask whether the repository starts a new
memory scaffold or uses an existing memory repository, unless `context_packet` already resolves
one; then report the resolved state instead of asking.
Before asking the developer to confirm anything, explain the memory in plain words a first-time
reader can follow:

- **What it is.** A second Git repository, separate from the code and created at one absolute
  path, `<coordination-root>/memory-repos/ar-<repository-name>`. It holds written explanations
  of how the code works and the decisions behind it; it is not a copy of the code. It sits
  outside the code repository so it can be versioned and shared on its own.
- **What setup creates.** `system/` for the memory's own instructions and machine settings,
  `docs/` for local reference material, and `onboarding/` for optional written cards.
- **What the ledger is.** A generated table pairing a commit in the code repository with the
  memory content written about that commit. It is produced automatically, never hand-written,
  and appears only after the first memory-content commit, called the memory baseline. Adopting
  that baseline is the step setup asks about.
- **Why the branch matters.** The memory is created and checked against the state of the branch
  the developer names. That name is recorded, and later steps compare themselves to it. Moving
  to another branch afterwards is a separate, deliberate procedure.

For a new memory repo, ask which code branch the memory is built on, default it to the branch
the code repository has checked out, and give the developer the absolute memory path. Pass the
answer to `memory_init` as `initial_branch`; when neither the argument nor the checked-out
branch is available, the call refuses rather than inventing a name. Initialize the memory root,
then adopt the first attributed baseline, in that order:

```text
memory_init(repo_id="<repo-id>", dry_run=true, initialize_git=true)          # preview
memory_init(repo_id="<repo-id>", initialize_git=true, initial_branch="<branch>")
memory_baseline_status(repo_id="<repo-id>")
memory_baseline_adopt(repo_id="<repo-id>", accept_drift=false, dry_run=true) # preview
memory_baseline_adopt(repo_id="<repo-id>")                                   # apply
```

For an existing memory repo, inspect it and report its baseline and knowledge state before
adopting anything. An existing memory is exactly the case that can hold onboarding and no
recorded knowledge foundation, so report both facts. Never write over existing memory.

The removed repo-local `ar-memory/` layout is refused by its exact path; setup does not migrate
or recreate it. Existing memory is inspected and its baseline/state reported, with dirty or
unusable inputs kept distinct from an absent root. See [Use External Memory](guides/use-external-memory.md).

When context resolution reports a missing memory location, it names the path it checked. Ask the
developer whether to initialize memory, explain that this creates the scaffold and settings,
and continue with the memory-root step only after that answer.

Setup authors no onboarding or knowledge and runs no citation fixer. It reads and reports any
existing onboarding and the repository's knowledge state, then tells the developer how to start
an Architect, which organizes optional onboarding and the knowledge foundation as a regular
task, master or sprint. A repository whose memory has a commit may still report `not-recorded`
when setup ends: that means it is set up for ordinary work with no recorded intent for a planner
to read, and the foundation is the Architect's first piece of work. `recorded` names the identity
that is there, and `unusable` is reported with its code and path; nothing is written over it.

At exit, say whether the project is ready for normal work, state the knowledge foundation's
state in the same sentence, and name the Architect as the next step for it. Setup never reports
a repository's knowledge as ready.

## Maintenance And Normal Work

`runtime_install(dry_run=true)` previews a runtime refresh; applying it reconciles managed
runtime files while preserving memory, tasks and user-owned state. Use the preview when the
developer asks for one or when local workflow policy requires it; otherwise apply the refresh so
setup can continue. It creates no skill copy, and it does not install skills as files. Verify the
installed package-owned runtime files against the served build; a missing skill folder is
expected and is not a defect. Benchmark fixtures are optional (`include_benchmarks=true`), and
local benchmark outputs are preserved. Retired providers are reported as unused; setup starts no
provider indexing or watcher and never polls a retired provider.

The user settings file is created once when absent. Its absent families use the built-in
defaults: no concurrency caps, no spawn harness preference, and no role harness or model choice.
Report the file's path and the outcome, and never overwrite, merge or complete an existing file.
A developer-requested local override stays in that file and is reported as the developer's, not
written by setup.

After setup, the user-started Architect follows the repository's normal task and approval
workflow. Read verified onboarding alongside source, qualify drift, and keep code and memory
changes under their existing owners. Role agents follow their exact handovers. See
[Workflows](workflows.md), [Skills](reference/skills.md), and [Refresh Stale Onboarding](guides/refresh-stale-onboarding.md).
