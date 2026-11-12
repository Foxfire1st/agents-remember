# Requirement and task design, controls and dashboard use

The Architect and Orchestrator use this file when authoring or amending requirements and task structure, placing controls or changing dashboard dependencies. It covers recorded callers and phases, approval, task structure and independent dashboard use. It grants no additional authority.

## Test requirement changes against recorded callers and phases

Before freezing a requirement, test its rule against every caller and phase named by touched records.

## Verify inherited rulings against developer direction

Check inherited rules against the developer’s directions before making them gates.

## Link existing tables instead of copying them

Link an existing table instead of copying it by hand into a task document.

## Describe the existing structure before specifying a replacement

Before a requirement fixes a structure, read the code and name the existing structure in its **Problem** section: files, functions, and who computes what. The developer's sentence is the aim, not the rule.

## Check prior directions before amending requirements

Search recorded directions before amending a requirement; do not contradict an earlier decision.

## Start from the developer’s observed defect

Get the developer’s picture or exact words before specifying a visible defect; mark an unmeasured mechanism as a hypothesis.

## Check native harness behaviour before adding rules

Before a requirement adds a setting, default or handover rule, check what the harness itself does; where the harness leaves it to the agent, AR adds nothing.

## Treat providers as settings unless explicitly constrained

A provider or harness named in a packet describes that day's code, not a limit: providers are role settings, and a rule that refuses other providers needs the developer's word.

## Plan a topic merge or propose a split

A task owns one primary requirement packet. It may also own further approved packets of the same topic. Merge work on one topic. One developer request is one task; do not split it on your own. Propose a split, with its dependencies, to the developer. Keep dependency and preservation context separate.

## Give each role a major step in new tasks

Before a new task starts, write its task document with one major step per role: build, review, curation, knowledge check and landing. Put detail in sub-steps. Step IDs and titles express intent.

## Preserve existing task structure

An existing task gains a step only when the developer slots new scope into it.

## Release requirements through the restriction-disclosure procedure

Before sending a new or changed requirement to a Worker, apply [Parallel work and waiting](../../../core/every-role.md#parallel-work-and-waiting) and [Requirements and limits](../../../core/every-role.md#requirements-and-limits) in the shared instructions delivered with this role, and [Delegate and decide](../../../roles/architect.md#delegate-and-decide) in the Architect role. Those sections govern independent work, guard admission, full inputs and pages, and Investigator proposals, including disclosure and developer acceptance of added refusals, limits or narrowing before release.

## Place user controls precisely

Give a clickable control one named location and state where it does not appear; avoid “wherever X is shown”.

## Preserve independent dashboard use

The dashboard is the developer's tool: before deferring, gating, moving or removing anything it depends on, check that he can open and use it with no agent active, or bring the conflict to him.
