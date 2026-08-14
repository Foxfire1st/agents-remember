# Chats end-to-end suite (`AR_RUN_CHATS_E2E`)

The durable, opt-in Chats E2E promoted in half-time feedback FB5 (260718-CHATS-L5F R7). Unlike
`e2e-production/` (which route-mocks the backend to test the bundle in isolation), this suite drives
the **real composed app against the real installed harnesses**: it boots an isolated dashboard
daemon from this worktree on a free port (the developer's `:8871` is never touched), builds+syncs
the current bundle, and drives codex / claude / pi through open → submit → full turn →
set-model/effort with **acceptance validation** → interrupt → End.

It is the suite that would have caught, before merge, every half-time functional defect:

| Assertion (`support/drive.ts`)      | Screenshot / requirement it guards                                  |
|-------------------------------------|---------------------------------------------------------------------|
| `assertNoUnknownVendorRows`         | codex startup flood (image.png, R1) + claude frame flood (image3, R3) |
| `assertAcceptanceValidated`         | opus[1m] "refused pair — requested provenance" (image3, R2)         |
| `assertNoVersionMismatchDemotion`   | claude wholesale "unverified" version demotion (image3, R3/R4)      |
| `assertSingleTurnResultInvariants`  | a settled turn double-projecting                                    |
| `assertNoProjectionAlarm`           | the cried-wolf codex launch red strip (R10 / audit V13)             |
| `assertWorkingStateSeenDuringTurn`  | a streaming turn shown settled-green (R9 / audit V5)                |
| composed heap sampling              | per-session structure release across open/End cycles (R5)           |

## Run

```bash
# from dashboard/ — requires a python with the agents-remember mcp package installed
AR_RUN_CHATS_E2E=1 \
AR_CHATS_E2E_PYTHON=/path/to/venv/bin/python \
npm run e2e:chats
```

Without `AR_RUN_CHATS_E2E=1` the suite is a **green no-op** (it never runs real harnesses in the
default gate). Environment knobs:

- `AR_CHATS_E2E_PYTHON` — python interpreter with the mcp package installed (default `python3`).
- `AR_CHATS_E2E_HARNESSES` — comma list to scope the composed drive (default `codex,claude,pi`).
- `AR_CHATS_E2E_SKIP_BUILD=1` — reuse the already-synced `package_data` bundle instead of rebuilding.

Requirements on the host: `tmux`, the installed `codex` / `claude` / `pi` binaries authenticated for
ordinary turns, and node's Chromium (`npx playwright install chromium`).
