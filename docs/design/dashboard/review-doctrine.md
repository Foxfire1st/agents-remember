# Cockpit Dashboard — Review Doctrine

The acceptance bars and visual grammar the `dashboard-experience-review` dev-skill enforces. A finding
that violates a rule here scores **one severity tier higher** than the same defect would in the
abstract, because it actively misleads the operator about live system state.

Seeded 2026-06-23 from `docs/design/engine-room/engine-room-visual-language.html` (the living spec,
with `podstage.html` as the scenario prototype) and the control-plane / IA / north-star design notes.

---

## 1 · The cockpit thesis

- **Never just a pretty toy.** Motion and layout must carry information, not decorate.
- **A cockpit is useless if all you can do is watch.** Value is visibility **plus** the ability to act
  on the running system in-place. Where the UI shows a state that needs a human action, it should offer
  the action (or honestly mark it as not-yet-actionable) — a display-only "decision needed" with no
  control is a workflow gap, not a feature.
- The unit of work is **the lifecycle**, not the repo.

## 2 · Observability-parity (the dominant rule)

**If chat — or an MCP status tool — can see it, the dashboard must show it.** Visibility-of-system-status
(Nielsen H1) fuses with parity and is the cockpit's core job; a real-time surface that hides or freezes
state fails catastrophically. The review diffs `provider_status` / `worktree_status` / `server_info` /
the chat event stream against what the dashboard surfaces; anything visible upstream but absent on the
dashboard is a parity-gap finding.

## 3 · The state-by-colour grammar

The whole UI reads state through colour + motion. Enforce these meanings; flag any surface that
contradicts them.

- **Cyan = ACTIVE step / in-flight.** The one transaction happening now: a flow grows source→tip, holds
  with a travelling dot + chevron, then retracts. **Exactly one** is "on" at a time.
- **Amber = SETTLED relationship at rest** (static wire, no dot/tip; retracts only when the relationship
  is *terminated*). Also the nominal structural/wireframe line colour.
- **Mint/green = FRESH / boot-success / landed / live.** The deliberate exception to "amber = at rest":
  **engines REST green, never amber.**
- **Red (alarm) = FAULT or BLOCKED**, with the load-bearing distinction: **blocked is STEADY, a fault
  FLICKERS.** They must never look alike.
- **dormant** = pruned/retired/configured-but-off (grid/muted/ink neutrals).

Cross-surface reuse to keep consistent: EventRiver left-border = trust (observed/approved = mint,
declared = amber, inferred = cyan); DetailPanel phase stepper (done = cyan, current = amber);
spine lanes (code = amber, memory = cyan).

### Newcomer-misread risks to check explicitly

- **Amber's dual meaning** (settled relationship line *and* nominal structural chrome) plus
  **green ≠ amber for a healthy engine** is the single most-conflated rule. A reviewer/user unfamiliar
  with the spec will read a resting green engine as "warning," or expect amber.
- **Steady-red vs flickering-red** is the only thing separating "you must choose" from "something broke"
  — and the differentiator is **motion**, invisible in a static frame. Sample at a settled beat, but
  judge flicker from the live motion / a short capture, not one screenshot.
- The engine room has **no on-screen legend** (Topology has a 4-dot legend; the engine room does not) —
  the grammar is entirely learned. Flag this as an understandability risk.

## 4 · Colour-as-only-signal

Every state must be distinguished by **more than hue** — shape, icon, label, or motion. Contrast and
perceptual separation alone (delegated to `color-expert`) are not enough; assert signal redundancy so a
colour-blind or glance-only user can still read state.

## 5 · Motion doctrine

- **GSAP timelines + Motion only; CSS is static-only.** Never flag a missing CSS transition as a defect,
  and never praise a CSS `@keyframes`/`transition` used for animation — it violates doctrine. Motion
  feel/easing is delegated to `emil-design-eng` + gsap/motion; *whether motion communicates the right
  state* is owned.
- **Engines power up from the MIDDLE outward** (center→edges, surge-then-settle), never bottom-up, and
  rest green.
- **Every transition must land in a complete, readable resting view** — nothing stranded mid-motion.
- Under reduced-motion / `?effects=off` (Calm), primitives freeze to their end-state; the "what's
  happening now" active-flow cue disappears, so a non-motion fallback must preserve that signal or it's
  a finding.

## 6 · Settled-beat sampling (reliability rule)

Read opacity/DOM/visibility **only at a paused, settled beat** — never mid-transition (an element
exiting/entering `AnimatePresence` is transiently `opacity:0`; sampling then gives a false "absent").
Confirm fill/state via the element's **CSS class**, not opacity or `scaleY` (those freeze when the tab
is backgrounded). The screenshot is the tiebreaker.

## 7 · Stale / freshness honesty

A frozen real-time stream must be **visibly distinguishable** from a healthy one — last-updated,
connection, and reconnect indicators are required. Catch the known failure modes presented as live: a
wrong-branch backend that renders empty, and a frozen scenario-player. "Looks healthy but is frozen" is
a high-severity violation.

## 8 · Review posture

- **Findings only.** The review never edits the dashboard; every output is a finding for a separate,
  gated fix job.
- **Drive live; sample settled.** Observe through the Chrome MCP (playwright-cli fallback); obey §6.
- **Delegate craft, own workflow.** See the skill's `delegation-map.md`.

---

## Carried known defects (verify, then re-confirm or retire)

- **D5 landing-tier retract bug** — at `cleanup-pending` the engine-room landing tier stays lit at rest.
- **Display-only affordances** — most attention-item actions (answer/decide/ack/retry/restart) and the
  engine-room recovery chips have no write path yet (only the commit gate + the terminal WebSocket are
  wired) — measured against §1 this is the biggest workflow gap.
- **No Onboarding Inspector** — onboarding-vs-code inspection (W5) has only aggregate numbers.
- **Conditional task-document content** — blank for any lifecycle without an authored
  `ar-task-document/v1` keyed by `lifecycleId`.
- **Topology renders empty** — in the 2026-06-23 dogfood the constellation canvas painted 0 pixels (no
  nodes, no empty-state message) despite a populated workspace; re-check whether it is a render bug or a
  missing empty-state.
