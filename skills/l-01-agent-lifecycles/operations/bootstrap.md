<!-- ar:roles all -->
# Operation — Session Bootstrap

Bootstrap uses this operation for a new user's first hour around the memory repository. It explains Agents Remember, records the developer's setup decisions, initializes settings and memory through their owning surfaces, verifies the result, and names the Architect as the next step.

Initial onboarding and the knowledge foundation are the Architect's regular work, not stages of this operation.

## When Bootstrap applies

Select this operation when a repository has no working memory root or its root does not resolve. Cases include no root, no baseline or an unresolvable coordination root.

With an attributed baseline and resolving context, apply [Bootstrap's scope boundary](../roles/bootstrap.md#boundaries) when choosing who continues the work. For a resolving repository without a recorded knowledge foundation, use step 8 below, not re-entry.

## Who carries the operation

Only Bootstrap carries this operation. Before asking a setup question, follow [its own-chat reporting boundary](../roles/bootstrap.md#boundaries).

Establish the developer's situation and decisions, then follow the normal workflow below. Where a `c-*` skill owns a procedure, hand that procedure to its owner.

## Required workspace facts and decisions

Bootstrap starts before any task document and needs these workspace facts:

- the intended code repository and its absolute root;
- the resolved `coordinationRoot` and `workspaceRoot` from `server_info` on `agents-remember-task`;
- whether that repository's memory root resolves and its current state.

Ask the developer in plain language for two initial decisions:

1. Which code branch is the foundation of the repository's memory? This branch is the **spear**.
2. Should setup scaffold a new memory repository or point to an existing one?

Briefs, tickets, leaf documents, worktree contracts and gates are not inputs. Their absence is normal.

## Normal workflow

Each step names its owning surface. Run that surface and point to its `c-*` skill; do not restate the skill's procedure. Call the tools named here only on `agents-remember-task`, never another installation's server.

### 1. Explain what the setup changes

Before asking for decisions, explain Agents Remember in plain language. The developer must know that the code stays where it is and memory lives in a separate Git repository under the resolved coordination root.

Onboarding documents live in memory, not beside the code. A small mapping records which memory content belongs to which code commit. It is one file named `memory.md` at the memory repository root, computed by adoption rather than authored by hand.

### 2. Establish the resolved context

Use `server_info` on `agents-remember-task` for the coordination root and allowed repositories, and `context_packet` to determine whether memory resolves. Report any missing fact exactly.

### 3. Record the repository's decisions

Memory is scaffolded and verified against the chosen spear branch's state. Record that choice as the name of the memory repository's initial branch. Changing the spear later requires a separate carryover operation, not an edit.

### 4. Write the initial user settings

Use `user_settings_init` on `agents-remember-task` once. Ask which harness each role should use, offering only harnesses this host runs. One harness may serve every role if the developer chooses it; at least one is required.

Leave model, effort and other settings unset intentionally. A role configured only with a harness uses that harness's defaults.

Preview with `dry_run=true`, write, then read back the state. Read and report an existing settings file's path and contents; never overwrite, merge or complete it. An existing file is not a gap. Report a refused write with its exact path and reason. Bootstrap changes no mode.

### 5. Initialize or repair the memory root

Before initializing or repairing the memory root, read [Initialize memory repository](../../c-00-initialize-memory-repo/SKILL.md) for the owning procedure and scaffolding details. Use `memory_init` on `agents-remember-task`, previewing with `dry_run=true` first.

### 6. Inspect what the baseline will carry

Read the memory root's own files to see what adoption commits; author none of that content. `docs/` and `system/` are enough to adopt. An absent `onboarding/` directory is expected, not a gap to close here.

Explain that optional onboarding and the knowledge foundation are later regular work of the Architect the user will start, inside its task; step 8 names the foundation's owner. Neither is this seat's work.

### 7. Adopt the first attributed baseline

Before adopting the first attributed baseline, read [Adopt memory baseline](../../c-10-adopt-memory-baseline/SKILL.md#workflow) for the procedure. Use `memory_baseline_adopt` on `agents-remember-task`, first previewed with `dry_run=true`. Read `memory_baseline_status` before and after adoption. Ask the developer for the drift-acceptance decision; do not assume it.

A validator refusal is unconditional. Report its actual output.

### 8. Report the knowledge foundation's state

Read and report the knowledge foundation's state, then name the next step at the handover below.

## Identify an unfamiliar failure state

When a step returns a state you cannot name, read [the failure states]({{detail:reference/bootstrap/failure-states.md}}) before interpreting it. An agent that cannot name the state is guessing.

## Evidence and result verification

Report claims are checked against the recorded observation, not a promise that a later live call returns the same answer.

Bootstrap has no independent acceptor, task document, gate or Reviewer. Its report is completion truth—a claim about its own work—not acceptance. Say so; do not imply an independent check.

For each evidence claim, record the surface, exact arguments, recorded answer and before and after state, so a reader can reproduce the observation from that evidence without asking Bootstrap. If no surface supplies evidence for a claim, label it as Bootstrap's observation, not evidence.

Preview every effectful step with `dry_run=true`. After each mutating step, call the surface that reports the resulting state. Applied but not read back is incomplete.

Record every failed or skipped step and its real output under **Steps Skipped Or Failed**. An empty failure section cannot support an unreproducible success claim.

## Authority gates

Before taking a setup action, apply [Bootstrap's boundaries](../roles/bootstrap.md#boundaries) for permitted surfaces, prohibited task and authoring actions, and the adoption-owned memory-content commit exception.

Ask before any irreversible operation. Removing, migrating, repointing or rewriting an existing memory artifact is never a quiet Bootstrap decision.

## Failure handling

### A developer decision is missing

The two initial setup decisions belong to the developer:

- No foundation-branch choice: report that setup cannot pass step 3, state the question and stop. Do not pick a branch.
- No answer about an existing memory repository: resolve it from `context_packet` on `agents-remember-task` and report the result instead of asking again.
- No intended repository: stop. There is no default repository.

### A step fails or needs a developer ruling

Report a failed step with the real output from `agents-remember-task`, not a paraphrase. Do not proceed past it or describe the intended result as observed.

Once the cause is fixed, or the developer says so, run the same step with the same options, or resume as this failure procedure says. Never rerun with different options to obtain a passing result.

A refusal requiring the developer's decision stops the operation at that step. State the required decision.

### A capability is absent or instructions contradict the system

Name an absent capability precisely. Identify the unexercised part of a route rather than calling the whole route complete or missing.

The role’s first message carries the compiled instructions. A launch that cannot compile them reports its named refusal rather than starting a session without instructions. Report the actual launch result and any instruction surface that says otherwise.

Report a contradiction between Bootstrap's instructions and the system as an instruction finding. Do not work around it silently.

## Handoff / exit

Bootstrap ends when the owning surfaces establish all of these facts:

- resolved memory and coordination roots;
- foundation branch;
- user settings path and the initialization result;
- baseline existence and commit;
- whether onboarding exists, read rather than authored here;
- the knowledge foundation's recorded identity, or the named state showing it is not recorded;
- every skipped step's reason.

Before naming the next step in the conversation and report, follow [the foundation handover](../reference/bootstrap/foundation-state.md#name-the-next-step). Then hand over and stop within Bootstrap's boundaries for the transition to ordinary lifecycle work.
