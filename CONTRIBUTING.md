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

One wrapper, `python -m agents_remember.code_quality.check`, is the gate. Four of
its steps enforce and fail the run on any finding: ruff (lint, including the
complexity rules `C901`, `PLR0911`, `PLR0912`, `PLR0915`), `ruff format --check`,
Pyright (types), and the full pytest suite — followed by CRAP (complexity x
**branch** coverage), where any score at or above the configured threshold is a hard
failure. The gate prints the branch coverage each CRAP offender would need, and says
so explicitly when the complexity term alone is over the line and no test can clear
it.

**No rail of this gate has a baseline, allowlist, ratchet or exemption file, and none
may be added.** The complexity rules briefly had one: arming them produced 67
offenders, which were recorded in `quality/complexity-baseline.txt` behind a
shrink-only cap and a dated burn-down. That was overruled — all 67 were refactored,
and the file, its module and its gate step were deleted. A complexity finding is
cleared by extracting a cohesive helper, never by `# noqa`, a per-file ignore, or a
wider limit in `pyproject.toml`. A CRAP finding is cleared by raising the function's
branch coverage or by splitting it.

Two steps do **not** enforce and are labelled as reports in the output: `radon
cc` and `radon mi` exit 0 whatever they find, so no Radon finding has ever been
able to fail this gate. They are printed for refactor scouting. A non-zero exit
from either still fails the run, because for a tool that exits 0 on every
finding a non-zero exit means the tool itself broke.

Scope is derived from the tree, not written down: `git ls-files '*.py'` is what
ruff, the formatter and Pyright receive, so a newly tracked Python file is gated
the moment it is added. The wrapper accepts no path arguments — there is no way to
narrow what it certifies.

### The coverage floor is on your diff, not on the tree

The binding coverage gate is `diff-coverage`, the last enforcing step. It scores the
same coverage report pytest just produced — no second run — restricted to the lines
your change touched, and it fails on **any** uncovered changed statement or untaken
changed branch, naming each one as `path:line`.

There is no aggregate percentage to pin, on purpose. With 88k lines of tests, much of
the package runs simply by being imported: the tree reports 87% and one entirely
untested twenty-line function moves that by 0.04 points, which no threshold can see.
A floor on changed lines is the only form that can. And it is 100% because anything
lower is a budget for untested code that grows with the size of the change — at a 90%
floor the median commit here (234 changed units) may leave 23 lines untested, which is
a whole function. `mcp/src/agents_remember/code_quality/diff_coverage.py` carries the
measurements.

The comparison point is the merge base against your source branch, resolved in this
order and printed on every run: `AR_GATE_DIFF_BASE`, the pull request base, the
branch's configured upstream, then the default branch. Set `AR_GATE_DIFF_BASE` when
you are on a leaf branch cut from a series branch — git cannot infer that fork point,
and without it the gate compares against `main` and asks you to cover the series'
lines as well as your own.

```sh
AR_GATE_DIFF_BASE=ar/<series-branch> python -m agents_remember.code_quality.check
```

Changed Python outside the measured packages — the suite itself, `scripts/`, the
provider images — is listed on every run under its own heading rather than dropped,
because `--cov` measures the shipped package and the floor cannot honestly speak for
the rest.

Set it up once per clone:

1. Install the dev environment: `pip install -e "mcp[dev]"`
2. Enable the shared hooks: run `./setup-hooks.sh` (or `git config core.hooksPath .githooks`)

### The two local tiers

`.githooks/pre-commit` and `.githooks/pre-push` are thin wrappers over
`.githooks/_gate.sh`, which takes the tier as its argument:

| Tier | Hook | Input state it reports | Runs | Cost |
| --- | --- | --- | --- | --- |
| `fast` | pre-commit | the staged content | generated-copy checks (skills, runtime, harness), ruff, `ruff format --check`, Pyright | about 20 seconds |
| `targeted` | pre-push | Git's ref updates plus the leaf change set (changed Python files, reverse-import closure, derived test subset) at index-known paths; the changed-lines rail is base-to-working-tree | generated-copy checks, then the change-set-scoped wrapper (`--targeted`) | about a minute |
| `full` | manual; also master integration (`worktree_integrate` on a series/master contract) | the whole tree at current-checkout bytes; host-managed RAM/swap by default at the master integration gate | generated-copy checks, then the full wrapper | minutes (~13-18) |

The fast tier enumerates Python paths with `git ls-files '*.py'` (the
staged/index population); the targeted tier derives its input from the leaf diff
(changed files, reverse-import closure, derived test subset). Every rail prints
that input, its resolved config, and its unit count before its result. The manual
and full-tier wrapper also enumerate non-ignored untracked files inside the
quality scope roots and state that they are **not** in the index/diff
measurement. That is a report, never implicit staging. Neither tier passes a
narrowing flag to ruff: `ruff check` runs at the configured selection in both;
the targeted tier simply points the rails at the leaf's change set, so the
complexity rules bite at commit time and not only on push. The full wrapper runs
exactly once per master, at the master integration gate, invoked by the
integration step itself; leaf closeouts and leaf integrations run the targeted
tier only.

The pre-push hook forwards Git's four-field ref-update lines as provenance. It
does not stage, stash, or mutate the index, and it does not claim the current
checkout is the pushed commit tree. The fixed rails still read current-checkout
bytes at index-known paths, and changed-lines coverage still compares the
resolved base to the working tree; the output says exactly that limitation.

The fast tier is cheap on purpose. `--no-verify` is all-or-nothing: it disables
every check, not only the slow one. A pre-commit hook expensive enough to be
worth skipping therefore costs you ruff, the formatter and Pyright as well, which
is how this repository previously ended up with a gate that never ran.

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
requires all four to pass before a merge — `Quality wrapper (Python 3.11)`,
`Quality wrapper (Python 3.12)`, `Quality wrapper (Python 3.13)` and
`Dashboard frontend rail` — so a local `--no-verify` delays the verdict rather
than avoiding it.

Two consequences worth knowing. The ruleset sets
`strict_required_status_checks_policy`, so a branch must also be up to date with
`main` before it can merge: a stale branch has to rebase and go green again on all
four. And because the frontend rail runs `npm ci && npm run build`, a registry
outage or a Node drift blocks merges until it is fixed — that is the intended
trade, not a misconfiguration.

This paragraph describes what the ruleset actually enforces. If the required
contexts change, change it here in the same commit; a document that runs ahead of
the setting is the failure mode this section exists to avoid.

### The environment-gated integration paths

`pyproject.toml` registers eight markers for suites that skip unless an `AR_*`
variable opts them in — fourteen tests over the Pi RPC transport, the Codex
app-server, the Claude stream-json transport, the L3 control routes, the production
evidence seam and the real MCP stdio server. Registering a marker is not applying
one: all eight were registered and documented while no test carried
`@pytest.mark.<name>`, so `pytest -m ar_run_pi_rpc_smoke` selected nothing, and
nothing set any of the variables either.

`scripts/run-gated-integration.py` is one command per path, and `list` prints what
each one needs plus whether this machine has it:

```sh
python scripts/run-gated-integration.py list
python scripts/run-gated-integration.py ar-run-pi-rpc-smoke
```

`.github/workflows/integration-gated.yml` runs the two that need no vendor account
on every push and pull request, and fails on failure:

| Path | Needs | Runs in CI |
| --- | --- | --- |
| `ar-run-pi-rpc-smoke` | node + npm; installs its own pinned Pi build and drives it `--offline` against `127.0.0.1` | yes |
| `agents-remember-real-mcp-config` | a generated settings file; spawns this repo's own MCP server over stdio. The live grepai half additionally needs the self-hosted docker stack up and indexed | the planning half |
| `ar-codex-app-server-live-smoke` | an installed, signed-in Codex whose live catalogue advertises the model. Sends no prompt, so it bills nothing | no |
| `ar-codex-app-server-live-conformance` | the same install, plus two real turns. Bills | no |
| `ar-claude-stream-smoke` | an installed, signed-in Claude Code. The prompt is the local `/cost` command, so spend is negligible | no |
| `ar-run-control-plane-installed` | exactly `codex 0.144.5` and `pi 0.80.7`, signed in, plus a 600-word essay prompt and an image upload. Bills | no |
| `ar-run-control-installed` | the same pinned pair, plus the essay prompt, two settled turns and a PNG upload. Bills | no |
| `ar-run-evidence-installed` | the same pinned pair. Tiny prompts, but it persists a real thread into your `CODEX_HOME`. Bills | no |

Six of the eight need an installed, signed-in vendor CLI that no hosted runner can
hold, and four of those bill for real model turns — that is a fact about the path,
not a setup nobody got round to. None of them is faked, stubbed or soft-skipped in
CI; they run here, in one command, and the runner states the cost before it starts.

`mcp/tests/test_gated_integration_runner.py` fails if any registered marker selects
zero tests again, or if the runner and the registry drift apart in either direction.

### Closeout

Worktree closeout runs the leaf change-set-scoped quality contract (`--targeted`)
before creating a code commit, even when hooks are not configured, in any
repository whose checkout carries the wrapper. The full wrapper runs exactly once
per master, at the master integration gate, invoked by the integration step
itself. A checkout that does not carry the wrapper is reported as
`wrapper-unavailable` in the closeout payload — the commit still happens, and the
payload states that it was not quality-checked.

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
