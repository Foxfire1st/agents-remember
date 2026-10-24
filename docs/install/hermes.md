# Install For Hermes.md

Hermes Agent supports project context files, MCP servers, and a skills system. Agents Remember
needs the MCP registration and one call; its skills arrive from the tool server as MCP resources,
so `~/.hermes/config.yaml` names no external skill folder.

Official references:

- [Hermes Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/)
- [Hermes Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then merge this into `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  agents-remember:
    command: "uvx"
    args:
      - "--refresh-package"
      - "agents-remember-mcp@latest"
      - "--config"
      - "<PATH/TO/YOUR/PROJECTS_FOLDER>/.hermes/mcp/agents-remember-settings.json"
```

`.hermes/config.yaml` in this repository is that snippet with the marker to replace by hand; no
script renders it. If you edit MCP configuration while Hermes is running, use `/reload-mcp`.

## Start With One Call

In a Hermes chat, name no task and no repository:

```text
role_start(role="bootstrap", request_id="<a UUID you generate>")
```

That one call prepares the runtime scaffold, provisions and starts the Paseo host, verifies the
Agents Remember plug-in, and starts one instructed Bootstrap seat. Its answer names the seat and
the dashboard address of its chat; continue setup there. A retry with the same `request_id`
reconciles to the same seat instead of a second one.

Bootstrap ends at memory setup and tells you to start the Architect, which organises the knowledge
foundation as ordinary task work. Bootstrap starts no role and authors no onboarding or knowledge.

## Skills

Hermes reads the Agents Remember skills from the tool server: each skill is published as an MCP
resource (the SEP-2640 skills extension) by the running server. That is the served copy, the
package's own, so what a session reads belongs to one installed build and no external skill folder
is added to `~/.hermes/config.yaml`.

## Optional: Project Context File

Hermes uses `HERMES.md` (or `.hermes.md`) as its highest-priority project context file, and also
supports `AGENTS.md` and `CLAUDE.md`.

Copy `.hermes/HERMES.md` to your workspace root as `HERMES.md` and replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` with your absolute path:

```markdown
# Workspace Agent Instructions

Read and follow `<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` as workspace
instructions.

@<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md
```

If the workspace root already has a different `HERMES.md`, merge the directive into it. Do not
overwrite a repository's root `AGENTS.md` just to wire Agents Remember; root `AGENTS.md` is
project-specific.
