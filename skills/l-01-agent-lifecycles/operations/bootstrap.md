<!-- ar:roles all -->
# Operation — Session Bootstrap

Run when a repo lacks a resolving memory root (if context resolves without a knowledge foundation, report via step 8; onboarding/foundation are later Architect work).

## Required workspace facts and decisions

Gather repo/root, `coordinationRoot`/`workspaceRoot` (`server_info`), and memory state. Ask: (1) foundation code branch (**spear**); (2) scaffold new memory repo or use existing.

## Normal workflow

1. **Explain:** code stays put; memory is a separate Git repo under `coordinationRoot`; onboarding lives in memory; the memory map links memory to code via adoption.
2. **Context:** `server_info`, `context_packet`; report missing facts verbatim.
3. **Decisions:** bind memory branch to spear (changing later needs carryover).
### 4. Write the initial user settings
Call `user_settings_init` once; ask host-available harness per role (one may serve all), model/effort/mode unset; preview (`dry_run=true`), write, read back; report existing settings verbatim without overwriting.
5. **Memory root:** read `docs/guides/onboard-existing-repo.md`; `memory_init` (`dry_run=true` first).
6. **Baseline:** inspect files without authoring (`docs/`/`system/` suffice; absent `onboarding/` is normal).
7. **Adopt:** read `docs/guides/adopt-existing-memory.md`; `memory_baseline_status` before/after `memory_baseline_adopt` (`dry_run=true` first); ask drift acceptance; validator refusals stand.
8. **Foundation:** report `knowledge` state, follow [handover](../reference/bootstrap/foundation-state.md#name-the-next-step), hand off to Architect.

When hitting unfamiliar states (`memory_init`/`memory_baseline_status`/`user_settings_init`/`server_info`/`context_packet`/`runtime_install`), read [failures]({{detail:reference/bootstrap/failure-states.md}}).

## Evidence and result verification

Completion truth only, not acceptance. Preview (`dry_run=true`) and read back mutations; record surface, args, answer, before/after state (non-surface = observations) and skipped/failed steps verbatim under **Steps Skipped Or Failed**. Ask before altering artifacts.

## Failure handling

### A developer decision is missing
No spear: stop before step 3. No existing-memory answer: resolve via `context_packet`. No repo: stop.

### A step fails or needs a developer ruling
Report exact output, stop, never rerun with altered options; resume identically after fix/ruling. Log absent capabilities, compile refusals verbatim, and contradictions. End once surfaces establish roots, spear, settings, baseline commit, onboarding, foundation state, and skipped reasons.
