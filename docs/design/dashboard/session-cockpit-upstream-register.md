# Session cockpit upstream register and Chats decision brief

The frontend deliberately renders missing serving contracts as gaps instead of fabricating them.
This register is the FEUI handoff to the developer/architect. It separates serving asks from the
Chats product decision so either can evolve without packing more policy into the main dashboard
route or the scenario catalog.

## Upstream asks

| Ask | Priority | Missing authority | Frontend landing when available |
| --- | --- | --- | --- |
| UA-1 — entry-granular transcript feed | High | Cursor-resumable entries for user/assistant/reasoning/tool/output/system records, including per-entry interaction choices and stable ids. It must provide an assistive-technology reading surface, not only visual terminal bytes. | Replace the controlled runner line-log as the primary rich surface; apply the designed per-cell collapse grammar and keep Inspector as the full-detail reveal. |
| UA-2 — role-aware spawn route | High | A serving route/parameter that reuses the authoritative role/session payload builder and returns the created catalog identity. | Add role to the dynamic launch flow without inventing client-side role defaults. |
| UA-3 — inbox bodies, history and escalation rung | High | Message bodies, consumed history and the current ladder rung on authoritative inbox reads. | Complete Bus detail, brief state and escalation inspection while retaining operator-inbox as the only developer reply write. |
| UA-4 — resolved role knobs read | Medium | Read-only resolved `orchestration.roles` posture, coordinated with CFGUI. | Show the role's effective configuration/evidence without making FEUI a settings editor. |
| UA-5 — usage and pressure telemetry | High | Per-session context percentage, token/cost counters, rate-limit windows and compaction activity with source/freshness. | Replace the exact `ctx — / cost —` slot and add staged warnings; reserve `compacting` as a state word. Never infer cost from catalog or process latency. |
| UA-6 — pushed turn state | Medium | Ordered turn-state events with generation/sequence identity and reconnect semantics. | Reduce poll/sweep latency while keeping catalog reconciliation authoritative. |
| UA-7 — interrupt/cancel turn | Critical | A generation-bound control route with definitive/ambiguous outcomes for Claude, Codex and Pi. | Enable the welded Stop-turn control, chord and palette command; preserve the post-interrupt state and receipt evidence. There is no PTY fallback for controlled sessions. |
| UA-8 — whole queue projection | High | Queue depth and per-item source, preview, state and stable operation identity across cockpit, terminal and inbox submissions; expose steer semantics only if the bridge truly supports them. | Replace the client-only “yours” subset with whole-queue truth and retain authoritative pop-back/withdraw semantics. |
| UA-9 — standing approval policy | Medium | Read-only effective permission/approval posture, beginning with Agents Remember's own settings and extending to vendor policy where authoritative. | Add a policy chip that explains why questions do or do not occur; never infer policy from recent interactions. |
| UA-10 — attachments/images on submit | Nice-to-have | Typed attachment upload/reference fields on the reliable submit authority. | Add explicit attachments to the composer. Current submit is text-only; vendor clipboard/image paste is not a headless-safe substitute. |

UA-1 and UA-7 are the largest experience gaps: controlled sessions have no vendor TUI, so the
current line-log is not a rich transcript and the runner's line reader cannot receive an Escape
interrupt.

## Chats cutover outcome

**State:** developer-ruled and implemented in FEUI-L8 S5.

There is now one product-facing `Chats` route backed by the keep-alive fleet cockpit. The old Chats
component, session-list grouping implementation and separate `Sessions` navigation entry are
retired. **Operations remains the application default**, and `RailChat` remains the separate
contextual chat inside the Operations right rail.

### Duty transfer

| Former full-page Chats duty | Current owner and authority |
| --- | --- |
| Initial catalog hydration and immediate cross-tab invalidation | Refcounted `startCatalogReconciler()` at `Cockpit` lifetime; remote termination excludes the ended id from a stale confirming read. |
| Raw terminal launch | Chats cockpit `＋ Terminal`; the server open request inherits the selected lifecycle. |
| Hosted harness launch | `LaunchFlow`; lifecycle inheritance rides the authoritative open request and a confirmed create is broadcast. |
| Leaf attach/move | `ChatContextBar` calls the server attach-leaf route first, applies only a 200 result, broadcasts the change, and renders 409 refusal evidence. |
| Last-active focus | One live-session preference shared by cockpit focus, `sessionStore.activeId`, Highlight routing and localStorage; landed rows remain inspectable without poll-driven focus theft. |
| Highlight delivery destination | Accepted/queued delivery commits the exact resulting session id into the Chats cockpit. Rejected, blocked, route-error and unresolved-endgame existing-target attempts preserve the prior active route, focused row and view. |
| Landed bulk cleanup | Rail and palette call the same landed-cleanup authority with an immutable id/label snapshot. Missing results remain visible and retryable at the view root; returned closed/skipped counts and reasons are preserved, and removal of the focused landed row hands focus to the smart live default without unmounting another visited terminal, socket, or scrollback. |
| Restored terminal states | `PtySurface` owns the no-focus placeholder as well as inspectable terminal layers, so the owner stays mounted through a removed-row handoff; `EndedSessionState` gives extant exited/retired focus an explicit no-terminal/no-messaging presentation. Landed rows remain distinct read-only terminal evidence. |

The Evidence/Capabilities/Bus inspector is explicitly toggleable, defaults closed without an
operator opt-in, and separates persisted operator intent from transient responsive collapse. A
narrow reload retains recovery intent; deliberate close cancels it. Its separator is named and
inert while collapsed, focus repairs to the visible toggle, the centre stage receives reclaimed
width, and F6 excludes the hidden region.

### Explicit boundaries

- Existing-row lifecycle attachment preserves only the old UI's local `sessionStore.setLifecycle`
  behavior. The control says **Route locally** because there is no attach-lifecycle endpoint and a
  catalog hydrate may replace it. This is not durable authority.
- Leaf attachment is different: its server endpoint is the arbiter, including same-role 409 refusal.
- The cutover consolidates product navigation and chrome; it does **not** deliver the UA-1
  entry-granular transcript or a structured conversation UI. Controlled chats still render the
  runner line-log in xterm, while raw sessions retain their real vendor TUI.
- The older best-effort `createSession` raw-launch helper still creates its local row when its open
  POST fails so the dev bench can render without a backend. That is retained legacy behavior, not a
  claim of authoritative launch success; native hosted launch uses the classifying `LaunchFlow`.
