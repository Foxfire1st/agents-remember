# Runtime Layout

The source checkout packages runtime assets under `runtime/`. The installer reconciles those assets into `ar-coordination/`.

## Source Checkout

```text
agents-remember-md/
  installer/install-runtime.py
  runtime/
    agents-md-files/
      coordinator/AGENTS.md
      skills/AGENTS.md
      system/AGENTS.md
      tasks/AGENTS.md
    scripts/
      install-skills.sh
    skills/
      U-01-core-skills/
      W-01-heavy-task-workflow/
      W-02-light-task-workflow/
      W-03-chat-task-workflow/
    system/defaults/examples/
```

## Installed Runtime

```text
ar-coordination/
  AGENTS.md
  scripts/
  skills/
  system/
    AGENTS.md
  tasks/
    AGENTS.md
  memory-repos/
  notes/
  worktrees/
  temp/
```

## Installer Contract

`installer/install-runtime.py` owns package runtime assets only:

- installed coordinator `AGENTS.md` templates
- installed skills
- installed scripts

It does not own live settings, notes, tasks, worktrees, memory repo content, temp files, or onboarding content.

## Skill Adapter Contract

`runtime/scripts/install-skills.sh`, installed as `ar-coordination/scripts/install-skills.sh`, creates symlinks from harness skill roots back to the installed runtime.

Default tree layout:

```text
<install-root>/agents-remember-md -> <ar-coordination>/skills
```

Flat layout:

```text
<install-root>/<skill-name> -> <ar-coordination>/skills/<skill-directory>
```

Use flat layout when a harness requires the folder containing `SKILL.md` to match the skill's lowercase frontmatter name.
