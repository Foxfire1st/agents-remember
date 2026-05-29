# Install For Antigravity

Antigravity is Google's Gemini-based agentic IDE and the successor to Windsurf. It discovers `AGENTS.md` files, supports native Skills, and supports session start hooks.

Reference:

- [Configuring MCP Servers and Skills for Antigravity](https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78)

## Workspace Instructions Or Start Hook

Because Antigravity supports start hooks, the preferred setup is a hook that injects `ar-coordination/AGENTS.md` as authoritative context at the start of every session. `C-13-install-and-onboard` installs that hook for you (it picks the hook for hook-capable harnesses and falls back to instruction placement otherwise).

If you set it up by hand, place an `AGENTS.md` at the workspace root as the fallback:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Add both the coordination runtime and target repository to the workspace when possible. If not, point the include at an absolute readable path.

## Skills

Install the runtime through the MCP server:

```text
runtime_install(dry_run=false)
```

Antigravity discovers skills in a workspace `.agents/skills/<skill-name>/SKILL.md` folder and in the global `~/.gemini/skills/<skill-name>/SKILL.md` folder. Its MCP configuration lives under `~/.gemini` rather than a `<root>/mcp/<settings>.json` sibling of the skills folder, so set `harnessSkillRoot` to the skills root you want (or register the MCP settings under a `.agents/mcp/` workspace folder so the sibling `.agents/skills/` is inferred).

Use the flat layout, because Antigravity expects the folder containing `SKILL.md` to match the skill's lowercase `name`:

```text
skills_install(layout="flat", dry_run=false)
```

Antigravity can invoke skills automatically or on request once they are discovered.
