# Install For Windsurf

Windsurf Cascade discovers `AGENTS.md` files and supports native Skills.

Official references:

- [Windsurf Skills](https://docs.windsurf.com/windsurf/cascade/skills)
- [Windsurf AGENTS.md](https://docs.windsurf.com/windsurf/cascade/agents-md)

## Workspace Instructions

Place an `AGENTS.md` at the workspace root:

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

Windsurf workspace skills live in `.windsurf/skills/<skill-name>/SKILL.md`. Global skills live in `~/.codeium/windsurf/skills/<skill-name>/SKILL.md`. Windsurf also discovers `.agents/skills` and `~/.agents/skills` for cross-agent compatibility.

Use the flat layout:

```text
skills_install(install_root="/absolute/path/to/.windsurf/skills", layout="flat", dry_run=false)
```

For a shared project-level install:

```text
skills_install(install_root="/absolute/path/to/.agents/skills", layout="flat", dry_run=false)
```

For global Windsurf skills:

```text
skills_install(install_root="/absolute/path/to/.codeium/windsurf/skills", layout="flat", dry_run=false)
```

Cascade can invoke skills automatically or manually with `@skill-name`.
