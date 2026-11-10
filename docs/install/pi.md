# Install For Pi.dev

Pi supports context files, project settings, project-local extensions, and package-managed
extensions. Pi core has no built-in MCP client, so the project settings load `pi-mcp-adapter`.
Agents Remember needs that registration and one call; its skills arrive from the tool server, so
`.pi/settings.json` lists no skill folder.

Official references:

- [Pi Using Pi](https://pi.dev/docs/latest/usage)
- [Pi Settings](https://pi.dev/docs/latest/settings)
- [Pi Extensions](https://pi.dev/docs/latest/extensions)
- [Pi Packages](https://pi.dev/docs/latest/packages)
- [pi-mcp-adapter](https://pi.dev/packages/pi-mcp-adapter)

## Register The MCP Server

Use the [common registration example](../../examples/mcp/server.example.json) and the
[shared settings setup](README.md). Adapt its command and arguments to the `mcpServers.agents-remember` entry in the Pi MCP adapter’s configuration.
The settings path is the absolute user-config path, not a workspace file.
Pi needs `npm:pi-mcp-adapter`; configure that package through Pi’s package settings.
Restart or reload the harness after changing its registration.

## Start With One Call

In a Pi chat, name no task and no repository:

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

Pi loads skills from folders it scans; Agents Remember writes none. The tool server publishes each
skill as an MCP resource (the SEP-2640 skills extension) and Pi reads them from the running server
through the adapter, so its settings name no skill folder and there is no skill tree to copy,
render or keep in sync.

## Harness-created files

Pi agents can create `~/.pi/agent/mcp.json` in the user's home. Agents Remember's agents leave that file unchanged and report it, so the developer decides about it. Running `pi` with a worktree as its working folder creates `.pi/npm` there; Agents Remember runs harness commands only in its scratch folders and removes such a folder by its literal path.
