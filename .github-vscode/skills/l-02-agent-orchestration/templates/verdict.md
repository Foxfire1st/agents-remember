# Verdict Template (adversarial reviewer)

The artifact an **adversarial reviewer** writes at a seam (`jobs/adversarial-reviewer.md`). It attaches
to the handover gate as **judge evidence**. Two variants share one shape: **master-exit** (before a
manager hands over to the orchestrator) and **super-exit** (before the orchestrator hands over to the
developer).

## Rules

1. **A verdict is evidence, not a decision.** State an explicit pass/block **recommendation**; the gate's
   decider decides. Never write the verdict as if it were the gate outcome.
2. **A block must decompose into fix leaves** — concrete, leaf-shaped findings the owning
   manager/orchestrator can dispatch. A block that cannot be named as fix leaves is not yet a block.
3. **Refute-or-confirm:** every finding must survive an attempt to refute it. Rank findings; cite the
   backing sub-agent report.
4. Cover all three lenses (completion · code quality · onboarding-vs-code) explicitly, even to say a
   lens is clean.

## Shape

```md
# Adversarial Verdict — <seam: master-exit | super-exit> · <master id | super branch>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | master-exit | super-exit                            |
| scope           | <master integration branch ref | super branch ref> |
| reviewer        | <this reviewer's agent/lifecycle id>               |
| task docs       | <master task_doc | portfolio task docs>            |
| recommendation  | PASS | PASS-WITH-NOTES | BLOCK                       |
| decider         | <manager | orchestrator | developer> (per L4 policy)|
| written         | <YYYY-MM-DDTHH:MM>                                  |

## Lens 1 — Completion vs Task Docs
- <requirement/step> — addressed | delta (justified: decision-log ref …) | MISSING
- backing report: <templates/impact-analysis.md artifact ref>

## Lens 2 — Code Quality
- system/tools.md suite: green | failing:<which>
- regressions vs the past (route indexes · cgc · grepai): none | <finding>
- backing report: <ref>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- drift_check clean · memory_quality_check clean: yes | <finding>
- route overviews current: yes | <finding>
- backing report: <templates/onboarding-coherency.md artifact ref>

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence (report ref) | Survives refutation? |
| - | -------- | ------- | --------------------- | -------------------- |

## If BLOCK — Fix Leaves (decomposed, leaf-shaped)
1. <fix leaf: scope · target files · done-when> → dispatch under <master | super>
   (a BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves)

## Judge-Evidence Note
This verdict attaches to the <seam> handover gate as judge evidence. The decider (<role>) decides;
this reviewer does not.
```
