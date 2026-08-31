# Install For Claude Code

Claude Code separates always-loaded project instructions from native skills.

- Use a `SessionStart` hook to load the coordinator first-action directive.
- Use `.claude/skills` or `~/.claude/skills` for Claude Code skills.

Official references:

- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Claude Code skills](https://code.claude.com/docs/en/skills)
- [Claude Code memory and CLAUDE.md](https://code.claude.com/docs/en/memory)

## Root Starter Package

The repository includes a Claude Code starter package at `.claude/`. Copy that
folder to your workspace root, then render the copied package. The
`.claude/render-starter` script is a convenience: with a single `--repo` list
such as `--repo my-app shared-lib`, it infers the workspace root from the copied
`.claude/` folder, fills path and repository placeholders, writes the Python
hook command/args, and validates that each requested repository exists. If you
prefer not to run the renderer, make those same replacements by hand and verify
that no placeholder tokens remain. Restart Claude Code once after rendering.
Also copy `.claude/mcp/mcp.json` to `<workspace>/.mcp.json`; Claude Code will
not detect the MCP registration if the file only lives under `.claude/mcp/`.

The package contains:

- `.claude/settings.json` - `SessionStart` hook registration.
- `.claude/hooks/agents-remember-session-start.py` and `.md` - Python startup
  hook that emits the directive as `additionalContext`.
- `.claude/mcp/mcp.json` - MCP registration template to copy to root
  `.mcp.json`.
- `.claude/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.claude/skills/` - Agents Remember skills in Claude Code's project skill
  root.

After the restart, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions

Load the coordinator first-action directive with the starter package's
`SessionStart` hook. This is the recommended setup for Claude Code, and the only
one that reliably makes the directive authoritative.

A `CLAUDE.md` import alone is not enough. Claude Code loads imported workspace
instructions as project context tagged with a "this context may or may not be
relevant" disclaimer, so the coordinator doctrine reads as optional and is easily
skipped. The hook instead injects the directive as authoritative
`additionalContext`.

Claude Code itself does not require `jq` for hooks. The starter package uses a
Python hook, and the renderer writes the local Python executable into
`.claude/settings.json`.

The starter package includes `.claude/hooks/agents-remember-session-start.md`
with the directive text:

```markdown
**Agents Remember — session start.**

If `AR_SPAWN_ROLE` is set, it must resolve to its canonical role file and arrive
with the plane-injected hosted-session identity. An unresolvable role or an
incomplete hosted identity fails closed; it never falls through to a pasted
brief or ambient free chat. With valid hosted identity—or when the first user
message is a role brief and no hosted identity was declared—the brief is the
session start.

Otherwise you are the developer-facing **free chat**: read
`ar-coordination/AGENTS.md`; answer research inline, or for ordinary role-shaped work create/resolve the
durable sprint and first leaf, compile the canonical architect brief, and call
`dispatch_agent` once on that sprint document with role `architect`.
```

The directive is entry-only by design: a spawned role's session start is the
role brief its orchestrating agent compiled, while free chat remains a launcher
rather than a global role identity. An explicit developer-declared task-seat
takeover instead targets the named role on its canonical task document. With no plane identity the call uses
ambient target-document/role-altitude validation; later hosted dispatch uses
plane identity and direct-child scope. Both pin the brief in one transaction,
and a plane refusal never falls back to ambient.

The starter package registers the hook in `.claude/settings.json`:

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

The hook reads the directive file and emits it as `additionalContext`, which
Claude Code injects as authoritative session context. This is what makes the
coordinator first action reliably run before any sibling-repo work.
`$CLAUDE_PROJECT_DIR` resolves to the folder that holds `.claude`; an absolute
path to the directive file works too.

Claude Code snapshots `settings.json` hooks at session start, so restart after
copying the package; confirm the directive appears as injected session context
on the new session.

### Fallback Without The Hook

If you cannot run a hook, add the same import to a root `CLAUDE.md`:

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

Claude Code is a **direct** skill-folder scanner: it discovers a skill only when
`SKILL.md` sits one level under the skill root, in a folder whose name matches the
skill's lowercase `name`. The copied starter package already provides one flat
folder per skill at `.claude/skills/<name>/SKILL.md`.

This produces, for example:

```text
.claude/skills/c-13-install-and-onboard/SKILL.md
.claude/skills/w-02-light-task-workflow/SKILL.md
```

Keep the copied package flat: one folder per skill directly under
`.claude/skills/`, named by the skill's lowercase frontmatter name. Claude Code
discovers that layout without recursive scanning.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.
