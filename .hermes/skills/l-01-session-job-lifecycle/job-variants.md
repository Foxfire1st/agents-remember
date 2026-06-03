# l-01-session-job-lifecycle Job Variants — The Lenses

The job type is a **lens**, picked during `frame` and re-pickable at any time. It is not a gate and it
never changes the spine (`orient -> ground -> frame -> decide -> build -> close`). A lens tunes three
things only:

- **Opening move** — the first concrete thing `frame` does for this kind of job.
- **Retrieval lean** — which `c-04-retrieval-strategy-router` strategy leads (others are still available).
- **Decide default** — where this job usually lands at `decide` (still a real decision, not automatic).

| Job        | Opening move (in `frame`)                       | Retrieval lean (`c-04-retrieval-strategy-router`) | `decide` default            |
| ---------- | ----------------------------------------------- | ---------------------------------- | --------------------------- |
| `bug`      | reproduce the failure; prove the root cause     | Relationship (cgc) + Intent        | -> build                    |
| `feature`  | clarify intent, scope, and explicit non-goals   | design doctrine + Intent           | -> build                    |
| `triage`   | assess severity, blast radius, and ownership    | breadth scan (Semantics first)     | may exit (route or spawn)   |
| `research` | state the question precisely                    | Semantics (grepai) + onboarding    | research-only exit          |

## bug

Lead with a reproduction and a proven root cause before proposing a fix — an unproven fix is a guess.
Lean on Relationship (callers/callees/impact via cgc) to bound the change, and Intent to confirm the
invariant the bug violates. Defaults to a build; the fix lands through `decide -> build -> close`.

## feature

Lead by pinning down intent, scope, and the non-goals — what this feature is explicitly *not*. Use the
design doctrine in `tasks/AGENTS.md` to pressure-test the shape, and Intent retrieval to find the
contracts the feature must respect. Defaults to a build; size decides chat vs durable `w-02-light-task-workflow` task at
`decide`.

## triage

Lead by assessing severity, blast radius, and ownership — enough to route, not to fix. A breadth scan
(Semantics first) maps the surface fast. Triage frequently **exits research-only**: its product is a
recommendation, a routed owner, or a spawned build/bug job — not a code change. Only escalate to a
build when triage itself is the cheapest place to fix it.

## research

Lead by stating the question precisely. Semantics (grepai over onboarding) plus committed-state
onboarding usually answers it; reach for source only as bounded Intent confirmation. Research
**exits research-only** by design: it produces an answer, and may recommend a follow-up build job, but
performs none itself.
