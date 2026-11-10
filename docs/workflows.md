# Workflows

Sessions route by role through the **`l-01-agent-lifecycles`** skill: a role agent that the
product started follows its handover, and any other session is developer-facing **free chat**.
Free chat answers a question inline; for work on a managed repository, it creates or resolves the
sprint document, writes the request of the developer into it, starts an Architect with
`role_start` (the call for an Architect names no document), and tells the developer that the
agent is in the dashboard and which sprint document holds the request. Another role is started
this way only when the developer names the role and the task. The architect's phase axis is request →
trust-checkpoint → reframe-research → decide → build → close. The job type
(bug / feature / triage / research) is a lens during reframe-research, not a gate.
The only task-format decision is the architect lifecycle's build-mode step.

## Shared Discipline

Every build keeps these rules:

1. Resolve the active context with `context_packet`; follow the [tool reference](../skills/l-01-agent-lifecycles/reference/knowledge/commands.md).
2. Run drift detection before planning against onboarding.
3. Wait for developer approval before implementation (the sprint architect's plan gate).
4. Curate current cards, sidecars and knowledge records against approved code changes in the leaf memory worktree, before publication.
5. Run the checks listed in the resolved memory layer's `system/tools.md` when available.

## Build Modes

At `decide`, the architect lifecycle picks one of:

### Research-only exit

The job answers a question or assesses something and changes no code — no worktree, no task file, no
closeout. Research and triage jobs usually exit here; chat is the right medium for the answer, and
the exit may recommend or spawn a build task.

### Durable task

Chat is never a build route: every code change lives under an approved task document. Small code
work takes the minimal [leaf-task](../skills/l-01-agent-lifecycles/templates/leaf-task.md) artifact; use the full form when the work needs a
durable task file but still fits a compact plan:

```text
ar-coordination/tasks/<repo>/<task-slug>/task.md
```

The task file holds requirements, implementation steps, proposed examples, decisions, open questions,
and references; the checklist is the live execution state. Use it for documentation restructures,
medium refactors, multi-step cleanup, and work that might survive more than one session.

### Master + light sub-task series

When the work outgrows a single-page plan — broad cross-repo or high-risk changes, or several distinct
slices that each need their own checklist and commit — escalate to a **master + light sub-task series**
(`master-template.md`): a wrapper task folder with a canonical master JSON document and numbered
leaf JSON documents, with Markdown renders for reading. Ordinary leaves own paired code and memory
worktree enclosures; an explicit direct-execution grant can select the master checkout instead.
Each slice retains its own approval and landing gates. This composition replaces the retired heavy workflow.

## Worktree-Backed Tasks

Every build is worktree-backed (only research-only exits skip the worktree); the invariant ladder is
task doc (approved) → branch (intent) → worktree (only where something is built). The
`worktree_start` tool creates the task
worktree. Its canonical contract is `series-contract.md`: at
`tasks/<repo>/<task>/series-contract.md` for the master, or
`tasks/<repo>/<task>/enclosures/<leaf>/series-contract.md` for a leaf enclosure. The
worktree tools own lifecycle, integration, cleanup, and closeout under the [authorized lifecycle operation](../skills/l-01-agent-lifecycles/operations/closeout.md).
Ordinary leaves have their own paired code and memory worktrees. A direct-execution grant
explicitly binds a leaf to the master checkout instead.

Closeout and commit approval are separate from implementation approval. The agent presents a dry-run
preview before any authorized closeout creates commits. The git-landing flow (direct vs PR-gated) is
deferred to the repo's `system/task-publication.md` when present, together with the
`landing.workflow` setting that decides which of the two the repository runs.

## Knowledge in a Leaf

The Worker reports changed behavior and hands off the exact candidate; the Curator
reconciles current cards, references, records and history rows in the admitted
memory worktree. A full contract-scoped `memory_quality_check` computes the
change-to-knowledge worklist and gate findings. `knowledge_integrity_check` runs
the validator and returns that leaf's latest persisted worklist; it does not
replace a fresh quality run.

The worklist binds base code B and memory K_B to candidate code C and memory K_C.
The closeout preview and publication routes judge the exact candidate trees,
recomputing the gate rather than trusting a saved worklist. A
`knowledge-gate-refused` result names repair findings; the Curator repairs memory
and reruns checks. There is no gate waiver. The validated curator-coherence
record remains a separate readiness requirement.

For file formats, commands, conversion/crossing and the read-only producer-list
check, see [Knowledge system](reference/knowledge-system.md). Implementation
approval does not authorize a Git commit or landing.

## Choosing A Build Mode

| Situation | Build mode |
| --- | --- |
| Answer or assessment, no code change | Research-only exit |
| One-session code change, low risk | Minimal [leaf-task](../skills/l-01-agent-lifecycles/templates/leaf-task.md) task (thin doc, worktree-backed) |
| Needs a durable plan or checklist | [leaf-task](../skills/l-01-agent-lifecycles/templates/leaf-task.md) light task |
| Outgrows a single-page plan, or broad/high-risk | Master + light sub-task series |
| Parallel implementation or explicit closeout tracking | Any build mode — all builds are worktree-backed through the worktree tools and their lifecycle authority |
