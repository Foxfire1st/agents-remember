# dashboard/ — Mission-Control Frontend

This is the **frontend sub-project** for the Agents Remember dashboard — a root-level
folder (a sibling of `mcp/`, `skills/`, `providers/`) so the UI dimension of the project is
visible at a glance rather than buried inside the Python package.

## Status

- **Slice 04 (serving layer):** the Python server is live
  (`agents_remember.serving`, run via `agents-remember dashboard`). It serves a
  hand-authored **placeholder** at `mcp/src/agents_remember/package_data/dashboard/index.html`
  that subscribes to the SSE stream and prints events — proof the transport works end to end.
- **Slice 05 (cockpit v1):** the real React/Vite cockpit lands here, with its own
  `package.json` / toolchain. Its production build (`dashboard/dist/`) is the artifact that
  ships.

## Build → ship contract

The Python wheel ships the **built** bundle as package data so no Node build is needed at
`pip install` time. `scripts/sync-dashboard.py` copies the build output into the package,
mirroring `scripts/sync-runtime.py` / `scripts/sync-skills.py`:

```
dashboard/dist/  ──(scripts/sync-dashboard.py)──▶  mcp/src/agents_remember/package_data/dashboard/
                                                   (StaticFiles-mounted at "/" by serving/static.py)
```

Until the slice-05 build exists, `dashboard/dist/` is absent and `sync-dashboard.py` no-ops,
leaving the committed placeholder in place. `dist/` and `node_modules/` are build artifacts
(git-ignored); the shipped bundle under `package_data/dashboard/` is committed.
