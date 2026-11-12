# Repository research

Curators and Investigators use this file to research a repository for its first or resumed knowledge foundation. It covers the source inventory the developer reviews, the bounded way the research proceeds, and what counts as evidence. A research run produces evidence in the task's notes; it writes no memory.

## Review the sources before research

Resolve the repository context and the memory's `system/sources.md` registry before selecting anything. Treat the registry as a routing index.

Show the developer the source inventory before the research begins and before asking for a broader source list:

| Source | Category | Location | Status | Planned use |
| --- | --- | --- | --- | --- |
| `<name>` | Domain Documentation | `<path or URL label>` | readable / unreadable / unavailable | `<what it will establish>` |

List the sources that will not be used and say why:

| Source | Category | Reason |
| --- | --- | --- |
| `<name>` | `<category>` | unavailable / stale / unrelated / wrong branch |

List the categories that are missing or weak:

| Category | Why it may matter |
| --- | --- |
| Domain Documentation | area findings suggest business rules not visible in code |

Ask only after the inventory is shown: whether it is correct, whether any source should be excluded, whether important documentation is missing, and whether internal pages, schemas, protocol specifications or allowed adjacent repositories should be added. Record the developer's corrections, additions and exclusions with the inventory in the task notes.

When the repository already has memory and its sources changed, review the source-inventory delta before expanding the research.

Use the resolved path rules to select source paths. When the resolved settings lack the common exclusions for generated, vendored, build, cache, environment and machine-specific files, record that in the source review and prefer repairing `system/settings.json` over adding an agent-local filter.

## Bound the research

A whole repository cannot be understood safely in one pass. Plan a bounded current-state investigation, and record after each step the areas examined, the areas deferred or not examined, the open questions, and one concrete action that resumes the work.

**Scout first.** The scout reads broadly and shallowly: top-level structure, package manifests, build files, entrypoint and routing hints, configuration, existing onboarding, the reviewed source inventory, and the adjacent signals the topology allows. It must not read every file deeply. It assigns every eligible top-level source path to one functional area or marks it excluded, gives each area a boundary and a priority with the reason, records cross-repository hints even when there are none, and ends with a report holding the technology profile, the functional areas, the cross-repository interface map, what is out of scope, the unresolved questions and the questions for the developer.

In a gated run, the developer confirms the area map before area research starts. An automated run records its assumptions and continues; it does not skip the review artifacts.

**Research one area at a time.** Cover structure, interfaces, patterns and concerns. Tag every finding `[HIGH]`, `[MEDIUM]` or `[LOW]`; a `[LOW]` finding stays parked and never becomes a fact. Merge the area reports into a concise brief that preserves the confidence tags, records contradictions, names the key files and suggests the routes that should govern them.

**Prioritize by risk.** Spend the research on hidden invariants and fragile logic, real cross-repository boundaries, central behavior and entrypoints, state transitions, security-sensitive validation and domain mapping. Defer routine helpers, passive data and configuration until a task touches them, and exclude generated, vendor and build output.

When the research resumes after the source tree moved, classify the change as expansion, move, cleanup, refresh or defer, and start from the source-inventory delta.

A run that stops early names the areas it did not reach and keeps a resumable next action. It never presents a partial investigation as complete.

## Treat sources as evidence

A registry entry, a search hit, an embedding hit or a source list is a pointer, not proof. Open the actual document or source file and cite that.

`system/sources.md` routes discovery. Use its Domain Documentation category as the discovery route for business rules and external behavior when that category is present, and cite the documentation itself rather than the registry. When a source is missing or cannot be read, record what was checked; never invent a source or imply that the search space was complete.

Keep the evidence the claim needs, with the origin identity: the repository path and line range, or the external document's name and the version or retrieval time actually read. The [repository foundation](curator/repository-foundation.md) detail owns the versioning rule for external foundation evidence.

When no relevant evidence exists, keep the section and record what was checked and that nothing relevant was found.

Use local source, test, configuration or artifact evidence for a same-repository claim. A cross-repository claim needs genuine external evidence at both ends, with the producer and the consumer matched. A naming-only tie is `[LOW]` and never becomes a durable fact; a same-repository implementation fact belongs in repository-internal references, not in a boundary record.

Read only the adjacent repositories the resolved settings allow, check the branch, and treat them as read-only. Record the branch and evidence limits and the confidence they support, and cite another repository's current identity rather than a paired-repository Git blob. Never update another repository's memory.

Documentation evidence and cross-repository boundary evidence are separate passes with separate provenance.

### Documentation evidence pack

Record the scope; the documentation sources checked, with name, type, location and result (relevant, not relevant or unavailable); the confirmed findings, each with its direct evidence, source path, the files or routes it applies to and its confidence; the constraints; the terms and definitions; the files likely affected; a table of topics where no relevant evidence was found, naming the sources checked and what was missing; and the open questions.

### Boundary evidence pack

Record the allowed adjacent repositories, with expected branch, actual branch, status and allowed use; a boundary summary; the confirmed interfaces, with direction, this repository's side, the other repository or system's side, the interface, the evidence and the confidence; the shared contracts, with each side's location and the synchronization requirement; the branch and topology notes; the same-repository facts that must not be classified as cross-repository; the boundary risks; and the ties that need the developer's confirmation.

## Know the hard stops

Stop and ask the developer when:

- the memory root cannot be resolved, or several are plausible;
- the target repository is ambiguous;
- the source inventory cannot be reviewed before the research begins;
- a required source is inaccessible for a load-bearing claim;
- the cross-repository settings are ambiguous for a required boundary, or the adjacent repository is not on the expected branch;
- documentation and code contradict each other on a load-bearing behavior;
- the output would require updating another repository;
- a `[LOW]` claim would otherwise become a durable fact.

## Hand the research over

The run writes no memory: no card, overview, knowledge record or history row. It ends with a research handoff in the task notes that names:

- the evidence produced, with its paths;
- the areas examined, not examined and deferred;
- the open questions;
- the sources not used, and why;
- the known risks;
- the next useful action.

The Curator of the foundation task reconciles the handoff against the admitted revisions and authors the records through the writer. Without a handoff, the Curator follows the foundation detail's inventory step directly and continues; the research run is optional, the foundation is not.

## Who runs it

The Curator of a foundation task reaches this file from [the repository foundation detail](curator/repository-foundation.md) and may run the research with helpers, as [the Curator role](../roles/curator.md#organise-helpers-within-the-assignment) allows. If the Architect starts an Investigator for the research, the [Investigator role](../roles/investigator.md#read-and-investigate) points here, and the Investigator's report path is recorded in the foundation task document so the Curator finds the evidence.

A run may use helpers in bounded pieces: a scout over the structure, one lens per area, a merge into the area brief, a documentation researcher over the approved sources, and a boundary researcher over the allowed adjacent repositories. The run's lead reads the distilled reports rather than the raw source trees, and answers for every part.

Research is evidence, not authored knowledge. The Curator's judgment and the writer turn it into records.
