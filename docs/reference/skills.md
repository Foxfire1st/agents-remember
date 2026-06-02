# Skills Reference

Installed skills live under:

```text
ar-coordination/skills/
```

The source copies live under:

```text
agents-remember-md/mcp/src/agents_remember/package_data/runtime/skills/
```

## Lifecycle And Workflow Skills

| Skill | Purpose |
| --- | --- |
| `l-01-session-job-lifecycle` | The session job lifecycle the coordinator routes every session into (orient → ground → frame → decide → build → close); owns the read-only exit and the build-mode decision. |
| `w-02-light-task-workflow` | Durable one-page task plan with approval gate and live checklist; escalates to a master + light sub-task series for larger work. |

## Core Skills

| Skill | Purpose |
| --- | --- |
| `c-00-initialize-memory-repo` | Create or repair a target repository memory root. |
| `c-01-findings-capture` | Route durable findings to the right artifact. |
| `c-02-memory-quality-control` | Control memory quality with task-start drift checks, pre-code-commit missing-onboarding checks, and closeout quality gates. |
| `c-03-repo-bootstrap` | Bootstrap repo overviews, route-local overviews, and onboarding coverage. |
| `c-04-retrieval-strategy-router` | Choose Semantics, Relationship, or Intent retrieval, using providers for discovery and onboarding/source as proof. |
| `c-05-create-or-update-onboarding-files` | Create and maintain file-level onboarding and entity catalogs. |
| `c-08-ar-coordination-context-resolver` | Resolve memory, coordination, task, temp, and cross-repo facts. |
| `c-09-git-worktree-manager` | Manage worktree lifecycle, integration, and cleanup gates. |
| `c-10-adopt-memory-baseline` | Adopt existing external-memory onboarding into the first ledgered baseline. |
| `c-11-memory-carryover-from-branch` | Carry richer memory forward after matching code lands. |
| `c-12-closeout` | Own the closeout approval gate and the code → memory → ledger commit sequence, for both direct edits and worktree-backed tasks. |
| `c-13-install-and-onboard` | Lead first-run setup: preflight checks, start hook (or instruction placement), memory repo, onboarding bootstrap, and provider indexing. |

## Installing Skills Into Harnesses

Use:

```text
skills_install()
```

The install target is normally inferred from the MCP settings location:
`<registration-root>/mcp/<settings>.json` installs into
`<registration-root>/skills/`. Use `harnessSkillRoot` only for non-standard
harness layouts.

The packaged skills are flat (one folder per skill), so each installs as
`<registration-root>/skills/<skill-name>/` named by its lowercase frontmatter name.

Do not copy individual skill folders by hand. The MCP tool copies the packaged skill tree consistently, including sibling files and shared helper modules.
