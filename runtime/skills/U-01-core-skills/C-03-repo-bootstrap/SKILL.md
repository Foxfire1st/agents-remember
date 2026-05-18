---
name: c-03-repo-bootstrap
description: "Bootstrap onboarding for undocumented repos or existing memory slices. Builds root overviews, route-local overview pillars, evidence packs, file cards, onboarding waves, deleted-slice cleanup, curator reviews, and handoff while keeping the orchestrator thin."
---

# Repo Bootstrap

Bootstrap durable onboarding for a repository that has little or no memory coverage, or for an already-ledgered memory repo whose source slices need targeted creation, refresh, move handling, or cleanup.

The minimum successful bootstrap is one root repo overview under the C-08 resolved `onboarding_root`:

```text
overview.md
```

For larger repositories, the scalable path is route-local and wave-based:

```text
root overview.md
  -> area research
  -> coverage plan
  -> governing route map
  -> route-local overview.md construction pillars
  -> evidence packs where needed
  -> file cards
  -> file-level onboarding waves
  -> curator review
  -> handoff
```

**Core constraint:** LLM context is a rolling window. A full repository cannot be understood safely in one unbounded pass. This skill breaks bootstrap into bounded phases where agents write durable artifacts to disk and the orchestrator reads only distilled artifacts, not raw repo dumps.

---

## Design Philosophy

### Locality-first memory

Durable memory must live where agents naturally look while traversing the codebase.

A root `overview.md` explains the repository as a whole. A route-local `overview.md` explains a source subtree as if that subtree were a small repository of its own. A file-level onboarding document explains one concrete source file and links back to the nearest governing overview.

Preferred durable placement, with every path relative to the resolved `onboarding_root`:

```text
overview.md
<mirrored-source-folder>/overview.md
<mirrored-source-file>.md
```

Detached `bootstrap/areas/*` artifacts are allowed as temporary research and promotion artifacts. Durable agent-facing overviews exist to be discoverable through source-path traversal.

### Overview as construction pillar

Route-local `overview.md` files are intermediate durable memory artifacts created after area research has identified:

1. where an area begins in the source tree
2. what the area governs
3. which files are load-bearing
4. which concepts repeat across files
5. which docs or cross-repo boundaries affect the area
6. which files should later receive file-level onboarding

Later file-level onboarding workers use the nearest governing `overview.md` as a construction pillar. The overview supplies the local area model; the file-level onboarding preserves concrete file-specific knowledge.

### Progressive discovery read order

When working on a source file, read onboarding from broadest to narrowest:

```text
overview.md
<ancestor-folder>/overview.md
<nearest-folder>/overview.md
<source-file>.md
```

For example:

```text
source:
  src/helpdesk/mappers/PlatformMapper.php

onboarding read path:
  overview.md
  src/overview.md
  src/helpdesk/overview.md
  src/helpdesk/mappers/overview.md
  src/helpdesk/mappers/PlatformMapper.php.md
```

This lets an agent reconstruct context whether it starts at the repo root or gets dropped directly onto a source file.

### File-level self-sufficiency

File-level onboarding stays first-class. It must not collapse into “see overview.md”.

A file onboarding document must carry the file-specific facts that protect that file:

- purpose
- logic
- conventions
- invariants and boundaries
- repo-internal relationships
- docs references that affect this file
- cross-repo behavior that affects this file
- update history

It must also backlink to the nearest governing overview so future agents discover the local area model.

### Knowledge promotion pipeline

Do not promote raw findings directly into durable memory. Use intermediate artifacts:

```text
raw repo observation
  -> scout finding
  -> area report
  -> coverage plan
  -> governing route map
  -> overview card
  -> route-local overview.md
  -> evidence pack
  -> file card
  -> file-level onboarding
  -> curator review
```

Exploration artifacts help agents understand. Promotion artifacts help agents and humans decide what becomes memory. Durable memory artifacts are the reviewed layer future agents will trust.

---

## Behavior To Preserve

This skill intentionally keeps the strongest current bootstrap behavior.

1. **Thin orchestrator** — the orchestrator coordinates, reads distilled artifacts, updates state, and enforces gates. It does not ingest raw code, directory trees, grep dumps, or full repo search output.
2. **Bounded specialization** — scout, structure, interface, pattern, concern, docs, boundary, overview, file, and curator workers operate on narrow scopes.
3. **Confidence tags** — all factual findings in research and promotion artifacts use `[HIGH]`, `[MEDIUM]`, or `[LOW]`.
4. **Durable checkpoints** — every phase produces artifacts that allow the bootstrap to stop and resume.
5. **Topology awareness** — bootstrap resolves the active memory root, path rules, storage rules, source registry, and cross-repo rules through `C-08-ar-coordination-context-resolver` before writing artifacts.
6. **Cross-repo read-only semantics** — adjacent repos may be read only when allowed; only the target repo's memory is updated.
7. **Developer consultation where it matters** — gated mode pauses at review gates; automated mode still parks uncertain claims and writes review artifacts.
8. **C-05 ownership of file-level onboarding** — C-03 prepares file cards and waves, but C-05 owns the canonical file-level content model.

---

## Inputs

| Input            | Required | Description                                                                                                                                                             |
| ---------------- | -------: | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo`           |      yes | Target repository name.                                                                                                                                                 |
| `topology`       |       no | Optional override passed through C-08. Normal bootstrap passes only `repo` and lets C-08 resolve topology.                                                              |
| `coordination-root` |    no | Optional coordination-root hint for explicit external-memory operations or repair.                                                                                       |
| `control-mode`   |       no | `gated` or `automated`. If omitted, ask during Phase 0A.                                                                                                                |
| `bootstrap-mode` |       no | `quick-orientation`, `safe-starter-memory`, `cross-repo-focused`, `domain-doc-focused`, `existing-memory-slice-maintenance`, or `full-bootstrap`. Defaults to `safe-starter-memory` when the user is unsure. |
| `seed-context`   |       no | Optional paths to known onboarding or documentation that should seed the run.                                                                                           |
| `priority-areas` |       no | Optional area names or source routes to prioritize. If omitted, scout identifies them.                                                                                  |

Do not build compatibility behavior for older bootstrap depth labels. This repo is alpha; prefer clear current behavior over old aliases.

---

## Control Modes

Bootstrap supports two control modes.

### Gated workflow

The orchestrator pauses after major phases for developer review:

- source inventory review
- scout / area map
- root overview
- governing route map
- first route-local overview wave
- first file onboarding wave
- final handoff

Use gated mode when the repo is business-critical, unfamiliar, or likely to contain hidden company intent.

### Automated workflow

The orchestrator may continue through the full bootstrap after setup without phase-by-phase developer approval, as long as hard stop conditions are not hit.

Automated mode removes human approval gates after setup. It does **not** remove evidence gates, review artifacts, curator reviews, state updates, or final handoff.

Automated execution starts only after the Phase 0 source inventory has been presented, corrected or accepted by the user, and recorded in `bootstrap/input-ledger.md`.

| Phase                     | Gated mode                   | Automated mode                                         |
| ------------------------- | ---------------------------- | ------------------------------------------------------ |
| Source inventory          | pre-automation intake gate   | pre-automation intake gate                             |
| Scout                     | pause for area-map review    | continue, record assumptions                           |
| Root overview             | pause for review             | continue after self-check                              |
| Governing route map       | pause for review             | continue unless placement confidence is LOW            |
| Route-local overview wave | pause after first wave       | continue after curator pass                            |
| Docs evidence pack        | ask when sources are missing | continue using approved sources; park missing evidence |
| Cross-repo boundary pack  | ask on LOW confidence        | continue only with HIGH/MEDIUM evidence; park LOW      |
| File onboarding wave      | pause after wave             | continue after curator pass                            |
| Handoff                   | present final                | present final and ask whether separate closeout should run |

### Automated uncertainty handling

```text
[HIGH]   -> may enter durable memory with evidence
[MEDIUM] -> may enter durable memory with careful wording and evidence
[LOW]    -> goes to Parking Lot / Open Questions / Handoff, not durable fact
```

### Hard stops in both modes

Stop and ask the developer when:

- memory root cannot be resolved
- multiple memory roots are plausible
- target repo is ambiguous
- source inventory cannot be reviewed before automation begins
- required source access is missing for a load-bearing evidence pass
- cross-repo settings are ambiguous for a required boundary
- adjacent repo branch mismatches for a required boundary
- docs and code contradict each other on a load-bearing behavior
- output would require updating a non-target repo
- a `[LOW]` claim would otherwise become durable fact

---

## Source Inventory Review Rule

Before asking the user for additional sources, the bootstrap orchestrator must present a reviewable source inventory.

The inventory must show:

1. sources discovered from the C-08 resolved `system/sources.md`
2. source categories
3. source locations
4. whether each source is readable or unavailable
5. what the orchestrator intends to use each source for
6. which sources will be ignored or treated as unavailable
7. which source categories appear missing or weak

Ask only after showing the inventory:

```text
Here is what I found and plan to treat as sources. Is this correct, and is anything missing?
```

Do not ask “do you have additional sources?” before showing what was found.

Write the reviewed result to:

```text
bootstrap/input-ledger.md
```

Use `templates/bootstrap-input-ledger-template.md`.

---

## Topology And Eligibility

Invoke `C-08-ar-coordination-context-resolver` before selecting bootstrap candidates or writing artifacts.

Use the resolved context to determine:

1. active memory root
2. onboarding storage mode
3. source path eligibility rules
4. file-type eligibility rules
5. `system/settings.md`
6. `system/settings.json` when present
7. `system/sources.md`
8. cross-repo allow rules
9. branch safeguards

Apply resolved `onboarding.pathRules` before selecting source paths. In shared settings, scoped rules such as `path: <repo-name>` define eligible source paths and file types for that repository. Storage is resolved separately from `onboarding.storage`.

In mixed workspaces, resolving one repo must not move neighboring repos onto a different memory root.

Bootstrap candidate selection is governed by the resolved `onboarding.pathRules` from `system/settings.json`. Starter settings should include a standard exclusion baseline so broad repo rules do not select generated, vendored, build-output, cache, IDE, local-machine, or downloaded metadata files unless the developer explicitly includes them for the run.

Recommended `settings.json` path-rule excludes:

```text
node_modules/**
vendor/**
dist/**
build/**
coverage/**
.cache/**
.pytest_cache/**
.venv/**
.idea/**
.vscode/**
.env
.env.*
**/generated/**
**/*.generated.*
**/*.Zone.Identifier
**/*:Zone.Identifier
```

Use the same settings baseline for fresh bootstrap and `existing-memory-slice-maintenance`. If a repository's resolved path rules are missing these common excludes, record that during source inventory review and prefer updating `settings.json` rather than relying on an agent-local filter.

---

## Artifact Paths

All paths below are relative to the C-08 resolved `onboarding_root`.

```text
overview.md
entities.md
bootstrap/
  STATE.md
  input-ledger.md
  scout-report.md
  coverage-plan.md
  governing-route-map.md
  areas/
    <area>.md
    <area>.brief.md
    <area>/
      structure.md
      interfaces.md
      patterns.md
      concerns.md
  overview-cards/
    <mirrored-source-route>.overview-card.md
  evidence/
    docs/
      <area-or-route>.docs-pack.md
    cross-repo/
      <area-or-route>.boundary-pack.md
  file-cards/
    <mirrored-source-path>.card.md
  waves/
    overview-wave-001.md
    onboarding-wave-001.md
  reviews/
    overview-wave-001.curator.md
    onboarding-wave-001.curator.md
  handoff.md
<mirrored-source-folder>/overview.md
<mirrored-source-file>.md
```

---

## Templates

Use explicit templates instead of inferring artifact shape from prior examples:

| Artifact                  | Template                                          |
| ------------------------- | ------------------------------------------------- |
| Root repo overview        | `templates/repo-overview-template.md`             |
| Bootstrap input ledger    | `templates/bootstrap-input-ledger-template.md`    |
| Bootstrap state           | `templates/bootstrap-state-template.md`           |
| Coverage plan             | `templates/coverage-plan-template.md`             |
| Governing route map       | `templates/governing-route-map-template.md`       |
| Route-local overview card | `templates/route-local-overview-card-template.md` |
| Route-local overview      | `templates/route-local-overview-template.md`      |
| Docs evidence pack        | `templates/docs-evidence-pack-template.md`        |
| Cross-repo boundary pack  | `templates/cross-repo-boundary-pack-template.md`  |
| File card                 | `templates/file-card-template.md`                 |
| Onboarding wave           | `templates/onboarding-wave-template.md`           |
| Curator review            | `templates/curator-review-template.md`            |
| Bootstrap handoff         | `templates/bootstrap-handoff-template.md`         |

---

## Citation And Reference Rules

`Docs References`, `Repo-Internal References`, and `Cross-Repo References` are explanation-first sections backed by citation tables when they carry behavioral or boundary context.

Required table columns:

```markdown
| Finding | Citations | Source Path |
| ------- | --------- | ----------- |
```

Rules:

1. `Finding` is a concise summary of what the cited lines establish.
2. `Citations` records exact line ranges, for example `L10-L18` or `L10-L18; L42-L47`.
3. `Docs References` uses canonical documentation links, even when a local mirror was read for line access.
4. `Repo-Internal References` uses same-repo source, test, config, generated artifact, or onboarding evidence.
5. `Cross-Repo References` uses workspace-relative links to adjacent repo code/onboarding or external boundary proof.
6. Never emit absolute filesystem paths in onboarding output.
7. Treat `system/sources.md`, search registries, embedding hits, and source lists as routing inputs only. Never cite them as proof.
8. If no relevant source exists, keep the section and record what was checked plus that no relevant evidence was found.

---

## Confidence Levels

All factual claims in scout reports, area reports, briefs, coverage plans, route maps, evidence packs, cards, waves, and curator reviews carry a confidence tag when they are not directly self-evident.

- **`[HIGH]`** — confirmed by developer, authoritative docs, or matched producer/consumer evidence across a boundary.
- **`[MEDIUM]`** — clear from code reading or repeated local patterns, but not externally confirmed.
- **`[LOW]`** — inferred, speculative, naming-based, config-only, partial, or uncertain.

Promotion rules:

| Confidence | Durable memory handling                            |
| ---------- | -------------------------------------------------- |
| `[HIGH]`   | may be stated as fact with evidence                |
| `[MEDIUM]` | may be stated carefully with source evidence       |
| `[LOW]`    | must be parked as unresolved; do not state as fact |

---

## Bootstrap State File

Every bootstrap produces and maintains:

```text
bootstrap/STATE.md
```

The state file is the first file read at the start of every bootstrap session and the last file updated at the end.

Use `templates/bootstrap-state-template.md`.

The state file tracks:

- current control mode
- current bootstrap mode
- topology and memory root
- source inventory status
- phase status
- areas
- governing routes
- route-local overview waves
- file onboarding waves
- decisions
- blockers
- parking lot
- deferred files
- next recommended action

---

## Phase 0 — Setup, Topology, Control Mode, And Source Intake

### 0.1 Resolve topology

Invoke `C-08-ar-coordination-context-resolver` with the target repo and optional topology/coordination-root hints.

Done when:

- memory root is known
- repo-relative source path eligibility is known
- onboarding storage is known
- `system/settings.md` and `system/settings.json` are resolved if present
- `system/sources.md` is resolved or its absence is recorded
- cross-repo rules and branch safeguards are known

### 0.2 Ask or record control mode

If `control-mode` was not supplied, present the two control modes and ask the user to choose `gated` or `automated`.

If `bootstrap-mode` was not supplied, ask for a mode or default to `safe-starter-memory` when the user is unsure.

Bootstrap modes:

| Mode                                  | Use when                                                                                 | Output target                                                                                                                |
| ------------------------------------- | ---------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `quick-orientation`                   | user wants repo understanding only                                                       | source inventory + scout + root overview                                                                                     |
| `safe-starter-memory`                 | default for most repos                                                                   | root overview + first route-local overview wave + first high-risk file wave                                                  |
| `cross-repo-focused`                  | repo participates in multi-repo flows                                                    | root overview + boundary packs + boundary route/file coverage                                                                |
| `domain-doc-focused`                  | behavior depends on domain docs                                                          | root overview + docs packs + docs-sensitive coverage                                                                         |
| `existing-memory-slice-maintenance`   | already-ledgered memory needs coverage or cleanup for an added, moved, deleted, or newly important route | source inventory delta + route overview/card or cleanup plan + evidence packs as needed + targeted file wave or removal list + curator review + handoff |
| `full-bootstrap`                      | mature/critical repo needs broad coverage                                                | full pass model in waves                                                                                                     |

### Existing-memory slice maintenance

Use `existing-memory-slice-maintenance` when the repository already has a usable memory layer and the work is structural rather than a single-file update.

Use it for:

1. a new package, module, feature area, or source route
2. a moved package or module route
3. a deleted package, module, feature area, or source route
4. a newly important source area that lacks a governing route-local overview
5. a stale route-local overview whose governed source slice changed meaning
6. many files appearing or disappearing together
7. targeted onboarding expansion where file-by-file maintenance would lose the structural context

This mode does not pretend the repository is blank. It starts from the existing root `overview.md`, `entities.md` when present, route-local overviews, verified file-level onboarding, bootstrap artifacts, and the source delta that triggered the maintenance.

Expansion outputs may include:

1. source inventory delta
2. route-local overview card
3. route-local overview
4. docs or cross-repo evidence packs where needed
5. file cards for load-bearing files
6. targeted onboarding wave
7. curator review
8. handoff

Cleanup outputs may include:

1. stale route assessment
2. cleanup or removal plan
3. affected route-local overview list
4. affected child file-level onboarding list
5. related bootstrap artifact list
6. preserved-history decision
7. curator review
8. handoff

C-05 remains the user-facing entry point for create/update onboarding requests. When C-05 detects that the change is route-level create, refresh, move, or delete work, it should route to this C-03 mode instead of flattening the work into independent file-level onboarding edits.

### 0.3 Present source inventory before asking for additions

Read the resolved `system/sources.md` and present a source inventory:

```markdown
## Source Inventory Review

### Sources I Plan To Use

| Source   | Category             | Location           | Status   | Planned Use                      |
| -------- | -------------------- | ------------------ | -------- | -------------------------------- |
| `<name>` | Domain Documentation | `<path/url label>` | readable | docs evidence packs for `<area>` |

### Sources I Will Not Use

| Source   | Category     | Reason                                         |
| -------- | ------------ | ---------------------------------------------- |
| `<name>` | `<category>` | unavailable / stale / unrelated / wrong branch |

### Missing Or Weak Source Categories

| Category             | Why It May Matter                                             |
| -------------------- | ------------------------------------------------------------- |
| Domain Documentation | area reports suggest business rules not fully visible in code |
```

Then ask:

1. Are these sources correct?
2. Should any source be excluded?
3. Are any important docs missing?
4. Are there Confluence pages, internal docs, vendor docs, schemas, protocol specs, or adjacent repos I should add?

### 0.4 Write input ledger and state

Write:

```text
bootstrap/input-ledger.md
bootstrap/STATE.md
```

Done when:

- source inventory has been presented and accepted or corrected by the user
- approved/excluded/user-added sources are recorded
- cross-repo context from settings is recorded
- control mode is recorded
- bootstrap mode is recorded
- hard stops for the run are recorded

---

## Phase 1 — Scout

### Goal

Map repository structure broadly without deep implementation analysis.

The scout is broad but shallow. It may read top-level structure, package manifests, build files, entrypoint hints, routing/config files, existing onboarding, approved source inventory, and allowed adjacent onboarding/code signals.

It must not read every file deeply.

### Procedure

1. Gather structural signals.
2. Build a tech profile.
3. Discover cross-repo signals allowed by topology.
4. Identify functional areas.
5. Assign every eligible top-level source path to one area or mark it excluded.
6. Prioritize areas.
7. Write `bootstrap/scout-report.md`.
8. In gated mode, ask the developer to confirm the area map.

### Scout report required sections

```markdown
# Scout Report — <repo>

## Tech Profile

## Functional Areas

## Cross-Repo Interface Map

## Out Of Scope

## Unresolved

## Developer Review Questions
```

Functional areas should include boundaries, priority, reason for priority, seed context, initial observations, and suggested deep-dive mode.

Done when:

- every top-level eligible source path is assigned to an area or excluded
- every area has boundaries and priority
- cross-repo hints are recorded, even if empty
- `STATE.md` is updated

---

## Phase 2 — Area Deep-Dives

### Goal

Produce confidence-tagged area knowledge that explains structure, interfaces, patterns, and concerns.

### Execution model

For each high-priority area, use up to four focused agents:

| Agent           | Focus                                     | Output                                 |
| --------------- | ----------------------------------------- | -------------------------------------- |
| Structure agent | architecture, modules, data flow, state   | `bootstrap/areas/<area>/structure.md`  |
| Interface agent | APIs, events, protocols, cross-repo hints | `bootstrap/areas/<area>/interfaces.md` |
| Pattern agent   | conventions, errors, tests, domain terms  | `bootstrap/areas/<area>/patterns.md`   |
| Concerns agent  | invariants, traps, fragility, security    | `bootstrap/areas/<area>/concerns.md`   |

For small areas, use one combined area agent instead.

### Sizing rules

|     Area size | Recommended model                    |
| ------------: | ------------------------------------ |
|  `< 10` files | inline or single area agent          |
| `10–25` files | single area agent or two lenses      |
| `25–75` files | specialized agents                   |
|   `75+` files | split area first or process subareas |

### Merge step

A merge agent reads only the section reports for one area and writes:

```text
bootstrap/areas/<area>.md
bootstrap/areas/<area>.brief.md
```

The merge preserves confidence tags, resolves duplicates, records contradictions, identifies key files, and suggests route-local overview candidates.

Done when:

- prioritized areas have area reports and briefs
- `[LOW]` claims remain unresolved or parked
- key files and possible governing routes are identified
- `STATE.md` is updated

---

## Phase 3 — Root Repo Overview Synthesis

### Goal

Create or refresh the root repo overview from the scout report and area briefs.

Output:

```text
overview.md
```

Use `templates/repo-overview-template.md`.

Root overview verification is route-based. Record `sourceRoute` as `<repo-root>` and fill both `lastVerifiedCommitHash` and `lastVerifiedCommitDate` from the source commit that the overview was checked against. C-02 later compares that recorded commit to `HEAD` across the whole repository route and also checks for local staged or unstaged changes.

The synthesis agent reads:

- `bootstrap/STATE.md`
- `bootstrap/input-ledger.md`
- `bootstrap/scout-report.md`
- area briefs
- existing `overview.md` if present
- specific full area reports only on demand

It does not load all raw source code or all full area reports upfront.

Required root overview sections:

```markdown
## What This Repo Is

## Architecture At A Glance

## Code Structure

## Functional Areas

## Cross-Repo References

## Build & Dev

## Key Invariants

## Glossary Terms

## Docs References

## What To Explore Next
```

Done when:

- root overview exists
- `STATE.md` records synthesis status
- gated mode review is complete or automated mode self-check passed

---

## Phase 4 — Bottom-Up Memory Build

### Goal

Convert approved area findings into durable route-local overviews and file-level onboarding through small, evidence-backed, reviewable waves.

Core rule:

```text
Build the next safe memory wave, not the whole memory layer at once.
```

### 4A — Coverage Planning

Write:

```text
bootstrap/coverage-plan.md
```

Use `templates/coverage-plan-template.md`.

Inputs:

- root `overview.md`
- `STATE.md`
- `scout-report.md`
- area reports and briefs
- concerns reports
- interface reports
- input ledger
- developer review notes

Classify files and routes:

| Classification           | Meaning                                  | Default action                              |
| ------------------------ | ---------------------------------------- | ------------------------------------------- |
| `landmine`               | hidden invariant / fragile logic         | early file card + wave                      |
| `cross-repo-boundary`    | can break another repo/system            | boundary pack + file card                   |
| `core-logic`             | central behavior                         | route overview and/or file card             |
| `entrypoint`             | user/system entry into area              | route overview and/or file card             |
| `domain-mapper`          | transforms domain state                  | route overview and file card if non-obvious |
| `state-machine`          | state transitions                        | route overview + early file card            |
| `security-sensitive`     | auth, validation, permissions            | early file card                             |
| `routine-support`        | helper with limited risk                 | defer unless touched                        |
| `simple-dto-config`      | passive data/config                      | defer unless touched                        |
| `deleted-route`          | source route disappeared                 | cleanup plan + affected memory list         |
| `moved-route`            | source route moved or was renamed        | move plan + overview/link update list       |
| `stale-onboarding-route` | route-local memory no longer matches code | refresh or retire plan                      |
| `generated-vendor-build` | generated/vendor/build output            | exclude                                     |
| `unknown`                | not enough evidence                      | investigate or ask                          |

Done when:

- priority routes and files are classified
- evidence needs are known
- deferred files have reasons and revisit triggers
- first overview wave candidates are known

### 4B — Governing Route Map

Write:

```text
bootstrap/governing-route-map.md
```

Use `templates/governing-route-map-template.md`.

This artifact decides where durable route-local `overview.md` files should live in the mirrored onboarding hierarchy.

Placement principles:

1. Place `overview.md` at the source route where the area begins.
2. Prefer locality over detached architecture folders.
3. Do not create an overview merely because a folder exists.
4. Create an overview when a subtree has shared models, workflows, repeated invariants, cross-repo boundaries, docs dependencies, multiple hotspots, or routing burden.
5. For cross-cutting workflows, choose the most natural local anchor and add local mentions in participating route overviews.

Done when:

- proposed governing routes are listed
- considered-but-deferred routes are recorded
- stale, moved, or deleted routes are recorded when running existing-memory slice maintenance
- cross-cutting concepts have primary local anchors
- gated mode review is complete or automated mode can proceed with non-LOW placements

### 4C — Route-Local Overview Cards

For each selected governing route, write:

```text
bootstrap/overview-cards/<mirrored-source-route>.overview-card.md
```

Use `templates/route-local-overview-card-template.md`.

Overview cards are work orders for route-local overview workers. They specify:

- why this overview exists
- what source subtree it governs
- what structures it must explain
- what files are load-bearing
- what docs or cross-repo packs apply
- what backlinks/downlinks are required
- what open questions remain

Done when:

- every first-wave route overview has a card
- cards list parent overview, child overview candidates, and governed files
- cards constrain workers enough to avoid broad repo re-discovery

### 4D — Route-Local Overview Waves

Create wave manifests under:

```text
bootstrap/waves/overview-wave-001.md
```

A wave should include a small number of overview cards.

Default wave sizing:

| Route type                          |  Max scope |
| ----------------------------------- | ---------: |
| high-risk boundary route            |    1 route |
| workflow-owning route               |    1 route |
| module route with multiple hotspots | 1–3 routes |
| routine directory                   |      defer |

Overview workers write durable route-local overviews:

```text
<mirrored-source-folder>/overview.md
```

Use `templates/route-local-overview-template.md`.

Route-local overview verification is also route-based. Record the governed repo-relative source route, then fill `lastVerifiedCommitHash` and `lastVerifiedCommitDate` from the source commit that the overview was checked against. A later change anywhere under that route is allowed to trigger C-02 drift even if the prose still turns out to be correct after review.

A route-local overview must include:

- parent overview backlink
- what belongs here / what does not
- structures found here
- operating model
- main flows
- load-bearing files
- local invariants and traps
- repo-internal references
- cross-repo references
- docs references
- file-level onboarding map
- child overviews
- how to use this area
- update history

Done when:

- all overview wave targets exist or have blockers
- the wave has a curator review
- `STATE.md` is updated

### 4E — Domain Documentation Evidence Pass

For each priority area or route where domain behavior, external protocol behavior, vendor/library behavior, or business rules matter, write:

```text
bootstrap/evidence/docs/<area-or-route>.docs-pack.md
```

Use `templates/docs-evidence-pack-template.md`.

Rules:

1. Use `system/sources.md` as a routing index only.
2. Use its `Domain Documentation` category as the required discovery path.
3. Cite actual documentation or local mirror evidence, not `system/sources.md`.
4. If no relevant documentation exists, record what was checked.
5. Embedding hits are pointers only; open and cite the source document.

Done when:

- each docs-dependent priority route has a docs pack or explicit no-evidence record
- every docs finding points to direct evidence

### 4F — Cross-Repo Boundary Pass

For each priority route with inbound or outbound cross-repo signals, write:

```text
bootstrap/evidence/cross-repo/<area-or-route>.boundary-pack.md
```

Use `templates/cross-repo-boundary-pack-template.md`.

Rules:

1. Read adjacent repos only when topology and branch safeguards allow it.
2. Treat adjacent repos as read-only context.
3. Record real system-boundary evidence only.
4. Same-repo implementation facts belong in repo-internal references, not boundary packs.
5. Naming-only ties are `[LOW]` and must not become durable fact.

Done when:

- confirmed boundaries are listed with confidence
- branch/topology notes are recorded
- LOW-confidence ties are queued for review or handoff

### 4G — File Card Generation

For each priority source file, write:

```text
bootstrap/file-cards/<mirrored-source-path>.card.md
```

Use `templates/file-card-template.md`.

A file card must include:

- classification and priority
- why the file matters
- governing overview path
- ancestor overviews
- evidence packs to read
- files the worker may read
- files the worker must not read without escalation
- required file onboarding sections
- known traps
- open questions

Do not assign file-level onboarding work without a file card unless the repo is tiny.

### 4H — File-Level Onboarding Waves

Create wave manifests under:

```text
bootstrap/waves/onboarding-wave-001.md
```

Use `templates/onboarding-wave-template.md`.

Each file worker receives one file card and follows `C-05-create-or-update-onboarding-files`.

Default wave sizing:

| File type                 |            Max scope |
| ------------------------- | -------------------: |
| landmine file             |            1–2 files |
| cross-repo boundary file  |            1–2 files |
| core logic cluster        |            2–3 files |
| mapper cluster            |            3–5 files |
| DTO/config/simple utility | defer unless touched |

File onboarding workers must:

1. read the file card first
2. read the nearest governing overview and listed ancestor overviews
3. read only listed source/evidence files unless escalating
4. keep file-level onboarding self-sufficient
5. add or update `governingOverview`
6. keep planning notes out of durable onboarding
7. preserve strict 1-to-1 source mapping

### 4I — Curator Review

After each overview or onboarding wave, write:

```text
bootstrap/reviews/<wave-name>.curator.md
```

Use `templates/curator-review-template.md`.

The curator checks:

- strict 1-to-1 mapping for file onboarding
- route-local overview placement is mirrored and local
- file onboarding backlinks to nearest governing overview
- overview downlinks list governed files
- no task-local planning in durable memory
- docs references cite actual evidence
- repo-internal and cross-repo references use correct buckets
- no source registry or embedding hit is cited as proof
- no absolute filesystem paths
- update history is append-only
- LOW-confidence claims are not stated as facts
- deferred files are recorded
- `STATE.md` is updated

### 4J — Developer Review And Next-Wave Decision

In gated mode, present each curator result and changed durable files to the developer.

Ask only high-value questions:

1. Do these overviews/onboarding files match your understanding?
2. Are any invariants missing?
3. Are any compatibility notes actually obsolete code or stale docs?
4. Are any `[LOW]` claims confirmable or false?
5. Should the next wave continue with the current priority order?

In automated mode, record the same questions in `STATE.md` and `bootstrap/handoff.md` unless they are hard blockers.

---

## Phase 5 — Handoff

When pausing or completing bootstrap, write:

```text
bootstrap/handoff.md
```

Use `templates/bootstrap-handoff-template.md`.

The handoff lists:

- control mode and bootstrap mode
- trusted coverage
- route-local overviews created
- file onboarding created
- route-local overviews and file onboarding removed, moved, or retired
- evidence packs created
- deferred coverage
- open questions
- known risks
- completed waves and curator results
- recommended next waves
- how future agents should use the bootstrap artifacts

Automated bootstrap stops at this handoff/review boundary. After presenting the handoff, ask whether a separate closeout should run.

---

## Guided Mode Defaults

If the developer does not know how to choose areas or depth, use `safe-starter-memory`:

1. Resolve topology.
2. Present source inventory and write input ledger.
3. Run scout.
4. Pick the top 3 high-risk areas by cross-repo coupling, entrypoints, command/control paths, state-machine logic, hidden invariants, and concerns reports.
5. Run area deep-dives for those areas.
6. Build or refresh root overview.
7. Create coverage plan.
8. Create governing route map.
9. Create route-local overview cards for the highest-value routes.
10. Build overview wave 1.
11. Run docs evidence only where domain docs matter.
12. Run cross-repo boundary pass only where signals exist.
13. Create file cards only for high and medium priority files.
14. Build onboarding wave 1 with no more than 5 file cards.
15. Run curator review.
16. Handoff or ask before wave 2 depending on control mode.

If the selected mode is `existing-memory-slice-maintenance`:

1. Resolve topology and read existing root, entity, route-local, and file-level onboarding for the affected slice.
2. Present the source inventory delta and write input ledger after the user accepts or corrects it.
3. Confirm the resolved `settings.json` path rules include the standard excludes before selecting added, moved, deleted, or refreshed paths.
4. Classify the route as expansion, move, cleanup, refresh, or defer.
5. Create or update the coverage plan and governing route map for that slice only.
6. For expansion or refresh, produce route-local overview cards, route-local overviews, evidence packs, file cards, targeted onboarding waves, curator review, and handoff as needed.
7. For move or deletion, produce a cleanup plan, affected onboarding list, preserved-history decision, curator review, and handoff.
8. Ask whether separate closeout should run after handoff.

Progress display for non-expert users:

```markdown
## Bootstrap Progress

| Step                   | Status      | What It Means                                        |
| ---------------------- | ----------- | ---------------------------------------------------- |
| Resolve setup          | done        | The system knows where memory lives                  |
| Review sources         | done        | The source universe is explicit                      |
| Map repo               | done        | The system knows the main parts of the repo          |
| Research risky areas   | in progress | Agents are checking places most likely to break      |
| Build root overview    | pending     | A repo guide will be created                         |
| Place local overviews  | pending     | Local construction pillars will be created           |
| Create first file wave | pending     | High-risk files get direct onboarding                |
| Review                 | pending     | Curator checks prevent bad memory from being trusted |
```

---

## Implementation Guards

Only guard against likely or proven bad behavior.

1. **Do not create detached durable area overviews by default.** Use route-local `overview.md` files in the mirrored onboarding hierarchy.
2. **Do not let overview files replace file onboarding.** File docs remain self-sufficient and backlink to governing overviews.
3. **Do not generate markdown for every file.** Full coverage means every file is covered, deferred, or excluded — not that every file has a file doc.
4. **Do not create an overview for every folder.** Create one only when the subtree has shared model, workflow, invariants, boundaries, docs dependencies, hotspots, or routing burden.
5. **Do not cite `system/sources.md`, search registries, or embedding hits as evidence.** They are routing inputs only.
6. **Do not update adjacent repo memory during target repo bootstrap.** Cross-repo context is read-only unless a separate task targets that repo.
7. **Do not preserve stale code or stale docs under vague compatibility language.** Compatibility requires evidence of an active consumer, migration requirement, external contract, supported version boundary, or developer-confirmed constraint.
8. **Do not let automated mode skip review artifacts.** Automated mode changes pause behavior, not artifact quality.
9. **Do not ask for additional sources before showing found sources.** Source intake must be reviewable.
10. **Do not promote `[LOW]` claims to durable fact.** Park them in state, handoff, or open questions.
11. **Do not add an extra repo-name folder under the resolved `onboarding_root`.** The resolver already returns the target repo's onboarding root.
12. **Do not run closeout as part of automated bootstrap.** Handoff is the automation boundary; closeout requires a separate approval.
13. **Do not flatten route-level maintenance into unrelated file-level edits.** Use `existing-memory-slice-maintenance` when a slice is added, moved, deleted, or structurally refreshed.

---

## Multi-Agent Execution Model

```text
Orchestrator
  -> resolves topology and control mode
  -> presents source inventory
  -> maintains STATE.md
  -> dispatches scoped workers
  -> reads only distilled artifacts

Scout agent
  -> reads broad repo structure and approved source inventory
  -> writes scout-report.md

Area lens agents
  -> read one area only
  -> write structure/interfaces/patterns/concerns reports

Area merge agent
  -> reads area section reports
  -> writes area report and brief

Synthesis agent
  -> reads scout + area briefs + state
  -> writes root overview.md

Coverage planner
  -> reads overview + area artifacts
  -> writes coverage-plan.md

Route mapper
  -> writes governing-route-map.md

Overview card/worker agents
  -> write route-local overview cards and route-local overview.md files

Docs librarian
  -> writes docs evidence packs from approved documentation sources

Boundary mapper
  -> writes cross-repo boundary packs from allowed adjacent context

File card/file worker agents
  -> write file cards and invoke/follow C-05 for file onboarding

Curator
  -> validates each wave and writes curator review
```

The orchestrator remains thin throughout.

---

## When To Use This Skill

| Situation                                                        | Use this skill?                            |
| ---------------------------------------------------------------- | ------------------------------------------ |
| New repo added to workspace, zero onboarding                     | yes                                        |
| Repo has placeholder overview and needs real content             | yes                                        |
| Task will touch an un-bootstrapped area                          | yes, `existing-memory-slice-maintenance` when repo memory already exists |
| Repo root overview exists but agents still get lost in a subtree | yes, route-local overview wave             |
| New package/module/source route appears in an already-ledgered repo | yes, `existing-memory-slice-maintenance` |
| Package/module/source route disappears or moves                  | yes, `existing-memory-slice-maintenance` cleanup or move handling |
| Cross-repo boundary is poorly understood                         | yes, cross-repo-focused mode               |
| Domain docs influence code behavior                              | yes, domain-doc-focused mode               |
| Repo is already well bootstrapped and one file changed           | no, use C-05 directly                      |
| Small script repo with a few files                               | probably not; write root overview directly |

---

## Relationship To Other Skills

| Skill                                             | Relationship                                                                                                  |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `C-08-ar-coordination-context-resolver`           | Required first step. Resolves memory root, settings, sources, path rules, storage, and cross-repo policy.     |
| `C-05-create-or-update-onboarding-files`          | Owns final file-level onboarding semantics and routes structural slice maintenance back to C-03. C-03 creates cards/waves and delegates file output rules to C-05. |
| `C-04-onboarding-read-mode`                       | Consumes bootstrapped overviews and file maps as the source-reading protocol for existing onboarding.          |
| `C-02-onboarding-drift-detection`                 | Becomes relevant after bootstrap; touched files can be promoted from deferred to covered.                     |
| `W-01-heavy-task-workflow`                        | May trigger targeted bootstrap when an active task enters an uncovered area.                                  |
| `confluence-search` / documentation search skills | Feed the docs evidence pass through approved sources from the input ledger.                                   |

---

## Acceptance Criteria

This skill implementation is successful when:

1. the minimum bootstrap still produces a useful root `overview.md`
2. source inventory is presented before asking for additions
3. gated and automated control modes are explicit
4. automated mode still writes review artifacts and curator reviews
5. broad Phase 4 deepening is replaced with bottom-up passes
6. route-local overviews are created in the mirrored onboarding hierarchy
7. file-level onboarding remains self-sufficient and backlinks to governing overview
8. docs evidence and cross-repo boundary evidence are separate passes
9. file cards constrain file workers before C-05 is invoked
10. curator review validates both overview waves and file onboarding waves
11. current thin-orchestrator, confidence-tag, checkpoint, topology, and cross-repo read-only behavior is preserved
12. guards prevent the likely bad behaviors without adding compatibility scaffolding for alpha-era labels
13. existing-memory slice maintenance can create, refresh, move, or clean up route-local memory without treating the repo as blank
14. automated bootstrap ends at handoff and asks whether separate closeout should run
