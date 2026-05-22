# Task: Harness MCP Compatibility And Docs Plan

**Status:** planning
**Repo:** agents-remember-md
**Type:** Docs
**Created:** 2026-05-22T17:57
**Parent Roadmap:** `/home/mohamedreadone/Projects/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/task.md`
**Roadmap Slot:** Add-on A1

---

## Objective

As an add-on to the Agentic Context Kernel MCP roadmap, research the MCP configuration model for every harness currently documented by `agents-remember-md`, then plan the documentation changes that make `ar-coordination/.venv/bin/python -m agents_remember.mcp` the normal harness-facing entrypoint.

The deeper goal is to move harness-specific setup away from Python script choreography and skill symlink workarounds. Harness docs should mainly explain how that harness registers a local stdio MCP server, while skill installation becomes a smaller instruction layer that teaches agents what the MCP operations mean.

---

## Requirements

- Cover the harnesses already listed in the source repo install docs: Codex, Claude Code, Cursor, Windsurf, VS Code + GitHub Copilot, Hermes.md, Pi.dev, and OpenClaw.
- Keep this task aligned with the parent MCP roadmap rather than treating it as an unrelated documentation project.
- Do not block Phase 1 Codex implementation on the full harness matrix; this add-on owns the later harness-specific documentation pass.
- Use current official/vendor documentation for each harness before changing install docs.
- Treat `ar-coordination/.venv` as the stable runtime anchor. Normal configs should launch the coordinator runtime Python and pass `-m agents_remember.mcp`.
- Avoid repeating the coordination root in normal MCP args. The MCP package should infer the coordination root from the runtime installation; explicit `--coordination-root` belongs to tests, workbenches, and debugging.
- Separate each harness's MCP config shape from the shared Agents Remember runtime model.
- Plan how source docs change across `README.md`, `docs/getting-started.md`, `docs/install/*.md`, and `docs/reference/skills.md`.
- Add a dedicated skills chapter explaining that skills remain useful as model-facing workflow instructions, but should stop teaching ad hoc Python command orchestration once MCP operations exist.
- Keep the scope documentation/planning-only until the developer approves implementation.
- Do not introduce broad compatibility layers or fallback behavior unless a harness's documented MCP behavior makes the need concrete.

---

## Initial Research Notes

| Harness | Current source repo doc | Current MCP configuration shape to plan around |
| --- | --- | --- |
| Codex | `docs/install/codex.md` | Codex supports MCP in CLI and IDE extension. Config lives in `~/.codex/config.toml` or trusted project `.codex/config.toml`. Stdio servers use `[mcp_servers.<name>]` with `command`, `args`, optional `env`, `env_vars`, `cwd`, and tool policy fields. CLI setup is available through `codex mcp add <name> -- <command...>`. |
| Claude Code | `docs/install/claude-code.md` | Claude Code supports local, project, and user MCP scopes. Project scope writes `.mcp.json` with `mcpServers`; stdio entries use `type: "stdio"`, `command`, `args`, and optional `env`. Project-scoped servers require trust approval. |
| Cursor | `docs/install/cursor.md` | Cursor uses `.cursor/mcp.json` for project config and `~/.cursor/mcp.json` for global config. The documented shape is `mcpServers` with local stdio `command`/`args`/`env` and remote `url`/`headers`. Cursor also supports a CLI MCP management surface. Confirm whether `type: "stdio"` should be included in our examples or omitted for compatibility with existing Cursor examples. |
| Windsurf | `docs/install/windsurf.md` | Windsurf Cascade uses `~/.codeium/windsurf/mcp_config.json` with `mcpServers`. Local servers use `command`, `args`, and optional `env`. Team whitelist policies can require exact server IDs plus exact command/args, so docs should make the venv command stable and copy-pasteable. |
| VS Code + GitHub Copilot | `docs/install/vscode-copilot.md` | VS Code uses `.vscode/mcp.json` or user profile MCP config with a top-level `servers` object, not `mcpServers`. Stdio entries use `type: "stdio"`, `command`, `args`, optional `env`/`envFile`, and optional sandbox fields. GitHub Copilot CLI separately uses `~/.copilot/mcp-config.json` with `mcpServers`, `type: "local"` or STDIO, `command`, `args`, `env`, and `tools`. The VS Code install page may need either a note or a split section. |
| Hermes.md | `docs/install/hermes.md` | Hermes reads MCP config from `~/.hermes/config.yaml` under `mcp_servers`. Stdio entries use `command`, `args`, and optional `env`; HTTP entries use `url` and `headers`. Hermes prefixes MCP tools as `mcp_<server>_<tool>`, so docs should note how Agents Remember tool names will appear. |
| Pi.dev | `docs/install/pi.md` | Pi core intentionally does not include built-in MCP. MCP requires an extension/package path. Current Pi package docs show `pi-mcp-adapter` using shared `.mcp.json` and `~/.config/mcp/mcp.json`, with Pi override files at `~/.pi/agent/mcp.json` and `.pi/mcp.json`; `pi-mcp-extension` uses `~/.pi/agent/mcp.json` and `.pi/mcp.json` with `mcpServers`. The docs task must choose which Pi path to recommend. |
| OpenClaw | `docs/install/openclaw.md` | OpenClaw has an MCP client-side registry under `mcp.servers`, managed by `openclaw mcp list/show/set/unset`. Stdio entries use `command`, `args`, optional `env`, and `cwd`/`workingDirectory`. These commands save config only and do not validate target reachability. OpenClaw can also run as an MCP server, but this task concerns configuring Agents Remember as a server consumed by OpenClaw-launched runtimes. |

---

## Implementation Steps

### S1 — Compatibility Matrix

- [ ] Produce a verified per-harness MCP compatibility matrix.
  - [ ] Confirm each source repo install page still corresponds to an actively documented harness.
  - [ ] Record the exact config file path, top-level key, stdio fields, env handling, and restart/reload behavior for each harness.
  - [ ] Mark Pi.dev's MCP support as extension/package-mediated rather than core-native.
  - [ ] Mark VS Code Copilot and GitHub Copilot CLI as related but different configuration surfaces.
  - [ ] Capture any mandatory trust, whitelist, sandbox, or approval behavior that affects documentation.
  - [ ] Verify the matrix against official docs before implementation approval.

### S2 — Documentation Change Plan

- [ ] Plan the docs update set for the MCP-first runtime model.
  - [ ] Add a shared canonical MCP entrypoint explanation near the quickstart/runtime setup docs.
  - [ ] Update each `docs/install/*.md` page with a harness-specific MCP config snippet.
  - [ ] Reduce per-harness skill exposure instructions to the minimum still required for that harness.
  - [ ] Add smoke-test commands or UI checks per harness where the vendor docs provide them.
  - [ ] Keep direct script invocation examples only where they remain operator/debug surfaces.
  - [ ] Identify whether `docs/install/README.md` needs a harness compatibility table.

### S3 — Skills Streamlining Plan

- [ ] Plan how skill installation changes once MCP is the normal operation surface.
  - [ ] Define skills as workflow semantics and retrieval doctrine, not Python runtime wiring.
  - [ ] Replace skill instructions that teach scripts directly with MCP operation names once those operations exist.
  - [ ] Keep script references only as manual CLI/debug alternatives and make that status explicit.
  - [ ] Decide whether a single shared skill install section can replace most repeated per-harness skill examples.
  - [ ] Note any harnesses where native skills remain awkward or unsupported and workspace instructions must carry more of the load.

### S4 — Review Gate

- [ ] Prepare the implementation-ready plan and stop for developer approval.
  - [ ] Include before/after examples for the shared quickstart and at least two representative harness pages.
  - [ ] Call out unresolved compatibility questions separately from confirmed vendor facts.
  - [ ] Do not edit source docs until the developer approves this task's implementation phase.

---

## Proposed Code Examples

### E1 — Shared Stdio Entry Shape

Distinct change covered: The common command/args contract every harness-specific example should express in its own config syntax.

Why this example is included: This is the core simplification: users point at the coordinator runtime venv and do not repeat the coordinator path in server args.

```json
{
  "command": "/path/to/ar-coordination/.venv/bin/python",
  "args": ["-m", "agents_remember.mcp"]
}
```

### E2 — Representative Harness Snippets

Distinct change covered: The same server expressed through different harness config shapes.

Why this example is included: The docs change should make the differences obvious without changing the underlying Agents Remember runtime contract.

```toml
[mcp_servers.agentsRemember]
command = "/path/to/ar-coordination/.venv/bin/python"
args = ["-m", "agents_remember.mcp"]
```

```json
{
  "mcpServers": {
    "agents-remember": {
      "type": "stdio",
      "command": "/path/to/ar-coordination/.venv/bin/python",
      "args": ["-m", "agents_remember.mcp"]
    }
  }
}
```

```json
{
  "servers": {
    "agents-remember": {
      "type": "stdio",
      "command": "/path/to/ar-coordination/.venv/bin/python",
      "args": ["-m", "agents_remember.mcp"]
    }
  }
}
```

```yaml
mcp_servers:
  agents_remember:
    command: "/path/to/ar-coordination/.venv/bin/python"
    args: ["-m", "agents_remember.mcp"]
```

---

## Decision Log

| Date-Time          | Decision           | Rationale |
| ------------------ | ------------------ | --------- |
| 2026-05-22T17:57 | Create a dedicated light-task artifact for harness MCP compatibility documentation. | The Phase 1 MCP task identified harness setup as a research need, but the compatibility and docs-planning work spans all existing install pages and deserves its own focused task. |
| 2026-05-22T17:57 | Treat MCP configuration as harness-specific and the runtime command as shared. | The web research shows each harness differs mainly in config file location and schema; Agents Remember should keep one stable stdio process contract. |
| 2026-05-22T17:57 | Keep skills, but shrink their installation and runtime responsibilities. | Skills still teach workflow meaning, but MCP should own reliable host-side operations so skills no longer need to preserve brittle Python invocation choreography. |
| 2026-05-22T17:57 | Classify this as add-on A1 for the Agentic Context Kernel MCP roadmap. | The task supports the existing MCP/refactor task list and cuts across Phase 1 harness setup plus Phase 4 skill rewiring rather than replacing either phase. |
| 2026-05-22T18:24 | Defer the full harness-specific documentation pass until after the initial Codex MCP implementation path is proven. | The harness variants are straightforward config-schema differences over one runtime command, so the roadmap should prove the MCP runtime first and then polish the broader install docs. |

---

## Open Questions

- Should the Pi.dev docs recommend `pi-mcp-adapter`, `pi-mcp-extension`, or a narrower "MCP support requires a Pi extension; choose one during implementation" note?
- Should `docs/install/vscode-copilot.md` cover GitHub Copilot CLI in the same page, or should Copilot CLI become its own install page?
- Should every harness page include both a config-file snippet and the harness CLI/UI setup path when one exists?
- How thin should skill installation become in v1: a single shared section plus harness notes, or still one short skill subsection per harness?

---

## References

- `/home/mohamedreadone/Projects/agents-remember-md/README.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/codex.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/claude-code.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/cursor.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/windsurf.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/vscode-copilot.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/hermes.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/pi.md`
- `/home/mohamedreadone/Projects/agents-remember-md/docs/install/openclaw.md`
- `/home/mohamedreadone/Projects/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/phase-01-mcp-design-and-scaffolding.md`
- `/home/mohamedreadone/Projects/ar-coordination/tasks/agents-remember-md/260521_provider-lifecycle-mcp-bridge/agentic-context-kernel-mcp-design-note.md`
- OpenAI Codex MCP docs: <https://developers.openai.com/codex/mcp>
- Claude Code MCP docs: <https://code.claude.com/docs/en/mcp>
- Cursor MCP docs: <https://docs.cursor.com/context/model-context-protocol>
- Windsurf Cascade MCP docs: <https://docs.windsurf.com/windsurf/cascade/mcp>
- VS Code MCP configuration reference: <https://code.visualstudio.com/docs/copilot/reference/mcp-configuration>
- GitHub Copilot CLI MCP docs: <https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers>
- Hermes MCP docs: <https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp>
- Hermes MCP config reference: <https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference>
- Pi core usage docs: <https://pi.dev/docs/latest/usage>
- Pi MCP adapter package docs: <https://pi.dev/packages/pi-mcp-adapter>
- Pi MCP extension package docs: <https://pi.dev/packages/pi-mcp-extension>
- OpenClaw MCP docs: <https://docs.openclaw.ai/cli/mcp>
