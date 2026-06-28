# Scenario Catalog — template

The durable catalog lives at `docs/design/dashboard/scenario-catalog.md` and is refreshed by Stage 1.
Each scenario is an operator-language **job story** mapped to the views that serve it, with the steps
where a user could get stuck and the UI states each step must handle. Use this shape per scenario.

---

## W<N> — <short scenario name>

**Persona:** <operator | incident-responder | expert>
**Job story:** "When <situation>, I want to <motivation>, so I can <outcome>." (user words, no internal IDs)
**Frequency:** <how often a user does this>

**Steps → serving view → stuck risk**

1. <step in user words>            → <view/affordance that serves it>   → <ok | ⚠ describe stuck risk>
2. <step>                          → <view>                             → <ok | ⚠ …>
3. <step that may have no view>    → <view or **GAP**>                  → <⚠ missing-view if no view>

**Forced states this scenario must verify:** <subset of: content · first-run-empty · zero-result-empty ·
cleared-empty · loading · partial · stale-disconnected · offline/5xx/403/404/validation/ratelimit ·
permission · overflow>

**Known defects (carried from prior reviews / onboarding):** <e.g. a documented visual bug at a state>

---

## Conventions

- One `W<N>` heading per scenario, numbered in rough order of how a user encounters them.
- A step whose serving view is **GAP** is a missing view; it must also appear in the missing-view
  matrix and as a finding.
- Keep job stories in the user's language; put the system entities/states in the steps and forced-state
  line, not in the job story.
- When Stage 1 discovers a new reachable entity state with no scenario, add a scenario (or a step) for
  it rather than dropping it.
