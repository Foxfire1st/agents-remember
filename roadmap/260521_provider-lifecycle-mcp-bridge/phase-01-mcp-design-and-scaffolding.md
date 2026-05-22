# Task: Phase 1 - MCP Design And Scaffolding

**Status:** planning
**Repo:** agents-remember-md
**Type:** Script | Config | Skill | Other
**Created:** 2026-05-22T00:07

---

## Objective

Design the first MCP server shape, installation path, configuration model, harness compatibility plan, and safety boundary after Phase 0 quality findings are reviewed.

---

## Request And Deeper Request

### Surface Request

Plan where the MCP lives, how it is installed, how harnesses configure it, and what the first server exposes.

### Deeper Request

Use MCP as the stable host-side controller boundary for context-kernel operations, while keeping Python services importable/testable and skills as the instruction layer.

### Highest-Leverage Framing

The first MCP should prove the architecture, not expose every operation. It should be small, typed, server-owned, and read-first.

### Assumptions

- Phase 0 findings will identify the service boundaries most worth extracting first.
- MCP setup differs across harnesses. Phase 1 should implement and smoke-test the Codex setup first; broader harness variants are tracked by the dedicated harness compatibility add-on task.
- Direct CLI usage can remain valid when justified, but must not become a duplicated contract.
- The first MCP sweep can be intentionally small: prove server startup, configuration, typed tool registration, and installed-runtime placement before provider/query behavior moves behind it.
- `stdio` is the likely first transport because local harnesses can launch the server host-side without exposing an HTTP endpoint.
- The isolated `ar-coordination-mcp-plus-refactor-workbench` removes the need to block mutation-capable wiring in early MCP phases; risky coordinator/runtime operations can be tested there before touching the real coordinator.

### Boundaries

- No arbitrary shell bridge.
- No caller-provided executable paths, working directories, or raw command strings.
- No compatibility fallback unless justified in this task file before implementation.
- No logging or progress output to stdout from the server process; stdout is the MCP protocol channel for local stdio servers.

---

## Requirements

- Research and implement the current Codex MCP setup path before Phase 1 implementation; defer broader harness-specific documentation to the harness compatibility add-on task.
- Decide source and installed runtime locations for the MCP server.
- Decide configuration inputs: coordination root, allowed repo ids, provider ids, timeout caps, transcript root.
- Define the first typed tool surface and safety rules.
- Decide how direct CLI usage coexists with MCP without duplicating lifecycle semantics.
- Use absolute paths in documented harness configuration examples.
- Treat the first compatibility target as local stdio MCP in Codex. Other harness-specific configuration variants belong to the dedicated harness compatibility add-on unless the developer promotes one into the current implementation slice.
- Give each installed `ar-coordination` root its own `.venv` for MCP/runtime Python dependencies and use that venv's Python as the normal harness command anchor.
- Keep the MCP Python SDK out of the source-repo dev-tools dependency path; declare runtime/MCP dependencies in source-owned `runtime/requirements.txt`, copy it to root `ar-coordination/requirements.txt`, then install those requirements into root `ar-coordination/.venv` when runtime dependency installation is explicitly enabled.
- Prefer a boring first server with `ping`/`server_info` style tools before exposing real context/provider operations.
- Allow mutation-capable provider/worktree tools to be wired once the MCP skeleton runs, provided they call pinned facades and are tested against the workbench first.
- Keep the server as a thin controller/API boundary over importable runtime modules; do not put deterministic business logic directly in MCP handlers.
- Produce a reviewable implementation plan before code changes.

---

## MCP Shape Notes

### MCP Mental Model

The MCP server is a host-launched process that exposes typed capabilities to an agent harness. For Agents Remember, the important capability types are:

- `Tools`: callable operations such as `ping`, `server_info`, `context_packet`, and later provider queries.
- `Resources`: read-only artifacts such as stored provider stdout/stderr or run results.
- `Prompts`: reusable prompt templates. These are not a first priority because Agents Remember skills already own model-facing workflow teaching.

The first implementation should be tool-first. Resource support can arrive with provider run artifacts in the read-surface phase.

### Source And Runtime Placement

Source package target:

```text
agents-remember-md/
  runtime/
    src/
      agents_remember/
        mcp/
          __main__.py
          server.py
          tools.py
          schemas.py
          safety.py
        controllers/
        kernel/
        providers/
        drift/
        worktrees/
    scripts/
      optional manual CLI wrappers
```

Installed runtime target:

```text
ar-coordination/
  .venv/
  src/
    agents_remember/
  system/settings.json
  temp/mcp-runs/
  providers/
  provider-data/
  memory-repos/
```

`ar-coordination/.venv` is the normal runtime anchor for MCP. Harnesses should point to that venv's Python and invoke the package module with `-m agents_remember.mcp`. The installed runtime must make `agents_remember` importable from that venv, for example by installing the runtime package into the venv or by adding a controlled runtime path during MCP setup. The MCP process should infer the coordination root from the venv location, so users do not repeat the same absolute `ar-coordination` path in both the interpreter and arguments. `--coordination-root` may exist as an explicit override for tests, workbench runs, or debugging, but it should not be required in the normal harness configuration.

Existing operational scripts remain facades over importable runtime modules as those modules are extracted. Script entrypoints can still exist for manual CLI use, but the MCP harness-facing entrypoint should be the installed module invocation from the coordinator runtime venv.

### Transport And Harness Configuration

Use stdio first unless Phase 1 research proves a target harness needs something else. Stdio avoids a local HTTP listener and matches the local-hosted, host-side process model that solves sandbox/process-namespace friction.

Representative Codex configuration shape:

```toml
[mcp_servers.agentsRemember]
command = "/home/user/projects/ar-coordination/.venv/bin/python"
args = ["-m", "agents_remember.mcp"]
```

Equivalent Codex CLI setup:

```bash
codex mcp add agentsRemember -- \
  /home/user/projects/ar-coordination/.venv/bin/python \
  -m agents_remember.mcp
```

The final docs must use absolute paths and must not rely on the harness current working directory. The normal config should require only one user-supplied absolute root-derived path: the runtime venv Python. Path wiring from that interpreter to the installed package and coordination root is an Agents Remember responsibility, not a user burden. Other harness schemas are documentation variants over the same command/args contract and are tracked in the add-on task.

### First Sweep

The first sweep should prove only the shell of the system:

- server process starts from the installed runtime
- configuration loads
- source/runtime import path works without skill-folder symlink tricks
- `ping` returns a trivial success result
- `server_info` reports server/config basics
- tests can launch the server or call the MCP tool functions without touching the real `ar-coordination`
- Codex can list or load the configured `agentsRemember` server from the installed runtime venv

Use `ar-coordination-mcp-plus-refactor-workbench` as the isolated coordination root for install/runtime experiments before any real coordinator reinstall is tested.

After that shell is proven, implementation can move the Python files into `runtime/src/agents_remember/...` and wire operations one by one behind current facades. The ordering still starts with boring server/config proof and then the context packet/session-start path, but provider/worktree mutation support is not categorically excluded from the early MCP work when it is isolated in the workbench.

### Safety Model

The MCP server owns executable paths, coordination roots, allowlists, timeout caps, and artifact roots. Model callers provide typed operation arguments, not shell authority.

Allowed first-shape examples:

```text
ping()
server_info()
context_packet(repo="agents-remember-md")
provider_status(repo="agents-remember-md")
```

Disallowed first-shape examples:

```text
run(command="python3 provider-lifecycle.py grepai start")
run(executable="/user/provided/path", args=["..."])
```

---

## Implementation Steps

### S1 - Research Harness Setup

- [ ] Gather the current Codex MCP installation/configuration pattern and route non-Codex variants to add-on A1.
  - [ ] Check Codex-relevant setup for `~/.codex/config.toml` and `codex mcp add`.
  - [ ] Confirm broader harness variants are covered by add-on A1 rather than this Phase 1 implementation slice.
  - [ ] Record compatibility implications for local stdio servers.

### S2 - Define Server Boundary

- [ ] Specify source/runtime placement and configuration.
  - [ ] Decide source module layout.
  - [ ] Decide installed runtime layout.
  - [ ] Decide the installer flag/name and default behavior for installing root `ar-coordination/requirements.txt` into `ar-coordination/.venv`.
  - [ ] Decide `python -m agents_remember.mcp` entrypoint behavior and optional script entrypoint behavior.
  - [ ] Decide stdio transport requirements and stdout/stderr/logging rules.
  - [ ] Define server-owned roots and allowlists.
  - [ ] Define transcript persistence behavior.

### S3 - Present First Implementation Slice

- [ ] Produce a reviewable Phase 1 implementation plan.
  - [ ] Identify exact modules/files to create or extract.
  - [ ] Include a minimal `ping`/`server_info` server slice before real provider/context tools.
  - [ ] Include safety model and fallback justification.
  - [ ] Include workbench install/test steps using `ar-coordination-mcp-plus-refactor-workbench`.
  - [ ] Stop for developer approval.

---

## Proposed Code Examples

### E1 - Typed MCP Tool Boundary

Distinct change covered: server-owned typed operation instead of raw shell execution.

Why this example is included: this is the core safety shape Phase 1 must preserve.

```python
def provider_query(repo_id: str, provider: str, operation: str, args: dict) -> ProviderTranscript:
    request = validate_provider_query(repo_id, provider, operation, args)
    return provider_service.run_query(request)
```

### E2 - Minimal Server Skeleton

Distinct change covered: first MCP sweep proves transport/configuration before real behavior.

Why this example is included: the first implementation should make the server run from the installed runtime without coupling that proof to provider refactoring.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Agents Remember")

@mcp.tool()
def ping() -> dict:
    return {"ok": True, "server": "agents-remember"}

@mcp.tool()
def server_info() -> dict:
    return {"ok": True, "capabilities": ["ping", "server_info"]}

def main() -> None:
    mcp.run()
```

---

## Decision Log

| Date-Time | Decision | Rationale |
| --- | --- | --- |
| 2026-05-22T00:07 | Prior "Phase 0 MCP Design Discussion & Scaffolding" is now Phase 1. | Quality baseline must come first. |
| 2026-05-22T11:50 | Use a minimal stdio MCP server as the first sweep. | It proves host-side transport, installation placement, and typed tool registration before provider/context behavior is moved behind MCP. |
| 2026-05-22T11:50 | Place MCP source under `runtime/src/agents_remember/mcp`. | This moves Python out of skill folders while keeping the installed runtime as the operational home. |
| 2026-05-22T11:50 | Use `ar-coordination-mcp-plus-refactor-workbench` for early runtime/MCP install tests. | The workbench lets installation and runtime layout fail safely without touching the real coordinator. |
| 2026-05-22T12:05 | Do not blanket-ban mutation-capable MCP wiring in early phases. | The workbench gives destructive/provider/worktree operations an isolated test target; the real safety rule is typed facade-backed operations, not read-only-only scope. |
| 2026-05-22T17:23 | Phase 1 minimum remains `ping` and `server_info`; `context.packet` is the first real context operation after the shell is proven. | The skeleton should prove host-side stdio, install placement, configuration, and typed tool registration before binding C-08/context-packet behavior. |
| 2026-05-22T17:23 | V1 compatibility targets stdio MCP plus Codex and Claude Code smoke configuration, not every possible harness. | Codex and Claude Code are the named harnesses in Phase 1 research; additional harnesses should not become mandatory without a concrete consumer need. |
| 2026-05-22T17:23 | Keep the MCP Python SDK dependency isolated until MCP installation is explicitly enabled. | Non-MCP runtime installation should not gain a new required dependency before the MCP entrypoint/install mode is selected and tested. |
| 2026-05-22T17:36 | Use `ar-coordination/.venv/bin/python -m agents_remember.mcp` as the normal harness entrypoint. | The venv path already anchors the installed coordinator runtime; users should not repeat the coordination root path or reason about internal script/module locations. |
| 2026-05-22T18:24 | Phase 1 implements the Codex MCP configuration path first; other harness variants move to add-on A1. | Harness configuration differences are straightforward variants over the same stdio command/args contract, so Phase 1 can focus on proving the runtime in Codex before the broader documentation pass. |
| 2026-05-22T18:24 | Use a source-owned copied requirements file for MCP runtime dependencies. | This matches the existing install model where runtime/provider requirements live in the source repo and are copied into `ar-coordination`; the installed coordinator venv then becomes the single Python runtime anchor for MCP. |
| 2026-05-22T18:24 | Reserve provider status as both context-packet input and a later standalone provider status tool. | Models may need a quick provider health check without rebuilding a full context packet; if they forget the active environment, they can use the context packet/resolver surface first. |
| 2026-05-22T18:34 | Put runtime Python requirements at `runtime/requirements.txt` in source and `ar-coordination/requirements.txt` after install. | The dependencies are installed into the root coordinator venv and support the installed runtime package, including MCP, so the copied requirements file should live at the installed coordinator root rather than under an MCP subfolder. |

---

## Resolved Questions And Remaining Research

### Resolved For Current Planning

- Phase 1 compatibility means local stdio MCP compatibility proven through Codex first. Claude Code and other harness variants are covered by add-on A1 unless the developer promotes one into the active slice.
- The first real operation after the skeleton should be `context.packet`/context startup composition, not provider query. Provider status may feed the packet, and a later standalone provider status tool should also be reserved for quick health checks.
- The MCP Python SDK should stay isolated from source-repo dev tooling. Runtime/MCP dependencies should be declared in source `runtime/requirements.txt`, copied to root `ar-coordination/requirements.txt`, and installed into root `ar-coordination/.venv` when runtime dependency installation is enabled.
- The normal harness config should use `/path/to/ar-coordination/.venv/bin/python` plus `["-m", "agents_remember.mcp"]`; the MCP runtime infers the coordination root from the venv location unless an explicit override is provided for tests or debugging.
- Phase 1 should ship the minimal `ping`/`server_info` shell first. A read-only `context_resolve` or `context.packet` facade is the next slice after the package skeleton is proven, not part of the first acceptance gate.
- Current Codex local stdio syntax is answered: use `[mcp_servers.agentsRemember]` in `~/.codex/config.toml` or trusted project `.codex/config.toml`, with `command = "/path/to/ar-coordination/.venv/bin/python"` and `args = ["-m", "agents_remember.mcp"]`; the CLI equivalent is `codex mcp add agentsRemember -- /path/to/ar-coordination/.venv/bin/python -m agents_remember.mcp`.

### Still Needs Phase 1 Research

- Decide the installer flag/name and default behavior for creating/updating `ar-coordination/.venv`.
- Decide the exact public tool spelling for standalone provider status, for example `provider.status` in docs versus `provider_status` if the MCP SDK/tool naming rules require an identifier-style name.

---

## References

- `phase-00-quality-baseline.md`
- `phase-00-refactor-strategy.md`
- `agentic-context-kernel-mcp-design-note.md`
- Model Context Protocol server concepts: `https://modelcontextprotocol.io/docs/learn/server-concepts`
- Model Context Protocol transports: `https://modelcontextprotocol.io/specification/2025-06-18/basic/transports`
- Model Context Protocol tools: `https://modelcontextprotocol.io/specification/2025-06-18/server/tools`
- Model Context Protocol Python SDK server docs: `https://modelcontextprotocol.github.io/python-sdk/server/`
