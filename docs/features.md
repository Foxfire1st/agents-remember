# Memory your coding agent can trust

Coding agents are good at local edits and bad at remembering why those edits are
dangerous. They re-read the same files every session, rediscover the same
constraints, and produce clean, plausible changes that quietly break a rule
nobody wrote down. The knowledge that would have stopped them — the invariants,
naming contracts, migration scars, cross-repo edges, and "this looks safe but is
not" facts — lives in people's heads, old PRs, and team habits, exactly where an
agent never looks.

Agents Remember turns that knowledge into durable, git-verified infrastructure:
Markdown cards and JSON knowledge in an external memory repository, with code
anchors checked against the selected Git tree before they are trusted. One
idea carries the whole system — **a source file's memory lives at a
deterministic mirror path** — and almost everything else on this page is what
that single idea makes cheap, verifiable, and safe.

This page walks through what that buys:

- **Notes retrieved by path, not by search** — a file's memory has a fixed
  address, so reads stay predictable and the context window never fills with
  loosely-related matches.
- **Staleness you can measure** — recorded anchors are compared with the
  selected code tree, so changed source is visible before a claim is trusted.
- **Search that helps *find* memory without ever becoming the memory** — semantic
  and code-graph lookup were optional accelerators layered over the same
  reviewable Markdown; both providers are retired, so by-path reads carry the
  product.
- **Memory that moves through Git like code** — isolated worktrees, an
  paired publication of code and memory, and a disposable knowledge index
  rebuilt from each selected text tree.
- **Behavior that travels with the repo** — each project's own settings,
  commands, conventions, and landing rules ride along in the memory and are read
  the same way across different coding harnesses.
- **One-call setup across harnesses** — one MCP call starts Bootstrap, while
  canonical skills remain package-served resources.
- **Operational guardrails for real teams** — authority settings, baseline
  adoption, branch memory carryover, cross-repo gates, benchmarks, and source
  quality checks round out the happy path.

## Table of Contents

1. [Why agents need this](#why-agents-need-this)
2. [The core idea: the path is the address](#the-core-idea-the-path-is-the-address)
3. [Memory you can trust: deterministic drift and a git-verified ledger](#memory-you-can-trust-deterministic-drift-and-a-git-verified-ledger)
4. [Three ways to reach knowledge](#three-ways-to-reach-knowledge)
5. [Isolated worktrees that keep memory main clean](#isolated-worktrees-that-keep-memory-main-clean)
6. [Cheap isolation: indexes are cloned, not rebuilt](#cheap-isolation-indexes-are-cloned-not-rebuilt)
7. [How it works in a session: judgment up front, determinism underneath](#how-it-works-in-a-session-judgment-up-front-determinism-underneath)
8. [The architecture: the model reasons, the server keeps the books](#the-architecture-the-model-reasons-the-server-keeps-the-books)
9. [Repo-owned behavior: the `system/` folder](#repo-owned-behavior-the-system-folder)
10. [Harness-native setup](#harness-native-setup)
11. [Operational guardrails](#operational-guardrails)
12. [How the pieces fit](#how-the-pieces-fit)
13. [Learn more](#learn-more)

## Why agents need this

A top-level instruction file helps, but it does not reappear when the agent is
deep inside a file deciding what to change. So the rule that mattered is out of
context at the exact moment it is violated.

The common alternative — pour the codebase into a vector store and retrieve
"relevant" chunks — trades one problem for a worse one. An embedding index is a
*second* representation of the code that has no built-in relationship to the
commit it was built from. It drifts the moment the code changes, it answers with
ranked guesses rather than addresses, and nothing tells the agent when a hit is
stale. It is confidently wrong, confidently fast, and it becomes a parallel
source of truth that no one reviews.

Agents Remember takes the opposite stance. The durable memory of record is
Markdown cards, JSON sidecars and shared knowledge records under Git: versioned,
diffable, reviewable, and anchored to the code. Semantic and graph search were
optional accelerators for *finding* a note — both are retired now, and neither was ever the thing you trust. What
you trust is the note, and whether Git says it still matches the code.

## The core idea: the path is the address

Durable memory is stored as **onboarding units** — Markdown notes derived from
source paths. A source file maps to a mirrored note inside its repository's memory
repo:

```text
src/foo/bar.ts
ar-coordination/memory-repos/ar-my-app/onboarding/src/foo/bar.ts.md
```

Each note explains what the code cannot make obvious on its own: its **purpose**,
important **logic**, **conventions**, **invariants and boundaries**, and durable
file-level concerns. In converted memory, the Markdown holds current prose and
numbered citation markers. Its paired `onboarding/<source path>.json` sidecar holds
references, realization claims and test proofs with code anchors. Route overviews
have their own `overview.json` reference sidecars.

Shared truths live once in `knowledge/<kind>/<ID>-<slug>.json` records: invariants,
families, decisions and other knowledge. Git records their meaning changes;
Curator-authored `knowledge/history/<owner>.json` rows record judgments about a
leaf's changes. A recomputed worklist and exact-tree gate check that those judgments
answer the candidate's changes before publication. The [knowledge-system
reference](reference/knowledge-system.md) describes the files, reader, review and
commands. Notes retain current truth without duplicating code or task plans.

Because the path *is* the address, three things that are normally search problems
become arithmetic:

- **Retrieval** — an agent holding a file knows its note's location directly. No
  ranking, no embedding threshold, no index lookup. Reads stay predictable, and
  unrelated-but-similar material never floods the context window.
- **Reverse lookup** — a note's path names the exact source file it describes, so
  any path that finds a note also finds the code.
- **Coverage** — a missing note or an orphaned one (its source file is gone) is
  visible from the path alone.

This also changes how memory scales. The cost of *reading* memory is proportional
to the files in scope for a task, not to the size of the repository. A large repo
can accumulate a great deal of memory; the agent still only reads the repo
overview, the relevant route-local overview if one exists, and the notes for the
files it is touching.

Durable memory comes in three shapes, matched to scope. **File-level notes** are
the one-to-one core. **Route overviews** (`overview.md`) orient a whole area — a
package, a module, or the repository itself — describing the shape of a region
rather than a single file. And **entity catalogs** (`entities.md`) track the
recurring entities and naming contracts that span many files at once. Overviews
and catalogs add structure on top; they never replace the file-specific facts
beneath them.

None of this needs Docker, a server process, or any provider. By-path memory is
always on. Everything that follows layers on top of it.

## Memory you can trust: deterministic anchors and commit attribution

A note is only useful if you know whether it still matches the code. Converted
cards keep prose in Markdown and code references in their JSON sidecars. The
checks compare those anchors with the code rather than a last-verified commit.

**Reference anchors bind both blob and content.** A code or test reference is
current only when its recorded blob matches the exact paired code tree and its
locator resolves there to the recorded content identity. An edit elsewhere in the
file changes the blob: even unchanged located bytes leave `stale:blob` debt until
the blob is re-recorded. A uniquely moved identical range needs its locator and
blob refreshed; changed or ambiguous content, an absent path or an unresolved
locator needs Curator judgment. The drift summary reports current and stale
reference counts and names unreadable sidecars and validator findings separately.
An inline onboarding marker can still be recognized, but it does not provide the
converted sidecar's anchor checks or a promise to strip note text from a digest.

Realization and proof entries use a separate currentness rule at the exact
selected code tree: an entry can remain current when its blob changed but its
resolved content is unchanged. Their states are:

- `current` — the blob matches or the resolved content is unchanged
- `stale` — content differs, the path is absent or the locator no longer resolves
- `unverifiable` — the requested tree or needed object cannot be read, or a changed
  blob's locator cannot be re-resolved; the answer names the reason

An invariant is stale if any entry is stale; otherwise it is unverifiable if any
entry is unverifiable, unrealized if it has no realization entries, and current
otherwise. These are mechanical observations, not a judgment that the prose is
correct. Stale knowledge stays visible with its state and reason, so it can inform
investigation without silently becoming current truth.

**Broader units carry references too.** An overview's `overview.json` holds the
anchors supporting its prose. Its currentness follows those references rather
than a blanket directory diff. Route indexes are regenerated to describe the
area's structure; that maintenance is distinct from checking reference anchors.
Entity catalogs retain their separate fingerprint check over the files defining
each entity.

**Commit attribution keeps memory and code paired.** Memory lives in a separate
repository, so the pairing must be explicit. Each attributed memory-content commit
carries a `Code-Commit: <sha>` trailer in its hashed Git object. The ledger mapping
is derived from those commits, newest first. An ignored `memory.md` cache can show
that mapping, but supplies no authority and is excluded from memory commits. This
table is a view of the attribution:

```text
| Code commit | Memory commit |
| ----------- | ------------- |
| a1b2c3…     | f9e8d7…       |
| 9f8e7d…     | 4c5b6a…       |
```

The commit-derived mapping has real consequences. It lets a branch carry the memory
version that matches its code state, it lets you restore memory to the exact
version that was synchronized with an earlier code commit — invaluable for
recovering from a bad state — and it lets the system *refuse* to hand over memory
that does not match the code in front of you, rather than guess. Commit attribution
gives external memory its operational power, and external is the only
supported topology: a repo-local `ar-memory/` root was removed from the product.

## Three ways to reach knowledge

By-path is the core, but an agent often needs to *find* the right file before it
can read its note. Agents Remember offers three retrieval substrates, matched to
what the agent already knows, and a router that picks among them.

- **By path** — the agent has a file in hand; the note is at its mirror address.
  Always available, needs nothing extra.
- **By meaning** — the concept is known but the file is not. A semantic search
  over the path-mirrored *onboarding* returned a note whose path then named the
  exact code file. Its provider is retired, so that route has no engine now.
- **By relationship** — an anchor is known but its connections are not. A
  code-relationship graph answered callers, callees, dependencies, complexity,
  and symbol lookups around that anchor. That provider is retired too.

The router chooses the *shape* of the missing context first, then the cheapest
substrate that fits:

- `Semantics` when the concept is known but the structure or location is not.
- `Relationship` when an anchor is known but its impact paths are not.
- `Intent` when an anchor is known but its hidden contracts, invariants, or
  branch-valid truths are not — answered by onboarding plus bounded source
  confirmation.

These compose. A triage task can start from an anchor in a ticket, use the
relationship graph to map nearby structure, then switch to intent to learn the
code truths that make a change safe. Both provider-backed substrates are
retired, so the router works on by-path and intent alone.

The discipline that holds it together: provider output is *candidate routing
evidence, not proof*. Source files, verified onboarding, drift checks, and
approved promotion remain the truth controls. The retrieval layer can never
quietly become a second, drifting source of truth — because it is not the source
of truth at all.

## Isolated worktrees that keep memory main clean

When work needs isolation, a task can run in its own Git worktree off a private
branch, so the main branch is never touched until the change is reviewed and
ready. With external memory this becomes a **dual worktree**: one worktree off
the code repo and one off the memory repo. Code *and* memory changes happen on
private branches together, and the memory main branch stays uncorrupted while a
feature or refactor is still in flight.

Commit attribution makes this safe rather than chaotic:

- **Start selects memory by commit attribution.** Creating a worktree derives the
  ledger mapping from memory commits for the code base commit. If that commit is
  unmapped, start blocks rather than substituting whatever is on memory main.
  A squash merge can create a new code commit that needs attribution first.
- **Closeout commits in a fixed order.** The candidate's judged memory is checked
  before either commit. Code commits first; the exact prepared memory tree is
  checked again before the memory-content commit, which carries its code
  attribution. Converted cards are not stamped with verification commits, and
  `memory.md` is refreshed as an ignored cache after the memory commit.
- **Integration is transactional across both repos.** Landing advances code main
  and memory main as a single all-or-nothing step. A failure on the memory side
  rolls the already-advanced code repo back too, so main never holds code without
  its matching verified memory.

This is what "memory as a first-class citizen" means in practice: memory is
protected by the same branch, review, fast-forward, and rollback machinery as
code — versioned Markdown and JSON moving through Git.

## Task isolation and disposable indexes

Each task's paired code and memory worktrees isolate its changes. The former
semantic and code-graph providers are retired: worktree setup does not provision
their stacks, clone their databases or seed their watchers.

The current knowledge index is an SQLite view derived from a selected memory text
tree. It is cached by tree identity outside Git working trees, rebuilt when absent
and evicted by age and size. Knowledge writers change the text; they do not write
or merge this disposable index.

## How it works in a session: judgment up front, determinism underneath

Memory is only as good as the discipline that keeps it honest, and that
discipline is the second half of the product. Sessions route by role through
one skill (`l-01-agent-lifecycles`): a role agent follows its
handover, and a developer-facing session is **free chat**. It answers
a question inline and, for work on a managed repository, creates or resolves the sprint
document, writes the request of the developer into it, starts an Architect with
`role_start` (the call for an Architect names no document), and tells the developer that
the agent is in the dashboard and which sprint document holds the request. Another role is
started this way only when the developer names the role and the task. The resulting architect runs:

```text
request → trust-checkpoint → reframe-research → decide → build → close
```

Before any code changes, the agent resolves context, checks drift, reframes the
request into the best version of the task, gathers evidence, and waits for the
developer to agree on that framing. Only then does it decide *how* to build:

- a **research-only** answer that changes no code, or
- a **durable task** with its own plan, checklist, and decision log — chat is
  never a build route, so even small code work takes the minimal
  [leaf-task](../skills/l-01-agent-lifecycles/templates/leaf-task.md) artifact, and larger work escalates to a master +
  light sub-task series.

Two principles run through the whole arc. The first is **evidence-first
reasoning**: for anything where correctness depends on interpretation, the agent
makes its evidence model visible — what it read, what it searched, and what would
prove the plan correct — rather than asserting conclusions. The second is that
**approval is not one switch but several**. Implementation approval is not commit
approval; commit, push, PR, merge, cleanup, and memory carryover are each their
own explicit gate. The blast radius of any single "looks good" stays bounded to
one action.

Crucially, onboarding records *approved current state, not plans*. Task notes,
proposals, and in-progress thinking stay in task files. Curation follows approved
code changes and prepares current cards and anchors before publication and
landing; the exact code and memory candidate is checked at publication. That
separation is what lets an agent reason from onboarding without wondering whether it describes reality or merely an intention.

## The architecture: the model reasons, the server keeps the books

The split of responsibility is the design thesis. The model does the judgment —
framing the problem, surfacing assumptions, comparing options, asking for the
right approvals. The deterministic work is offloaded to an MCP server so the same
repo state always yields the same result, and so the agent *cannot* improvise its
way around a gate.

That server is a single process exposing a focused set of typed tools, and the
boundary between them is the trust boundary:

- **Read-only tools** orient on a repository, check drift and preview closeout
  without changing working-tree files, the Git index, local branches or task
  documents. They may write the permitted caches, captured objects and reports;
  an explicitly requested freshness read may fetch the configured upstream. The
  [read boundary](architecture.md#what-a-read-may-write) defines those writes.
- **Mutating tools** — anything that writes Git history — are commit-gated. They
  run only behind an explicit intent note and a paired preview, and the preview
  is literally the apply path with the commit switched off. What you approve in a
  preview is exactly what runs.

Every tool result is shape-checked against a declared model and carries an
accurate token count, so the agent gets predictable, budget-aware payloads it
does not have to parse defensively. A one-call context packet replaces a dozen
exploratory probes at the start of a task. The point of all of this is auditability:
context resolution, drift classification, commit sequencing, and Git surgery live
in reviewable code, not in a prompt — which is what makes the memory layer
reproducible rather than "an LLM with good intentions."

## Repo-owned behavior: the `system/` folder

Every memory repo ships a committed `system/` folder — the repository's own
operating manual, versioned right next to its memory. It holds the storage and
path rules that decide which files get notes, a registry of where the project's
documentation lives, the exact lint / type-check / test / build commands for the
repo, its coding guidelines, its landing-and-publication (`task-publication`) flow, and the
template for how a change should be reported.

These files are **primitives, not skills**. The generic skills and the session
lifecycle read them at the moment each one matters: the build step runs the checks
from `tools.md` before every commit, the landing step follows `task-publication.md`
before any push, and a finished change is reported in the shape the repo's
template defines. A repository tailors how an agent works on it — its commands,
its branch policy, its code-shape budgets, its report format — by editing plain
Markdown, never by writing a custom skill for a specific harness.

Two properties make that pay off. First, it **travels with the memory**: because
`system/` is committed in the memory repo, every contributor who resolves that
memory inherits the same instructions automatically — their agent picks up the
project's conventions with no per-person configuration. Second, it is **portable
across harnesses**: the same skills-and-instructions contract installs on Claude
Code, Codex, Cursor, and the rest, so the same `system/` folder yields the same
behavior whichever coding agent a contributor brings. Workspace-wide defaults can
live in the coordinator's own `system/` folder; where the two overlap, the
repository's own rules win.

## Harness-native setup

Register the MCP server with Claude Code, Codex, Cursor, Antigravity, VS Code
with GitHub Copilot, Hermes, Pi.dev or OpenClaw using that harness's install guide.
The server serves canonical skills from its packaged resource tree.

From your own chat, make one `role_start(role="bootstrap", request_id=<UUID>)`
call. It prepares a missing runtime and host and starts an instructed Bootstrap
seat; its answer names the seat and the dashboard/chat where setup continues.
Bootstrap previews settings, initializes or reads the external memory scaffold,
and hands optional onboarding and the knowledge foundation to a user-started
Architect's regular task. No harness or coordination-root skill copy, renderer,
provider indexing or setup-authored knowledge is part of this path.

This is also why the setup is portable. A contributor can use Claude Code while
another uses Codex or Cursor, and both resolve the same committed memory,
`system/` rules, and MCP tool surface. The harness changes the MCP registration
format; it does not change what the repository asks the agent to do.

## Operational guardrails

The main loop is by-path memory, drift checks, and approval-gated updates. Real
repositories also need the edge-case machinery that keeps that loop from
becoming fragile:

- **MCP authority settings** declare which repositories the server may touch
  (old provider keys are reported once as unused), where transcripts go, which
  timeout caps apply, and how configured roots stay inside allowed workspace
  paths.
- **Memory baseline adoption** lets an existing external-memory repo become the
  first ledgered baseline only after drift/status review and explicit acceptance
  when the memory is not proven current.
- **Branch memory carryover** imports richer onboarding from a source branch
  only after the matching code has landed, using evidence such as exact commits,
  patch-id matches, or final-content matches instead of copying stale branch
  memory wholesale.
- **Branch-gated cross-repo context** can include selected sibling-repo memory
  only when configured branch and ledger checks say that context matches the code
  being reasoned about.
- **Benchmark tools** prepare paired source-only and memory-enabled Codex runs,
  capture JSONL results, and summarize metrics so claims about the memory layer
  can be checked instead of waved through.
- **Source quality tooling** runs repository-owned checks. Deterministic hooks
  check generated copies, Ruff lint and formatting, and Pyright; dashboard changes
  also have their own generation, lint and type checks. Focused development tests
  and explicitly requested Dagger certification are separate lanes. Coverage and
  production CRAP scores are diagnostic: CRAP 20 prompts review rather than
  failing delivery. Lint, formatting, typing, structural rules, test failures and
  diagnostic-tool errors still enforce; Radon reports complexity and
  maintainability without a score gate.

## How the pieces fit

The whole system hangs off one invariant and compounds from there:

- The **address rule** makes a known file's note directly reachable; anchor
  comparisons measure currentness against the code.
- **Commit attribution** pairs memory with code across separate repositories.
- **Dual worktrees** quarantine in-progress memory so main is never corrupted.
- The **disposable knowledge index** is rebuilt from the selected text tree.
- The **MCP server** makes every operation deterministic and gated.
- The **lifecycle** sequences all of it behind human judgment and explicit
  approvals.
- The **`system/` folder** carries each repo's own rules with its memory, so the
  same generic skills behave the way that project wants — across harnesses.
- **Package-served skills** expose the same memory system as MCP resources
  through the registered tool server.
- **Operational guards** cover authority, baselines, carryover, cross-repo
  context, benchmarks, and quality checks when the simple path is not enough.

The memory layer is intentionally small — Markdown, JSON, Git metadata, and
deterministic paths. The workflow layer around it can be heavier, but it exists
for one reason: to protect that small, trustworthy memory from stale or
speculative content. That is the bet — that durable, verifiable, by-path memory
beside the code, kept honest by Git, is what lets a coding agent finally remember
what your team already knows.

## Learn more

- [Concepts](concepts.md) — onboarding units, memory roots, drift, and approval
  gates.
- [Architecture](architecture.md) — runtime, coordination, and memory
  repos.
- [Providers](guides/providers.md) — the retired semantic and code-graph
  providers, and how leftover state from an older install is cleaned up.
- [External Memory](guides/use-external-memory.md) — separate memory repos and
  the ledger.
- [Workflows](workflows.md) — the agent lifecycles and their build modes.
- [Install Guides](install/README.md) — MCP registration and first-run setup.
- [Settings Reference](reference/settings-json.md) — MCP authority settings.
- [Getting Started](getting-started.md) — set it up in your own workspace.
