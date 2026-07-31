# Contributing to Agents Remember

Thanks for contributing.

Agents Remember is a markdown-first memory layer and workflow system for coding agents. It is also a substantial application: roughly 190,000 lines of Python under `mcp/` (the MCP server, CLI, and workflow engine, plus its test suite) and a TypeScript dashboard under `dashboard/`, alongside the instructions, skills, and onboarding conventions. Contributions land in both halves, and most of them touch code.

The standard for changes is the same either way: make the system clearer, safer, and easier to apply consistently. Code additionally has to pass the quality gate described below — that part is not a matter of taste.

## What belongs here

Good contributions include:

- fixing defects in the MCP server, CLI, workflow engine, or dashboard
- adding or tightening tests, especially where the gate shows thin coverage
- fixing unclear or conflicting workflow guidance
- improving skills so their scope, inputs, and outputs are easier to follow
- tightening onboarding conventions and examples
- correcting stale or misleading repository documentation
- improving bootstrap guidance for new repositories
- clarifying promotion gates, review checkpoints, or artifact ownership

If a change only makes the wording more clever but not more precise, it usually does not help.

## Core principles

Please preserve the ideas this repository is built around:

1. Drift check before planning.
2. Approval before implementation.
3. Onboarding updates only after approved changes.
4. Persistent memory should be path-derived, readable, and easy to maintain.
5. Workflow is optional; the memory layer is the product.

When contributing, prefer changes that reinforce those rules instead of introducing parallel exceptions.

## Repository shape

This repository is organized around a few distinct responsibilities:

- `mcp/src/agents_remember` — the MCP server, CLI, workflow engine, and quality wrapper
- `mcp/tests` — the pytest suite that the gate runs in full
- `dashboard/` — the cockpit frontend; its build output is generated, is not committed, and is produced by the release job
- `scripts/` — synchronisers that keep generated copies in step with their sources
- `.githooks/` — the shared local gate, in a fast tier and a full tier
- top-level agent guidance
- reusable skills
- workflow phase assets
- onboarding and examples
- supporting reference documentation

Keep those responsibilities separate. Do not move detailed phase behavior into entrypoint guidance, and do not turn examples into normative rules unless the repo is explicitly adopting them. Never hand-edit a generated copy: change the source and re-run the matching script in `scripts/`.

## Before opening a pull request

Please make sure your change is scoped and intentional.

1. Identify the exact rule, workflow step, example, or convention that is wrong or incomplete.
2. Make the smallest coherent change that fixes that problem.
3. Update nearby examples, cross references, or self-documentation in the same pull request when they would otherwise drift.
4. Call out any behavior change clearly in the pull request description.

For larger workflow changes, open a discussion or draft pull request early instead of landing a surprise rewrite.

## Quality gates

One wrapper, `python -m agents_remember.code_quality.check`, is the gate. It runs
ruff (lint), Pyright (types), the full pytest suite, and CRAP (complexity x
coverage, where any score at or above the configured threshold is a hard
failure), and it fails on any finding.

Set it up once per clone:

1. Install the dev environment: `pip install -e "mcp[dev]"`
2. Enable the shared hooks: run `./setup-hooks.sh` (or `git config core.hooksPath .githooks`)

### The two local tiers

`.githooks/pre-commit` and `.githooks/pre-push` are thin wrappers over
`.githooks/_gate.sh`, which takes the tier as its argument:

| Tier | Hook | Certifies | Runs | Cost |
| --- | --- | --- | --- | --- |
| `fast` | pre-commit | the staged content | generated-copy checks, ruff, Pyright | about 20 seconds |
| `full` | pre-push | the working tree | generated-copy checks, then the full wrapper | minutes |

The fast tier is cheap on purpose. `--no-verify` is all-or-nothing: it disables
every check, not only the slow one. A pre-commit hook expensive enough to be
worth skipping therefore costs you ruff and Pyright as well, which is how this
repository previously ended up with a gate that never ran.

To certify the staged content rather than the working tree, the fast tier parks
unstaged and untracked files with `git stash push --keep-index --include-untracked`
for the duration of the checks, and restores them from a trap that fires on
success, on failure, and on Ctrl-C. What follows from that:

- A scratch file you have not staged cannot fail your commit.
- A partially staged file is checked as staged, not as edited.
- Nothing is stashed when the working tree already matches the index, nor during
  a merge, rebase, cherry-pick, or revert — stashing there would move the
  conflict resolution out of the tree git is about to commit from. In those
  states the fast tier certifies the working tree instead and says so.
- If the hook is killed outright (`SIGKILL`, a crash, a closed terminal) the trap
  cannot run, and your work is left in a stash named
  `agents-remember pre-commit gate: staged-content isolation`. Recover it with
  `git reset --hard && git stash pop --index`.

### CI

`.github/workflows/quality-checks.yml` runs the same wrapper on every branch push
and every pull request, on Python 3.11, 3.12, and 3.13, alongside the dashboard
frontend rail (lint, typecheck, unit tests, build). The branch ruleset on `main`
requires `Quality wrapper (Python 3.11)`, `Quality wrapper (Python 3.12)`, and
`Quality wrapper (Python 3.13)` to pass before a merge, so a local `--no-verify`
delays the verdict rather than avoiding it.

`Dashboard frontend rail` runs on every push and pull request but is **not yet a
required check**, so a red frontend does not currently block a merge. Making it
required is a repository settings change, not a change to any file here: add the
context `Dashboard frontend rail` (integration id `15368`) to the
`required_status_checks` rule of the `main-branch-rules` ruleset. This paragraph
is the enforced truth as of this commit — if the context is added, update it
rather than leaving the document ahead of the setting.

### Closeout

Worktree closeout runs the same strict wrapper before creating a code commit,
even when hooks are not configured, in any repository whose checkout carries the
wrapper. A checkout that does not carry it is reported as `wrapper-unavailable`
in the closeout payload — the commit still happens, and the payload states that
it was not quality-checked.

## Writing guidelines

Write for both humans and agents.

- Prefer direct instructions over broad commentary.
- Be explicit about ownership, order, and scope.
- Use examples when they remove ambiguity.
- Keep examples minimal but realistic.
- Avoid duplicating the same rule in multiple places unless repeated visibility is part of the design.
- Do not add speculative guidance that describes how the system might work later. Document current behavior or clearly proposed behavior only when the file is meant to define it.

## Workflow-specific guidance

If you change a workflow, also update the surrounding material that explains it. A workflow change is incomplete if the main guidance changes but the related examples, onboarding, or review expectations still describe the old behavior.

If you change onboarding conventions, preserve the one-to-one path mirroring model unless the change is deliberately redesigning that contract.

If you add or revise a skill, keep its boundary narrow and explicit. A good skill says when to use it, what it reads, what it produces, and what it should not be used for.

## Pull request checklist

Before submitting, verify that:

- the change fits the repository’s memory-layer model
- related documentation and examples were updated where needed
- links, paths, and snippets still make sense
- new guidance does not conflict with existing workflow rules
- any breaking or behavior-changing workflow update is called out clearly

## Collaboration

Be precise, respectful, and willing to justify tradeoffs. The goal is not to accumulate more process. The goal is to keep the process that exists legible, trustworthy, and useful across many sessions and many repositories.
