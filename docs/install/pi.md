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
runtime_install()
```

Pi loads skills from project `.pi/skills`, project `.agents/skills`, global `~/.pi/agent/skills`, global `~/.agents/skills`, settings entries, package entries, and repeated `--skill <path>` flags. Place the MCP settings under the matching registration folder, such as `.pi/mcp/` or `.agents/mcp/`, and the skill target is inferred as the sibling `skills/` folder.

Use flat layout for Pi-native skill roots:

```text
skills_install(layout="flat")
```

Pi can discover `SKILL.md` directories recursively, but skill names should still match their parent folder for clean validation.
