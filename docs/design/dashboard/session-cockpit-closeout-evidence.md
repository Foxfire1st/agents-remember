# Session cockpit closeout evidence

This is the durable code-repository evidence index for master `260715-FEUI`. It keeps the design
route readable without expanding `scenario-catalog.md` into a second cockpit specification. The
binding product authorities remain the master task, the v3 cockpit design, and the developer-ruled
session-surface specification in the coordination task.

The snapshot represented here is the FEUI-L8 candidate on 2026-07-18, including the developer-ruled
S5 cutover to one product-facing Chats cockpit. The serving-contract boundaries and exact duty
transfer are recorded in `session-cockpit-upstream-register.md`.

## Coverage matrix

| Design area | Implementing leaf or leaves | Durable implementation and evidence |
| --- | --- | --- |
| Positioning, sole Chats route, full-bleed keep-alive shell, WebTUI scope | L1, L8 | `dashboard/src/cockpit/Cockpit.tsx`, `dashboard/src/styles/webtui.css`, `dashboard/src/panels/session-cockpit/SessionsView.tsx`, and their focused tests; internal `sessions-*` markers remain implementation scope, not a second product route |
| Two session archetypes and PTY truth | L6 | `PtySurface.tsx`, `Terminal.tsx`, `lifecycleCopy.ts`, `dev/PtyRenderBench.tsx`, and `PtySurface.test.tsx` |
| Fleet layout, hierarchy, attention, gate and dot grammar | L2 | `SessionRail.tsx`, `railModel.ts`, `stateGrammar.ts`, and the rail/model/grammar suites |
| Shared session projection, poll freshness, eager/cross-tab reconciliation and seat events | L2, L8 | `catalogPoll.ts`, `seatEvents.ts`, `sessionCockpitStore.ts`; both poll and invalidation reconciler are refcounted and Cockpit-owned |
| Palette, keyboard zones, focus model and effective user bindings | L1, L8 | `data/commands.ts`, `data/keymap/*`, `CommandPalette.tsx`, `useKeyboardZones.ts`, and the keyboard Playwright paths |
| Dynamic capability sourcing and launch | L3 | `capabilityCatalog.ts`, `LaunchFlow.tsx`, `launchEvidence.ts`; dynamic-envelope and all-harness failure suites |
| Requested/effective model and effort evidence | L4 | `setAcceptance.ts`, `setClient.ts`, `setChips.ts`, `ModelEffortControl.tsx`; clamp/queued/unknown/pair-flow suites |
| Reliable composer, receipts, reconcile and authoritative pop-back | L5 | `SessionComposer.tsx`, `submitMachine.ts`, `submitClient.ts`, `submissionLifecycleClient.ts`; reliable-submit and withdrawal suites |
| Structured interaction answer and lifecycle controls | L6, L8 | `InteractionBar.tsx`, `interactionAnswer.ts`, `WorkingLine.tsx`, `sessionLifecycle.ts`, `LandedCleanupNotice.tsx`, and `EndedSessionState.tsx`; interaction, cleanup-authority, handoff, and ended-stage suites |
| Evidence, capability and Bus inspector; persistent status line | L7 | `EvidencePane.tsx`, `CapabilitiesPane.tsx`, `BusPane.tsx`, `SeatInspector.tsx`, `StatusLine.tsx`; inspector suites |
| Legacy Chats duty transfer and retirement | L8 | `ChatContextBar.tsx`, `LaunchFlow.tsx`, `HighlightComposer.tsx`, `catalogPoll.ts`, the one-route Cockpit tests, and S5 browser coverage; raw launch, server leaf move/refusal, lifecycle inheritance, commit-point-only Highlight routing, authoritative landed cleanup, ended-stage restoration, and last-active restoration are pinned |
| Failure/freshness honesty, live regions and accessibility | L2–L8 | `CockpitLiveRegions.tsx`, `announcer.ts`, state words on every chip, freshness diagnostics, the L8 unit and browser gates below |
| Styling, virtualization, performance and scenario testing | L1, L6–L8 | token/Panda sources, `VirtualizedInspectorList.tsx`, `SessionRail.tsx`, `dashboard/perf/cockpit.perf.spec.ts`, `dashboard/e2e/cockpit.spec.ts` |
| Upstream moats and absent contracts | L7, L8 | Honest UA slots/copy in the product and the companion upstream register; no server field is synthesized |

The UA-1 structured transcript grammar is designed but not implemented because the entry-granular
feed does not exist. Controlled sessions therefore continue to show the runner line-log in xterm.

## L8 accessibility evidence

- The permanent polite and assertive regions exist before their first message. A monotonically
  keyed child makes identical repeats observable; same-hydration urgent transitions are combined
  so one session cannot overwrite another.
- Set acceptance and submission receipts announce politely only for the focused session. Any
  session entering failed or awaiting-input announces assertively, except the focused interaction
  whose visible `InteractionBar` alert already owns the announcement.
- Decorative rail glyphs are `aria-hidden`; brief, gate, failed, input-needed and state markers
  retain visible or accessible words. Rail and stage use the same state grammar.
- The palette is modal, traps Tab, restores its invoker, and closes before running a focus command
  so focus commands keep their intended destination. Browser coverage reaches the PTY textarea,
  types into it, and exits to chrome with F6.
- The Evidence/Capabilities/Bus inspector defaults closed, has an always-obvious accessible toggle,
  and keeps deliberate intent separate from responsive geometry. Narrow auto-collapse and a narrow
  reload retain opt-in for width recovery; deliberate close cancels it. Both separators are named,
  the collapsed inspector separator is inert and absent from traversal, and any focused close hands
  focus to the visible toggle. The stage owns the reclaimed width rather than reserving an empty
  third pane.
- Exited and retired focus renders an unmistakable ended overview with no live terminal attachment
  or composer. A landed row remains a distinct read-only terminal inspection, and its keep-alive
  layer survives focus switches to ended evidence.
- The composer profile defaults to Emacs. The keyboard-reference page changes it to Vim without
  rebuilding the draft: Vim owns Escape for insert-to-normal while F6 still exits the editor.
- Browser-loaded OKLCH text tokens were checked against both `bg` and `bgPanel`. The lowest pair is
  `alarm`: approximately 5.81:1 on `bg` and 5.41:1 on `bgPanel`; every audited status-text token is
  at least 4.5:1. Live failed, awaiting-input and working chips are additionally proven to resolve
  to `alarm`, `amber` and `muted`, respectively, with their words intact.

The reproducible browser coverage is in `dashboard/e2e/cockpit.spec.ts`; focused store/DOM evidence
is in `announcer.test.ts`, `submitClient.test.ts`, `CockpitLiveRegions.test.tsx`,
`SessionRail.test.tsx`, and the keyboard preference tests.

## L8 performance and fetch evidence

Run from `dashboard/`:

```sh
npm run perf:cockpit
```

That command first proves the real virtualization boundaries and then runs isolated Chromium
measurements on the exact Terminal component and selected DOM renderer.

| Surface | Ordinary path | Virtualized path |
| --- | ---: | ---: |
| Session rail | 50 rendered row shells in the active roles/tree view | 51 rendered row shells, browser `content-visibility:auto` with intrinsic row size |
| Inspector ledgers | 100 rows | 101 rows, TanStack virtualizer with full accessible set size |

The rail threshold is computed from the exact row shells rendered by the active view, not a live-row
subtotal: roles mode includes completed-unattached rows and only expanded master-completed rows;
tree mode uses the spawn-tree population. Focused tests exercise exactly 50 and 51 actual DOM rows
for flat live, completed-unattached, collapsed/expanded completed folders, and tree mode, asserting
the root count/flag and every row's `content-visibility`/intrinsic-size style. The performance test
also compares the declared count to the actual row-shell count across all 24 rerenders.

2026-07-18 headless-Chromium measurement, 20 runner-line-log writes per second per pane, three-second
sample after one-second settle:

| Concurrent panes | Mean frame | p95 frame | Frames over 33.4 ms |
| ---: | ---: | ---: | ---: |
| 1 | 16.64 ms | 16.80 ms | 0 |
| 6 | 16.64 ms | 16.70 ms | 0 |
| 12 | 16.59 ms | 16.80 ms | 0 |

These are a regression tripwire on this host, not a universal hardware ranking. The gate allows
shared-runner margin but rejects a 30 Hz collapse. The L6 SwiftShader caveat and DOM-over-WebGL
decision remain unchanged.

The same runner forced 24 local rail rerenders. The non-catalog request map was unchanged
before/after (`GET /api/files/repos: 2`, `GET /api/harnesses: 4`); the hoisted 2.5-second driver
crossed one allowed beat (`GET /api/terminal/sessions: 2→3`). No action/non-poll request appeared.
Static inspection likewise found fetches in effects, explicit actions, and transport clients—not
render bodies. The status line stayed `ctx — / cost — (UA-5 slot)`; catalog-path cheapness is never
turned into a session-capacity or cost claim.

## L8 scenario matrix

The Chats-cockpit definitions and their fake HTTP authority live in
`dashboard/src/dev/cockpitScenarios.ts`; `dashboard/src/dev/scenarios.ts` catalogues them as
first-class `?scenario=` entries. Their stable `sessions-*` scenario ids are internal test handles,
not a product navigation label. The legacy `?state=` path remains a single-frame gallery selector.

| Scenario | Contract exercised |
| --- | --- |
| `sessions-launch-happy` | dynamic harness/model/effort selection and starting-row hydration |
| `sessions-launch-conflict` | HTTP 409 retains both live and attempted pair evidence |
| `sessions-failed-harnesses` | three real LaunchFlow POSTs remain starting until one released catalog sweep projects the same Claude, Codex and Pi ids as failed, with retained refusal evidence and no retry |
| `sessions-set-promotion` | one real Set-client POST returns queued, stays pending/effective-high while working, then a released turn-ended catalog transition triggers exact-session readback and promotes effective effort to max |
| `sessions-submit-reconcile` | lost response reconciles the same request id with no resend |
| `sessions-interaction-answer` | projected interaction choice reaches the gate route |
| `sessions-fleet-12` | mixed fleet, collapsed completed groups and attention rollups |
| `sessions-ended-exited` | restored exited row renders an ended overview while a landed transcript remains read-only and inspectable |
| `sessions-ended-retired` | restored retired row renders retained retirement reason without terminal or composer theater |
| `sessions-pty-dropped` | pane-local WebSocket drop is visible |
| `sessions-catalog-stale` | missed catalog beats retain rows and expose stale freshness |

The browser suite also covers `effects=off`, terminal enter/exit, palette containment and invoker
return, stored malicious Vim overrides falling back to the effective F6/Shift+F6 escape, and
loaded-token contrast. One same-page selector regression populates capability/submission state,
switches Chats scenario→Chats scenario and Chats→ordinary gallery without navigation, and proves session, per-session,
cache, announcement, lifecycle and PTY-harvest residue is cleared before the next target mounts.

## Invariant pre-audit

This table is worker evidence for the independent reviewer, not a substitute for that review.

| Master invariant | Candidate evidence |
| --- | --- |
| Dynamic-only pickers | Production launch and control menus derive solely from capability envelopes/snapshots; no fallback row or catalog is present. Hardcoded pairs exist only in fixtures/dev scenarios. |
| Model-gated effort | `deriveEffortMenu` reads the selected/staged model row; a model change re-gates effort and the pair flow preserves partial failure evidence. |
| No composer paste path | The canonical Chats cockpit, RailChat and HighlightComposer use `SessionComposer`/`submitSessionText`; production call-site search finds no paste/injection caller. Legacy data-layer paste helpers and their historical tests remain defined but unreachable, matching the accepted L5 boundary. Raw xterm keystrokes remain terminal input by design. |
| Acceptance honesty | The complete Set alphabet and the complete submit alphabet are exhaustively parsed. Clamp keeps requested/effective separate; queued does not move the effective marker; unknown reconciles without resend. |
| Cleanup authority honesty | Rail and palette bulk cleanup share one exact-target action. A missing/non-OK result produces a root-level retryable alert without claiming mutation; a returned result preserves exact closed/skipped counts and reasons, closes only named rows, and hands removed landed focus to the smart live default while the unconditional `PtySurface` owner preserves the exact visited terminal/socket/scrollback instance across the transient no-focus render. |
| Uniform starting-to-failed | Three real LaunchFlow requests cross the dev HTTP authority, render as starting with caller-minted ids, and only a released catalog poll projects those same ids as failed. Browser assertions pin request bodies/counts, both states, verbatim errors, retained pairs, and the absence of automatic retry. |
| Stop residuals informational | Both terminate `controlStopDetail` and retire `retireControlStopError` survive tombstoning, are labeled informational, and do not alter state grammar. |
| Moats stay visible | UA-1, UA-3, UA-5, UA-7, UA-8 and UA-10 gaps are named at their product boundary; the UI does not invent transcript, capacity, interrupt, whole-queue or attachment data. |
| No production catalog/timing invention | Catalog options come from wire envelopes. Poll, reconciliation, freshness and animation timings are named constants tied to their contracts; fixture timings stay under test/dev routes. |

Independent reviewer and curator artifacts live with the coordination task and remain the authority
for approval and onboarding closeout.
