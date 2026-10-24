# Getting Started

Agents Remember setup starts with one call after the MCP server is installed and registered
with your harness. Follow your [harness install guide](install/README.md) to wire the server;
restart the harness if it needs that to discover the MCP connection.

## Start Setup

From your own chat, call:

```text
role_start(role="bootstrap", request_id="<fresh UUID>")
```

The call prepares a missing coordinator scaffold and host, verifies the product plug-in and
starts one instructed Bootstrap seat. Its answer names that seat and the dashboard/chat where
you continue. It creates no task or other role. If preparation refuses, read the named stage,
actual effects and remaining host state, resolve that reason, and retry the same request UUID.

No skill folder, starter package or renderer is required. The MCP server serves canonical
skills from its package as resources. Optional harness-native instruction/hook files carry only
the first-action directive; the install guides describe them separately from server wiring.

## Example Workspace

```text
projects/
  agents-remember/       # source checkout when developing the product
  my-app/               # your code repository
  ar-coordination/      # runtime and local coordination
```

Skills remain in the server package, rather than in a harness or the coordination root.
The source checkout is not required by an installation built from a wheel.

## MCP Registration

Use the [install guide](install/README.md) for your harness and the build you installed. It
names the command and an absolute MCP settings path. A source-built Python3.14 wheel can be
registered with:

```json
{
  "command": "uvx",
  "args": [
    "--python", "3.14", "--from", "/absolute/path/to/built-wheel.whl",
    "agents-remember-mcp", "--config", "/absolute/path/to/agents-remember-settings.json"
  ]
}
```

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
existing file is reported without overwriting it.

For each repository, choose a new memory scaffold or an existing memory repository.
External memory is the only supported topology:

```text
ar-coordination/memory-repos/ar-my-app/
  system/
  onboarding/
  docs/
```

The removed repo-local `ar-memory/` layout is refused by its exact path; setup does not migrate
or recreate it. Existing memory is inspected and its baseline/state reported, with dirty or
unusable inputs kept distinct from an absent root. See [Use External Memory](guides/use-external-memory.md).

Setup authors no onboarding or knowledge and starts no other role. At exit it tells you how to
start an Architect, which organizes optional onboarding and the knowledge foundation as a
regular task, master or sprint. The substantive procedures remain in the package-served
`c-13-install-and-onboard`, `c-00-initialize-memory-repo` and `c-14-knowledge-bootstrap` references.

## Maintenance And Normal Work

`runtime_install(dry_run=true)` previews a runtime refresh; applying it reconciles managed
runtime files while preserving memory, tasks and user-owned state. It creates no skill copy.
Benchmark fixtures are optional (`include_benchmarks=true`), and local benchmark outputs are
preserved. Retired providers are reported as unused; setup starts no provider indexing or watcher.

After setup, the user-started Architect follows the repository's normal task and approval
workflow. Read verified onboarding alongside source, qualify drift, and keep code and memory
changes under their existing owners. Role agents follow their exact handovers. See
[Workflows](workflows.md), [Skills](reference/skills.md), and [Refresh Stale Onboarding](guides/refresh-stale-onboarding.md).
