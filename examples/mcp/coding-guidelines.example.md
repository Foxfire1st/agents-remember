# Code Shape and Refactor Discipline

## Design Philosophy

Agents Remember is a memory and onboarding system. The codebase must therefore be easy for both humans and agents to navigate.

The rule is not: "Put the new logic wherever the nearby logic already lives."

The rule is: "Every meaningful concept gets a clear home, and files must stay small enough that their purpose can be understood without re-reading half the project."

A source file is a local unit of ownership. It should usually explain one concept, one adapter boundary, one workflow step, one parser, one service, or one policy area. When a file grows because it has become the easiest place to append behavior, that is architecture drift.

Code structure must preserve these properties:

1. A developer can tell what a file owns from its path and name.
2. An agent can find the right file without scanning thousands of lines.
3. New behavior can be added without touching unrelated responsibilities.
4. Tests can target one behavior boundary without needing the whole system.
5. Onboarding notes can remain local and meaningful because the code file itself remains local and meaningful.

Large files are allowed only when they are explicitly boring: generated output, static data, fixtures, or intentionally centralized declarations. Large files containing branching behavior, orchestration, parsing, command handling, or policy decisions are not acceptable.

## File Size Budget

Treat these as hard review signals for source code files:

|      File size | Meaning                  | Required behavior                                                                                                       |
| -------------: | ------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
|    0-300 lines | Healthy default          | Normal edits allowed.                                                                                                   |
|  300-600 lines | Acceptable but watchful  | New logic is allowed only if it fits the existing single responsibility.                                                |
|  600-900 lines | Refactor pressure        | Before adding logic, explain why this file is still the right home. Prefer extraction.                                  |
| 900-1200 lines | Soft limit exceeded      | Do not add new feature logic without also extracting something.                                                         |
|    1200+ lines | Hard limit exceeded      | Stop and propose a split plan before adding behavior.                                                                   |
|    2000+ lines | Architectural failure    | Treat the file as a refactor target. New logic belongs elsewhere unless the developer explicitly approves an exception. |
|    4000+ lines | Emergency cleanup target | Do not append. Identify responsibilities, seams, and extraction order.                                                  |

The most important rule:

**No agent may make an already-too-large file larger merely because it was the fastest place to patch the task.**

## Function and Class Budget

Use these as defaults unless the file is a generated artifact or intentionally declarative data:

| Unit                     |           Target |                                                    Hard pressure |
| ------------------------ | ---------------: | ---------------------------------------------------------------: |
| Function / method        |      <= 40 lines |                    > 80 lines requires extraction or explanation |
| Class                    |     <= 250 lines |                        > 400 lines requires split or explanation |
| Public methods per class |            <= 10 |                         > 15 indicates too many responsibilities |
| Function arguments       | <= 5 normal args | Use a dataclass/config object when arguments represent a concept |
| Local variables          |            <= 12 |                   Too many locals usually means hidden sub-steps |
| Nested blocks            |      <= 3 levels |   Prefer guard clauses, extracted functions, or strategy objects |
| Cyclomatic complexity    |            <= 10 |           > 10 requires simplification or explicit justification |

A long function is not fixed by adding comments. Comments can clarify intent, but extraction creates a reviewable boundary.

## Split Triggers

Create or use a separate module when any of these are true:

1. The new logic introduces a new noun:
   - resolver
   - validator
   - formatter
   - planner
   - registry
   - adapter
   - provider
   - policy
   - checker
   - parser
   - renderer
   - reporter

2. The new logic introduces a new lifecycle phase:
   - discovery
   - classification
   - validation
   - execution
   - reporting
   - closeout
   - migration
   - cleanup

3. The new logic introduces a new external boundary:
   - filesystem
   - Git
   - MCP transport
   - CLI
   - workspace settings
   - memory repo
   - worktree state
   - benchmark output

4. The existing file already owns two different concerns.

5. The existing file is above 600 lines and the new logic is not a tiny bug fix.

6. The new logic needs tests that would be clearer if aimed at a smaller unit.

## Responsibility Rules

A module should normally be one of these things:

### CLI adapter

Owns argument parsing, command wiring, and conversion into application calls.

Must not own core business logic.

### Application entry point

Owns high-level request flow.

May coordinate services.

Must not contain deep parsing, Git mechanics, filesystem traversal, or policy details inline.

### Service

Owns one domain operation.

May call lower-level helpers.

Must not know about CLI formatting or MCP transport details.

### Policy module

Owns decisions.

Examples:

- whether onboarding is stale
- whether a worktree is valid
- whether a memory update is allowed
- whether a file belongs to a checker category

Policy modules should be easy to test with plain inputs and outputs.

### Adapter / provider

Owns interaction with an external system.

Examples:

- Git command adapter
- filesystem adapter
- MCP adapter
- workspace adapter

Adapters should not decide business policy. They provide facts or perform requested operations.

### Parser

Owns conversion from text/files/process output into structured data.

Parsers should not perform side effects.

### Reporter / formatter

Owns display output.

Reporters should not perform discovery, mutation, or policy decisions.

### Model / dataclass

Owns structured state.

Models should not grow behavior-heavy methods unless that behavior is intrinsic to the model.

## Anti-Patterns

The following patterns are not acceptable:

1. "Just add one more helper at the bottom."
2. "This file already handles related things, so I added another related thing."
3. "The function was already long, so adding a few branches does not matter."
4. "I avoided creating a new file because that seemed heavier."
5. "I kept the logic inline so the reader can see everything in one place."
6. "I added comments instead of extracting the concept."
7. "I reused the application entry point because it already had access to all dependencies."
8. "I added another mode flag instead of creating separate strategies."
9. "I passed more booleans instead of introducing a request/options object."
10. "I changed unrelated logic because it was nearby."

These are drift behaviors. Agents must call them out before continuing.

## Required Agent Behavior Before Editing

Before adding code to any file, inspect the current file shape.

If the target file is above 600 lines, the agent must state:

1. Current approximate file length.
2. Existing responsibility of the file.
3. Whether the new behavior belongs there.
4. Whether extraction is more appropriate.
5. The intended destination if a new module should be created.

If the target file is above 900 lines, the agent must not add new feature logic without a split plan.

If the target file is above 1200 lines, the default action is extraction, not extension.

If the task is urgent and the developer accepts a tactical patch, the agent must mark the file as a refactor follow-up and explain the smallest safe extraction seam.

## Refactor-First Rule for Large Files

When a large file must be changed, prefer this sequence:

1. Identify the smallest coherent responsibility inside the file.
2. Extract that responsibility into a named module.
3. Preserve public behavior.
4. Add or move tests around the extracted boundary.
5. Then apply the requested feature/fix through the new boundary.

Do not perform huge speculative rewrites. The preferred style is small, named extractions that make future work safer.

## Naming Rules for New Modules (Python example)

Use boring names that describe ownership.

Good:

- `worktree_state.py`
- `worktree_registry.py`
- `memory_health.py`
- `onboarding_resolver.py`
- `drift_reporter.py`
- `quality_policy.py`
- `git_status_parser.py`
- `mcp_tool_registry.py`

Bad:

- `utils.py`
- `helpers.py`
- `common.py`
- `misc.py`
- `manager.py` without a precise noun
- `processor.py` without a precise noun
- `handler.py` without the event or boundary it handles

`utils.py` is allowed only for tiny, dependency-free primitives that are genuinely shared and have no domain ownership.

## Boolean Flag Rule

Avoid adding boolean mode flags to functions that already have branching behavior.

Bad:

```python
def update_memory(path: Path, dry_run: bool, force: bool, skip_validation: bool) -> None:
    ...
```
