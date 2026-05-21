# Agentic Context Kernel And MCP Bridge Design Note

**Project:** `agents-remember-md`  
**Intended location:** beside `roadmap/260521_provider-lifecycle-mcp-bridge/task.md` or as a companion philosophy/design artifact  
**Draft date:** 2026-05-21  
**Status:** design consolidation / roadmap companion  

---

## 0. Important Notes

**Developer-Note: This document is not supposed to be viewed as fully specced. It is a more of a mature brain storm. Not all ideas are getting adopted. And some are future music. Many shapes are not mature. The model that wrote this document worked off of memory in regards to the agents-remember-md repo. Therefore not everything is accurately based of our current codes state. But the general gist has the right spirit in many areas. The directions of this document that are solid are following:

- Building an MCP to enable provider operations especially when they are restricted to their sandbox.
- Using the MCP as a server to expose functionality that today is scattered within skills.
- Organising the python functions by domain and modularising monololiths into smaller modules that are defined by their purpose
- Skills remain as the instructions layer that teach the model how to operate the MCP when before they tought how operate the pythons directly.

This is basically all what is "new". We do not invent new functionality. We want to migrate functionality to enable safer and smoother operations and to reduce technical debt.
Any fallbacks or compatability code has to be explicitly justified to the developer before implementing it. So they have to appear in the task file. And those fallbacks need to
have a concrete reason. "Existing users" is not one of them. This is a greenfield project that hasn't even hit v1.0.0. They are no users that could get hurt. 
In fact in order to hit even close v1.0.0 the "slop" that has build up needs to be significantly reduced. To achieve that the project has now a pyproject.toml and a requirements.txt at it's root defining dev dependencies to ensure quality. Therefore before any plans can be made those code quality tools need to be installed first in a virtual environment. Where those tools are run (source repo or ar-coordinator) depends on wether or not the pythons need to be in their runtime or not. Once they are installed the agent can run those to get reliable data about the weak parts of this code base.
The findings than get discussed with the developer and written in a markdown file inside the task folder so the information doesn't get lost. Based on what the analysis presents we can think of a refactoring strategy including priorisation.

So in short I aim for a cleaner, more reliable, better defined, and maintainable architecture. The MCP server is a core piece to that in order to seperate concerns and improve operations.

When it is about time to make the task files we will use the Light task schema. One master file that serves as overview and every phase gets its own file. Once implementation starts
the agent is only allowed to implement one task at a time and then ask the developer for feedback.

**


---

## 1. Purpose

This note consolidates the design shift that emerged while integrating GrepAI, CodeGraphContext (CGC), provider lifecycle management, and the upcoming MCP bridge into `agents-remember-md`.

The project began as a way to create and maintain automated structural documentation for codebases. It has evolved into something more fundamental: a branch-aware, provider-aware, human-reviewed context layer that coding agents and surrounding tools can build on.

The working phrase for this layer is:

> **Agentic Context Kernel**

This document explains why that framing fits, what boundaries it implies, and how the MCP bridge should be designed so the system can move from experiment to infrastructure without turning the skills, scripts, or providers into an unmaintainable tangle.

---

## 2. Reframed Task

### Surface Request

Build an MCP bridge so agents can access provider lifecycle commands, GrepAI, CGC, resolver state, worktree state, and drift/staleness checks more reliably across harnesses and sandboxes.

### Deeper Objective

Move `agents-remember-md` from a collection of useful skills and scripts into a stable control plane that exposes clear, safe, composable context operations to multiple consumers:

- coding harnesses such as Codex, Claude Code, Copilot, and future agent runtimes
- a future dashboard or TUI
- local CLI workflows
- CI/GitHub bots
- human review workflows

### Highest-Leverage Framing

The MCP should not be treated as “where the logic goes.” The MCP is the **controller/API boundary** over deterministic kernel services.

The correct split is:

```text
Skills      = pedagogy, routing doctrine, model affordances
MCP         = safe controller/API boundary
Python core = deterministic kernel services
Providers   = replaceable drivers / retrieval substrates
Views       = harnesses, dashboard, TUI, CI, bots
```

This framing lets the project keep the skills as the model-teaching layer while moving operational complexity into testable, importable, host-side services.

---

## 3. Design Philosophy

### 3.1 Core Mental Model

`agents-remember-md` should be understood as a **context kernel** for agentic software work.

A normal operating-system kernel does not try to be the desktop, the terminal, every driver, and every app. It exposes stable primitives that higher-level systems build on. Likewise, Agents Remember should not try to be a code harness, a graph database, a vector database, or a dashboard. It should expose reliable primitives around context, truth, branch state, memory, provider health, and workflow state.

The kernel answers questions like:

```text
Where am I?
Which branch and memory state am I allowed to trust?
Which provider can answer this kind of question?
Is that provider fresh?
Which onboarding truth applies here?
What workflow state am I in?
What must be updated before closeout?
```

The project becomes stronger when these questions are answered by stable services instead of being rediscovered by the model through repeated shell calls.

### 3.2 Why “Kernel” And Not “Operating System”

The system is not a complete operating system. The harnesses already provide many “distro-like” features: chat, file exploration, terminal execution, tool palettes, editors, task surfaces, and model execution.

Agents Remember fits below those surfaces:

```text
Kernel:
  resolver, worktree truth, memory pairing, drift, provider health,
  retrieval routing, provenance, closeout contracts

Drivers:
  CGC, GrepAI, future graph/search/index providers

Shells / Harnesses:
  Codex, Claude Code, Copilot, pi.dev, VS Code integrations

Views:
  dashboard, TUI, CI summaries, GitHub bots

Distro:
  kernel + default providers + skills + dashboard/TUI + install/runtime conventions
```

This distinction matters because it prevents scope creep. The kernel should remain boring, deterministic, and composable. Higher-level experiences can be expressive, visual, and workflow-specific.

### 3.3 Skills Are Teaching Surfaces, Not Service Containers

The skills should not own operational complexity. Their purpose is to teach the model:

- which substrate to use
- what question shape each provider answers
- what a useful provider query looks like
- how to interpret output
- when to confirm with onboarding/source truth
- when to fall back

This is why the GrepAI and CGC provider cards belong beside C-04. C-04 is the execution point where the agent chooses the retrieval substrate. The model is unlikely to inspect provider implementation folders during normal user-mode work. The provider cards must live close to the moment where the model asks:

```text
Do I need Semantics, Relationship, or Intent?
```

The cards are not provider manuals. They are **affordance cards**.

They teach:

```text
refactor target unclear      -> CGC complexity
blast radius unclear         -> CGC callers / deps
known function, downstream?  -> CGC calls
two anchors, path unknown?   -> CGC chain
concept known, path unknown? -> GrepAI search
contract/invariant unclear?  -> onboarding + bounded source confirmation
```

The model uses tools more reliably once it can see exactly what output shape a tool gives and why that output is better than rediscovering the same information through repeated `rg`/read calls.

### 3.4 MCP Is The Controller Boundary, Not The New Monolith

MCP is valuable because it can run host-side and expose resources that sandboxes often cannot safely access:

- provider watchers
- process state
- local graph/search databases
- coordination runtime folders
- provider runtime folders
- host-local network endpoints
- long-lived lifecycle state

However, the MCP server should not become a giant replacement script. It should be a thin controller over smaller Python services.

The architecture should move from:

```text
skill -> giant script -> provider
```

Toward:

```text
skill -> MCP tool -> focused service module -> provider/kernel state
```

This lets scripts remain valid for direct CLI use while giving harnesses and dashboards a cleaner server-like API.

### 3.5 Providers Are Discovery Substrates, Not Truth

GrepAI and CGC are not competing with onboarding. They answer different kinds of questions:

- **GrepAI / Semantics:** the concept is known but the vocabulary, route, or file is unknown.
- **CGC / Relationship:** the anchor is known but the neighborhood, callers, callees, dependency path, or impact surface is unknown.
- **Onboarding + source / Intent:** the anchor is known but the hidden contract, invariant, behavioral expectation, or human decision needs to be proven.

Provider output is candidate-routing evidence. The proof layer remains source, branch validity, drift checks, verified onboarding, and human-approved memory promotion.

---

## 4. Why This Direction Has Substance

This design is not just an aesthetic refactor. It lines up with several broader patterns in agent infrastructure.

### 4.1 Tool Access Needs A Protocol Boundary

Anthropic introduced MCP as a standard for connecting AI assistants to systems where data lives, including repositories, business tools, and development environments. The stated problem is that powerful models are constrained when isolated from data and each new data source requires a separate integration.[^anthropic-mcp]

The official MCP architecture describes a client-server model where AI hosts connect to MCP servers that provide tools, resources, and prompts. It explicitly separates data-layer primitives from transport mechanics, which maps well to using MCP as a controller boundary rather than burying logic in prompt files.[^mcp-architecture]

### 4.2 Tool Surfaces Must Be Discoverable And Typed

MCP tools are designed to be model-controlled: the language model can discover and invoke tools based on context and the user prompt. Tool definitions include names, descriptions, input schemas, and optional output schemas; tool results can contain both unstructured text and structured JSON.[^mcp-tools]

This supports the design choice to expose typed operations such as `cgc.analyze_complexity` or `grepai.search`, while still preserving native provider transcripts for the model to inspect.

### 4.3 MCP Needs A Narrow Safety Boundary

MCP security guidance calls out risks around local MCP servers, broad scopes, arbitrary code execution, hidden commands, data exfiltration, and loss of visibility. It recommends showing exact commands, requiring explicit approval for dangerous operations, restricting filesystem/network access, using stdio for local servers when appropriate, and designing least-privilege scopes.[^mcp-security]

For Agents Remember, this argues strongly against an MCP tool that accepts arbitrary shell commands or executable paths. The MCP should expose typed, allowlisted operations with server-owned executable paths, server-owned coordination roots, timeout caps, transcript persistence, and read-only operations first.

### 4.4 Skills Are A Valid Teaching Layer

Claude Code’s skill documentation describes skills as reusable instruction units that load when relevant, with supporting files for detailed references and examples. It explicitly recommends moving detailed reference material out of the main `SKILL.md` and linking supporting files so the model knows when to load them.[^claude-skills]

That matches the C-04 design: the main skill teaches the substrate router, while adjacent provider cards teach the high-leverage GrepAI and CGC call patterns.

### 4.5 Retrieval Shape Matters

GraphRAG research argues that naive RAG struggles with global sensemaking questions and that graph-shaped retrieval can improve answers over private corpora by constructing entity graphs and community summaries.[^graphrag]

Agents Remember does not need to copy GraphRAG’s exact pipeline. The useful lesson is that not every question should be flattened into semantic text retrieval. Some questions are semantic, some are relational, and some are intent/provenance questions. C-04’s substrate model formalizes that distinction.

---

## 5. Architecture: Model / Controller / View

A useful way to explain the new architecture is a pragmatic MVC-like split.

### Model

The model layer is the durable and derived state:

```text
source repository
memory repository
onboarding files
route indexes
ledger / branch mapping
provider indexes
provider state
worktree state
drift state
task state
```

### Controller

The controller layer exposes safe operations over that state:

```text
MCP tools
CLI commands
context-packet service
provider query service
worktree service
drift service
task/closeout service
```

### View

The view layer consumes controller outputs:

```text
Codex / Claude Code / Copilot chat
dashboard
TUI
CI output
GitHub bot comments
manual CLI output
```

The important implication: harnesses are views too. They should not need to understand internal folder choreography. They should receive model-ready packets and invoke typed controller operations.

---

## 6. First-Class Kernel Services

The existing skills already define many of the boundaries. The refactor should transport those boundaries into Python service modules and MCP tools.

A possible module layout:

```text
agents-remember-md
  runtime
    src
      agents_remember/
        kernel/
          resolver.py
          context_packet.py
          branches.py
          provenance.py

        controllers/  
          bootstrap_session.py

        worktrees/
          create.py
          status.py
          validate.py
          pairing.py

        drift/
          fast_check.py
          full_check.py
          closeout.py
          actionable_files.py

        providers/
          lifecycle.py
          cgc.py
          grepai.py
          health.py
          transcripts.py
          query_contracts.py

        tasks/
          task_state.py
          closeout_state.py
          roadmap.py

        mcp/
          server.py
          tools.py
          schemas.py
          safety.py

        cli/
          provider_lifecycle.py
          context_packet.py
          worktree.py
```

**controller** can bundle python invocations that happen frequently in the same order into a bundle that gives one structured response. 
Those bundles are simple python wrapper scripts themself and can then also be exposed via MCP.
Most time & tokens are not wasted on python runs themselfs but on model reasoning between tool calls. Bundling reduces cost. 
One of these controllers is described as startup `context packet` later in this document.

The above structure is an example. The individual files are to be discussed. But the structure is much better. And where necessary sub directories
can be introduced to modularise this further where useful.

The goal is not to split large files randomly. Split by domain boundary.

The question for each extracted function should be:

> Which kernel service owns this truth?

Not:

> How can I make this file shorter?

---

## 7. The Context Packet

The highest-leverage first API is a fast startup context packet.

Current cold-start behavior forces agents to rediscover the same sequence:

```text
resolve repo/context
-> check provider lifecycle
-> check watchers
-> check staleness/drift
-> decide what is safe to use
```

The scripts are fast. The agent thinking through the next script call is slow.

This should become a single operation:

```bash
ar-context packet --repo agents-remember-md --format json
```

Or, through MCP:

```text
context.packet(repoId = "agents-remember-md")
```

### Example Shape

```json
{
  "repo": {
    "id": "agents-remember-md",
    "codeRoot": "/home/user/projects/agents-remember-md",
    "memoryRoot": "/home/user/projects/ar-agents-remember-md",
    "branch": "main",
    "worktree": {
      "isWorktree": false,
      "expectedBranch": "main",
      "branchValid": true,
      "dirty": false
    }
  },
  "resolver": {
    "coordinationRoot": "/home/user/projects/ar-coordination",
    "settingsFile": "/home/user/projects/ar-coordination/system/settings.json",
    "resolvedAt": "2026-05-21T18:30:00+02:00"
  },
  "providers": {
    "cgc": {
      "configured": true,
      "healthy": true,
      "watcher": "running",
      "indexFreshness": "fresh",
      "backend": "falkordb",
      "graph": "cgc_agents_remember_md",
      "mayUse": true
    },
    "grepai": {
      "configured": true,
      "healthy": true,
      "watcher": "running",
      "indexFreshness": "fresh",
      "mayUse": true
    }
  },
  "drift": {
    "mode": "fast",
    "status": "clean",
    "actionableFiles": [],
    "requiresOnboardingUpdate": false
  },
  "recommendedAgentMode": {
    "mayUseCGC": true,
    "mayUseGrepAI": true,
    "mustRunFullDriftBeforeCloseout": true,
    "safeFallback": "onboarding-only"
  },
  "warnings": []
}
```

### Fast Packet vs Full Validation

The context packet should not become an expensive closeout validation. It should be a fast trust snapshot.

```text
Fast startup packet:
  resolver
  branch/worktree state
  provider health
  watcher status
  last known drift summary
  stale flags

Deep closeout validation:
  full drift detection
  onboarding update checks
  ledger verification
  source/memory commit consistency
```

Rule:

> On session start, call the context packet. Before memory updates or closeout, call full validation.

---

## 8. MCP API Surface: v1

The v1 MCP should be small, typed, and read-first.

### 8.1 Core Tools

```text
context.packet(repoId)
repo.resolve(repoId)
worktree.status(repoId)
provider.status(repoId)
provider.query(repoId, provider, operation, args)
drift.check(repoId, mode = "fast" | "full")
run_artifact.read(runId, file = "stdout" | "stderr" | "result")
```

### 8.2 Worktree Mutations

Worktree creation is important enough to support, but it should be treated as a higher-trust operation than read-only context queries.

Possible staged rollout:

```text
v1:
  worktree.status
  worktree.plan_create

v1.5:
  worktree.create with explicit arguments and guardrails

later:
  worktree.delete / cleanup only with explicit destructive confirmation
```

Worktree operations matter because parallel work can corrupt external memory if code/memory pairing is not protected. Worktrees are not just convenience. They are a safety boundary for branch-local truth.

### 8.3 Provider Query Examples

CGC complexity:

```json
{
  "provider": "cgc",
  "operation": "analyze_complexity",
  "repoId": "agents-remember-md",
  "args": {
    "limit": 10
  }
}
```

CGC callers:

```json
{
  "provider": "cgc",
  "operation": "analyze_callers",
  "repoId": "agents-remember-md",
  "args": {
    "symbol": "dispatchCommand",
    "file": "src/app/command-router.ts"
  }
}
```

GrepAI search:

```json
{
  "provider": "grepai",
  "operation": "search",
  "repoId": "agents-remember-md",
  "args": {
    "query": "provider stale state",
    "scope": "memory",
    "limit": 5,
    "format": "compact"
  }
}
```

This keeps provider flexibility while avoiding arbitrary shell execution.

---

## 9. MCP Safety Model

### 9.1 Hard Boundaries

The MCP must not become arbitrary shell execution.

Rules:

```text
No caller-provided executable paths.
No raw shell command execution in default tools.
No arbitrary working directories.
No unbounded output.
No destructive operations in v1.
No provider refresh/purge/start/stop until read/status/query are boring.
```

### 9.2 Server-Owned Context

The server owns:

```text
coordination root
provider lifecycle script path
provider runtime paths
allowed repo ids
allowed provider ids
allowed operations
timeout caps
transcript storage root
```

The model provides typed operation arguments, not executable authority.

### 9.3 Transcript Preservation

Provider-native output should be preserved. CGC tables and GrepAI snippets are useful as text; forcing everything into normalized JSON would lose signal.

Every run should persist:

```text
ar-coordination/temp/provider-mcp/runs/<run-id>/
  command.txt
  stdout.txt
  stderr.txt
  result.json
  transcript.txt
```

A normal response can inline the full transcript. Large responses should return head/tail excerpts plus artifact paths.

**Developer - note: What's most important is that the model receives provider outputs in the providers native shape or a safe, simple, and clean equivalent to avoid output corruption.**

### 9.4 Response Shape

```json
{
  "ok": true,
  "kind": "provider-transcript",
  "provider": "cgc",
  "operation": "analyze_complexity",
  "repoId": "agents-remember-md",
  "exitCode": 0,
  "durationMs": 842,
  "truncated": false,
  "stdout": "... native CGC output ...",
  "stderr": "",
  "artifacts": {
    "runId": "20260521T183000Z-cgc-analyze-complexity-abc123",
    "runDir": "/.../temp/provider-mcp/runs/...",
    "stdoutPath": ".../stdout.txt",
    "stderrPath": ".../stderr.txt",
    "resultPath": ".../result.json"
  }
}
```

**Developer - note: Reminder this is an example. Not the final structure. Every single field should have an actual purpose! And we only add fields to ensure present-day features can 
keep operating without breaking. Anything else is extra.**

---

## 10. Provider Capability Cards

The C-04 provider cards should remain beside the C-04 skill.

### Why

The model will usually operate in “user mode,” where providers are tools, not code areas to inspect. If the provider documentation lives only under provider implementation folders, the agent has no strong reason to read it before deciding how to retrieve context.

C-04 is where the model chooses a substrate. Therefore C-04 should also teach the provider affordances.

### Pattern For Future Provider Cards

Every provider card should answer:

```text
Use when:
Do not use when:
Question shape:
Command / MCP operation:
Example output:
How to interpret output:
What to verify next:
Fallback if unavailable:
Output budget guidance:
Freshness/health check:
```

### Rule

> Provider cards teach usage. Provider folders implement behavior.

---

## 11. Dashboard/TUI Implications (not in scope for this task)

A future dashboard should not be a clone of Understand-Anything’s generated graph viewer. It should be a live substrate-composed cockpit.

The visual backbone can come from CGC because relationships provide the spatial map:

```text
files
functions
classes
imports
callers
callees
chains
complexity
blast radius
```

Onboarding then enriches selected nodes with human-reviewed truth:

```text
why this exists
what invariant it protects
what hidden contract applies
what branch/commit verified it
what task decision created it
what should not regress
```

GrepAI provides semantic entry:

```text
concept known, route unknown
vocabulary mismatch
fuzzy domain phrase
memory route discovery
```

Provider health and drift provide trust state:

```text
CGC healthy/stale/watcher down
GrepAI healthy/stale/watcher down
onboarding verified/drifted/missing
worktree branch valid/invalid
task closeout pending/clean
```

This makes the dashboard a view over the context kernel, not a separate analysis product.

---

## 12. Implementation Sequence

### Phase 0: MCP Design Discusion & Scaffolding
- Plan the MCP
- Where does it live in the source repo
- Where does it live once installed?
- How does MCP setup differ from Code Harness to Harness? The agent needs to research that online.
- How can the MCP be made as compatible as possible? 
- How is supposed to be installed and when does it's installation step appear in the run-time installation (discuss full end-to-end installation workflow, who does what, how much is automated? etc.)
- How is the MCP going to be configured. What settings does it actually need to work?

### Phase 1: Context Packet

- Extract resolver/provider/drift summary into focused Python services.
- Add CLI command for `context packet`.
- Keep it fast.
- Do not include full closeout validation.
- Return stable JSON.

### Phase 2: MCP Read Surface

- Expose `context.packet` first.
- Add `provider.status` and read-only provider queries.
- Persist transcripts.
- Keep direct script usage valid.

### Phase 3: Skill Rewiring

- Migrate C-04 toolcalls to use MCP tools.
- Without MCP support access to providers doesn't work. So when providers are configured the MCP is a MUST!
- Keep top-level provider-card navigation in C-04 and add/change when other prividers are being added.
- Teach onboarding/source-only fallback only after MCP/provider failure is reported.

### Phase 4: Provider Lifecycle Expansion (modularised scripts -> controller)

```text
start watcher
stop watcher
refresh provider
hard refresh
purge index
```

### Phase 5: Provider Query Expansion (Operations & Args)

CGC:

```text
find_name
analyze_calls
analyze_callers
analyze_chain
analyze_deps
analyze_complexity
```

GrepAI:

```text
search_compact
search_scoped_project
search_route_scoped
status
```

### Phase 6: Worktree Services

- Move worktree state and creation planning.
- Refactor monolithic code into smaller modules where boundaries are defined by purpose
- Evaluate where code is dead, stale, or duplicated (unnecessary fallbacks) and streamline that
- Combine modules repeatable workflows in Controller wrapper scripts:
  - Code & Memory Worktree status
  - Worktree branch strategies
  - ...

---

## 13. Review Checklist

A strong implementation should satisfy:

```text
A new chat can call one context packet and know the safe operating state.
Provider status is host-side and not dependent on sandbox process visibility.
CGC/GrepAI calls preserve native useful output.
The model cannot pass arbitrary executables or shell commands.
Every MCP run is logged with stdout/stderr/command/result artifacts.
Skills teach when to use tools instead of carrying operational code.
Python logic is importable/testable outside skill folders.
Direct CLI usage remains valid.
No-provider mode still degrades to onboarding-only routing.
Read-only MCP tools work before mutating tools exist.
```

Red flags:

```text
MCP accepts arbitrary command strings by default.
MCP becomes a new 2,000-line monolith.
Skills still contain large operational shell/Python procedures.
Provider output is normalized so aggressively that CGC tables lose signal.
Context packet becomes as expensive as full closeout validation.
Worktree/memory branch pairing is hidden behind implicit behavior.
Provider freshness is assumed instead of reported.
```

---

## 14. Positioning Language

Possible short framing:

> Agents Remember is an agentic context kernel for software repositories. It gives coding agents a branch-aware, provider-aware, human-reviewed way to discover, verify, and maintain project knowledge across sessions, worktrees, and tools.

Slightly more product-facing:

> Agents Remember is not a code graph, vector database, or dashboard. It is the context control plane that lets those systems work together safely.

More architectural:

> Agents Remember exposes stable context primitives: resolver state, branch/worktree truth, memory provenance, provider lifecycle, retrieval substrate routing, drift detection, and approval-gated memory promotion. Harnesses and dashboards can build on those primitives without reimplementing the operating procedure.

---

## 15. Open Questions

- Should the first MCP implementation expose one generic typed `provider.query` tool or separate provider-specific tools such as `cgc.query` and `grepai.search`?
- Should worktree creation be included in the first MCP milestone, or should v1 stay read-only plus planning?
- Should MCP responses return both `structuredContent` and text transcripts for every provider call?
- Should context packet output be versioned as `contextPacketVersion: 1` from the start?
- Should dashboard/TUI consume the exact same packets, or should there be a second view-optimized projection?
- How much of the existing provider lifecycle script should remain CLI orchestration versus move into importable service modules?

---

## 16. Non-Goals

This design does not require:

```text
rewriting every skill at once
moving all Python code before MCP exists
building a dashboard before the kernel APIs are stable
normalizing every provider output to one schema
turning Agents Remember into a code harness
turning MCP into an arbitrary shell bridge
making GrepAI or CGC mandatory dependencies
```

The migration can be incremental. Each extracted service and MCP surface should reduce one repeated orchestration burden.

---

## 17. Summary

The project’s direction has become clearer:

```text
Automated structural documentation
  -> persistent onboarding memory
  -> branch-aware memory/worktree system
  -> retrieval substrate router
  -> provider lifecycle manager
  -> host-side MCP controller
  -> agentic context kernel
```

The next high-leverage step is not adding more prompts. It is exposing the existing operating procedure as stable, typed, host-side controller operations.

The skills remain essential because they teach the model what the operations mean. The MCP makes those operations reliable across harnesses and sandboxes. The Python kernel keeps the implementation testable. Providers remain replaceable drivers. Future dashboards and TUIs become views over the same context state.

That separation is what turns the project from a clever experiment into infrastructure that can “just work.”

---

## References

[^anthropic-mcp]: Anthropic, “Introducing the Model Context Protocol,” 2024-11-25. <https://www.anthropic.com/news/model-context-protocol>

[^mcp-architecture]: Model Context Protocol documentation, “Architecture overview,” version current at time of drafting. <https://modelcontextprotocol.io/docs/learn/architecture>

[^mcp-tools]: Model Context Protocol specification, “Tools,” version 2025-06-18. <https://modelcontextprotocol.io/specification/2025-06-18/server/tools>

[^mcp-security]: Model Context Protocol documentation, “Security Best Practices,” version current at time of drafting. <https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices>

[^claude-skills]: Claude Code documentation, “Extend Claude with skills,” current at time of drafting. <https://code.claude.com/docs/en/skills>

[^graphrag]: Darren Edge et al., “From Local to Global: A Graph RAG Approach to Query-Focused Summarization,” arXiv:2404.16130, 2024. <https://arxiv.org/abs/2404.16130>
