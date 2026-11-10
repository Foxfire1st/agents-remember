# Install For Claude Code

Claude Code separates always-loaded project instructions from the MCP servers it starts. Agents
Remember needs one registration and one call; its skills arrive from the tool server, so nothing is
copied into a skill folder.

Official references:

- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code memory and CLAUDE.md](https://code.claude.com/docs/en/memory)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `mcpServers.agents-remember` entry in Claude Code’s MCP configuration.
The settings path is the absolute user-config path, not a workspace file.
Restart or reload the harness after changing its registration.

## Start With One Call

In a Claude Code chat, name no task and no repository:

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

Claude Code discovers project skills from folders it scans. Agents Remember creates none: the tool
server publishes each skill as an MCP resource (the SEP-2640 skills extension) and Claude Code reads
them from the running server, so there is no skill tree to copy, render or keep in sync.
