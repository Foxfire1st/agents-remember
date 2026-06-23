# Owned methods

The analysis passes this skill runs itself (Stage 3 of `SKILL.md`), plus the persona model and the
severity model used to consolidate (Stage 5). Each method is a runnable procedure; drive observation
through the Chrome MCP and obey the settled-beat rule throughout.

## Severity model

Rate every finding 0–4 = **frequency × impact × persistence**, averaged across the three personas:

| Score | Meaning |
|---|---|
| 4 Blocker | a user cannot complete an intended workflow (incl. any scenario-blocking **missing view**) |
| 3 High | a user is likely to get stuck or take a wrong action; a doctrine violation that misleads about system state |
| 2 Medium | friction, ambiguity, or a craft defect that slows but doesn't block |
| 1 Low | polish nit |
| 0 Note | observation / future idea |

A **doctrine** violation (see `docs/design/dashboard/review-doctrine.md`) scores one tier higher than
the same defect would in the abstract, because it actively misleads the operator about live state.

## The three personas

Run the heuristic and walkthrough passes once per persona; they surface different failures.

- **Operator** — daily driver steering many lifecycles/sessions; cares about glance-clarity, "what
  needs me," and acting fast.
- **Incident-responder** — arrives when something broke; cares about "show me the state and the safe
  next action."
- **Expert daily-driver** — knows the system; cares about density, shortcuts, and not being slowed by
  hand-holding.

## Method 1 — Scenario-driven cognitive walkthrough (Stage 3a)

For each scenario in the catalog, for each persona:

1. Fix the persona + goal in the user's own words (no internal IDs).
2. Decompose the goal into the correct atomic action sequence.
3. At each step, against the **live, settled** UI, answer the four Wharton questions:
   - Will the user try to achieve the right effect? (is this the action they'd reach for)
   - Will they notice the correct control is available?
   - Will they associate the control with the effect they want?
   - Will they see that progress is being made? (maps onto visibility-of-status + observability-parity)
4. Emit per step: **Pass** or **STUCK** (with which question failed) and a severity.
5. If a step has no control at all → it is a **missing-view** finding; add it to the matrix (Method 2).

Guard: a control that is mid-transition (transient `opacity:0`) is **not** absent — re-check at a
settled beat before recording STUCK.

## Method 2 — Workflow × UI-state matrix → missing views (Stage 3b)

The highest-value owned detector. No installed skill does this.

1. Build the row set = every scenario step (from the catalog) **and** every system state an entity can
   reach (provider: booting/nominal/saturated/failed/dormant; lifecycle: phase × state; worktree:
   active/closing/abandoned/blocked; session: idle/busy/awaiting-input/disconnected).
2. Build the column set = the forced UI-state list: `content`, `first-run-empty`, `zero-result-empty`,
   `cleared-empty`, `loading`, `partial`, `stale-disconnected`, `offline/5xx/403/404/validation/
   ratelimit`, `permission`, `overflow`.
3. For each cell, confirm against the live app (drive states via the dummy-worktree harness where
   needed) whether a view encodes it.
4. **Every blank cell is a missing-view (or missing-state) finding.** A scenario-blocking blank is
   Blocker/High. Record in `templates/missing-view-matrix-template.md` shape.

## Method 3 — Observability canon audit: RED/USE + altitude (Stage 3c)

Grade the dashboard as a *monitoring surface*:

1. **Golden signals per entity** — RED (Rate/Errors/Duration) for request/command-shaped entities
   (lifecycle, worktree ops, TUI commands); USE (Utilization/Saturation/Errors) for resources
   (providers, PTY, queues). Flag any entity missing its signals.
2. **At-a-glance health** — can the operator answer "is everything okay?" from the first viewport
   without drilling? Flag if the top-level view buries faults.
3. **Symptoms over causes** salience; **inverted-pyramid drill-down** — every red/fault state must
   link to its cause and to the control that addresses it.
4. **Observability-parity** — diff what `provider_status` / `worktree_status` / `server_info` / the
   chat event stream expose against what the dashboard surfaces. Anything visible upstream but absent
   on the dashboard is a parity-gap finding.
5. **Stale-data honesty** — there must be a last-updated / connection / reconnect indicator; a frozen
   stream must be visibly distinguishable from a healthy one.

## Method 4 — Motion-as-communication (Stage 3d)

Judge motion as *information*, not decoration. Sample only at settled beats; confirm via CSS class.

1. Does each transition **encode** the state change it accompanies? (e.g. engine power-up fills
   center-out and reads as "booting"; an active step grows cyan then retracts to amber-settled;
   terminated flows retract; green = nominal at rest).
2. Does every transition **land in a complete, readable resting view** (no element stranded mid-motion)?
3. Is the *one* active focal point unambiguous (exactly one in-flight cue at a time)?
4. Under reduced-motion / `?effects=off` (Calm), is the "what's happening now" signal preserved by a
   non-motion cue, or is it lost?
5. Delegate motion *feel/easing/duration* to `emil-design-eng` + gsap/motion (see `delegation-map.md`);
   this method judges only whether motion communicates the right state.

## Method 5 — Task-6 TUI control-plane review (Stage 3e)

Review the embedded interactive terminal as a first-class control surface:

1. **Focus contention** — when the terminal has focus, do dashboard keyboard shortcuts steal keys (or
   vice-versa)? Is the focused/unfocused state legible?
2. **Liveness legibility** — can the user tell busy vs idle vs awaiting-input vs disconnected?
3. **Escapability** — can the user detach / Ctrl-C / switch sessions without killing the dashboard or
   the session? Does a session survive view-switch and tab-switch?
4. **Round-trip** — a command's effect must trace back visibly: command → in-flight → success or
   failure-with-recovery → reflected on the engine-room canvas. Confirm delivery is honestly reported
   (Sending… → delivered / Retry), and that context injection (selection/composer) is "no silent
   action" (nothing reaches the agent until Send).
5. **Safety** — destructive actions (abandon / cleanup / integrate / kill-PTY) must be guarded
   (confirm/undo) and visually distinct.

## Method 6 — Information-scent / 5-second / progressive-disclosure foraging

The "can a user understand it by looking, and is it led well" pass:

1. **5-second test** per top-level view — show a settled screenshot for ~5s (figuratively): can the
   user name what this view is for and the first 3 things their eye goes to? Flag weak/over-busy
   first impressions.
2. **Scent trace** per scenario — from intent to action, follow the link/label scent and stop at the
   first weak-scent hop; that hop is the exact stuck coordinate. Flag dead patches (regions with no
   actionable scent).
3. **Progressive disclosure** — is advanced/rare functionality hidden until needed, and is needed
   functionality not buried? Flag IA depth > 3 levels and hidden-but-needed controls.
4. **Hierarchy** — does each view have a clear focal point and a single primary action; is the
   entry-point view obvious to a newcomer?

## Heuristic backbone (Nielsen-10, re-weighted)

Underlying Methods 1, 5, and 6, run a Nielsen-10 heuristic evaluation per persona, with two
re-weightings for this domain:

- **H1 visibility-of-system-status fuses with observability-parity** and becomes the dominant
  catastrophe class — a real-time cockpit that hides or freezes state fails its core job.
- Add a **workflow-coverage gate**: a heuristic pass cannot be "clean" while Method 2 shows a
  scenario-blocking missing view.

Rate each heuristic finding with the severity model above and feed it into Stage 5 consolidation.
