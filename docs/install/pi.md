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

Install the runtime:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

Pi loads skills from project `.pi/skills`, project `.agents/skills`, global `~/.pi/agent/skills`, global `~/.agents/skills`, settings entries, package entries, and repeated `--skill <path>` flags.

Use flat layout for Pi-native skill roots:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.pi/skills \
  --layout flat
```

For a shared project install:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.agents/skills \
  --layout flat
```

For global Pi skills:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.pi/agent/skills \
  --layout flat
```

Pi can discover `SKILL.md` directories recursively, but skill names should still match their parent folder for clean validation.
