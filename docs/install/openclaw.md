# Install For OpenClaw

OpenClaw uses an agent workspace with workspace files, and MCP server definitions live in the
OpenClaw config registry under `mcp.servers`. Agents Remember needs that registration and one call;
its skills arrive from the tool server, so nothing is copied into a workspace skill folder.

Official references:

- [OpenClaw Agent Workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- [OpenClaw MCP](https://docs.openclaw.ai/cli/mcp)
- [OpenClaw Configuration Reference](https://docs.openclaw.ai/gateway/configuration-reference)
- [OpenClaw Gateway](https://docs.openclaw.ai/cli/gateway)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `mcp.servers.agents-remember` entry in `~/.openclaw/openclaw.json`.
The settings path is the absolute user-config path, not a workspace file.
Restart or reload the harness after changing its registration.

## Start With One Call

In an OpenClaw chat, name no task and no repository:

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

OpenClaw loads skills from the places it scans; Agents Remember writes none there. The tool server
publishes each skill as an MCP resource (the SEP-2640 skills extension) and OpenClaw reads them from
the running server, so there is no workspace skill copy to keep current and no skill-sync step to
follow.

## Long-running Turns

Deep reasoning and large onboarding waves usually fit inside OpenClaw's current agent runtime
timeout. If a local or remote model stops streaming long enough to hit the provider idle watchdog,
prefer a provider-scoped timeout:

```json5
{
  models: {
    providers: {
      "<provider-id>": {
        timeoutSeconds: 300
      },
    },
  },
}
```

Use `agents.defaults.timeoutSeconds` only when the entire embedded agent run needs a different
runtime cap. Restart the gateway after config changes that do not hot-apply:

```bash
openclaw gateway restart
```
