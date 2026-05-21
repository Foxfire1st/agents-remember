# Task: C-04 Trusted Context Router

**Status:** closeout in progress
**Repo:** agents-remember-md
**Type:** Skill | Script | Config | Docs
**Created:** 2026-05-19T13:05

---

## Objective

Redesign C-04 from an onboarding-read protocol into a trusted context routing skill. The skill should route agents across optional discovery providers such as GrepAI, CodeGraphContext, deferred Graphify support, and future tools while preserving Agents Remember as the verification, drift, branch-validity, source-proof, and memory-promotion layer.

## Closeout Snapshot

- S1 is implemented in source settings/reference docs and the installed runtime: `contextProviders` can express one GrepAI memory provider and one `codegraphcontext-code` setting whose `roots` array expands into multiple CodeGraphContext code-repo runtime instances.
- S2 is in progress in `provider-evaluation.md`: GrepAI is running for `ar-coordination/memory-repos`; CodeGraphContext now runs against the lifecycle-owned browser-capable FalkorDB Docker backend and has clean indexes for `agents-remember-md`, `resolve_auto_editor`, and `tensorflow`.
- Provider lifecycle source now expands `codegraphcontext-code.roots[]` through the default coordinator `system/settings.json`, emits FalkorDB remote process env, starts/statuses the shared Docker backend, and exposes per-root plus all-root watcher lifecycle commands.
- Provider lifecycle now treats stale generated CGC runtime instances and legacy embedded-backend artifacts as reconciliation targets. Example placeholders must not become live provider folders, and root paths must resolve to existing code repositories before runtime state is created.
- CGC provider requirements now explicitly pin Tree-Sitter parser dependencies. Without those pins, Python 3.13 installs `codegraphcontext==0.4.10` without parsers and can produce a technically successful but file-only graph.
- The `ar-agents-remember-md` memory repo path rules now include `.txt` and `.patch` so provider requirement and patch assets are first-class onboarding-covered source files.

---

## Conceptual Model

C-04 is a retrieval strategy router over memory substrates. It should teach the model to satisfy its current intent efficiently by choosing the substrate whose retrieval shape matches the question, then moving between substrates when the next missing piece changes.

The working triangle:

- `Semantics`: use when the concept is known but the structure or location is unknown. Default substrate: GrepAI over memory repos.
- `Relationship`: use when an anchor is known but surrounding relationships, impact paths, callers, callees, or dependencies are unknown. Default substrate: CodeGraphContext over code repos.
- `Intent`: use when an anchor or location is known but the hidden contracts, invariants, branch-valid truths, behavioral expectations, or code intent are unknown. Default substrate: onboarding plus source confirmation.

The router should select a retrieval contract before selecting a provider. It should ask what context bundle the agent needs for the work, then choose the substrate and retrieval strategy that can deliver that bundle with the least rediscovery. Providers are primitives that serve the contract; they are not competing sources of truth.

Substrate navigation can be sequential. A triage task may start with anchors from the ticket, use the Relationship substrate to discover surrounding structure, then move to the Intent substrate to understand hidden contracts and code truths before proposing a next step.

## Industry Retrieval Context

The following table captures the developer-provided transcript framing from the Pinecone/Page Index/SAP/GraphRAG video. These notes are context for the retrieval-substrate direction, not independently verified current-market claims in this task.

| Company / product               | Strategy described in transcript                                                                                                                                         | Retrieval shape lesson for Agents Remember                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Pinecone / Nexus / NoQL         | Vector search alone is not enough for agents. Retrieval should carry intent, filters, access policy, provenance, response shape, confidence, and budget.                 | C-04 should route by retrieval contract and returned bundle shape, not just "similar text."                                  |
| Page Index                      | Some documents should not be chunked because hierarchy carries meaning. Retrieval walks a document tree with summaries instead of flattening everything into embeddings. | Path-derived onboarding and route overviews are structural memory; preserve structure when structure controls meaning.       |
| SAP / Dremio                    | Enterprise agents need governed access to business data across systems, with semantic layers, federation, permissions, and lineage.                                      | Agents Remember should keep source proof, branch validity, and provenance as first-class controls around any provider.       |
| SAP / Prior Labs / TabPFN       | Tabular knowledge should be reasoned over as tables, not flattened into prose for an LLM.                                                                                | Retrieval substrates should match the native shape of the knowledge; do not force every memory type into text/vector search. |
| Microsoft / GraphRAG            | Some tasks are naturally relational: shared incidents, dependencies, root causes, entity neighborhoods, and impact paths.                                                | Relationship retrieval belongs in a graph/code-relationship substrate such as CGC, with source verification after discovery. |
| Google / knowledge architecture | Knowledge architecture is becoming a headline infrastructure concern for AI systems.                                                                                     | The durable design problem is memory architecture, not one provider choice.                                                  |
| Cloudflare agent memory         | Agent memory is becoming infrastructure, not just an application feature.                                                                                                | Agents Remember should remain the trust/control layer that coordinates memory, providers, drift, and promotion.              |
| Chroma / context rot research   | Larger context windows do not solve retrieval if the context is cluttered or poorly shaped.                                                                              | The goal is appropriate context, not maximum context; C-04 should reduce rediscovery and avoid context dumping.              |

Summary principle: define the retrieval contract and the context bundle the agent needs before choosing a database or provider. The provider is a primitive for delivering a substrate-shaped bundle, not the memory strategy itself.

---

## Requirements

- Models are instructed to use a discovery provider based on what they need to know.
- C-04's core routing rule is: use the Semantics substrate when the concept is known but structure/location is unknown; use the Relationship substrate when anchors are known but relationships are unknown; use the Intent substrate when anchors/locations are known but hidden contracts and code truths are unknown.
- The initial C-04 change is a router-behavior test, not the full provider lifecycle platform. Deeper lifecycle, task-strategy, provider-budget, and proof-promotion details may be deferred until the router is tested with real model behavior.
- Discovery providers are part of three categories: relationship (e.g. CodeGraphContext, deferred Graphify support), semantic (e.g. GrepAI), and path-based truth layer (onboarding).
- Agents Remember must be positioned as complementary to specialized retrieval/index providers, not as a competitor to them.
- Retrieval/index providers may reduce discovery cost, token pressure, and search friction, but the implementation must not promise wall-time speedups as the main value claim.
- Provider results are candidate-routing evidence only; source files, verified onboarding, drift checks, branch validity, and route indexes remain the proof layer.
- GrepAI, Graphify, and future tools must be optional provider profiles/instruction surfaces, not hard dependencies. Skipping them should degrade gracefully to onboarding-only routing like it is
  today. But skipping will increase retrieval costs for the user.
- The first implementation should avoid API-normalization adapters that wrap provider result formats; agents should be instructed which provider to use for which question shape and should form provider-native queries/results within the normal tool-use loop.
- Provider installation must be opt-in and explain the advantage in user-friendly language. If agreed system needs to take care of installation.
- Provider freshness must be workflow-managed; users should not need to remember random watchers.
- Before implementation, GrepAI and the selected relationship provider installation, daemon/watch behavior, index storage, re-ingestion commands, stale-index failure modes, and hook/refresh points must be mapped so provider lifecycle does not clash with Agents Remember's operational model.
- When a settings file enables a GrepAI provider, the system must check that the GrepAI watcher is running for the configured root before relying on GrepAI results. This should become an always-on installed-runtime rule, either in `AGENTS.md` or the resolved `system/*` guidance.
- GrepAI's managed memory provider should run at the `ar-coordination/memory-repos` root so one watcher can cover multiple memory repos.
- Graphify provider artifacts must live under the coordination runtime, for example `ar-coordination/providers/graphify/<repo>/`, not inside code repositories or memory repos. If Graphify cannot build, update, query, and serve from a configured external artifact root, it is not acceptable as an Agents Remember managed provider.
- Graphify managed mode must be driven by the Agents Remember provider lifecycle manager, not by Graphify's own agent installers or git hooks. The lifecycle manager must run with a coordination-owned working directory, set `GRAPHIFY_OUT` to an absolute coordination-runtime artifact path, pass `--out` or `--graph` explicitly where supported, and disable or sandbox Graphify features that still assume repo-local `graphify-out`.
- CodeGraphContext should be evaluated as the preferred Graphify replacement for the relationship provider role because it offers native CLI/MCP relationship queries, watch/index/delete/doctor lifecycle commands, and configurable database backends.
- CodeGraphContext lifecycle checks must distinguish between its long-lived components: `cgc watch` is a blocking foreground watcher process, `cgc mcp start` is a long-lived MCP server that can own watcher state internally, and the managed database backend is a lifecycle-owned FalkorDB Docker DBMS.
- CodeGraphContext should be managed per configured code repository root, with one runtime root and one supervised ingestion process per expanded `codegraphcontext-code.roots[]` entry. Several code repos are supported by one `codegraphcontext-code` provider setting whose roots are keyed by stable repo id and resolved C-08 code root.
- A foreground watcher is acceptable when Agents Remember owns the process lifecycle. The watcher must self-update on file changes, and the lifecycle manager must also expose a hard refresh path that rebuilds the provider index from source.
- GrepAI and CodeGraphContext must both be queryable through CLI commands. MCP may be supported as an ergonomic alternate transport, but token economy is governed by returned result size, call count, and evidence selection rather than by CLI vs MCP alone.
- A small provider lifecycle manager is the minimum programmatic integration: status, start, stop, refresh, and doctor checks for configured providers.
- C-03 and C-05 must notify or refresh provider indexes after onboarding updates.
- GrepAI should be the default semantic provider for the memory layer when configured.
- CodeGraphContext should be the default relationship provider for code repos when configured. Graphify remains deferred fallback/research until its artifact and hook behavior is less costly to contain.
- Provider dependencies must be pinned and installed into coordination-owned provider environments, not user-global Python environments.
- Provider virtual environments should be shared per provider type under `<coordination_root>/providers/_venvs/<provider>/`, not shared across unrelated providers. This keeps installs reusable across repo instances while avoiding dependency collisions between provider CLIs.
- Provider dependency pins should live under `<coordination_root>/providers/requirements/<provider>.txt`; CGC should start pinned to the evaluated version and include parser dependencies before any monkey patch is applied.
- CGC's pinned dependency set must include the parser dependencies needed for symbol extraction, not only the top-level `codegraphcontext` package. A successful install that lacks Tree-Sitter support is not acceptable because it can index files while producing no functions/classes/modules.
- Provider patches should live under `<coordination_root>/providers/patches/<provider>/` and be applied idempotently by lifecycle tooling. Patched provider state should record provider version, patch identity, and patch verification in the provider runtime state.
- Provider runtime scaffolding must live under `<coordination_root>/providers/<provider>/<instance-id>/` and must be disposable on reinstall. Durable provider databases live under `<coordination_root>/provider-data/`. For CGC, per-repo runtime artifacts live under `<coordination_root>/providers/codegraphcontext/<repo-id>/.codegraphcontext/`, while shared FalkorDB DBMS data lives under `<coordination_root>/provider-data/codegraphcontext/falkordb/`.
- CGC is not acceptable as a managed provider until repo-local `.cgcignore` creation is patched or fixed upstream. The provider may read indexed source repositories, but it must not create `.cgcignore`, `.codegraphcontext`, reports, database files, logs, or other provider artifacts inside code repositories.
- CGC upstream issue candidate: request an explicit `.cgcignore` path/config option that is honored before repo-local discovery/creation so externally managed indexes can keep ignore files under their runtime root.
- The implementation must preserve C-08 context resolution, C-02 drift discipline, W-02 approval gates, and C-05 onboarding propagation.
- Existing route indexes and `overview.index.json` remain first-class context inputs.

---

## Implementation Steps

### S1 — Finalize provider contract and settings shape

- Update the machine-readable settings examples and reference docs for `contextProviders`.
- Define provider fields for `type`, `scope`, `roots`, `runtimeRoot`, `instanceRootTemplate`, `stateFileTemplate`, `backend`, `processEnvTemplate`, `watch`, `freshness`, and `transportPolicy`.
- Keep settings declarative: settings say which providers exist and where they live; the lifecycle manager owns process decisions.
- Preserve existing settings compatibility when `contextProviders` is absent.
- Target files:
  - `runtime/system/defaults/examples/coordinator/settings.json`
  - `runtime/system/defaults/examples/coordinator/settings.md`
  - `runtime/system/defaults/examples/coordinator/tools.md`
  - `docs/reference/settings-json.md`

Completion gate: a user can express one GrepAI memory provider and multiple CGC code providers without changing code or memory repo contents.

### S2 — Run provider quality and volume evaluation before deeper integration

- Evaluate GrepAI against the current `ar-coordination/memory-repos` index.
- Install/evaluate CodeGraphContext only after opt-in consent, then index at least two code repos if available: the active `agents-remember-md` repo and one additional sibling code repo.
- Store all test artifacts under the active task folder, for example `provider-evaluation.md`, with no provider artifacts in code repos.
- Treat artifact containment as part of provider quality. Record whether a provider mutates the indexed source repo, writes hidden files, writes reports, creates global config, or requires cleanup.
- Pause full TensorFlow CGC indexing until the CGC runtime-root layout and `.cgcignore` mutation behavior are patched or explicitly accepted.
- Record indexing wall time, shutdown warnings, query latency, concurrency behavior, and database lock behavior. CGC's first `agents-remember-md` spike showed that relationship quality cannot be judged separately from lifecycle cost.
- Use CLI first for repeatability; use MCP only if already mounted and bounded.
- For each query, record result volume and quality before treating the provider as useful.
- Compare provider output against source/onboarding truth for a small sample, because provider output is candidate routing only.
- Include at least one chained substrate test, such as a triage-shaped prompt that starts with ticket anchors, uses Relationship retrieval for surrounding structure, then uses Intent retrieval for hidden contracts/code truths.

Evaluation table format:

| Provider | Scope/root                         | Transport | Query / command shape                                  | Returned volume   | Summary of returned candidates            | Verification sample        | Quality/quantity judgement             | Design consequence                         |
| -------- | ---------------------------------- | --------- | ------------------------------------------------------ | ----------------- | ----------------------------------------- | -------------------------- | -------------------------------------- | ------------------------------------------ |
| GrepAI   | `<coordination_root>/memory-repos` | CLI       | Semantic route query for a known onboarding concept    | Lines/items/chars | Which memory routes/files came back       | Source/onboarding checked? | Good/noisy/sparse, too much/too little | Keep, adjust query guidance, or cap harder |
| GrepAI   | `<coordination_root>/memory-repos` | CLI       | Ambiguous semantic query that should not overclaim     | Lines/items/chars | Candidate routes and confidence shape     | Source/onboarding checked? | Good/noisy/sparse, too much/too little | Update C-04 fallback rule                  |
| CGC      | `<code_repo_root>`                 | CLI       | Caller/callee query for a known function/class         | Lines/items/chars | Relationship candidates returned          | Source checked?            | Good/noisy/sparse, too much/too little | Keep command as supported probe or avoid   |
| CGC      | `<code_repo_root>`                 | CLI       | Dependency/impact query across modules                 | Lines/items/chars | Candidate impact path(s) returned         | Source checked?            | Good/noisy/sparse, too much/too little | Keep, cap depth, or require source-first   |
| CGC      | `<code_repo_root>`                 | CLI       | Negative or low-signal query                           | Lines/items/chars | Whether it fails quietly or floods output | Source checked?            | Good/noisy/sparse, too much/too little | Add guardrail or avoid command             |
| CGC      | `<provider_runtime_root>`          | CLI       | Containment probe                                      | Files/paths       | Artifacts created by install/index/query  | Source repo dirtied?       | Clean/patchable/unacceptable           | Accept, patch, upstream issue, or defer    |
| Chained  | memory + code                      | CLI       | Triage-shaped task: anchors -> relationships -> intent | Lines/items/chars | Bundle assembled across substrates        | Source/onboarding checked? | Coherent, excessive, missing contract? | Adjust router sequence or substrate names  |

Completion gate: the task has a written judgement about whether GrepAI and CGC produce useful candidate routes at acceptable output volume and acceptable lifecycle cost, plus specific query limits for C-04. CGC cannot pass this gate until source-repo `.cgcignore` mutation is patched, accepted as a deliberate repo config choice, or fixed upstream.

### S3 — Implement provider lifecycle manager

- Add a small shared provider manager surface rather than provider-specific adapters that normalize query outputs.
- Provide `status`, `start`, `stop`, `refresh`, and `doctor` for each configured provider instance.
- Track runtime state under `<coordination_root>/providers/<provider>/<id>/`, including logs, PID/process metadata, last status, and last refresh.
- Install provider dependencies from `<coordination_root>/providers/requirements/<provider>.txt` into `<coordination_root>/providers/_venvs/<provider>/`.
- Apply provider patches from `<coordination_root>/providers/patches/<provider>/` after installation and before provider use. Patches must be idempotent and version-checked.
- Write `provider-state.json` under each provider instance runtime root with provider version, venv path, requirements file hash, applied patch ids, last doctor result, last refresh, and artifact-containment status.
- GrepAI implementation:
  - status: run from configured root with `grepai status --no-ui` and `grepai watch --status`
  - start: `grepai watch --background --log-dir <runtimeRoot>/logs`
  - stop: `grepai watch --stop`
  - refresh: stop/start managed watcher, with destructive `.grepai` rebuild only as explicit doctor remediation
  - doctor: command availability, root `.grepai`, index stats, watcher state, log path
- CGC implementation:
  - settings input: read `contextProviders.providers.codegraphcontext-code` from coordinator settings as the primary managed configuration; expand every `roots[]` entry into a per-repo runtime instance using `repoId`, `path`, optional root-level `cgcignorePatterns`, `runtimeRoot`, `instanceRootTemplate`, `stateFileTemplate`, `processEnvTemplate`, and the resolved backend state
  - debug overrides: keep manual `--repo-id` / `--code-repo-root` style flags only as explicit debug/development overrides, not as the main install/start/status path
  - status: command availability, runtime paths, provider process state, `cgc doctor`, `cgc list`, and `cgc stats <code_repo_root>`
  - install: create or reuse `<coordination_root>/providers/_venvs/codegraphcontext/`, install the pinned requirements file, apply the required `.cgcignore` runtime-root and Windows hard-refresh delete-prefix patches, and verify the patched behavior before indexing
  - database backend: use FalkorDB Docker with browser UI as the only managed CGC backend
  - docker preflight: verify Docker CLI availability, daemon availability, architecture support, image pin/digest, free loopback ports, and writable persistent data root before enabling the backend
  - docker install/start: pull the pinned `falkordb/falkordb` image with browser support, create one lifecycle-owned container per coordination root, bind Redis/FalkorDB only to `127.0.0.1:<allocated-port>`, bind browser UI to `127.0.0.1:<allocated-browser-port>`, and mount persistent DB data under the coordination provider root or a named volume recorded in provider state
  - docker status: report container id/name, image digest, port bindings, data volume/path, browser URL, `FALKORDB_HOST`/`FALKORDB_PORT`, graph namespace strategy, and a real client health query
  - runtime layout: use `<coordination_root>/providers/codegraphcontext/<repo-id>/.codegraphcontext/` for per-repo `.env`, `config.yaml`, `.cgcignore`, logs, watcher state, and state; write provider defaults, the source repo's top-level `.gitignore` patterns, and root-specific `cgcignorePatterns` into the managed `.cgcignore`; keep shared FalkorDB Docker DBMS data under a coordination-owned shared CGC backend path rather than inside reinstallable venvs or source repos
  - start: `cgc start` and `cgc start-all` must read `<coordination_root>/system/settings.json`, fan out over every configured root, and start one supervised foreground `cgc watch <code_repo_root>` or managed `cgc mcp start` per repo instance; `cgc start --repo-id <repoId>` starts one selected root
  - stop: `cgc stop`, `cgc stop-all`, and `cgc shutdown-all` must read `<coordination_root>/system/settings.json`, fan out over every configured root, and terminate each lifecycle-manager-owned process; `cgc stop --repo-id <repoId>` stops one selected root
  - refresh: prefer watcher-backed incremental reconciliation when possible; use `cgc index <code_repo_root> --force` only as an explicit hard rebuild for that repo scope, never as an implicit reinstall side effect
  - purge-db: explicit destructive action only; may delete a repo index, graph namespace, FalkorDB volume, or whole DBMS data path only when the user requested destructive cleanup
  - doctor: path containment, env resolution, backend availability, patch verification, no source-repo artifact creation, query sanity check
- Likely target files:
  - new shared module under `runtime/skills/U-01-core-skills/_shared/agents_remember/`
  - optional script under `runtime/scripts/` for manual provider lifecycle commands

Completion gate: provider lifecycle commands can be dry-run and can report actionable status without relying on a model to remember shell incantations.

### S4 — Rewrite C-04 as Trusted Context Router

- Replace the old C-04 onboarding-read behavior with a provider-aware routing protocol while preserving onboarding-only fallback.
- Rename the skill and physical folder to `C-04-retrieval-strategy-router`. This describes the long-term job more precisely than `context-router`: choose a retrieval strategy based on model intent and memory substrate.
- Update all skill references in runtime docs, installed guidance, onboarding sidecars, and generated indexes in the same change.
- Add substrate selection rules:
  - Semantics: concept known, structure/location unknown -> query GrepAI over memory when configured and healthy
  - Relationship: anchors known, relationships/impact unknown -> query CodeGraphContext over code when configured and healthy
  - Intent: anchors/locations known, hidden contracts or code truths unknown -> use onboarding, route overviews, sidecars, and bounded source confirmation
  - Sequential routing: after one substrate produces useful anchors, move to the next substrate that matches the new missing piece
  - provider unavailable, stale, noisy, or unhelpful: degrade to onboarding-only discovery and bounded source search
- Target file:
  - `runtime/skills/U-01-core-skills/C-04-retrieval-strategy-router/SKILL.md`

Completion gate: an agent can read C-04 and know when to query GrepAI, when to query CGC, when to ignore a provider, and how to prove the final claim.

Proposed front matter for the initial router test:

```yaml
---
name: c-04-retrieval-strategy-router
description: "Choose retrieval strategies across memory substrates: semantics for known concepts with unknown structure, relationships for known anchors with unknown connections, and intent for hidden contracts and code truths."
---
```

#### Deferred until needed:

- Retrieval strategies for different task types e.g. triage, code questions, ... Strategies themselfs would need to be defined.
- final proof: use onboarding/source regardless of provider output
- Make provider output a candidate packet input, not an answer.
- Add query budgets from S2: maximum provider calls, maximum returned lines/items, and fallback behavior when output is noisy or stale.

### S5 — Encode always-on provider rules in installed runtime guidance

- Update installed `AGENTS.md` or system guidance so provider checks happen before reliance.
- Required rule: if settings enables GrepAI, verify the watcher/index for the configured root before using GrepAI output.
- Required rule: if settings enables CGC, verify provider status for the specific code repo before using CGC output.
- Make unhealthy providers degrade to onboarding-only routing rather than blocking all work.
- Target files:
  - `runtime/agents-md-files/system/AGENTS.md`
  - possibly `runtime/agents-md-files/coordinator/AGENTS.md`

Completion gate: installed guidance protects users from stale provider output even if they never read the provider manager docs.

### S6 — Add refresh hooks to onboarding workflows

- Update C-03 and C-05 so onboarding bootstrap/update work triggers provider refresh instructions or lifecycle-manager calls after successful memory changes.
- GrepAI: after C-03/C-05 changes memory files, ensure the memory-repos watcher sees changes or run managed refresh if unhealthy.
- CGC: after code closeout or branch/worktree transitions, refresh the affected code repo provider when configured.
- Do not refresh before approval gates or before memory/code commits that establish truth.
- Target files:
  - `runtime/skills/U-01-core-skills/C-03-repo-bootstrap/SKILL.md`
  - `runtime/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md`
  - possibly `runtime/skills/U-01-core-skills/C-09-git-worktree-manager/SKILL.md`

Completion gate: provider freshness becomes part of workflow closeout instead of user memory.

### S7 — Document operation and failure modes

- Add concise provider docs covering installation consent, artifact layout, lifecycle commands, status interpretation, stale-index behavior, and fallback.
- Document provider dependency layout: pinned requirements under `providers/requirements/`, per-provider venvs under `providers/_venvs/`, version-specific patches under `providers/patches/`, and per-instance runtime state under `providers/<provider>/<instance-id>/`.
- Document the CGC `.cgcignore` caveat, the monkey patch, how to verify it, and the upstream issue/request that would let the patch be removed.
- Document CGC performance observations separately from result quality: index wall time, executor shutdown warnings, discarded embedded-backend lock/concurrency behavior, FalkorDB Docker lifecycle cost, Docker daemon requirements, browser UI port, and refresh cost.
- Explain CLI vs MCP as transport choice; token economy is controlled by returned evidence budgets.
- Document multi-repo CGC setup as one `codegraphcontext-code` provider setting whose `roots` array expands into multiple per-repo runtime instances that share one FalkorDB Docker DBMS.
- Target docs:
  - `docs/reference/settings-json.md`
  - new or existing provider reference under `docs/reference/`
  - README/docs wording that no longer frames Agents Remember as competing with retrieval systems

Completion gate: a user can install/configure providers without reverse-engineering this task thread.

### S8 — Test and review

- Unit-test settings parsing and token/runtime-root expansion if a shared Python module is added.
- Unit-test provider install layout expansion: requirements path, venv path, patch path, runtime root, and provider-state path.
- Test the CGC `.cgcignore` patch against a temporary source repo and assert no `.cgcignore` or `.codegraphcontext` file is created in the indexed source repository.
- Run lifecycle manager `doctor/status` in dry-run mode without requiring CGC installation.
- When CGC is installed, run real smoke tests with runtime roots under `ar-coordination/providers/codegraphcontext/<repo-id>/`.
- Verify C-04 remains usable with no providers configured.
- Run markdown/doc checks if the repo has them.

Completion gate: the implementation passes no-provider, GrepAI-only, and GrepAI+CGC configured scenarios, and the provider evaluation table has informed the final C-04 query budgets.

### Bonus — Write the Agents Remember philosophy document

- Create a source-repo document that explains the philosophy behind Agents Remember's major design decisions.
- Treat this as a narrative design document, not a narrow reference page. It should explain where the ideas came from, which external developments support or challenge them, and how those ideas combine into the current product shape.
- Proposed location: `docs/philosophy.md`, with a link from `docs/README.md` once the first draft is good enough.
- Candidate themes:
  - why path-derived onboarding exists
  - why memory must be git-verifiable and drift-aware
  - why approval-gated memory matters
  - why Agents Remember is a trust/control layer over memory promotion, not a vector/graph/search competitor
  - what & why different retrieval strategies are employed.
    key parts: - the retrieval triangle of intent, semantics, and relationships - why onboardings alone works but is not enough - why intent retrieval should not compete with semantic or relationship based retrieval.
  - why task management is part of agents-remember-md and how it is tied to building truth.
    key parts: - knowledge in chat is fleeting - task files contain the intermediate change/plan - onboarding preserves the decisions that made it into the code - so knowledge can only effectively be preserved as long as the model is still aware - within a task a model can support the devs work of documentation. This can't be done effectively after other then the developer re-explaining - lessons learned from tasks through onboarding can inform future tasks
  - why C-04 routes by retrieval contract/substrate instead of picking one database
  - why context bundles and appropriate context matter more than maximum context
- Research expectation: bring in external information retroactively and cite it carefully. Candidate areas include vector-search limits for agents, document-structure retrieval, GraphRAG/relationship retrieval, context-rot or long-context limits, enterprise semantic layers/provenance, and agent memory products.
- Keep speculative or unverified industry observations out of public-facing claims until sourced.

Completion gate: a first draft exists that explains the product's conceptual lineage without turning the current C-04 implementation task into a broad market-research project.

---

## Proposed Code Examples

### E1 — Provider settings extension

Coordination configuration for optional context providers.

Why this example is included: the provider registry should live in ar-coordination settings to simplify installation and management. Provider indexes are local discovery accelerators, not durable memory. Their stores should not become distributed truth like onboarding.
What they offer is specialized discovery and lower retrieval friction, but they should not change Agents Remember's role as the routing, verification, drift, branch-validity, source-proof, and memory-promotion layer.

```json
{
  "version": 3,
  "contextProviders": {
    "enabled": true,
    "providers": {
      "grepai-memory": {
        "type": "semantic",
        "scope": "memory",
        "enabled": true,
        "roots": ["<coordination_root>/memory-repos"],
        "runtimeRoot": "<coordination_root>/providers/grepai/memory-repos",
        "watch": {
          "mode": "background",
          "cwd": "<coordination_root>/memory-repos",
          "logDir": "<runtimeRoot>/logs"
        },
        "freshness": {
          "refreshAfter": ["C-03", "C-05"]
        }
      },
      "codegraphcontext-code": {
        "type": "relationship",
        "scope": "code",
        "enabled": false,
        "roots": [
          {
            "repoId": "agents-remember-md",
            "path": "<workspace_root>/agents-remember-md"
          },
          {
            "repoId": "resolve_auto_editor",
            "path": "<workspace_root>/resolve_auto_editor",
            "cgcignorePatterns": [
              "tools/ffmpeg/"
            ]
          }
        ],
        "runtimeRoot": "<coordination_root>/providers/codegraphcontext",
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
        "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
        "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
        "stateFileTemplate": "<instanceRoot>/provider-state.json",
        "backend": {
          "id": "codegraphcontext-falkordb",
          "type": "falkordb-remote",
          "mode": "docker",
          "image": "falkordb/falkordb:v4.18.7",
          "imageLockFile": "<coordination_root>/providers/requirements/codegraphcontext-falkordb-docker.lock",
        "runtimeRoot": "<coordination_root>/provider-data/codegraphcontext/falkordb",
          "dataRoot": "<backendRuntimeRoot>/data",
          "containerName": "ar-cgc-falkordb",
          "ports": {
            "falkordb": {
              "bindHost": "127.0.0.1",
              "hostPort": "auto",
              "containerPort": 6379
            },
            "browser": {
              "bindHost": "127.0.0.1",
              "hostPort": "auto",
              "containerPort": 3000
            }
          }
        },
        "processEnvTemplate": {
          "CGC_RUNTIME_DB_TYPE": "falkordb-remote",
          "DEFAULT_DATABASE": "falkordb-remote",
          "HOME": "<instanceRoot>/.codegraphcontext/run/home",
          "USERPROFILE": "<instanceRoot>/.codegraphcontext/run/home",
          "APPDATA": "<instanceRoot>/.codegraphcontext/run/appdata",
          "LOCALAPPDATA": "<instanceRoot>/.codegraphcontext/run/localappdata",
          "FALKORDB_HOST": "<backend.ports.falkordb.bindHost>",
          "FALKORDB_PORT": "<backend.ports.falkordb.hostPort>",
          "FALKORDB_GRAPH_NAME": "cgc_<repoGraphId>",
          "LOG_FILE_PATH": "<instanceRoot>/.codegraphcontext/logs/cgc.log",
          "DEBUG_LOG_PATH": "<instanceRoot>/.codegraphcontext/logs/debug.log",
          "ENABLE_AUTO_WATCH": "false",
          "PYTHONIOENCODING": "utf-8",
          "PYTHONUTF8": "1"
        },
        "watch": {
          "mode": "managed-foreground",
          "cwdTemplate": "<instanceRoot>",
          "logFileTemplate": "<instanceRoot>/.codegraphcontext/logs/watch.log",
          "requiredBeforeSourceEdits": true,
          "requiredBeforeBranchSwitch": true
        },
        "freshness": {
          "refreshAfter": ["C-09-closeout"],
          "branchSwitch": "watcher-first",
          "hardRefresh": "explicit-only"
        }
      }
    },
    "policy": {
      "discoveryOnly": true,
      "sourceProofRequired": true,
      "maxSemanticQueriesPerPacket": 1,
      "maxGraphQueriesPerPacket": 2,
      "transportPolicy": {
        "default": "cli",
        "mcp": "optional",
        "tokenEconomy": "budget-returned-evidence"
      }
    }
  }
}
```

E1 field audit:

- `roots` is intentionally plural and is the unit that expands one provider setting into N repo instances. Each entry needs a stable `repoId` and a `path`; deriving ids from paths should be a fallback only for hand-written local config.
- The lifecycle manager writes each source repo's top-level `.gitignore` patterns into that repo instance's runtime-owned `.cgcignore`, so folders intentionally excluded from git such as `samples/` and `.tmp_drt/` are also excluded from CGC indexing.
- `roots[].cgcignorePatterns` is a repo-specific managed ignore input for additional vendored/generated paths that are not already covered by `.gitignore`; the lifecycle manager writes those patterns into the same runtime-owned `.cgcignore`.
- `runtimeRoot` is the provider-family root. It is not per repo. Per-repo state comes from `instanceRootTemplate`.
- `stateFileTemplate` is per root because status, process metadata, resolved backend ports, last refresh, and containment checks belong to each expanded repo instance.
- `backend` describes one shared FalkorDB Docker DBMS for the provider. It is not repeated per repo.
- `backend.imageLockFile` is consumed by Docker install/preflight so image tags can be resolved to a reproducible digest without hiding the pin in ad hoc shell history.
- `ports.*.hostPort: "auto"` is an input to the lifecycle manager. The resolved host ports and browser URL belong in provider state after install/start, not in the declarative settings file.
- `processEnvTemplate`, `watch.cwdTemplate`, and `watch.logFileTemplate` are per-root templates. The lifecycle manager must expand them with `repoId`, graph-safe `repoGraphId`, `path`, `instanceRoot`, and resolved backend port values before running CGC.
- `preserveDataOnReinstall` and `destructiveActions` are not per-provider settings because they are global lifecycle invariants: reinstall preserves DBMS data, and destructive DB operations require explicit commands.
- The runtime installer may copy this settings shape, but provider setup must actually consume it. Manual `--repo-id --code-repo-root` commands are acceptable debug overrides, not the primary managed path.

---

## Provider Lifecycle Notes

### GrepAI memory provider

- Purpose: semantic discovery over Agents Remember memory repos. GrepAI returns candidate memory routes, not truth.
- Scope: one configured root at `<coordination_root>/memory-repos` so the same index covers multiple memory repos.
- Artifacts: GrepAI-owned index/config stays at `<coordination_root>/memory-repos/.grepai`. Agents Remember-owned process metadata, PID files, and logs stay at `<coordination_root>/providers/grepai/memory-repos/`.
- Managed start: run from `<coordination_root>/memory-repos` with `grepai watch --background --log-dir <runtimeRoot>/logs`. Foreground `grepai watch` is allowed only for manual development sessions.
- Status gate: before using GrepAI, run status checks against the configured root, including `grepai status --no-ui` for index state and `grepai watch --status` for managed background watcher state. If a developer is running a foreground watcher, the lifecycle manager may treat it as an observed dev state but should not encode that as the required runtime mode.
- Self-update: the watcher performs an initial scan, skips unchanged files by modification time, indexes new/modified files, and handles create/modify/delete/rename events with debouncing.
- Hard refresh: stop and restart the managed watcher so the initial scan reconciles disk/index state. A full destructive rebuild of `.grepai` is a doctor/remediation action and should require explicit approval because it rewrites provider-owned index state.
- Query mode: agents may use `grepai search` or GrepAI MCP as semantic discovery. Results must be verified through onboarding/source proof before memory promotion.
- Transport choice: prefer CLI for deterministic lifecycle/status/search flows. MCP is acceptable when the runtime has already mounted it and it returns similarly bounded results. Neither transport is proof.

### CodeGraphContext code provider

- Purpose: relationship and impact discovery over code repositories. CGC returns candidate relationships, not final architecture claims.
- Scope: one `codegraphcontext-code` provider setting can cover multiple code repositories. Multi-repo support is achieved by expanding its `roots` array into per-repo runtime instances, each with its own instance root and one supervised ingestion process, while all instances share one FalkorDB Docker DBMS.
- Install: use `<coordination_root>/providers/_venvs/codegraphcontext/` as the shared CGC venv and install only from the source-copied `<coordination_root>/providers/requirements/codegraphcontext.txt` pin.
- Patch policy: apply version-checked patches from `<coordination_root>/providers/patches/codegraphcontext/` only to enforce the managed-provider contract. Patch application must happen after install and before indexing, and failure to apply or verify a required patch makes the provider faulted.
- Required patches: CGC v0.4.10 must preserve an explicit runtime-owned `.cgcignore` instead of overwriting it, and on Windows its repository deletion path must match both slash and backslash child prefixes so `cgc index --force` removes stale ignored paths before rebuilding.
- Artifacts: all disposable Agents Remember-managed CGC artifacts for a repo live under `<coordination_root>/providers/codegraphcontext/<repo-id>/`. The repo runtime root holds CGC config, ignore rules, logs, ingestion state, and `provider-state.json`; shared durable FalkorDB DBMS data lives under `<coordination_root>/provider-data/codegraphcontext/falkordb/`.
- Source containment: managed CGC must not create provider config, ignore files, reports, databases, logs, or runtime state inside indexed source repositories. Any source-repo mutation is either an explicitly approved source config change or a provider fault.
- Configuration: lifecycle tooling expands `processEnvTemplate` per repo instance and prefers explicit runtime environment over user-global CGC config. FalkorDB Docker mode should set `CGC_RUNTIME_DB_TYPE=falkordb-remote`, `DEFAULT_DATABASE=falkordb-remote`, `FALKORDB_HOST=127.0.0.1`, `FALKORDB_PORT=<allocated-redis-port>`, optional `FALKORDB_PASSWORD`, `FALKORDB_GRAPH_NAME=<repo-scoped-namespace>`, `LOG_FILE_PATH=<instanceRoot>/.codegraphcontext/logs/cgc.log`, `DEBUG_LOG_PATH=<instanceRoot>/.codegraphcontext/logs/debug.log`, `ENABLE_AUTO_WATCH=false`, `PYTHONIOENCODING=utf-8`, and `PYTHONUTF8=1`.
- Persisted config: keep process controls and user-profile routing as process environment unless the installed CGC version explicitly accepts them as persisted config. Persist only lifecycle-vetted CGC config under the instance root. Route `HOME`/`USERPROFILE` to a child such as `<instanceRoot>/.codegraphcontext/run/home`, not `<instanceRoot>` itself, so CGC can still detect `<instanceRoot>/.codegraphcontext` as the local repo context.
- Backend target: use `falkordb-remote` backed by a lifecycle-owned Docker container using the browser-capable `falkordb/falkordb` image. There is no managed alternate backend in this task.
- Shared DBMS model: FalkorDB Docker mode should run one DBMS instance per coordination root. Repos remain separate provider instances at the Agents Remember layer through repo-scoped graph/repository metadata, while sharing the same backend service and persistent DBMS storage.
- Managed start: start exactly one lifecycle-owned ingestion process for each selected root. That process can be a supervised foreground/blocking `cgc watch <code_repository_root>` process or an MCP server that owns watch state for that same root, but the lifecycle manager should not run both for the same root during normal operation.
- Start-all: settings-scoped start must be available. `cgc start` without `--repo-id` and `cgc start-all` both read `<coordination_root>/system/settings.json`, fan out over all configured `roots[]`, report per-repo results, and avoid launching duplicate watchers when a managed PID is already alive.
- Shutdown-all: settings-scoped shutdown must be available. `cgc stop` without `--repo-id`, `cgc stop-all`, and `cgc shutdown-all` all read `<coordination_root>/system/settings.json`, fan out over all configured `roots[]`, report per-repo results, and treat an already-stopped repo as a successful no-op.
- Status gate: check that the provider command is installed, runtime paths are writable, patch verification passes, `cgc doctor` succeeds under the provider env, the target repo appears in `cgc list`, `cgc stats <code_repository_root>` reports indexed content, no provider artifacts were written to the source repo, and the selected root has its expected lifecycle-owned ingestion process when watch mode is enabled.
- Self-update: `cgc watch` uses a watchdog observer and updates the graph on create/modify/delete/move events. If MCP is used instead of a direct CLI watcher, the MCP server owns equivalent watch state through `watch_directory`, `list_watched_paths`, and `unwatch_directory` for that root.
- Freshness: watcher-first is the intended operating mode for source edits and branch switches. If the selected root's ingestion process was down or a branch switch may have invalidated coverage, mark that repo instance stale and reconcile it before claiming complete relationship coverage.
- Hard refresh: a per-repo hard refresh is an explicit lifecycle operation, not an install or reinstall side effect. If that repo root's ingestion process is running, stop or pause it during the rebuild and restart it afterward; do not stop the shared FalkorDB DBMS for a per-repo hard refresh.
- Destructive boundary: reinstall/update may replace CGC venvs, scripts, patches, config scaffolding, containers, and missing runtime directories, but it must preserve FalkorDB volumes/bind mounts and graph namespaces. Deleting indexed data requires an explicit destructive action and should be recorded in provider state.
- Query mode: agents may use CGC CLI/MCP relationship commands such as callers, callees, call chain, dependencies, inheritance tree, variable usage, complexity, dead-code, or read-only Cypher. Results must lead to source/onboarding verification before claims are promoted.
- Transport choice: prefer CLI for scripted provider manager actions and bounded relationship probes. MCP is acceptable for interactive agent workflows, especially when mounted once per provider instance. Token economy must be enforced through result limits and source-follow-up discipline either way.

### Provider manager responsibilities

- Install providers only after opt-in consent.
- Keep provider roots out of code repos and memory repos unless the provider's own index format requires a repo-local config file already approved for that provider.
- Install provider CLIs/binaries into coordination-owned locations from source-copied pinned files under `providers/requirements/`.
- Apply and verify version-specific provider patches before using patched providers.
- Track configured roots, runtime roots, process ids, log paths, command versions, and last successful refresh timestamps.
- Track provider versions, requirements hashes, applied patch ids, and artifact-containment checks in `provider-state.json`.
- On Windows, process-health checks must use a reliable PID API rather than localized shell tools such as `tasklist`, because shell access failures can misreport running watchers as dead.
- GrepAI status must require a live native or managed watcher when the provider is enabled; a successful index-status command alone is not enough to mark the provider healthy.
- Support `status`, `start`, `stop`, `refresh`, and `doctor` for each configured provider setting and each expanded provider instance.
- Provide one coordinator-level watcher command surface that starts, checks, and stops every enabled provider watcher; the normal operator workflow must not require separate GrepAI and CGC commands.
- Delete and recreate `<coordination_root>/providers/` during reinstall, then install dependencies for enabled providers from the copied pins; keep durable provider databases under `<coordination_root>/provider-data/`.
- Reject configured CGC roots that do not resolve to existing code repository directories before creating runtime state.
- Allow multiple GrepAI memory scopes if needed later, but default to one memory-repos root.
- Allow multiple CGC code scopes from the start by expanding `codegraphcontext-code.roots[]`; each expanded repo scope is keyed by stable `repoId` and C-08 resolved code root.

---

## Router Benchmark Check

Benchmark run: `ar-coordination/benchmarks/user-runs/tensorflow-check-numerics-xla/2026-05-21T03-31-16/summary.md`.

Baseline for comparison: the original 2026-05-19 no-onboarding author baseline had duration `207.042 - 254.066` seconds, input tokens `1202850 - 1247262`, output tokens `7209 - 9475`, JSONL size `214964 - 711605`, and exit code `0`.

| Variant | Duration | Input tokens | Output tokens | JSONL bytes | Observed routing behavior |
| --- | ---: | ---: | ---: | ---: | --- |
| no-onboarding | 351.377 | 2206136 | 7744 | 2560686 | Source-only run did not use Agents Remember skills or providers. It relied on broad `rg` source discovery and produced a correct triage. |
| with-onboarding | 380.346 | 1153975 | 8754 | 208865 | Read C-08, C-02, and `C-04-retrieval-strategy-router`; checked provider readiness; benchmark-local provider settings reported both GrepAI and CGC disabled; degraded to Intent with sidecars and bounded source confirmation. |
| with-onboarding-warm | 397.515 | 1579427 | 9999 | 346154 | Read `C-04-retrieval-strategy-router`, route indexes, sidecars, and source. The warm prompt correctly skipped C-08/C-02, but no live provider query ran. |

Judgement: this run validates that the renamed C-04 skill is discoverable and that agents can apply the Semantics / Relationship / Intent framing with provider fallback. It does not validate live GrepAI or CGC use inside benchmark runs, because the benchmark-local `ar-coordination/` fixture currently lacks provider settings and reports `grepai-memory=false` and `codegraphcontext-code=false`. A provider-enabled benchmark mode is still needed if the benchmark is expected to measure GrepAI/CGC behavior rather than onboarding-only fallback.

---

## Decision Log

| Date-Time        | Decision                                                                                                                                        | Rationale                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-21T03:40 | Treat the first post-C-04 benchmark pass as routing/fallback evidence, not provider-use evidence. | The benchmark workspace used the renamed C-04 skill and produced correct TensorFlow triage answers, but its local coordinator did not include provider settings. `watchers status` therefore reported both GrepAI and CGC disabled, so the agent correctly used Intent/onboarding fallback. A follow-up benchmark fixture mode must inject or mount provider settings before using this benchmark to judge live Semantics/Relationship provider behavior. |
| 2026-05-21T03:20 | Rename the physical C-04 skill folder to `C-04-retrieval-strategy-router`. | The skill discovery and benchmark surfaces should not carry a stale path once the behavior and front matter have changed. The source, installed runtime, and current onboarding sidecar path now use the retrieval-router folder name; benchmark historical author results keep the old path as baseline evidence. |
| 2026-05-21T03:05 | Wire the C-04 router into installed guidance and onboarding memory. | Updating the skill body alone would leave future agents with stale routing surfaces. The installed/source AGENTS templates, coordinator tools examples, live coordinator tools, and file-level onboarding now describe C-04 as the retrieval strategy router, use GrepAI query-first search syntax, and point Relationship probes through lifecycle-managed `cgc run`. |
| 2026-05-21T03:00 | Rewrite C-04 as `c-04-retrieval-strategy-router` while keeping the physical `C-04-onboarding-read-mode/` folder stable for the first test pass. | The skill now teaches agents to choose Semantics, Relationship, or Intent based on the missing context bundle; GrepAI and CGC are discovery providers with bounded query budgets, while onboarding/source remain proof. The initial stable-folder choice avoided installed-skill path churn while the front matter and references exposed the new skill name; this was superseded by the physical rename decision above. |
| 2026-05-21T03:00 | Add lifecycle `cgc run` as a native CGC pass-through with managed environment. | Raw `cgc.exe analyze ...` fails outside the provider runtime because FalkorDB process environment is not present. The pass-through keeps provider queries native while applying the configured repo environment, so C-04 can give agents a usable relationship-query command without introducing an output-normalization adapter. |
| 2026-05-21T02:33 | Add an aggregate `watchers` lifecycle command for all enabled provider watchers.                                                                 | Starting provider watchers should be one coordinator operation, not separate GrepAI and CGC invocations. `watchers start`, `watchers status`, and `watchers shutdown-all` read the enabled provider settings and fan out to GrepAI plus every configured CGC repo. |
| 2026-05-21T02:33 | Make coordinator settings the default CGC lifecycle input; keep `--from-settings` only as a debug override.                                      | The lifecycle script is installed under the coordinator runtime and the normal machine-readable settings location is stable at `<coordination_root>/system/settings.json`. Requiring `--from-settings` in normal commands was performative and made the installed runtime harder to use. |
| 2026-05-21T02:25 | Treat GrepAI status as unhealthy when no observable watcher is running, while keeping manual foreground watch as valid development evidence.      | Reinstall correctly restored the GrepAI binary and index access, and manual foreground `grepai watch --no-ui` works from `<coordination_root>/memory-repos`. The remaining gap is lifecycle ownership/status detection: managed start still needs to keep a watcher alive or deliberately observe the developer-run foreground watcher instead of hiding watcher absence behind successful index-status return codes. |
| 2026-05-21T02:20 | Use Windows process API checks for managed watcher PID health.                                                                                  | After reinstall, `cgc start-all` launched all three watchers, but `status` reported them as dead because `tasklist` returned access denied in this environment. The lifecycle manager now checks Windows PIDs through `OpenProcess` / `GetExitCodeProcess`, so status can report live managed watcher processes without depending on localized or permission-sensitive command output. |
| 2026-05-21T02:14 | Make reinstall install enabled provider dependencies after recreating provider scaffolding.                                                     | Because `providers/` is now intentionally disposable, reinstall cannot stop after copying requirement files. The installer now reads the live coordinator settings and runs the GrepAI and CGC install commands for enabled providers unless `--skip-provider-deps` is explicitly requested. Durable indexed data remains under `provider-data/` and is not deleted by reinstall. |
| 2026-05-21T01:47 | Make provider reinstall wipe scaffolding and keep durable provider data in `provider-data/`.                                                   | A live reinstall left an unconfigured `my-app` CGC instance plus legacy Kuzu files under provider state, proving install/apply was not idempotent enough. The installer should not maintain fragile preserve/delete lists inside `providers/`; it now treats `providers/` as disposable scaffolding and preserves databases only by moving durable backend state outside that tree to `provider-data/`. |
| 2026-05-21T01:24 | Patch CGC hard-refresh deletion on Windows before trusting clean rebuilds.                                                                       | CGC v0.4.10 stores Windows paths with backslashes but its repository-delete helper builds a slash-only child prefix. `cgc index --force` therefore printed that the old index was deleted while leaving stale `File` nodes from the earlier bad `samples/` and `/tools` indexing run. The managed provider now applies a second version-checked patch so hard refresh deletes both slash and backslash child paths before rebuilding. After reinstalling the runtime, applying the patch, and refreshing `resolve_auto_editor`, FalkorDB reports 146 `File` nodes, 0 under root `samples/`, and 0 under root `tools/` or `.tmp_drt`. |
| 2026-05-21T01:10 | Route CGC runtime context so managed ignore rules and source `.gitignore` exclusions are authoritative before indexing.                         | The `resolve_auto_editor` crash was amplified by two lifecycle bugs: the old `.cgcignore` patch selected the explicit runtime ignore path but let CGC overwrite it with defaults, and `HOME`/`USERPROFILE=<instanceRoot>` made CGC classify `<instanceRoot>/.codegraphcontext` as global config instead of the local repo context. CGC then ignored the managed `.cgcignore`, indexed 551 files including `.gitignore`-ignored `samples/`, `/tools`, and generated JSON, and hit native `tree_sitter` access violations. Patch v2 preserves existing explicit runtime ignores; `HOME`/`USERPROFILE` now point at `<instanceRoot>/.codegraphcontext/run/home`; managed `.cgcignore` inherits top-level `.gitignore` plus root-specific patterns. With those fixes, `resolve_auto_editor` indexed 146 files successfully in 7.6 seconds. |
| 2026-05-20T23:50 | Set FalkorDB Docker as the only managed CGC backend direction.                                                                                  | The provider lifecycle should not carry alternate managed backend code for a path the developer has rejected. The implementation should target one lifecycle-owned browser-capable FalkorDB Docker DBMS per coordination root, with repo-scoped CGC provider instances connecting to that service. Previous embedded-backend observations remain evidence for why this direction was chosen, not a fallback implementation path. |
| 2026-05-20T19:41 | Treat provider requirement and patch assets as onboarding-covered source files.                                                                 | The provider lifecycle implementation adds `runtime/providers/requirements/codegraphcontext.txt` and a versioned CGC patch file. The memory repo path rules previously excluded `.txt` and `.patch`, which left their sidecars marked disabled during drift checks. The memory repo now includes those file types so provider pins and monkey patches remain visible to future agents.                                                                                                                                                                                                                                                                           |
| 2026-05-20T19:11 | Start closeout with provider evaluation evidence and live CGC runtime verification.                                                             | `provider-evaluation.md` records the contained provider layout, GrepAI watcher state, CGC install/patch status, `agents-remember-md` prototype indexing stats, and TensorFlow refresh risk. The prototype runtime produced stats for `agents-remember-md`, but managed implementation is now directed to FalkorDB Docker only.                                                                              |
| 2026-05-20T18:20 | Implement the first contained provider lifecycle surface before rerunning CGC indexing.                                                         | The repo-local `.cgcignore` was removed, and source changes now ship pinned CGC requirements, a version-specific `.cgcignore` runtime-root patch asset, installer support for `runtime/providers`, a shared CGC layout/patch helper, unit tests, and a `provider-lifecycle.py` script for CGC/GrepAI status, layout, patch, start, stop, refresh, and doctor flows. The clean-layout evaluation can now proceed from a contained runtime root instead of repeating the messy split `db/` plus `home/.codegraphcontext` layout.                                                                                                                                   |
| 2026-05-20T17:25 | Pin provider installs, use one venv per provider type, and require a CGC `.cgcignore` runtime-root patch before managed CGC adoption.           | The first CGC spike showed two lifecycle mismatches: early path controls produced a split layout, and CGC v0.4.10 generated an untracked `.cgcignore` inside the indexed `agents-remember-md` source repo. The task now prefers coordination-owned provider roots, shared provider-specific venvs, pinned requirements under `providers/requirements/`, version-checked patches under `providers/patches/`, and FalkorDB Docker as the only managed CGC backend. TensorFlow CGC indexing is paused until the containment patch and target backend are in place.                     |
| 2026-05-20T14:22 | Add a bonus task to draft a source-repo philosophy document for Agents Remember.                                                                | Developer observed that the project is conceptually close to complete and that a dedicated document should explain the philosophy behind Agents Remember's decisions, including external developments and retroactive research. This is added as a bonus task so the idea is captured while keeping C-04 implementation focused.                                                                                                                                                                                                                                                                                                                                 |
| 2026-05-20T14:15 | Capture the transcript's industry retrieval context as a task-local table.                                                                      | Developer wanted the current-industry-development framing written down but was not sure where it belongs yet. The task now has an `Industry Retrieval Context` table after the conceptual model, explicitly attributed to the developer-provided transcript and framed as support for the retrieval-substrate direction rather than verified product documentation.                                                                                                                                                                                                                                                                                              |
| 2026-05-20T14:08 | Reframe open questions around the initial C-04 router test versus deferred provider lifecycle work.                                             | The previous open questions still reflected the broader provider-platform direction. The current task focus is testing whether models adapt to the Semantics / Relationship / Intent retrieval strategy router first. Physical skill rename, substrate naming, and model-adaptation examples now block the initial C-04 pass; provider refresh hooks, CGC artifact verification, and Graphify containment move to deferred lifecycle questions.                                                                                                                                                                                                                  |
| 2026-05-20T14:00 | Document the Semantics / Relationship / Intent substrate concept in source docs now, with broader doc updates deferred.                         | Developer requested that the concept be documented in the source repository docs immediately, while the rest of the documentation can be updated later in the task. `docs/concepts.md` now explains C-04 as a retrieval strategy router over memory substrates and keeps provider output framed as candidate routing evidence rather than proof.                                                                                                                                                                                                                                                                                                                 |
| 2026-05-20T13:55 | Reframe C-04 as a retrieval strategy router over Semantics, Relationship, and Intent substrates.                                                | Developer clarified that the long-term skill should provide retrieval strategies for different memory substrates based on the model's intent. Semantics applies when the concept is known but structure/location is unknown; Relationship applies when an anchor is known but relationships are unknown; Intent applies when an anchor/location is known but hidden contracts and code truths are unknown. This reframes C-04 from "which tool should I call" to "which retrieval contract and substrate fit the next missing bundle."                                                                                                                           |
| 2026-05-20T13:55 | Prefer `c-04-retrieval-strategy-router` over `c-04-context-router` as the refined skill name.                                                   | `context-router` pointed in the right direction, but the new substrate model is more specific. `c-04-retrieval-strategy-router` tells the model that the skill chooses a strategy, not just a context source, while the description can name the Semantics / Relationship / Intent triangle.                                                                                                                                                                                                                                                                                                                                                                     |
| 2026-05-20T13:25 | Recommend renaming C-04 to `c-04-context-router` for the initial router-behavior test.                                                          | Developer clarified that several lifecycle/provider elements are deferred until the system is tested, and the first C-04 change should focus on how the model adapts to the router. `c-04-context-router` describes the actual behavior without overclaiming the full "trusted" provider lifecycle. The proposed front matter encodes the known-location / known-meaning / known-anchors routing triad.                                                                                                                                                                                                                                                          |
| 2026-05-20T13:06 | Define C-04's provider routing around known location, known meaning, and known anchors.                                                         | Developer clarified the core mental model: use onboarding when location is known; use GrepAI over memory when meaning is known but location is unknown; use CodeGraphContext over code when anchors are known but relationships are unknown. This becomes the primary decision rule for the C-04 rewrite and keeps providers complementary instead of overlapping by default.                                                                                                                                                                                                                                                                                    |
| 2026-05-19T17:15 | Make provider quality/volume evaluation an implementation gate before deeper integration.                                                       | Developer requested that the actual implementation plan include testing both tools for output volume and quality, with a table documenting what was queried, what was returned, and a judgement about usefulness. S2 now gates C-04 query budgets and provider-default decisions on measured GrepAI/CGC behavior rather than assumptions from docs.                                                                                                                                                                                                                                                                                                              |
| 2026-05-19T17:06 | Treat CLI and MCP as transports with similar token economics when result budgets are equal.                                                     | Developer clarified the expectation that both GrepAI and CGC can be queried through CLI, and asked whether CLI/MCP token economy is probably the same. The design answer is that transport is secondary: CLI is preferred for deterministic lifecycle/status/query commands, MCP is acceptable for mounted interactive workflows, and token cost is controlled by bounding returned provider output and only promoting claims after source/onboarding verification.                                                                                                                                                                                              |
| 2026-05-19T16:58 | Accept managed foreground CGC watchers as long as Agents Remember owns the lifecycle.                                                           | Developer clarified that foreground runtime is acceptable when Agents Remember can start, stop, monitor, and refresh it without user babysitting. The important contract is self-update through watch mode plus a hard-refresh path. GrepAI remains a memory-repos-wide semantic watcher, while CGC should be made multi-code-repo capable through one runtime root and supervised watcher/MCP process per configured code repo.                                                                                                                                                                                                                                 |
| 2026-05-19T16:43 | Treat CodeGraphContext as a supervised-process provider, not a no-process library.                                                              | Source confirms CGC has multiple runtime modes: the CLI `watch_helper` starts a watchdog observer thread and then blocks until Ctrl+C; `MCPServer` owns a `CodeWatcher` and exposes `watch_directory`, `list_watched_paths`, and `unwatch_directory`; and FalkorDB can be run as a managed DBMS backend. The provider lifecycle manager should therefore own the process boundary instead of assuming CGC provides an independent daemon manager.                                                                                                                          |
| 2026-05-19T16:31 | Treat CodeGraphContext as the leading candidate to replace Graphify for relationship discovery.                                                 | Current docs and source show it is much closer to the desired provider shape: native MCP and CLI, commands for callers/callees/call chains/dependencies/inheritance/dead-code, `index`, `watch`, `delete`, `doctor`, and portable bundles. Source exposes enough runtime controls for Agents Remember to place artifacts under `ar-coordination/providers/codegraphcontext/<repo>/` without relying on repo-local generated files. It is not installed locally yet, so an installation/source spike remains required before adoption.                                          |
| 2026-05-19T15:58 | Use Graphify only through an Agents Remember-managed lifecycle, not Graphify's installed hooks or generated agent instructions.                 | Source inspection of `graphifyy==0.7.10` confirms core graph paths are configurable enough for a managed provider: `_GRAPHIFY_OUT` comes from `GRAPHIFY_OUT`, watch/update/check-update use it, query/path/explain accept `--graph`, MCP serving accepts an explicit graph path, and `extract --out DIR` writes primary outputs under `<DIR>/graphify-out`. However `extract --out` alone does not redirect caches unless `GRAPHIFY_OUT` is also set, packaged hooks and platform instructions hard-code `graphify-out`, `cluster-only` writes to `<watch_path>/graphify-out`, and secondary memory/converted-file paths still assume repo-local `graphify-out`. |
| 2026-05-19T15:32 | Treat Graphify artifact-root support as partial until tested across the lifecycle.                                                              | Public Graphify docs show default outputs under `<path>/graphify-out/`; release notes document `graphify extract --out DIR`, and CLI docs show `graphify query --graph path/to/graph.json`. This is enough to justify a spike, but not enough to prove build/update/watch/hook/MCP can all be contained under `ar-coordination/providers/graphify/<repo>/`.                                                                                                                                                                                                                                                                                                      |
| 2026-05-19T14:53 | Do not treat the developer's manual foreground GrepAI watcher as the required runtime mode.                                                     | Developer clarified that the current `grepai watch` terminal session explains local observations but does not prescribe how Agents Remember should run GrepAI. The provider lifecycle manager should choose a managed mode that is reliable to start, stop, verify, and refresh.                                                                                                                                                                                                                                                                                                                                                                                 |
| 2026-05-19T14:50 | GrepAI watcher checks must be scoped to the configured provider root and account for manual foreground watcher observations during development. | Developer clarified the runner operates above `ar-coordination/memory-repos`. Local inspection found the relevant `.grepai` config under `ar-coordination/memory-repos/.grepai` with a fresh 1401-file index, but `grepai watch --status` still reports no background watcher from Codex's process context. The lifecycle manager should not rely only on global/background status while evaluating current local state.                                                                                                                                                                                                                                         |
| 2026-05-19T14:45 | Require provider runtime lifecycle management and containment before implementation.                                                            | Developer clarified that GrepAI must be running before reliance, configured GrepAI providers should trigger watcher checks as an always-on rule, Graphify artifacts must stay in the coordination runtime rather than code or memory repos, and a provider lifecycle manager is the minimum programmatic integration.                                                                                                                                                                                                                                                                                                                                            |
| 2026-05-19T14:15 | Add a provider lifecycle preflight before implementation.                                                                                       | Developer wants GrepAI and Graphify installation, runtime, maintenance, and re-ingestion behavior understood before code changes so provider quirks do not surface only after integration.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| 2026-05-19T14:03 | Prefer instructional provider profiles over API-normalization adapters.                                                                         | Developer clarified that normalizing GrepAI/Graphify outputs would create fragile adapter maintenance and make tool swaps expensive. The router should tell the model which provider to use for which question shape and let the model query the provider natively.                                                                                                                                                                                                                                                                                                                                                                                              |
| 2026-05-19T13:53 | Keep CocoIndex out of the concrete provider scope until separately evaluated.                                                                   | Developer clarified that the task has only considered GrepAI and Graphify so far. CocoIndex appears adjacent and possibly overlapping, but it should remain future-tool context rather than a named task requirement.                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 2026-05-19T13:47 | Reframe Agents Remember as complementary trust/control infrastructure over optional discovery providers.                                        | Developer clarified that Agents Remember should not compete with GrepAI, Graphify, or future retrieval/index tools. Local TensorFlow benchmark summaries show onboarding can save tokens in some variants but does not reliably reduce wall time, so speed cannot be the primary claim.                                                                                                                                                                                                                                                                                                                                                                          |

---

## Open Questions

| Category                        | Question                                                                                                                                                                                                                                                 | Current handling                                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Resolved initial C-04 router test | Should the initial pass only change C-04's front matter/title/body to `c-04-retrieval-strategy-router` while keeping the physical folder `C-04-onboarding-read-mode`, or should the folder be renamed after updating every reference? | Resolved: the first pass kept the path stable; the follow-up renamed the physical folder to `C-04-retrieval-strategy-router` and updated current references. |
| Resolved initial C-04 router test | Is `Intent` the right substrate name, or should it be renamed to reduce ambiguity with the model's intent?                                                                                                                                               | Resolved for the first test pass: keep `Intent`, explicitly defined as hidden contracts, invariants, behavioral expectations, branch-valid truths, and code intent. |
| Initial router evaluation | What minimal prompt/example set should be used to judge whether the model adapts to the Semantics / Relationship / Intent router before deeper provider lifecycle work?                                                                                  | First pass complete on `tensorflow-check-numerics-xla`: agents read the renamed C-04 and used Intent fallback. Provider-enabled benchmark coverage remains open because the isolated fixture currently has providers disabled. |
| Deferred provider lifecycle     | When provider lifecycle work resumes, should refresh hooks execute provider manager commands directly where configured, or only emit explicit workflow instructions until each provider helper has real smoke coverage?                                  | Deferred until provider lifecycle helpers exist and can be smoke-tested.                                       |
| Deferred provider lifecycle     | How should the FalkorDB Docker backend be implemented and verified as the managed CGC backend?                                                                                                                | Target one browser-capable `falkordb/falkordb` container per coordination root, shared by repo-scoped CGC provider instances, with persistent DBMS data preserved across reinstall and explicit destructive purge only. |
| Deferred provider lifecycle     | Is TensorFlow-sized CGC refresh cost acceptable for default relationship-provider setup, or should large-repo hard refresh remain explicit opt-in?                                                                                                       | Pending final TensorFlow refresh duration and stats in `provider-evaluation.md`.                               |
| Deferred provider lifecycle     | Should Graphify's hard-coded secondary paths be revisited?                                                                                                                                                                                               | Only if CGC performance, locking, or relationship quality fails evaluation badly enough to justify fallback.   |

---

## References

- Developer clarification in this task discussion.
- `agents-remember-md/benchmarks/cases/tensorflow-check-numerics-xla/author-results/2026-05-19/summary.md`
- `ar-coordination/benchmarks/user-runs/tensorflow-check-numerics-xla/2026-05-21T03-31-16/summary.md`
- GrepAI semantic search docs: https://yoanbernabeu.github.io/grepai/search-guide/
- Local GrepAI CLI help checked for `grepai watch --background --status --stop --log-dir`, `grepai status --no-ui`, and watcher self-update semantics.
- Graphify docs: https://graphify.net/zh/
- CocoIndex docs checked only as adjacent future-tool context: https://cocoindex.io/cocoindex-code/
- Graphify CLI reference: https://graphify.net/graphify-cli-commands.html
- Graphify v0.7.3 release note for `graphify extract --out DIR`: https://newreleases.io/project/github/safishamsi/graphify/release/v0.7.3
- Local source inspection of the published `graphifyy==0.7.10` wheel, especially `graphify/__main__.py`, `graphify/watch.py`, `graphify/cache.py`, `graphify/detect.py`, `graphify/hooks.py`, and `graphify/serve.py`.
- CodeGraphContext docs and source: https://github.com/CodeGraphContext/CodeGraphContext and https://codegraphcontext.github.io/CodeGraphContext/
- CodeQL CLI database docs checked as a more mature but less plug-and-play fallback: https://docs.github.com/en/code-security/reference/code-scanning/codeql/codeql-cli-manual/database-create
- Joern docs checked as a CPG fallback with configurable outputs but heavier query/lifecycle integration: https://docs.joern.io/

---
