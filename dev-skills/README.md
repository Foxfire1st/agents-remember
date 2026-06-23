# dev-skills/

Developer-only Claude Code skills. **Not distributed.**

`scripts/sync-skills.py` only copies the canonical `skills/` tree into the MCP `package_data`
(`mcp/src/agents_remember/package_data/runtime/skills/`) and every harness starter package
(`.claude/`, `.codex/`, `.cursor/`, …). Nothing under `dev-skills/` is synced, shipped, or required by
any Agents Remember user, and the pre-commit `sync-skills.py --check` gate never inspects it.

These skills are also **outside the memory `pathRules` include scope**, so they carry no onboarding
sidecars and are not indexed by the providers.

## Why this tree exists

Some skills are internal tooling for building Agents Remember itself — they review the product, drive
dev workflows, or scaffold work that an end user has no reason to install. Shipping them in the
starter packages would only bloat every harness install. They still belong in the repo (governed,
versioned, code-reviewed, landed via PR), just not in the distribution path.

## Installing one by hand

When a dev-skill is complete and you want to use it, copy its folder into your harness skills
directory, e.g.:

```bash
cp -r dev-skills/<skill-name> ~/.claude/skills/<skill-name>   # or the repo-local .claude/skills/
```

Then restart the harness so it registers. Do **not** add it to `sync-skills.py` or move it into
`skills/` unless you have decided it should ship to users.

## Current dev-skills

- `dashboard-experience-review/` — reviews the agents-remember cockpit dashboard like a user:
  discovers workflow scenarios, detects missing views, and judges UX + observability quality,
  conducting the installed design/a11y/motion skills. Findings only.
