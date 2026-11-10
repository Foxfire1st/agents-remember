# Install For VS Code + GitHub Copilot

VS Code supports always-on repository instructions, workspace MCP configuration, and agent hooks
for GitHub Copilot Chat. Agents Remember needs the MCP registration and one call; its skills arrive
from the tool server as MCP resources, so no skill folder is copied into the workspace.

Official references:

- [Use custom instructions in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)
- [Agent hooks in VS Code](https://code.visualstudio.com/docs/copilot/customization/hooks)
- [Add and manage MCP servers in VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `servers.agents-remember` entry in VS Code’s MCP configuration.
The settings path is the absolute user-config path, not a workspace file.
Restart or reload the harness after changing its registration.

## Start With One Call

In a Copilot Chat session, name no task and no repository:

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

VS Code discovers project skills from folders it scans; Agents Remember writes none. The tool
server publishes each skill as an MCP resource (the SEP-2640 skills extension) and Copilot reads
them from the running server, so there is no skill tree to copy, render or keep in sync.
