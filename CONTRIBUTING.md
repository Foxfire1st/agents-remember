# Contributing to Agents Remember

This page is for a person who changes this repository. It tells where things are, how to set
up a development checkout, which checks run at which moment, and what a pull request needs.
The pages under [docs/](docs/README.md) describe the product for a person who uses it.

Everyone who takes part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report a
vulnerability as the [Security Policy](SECURITY.md) says, not in a public issue.

## What the repository holds

| Path | Content |
| --- | --- |
| `mcp/src/agents_remember/` | The Python package: the tool server, the command `agents-remember`, [the dashboard's server](docs/concepts.md#dashboard), and the worktree and knowledge code. |
| `mcp/src/agents_remember/package_data/` | What the wheel carries beside the code: the generated copies under `runtime/` and `benchmarks/`; the presentation kit and the explainer's instructions (`canvas/`); the npm package that installs the host, with the pinned versions of Paseo and of Node (`paseo_host/`); the plug-in for the host (`paseo_plugin/`); the vocabulary of the token counter, so that the tool server starts without the network (`tiktoken/`); two lists of environment variables, those that name a harness's own folder (`host-harness-homes.json`) and those of a calling session, which the commands that install and run the host do not inherit (`host-session-environment.json`); and, in a wheel built after `scripts/sync-dashboard.py`, the built dashboard (`dashboard/`). |
| `mcp/tests/` | The pytest suite and its two test catalogs: the lane manifest `mcp/tests/test-evidence-lanes.toml` and the lifecycle catalog `mcp/tests/evidence-lifecycle.toml`. |
| `mcp/test_support/` | The package `agents_remember_test_support`: pytest plug-ins, the quality code and the validator of the two test catalogs. The wheel does not ship it. |
| `mcp/pyproject.toml` | The package's metadata, its dependencies and the `dev` extra. |
| `mcp/certification-profile-v1.json` | This repository's certification profile. See [Certification](#certification). |
| `dashboard/` | The dashboard's source: TypeScript and React, built with Vite. Git ignores its build output. |
| `skills/` | The source of the skills that the tool server serves. |
| `system/`, `providers/`, `benchmarks/` | The source of the runtime assets that the package carries. |
| `examples/mcp/` | Example settings files. |
| `docs/` | The user documentation. `docs/design/` holds design pages for contributors. |
| `docs/assets/` | The pictures and the video that `README.md` shows. |
| `scripts/` | Development scripts: the environment bootstrap, the generators of the copies, the documentation check, the sandbox. |
| `.githooks/`, `setup-hooks.sh` | The Git hooks and the script that enables them. |
| `.dagger/`, `dagger.json` | The Dagger module that runs certification. |
| `.github/workflows/` | The pull-request check and the release job. |
| `pyproject.toml` | The settings of Ruff, Pyright, Coverage.py, Radon and pytest for the whole checkout. |
| `layers.toml` | The import order between the top-level packages of `agents_remember`. |
| `generated-copies.json` | The declaration of the generated copy of `skills/`. |
| `AGENTS.md` | The instructions for a coding agent that works on this checkout. |

Three scripts folders have their own page: [scripts/pnt_sandbox](scripts/pnt_sandbox/README.md), the code of the <!-- docs-check: identifier: the name of the script in the repository -->
[sandbox of the whole product](#a-sandbox-of-the-whole-product),
[scripts/e2e_harness](scripts/e2e_harness/README.md) and
[scripts/agent_evals](scripts/agent_evals/README.md).

The tools of the tool server are registered in `mcp/src/agents_remember/mcp/registration/`: one
module for each group, each with that group's `@server.tool()` registrations. `create_server`
walks `TOOL_REGISTRARS`. The served list is `PUBLIC_TOOLS` in
`mcp/src/agents_remember/models/tools/public_roster.py`, and the models in
`mcp/src/agents_remember/models/` enforce the shapes of the answers through
`tool_registry.TOOL_RESPONSE_MODELS`. Every tool of the served list has one row in the
[MCP Tool Reference](docs/reference/mcp-tools.md).

A tool that reads keeps the boundary that
[What a Read May Write](docs/architecture.md#what-a-read-may-write) states. A read runs Git with
`GIT_OPTIONAL_LOCKS=0`, so it takes no lock on an index. A capture that nothing records writes
its objects into a scratch object directory, never into the repository. A read creates no
directory except to hold a file that it may write.

## Development environment

A development checkout needs Linux or WSL, Git, [uv](https://docs.astral.sh/uv/) and, for the
dashboard, Node with npm. `scripts/python-runtime-contract.env` holds the pinned versions:
Python 3.14.8 and uv 0.12.6 as the lowest accepted uv. The bootstrap, the hooks, both GitHub
workflows and the Dagger module read this one file.

Set up a clone once, from the repository root:

```bash
scripts/bootstrap-mcp-venv.sh
npm --prefix dashboard ci
./setup-hooks.sh
```

`scripts/bootstrap-mcp-venv.sh` does three things.

1. It checks that the `uv` on the `PATH` is the pinned version or newer. It installs no uv and
   refuses an older or missing one before it creates anything. It prints the version and the
   path of the uv it builds with.
2. It builds CPython 3.14.8 from the Python.org source archive, unless a valid build is
   already there. Before the build it checks the archive's SHA-256, and it builds with
   pyenv's `python-build` at the commit that the contract pins. The interpreter lands outside
   the repository, under `~/.local/share/agents-remember/python/`, or under `XDG_DATA_HOME`
   when that is set. The script leaves the system's Python as it is. The build needs `curl`,
   `gcc`, `git`, `make`, `sha256sum` and `tar`.
3. It creates `mcp/.venv` from the locked dependencies with every extra, on the interpreter
   of step 2. uv may bring no Python of its own. `scripts/check-python-runtime.py` then
   proves the interpreter's version, its origin, its extension modules and the Linux pidfd
   calls, and `uv pip check` proves that the installed packages fit together.

The script refuses an existing `mcp/.venv`. `scripts/bootstrap-mcp-venv.sh --replace` moves
the old environment aside, builds a new one and restores the old one when the build fails.
`--prefix PATH` names another place for the interpreter; the path is absolute and ends in
`cpython-3.14.8`.

`npm --prefix dashboard ci` installs the dashboard's dependencies. The hooks need them.

`./setup-hooks.sh` sets `core.hooksPath` to `.githooks` and `blame.ignoreRevsFile` to
`.git-blame-ignore-revs` for this clone. Git enables no committed hook by itself, so each
clone runs the script once.

### A linked Git worktree

Two things differ in a linked worktree of the clone, a worktree that `git worktree add` made.

- A hook looks for `mcp/.venv/bin/python` in the worktree first and then in the primary
  worktree. One Python environment in the primary worktree therefore serves every linked
  worktree. Each worktree still needs its own `dashboard/node_modules`.
- A command that loads the package from a linked worktree and reads the coordination root from
  the settings file does not use that root, unless the process declares its mode. It answers
  from a disposable coordination root of that worktree, `provider-runtime/dev-ar-coordination`
  in the worktree's parent folder, and says so in one line on standard error. `--config` does
  not change this. From the primary worktree such a command refuses. The tool server and
  `agents-remember dashboard` declare their own mode and use the configured root. A Python
  process declares the test mode by calling `declare_test_process()` of
  `agents_remember.kernel.primitives.checkout_coordination` before it loads the settings, as
  the test suite does; no option and no environment variable declares it. `server_info` and
  the dashboard's served state name such a binding in the field `checkoutBinding`. To work on
  the configured coordination root, run the installed command.

### Working with the project's own memory

This repository is developed with Agents Remember. Its memory repository is public at
[Foxfire1st/ar-agents-remember](https://github.com/Foxfire1st/ar-agents-remember).

Advice: work with that memory. Clone it to
`<coordination-root>/memory-repos/ar-agents-remember`, the place of the memory repository for
a code repository with the id `agents-remember`. An agent then reads the card of each file it
edits, and your memory updates travel with your code change.
[Getting Started](docs/getting-started.md) tells how to set up a coordination root.

## Generated copies

The package carries copies of files whose source lies elsewhere in the repository. Change the
source and run the script. Never edit a copy by hand.

| Source | Copy | Script |
| --- | --- | --- |
| `skills/` | `mcp/src/agents_remember/package_data/runtime/skills/` | `python3 scripts/sync-skills.py` |
| `benchmarks/`, `providers/`, `system/` | `package_data/benchmarks/`, `package_data/runtime/providers/` and `package_data/runtime/system/` under `mcp/src/agents_remember/` | `python3 scripts/sync-runtime.py` |
| The Python models of the dashboard's data | `dashboard/src/types/projection.schema.json`, `dashboard/src/types/projection.ts` | `mcp/.venv/bin/python scripts/sync-projection-types.py` |
| `dashboard/dist/`, the built dashboard | `mcp/src/agents_remember/package_data/dashboard/` and the file `dashboard.fingerprint` beside it | `python3 scripts/sync-dashboard.py` |

Each script takes `--check`, which compares, reports a difference and writes nothing. The
first three also take `--list-targets`. The pre-commit hook runs the first three with
`--check`.

Git tracks the first three copies. Git ignores the dashboard's build output and its copy, so
neither is committed.

One copy has no script. `docs/tool-details.md` has a copy at
`mcp/src/agents_remember/package_data/runtime/docs/tool-details.md`, and a test fails when
the two differ. Copy the file after you change it.

## Building the dashboard

The command `agents-remember dashboard` serves the built dashboard from the package. A
checkout has none until you build it:

```bash
npm --prefix dashboard ci
npm --prefix dashboard run build
python3 scripts/sync-dashboard.py
```

`scripts/sync-dashboard.py` refuses when `dashboard/dist/` is missing and when the build does
not carry the fingerprint of the dashboard's present source. Without a placed build the
dashboard's address answers `503` and names these three commands. Its `/api` paths still
answer.

Build the dashboard before you build a wheel, because the wheel ships what the script placed:

```bash
uv build --wheel mcp
```

A wheel built without a placed dashboard builds without an error and serves no dashboard.

For work on the dashboard itself, `npm --prefix dashboard run dev` starts Vite's development
server. It forwards `/api` and `/canvas` to a running dashboard at `http://127.0.0.1:8765`; the
environment variable `AR_DASHBOARD_API` names another address.

| Command | What it runs |
| --- | --- |
| `npm --prefix dashboard test` | The Vitest unit and component tests. |
| `npm --prefix dashboard run lint` | ESLint. |
| `npm --prefix dashboard run typecheck` | The TypeScript compiler. |
| `npm --prefix dashboard run codegen` | Panda's code generation, which the type check needs. |
| `npm --prefix dashboard run e2e` | The Playwright tests. |

## Tests

Run pytest from the repository root with the environment's interpreter:

```bash
mcp/.venv/bin/python -m pytest
mcp/.venv/bin/python -m pytest -m integration
mcp/.venv/bin/python -m pytest -m ""
mcp/.venv/bin/python -m pytest mcp/tests/<file>.py -n=0
```

The first line runs the unit tests, the second the integration tests, the third both. The
fourth runs one file without parallel workers, for debugging.

`pyproject.toml` at the repository root holds the pytest settings:

- The default selection is `-m "not integration"`.
- Four workers run in parallel (`-n=4`), and the tests of one file stay in one worker.
- An unregistered pytest marker and an unknown settings key each fail the run.
- A warning fails the test that raises it, except three named warnings of other packages.

`mcp/tests/conftest.py` gives every run its own environment before collection:

- a temporary home with its own configuration, data and cache folders;
- no Git configuration of the user or the system;
- no credential and no opt-in for a live service from the calling shell.

Every run needs a Git checkout, because collection reads the list of test files from Git. An
exported tree without a Git folder fails collection.

### Lanes

`mcp/tests/test-evidence-lanes.toml` assigns every test file to exactly one of seven lanes:
`unit-regression`, `public-contract`, `integration`, `architecture-fitness`,
`provider-conformance`, `stress-durability` and `migration`. A test file without a line in
the lane manifest stops collection.

The files of the lanes `integration` and `stress-durability` carry the pytest marker
`integration`. The default run does not import them.

`pyproject.toml` sets two case budgets for collected test cases, each counted with its
parameters: `unit_case_budget = 10000` and `integration_case_budget = 2000`. A run whose
selection exceeds a budget stops after collection and runs no test.

`mcp/tests/evidence-lifecycle.toml`, the lifecycle catalog, lists the fixtures, recordings and
shared support files of the suite with the files that use them. Both catalogs have one
canonical form.

When you add a test file, add its line to the lane manifest by hand: the lane is your
decision. Then, and after you rename or remove a test file or a listed support file, let
this command bring both catalogs into their canonical form:

```bash
PYTHONPATH=mcp/test_support:mcp/src mcp/.venv/bin/python -m agents_remember_test_support.testing.evidence_lifecycle --project-root . --write
```

The command decides nothing. It orders rows and lists, removes the lines of files that are
gone, and sets each list `consumers` that the source tree determines. Without `--write` the
same command only validates the two catalogs. The pre-commit hook runs it that way.

### A test under load

`scripts/test-under-load.sh` runs a command on one processor core that ten busy loops share,
and stops the loops afterwards. Use it to show that a timing-sensitive test passes on a busy
machine:

```bash
scripts/test-under-load.sh mcp/.venv/bin/python -m pytest mcp/tests/<file>.py -n=0
```

It runs the command three times and fails when one run fails. `AR_LOAD_RUNS=1` runs it once.

### What deserves a test

A test protects a distinct user operation, a consequential failure or a regression that
happened. Before you add one, extend or merge an existing test that covers the same ground
at another layer. Remove a test's unused support together with the test.

Raising a budget in `pyproject.toml` needs a reason in the change description: the behaviour
that needs the new cases, why existing tests cannot hold it, and the cost in cases, code size
and run time. Moving unit tests behind the pytest marker `integration` is not a reduction.

## What fails a change and what only reports

These rules hold for every route that runs the checks: the hooks, the pull-request check and
certification.

**Failing.** A finding of one of these fails the route that runs it:

- Ruff's lint rules, with the complexity rules `C901` (limit 10), `PLR0911`, `PLR0912` and
  `PLR0915`. Clear a complexity finding by restructuring the code, not by `# noqa`, a per-file
  ignore or a higher limit.
- `ruff format --check`.
- Pyright.
- The check of the generated copies and of the two test catalogs.
- A failing test, where the route runs tests.
- In certification also the file-size rule: a measured file of 1,200 lines or more fails.

**Reporting only.** These produce a report and fail nothing:

- Coverage, including the coverage of changed lines. No percentage is required, and an
  uncovered line demands no test.
- The CRAP score. It weighs a function's cyclomatic complexity `cc`, as Radon measures it,
  against its branch coverage: `cc² × (1 − coverage)³ + cc`. It is computed for production
  functions only; tests and test support are left out. A score of 20 or more is a prompt to
  review the function: simplify it, add a test of its behaviour, or say in the change
  description why the score is acceptable.
- Radon's complexity and maintainability reports. Radon exits with 0 whatever it finds.

An error of a tool itself, as opposed to a finding, fails the route in every case.

The design pages give the detail:
[Python test policy and commands](docs/design/python-pytest-bootstrap.md),
[Python Test Evidence System](docs/design/python-evidence-system.md) and
[Python Test Evidence Authority](docs/design/python-test-evidence.md).

## Git hooks

`.githooks/pre-commit` and `.githooks/pre-push` both start `.githooks/_gate.sh`, with a tier
as its argument. No hook runs a test or certification.

| Tier | Started by | Checks | Reads |
| --- | --- | --- | --- |
| `fast` | `git commit` | the generated copies, Ruff, `ruff format --check`, Pyright, the two test catalogs, and the dashboard's code generation, lint and type check | the staged content |
| `targeted` | `git push` | the same checks | the files of the checkout as they are |
| `full` | by hand only | nothing; it ends with an error and says that the tests run only in Dagger | nothing |

Both hooks need `mcp/.venv` with Python 3.14.8 and `dashboard/node_modules`. A hook that
misses one of them names the command that creates it and stops.

The Python checks cover every Python file that Git tracks (`git ls-files '*.py'`).

The pre-push hook passes on the list of pushed refs that Git gives it, for the report only. It
stages nothing, stashes nothing and does not claim to have checked the pushed commits.

### How the pre-commit hook isolates the staged content

The `fast` tier checks what the commit will contain. For the time of the checks it puts
unstaged and untracked files away with `git stash push --keep-index --include-untracked`, and
it brings them back when it ends, also after a failure and after Ctrl-C. Therefore:

- A file you have not staged cannot fail your commit.
- A partly staged file is checked as staged.
- The hook stashes nothing when the working tree equals the index.
- The hook stashes nothing during a merge, a rebase, a cherry-pick or a revert, because a
  stash would take the conflict resolution out of the tree. It then checks the working tree
  and says so.
- When the hook is killed outright, your work stays in a stash named
  `agents-remember pre-commit gate: staged-content isolation`. Bring it back with
  `git reset --hard && git stash pop --index`.

`git commit --no-verify` and `git push --no-verify` switch off every check of the hook, not
one of them.

## What a closeout and a landing run

In Agents Remember, a closeout commits a task's code and memory, and a landing brings that
work onto the branch of its master or sprint. When this repository is the code repository,
both steps run two checks on the exact trees they publish:

1. **The knowledge gate.** It judges the pair of code tree and memory tree. A refusal names
   its findings as `knowledge-gate-refused`.
   [Knowledge System](docs/reference/knowledge-system.md) describes it.
2. **The repository's pre-commit hook.** The product starts the hook that Git would run for a
   commit in that worktree, here `.githooks/pre-commit`, with the index holding the tree to be
   published. A failing hook, a hook that cannot start, a hook that changes a tracked file
   or the index, and a hook that does not end in time each refuse the landing, and no ref
   moves. The limit is 900 seconds unless
   the setting `orchestration.landing.commitCheckTimeoutSeconds` names another
   ([Settings Reference](docs/reference/settings-json.md)).

A commit that passed carries the trailer `Commit-Check-Pass: tree=<tree> hook=<hook>`.
`<tree>` is the Git id of the tree that the hook checked, and `<hook>` is the path of the hook
from the repository's top folder, here `.githooks/pre-commit`. In a repository without a
pre-commit hook the trailer says `hook=none`. No landing route skips the hook.

A closeout and a landing run no test suite and no certification.
[Worktrees And Closeout](docs/reference/worktrees-c09.md) describes both steps.

## Certification

Certification is the full run of this repository's checks and tests in a clean container. It
runs only when a contributor asks for it. No hook, no closeout, no landing, no pull request
and no release job starts it. It commits nothing.

The run goes through Dagger:

- `dagger.json` pins the Dagger engine `v0.21.8` and names the module in `.dagger/`.
- The module's function `quality` rebuilds the candidate from Git in a pinned Ubuntu
  container, installs it from nothing and runs the checks there.
- `mcp/certification-profile-v1.json` says what runs, in four certification gates in a fixed
  order: checks before the tests, the test suites, checks that read the test results, and the
  integration and end-to-end tests.
  [Repository Certification Profiles](docs/reference/repository-certification-profile.md)
  describes the format.

Ask for a run with this command, from the checkout to certify:

```bash
PYTHONPATH=mcp/test_support:mcp/src mcp/.venv/bin/python -m agents_remember_test_support.code_quality.clean_room \
  --worktree-group <folder-for-reports> \
  --repository-id agents-remember \
  --certification-profile mcp/certification-profile-v1.json \
  --mode targeted \
  --diff-base <commit>
```

- `--mode` is `targeted` for the tests that the change reaches, or `full` for all of them.
  `full` is the default.
- `--diff-base` names the commit that the change is compared with. The run refuses an empty
  value. Advice: name the commit that the change starts from, for example the output of
  `git merge-base HEAD main`.
- `--code-worktree` names the checkout; the default is the current folder. The run certifies
  the tree that is staged there.
- `--memory-cap-bytes` limits the memory of the container.
- The reports land in `<folder-for-reports>/reports/`. Each run deletes and rebuilds
  `<folder-for-reports>/reports/test-sandbox/`, its clean copy of the candidate. Advice: name
  an empty folder outside the checkout. A path on a Windows drive is refused.

The command needs Docker, the program `dagger` on the `PATH`, and a Dagger engine of the
pinned version that already runs as a Docker container with its layer store mounted. The
command starts no engine; [Dagger's documentation](https://docs.dagger.io/) tells how to
install `dagger` and run an engine. The environment variable `AR_DAGGER_RUNTIME_AUTHORITY`
declares the engine to the command, as one JSON object:

```json
{"schemaVersion": "dagger-host-declaration/v1", "endpoint": "container://<engine-container>", "layerStore": "/var/lib/dagger", "engineVersion": "v0.21.8"}
```

`endpoint` names the engine's container, and `layerStore` the absolute path at which that
container mounts its layer store. Without the declaration, or when the container does not run
or does not mount that path, the command refuses before it starts any Dagger command.

Only a passed run of this kind counts as certification. A pytest run on your machine is a
development check, whatever it reports. The option `--certify` of pytest belongs to the run
inside the container and refuses elsewhere.

The module has two more functions. `cadence-evidence` runs the stress, provider and migration
tests on request and marks its result as not accepting. No script of this repository calls it.
Call it with the Dagger command line from the root of a clean clone of the commit, with a Git
bundle of that commit outside the clone; `--trigger` is `scheduled`, `provider-bump` or
`migration-window`:

```bash
git bundle create <bundle-file> HEAD
dagger call cadence-evidence --source=. --repository-bundle=<bundle-file> --trigger=scheduled reports export --path=<folder>
```

`portable-certification` runs the profile of another repository. The clean-room command calls
it when that repository's certification profile names it as `functionName` of its executor
adapter.

## Continuous integration

GitHub runs `.github/workflows/quality-checks.yml` for a pull request, not for a push to a
branch. It installs the locked Python and dashboard dependencies and runs
`.githooks/_gate.sh targeted`, the checks of the pre-push hook. A finding fails the pull
request. The workflow runs no test and no certification.

`.github/workflows/publish-mcp-to-pypi.yml` runs for a tag of the form `mcp-v*`. It refuses
a tag whose commit is not on `main`. It builds the dashboard, places it, builds the wheel
and the source distribution, checks that both ship the dashboard, installs and starts the
wheel in a clean environment, and publishes to PyPI.
[Release Checklist](docs/release-checklist.md) lists the steps of a release.

Advice: when a change renames a required status check, change the branch rule on GitHub in
the same pull request.

## Documentation

The public documents are `README.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`,
`CODE_OF_CONDUCT.md`, `mcp/README.md` and every `.md` and `.txt` file under `docs/`. One check
compares them with the tree, without the network:

```bash
python3 scripts/check-public-docs.py
```

It prints one line for each finding, as `file:line: kind: target (reason)`, then the line
`check-public-docs: <n> documents, <m> findings`. It ends with the exit status 1 when it found
something, and 0 otherwise.

The entry points are `README.md`, `mcp/README.md`, `docs/README.md`,
`docs/getting-started.md`, `docs/install/README.md`, `docs/llms.txt` and the page of each
harness under `docs/install/`. An index page is a page between `docs/README.md` and the pages it
lists, such as `docs/guides/README.md`. The check refuses:

| Kind | What it refuses |
| --- | --- |
| `broken-link` | A link to a path of this repository that the tree does not hold. |
| `missing-anchor` | A link to an anchor that is no heading of its target. |
| `missing-path` | A repository path, in code form or in a code block, that does not exist. A path that Git ignores is a build product and passes. |
| `outside-docs` | A page under `docs/` that links by a relative path to a file outside `docs/`. |
| `unreached` | A file under `docs/` that no chain of links from `docs/README.md` reaches; a file that is no page also counts as reached when `README.md` shows or links it by a relative path. Also a page that is more than two links away from `docs/README.md`, that is, reached only through more than one index page. |
| `entry-link` | A link that an entry point owes and lacks: `README.md` links `docs/README.md`, `docs/getting-started.md`, `docs/install/README.md` and `ROADMAP.md`; `mcp/README.md` the first three; a harness page `docs/install/README.md` and `docs/getting-started.md`. Also a relative link in `mcp/README.md`. |
| `install-steps` | An entry point whose install steps differ from those of `docs/getting-started.md` in number, order, title or command line. |
| `identifier` | An identifier of a task, a requirement or a decision of this project's own planning. |
| `marker` | A docs-check marker without a reason, of an unknown kind, or with nothing to allow. |

No hook runs the check. The test `mcp/tests/test_public_docs_check.py` runs it on the checkout
with the unit tests and fails on a finding. The same test proves each kind of finding by one
change to the checkout's own documents, which it holds in memory.

The check leaves the paths under `mcp/src/agents_remember/` and `skills/` to a second test,
`mcp/tests/test_instruction_consistency.py`. That test reads `README.md` and every file under
`docs/`. Each such path that a page names must exist, and each `agents-remember` command and
each tool that a page names must be registered.

Two rules follow from the check.

- **Links.** A page under `docs/` links another page under `docs/` by a relative path. It
  links a file outside `docs/` by an absolute link of the form
  `https://github.com/Foxfire1st/agents-remember/blob/main/<path>`, with `/tree/main/` for a
  folder. `mcp/README.md` uses absolute links only.
- **Docs-check markers.** A docs-check marker with a reason allows every finding of its kind on the lines it covers. The
  kind `path` allows a missing path and the kind `identifier` an identifier. On the same line
  a docs-check marker covers that line. Alone on a line it covers the next line, or the code
  block that the next line opens. Advice: use a docs-check marker only where no other wording
  is possible.

The two docs-check markers have these forms:

```markdown
<!-- docs-check: path: <reason> -->
<!-- docs-check: identifier: <reason> -->
```

A public document describes the product as the tree has it. When a change alters a command, a
tool, a setting or a label of the dashboard, change the pages that name it in the same pull
request.

## A sandbox of the whole product

`scripts/pnt-sandbox.py` builds and runs a self-contained copy of the product in one <!-- docs-check: identifier: the name of the script in the repository -->
disposable folder: a coordination root, a small code repository, the dashboard and the host.
It uses ports and folders of its own. The wheel does not ship it.

<!-- docs-check: identifier: the name of the script in the repository -->
```bash
python3 scripts/pnt-sandbox.py build
python3 scripts/pnt-sandbox.py start <checkout>
python3 scripts/pnt-sandbox.py check
python3 scripts/pnt-sandbox.py stop
python3 scripts/pnt-sandbox.py reset
```

`start` runs the product of the checkout `<checkout>`, for example this checkout. It builds the
sandbox first when that is not done. It creates the checkout's `mcp/.venv` when it is missing
and builds the checkout's dashboard when the placed build is missing or stale. `build` and
`check` use the checkout that holds the script, unless `--checkout` names another. Every
command takes `--sandbox <folder>`; the default is `~/.local/state/ar-pnt/sandbox`. <!-- docs-check: identifier: the name of the script in the repository -->
`build`, `check`, `start` and `stop` take `--host-port` and `--dashboard-port`, with the
defaults 6820 and 9797. [scripts/pnt_sandbox](scripts/pnt_sandbox/README.md) describes the rules of use and <!-- docs-check: identifier: the name of the script in the repository -->
what the sandbox cannot isolate.

## Making a change

1. Name the rule, step, example or behaviour that is wrong or missing.
2. Make the smallest change that repairs it as a whole.
3. Change the tests, the examples and the pages that would otherwise describe the old
   behaviour, in the same pull request.
4. Say in the description what changes for a user.

Advice: for a large change to a workflow, open a discussion or a draft pull request before
you write it.

A good contribution repairs a defect in the tool server, the command line or the dashboard,
merges overlapping tests, makes a skill's scope and outputs plainer, or corrects a page that
differs from the product.

When you change a skill, state when to use it, what it reads, what it produces and what it is
not for. When you change where cards stand, keep the rule that a card stands at the mirrored
path of the source file it describes ([Concepts](docs/concepts.md)).

Write instructions and pages for both people and agents: say who does what, in which order
and within which limits. Use an example where it removes a doubt. State a rule once.
Describe what the product does, not what it may do later.

### Before you open the pull request

- The hooks pass.
- The tests that your change reaches pass.
- `python3 scripts/check-public-docs.py` reports no finding on a page you touched.
- The generated copies are current.
- The description names every change of behaviour.
