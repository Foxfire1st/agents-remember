# dashboard/ — Mission-Control Frontend

This is the **frontend sub-project** for the Agents Remember dashboard — a root-level
folder (a sibling of `mcp/`, `skills/`, `providers/`) so the UI dimension of the project is
visible at a glance rather than buried inside the Python package.

## Status

The React/Vite cockpit lives here with its own `package.json` and toolchain. The Python
server (`agents_remember.serving`, run via `agents-remember dashboard`) mounts this
project's production build at `/`. There is no placeholder and no fallback UI: the server
serves the built cockpit or it serves an error that names the build command.

## Build → ship contract

The wheel and sdist ship the **built** bundle as package data so no Node build is needed at
`pip install` time. The release job (`.github/workflows/publish-mcp-to-pypi.yml`) builds the
frontend and then runs `scripts/sync-dashboard.py`, which places the build output into the
package alongside a `dashboard.fingerprint` sidecar:

```
dashboard/dist/  ──(scripts/sync-dashboard.py)──▶  mcp/src/agents_remember/package_data/dashboard/
                                                   (StaticFiles-mounted at "/" by serving/static.py)
                                                +  package_data/dashboard.fingerprint
                                                   (reported as servingBuild.dashboardBuild)
```

**The built bundle is not in version control** (master decision OQ6, 2026-07-31). A 28 MB
generated tree in git is what made the pre-commit gate unpassable and trained the
`--no-verify` habit, so `package_data/dashboard/` and `package_data/dashboard.fingerprint`
are git-ignored (`.gitignore:23-24`), as are `dist/` and `node_modules/`.

Two consequences worth stating plainly:

- **A source checkout has no cockpit until you build one.** Installing from source without
  Node yields no dashboard: `GET /` answers `503` naming the directory it expected and the
  command that produces it. The API under `/api` is unaffected. The remedy is
  `npm --prefix dashboard ci && npm --prefix dashboard run build && python3 scripts/sync-dashboard.py`.
- **`sync-dashboard.py` never no-ops.** It refuses (exit 1) when `dashboard/dist` is absent,
  and it refuses when `dashboard/dist` does not carry the build-input fingerprint computed
  from the dashboard source as it stands now — the value `vite.config.ts` compiles into the
  bundle as `__AR_DASHBOARD_BUILD__`. So the sidecar is a value read back out of the bundle,
  never a stamp applied over one.

`npm run e2e:production` runs Playwright against `npm run preview`, which serves
`dashboard/dist`, and its fixture reads `package_data/dashboard.fingerprint`; run the full
build-and-place chain above before it.
