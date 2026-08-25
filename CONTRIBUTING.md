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

### Acceptance runs in Dagger

For this repository, only the pinned Dagger Ubuntu graph produces acceptance
evidence. Host `pytest`, Playwright, and direct `agents_remember.code_quality.check`
execution refuse before test or retry planning. A direct targeted `npm test -- <files>` Vitest
run is supported as a fast unit/component diagnostic loop; it never substitutes for Dagger
acceptance, changed-lines coverage, or lifecycle evidence. For Python, the only supported direct
diagnostic command is:

```text
./scripts/test-python mcp/tests/test_file.py::test_name [EXACT_NODE ...]
```

Pass one to eight exact node IDs already present in `mcp/tests/python-direct-cohort.toml`. The
command runs serially and validates the complete content-sealed cohort policy before pytest starts:
reviewed file/symbol closure, local-import facts, effect disposition, and configuration hashes.
It refuses the whole request if any node is outside the manifest, any dependency/effect fact is
unknown or unsafe, or any audited content changed. It accepts no pytest flags, never runs an
eligible subset, never falls back to Dagger, and marks every JSON result `altitude=diagnostic`,
`certifying=false`. The timing record separates admission, bootstrap, collection, first-node
delay, execution, and reporting; none of those measurements or outcomes is acceptance evidence.
Before adding a durable fixture, recording, migration proof, or shared-support world, follow the
authority, lifecycle, cadence, and replacement rules in
[`docs/design/python-evidence-system.md`](docs/design/python-evidence-system.md). The executable
inventory is `mcp/tests/evidence-lifecycle.toml`; an uncataloged or expired governed artifact fails
the static quality tier and Dagger quality route.
The lifecycle tools own the two accepted invocations:

- `mode=targeted` for focused leaf acceptance (changed files, reverse-import closure,
  and the derived pytest subset);
- `mode=full` once at master integration for the complete repository suite.

Both modes require the exact Git comparison commit through `--diff-base`. In a leaf,
that is the recorded master base; at master integration, it is the recorded super
base. An omitted base is refused because the clean-room checkout has no trustworthy
implicit upstream and must never turn changed-lines coverage into whole-tree coverage
against Git's empty tree. `dagger call quality --help` is the executable reference for
the current inputs; the lifecycle tools construct the source snapshot and ancestry
bundle and invoke it automatically.

Inside that nonce-attested graph, one wrapper,
`python -m agents_remember.code_quality.check`, is the gate. It is an internal
executor, not a host command. Four of its steps enforce and fail the run on any
finding: ruff (lint, including the
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

The lifecycle supplies the comparison point as `AR_GATE_DIFF_BASE` from the
worktree contract. On a leaf branch it is the recorded series base, and at master
integration it is the recorded super base. Do not substitute a host invocation:
the wrapper refuses without the nonce-attested Dagger environment.

Changed Python outside the measured packages — the suite itself, `scripts/`, the
provider images — is listed on every run under its own heading rather than dropped,
because `--cov` measures the shipped package and the floor cannot honestly speak for
the rest.

Set it up once per clone:

1. Install the dev environment: `pip install -e "mcp[dev]"`
2. Enable the shared hooks: run `./setup-hooks.sh` (or `git config core.hooksPath .githooks`)

### Local diagnostic tiers

`.githooks/pre-commit` and `.githooks/pre-push` are thin wrappers over
`.githooks/_gate.sh`, which takes the tier as its argument:

| Tier | Hook | Input state it reports | Runs | Cost |
| --- | --- | --- | --- | --- |
| `fast` | pre-commit | the staged content | generated-copy checks (skills, runtime, harness), ruff, `ruff format --check`, Pyright | about 20 seconds |
| `targeted` | pre-push diagnostic | Git's ref updates plus current-checkout bytes at index-known paths | the same deterministic non-test checks as `fast`; Dagger acceptance is not run | about 20 seconds |
| `full` | manual refusal | none | refuses host execution and points to the lifecycle-owned Dagger gate | immediate |

The fast tier enumerates Python paths with `git ls-files '*.py'` (the
staged/index population). The targeted pre-push tier repeats those deterministic
non-test checks against current-checkout bytes and records the pushed refs as
provenance. Neither tier runs pytest, Vitest, Playwright, or the Dagger acceptance
graph. The accepting targeted graph runs exactly once when a leaf closeout creates
its commit. Leaf integration lands that exact certified commit without rerunning it.
The accepting full graph runs exactly once per master, at the master integration
gate invoked by `worktree_integrate`. Host hooks do not replace either acceptance
boundary.

The pre-push hook forwards Git's four-field ref-update lines as provenance. It
does not stage, stash, mutate the index, run tests, or claim that the current
checkout is the pushed commit tree.

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

GitHub validation is pull-request-only. Ordinary branch pushes do not launch a
second copy of the same workflows. `.github/workflows/quality-checks.yml` installs
the Python and dashboard development dependencies and runs the deterministic
non-test hook: generated-copy checks, Ruff, `ruff format --check`, Pyright,
dashboard code generation, lint, and typecheck. A finding fails the pull request.

Neither workflow runs Dagger acceptance or a host test suite. The pull request
validates the GitHub environment and merge surface; it does not spend the leaf or
master acceptance boundary again. If required status-check names change, update
the branch ruleset in the same change so the PR cannot merge without its current
checks.

### The environment-gated integration paths

`pyproject.toml` registers eight markers for suites that skip unless an `AR_*`
variable opts them in — fourteen tests over the Pi RPC transport, the Codex
app-server, the Claude stream-json transport, the L3 control routes, the production
evidence seam and the real MCP stdio server. Registering a marker is not applying
one: all eight were registered and documented while no test carried
`@pytest.mark.<name>`, so `pytest -m ar_run_pi_rpc_smoke` selected nothing, and
nothing set any of the variables either.

`scripts/run-gated-integration.py list` reports each selection and what it needs.
Execution is test-capable and therefore refuses outside the nonce-attested Dagger
environment; it is not a host-test escape hatch:

```sh
python scripts/run-gated-integration.py list
```

The environment-gated paths are not GitHub PR checks. They are pytest selections and
therefore inherit the repository-wide Dagger attestation requirement; a host or plain
GitHub runner is refused before collection. Their requirements remain:

| Path | Needs | Automated PR check |
| --- | --- | --- |
| `ar-run-pi-rpc-smoke` | node + npm; installs its own pinned Pi build and drives it `--offline` against `127.0.0.1` | no |
| `agents-remember-real-mcp-config` | a generated settings file; spawns this repo's own MCP server over stdio. The live grepai half additionally needs the self-hosted docker stack up and indexed | no |
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

Worktree closeout runs the leaf change-set-scoped quality contract through Dagger
`mode=targeted` before creating a code commit, even when hooks are not configured.
Dagger `mode=full` runs exactly once per master, at the master integration gate,
invoked by the integration step itself. Leaf integration, push, pull-request
validation, tag, and publish do not rerun either acceptance. Agents Remember owns
this integrated wrapper, so removing it is a hard refusal at leaf closeout and master
integration. A consuming repository without this adapter instead receives the generic
`wrapper-unavailable` state and follows the executor policy in its own memory root.

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
