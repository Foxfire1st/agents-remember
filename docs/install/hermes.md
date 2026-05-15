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

Install the runtime:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

Hermes local skills commonly live under `~/.hermes/skills/`. Use a category folder and flat layout so each visible skill folder matches the skill name:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.hermes/skills/agents-remember-md \
  --layout flat
```

You can also install into a shared skills directory and add it to `~/.hermes/config.yaml`:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.agents/skills/agents-remember-md \
  --layout flat
```

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
```
