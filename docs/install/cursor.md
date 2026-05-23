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
runtime_install(dry_run=false)
```

Cursor loads skills from `.agents/skills`, `.cursor/skills`, `~/.agents/skills`, and `~/.cursor/skills`; it also supports Claude and Codex compatibility folders.

Use the flat layout so each visible folder matches the lowercase skill `name`:

```text
skills_install(install_root="/absolute/path/to/.cursor/skills", layout="flat", dry_run=false)
```

For a shared cross-agent project install:

```text
skills_install(install_root="/absolute/path/to/.agents/skills", layout="flat", dry_run=false)
```

For user-wide Cursor skills:

```text
skills_install(install_root="/absolute/path/to/.cursor/skills", layout="flat", dry_run=false)
```
