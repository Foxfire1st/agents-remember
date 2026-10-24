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

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then add the server to `<workspace>/.mcp.json`:

```json
{
  "mcpServers": {
    "agents-remember": {
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.claude/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

`.claude/mcp/mcp.json` in this repository is that registration with
`<PATH/TO/YOUR/PROJECTS_FOLDER>` markers to replace by hand; no script renders it. Restart Claude
Code once so it starts the server.

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

## Optional: Session-Start Directive

A `SessionStart` hook is the reliable way to make the first-action directive authoritative in a
chat the product did not start. Copy `.claude/hooks/agents-remember-session-start.py` and its
sibling `.md` directive into your workspace `.claude/hooks/`, replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` in the directive with your absolute path, and write the hook into
`.claude/settings.json` with the interpreter you want:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<PYTHON_EXECUTABLE>",
            "args": [
              "<PATH/TO/YOUR/PROJECTS_FOLDER>/.claude/hooks/agents-remember-session-start.py"
            ]
          }
        ]
      }
    ]
  }
}
```

The directive it emits:

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

The directive is entry-only by design: the hook gives this text to a chat that the product did not
start, and says nothing to a role agent that already carries its hand-over. Free chat remains a
launcher rather than a global role identity. Claude Code snapshots `settings.json` hooks at session
start, so restart after writing the file.

### Fallback Without The Hook

If you cannot run a hook, add the same import to a root `CLAUDE.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

If `ar-coordination` is outside the workspace, point at the actual readable path. This loads the
doctrine, but only as optional context: expect weaker instruction following, and treat this as a
degraded fallback rather than an equivalent to the hook.
