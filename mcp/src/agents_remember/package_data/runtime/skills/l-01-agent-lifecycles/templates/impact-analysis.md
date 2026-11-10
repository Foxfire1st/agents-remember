# Impact-Analysis Template

The Reviewer uses this template to report planned-work conflicts and risks to landed work, including analyses delegated to its helpers. The Orchestrator and developer can inspect the report for planning and review decisions; the report decides nothing.

The Reviewer writes the report or checks and owns its helper’s contribution. When a helper writes the analysis, the spawning agent alone changes Agents Remember (AR) state in its main loop.

## Rules

1. Cover **two axes**: planned-vs-planned (does this change collide with another master/leaf, present or
   future?) and planned-vs-past (does it regress something already landed?).
2. Evidence-first: cite the route indexes and the files opened from
   `read_ar_files` results that support each finding.
3. State limits per finding — what the evidence does **not** prove.
4. This is a report, not a decision: it feeds the Orchestrator's
   [spirit test and proposals to reshape the plan](../roles/orchestrator.md#changes-to-the-plan)
   or the Reviewer's verdict; it never decides.

## Shape

```md
# Impact Analysis — <subject: master identifier | change set | leaf group>

| Field     | Value                                        |
| --------- | -------------------------------------------- |
| for       | orchestrator (portfolio) \| reviewer (<seam>) |
| author    | <analysis role / helper label identifying the assigned routes>      |
| subject   | <what was analyzed>                          |
| written   | <YYYY-MM-DDTHH:MM>                            |

## Surface Swept
- Routes / areas touched: <route index references>
- Anchors: <symbols / files>

## Planned-vs-Planned (collisions with other masters/leaves — including future work)
| Collision | With (master/leaf) | Kind (code \| memory \| sequencing) | Evidence | Limits |
| --------- | ------------------ | --------------------------------- | -------- | ------ |
- Recommended resolution: up-front foundation-master extraction | leaf move | sequential ordering | none needed

## Planned-vs-Past (regression surface)
| Risk | Landed thing at risk | Evidence (route index) | Limits |
| ---- | -------------------- | --------------------------------- | ------ |

## Evidence Inventory
- Route indexes read:
- Identifiers and card paths returned by `read_ar_files`, and the files opened with native file tools:

## Actions for the spawning agent's main loop
- <the one or two things the orchestrator/reviewer must act on> — no mutation performed here.
```
