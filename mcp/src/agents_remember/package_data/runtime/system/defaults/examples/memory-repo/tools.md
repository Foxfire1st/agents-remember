# Tools Example

Copy or rename this file to the memory layer's `system/tools.md` when scaffolding a memory repo.

Use this file as the active list of CLI commands, MCPs, code quality tools,
branch workflow notes, and other checks the agent should reference. The derived
path locations are documented in the same memory layer's `system/settings.md`.

## Coding Tools

### Code Quality

List repo-specific lint, format, typecheck, test, build, and smoke-check
commands here.

When implementation work changes source code, report quality results with a
project-adjusted version of `system/code-quality-report-template.md`. The
example template must be adapted to this repository's actual lint, test,
coverage, build, typecheck, and complexity tools. The report should include
the actual tool findings, not only that checks were run.

#### Repo 1

- npm run lint
