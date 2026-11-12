# Python test policy and commands

This page states how the Python tests of this repository are admitted, selected and run. It is
for a contributor.
[CONTRIBUTING.md](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md)
describes the development environment, the hooks and the other checks.

## Policy

Preserve important behavior with the smallest maintainable suite. A new case must protect a
distinct user operation, consequential failure, or actual regression. Extend, consolidate or
replace existing tests before adding overlapping protection. Private helper branches, coverage
percentages and higher test counts are not reasons to add tests. Remove unused support when
removing tests; do not hide a large suite behind the pytest marker `integration`.

## Budgets

`pyproject.toml` at the repository root declares two case budgets, each counted with its
parameters: `unit_case_budget = 10000` and `integration_case_budget = 2000`.
`pytest_collection_finish` in `mcp/tests/conftest.py` counts the selected items of the run
itself. It starts no second collection, no dependency scan and no further test run. A selection
over a budget ends the run with a usage error before any test runs. The combined run checks
both budgets.

Growth beyond a budget requires an explicit tradeoff in the change description: distinct
protection, why consolidation is insufficient, case count, test and support size, and elapsed
runtime. A raised budget alone is not that justification.

## Commands

Run the commands from the repository root.

- Unit tests: `mcp/.venv/bin/python -m pytest`.
- Integration tests: `mcp/.venv/bin/python -m pytest -m integration`.
- Combined: `mcp/.venv/bin/python -m pytest -m ""`.
- Focused debugging: supply a test file or node, optionally with `-n=0`.
- A fixed random order: add `--random-order-seed <number>`; the header of the run reports the
  seed.
- Every command needs a Git checkout: the pytest plug-in
  `agents_remember_test_support.testing.evidence_lanes`, which checks each test file against the
  [lane](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#lanes) manifest,
  lists the test files through `git ls-files`, so an exported (`git archive` or tarball) tree
  must run `git init` and `git add -A` first or collection fails instead of running.

Four workers run by default, and the cases of one file stay in one worker. The default
selection is `-m "not integration"`. The test files of the lanes `integration` and
`stress-durability` of `mcp/tests/test-evidence-lanes.toml` are the integration tests: the
default run skips their files before import, and every other run marks their cases
`integration`.

Keep small parser, selection, reporting and retry input and output tests among the unit tests;
use real application, process and publication boundaries sparingly. Do not run nested pytest,
repeated collection or repository scans to test the suite.

## Certification

Certification runs only when a contributor asks for it. It runs through Dagger and commits
nothing. No hook, no closeout and no landing starts it: a closeout runs the knowledge gate and
the repository's pre-commit hook on the tree it publishes, and no test.

The certification run, which the command in
[Certification](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#certification)
starts, runs pytest with `--certify -m ""` and measures branch coverage of the product code,
including the Python child processes that the tests start. `--certify` loads the evidence
services of that run and refuses outside it. A pytest run on a contributor's machine is a
development check and certifies nothing, whatever it reports.

## Diagnostic metrics

Coverage, including the coverage of changed production code, is information only. There is no
mandatory percentage and an uncovered line demands no test.
The [CRAP score](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#what-fails-a-change-and-what-only-reports) uses production functions only,
excluding tests and verification support. Its threshold of 20 prompts review but does not block
delivery. Choose simpler production code, a meaningful behavioral test, or a concise justified
acceptance in the change description. Do not add a score-exception system, coverage baseline or
ratchet.

Lint, formatting, typing, structural rules and test failures are enforcing. An error of a
diagnostic tool itself is a visible failure, distinct from a finding.

## Isolation

`mcp/tests/conftest.py` puts the checkout's own `mcp/src` and `mcp/test_support` first on the
import path, so a run tests the candidate and not an installed copy. Before collection it
creates disposable home, configuration, data and cache directories and points the process at
them. It removes inherited credentials and the opt-ins for live providers, and it ignores the
user's and the system's Git configuration.

A test that changes module-level mutable state of the package and does not restore it fails.
Tests that need the application's composed services request the fixture `worktree_services`.
A case with the pytest marker `integration` receives it automatically; a unit case does not.
