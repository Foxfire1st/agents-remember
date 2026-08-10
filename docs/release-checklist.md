# Release Checklist

Run through this before tagging a public `agents-remember-mcp` release. Release notes live in
GitHub Releases (there is no `CHANGELOG.md`); the canonical tag is `mcp-vX.Y.Z`.

## Quality

- [ ] `python -m agents_remember.code_quality.check` passes (Ruff — complexity rules
      included — `ruff format --check`, file size, Pyright, the full pytest suite, and
      mandatory CRAP/diff-coverage enforcement). Cheap rails precede pytest; the two Radon
      steps are reports and cannot fail it, and no rail carries a baseline or exemption list.
- [ ] CI is green on the PR across the Python `3.11 / 3.12 / 3.13` matrix.

## Version sync (must all match)

- [ ] `mcp/pyproject.toml` `version`
- [ ] `mcp/src/agents_remember/mcp/__init__.py` `SERVER_VERSION` fallback
- [ ] `README.md` Status section line

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
- [ ] Push the `mcp-vX.Y.Z` tag at the merged commit; confirm `publish-mcp-to-pypi.yml` succeeds and the
      version resolves on PyPI.
- [ ] Create the GitHub Release on the `mcp-vX.Y.Z` tag.
