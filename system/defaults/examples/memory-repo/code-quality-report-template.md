# Code Quality Report Template

This is a memory-repo example template. Copy or adapt it into a project's
memory layer and revise the tools, sections, thresholds, and wording to match
that project's real quality stack.

Use this template whenever implementation work changes source code and quality
tools are run. The goal is to report what the tools actually found, not only
that they were executed.

## Summary

- Scope checked: `<repo / package / focused paths>`
- Final result: `<passed / failed / partial>`
- Full-suite command: `<command>`
- Focused commands: `<commands or none>`

## Tool Results

| Tool            | Result                                   | Details                                                  |
| --------------- | ---------------------------------------- | -------------------------------------------------------- |
| Ruff            | `<passed / failed / not run>`            | `<lint/import/format findings, or "no findings">`        |
| Pytest          | `<passed / failed / not run>`            | `<passed/skipped/failed counts and notable failures>`    |
| Coverage        | `<reported / not reported>`              | `<total coverage and important uncovered touched areas>` |
| Radon CC        | `<passed / reported / failed / not run>` | `<average complexity plus high-complexity functions>`    |
| Radon MI        | `<passed / reported / failed / not run>` | `<files with maintainability pressure>`                  |
| CRAP-Calculator | `<passed / reported / failed / not run>` | `<threshold count and highest-risk functions>`           |

Adjust this table for the project. For example, a TypeScript project might use
ESLint, TypeScript, Vitest, Playwright, and bundle checks instead of the Python
tools listed above.

## Findings

### In-Scope Findings

List findings in files touched by the implementation. Do not hide these behind
"report-only" wording.

| Severity            | Tool     | File / Function   | Finding                    | Decision                                            |
| ------------------- | -------- | ----------------- | -------------------------- | --------------------------------------------------- |
| `<high/medium/low>` | `<tool>` | `<path:function>` | `<what the tool reported>` | `<fixed / accepted with reason / follow-up needed>` |

### Existing Or Out-Of-Scope Findings

List notable findings outside the implementation scope separately so the
developer can distinguish current risk from inherited repo pressure.

| Tool     | File / Function   | Finding                    | Why Not Fixed Now |
| -------- | ----------------- | -------------------------- | ----------------- |
| `<tool>` | `<path:function>` | `<what the tool reported>` | `<reason>`        |

## Verification Notes

- Quality wrapper result: `<exact result, e.g. "234 passed, 3 skipped">`
- Touched-file complexity result: `<summary or command>`
- Coverage artifact: `<path or "temporary wrapper artifact only">`
- Blockers or skipped checks: `<none or exact blocker>`

## Follow-Up

- `<actionable cleanup or retest item>`
