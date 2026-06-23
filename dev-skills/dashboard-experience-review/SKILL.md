---
name: dashboard-experience-review
description: "Review the agents-remember cockpit dashboard like a user: discover the intended workflow scenarios, detect missing views and where users get stuck, and judge whether the UI is self-explanatory at a glance / leads the user / progressively discloses / has clear hierarchy / is a good observability surface. Conducts the installed design, accessibility, data-viz, and motion skills and owns only the workflow-completeness layer none of them cover. Findings only — never auto-fixes."
---

# dashboard-experience-review Dashboard Experience Review

Use this skill to review a **live** agents-remember cockpit dashboard (including the Task-6 control
plane) against the user workflows it is supposed to support. It does not just grade rendered pixels —
it asks *"what would a user try to do here, and can they?"*: it discovers the intended workflow
scenarios, detects **missing views** that break those workflows, finds where a user gets stuck, and
judges glance-clarity, hierarchy, progressive disclosure, and observability quality.

It is a **conductor**. The craft dimensions (visual hierarchy, accessibility, data-viz honesty,
motion feel) are already covered by installed skills — this skill **delegates** to them and owns only
the layer none of them cover (scenario discovery, missing-view detection, observability-parity,
motion-as-communication, the Task-6 terminal UX). See `delegation-map.md`.

**Findings only.** This skill never edits the dashboard. A fix is a separate, gated build job. Every
output is a finding, routed in the design-review triage format so it can feed that pipeline.

## When to use

- Reviewing the cockpit dashboard before a release, a PR, or a demo.
- As the **final review step of an ongoing dashboard build task**, before its closeout.
- Investigating a "the dashboard looks fine but users can't do X" report.
- Auditing whether the dashboard honours observability-parity ("if chat or an MCP status tool can see
  it, the dashboard must show it").

Do **not** use it to *fix* anything, to review non-dashboard UIs, or as a substitute for the craft
skills it delegates to.

## Invocation modes

Stage 0 detects which mode it is running in and resolves the target accordingly.

1. **Standalone review.** The caller points the skill at a running dashboard (frontend URL/port +
   backend, default `:8765`). No worktree coupling; the skill drives the running app and emits a
   self-contained review report.
2. **Final step inside an ongoing task.** Invoked within an active build worktree before that task's
   closeout. The skill reviews the **work-in-progress** dashboard served from *that worktree's
   same-branch backend* (a mismatched-branch backend renders empty — see `review-doctrine.md`), so
   its findings inform the host task's closeout. It reads the host worktree's code/observer/projection
   directly rather than `main`.

## The pipeline

Run these stages in order. Stages 1 and 3 are OWNED; stage 4 delegates. Full method procedures live
in `owned-methods.md`; per-delegate constraints in `delegation-map.md`; output shapes in `templates/`.

- **Stage 0 — Scope & governance.** Detect invocation mode; resolve the running dashboard
  (frontend URL/port, backend `:8765`) and the worktree whose code it serves; confirm the dashboard
  is actually live and populated. Load the doctrine in `review-doctrine.md` as DESIGN constraints
  (a doctrine violation scores a higher severity than a generic nit). Set findings-only. Build the
  delegation table for this run.
- **Stage 1 — Ground-truth entity + scenario model (OWNED).** Enumerate every entity and every state
  it can reach from the *running system*, not from guesswork: call `provider_status`,
  `worktree_status`, `server_info` and skim the dashboard's `observer/` + `data/` + `types/projection`
  code. Author or refresh the **Workflow Scenario Catalog** (`docs/design/dashboard/scenario-catalog.md`)
  as operator-language job-stories. Mark **orphan views** (a view serving no job) and **orphan jobs**
  (a job with no serving view = a candidate missing tab).
- **Stage 2 — Live observation.** Drive the running app with the Chrome MCP (playwright-cli is the WSL
  fallback). To populate real engine processes, drive a disposable dummy worktree
  (start → fake commit → abandon → cleanup). **Sample motion only at a paused, settled beat** — never
  mid-transition (see the settled-beat rule below). Capture screenshots, DOM, console, and network;
  extract computed fill/stroke/background hex for the color checks.
- **Stage 3 — OWNED analyses** (one method-encoded pass each, from `owned-methods.md`):
  (a) scenario-driven cognitive walkthrough; (b) workflow × UI-state matrix → missing views;
  (c) observability-parity diff + RED/USE altitude; (d) motion-as-communication; (e) Task-6 TUI
  control-plane round-trip + safety + focus.
- **Stage 4 — Delegate the bounded dimensions.** Hand each *resolved, settled* view to the installed
  craft skills and fold their findings **by reference** (do not re-derive): glance/hierarchy/friction,
  per-view robustness/console/a11y, code WCAG, chart honesty, color separation, motion feel/API. Ground
  any stack/observability claim through Context7. Constraints per delegate are in `delegation-map.md`.
- **Stage 5 — Consolidate & severity-rate.** Run the analyses from the three personas
  (operator / incident-responder / expert daily-driver), affinity-cluster duplicates, and rate each
  finding 0–4 (frequency × impact × persistence, mean across personas). Any scenario-blocking
  missing-view is Blocker/High.
- **Stage 6 — Emit the report inline** using `templates/review-report-template.md` (findings only).

## What this skill OWNS

No installed or marketplace skill covers these, so the skill performs them itself:

- **Workflow-scenario discovery** from the live entity model.
- **Missing-view detection** as a scenarios × steps × forced-UI-state diff (a blank cell is a missing
  view) — see `templates/missing-view-matrix-template.md`.
- **Observability-parity** — diff what chat / MCP status tools can see against what the dashboard
  surfaces.
- **Motion-as-communication** — does a GSAP/Motion transition *encode* the state change (not just look
  nice), and does every transition land in a complete, readable resting view.
- **Color-as-only-signal** — every state must be distinguished by more than hue (shape/icon/label/motion).
- **Task-6 TUI control-plane UX** — terminal-vs-DOM focus contention, busy/idle/awaiting-input
  legibility, escapability, and the command → in-flight → success/failure-with-recovery → effect-on-canvas
  round-trip.
- **Stale/freshness honesty** — a frozen real-time stream must not look identical to a healthy one.
- **Cross-entity continuity & control-plane safety** — alert → worktree → lifecycle without losing
  context; destructive actions guarded.

## What it DELEGATES

Glance/hierarchy/friction, per-view robustness + a11y + console, code WCAG, chart honesty, color
perceptual separation, motion feel/API, and doc/observability grounding — all delegated to installed
skills + the Chrome MCP + Context7. The exact mapping and per-delegate constraints (e.g. disable
gstack `design-review`'s fix loop, scope `tufte-data-viz` to real chart panels only) are in
`delegation-map.md`.

## The settled-beat rule (cross-cutting)

The cockpit animates heavily. **Read opacity/DOM/visibility only at a paused, settled beat** — never
mid-transition. An element exiting/entering `AnimatePresence` is transiently `opacity: 0`; sampling it
then produces a false "control absent" finding. Confirm fill/state via the element's CSS class, not
its opacity or `scaleY` (those freeze when the tab is backgrounded). When in doubt, the screenshot is
the tiebreaker. This rule applies to every visibility/state check in Stages 2–4.

## Output

- A durable **Workflow Scenario Catalog** at `docs/design/dashboard/scenario-catalog.md`, refreshed
  each run (Stage 1).
- A per-run **review report** emitted inline (Stage 6), containing: a Scenario Coverage Matrix, a
  State Coverage Matrix (blanks = missing views), a ranked Missing-View backlog, a severity-rated
  findings table with a `delegated-to` column, a glance/self-explanatory verdict, and the delegated
  sub-skill outputs by reference.

## Companion files

- `owned-methods.md` — the five encoded analysis passes, the three-persona heuristic evaluation, and
  the 0–4 severity model.
- `delegation-map.md` — per-dimension delegate + constraints, and the optional off-the-shelf
  delegates.
- `templates/` — `scenario-catalog-template.md`, `review-report-template.md`,
  `missing-view-matrix-template.md`.
- `docs/design/dashboard/review-doctrine.md` (in the repo, not synced) — the dashboard's review
  doctrine and the cyan/amber/green visual grammar this skill enforces.
