# Missing-View Matrix — template

The Stage 3b detector (`owned-methods.md`, Method 2). Cross every workflow step **and** every reachable
system state against the forced UI-state list. **Every blank cell is a missing-view / missing-state
finding.** This is the diff no installed skill performs.

## Rows — workflow steps + reachable system states

Build the row set from:

- **Scenario steps** — every step from `docs/design/dashboard/scenario-catalog.md`.
- **Reachable entity states** (enumerated in Stage 1 from `provider_status` / `worktree_status` /
  `server_info` + observer/projection code):
  - provider: booting · nominal · saturated · failed · dormant
  - lifecycle: phase × state (request → … → close)
  - worktree: active · closing · abandoned · blocked
  - session/TUI: idle · busy · awaiting-input · disconnected

## Columns — forced UI states

`content` · `first-run-empty` · `zero-result-empty` · `cleared-empty` · `loading` · `partial` ·
`stale-disconnected` · `offline/5xx/403/404/validation/ratelimit` · `permission` · `overflow`

## Matrix

| Row (step / state) | content | empty(×3) | loading | partial | stale | error | permission | overflow |
|---|---|---|---|---|---|---|---|---|
| <row> | ✓ / ✗ | … | … | … | … | … | … | … |

Cell legend: `✓` a view encodes it (cite the view) · `~` partial/ambiguous · `✗` **missing** (finding).

## Output

Promote every `✗` to the report's Missing-View Backlog (section 3) and Findings (section 4). A `✗` that
blocks a catalogued scenario step is Blocker/High. To exercise states that don't occur naturally, drive
them via the disposable dummy-worktree harness (start → fake commit → abandon → cleanup) or by pointing
at a worktree already in that state.
