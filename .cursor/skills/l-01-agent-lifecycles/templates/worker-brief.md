# Template — Worker Brief

The dispatch packet a spawning seat (manager / orchestrator) compiles for a worker. **The brief is
the worker's entire session start** — it replaces the front half the spawner already ran (trust
checkpoint, reframe, plan). Compile it fresh per leaf from this shape; the proven form below
absorbed a series of real dispatch frictions (route-index leaks, attestation format, provider-stack
keying, missing `python` shim), so deviate knowingly or not at all.

Spawn with `env={"AR_SPAWN_ROLE": "worker"}` and the **qualified** leaf key
`<repository>/<master>/<docId>` so the session-start router and the dashboard leaf rail both engage.

---

```md
# WORKER BRIEF — <leaf-id> · <leaf title>

You are a WORKER for leaf `<leaf-id>` of master `<master>` (repo: <repo-id>). Your lifecycle is
`skills/l-01-agent-lifecycles/roles/worker.md`; this brief is your session start. Execute the leaf
completely, write your turn report, then stop.

## Worktrees (your ONLY writable areas)
- Code:   `<code-worktree-path>` (branch `<work-branch>`, base `<base-commit>`)
- Memory: `<memory-worktree-path>`
- Plus your turn report at the path below. Nothing else. NEVER `git commit` — the owning seat
  closes out after reviewing your report.

## Tool surface
- Native file tools inside the two worktrees; shell for the checks below.
- Read-only AR retrieval: `read_ar_files` (serves the OFFICIAL baseline, never your worktree —
  final verification uses native reads), `grepai_search` / `cgc_*` (provider stack key:
  `<stack-key-or-NONE>`), `context_packet`.
- No `worktree_*`, `lifecycle_*`, `task_doc`, `gate_*`, `memory_*`, or `route_index_refresh` —
  generated route indexes are regenerated with a local `build_route_indexes(...)` from the memory
  worktree.
- Interpreter: `<venv-python-path>` with `PYTHONPATH=<code-worktree>/mcp/src` — there is no
  `python` shim in this environment.

## The task
Leaf spec: `<leaf-doc-path>` (read it first). <One-paragraph task statement: the bug/feature, the
files involved, the invariants that must hold, what NOT to touch.>

## Checks (green before you report)
- Focused: <lint/typecheck/tests over changed paths, exact commands>.
- Full: <the resolved system/tools.md wrapper command> — must exit 0.
- `git diff --check` in both worktrees.

## Onboarding (same editing pass, per c-05)
- Changed source files: update the sidecar BODY now; new files: create the sidecar.
- Route overviews: genuine body update where routes changed; otherwise the newest history entry
  uses the LITERAL form `- <ISO timestamp> — No route impact: <reason>` (timestamp first).
- Pin idiom for verification metadata: "Verification metadata pinned until closeout stamps the
  <leaf-id> commit."

## Turn report (mandatory, last act)
Write `<notes-reports-path>/<leaf-id>-worker-report.md` following
`skills/l-01-agent-lifecycles/templates/turn-report.md` — including exact check commands +
outcomes, the retrieval-evidence tally, and the respawn state. If blocked: fill Escalations and
stop — escalate to <owning-seat contact>, never to the developer.
```

---

**Compiler notes for the spawning seat.**

- Fill every `<placeholder>`; a brief with an unresolved placeholder is not dispatchable.
- Verify the provider stack actually answers before naming it; write `NONE (native reads only)`
  when it does not — a worker discovering dead providers mid-leaf wastes its turn.
- Deliver as an echo-confirmed paste; verify the harness's paste chip (`[Pasted Content N chars]`)
  before submitting, and only count delivery on a post-boot echo.
- The report path lives under the series `notes/reports/` — the same folder the seam verdicts use.
