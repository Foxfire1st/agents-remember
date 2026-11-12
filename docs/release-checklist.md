# Release Checklist

This page lists what a maintainer checks before tagging a release of the package
`agents-remember-mcp`. A release is a tag of the form `mcp-v<version>` on a commit of `main`. The
release job `.github/workflows/publish-mcp-to-pypi.yml` runs for such a tag and publishes the
package.

The repository holds no changelog file. The release notes stand in the GitHub release of the tag.

In this page, `<version>` is the version of the release, as `mcp/pyproject.toml` states it.

## Checks And Certification

The Git hooks of this repository run deterministic checks and no test: the generated copies, Ruff,
the format check, Pyright, the two test catalogs, and the dashboard's code generation, lint and
type check. The pull-request check on GitHub runs the same checks.

No hook, no closeout, no landing, no pull request and no release job starts a test suite or a
certification. A certification is the full run of the repository's checks and tests in a clean
container. It runs only when a contributor asks for it.
[Git hooks](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#git-hooks),
[Tests](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#tests) and
[Certification](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#certification)
describe the three.

- [ ] The pull-request check `.github/workflows/quality-checks.yml` passed on the pull request
      that carries the release commit. It runs `.githooks/_gate.sh targeted`.
- [ ] The tests that you chose for this release passed. No job runs them for you. The choice
      is yours: `mcp/.venv/bin/python -m pytest -m ""` runs every Python test, and
      `npm --prefix dashboard test` runs the dashboard's unit and component tests.
- [ ] You decided whether this release needs a certification. If it does, you asked for a run
      with `--mode full` on the release commit, and the run passed.

## Version

Each of these places carries `<version>`:

- [ ] `mcp/pyproject.toml`, the key `version`.
- [ ] `mcp/src/agents_remember/kernel/primitives/version.py`, the value that
      `_resolve_server_version` returns for a source checkout without an installed package.
- [ ] `README.md`, the section "Status".

## Generated Copies

- [ ] `python3 scripts/sync-skills.py --check`, `python3 scripts/sync-runtime.py --check` and
      `mcp/.venv/bin/python scripts/sync-projection-types.py --check` report no difference.
- [ ] `docs/tool-details.md` equals its copy
      `mcp/src/agents_remember/package_data/runtime/docs/tool-details.md`.

[Generated copies](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#generated-copies)
names the source of each copy.

## The Host

The package pins one version of the host, Paseo, and one version of Node for it. The pin stands
in three files of the folder `mcp/src/agents_remember/package_data/paseo_host/`: `contract.json`,
`package.json` and `package-lock.json`. The plug-in of Agents Remember lies in
`mcp/src/agents_remember/package_data/paseo_plugin/`. Its `package.json` pins the plug-in SDK
`@getpaseo/plugin` to the host version.

A release that changes the host version or the Node version checks every box of this section. A
release that changes neither checks the fourth box only, the run of
`python3 scripts/check-host-contract.py`.

- [ ] The host version is the same in three places: in `contract.json`, in the dependency of the
      host's `package.json`, and in the entry `@getpaseo/plugin` of the plug-in's `package.json`.
- [ ] `package-lock.json` is generated as a whole for the host's `package.json`. Copy
      `package.json` into a scratch folder. With the npm of the Node version that `contract.json`
      names, run `npm install --package-lock-only --ignore-scripts --prefix <scratch-folder>`.
      Copy the resulting `package-lock.json` back. An installation runs `npm ci` on this file and
      resolves no version of its own.
- [ ] For a change of the Node version, `contract.json` carries three values that belong
      together: the version, the URL of the official `linux-x64` archive of that version, and the
      SHA-256 of the archive. The contract names no other platform.
- [ ] `python3 scripts/check-host-contract.py` prints
      `host contract, plugin SDK and whole npm lock agree`.
- [ ] `mcp/.venv/bin/python -m pytest mcp/tests/test_host_release_contract.py -n=0` passes.
- [ ] The plug-in passes its own two scripts. In a scratch copy of the plug-in folder, run
      `npm install`, which installs the development dependencies, then `npm run typecheck` and
      `npm test`. No hook and no job runs these scripts, and the plug-in has no lock file.
- [ ] Most files in the plug-in's folder `client/` rely on behaviour of the host that the
      plug-in SDK does not cover, and each of them names that behaviour in a comment with the
      word "unsupported". `client/sidebar.ts` relies on the host's menu button and stored panel
      state, which the comment of `client/look.ts` names; `client/hierarchy.ts` uses the public
      SDK only. In the plug-in folder, `grep -n -i unsupported client/*.ts` lists the comments.
      Each named behaviour holds in the pinned host version. No script checks this against the
      host. Advice: confirm each behaviour in the sandbox of the last box, where the pinned host
      runs with the plug-in.
- [ ] The pinned host starts in a sandbox with the plug-in loaded. Run
      `python3 scripts/pnt-sandbox.py start <checkout>` on the release checkout. Then <!-- docs-check: identifier: the name of the script in the repository -->
      `<checkout>/mcp/.venv/bin/agents-remember paseo status --config <sandbox>/settings/agents-remember-settings.json`
      reports the state `running` in its field `plugin`.

The sandbox has folders and ports of its own. Do not provision the host of the release into your
own installation for this check.
[A sandbox of the whole product](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#a-sandbox-of-the-whole-product)
describes the sandbox.

## Install And First Run

These checks run on a wheel that is built from the release checkout. Build the wheel from the
root of that checkout with the commands that
[step 1 of Getting started](getting-started.md#1-install-the-package) gives for a checkout of
the repository, up to `uv build --wheel mcp`:

```bash
npm --prefix dashboard ci
npm --prefix dashboard run build
python3 scripts/sync-dashboard.py
uv build --wheel mcp
```

- [ ] The wheel installs into an empty virtual environment of Python 3.14, and the tool server
      prints its help text there. The help text names the one option `--config`.

      ```bash
      python3 -m venv <scratch-folder>/venv
      <scratch-folder>/venv/bin/python -m pip install mcp/dist/agents_remember_mcp-<version>-py3-none-any.whl
      <scratch-folder>/venv/bin/agents-remember-mcp --help
      ```

- [ ] `python3 scripts/pnt-sandbox.py start <checkout>` ends without a refusal. The start creates <!-- docs-check: identifier: the name of the script in the repository -->
      a coordination root, a memory repository and task documents through the tool server of the
      checkout, with the tools `runtime_install`, `memory_init`, `memory_baseline_adopt` and
      `task_doc`. It then provisions the host through `runtime_install` and starts the dashboard.

## Documentation

- [ ] `python3 scripts/check-public-docs.py` passes.

[Documentation](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#documentation)
describes what the check refuses.

## Tag And Publish

- [ ] The release commit is on `main`, merged through a pull request.
- [ ] The tag `mcp-v<version>` is pushed on that commit. The release job refuses a tag whose
      commit `origin/main` does not contain. It builds the dashboard and places it, builds the
      wheel and the source distribution, and checks that both hold the dashboard. It then
      installs the wheel in a clean environment, starts the tool server with `--help`, and
      publishes the two files to PyPI. It runs no test and no certification.
- [ ] The release job passed, and PyPI lists `<version>`.
- [ ] The GitHub release of the tag exists and holds the release notes.
