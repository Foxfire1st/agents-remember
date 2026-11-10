# Install For Hermes.md

Hermes Agent supports project context files, MCP servers, and a skills system. Agents Remember
needs the MCP registration and one call; its skills arrive from the tool server as MCP resources,
so `~/.hermes/config.yaml` names no external skill folder.

Official references:

- [Hermes Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/)
- [Hermes Configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuration/)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `mcp_servers.agents-remember` entry in `~/.hermes/config.yaml`.
The settings path is the absolute user-config path, not a workspace file.
Restart or reload the harness after changing its registration.

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
