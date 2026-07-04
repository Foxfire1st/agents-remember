# Impact-Analysis Template

A durable report a fan-out sub-agent writes for the **orchestrator** (portfolio phase) or the
**adversarial reviewer** (completion + code-quality lenses). It is the **integrity-bulwark** artifact:
planned-vs-planned **and** planned-vs-past blast radius. Sub-agents WRITE it; AR state mutations stay in
the spawning agent's main loop.

## Rules

1. Cover **two axes**: planned-vs-planned (does this change collide with another master/leaf, present or
   future?) and planned-vs-past (does it regress something already landed — the "fixed one, broke two"
   surface?).
2. Evidence-first: cite the route indexes, `cgc_*` queries, `grepai_search` queries, and
   `read_ar_files` reads that back each finding.
3. State limits per finding — what the evidence does **not** prove.
4. This is a report, not a decision: it feeds the orchestrator's spirit test / reshape proposals or the
   reviewer's verdict; it never decides.

## Shape

```md
# Impact Analysis — <subject: master id | change set | leaf group>

| Field     | Value                                        |
| --------- | -------------------------------------------- |
| for       | orchestrator (portfolio) | reviewer (<seam>) |
| author    | <sub-agent id>                               |
| subject   | <what was analyzed>                          |
| written   | <YYYY-MM-DDTHH:MM>                            |

## Surface Swept
- Routes / areas touched: <route index refs>
- Anchors: <cgc symbols / files>

## Planned-vs-Planned (collisions with other masters/leaves — incl. FUTURE)
| Collision | With (master/leaf) | Kind (code | memory | sequencing) | Evidence | Limits |
| --------- | ------------------ | --------------------------------- | -------- | ------ |
- Recommended resolution: up-front foundation-master extraction | leaf move | sequential ordering | none needed

## Planned-vs-Past (regression surface)
| Risk | Landed thing at risk | Evidence (cgc/grepai/route index) | Limits |
| ---- | -------------------- | --------------------------------- | ------ |

## Evidence Inventory
- Route indexes read:
- cgc queries (callers/callees/deps/impact):
- grepai queries:
- read_ar_files reads (paired source+onboarding):

## Bottom Line (for the spawning agent's main loop)
- <the one or two things the orchestrator/reviewer must act on> — no mutation performed here.
```
