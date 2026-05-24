# Skills Reference

Installed skills live under:

```text
ar-coordination/skills/
```

The source copies live under:

```text
agents-remember-md/mcp/src/agents_remember/package_data/runtime/skills/
```

## Workflow Skills

| Skill | Purpose |
| --- | --- |
| `W-03-chat-task-workflow` | Default current-session workflow. |
| `W-02-light-task-workflow` | Durable one-page task plan with approval gate and live checklist. |
| `W-01-heavy-task-workflow` | Full phased workflow for high-risk or explicitly heavy tasks. |

## Core Skills

| Skill | Purpose |
| --- | --- |
| `C-00-initialize-memory-repo` | Create or repair a target repository memory root. |
| `C-01-findings-capture` | Route durable findings to the right artifact. |
| `C-02-memory-quality-control` | Control memory quality with task-start drift checks, pre-code-commit missing-onboarding checks, and closeout quality gates. |
| `C-03-repo-bootstrap` | Bootstrap repo overviews, route-local overviews, and onboarding coverage. |
| `C-04-retrieval-strategy-router` | Choose Semantics, Relationship, or Intent retrieval, using providers for discovery and onboarding/source as proof. |
| `C-05-create-or-update-onboarding-files` | Create and maintain file-level onboarding and entity catalogs. |
| `C-08-ar-coordination-context-resolver` | Resolve memory, coordination, task, temp, and cross-repo facts. |
| `C-09-git-worktree-manager` | Manage worktree lifecycle, direct closeout, integration, and cleanup gates. |
| `C-10-adopt-memory-baseline` | Adopt existing external-memory onboarding into the first ledgered baseline. |
| `C-11-memory-carryover-from-branch` | Carry richer memory forward after matching code lands. |

## Heavy Workflow Phase Skills

The heavy workflow owns phase-local skills under:

```text
W-01-heavy-task-workflow/skills/
```

Those skills handle research, synthesis, design, planning, implementation, and adversarial checkpoint review. They should be used only through the heavy workflow unless a developer explicitly asks for that phase behavior.

## Installing Skills Into Harnesses

Use:

```text
skills_install(dry_run=false)
```

The install target is normally inferred from the MCP settings location:
`<registration-root>/mcp/<settings>.json` installs into
`<registration-root>/skills/`. Use `harnessSkillRoot` only for non-standard
harness layouts.

Use `layout="flat"` when the harness requires direct `<skill-name>/SKILL.md` folders.

Do not copy individual skill folders by hand. The MCP tool copies the packaged skill tree consistently, including sibling files and shared helper modules.
