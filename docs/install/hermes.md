# Install For Hermes.md

Hermes Agent supports project context files and a skills system.

Official references:

- [Hermes Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/)
- [Hermes Skills](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md)

## Workspace Instructions

Hermes uses `.hermes.md` or `HERMES.md` as highest-priority project context files, and also supports `AGENTS.md` and `CLAUDE.md`.

Use `AGENTS.md` for a shared cross-agent workspace:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Use `HERMES.md` if you want Hermes-specific priority over shared context files.

## Skills

Install the runtime through the MCP server:

```text
runtime_install()
```

Hermes local skills commonly live under `~/.hermes/skills/`. Place the MCP
settings under `~/.hermes/mcp/` to infer `~/.hermes/skills/`, or set
`harnessSkillRoot` to a category folder when you want one. `skills_install`
installs one flat folder per skill, each matching the skill name:

```text
skills_install()
```

You can also use `harnessSkillRoot` for a shared skills directory and add it to
`~/.hermes/config.yaml` when it does not follow the sibling-folder convention:

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
```
