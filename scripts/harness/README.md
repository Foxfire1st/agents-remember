# Harness starter package source

One definition of everything the eight self-hosted harness starter packages share.
`scripts/sync-harness.py` fans it out; nothing here is imported at run time.

```text
render_starter.py         fragment library for the eight render-starter.py programs
session_start_hook.py     fragment library for the four session-start hook scripts
shared/                   bodies copied out verbatim, or wrapped in per-harness framing
```

45 files across the nine trees are generated from the seven sources here.

Edit here, then run:

```bash
python3 scripts/sync-harness.py          # write every generated file
python3 scripts/sync-harness.py --check  # verify only; non-zero on drift
```

`mcp/tests/test_sync_harness.py` runs the same check, so drift fails the test suite
rather than waiting for someone to remember the script.

## Why generate instead of import

A starter package is copied into a user's workspace and run from there. It cannot
import a shared module out of this repository, so each program has to stay a single
self-contained file. Sharing therefore happens at generation time.

## Why the fragment libraries are real Python

Both libraries type-check and lint as ordinary modules, so the shared body is verified
once as a whole instead of once per copy. Every generated program is a subset of a
checked module plus a constants block, and `sync-harness.py` derives each program's
imports from the fragments it actually emits — an import cannot be left behind when a
fragment moves.

## What is shared and what is per-harness

Shared, one definition here:

| Artifact | Copies it replaces |
| --- | --- |
| `render_starter.py` shared fragments | the body of 8 `render-starter.py` programs |
| `session_start_hook.py` shared fragments | the body of 4 hook scripts |
| `shared/render-starter.sh` | 8 byte-identical copies |
| `shared/render-starter.ps1` | 8 byte-identical copies |
| `shared/agents-remember-settings.json` | 8 byte-identical copies |
| `shared/session-start-directive.md` | 6 copies: 4 hook directives, Copilot instructions, Cursor rule |
| `shared/workspace-directive.md` | 3 copies: `HERMES.md`, `GEMINI.md`, OpenClaw `AGENTS.md` |

The last two are the same directive with one deliberate difference. Files read from
inside the workspace name `ar-coordination/AGENTS.md` relatively; the context files
Hermes, Antigravity and OpenClaw mirror to the workspace root carry the rendered
absolute path and say the rules are workspace instructions. Which body a harness takes
is per-harness; the body is not. Their framing — Cursor's front matter, the
`# … Workspace Instructions` headings, the `@`-include lines, Copilot's note about
where the path resolves — is declared as `prologue`/`epilogue` in the same table.

Per-harness, declared once each in `sync-harness.py`'s `HARNESSES` table because the
harness genuinely requires it:

- **`render_<harness>`** — the rendering steps, one per harness. These are the real
  differences: which files get placeholder substitution, and in what order.
- **`command_string`** (Codex, Cursor, VS Code) — those three embed the hook as one
  command string; Claude Code takes interpreter and script as separate fields.
- **`toml_basic_string_content`** (Codex) — Codex configuration is TOML, so an embedded
  Windows path has to be escaped for a TOML basic string.
- **`write_context_file`** (Hermes, Antigravity) — both read their context file from the
  workspace root, so the rendered template is mirrored out with a merge guard.
- **`render_claude_settings`, `render_cursor_hooks`, `render_vscode_hooks`** — three
  different hook-configuration schemas: nested `hooks.SessionStart[].hooks[]` with
  `command` + `args`, a flat `hooks.sessionStart[].command` string, and a `command` plus
  a per-platform `windows`/`osx`/`linux` override.
- **`hook_script_path`, `vscode_root`** (VS Code) — this starter ships as
  `.github-vscode/` so it does not collide with a repository's own `.github/`, but VS
  Code reads the hook from `.github/` and its MCP settings from `.vscode/`. The starter
  is the one that spans two folders.
- **`started_inside_workspace`** (Codex hook) — Codex registers session-start hooks
  globally, so the hook fires for unrelated sessions and has to scope itself.
- **payload envelope** — Cursor reads `{"additional_context": ...}`; Claude Code, Codex
  and VS Code read `{"hookSpecificOutput": {...}}`.
- **`PLACEHOLDERS`, `TARGET_FILES`, `HARNESS_LABEL`** — data, emitted as a constants
  block so `validate` and `main` are identical in every generated file.

Everything else in a starter package — `config.toml`, `settings.json`, `mcp.json`,
`mcp_config.json`, `hooks.json`, `openclaw.merge.json`, `config.yaml`,
`extensions/agents-remember-start.ts` — is a genuine single-copy per-harness file:
different serialisation formats (JSON, TOML, YAML), different schema keys
(`mcpServers` vs `servers` vs `mcp.servers`), and a different folder name baked into
every path. `sync-harness.py` does not manage those and leaves them alone.

The skill trees under each starter package are generated too, by
`scripts/sync-skills.py` from root `skills/`.
