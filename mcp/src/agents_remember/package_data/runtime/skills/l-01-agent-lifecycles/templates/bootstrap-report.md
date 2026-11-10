# Bootstrap report

Bootstrap uses this template when writing its setup report. It supplies the sections and state fields for the developer to read.

The [Bootstrap role](../roles/bootstrap.md#report) owns the report duty and completion boundary. The [Bootstrap operation](../operations/bootstrap.md#evidence-and-result-verification) owns its evidence standard.

```md
<taskReportPath>
# Bootstrap Report — <repository>

## Resolved State
- Coordination root:
- Memory root:
- User settings: <path, and created | already there, read and left unchanged | refused: code and reason>
- Harness per role: <role: harness, ... — model and effort left to each harness's defaults>
- Spear branch (the memory's foundation):
- Memory repository branch and HEAD:
- Baseline: <adopted at commit | already adopted | not adopted, and why>
- Onboarding: <present, as read | absent — the Architect's later work, never authored here>
- Knowledge foundation: <not-recorded | recorded at <identity> | unusable: <code and path> — the state read, never a claim that it was made here>

## Decisions Taken
- Spear branch, and the developer's words for it:
- New memory repo or existing:
- Drift acceptance at adoption: <not needed | accepted, at which drift count | declined>

## Steps Run
- <step>: <surface> → <result read back>

## Steps Skipped Or Failed
- <step>: <reason or real output>

## Next Step Named To The Developer
- Start the Architect; its foundation work follows skill://agents-remember/skills/c-14-knowledge-bootstrap/SKILL.md, section "Who Runs It".

## Limitations Stated To The Developer
- <capability that does not exist, named as absent>

## Boundaries
- No code committed, no branch moved, no worktree created: yes
- Memory content written only through the setup surfaces: yes
- Existing memory artifact migrated, rewritten, or deleted: no
- Onboarding or knowledge records authored by this seat: no. Onboarding is authored in the later
  Architect task. The Curator authors the knowledge foundation. This seat reports only the state it read.
```
