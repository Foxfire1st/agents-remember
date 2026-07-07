# Deep Research Report Template

Use this template after the developer-agreed reframe and before the lifecycle's
`decide` step. The lifecycle owns when deeper research happens and which proof
categories are required; this template owns the report shape, evidence IDs, and
optional sections.

For small research-only answers, use the compact shape. For ambiguous,
architectural, taxonomy-heavy, bug-root-cause, triage-routing, or build-spawning
work, use the full shape.

## Report Rules

1. Keep the report claim-first: findings state what is now believed to be true.
2. Treat evidence rows as proof records, not activity logs.
3. Every finding cites evidence IDs, and every evidence row names the claim it proves.
4. Mark inference separately when the report reasons across multiple evidence rows.
5. Keep limits visible: every evidence row should say what it does not prove.
6. End with the lifecycle decision: research-only exit or recommended follow-up build.

## Full Shape

```md
# Deep Research Report: <question>

## Frame

- Surface request:
- Deeper objective:
- Agreed scope:
- Non-goals:
- Invariants:
- Truth gaps before research:

## Short Answer

<The current best answer or recommendation.>

## Findings

- F-01: <claim> [E-01, E-03]
- F-02: <claim> [E-02]

## Evidence Ledger

| ID | Kind | Source / Query | Claim Proven | Limits |
|----|------|----------------|--------------|--------|
| E-01 | Semantics | query: "<query text>" | F-01 | Does not prove runtime behavior. |
| E-02 | Intent | onboarding: `<path>`; source: `<path>` | F-02 | Covers committed HEAD only. |
| E-03 | Relationship | cgc callees/deps: `<anchor>` | F-01 | Does not prove hidden business intent. |

## Proof Inventory

- Onboarding docs read:
- Semantic queries performed:
- Code graph queries performed:
- Source files inspected:
- External references checked:
- Executable validations run:

## Remaining Truth Gaps

| Gap | Why It Matters | Blocks Build? |
|-----|----------------|---------------|
| <gap> | <impact> | yes/no |

## Decision

- Research-only exit:
- Spawn build?
- Suggested artifact shape if spawned (minimal `w-02` task vs master + series):
- Plan-gate status:
```

## Compact Shape

```md
## Short Answer

<Answer.>

## Evidence

- E-01 (<kind>): <source/query> proves <claim>; limits: <limits>.

## Proof Inventory

- Onboarding docs read:
- Semantic queries performed:
- Code graph queries performed:
- Source files inspected:

## Remaining Truth Gaps

- <gap or "none known">

## Decision

<research-only exit or recommended follow-up build>
```

## Evidence Kinds

- `Semantics`: fuzzy routing through GrepAI or onboarding search.
- `Relationship`: CodeGraphContext callers, callees, dependencies, impact, or neighboring structure.
- `Intent`: paired onboarding plus bounded source confirmation for contracts, invariants, behavioral expectations, or branch-valid truths.
- `External`: domain documentation, standards, issues, PRs, release notes, or other non-repo references.
- `Executable`: reproduction steps, tests, scripts, command output, or generated validation artifacts.
- `Developer`: explicit clarification from the developer; verify against source before treating it as durable current-state knowledge.
- `Inference`: reasoning across evidence rows. Cite the input evidence IDs in `Source / Query` and state the remaining uncertainty.

## Evidence Ledger Guidance

The evidence ledger should be precise enough that another agent can tell why a
claim is supported without rerunning the entire investigation. Prefer concrete
paths, query strings, anchors, and command names over prose summaries.

Use `Limits` to prevent overclaiming. Examples:

- `Routes the feature area but does not prove current source behavior.`
- `Confirms the source contract at HEAD, not dirty work-in-progress.`
- `Shows the call path but not whether the path is user-facing.`
- `Explains the documented intent but needs executable validation before build.`
