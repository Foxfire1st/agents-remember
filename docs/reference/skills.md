# Skills Reference

Agents Remember ships its skills inside the package. The tool server publishes every skill as an
MCP resource (the SEP-2640 skills extension), so a harness reads the served skill text from the
running server instead of a copy in a project or home skill folder. Nothing installs a skill
folder: there is no `skills_install` tool any more, no harness mirror, and no copied
coordination-root `skills/` tree (MIK-R196).

There is exactly one skill copy to keep current, and it is the serving copy:

```text
agents-remember/skills/                                  # canonical authoring source
mcp/src/agents_remember/package_data/runtime/skills/     # generated package copy that is served
```

After editing root `skills/`, regenerate the serving copy:

```text
python3 scripts/sync-skills.py
```

That refresh is declared in `generated-copies.json`, so the pre-commit and pre-push hooks run
`python3 scripts/sync-skills.py --check` and fail before push when the two diverge. The canonical
tree and the packaged tree keep the same paths and bytes; the packaged one is what any installed
build serves, which is what makes a recorded origin/URI/digest name the same content next time.

## Lifecycle And Workflow Skills

| Skill | Purpose |
| --- | --- |
| `l-01-agent-lifecycles` | The agent lifecycles, one per agent type under one roof. A role agent on the Paseo host gets one role file and one operation; the skill houses the self-contained per-role lifecycles (architect · backend orchestrator · designer · strategist · manager · worker · curator · investigator · adversarial reviewer) plus the report-template library and the reviewer criteria catalogs. For work on a managed repository, free chat creates or resolves the sprint document, writes the request of the developer into it, and starts an Architect with `role_start` (the call for an Architect names no document; the developer names the sprint document to it in the dashboard); another role is started this way only when the developer names the role and the task. |
| `w-02-light-task-workflow` | Durable one-page task plan with approval gate and live checklist; escalates to a master + light sub-task series for larger work. |

## Core Skills

| Skill | Purpose |
| --- | --- |
| `c-00-initialize-memory-repo` | Create or repair a target repository memory root. |
| `c-03-repo-bootstrap` | Bootstrap repo overviews, route-local overviews, and onboarding coverage. |
| `c-04-retrieval-strategy-router` | Choose Semantics, Relationship, or Intent retrieval, using providers for discovery and onboarding/source as proof. |
| `c-05-create-or-update-onboarding-files` | Route durable findings to the right artifact, control memory quality with task-start drift and closeout gates, and create and maintain file-level onboarding and entity catalogs. |
| `c-08-ar-coordination-context-resolver` | Resolve memory, coordination, task, temp, and cross-repo facts. |
| `c-10-adopt-memory-baseline` | Adopt existing external-memory onboarding as an accepted Git memory baseline and refresh its computed ledger cache, and carry richer memory forward after matching code lands. |
| `c-13-install-and-onboard` | Lead package-based first-run setup after the MCP server is registered: the one-call Bootstrap start, the runtime scaffold, the memory repo, the baseline, user settings, and provider indexing. |
| `c-14-knowledge-bootstrap` | Build or resume a repository's knowledge foundation through the existing curator as ordinary Architect-organized task work: read the current knowledge state, inventory bounded sources, author invariants and families through the curator's own writer, read the result back, and report coverage and unresolved work. |

Closing out a leaf creates the code and memory commits in one transaction, and the worktree
lifecycle keeps integration behind its approval gates; both live in the skills above rather than
in separate skills of their own.

## Reading Served Skills

A harness that reads MCP resources lists the served skills and reads one file at a time. Each
resource's origin and URI are the server's own (`agents-remember/skills`), so two servers serving
one skill name stay distinct.

For the first-run path, the free chat needs one tool call — `role_start(role="bootstrap",
request_id=<uuid>)` — and no skill read at all: the launched Bootstrap seat receives its compiled
role and operation instructions with its first message. Setup decisions and the Architect handover
then continue in the dashboard.
