# Release Checklist

Run through this before tagging a public `agents-remember-mcp` release. Release notes live in
GitHub Releases (there is no `CHANGELOG.md`); the canonical tag is `mcp-vX.Y.Z`.

## Quality

- [ ] Every leaf was accepted once by the lifecycle-owned Dagger `mode=targeted`
      gate before its commit, and the master was accepted once by Dagger `mode=full`
      at integration. Do not rerun either gate for release.
- [ ] The pull-request-only deterministic repository check is green. It validates
      generated copies, lint, formatting, and types; it does not run acceptance tests.

## Version sync (must all match)

- [ ] `mcp/pyproject.toml` `version`
- [ ] `mcp/src/agents_remember/mcp/__init__.py` `SERVER_VERSION` fallback
- [ ] `README.md` Status section line

## Shipped host contract and whole lock

- [ ] An intentional host release updates paseo_host/contract.json, the exact
      dependency in paseo_host/package.json and the plugin development SDK pin
      together. The plugin's unsupported host adapters require separate release
      qualification; do not move only the top package.
- [ ] In an isolated release staging directory, copy the updated host manifest
      and use the contract's checked product Node/npm to regenerate the complete
      package-lock.json with npm install --package-lock-only --ignore-scripts
      --prefix <staging>. Copy that whole artifact into paseo_host/; production
      installs use npm ci and never resolve the release's ranges again.
- [ ] For an intentional Node change, update its exact version, matching official
      Linux-x64 archive URL and SHA-256 together; no other platform is claimed.
- [ ] Run python scripts/check-host-contract.py and the focused host-release
      contract tests. The check binds the installed host lock entry, manifest,
      plugin SDK, every locked integrity/resolution, and Node version/platform URL.

## Install & first-run smoke

- [ ] Fresh `uvx agents-remember-mcp==<version>` starts and serves the tool list.
- [ ] `runtime_install(dry_run=true)` previews cleanly; `runtime_install(dry_run=false, install_provider_deps=false)` applies.
- [ ] `skills_install(dry_run=true)` previews; `skills_install(dry_run=false)` still works as a maintenance/manual install path.
- [ ] `python3 scripts/sync-skills.py --check` confirms root `skills/`, MCP package data, and harness package skill folders are in sync.
- [ ] Provider-disabled setup works.
- [ ] Provider-enabled setup reports useful diagnostics when Docker is unavailable (does not hang).

## Docs

- [ ] No stale references to removed APIs or arguments remain.
- [ ] Quickstart, install pages, and the MCP tool reference match the shipped tool surface.

## Tag & publish

- [ ] Land the release on `main` via PR (PR-gated `main`).
- [ ] Push the `mcp-vX.Y.Z` tag at the merged commit; the publish workflow first
      proves the tag is reachable from `origin/main`, then builds and publishes without
      rerunning acceptance. Confirm it succeeds and the version resolves on PyPI.
- [ ] Create the GitHub Release on the `mcp-vX.Y.Z` tag.
