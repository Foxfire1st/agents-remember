# Install For Cursor

Cursor supports native Agent Skills and recursively discovers skills from project and user roots.

Official reference: [Cursor Agent Skills](https://cursor.com/docs/skills.md).

## Persistent Instructions

Use either a root `AGENTS.md` or a Cursor project rule. In a sibling-repo workspace, a project rule is explicit.

Create `.cursor/rules/agents-remember.mdc`:

```markdown
---
description: Agents Remember memory system conventions
alwaysApply: true
---

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Use an absolute path when `ar-coordination` is outside the workspace.

## Skills

Install the runtime through the MCP server:

```text
runtime_install()
```

Cursor loads skills from `.agents/skills`, `.cursor/skills`, `~/.agents/skills`, and `~/.cursor/skills`; it also supports Claude and Codex compatibility folders. Place the MCP settings under the matching registration folder, such as `.cursor/mcp/` or `.agents/mcp/`, and the skill target is inferred as the sibling `skills/` folder.

Use the flat layout so each visible folder matches the lowercase skill `name`:

```text
skills_install(layout="flat")
```
