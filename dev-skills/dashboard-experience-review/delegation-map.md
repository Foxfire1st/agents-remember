# Delegation map

This skill is a conductor: it owns the workflow-completeness layer (`owned-methods.md`) and delegates
the bounded craft dimensions to installed skills + MCP tools. Delegate by handing each delegate a
**resolved, settled view** (URL + viewport, paused at a settled beat) and **fold its findings by
reference** — do not re-derive them.

Global rule: **findings only.** Disable any delegate's auto-fix/auto-commit behaviour before invoking
it; a fix is a separate gated build job.

## What the conductor OWNS (no delegate exists)

Scenario discovery · missing-view diff · observability-parity · motion-as-communication ·
color-as-only-signal · Task-6 TUI UX · stale/freshness honesty · RED/USE altitude · cross-entity
continuity · control-plane safety. (See `owned-methods.md`.)

## Delegation table

| Dimension | Delegate to | Constraint |
|---|---|---|
| Self-explanatory glance / Trunk test / Goodwill-friction / App-UI hierarchy | gstack `design-review` | **Findings-only — disable its fix loop.** Ignore its dead `$B` browse binary; feed observations from the Chrome MCP. Discard its CSS-motion heuristics (our stack is GSAP/Motion, no CSS animation). |
| Per-view robustness (empty/loading/error/overflow), console, 5 interactive states, responsive, a11y | secondsky `design-review:design-review` | Findings-only. Bypass its Playwright wiring; feed from the Chrome MCP. Skip its perceived-performance/motion verdicts (owned by Method 4 + emil). |
| Code-level WCAG / interface-guideline (file:line) | `web-design-guidelines` | Code pass; complements the live a11y pass, doesn't replace it. |
| Chart / sparkline / quant-panel honesty (Tufte) | `tufte-data-viz` | Scope to **literal chart panels only** — never the engine-room SVG. Suppress its anti-animation stance. |
| Color state-language perceptual separation + label/terminal contrast | `color-expert` | It has no eyes — **feed it measured computed hex** captured via the Chrome MCP `javascript_tool` at a settled beat. |
| Motion feel / easing / duration / should-it-animate / interruptibility | `emil-design-eng` | Tell it the stack is **GSAP timelines + Motion, no CSS animation**; want motion-spec verdicts, not CSS keyframes. |
| Motion API correctness (eases, reduced-motion, AnimatePresence/layoutId, ScrollTrigger) | `gsap-skills` + `motion` | Ground claims in source, not assumed `easeInOut` (Motion's no-ease tween default is `cubic-bezier(0.25,0.1,0.35,1)`). |
| Visual polish / spacing / AI-slop / microcopy / structure-as-meaning | gstack `design-review` + `frontend-design` (copy + structure lenses only) | `frontend-design`'s marketing-page framing is out of scope; borrow only its microcopy + structural lenses. |
| Plan-level IA / journey / per-feature state-table templates | `plan-design-review` (Pass 1/2/3 **as method**) | Borrow the passes as templates filled from **observed live behaviour**; do **not** run it in plan mode on a fabricated plan. |
| Live observation: drive, screenshot, console/network, computed style, GIF | Chrome MCP (`claude-in-chrome`); `playwright-cli` (WSL fallback) | Enabler, not judgment. Sample motion only at a paused settled beat; native Chrome is the motion confirmer. Purge `.playwright-cli/` before closeout. `list_connected_browsers` returning `[]` usually = the extension is toggled off, not absent. |
| Stack + observability doc grounding | Context7 (+ `react-aria` / `pmndrs` MCPs) | Grounds findings (GSAP/Motion/Panda/React-Aria/xterm + Grafana RED/USE doctrine); decides nothing. Do not install Grafana skills — pull the doctrine via Context7. |

## Optional off-the-shelf delegates (not required)

If installed, the conductor may delegate to these for Stage 3a / scenario work; otherwise it runs the
encoded methods itself. Neither is needed, and neither can derive the AR scenario set from the running
system — that stays owned.

- `mastepanoski/cognitive-walkthrough` (MIT) — a clean standalone 4-question walkthrough engine with
  per-step severity. Stage 3a may delegate the per-scenario walkthrough to it.
  Install: `npx skills add mastepanoski/claude-skills --skill cognitive-walkthrough`.
- `mastepanoski/ux-audit-rethink` (MIT) — a generic journey / findability / progressive-disclosure
  second opinion. Use only as a cross-check against the owned analyses.
  Install: `npx skills add mastepanoski/claude-skills --skill ux-audit-rethink`.

## Already-installed inventory this relies on

gstack `design-review`, secondsky `design-review:design-review`, `web-design-guidelines`,
`tufte-data-viz`, `color-expert`, `emil-design-eng`, `frontend-design`, `taste-skill`,
`plan-design-review`, `gsap-skills`, `motion` — plus the Chrome MCP and Context7 (+ react-aria,
pmndrs) MCP servers. If a delegate is missing at run time, record it as a coverage gap in the report
rather than silently skipping the dimension.
