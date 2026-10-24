# Install For Antigravity

Antigravity supports workspace context files, workspace MCP configuration, plugins, execution
hooks, and workspace skills. Agents Remember needs the MCP registration and one call; its skills
arrive from the tool server as MCP resources, so no skill folder is copied into the workspace.

Official references:

- [Antigravity CLI Plugins & Skills](https://antigravity.google/docs/cli-plugins)
- [Antigravity IDE MCP](https://antigravity.google/docs/ide-mcp)
- [Antigravity IDE Rules](https://antigravity.google/docs/ide-rules)
- [Antigravity IDE Hooks](https://antigravity.google/docs/ide-hooks)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then add the server to the workspace `.agents/mcp_config.json`:

```json
{
  "mcpServers": {
    "agents-remember": {
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.agents/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

`.agents/mcp_config.json` in this repository is that registration with
`<PATH/TO/YOUR/PROJECTS_FOLDER>` markers to replace by hand; no script renders it. Antigravity IDE
also exposes a raw MCP config from its MCP settings UI, at `~/.gemini/config/mcp_config.json` for
the global file. Open `/mcp` in the CLI, or the IDE MCP Store's raw config view, to confirm the
server is visible after a restart.

## Start With One Call

In an Antigravity chat, name no task and no repository:

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

Antigravity discovers workspace skills from folders it scans; Agents Remember writes none. The
tool server publishes each skill as an MCP resource (the SEP-2640 skills extension) and Antigravity
reads them from the running server, so there is no skill tree to copy, render or keep in sync.

## Optional: Workspace Context File

Antigravity reads workspace `GEMINI.md` and `AGENTS.md` files, and also reads global
`~/.gemini/GEMINI.md`. Do not overwrite a repository's root `AGENTS.md` just to wire Agents
Remember; root `AGENTS.md` is project-specific.

Copy `.agents/GEMINI.md` into your workspace root as `GEMINI.md` and replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` with your absolute path:

```markdown
# Workspace Agent Instructions

Read and follow `<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md
```

If the workspace root already has a different `GEMINI.md`, merge the directive into it rather than
replacing the file. Add both the coordination runtime and the target repository to the workspace
when possible; if not, point the include at an absolute readable path.

Antigravity documents command hooks in `.agents/hooks.json` and a `PreInvocation` hook that can
inject transient context; it does not document a simple project-local `session_start` hook, so the
always-loaded context file is the instruction surface to rely on.
