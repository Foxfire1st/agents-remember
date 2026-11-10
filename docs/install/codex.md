# Install For Codex

Codex needs two things:

1. the Agents Remember MCP server in Codex config
2. one call that starts Bootstrap and returns its dashboard seat

Codex reads the skills from the tool server as MCP resources. Nothing is copied into the harness.

Official references:

- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Codex AGENTS.md instructions](https://developers.openai.com/codex/guides/agents-md)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `[mcp_servers.agents-remember]` table in `~/.codex/config.toml`.
The settings path is the absolute user-config path, not a workspace file.
Restart or reload the harness after changing its registration.

## Start With One Call

In a Codex chat, name no task and no repository:

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

The tool server publishes every skill as an MCP resource (the SEP-2640 skills extension). Codex
reads them from the running server, so there is no skill folder to create, copy or keep in sync. If
a chat does not see the skills, check that the server is running and that the client reads MCP
resources — not that a skill folder is missing.
