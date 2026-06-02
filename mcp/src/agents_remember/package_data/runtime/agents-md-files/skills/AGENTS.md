# Core Skills

## Routing

1. Need to know where memory, tasks, docs, settings, worktrees, or cross-repo allowances live?
   → c-08-ar-coordination-context-resolver

2. Missing repo memory scaffold?
   → c-00-initialize-memory-repo

3. Existing onboarding may be stale, unverifiable, missing, or orphaned?
   → c-02-memory-quality-control

4. A confirmed fact appeared and needs durable placement?
   → c-01-findings-capture

5. Repo or area lacks enough onboarding to safely work there?
   → c-03-repo-bootstrap

6. Need to choose between semantic search, relationship graph queries, and
   onboarding/source proof before reasoning, planning, answering, or editing?
   → c-04-retrieval-strategy-router

7. Need to create/update file onboarding, inline onboarding, entity catalogs, references, metadata, or update history?
   → c-05-create-or-update-onboarding-files

8. Need worktrees, task contracts, closeout, memory ledger alignment, integration, or cleanup?
   → c-09-git-worktree-manager

9. Existing external-memory onboarding should become the initial ledgered baseline?
   → c-10-adopt-memory-baseline

10. Need to carryover memory from one memory branch to another?
    → c-11-memory-carryover-from-branch

## Reference Style (skills & MCP tools)

When writing or editing any skill or documentation, name skills and MCP tools
explicitly and in full — never abbreviate, so a reader can always tell a skill
invocation from an MCP tool call:

- **Skills** — the full lowercase skill id followed by the word "skill":
  e.g. *the `c-12-closeout` skill*, *the `l-01-session-job-lifecycle` skill*.
  Never use an abbreviation (`C-12`, `L-01`, `W-02`) and never the bare id
  without the word "skill". Skill directory names match the installed lowercase
  ids (e.g. `runtime/skills/c-12-closeout/`).
- **MCP tools** — the snake_case tool name qualified with "MCP tool":
  e.g. *the `context_packet` MCP tool*, *the `grepai_search` MCP tool*. Never
  label a tool a skill or a skill a tool.
