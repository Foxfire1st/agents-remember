# Install For Pi.dev

Pi supports `AGENTS.md`/`CLAUDE.md` context and Agent Skills.

Official reference: [Pi Skills](https://pi.dev/docs/latest/skills).

## Workspace Instructions

Create `AGENTS.md` at the shared workspace root:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Pi also supports global context under `~/.pi/agent/AGENTS.md`.

## Skills

Install the runtime through the MCP server:

```text
runtime_install(dry_run=false)
```

Pi loads skills from project `.pi/skills`, project `.agents/skills`, global `~/.pi/agent/skills`, global `~/.agents/skills`, settings entries, package entries, and repeated `--skill <path>` flags.

Use flat layout for Pi-native skill roots:

```text
skills_install(install_root="/absolute/path/to/.pi/skills", layout="flat", dry_run=false)
```

For a shared project install:

```text
skills_install(install_root="/absolute/path/to/.agents/skills", layout="flat", dry_run=false)
```

For global Pi skills:

```text
skills_install(install_root="/absolute/path/to/.pi/agent/skills", layout="flat", dry_run=false)
```

Pi can discover `SKILL.md` directories recursively, but skill names should still match their parent folder for clean validation.
