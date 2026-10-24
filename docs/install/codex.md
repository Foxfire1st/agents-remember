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

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then add the server to `~/.codex/config.toml`:

```toml
[mcp_servers.agents-remember]
command = "uvx"
args = [
    "--refresh-package",
    "agents-remember-mcp@latest",
    "--config",
    "<PATH/TO/YOUR/PROJECTS_FOLDER>/.codex/mcp/agents-remember-settings.json"
]
```

Replace the settings path with your absolute one. `.codex/mcp/agents-remember-settings.json` in
this repository is a template with `<PATH/TO/YOUR/PROJECTS_FOLDER>` and
`<YOUR_REPOSITORY_FOLDER_NAME>` markers to fill in by hand; no script renders it.

Restart Codex once so it starts the server.

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

## Optional: Workspace Instruction File

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember; root `AGENTS.md` is
project-specific.

If you want the free chat to load the first-action notice at session start, copy
`.codex/hooks/agents-remember-session-start.py` and its sibling `.md` directive into your
workspace's `.codex/hooks/`, replace `<PATH/TO/YOUR/PROJECTS_FOLDER>` in the directive with your
absolute path, and register the hook in `~/.codex/config.toml` with the interpreter you want:

```toml
[[hooks.SessionStart]]

[[hooks.SessionStart.hooks]]
type = "command"
command = "<PYTHON_EXECUTABLE> <PATH/TO/YOUR_PROJECTS_FOLDER>/.codex/hooks/agents-remember-session-start.py"
timeout = 60
statusMessage = "Loading Agents Remember workspace directive"
```

The hook emits this directive and stays silent for a role agent, which arrives with its own
hand-over:

```markdown
**Agents Remember — session start.**

You are the developer-facing **free chat**: read
`ar-coordination/AGENTS.md`. Answer a question inline; for work on a managed
repository, create or resolve the sprint document, write the request of the
developer into it, start an Architect with `role_start` (the call for an
Architect names no document), and tell the developer that the agent is in the
dashboard and which sprint document holds the request. Another role is started
this way only when the developer names the role and the task.
```
