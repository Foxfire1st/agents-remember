# Skills Reference

Installed skills live under:

```text
ar-coordination/skills/
```

The canonical source copies live under:

```text
agents-remember/skills/
```

After editing root `skills/`, run:

```text
python3 scripts/sync-skills.py
```

That refreshes the generated MCP package-data copy and every harness starter
package skill folder. The pre-commit and pre-push hooks run
`python3 scripts/sync-skills.py --check` so unsynced copies fail before push.

## Lifecycle And Workflow Skills

| Skill | Purpose |
| --- | --- |
| `l-01-agent-lifecycles` | The agent lifecycles, one per agent type under one roof. A role agent on the Paseo host gets one role file and one operation; the skill houses the self-contained per-role lifecycles (architect · backend orchestrator · designer · strategist · manager · worker · curator · investigator · adversarial reviewer) plus the report-template library and the reviewer criteria catalogs. For work on a managed repository, free chat creates or resolves the sprint document, writes the request of the developer into it, and starts an Architect with `role_start` (the call for an Architect names no document; the developer names the sprint document to it in the dashboard); another role is started this way only when the developer names the role and the task. |
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
| `c-10-adopt-memory-baseline` | Adopt existing external-memory onboarding as an accepted Git memory baseline and refresh its computed ledger cache. |
| `c-11-memory-carryover-from-branch` | Carry richer memory forward after matching code lands. |
| `c-12-closeout` | Own closeout approval and the code → memory-content commit sequence; keep the computed ledger cache outside the Git transaction. |
| `c-13-install-and-onboard` | Lead package-based first-run setup after the harness starter package and MCP server are wired: preflight checks, `runtime_install`, memory repo, onboarding bootstrap, the knowledge foundation, and provider indexing. |
| `c-14-knowledge-bootstrap` | Build or resume a repository's knowledge foundation through the existing curator: read the current knowledge state, inventory bounded sources, author invariants and families through the curator's own writer, publish through the taskless bootstrap, read the result back, and report coverage and unresolved work. |

## Installing Skills Into Harnesses

The normal first-run path does **not** use this section. Copy the harness starter
package instead; it already contains the skill folders each harness discovers.

For manual maintenance or non-package installs, use:

```text
skills_install()
```

The install target is normally inferred from the MCP settings location:
`<registration-root>/mcp/<settings>.json` installs into
`<registration-root>/skills/`. Use `harnessSkillRoot` only for non-standard
harness layouts.

The packaged skills are flat (one folder per skill), so each installs as
`<registration-root>/skills/<skill-name>/` named by its lowercase frontmatter name.

Do not copy individual skill folders by hand for a maintenance install. The MCP
tool copies the packaged skill tree consistently, including sibling files and
shared helper modules.
