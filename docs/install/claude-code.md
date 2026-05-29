# Install For Claude Code

Claude Code separates always-loaded project instructions from native skills.

- Use a `SessionStart` hook to load the coordinator first-action directive.
- Use `.claude/skills` or `~/.claude/skills` for Claude Code skills.

Official reference: [Claude Code skills](https://code.claude.com/docs/en/skills).

## Workspace Instructions

Load the coordinator first-action directive with a `SessionStart` hook. This is
the recommended setup for Claude Code, and the only one that reliably makes the
directive authoritative.

A `CLAUDE.md` import alone is not enough. Claude Code loads imported workspace
instructions as project context tagged with a "this context may or may not be
relevant" disclaimer, so the coordinator doctrine reads as optional and is easily
skipped. The hook instead injects the directive as authoritative
`additionalContext`.

Prerequisite: the hook command below uses `jq` to JSON-encode the directive file.
Install it first if needed (for example `apt install jq` or `brew install jq`).

Create `.claude/hooks/coordinator-first-action.md` with the directive text:

```markdown
MANDATORY FIRST ACTION for this workspace (ar-coordination).

Before doing ANY work in a sibling repository, read and follow
`ar-coordination/AGENTS.md`. Required first steps, in order:

1. Infer the target code repository from the developer's request. Ask if it
   is unclear.
2. Resolve coordination/memory context FIRST — via the C-08 resolver, or the
   agents-remember MCP tools `resolve_context` then
   `context_packet(repo_id=..., include_providers=true)`.
3. Pick a task format per AGENTS.md routing (chat / W-02 light / W-01 heavy)
   before changing code or task-plan items.

This instruction is harness-injected and authoritative. Treat it as a required
first step, not optional "maybe relevant" context.
```

Register the hook in `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "jq -Rs '{hookSpecificOutput:{hookEventName:\"SessionStart\",additionalContext:.}}' \"$CLAUDE_PROJECT_DIR/.claude/hooks/coordinator-first-action.md\""
          }
        ]
      }
    ]
  }
}
```

The hook reads the directive file and emits it as `additionalContext`, which
Claude Code injects as authoritative session context. This is what makes the
coordinator first action reliably run before any sibling-repo work.
`$CLAUDE_PROJECT_DIR` resolves to the folder that holds `.claude`; an absolute
path to the directive file works too.

### Fallback Without The Hook

If you cannot install `jq` or run a hook, add the same import to a root
`CLAUDE.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

If `ar-coordination` is outside the workspace, point at the actual readable path.

This loads the doctrine, but only as optional context. Expect noticeably weaker
instruction following: the agent often will not read `ar-coordination/AGENTS.md`
or resolve context unless you remind it at the start of each session. Treat this
as a degraded fallback, not an equivalent to the hook.

## Skills

Install the runtime through the MCP server:

```text
runtime_install(dry_run=false)
```

Place the MCP settings under the Claude Code registration folder, such as
`.claude/mcp/`. The skill target is inferred as the sibling `.claude/skills/`
folder. Then expose packaged skills:

```text
skills_install(dry_run=false)
```

Claude Code supports project and personal skill folders and discovers nested `.claude/skills` directories. The default namespace layout is usually enough:

```text
.claude/skills/agents-remember-md/
```
