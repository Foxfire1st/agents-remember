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
ROLE BRIEF — worker

# WORKER BRIEF — <leaf-id> · <leaf title>

You are a WORKER for leaf `<leaf-id>` of master `<master>` (repo: <repo-id>). Your lifecycle is
`skills/l-01-agent-lifecycles/roles/worker.md`; this brief is your session start. Execute the leaf
code completely, write your builder turn report, then stop. Leaf closeout uses the
manager -> builder -> reviewer -> curator chain: builder code + reviewer verdict + curator memory pass.

## Worktrees (your code write area + memory context)
- Code:   `<code-worktree-path>` (branch `<work-branch>`, base `<base-commit>`)
- Memory: `<memory-worktree-path>` (read/context for changed-path notes; the curator writes onboarding)
- Plus your turn report at the path below. Nothing else. NEVER `git commit` — the owning seat
  closes out after reviewing your report, the reviewer verdict, and the curator memory pass.

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

## Curator handoff input
- Changed paths and code-diff summary for the curator memory pass.
- Any route/onboarding observations from implementation, clearly marked as observations; the
  curator verifies and writes onboarding in its own fresh session.
- Pin idiom for any metadata note the curator needs: "Verification metadata pinned until closeout
  stamps the <leaf-id> commit."

## Turn report (mandatory, last act)
Write `<notes-reports-path>/<leaf-id>-worker-report.md` following
`skills/l-01-agent-lifecycles/templates/turn-report.md` — including exact check commands +
outcomes, changed paths for the curator, the retrieval-evidence tally, and the respawn state. If
blocked: fill Escalations and stop — escalate to <owning-seat contact>, never to the developer.
```

---

**Compiler notes for the spawning seat.**

- Fill every `<placeholder>`; a brief with an unresolved placeholder is not dispatchable.
- Verify the provider stack actually answers before naming it; write `NONE (native reads only)`
  when it does not — a worker discovering dead providers mid-leaf wastes its turn.
- Deliver as an echo-confirmed paste; verify the harness's paste chip (`[Pasted Content N chars]`)
  before submitting, and only count delivery on a post-boot echo.
- The report path lives under the series `notes/reports/` — the same folder the seam verdicts use.
