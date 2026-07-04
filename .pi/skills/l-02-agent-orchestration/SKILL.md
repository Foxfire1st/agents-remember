---
name: l-02-agent-orchestration
description: "The developer-invoked agent-orchestration frame: the consistent runtime that houses the orchestration-family jobs (designer, orchestrator, manager, worker, adversarial reviewer). Owns the thin contact points every job shares — context intake, profile-fit job selection, housed job execution, wrap-up — plus the coordination-leaf convention, the escalation ladder, the two adversarial review seams, the gate-delegation doctrine, and the super integration branch topology. Entered only on an explicit developer request; never self-spawning."
---

# l-02-agent-orchestration Agent Orchestration Frame

This skill is the **frame**: the thin, consistent container/runtime that houses the different
orchestration-family jobs. It is **not** an executor and **not** a job itself. Where the
`l-01-session-job-lifecycle` skill is the single-session lifecycle one agent runs, the
`l-02-agent-orchestration` skill is the multi-agent runtime a whole series runs inside — the same
trust checkpoint, the same observability, the same gates and escalation seams, the same durable-artifact
obligations, the same close — with each job's meat living in its own **job file** (the payload).

> **Frame doctrine (design record §"the frame doctrine").** Decomposing the historical
> "Eierlegende Wollmilchsau" build lifecycle (one implicit role doing a bit of everything minus
> orchestration, the developer wearing the orchestrator + manager hats) narrows the lifecycle proper
> down to thin **contact points** every job shares: **context → job selection + execution → wrap-up**.
> The frame guarantees only those sockets; a job's flow takes over from there. Drawn as the **FRAME**
> model on the FlowTab canvas (`dashboard/src/panels/flowModels.ts`), which — together with the
> `designer`, `orchestrator`, `manager`, `worker`, `reviewer`, and `comms` models — is the behavioral
> spec this skill and its job files implement.

## Companion Files

The frame is deliberately thin; the payloads and reusable artifact shapes are companions.

**Job files** (`jobs/<role>.md`) — one per role, each carrying **both axes in one file**: role
(what seat it fills) + lens (how it reads the work), plus a harness-agnostic **knob block**.

1. `jobs/designer.md` — co-think a master task with the developer (task design as its own job).
2. `jobs/orchestrator.md` — portfolio streamlining → dependency-ordered dispatch → super integration branch. **Owns the spirit test.**
3. `jobs/manager.md` — one master; leaf-dispatch loop → master-exit handover.
4. `jobs/worker.md` — one leaf, short-lived, fresh session, mandatory turn report.
5. `jobs/adversarial-reviewer.md` — the two review seams; verdicts are evidence, not decisions.

**Per-harness job variants** (`jobs/<role>.<harness>.md`) — a harness-specific overlay resolved on top
of the portable job file (see "Knob Block & Per-Harness Variants"). Two exemplars ship:

6. `jobs/orchestrator.claude-code.md` — sub-agent fan-out with durable reports on Claude Code.
7. `jobs/worker.claude-code.md` — the AR-mutations-stay-in-the-main-loop rule on Claude Code.

**Report templates** (`templates/<name>.md`) — the report-template library the orchestrator and
reviewers write into; sub-agents fan out and fill these, so the analysis survives compaction.

8. `templates/turn-report.md` — the mandatory worker hand-off artifact.
9. `templates/master-handover-packet.md` — manager → orchestrator, at master exit.
10. `templates/conversation-handover-packet.md` — role takeover + worker respawn onboarding.
11. `templates/verdict.md` — the adversarial verdict (master-exit and super-exit variants).
12. `templates/impact-analysis.md` — planned-vs-planned and planned-vs-past blast-radius.
13. `templates/onboarding-coherency.md` — the onboarding-vs-code review lens.

```
context  ──▶  job selection  ──▶  housed job runs  ──▶  wrap-up
(trust        (profile-fit;        (the job file's        (durable artifact ·
 checkpoint)   wrong profile →      own flow; the frame     land per the job's path ·
               takeover spawn)      stays thin)             lifecycle_end)
```

## Entry Rule — Developer-Requested, Never Self-Spawning

The frame is entered **only** when a developer explicitly asks for it (e.g. *"orchestrate these
masters"*, *"help me think this task through"*). It is the **single entry point** into the
orchestration runtime — no job self-spawns into it, and no agent promotes itself into an orchestrator
or manager seat. The developer talks to **one orchestrator** as the single point of contact; managers
and workers remain directly reachable through their attached chats, but the standing relay is
developer ↔ orchestrator (design decision D15; borrowed vocabulary — see "Credits").

## Contact Point 1 — Context (the trust checkpoint)

Every session that enters the frame — at any seat — runs the same trust checkpoint the
`l-01-session-job-lifecycle` skill owns: resolve the coordination/memory context with the
`context_packet` MCP tool (`include_providers`, `include_drift`, `include_freshness`), report the
packet facts, and make memory/provider trustworthiness a precondition before relying on any analysis.
The frame adds nothing here — it **guarantees** that the checkpoint runs identically for a designer, an
orchestrator, a manager, a worker, or a reviewer, so the whole series is observable from the first
call. `lifecycle_start` promotes a fleeting lifecycle exactly as it does in the single-session case.

## Contact Point 2 — Job Selection (profile-fit against the job registry)

The container picks its payload. The **job registry** (below) names, for each role, the wanted
harness / model / effort profile. Selection is a two-step check:

1. **Which job?** The developer's request and the coordination-leaf role marker (see the
   coordination-leaf convention) name the seat: designer, orchestrator, manager, worker, or reviewer.
2. **Profile-fit?** Compare the current session's harness/model/effort to the job's resolved knob
   block (job file defaults, overlaid by any `jobs/<role>.<harness>.md` variant, overlaid by the
   settings.json orchestration block). **If the profile is wrong** (e.g. the developer opened the run
   in a light/fast profile but the orchestrator job wants a high-effort model), the session does not
   soldier on in the wrong seat — it performs a **takeover spawn**: `spawn_agent_session(<role>)` on
   the correct profile, handing the successor a **conversation-handover packet**
   (`templates/conversation-handover-packet.md`) so the new session onboards from **state, not the
   transcript**. The one handover-packet schema serves master handover, role takeover, and worker
   respawn alike.

> `spawn_agent_session` is the orchestration spawn tool authored in **leaf L2** of this series. Until
> L2 lands it, treat every `spawn_agent_session(...)` reference here as the **contract** a takeover /
> dispatch will call; a run before L2 spawns successors by the interim path the developer directs. The
> frame owns the *doctrine* of the spawn seam, not its implementation.

### The Job Registry (the five roles)

| Role | Seat | Lens (how it reads the work) | Job file |
| --- | --- | --- | --- |
| **designer** | scoped to one master (front of the pipeline) | co-think: meta-question · reframe-before-execution · evidence-first (`tasks/AGENTS.md` as a job) | `jobs/designer.md` |
| **orchestrator** | the FIRST coordination leaf of the series | portfolio bird's-eye: streamline · integrity bulwark · dependency-ordered dispatch · **spirit test** | `jobs/orchestrator.md` |
| **manager** | one coordination leaf per master | one master's leaf loop: dispatch · review · delegated gates · master-exit handover | `jobs/manager.md` |
| **worker** | one leaf enclosure/worktree | the `l-01-session-job-lifecycle` build spine, worker lens | `jobs/worker.md` |
| **adversarial reviewer** | short-lived, spawned at a seam | refute-or-confirm: completion vs docs · code quality · onboarding-vs-code | `jobs/adversarial-reviewer.md` |

The lens vocabulary continues the `l-01-session-job-lifecycle` job lenses (research · triage · bug ·
feature). "Spine unchanged, lens specializes" (borrowed 260619 S8): a worker still runs the ordinary
build spine — the job lens tunes it, it does not fork it.

## Contact Point 3 — Housed Job Execution

From here the **job file's own flow takes over** — see its drawing on the canvas and its
`jobs/<role>.md`. The frame guarantees only the contact points the housed job plugs into:

- **Observability** — the coordination leaf + its `task_doc` and the append-only event substrate; every
  seat has a leaf-attached chat the developer can walk into at any level.
- **Gates** — plan and closeout gates, delegable per the gate-delegation doctrine below.
- **Escalation** — the escalation ladder below; no level skipped.
- **Durable artifacts** — the artifact obligations below; the reporting substrate that survives
  compaction, termination, and clears.

### The Coordination-Leaf Convention

Coordination seats are ordinary `subTask` **leaves with no enclosure** — no worktree, no schema change
(the design record's "coordination leaves are ordinary subTask leaves without enclosures"). A **role
marker** on the leaf distinguishes a coordination leaf from a work leaf so the dashboard can render the
seat. The convention:

- **The first coordination leaf of the series = the orchestrator seat.** At rest it holds no
  enclosure. (It is *not* entirely worktree-less: integrating a completed master into the super branch
  happens in an orchestrator worktree — see the topology summary.)
- **One coordination leaf per manager** — its own seat + attached chat, no worktree.
- **Work leaves are unchanged** — each gets its own enclosure/worktree exactly as a
  `w-02-light-task-workflow` leaf does; a worker attaches it.

The orchestration skill scaffolds the series with this convention via the `task_doc` MCP tool: the
orchestrator seat leaf first, then a coordination leaf per master, then the work leaves under each
master.

### The Escalation Ladder

**worker → manager → orchestrator → developer.** No level is skipped. Each level resolves within its
own view first; only a **stumped orchestrator** — despite the bird's-eye view — raises to the
developer. A worker never escalates straight to the developer; a manager never does either.

The **spirit test governs autonomy at exactly one rung, the orchestrator, and only there.** It is
**orchestrator-only** (design correction 2026-07-04): a change that stays within the spirit of what the
developer accepted → the orchestrator acts on its own with a decision-log entry; a necessary change
that goes *against* that spirit → the orchestrator raises it for a joint decision with the developer.
Managers and workers are given **no** creative-liberty prompting in either direction: the **default
agent behavior stands** — fulfill the task, fill small blanks — and any plan delta beyond
blank-filling **escalates** to the orchestrator (which alone holds the global view needed to judge a
collision). This asymmetry is deliberate; do not port the spirit test down the ladder.

### The Two Adversarial Review Seams

Adversarial review is spawned at **exactly two seams** (developer decision 2026-07-03), never
per-leaf. Leaf-level review is the manager's own duty, not an adversarial seam.

1. **Master-exit** — before a manager hands its completed master integration branch to the
   orchestrator.
2. **Super-exit** — before the orchestrator hands the super integration branch to the developer.

Each seam spawns an `adversarial-reviewer` job over three lenses (completion vs task docs · code
quality per `system/tools.md` · onboarding-vs-code = paired `read_ar_files` + `memory_quality_check` +
drift). The verdict is a **templated artifact** (`templates/verdict.md`) that attaches to the handover
gate as **judge evidence** — it is **evidence, never a decision**. A **blocking verdict must decompose
into fix leaves** (concrete, leaf-shaped findings the owning manager/orchestrator can dispatch), never
prose-only complaints.

### The Gate-Delegation Doctrine (enforcement is L4)

Leaf plan/closeout gates become **delegable to a configured role** — a manager decides its leaves'
delegated gates; the orchestrator's seam gates may require a reviewer verdict first. The invariant is
**sharpened, not weakened**: *the owning agent never self-approves; a distinct, configured role may.*
Delegated decisions are **attributed** (`decidedBy: <manager lifecycle>`, `decidedVia: orchestration`)
and dashboard-visible. Human review concentrates at the **master and/or super integration branch** (+
push), where UX changes are reviewable as whole behavior; developer rejections flow to the orchestrator,
which decomposes them into fix leaves.

> **This skill describes the gate-delegation doctrine; it does not enforce it.** The kind-generic gate
> policy, the auditable judge rung, and the delegation attribution are implemented in **leaf L4** of
> this series (gate policy + judge rung). Until L4 lands, gates behave as the
> `l-01-session-job-lifecycle` skill defines them (the owning agent hands off to the developer); the
> delegation described here is the *target* behavior a policy will unlock.

### Artifact Obligations (per role)

The reporting substrate is what makes the runtime survive compaction, session death, and clears. Each
role carries a distinct obligation:

- **Worker → a MANDATORY turn report** (`templates/turn-report.md`) at every hand-off: what / issues /
  solved / left, doubling as the respawn-onboarding state. A missing report is **nudged**.
- **Orchestrator → durable notes + reports** — its own artifacts are the most important in the system
  (only it sees the whole picture) and must survive compaction, termination, and clears. Fan-out
  sub-agents **write templated report artifacts**; **AR state mutations** (`task_doc`, gates, spawn,
  closeout) stay in the **main loop** (design addendum item 5 — the developer overrule of the
  read-only phrasing).
- **Manager → the master-handover packet** (`templates/master-handover-packet.md`) at master exit.
- **Reviewer → a verdict artifact** (`templates/verdict.md`) at each seam.

## Contact Point 4 — Wrap-Up

The frame closes every job the same way: the durable artifact (turn report / notes / packet / verdict)
is written, the work lands per the **job's own path** (a worker closes out + integrates its leaf; a
manager hands its master over; the orchestrator lands super → main), and `lifecycle_end` records the
terminal state. Continuity always lives in the `task_doc` + durable artifacts, **never in the
transcript** — which is exactly why short-lived workers and reviewers are safe.

## Knob Block & Per-Harness Variants

Job files are **model-interpreted markdown, never an executor** (borrowed D6). Each job file carries a
portable **knob block** (borrowed D7) — the harness-agnostic defaults the terminal host injects at
spawn (the same seam as the planned analytics env injection):

```md
## Knobs
| Knob    | Default        | Notes                                            |
| ------- | -------------- | ------------------------------------------------ |
| harness | <harness-id>   | portable default; a variant file may override    |
| model   | <model-class>  | reasoning weight the seat wants                   |
| effort  | <low|med|high> | thinking budget                                  |
| tools   | <tool profile> | the tool surface the seat is allowed / expected  |
```

**Per-harness variant resolution** (borrowed D12): a `jobs/<role>.<harness>.md` file is an **overlay**
on the portable `jobs/<role>.md`. Resolution order when a session on harness `H` selects role `R`:

```
jobs/<R>.md  (portable base: role + lens + duties + portable knobs)
   └─ overlaid by jobs/<R>.<H>.md   when present   (harness-specific knobs + harness idioms)
        └─ overlaid by settings.json orchestration block   (machine/user override — see below)
```

The variant carries only what is harness-specific (the concrete model id, the sub-agent/fan-out
mechanic, tool-surface specifics); it never restates the role's duties. Two exemplars ship
(`jobs/orchestrator.claude-code.md`, `jobs/worker.claude-code.md`); other roles/harnesses fall through
to the portable base until a variant is authored.

## Comms Protocol (channels — see the COMMS canvas model)

Three channels compose (all existing or near-existing primitives):

1. **Inbox = the durable queue.** The `operator_inbox_post` / `operator_inbox_poll` /
   `operator_inbox_consume` MCP tools, generalized to agent→agent addressing (by lifecycle/agent
   identity); every message is durable and dashboard-visible.
2. **Stdin push = delivery.** Serving injects the message (or a mail hint) into the target session's
   PTY via the echo-confirmed paste machinery — no poll loop for AR-hosted sessions; polling remains a
   fallback for non-hosted agents.
3. **Turn-report artifacts = reporting.** Every hand-off leaves a reviewable artifact (the templates
   above) that survives compaction and session death.

**Nudging** rides trustworthy inactivity signals (heartbeat-aware, provably-stopped) plus
missing-artifact detection → a rate-limited, logged manager stdin nudge. **Escalation** flows up the
ladder above through the inbox. The comms substrate (inbox push + artifacts + nudges) is implemented in
**leaf L3** of this series; this skill describes the protocol it realizes.

## The Super Integration Branch Topology (summary — see the design record + L5)

The git topology is an **accumulative** super integration branch, owned by the orchestrator:

```
main
  └── super-integration (orchestrator-owned, based off main)
        ├── master-A integration branch (based off super @ t0) ── leaves land via C-11
        ├── integrate A → super  (orchestrator worktree, source = super, C-11)     @ t1
        ├── master-B integration branch (based off super @ t1 → sees A's results)
        ├── integrate B → super                                                    @ t2
        └── … final: super → main PR (remote merge) + memory carry-over to main + push
```

Master integration branches base off the **super** branch, so it accumulates. Dependency-ordered
dispatch: when master B depends on master A, A's manager is dispatched first; B's is dispatched only
after A is integrated into super. Independent masters may run in parallel off the same super base; the
`c-11-memory-carryover-from-branch` skill's reconcile absorbs a moved super base. **C-11 is the
universal integration mechanic at every level** (leaf→master, master→super, super→main). Every
integration edge carries its C-11 memory carry-over so the ledger maps each accumulated commit.

> This is a **summary**. The full topology, the orchestrator worktree flow, the parallel-conflict
> maneuver (up-front foundation-master extraction vs post-hoc super-branch remediation), and the
> memory single-siding rule are owned by **leaf L5** and the design record
> (`260703_agent-orchestration/notes/design-agent-orchestration.md`, §"the git topology" + addenda).

## settings.json Orchestration Block (schema documentation — parsing is deferred)

Machine/user overrides layer **over** the job-file defaults. They live in the **MCP authority settings
file** (see `docs/reference/settings-json.md`, "MCP Authority Settings"), not the memory
`system/settings.json`. **Precedence: job-file defaults < settings.json** — the settings block wins.

> **Schema only.** The parsing, validation, and injection code for this block is **deferred to leaf
> L4** (with the gate policy). This section documents the *shape* a later leaf will parse; nothing in
> this skill reads it yet.

```jsonc
{
  // ... existing MCP authority settings (coordinationRoot, repositories, providers, ...) ...
  "orchestration": {
    "roles": {
      // role → knob override, layered over the resolved job-file knob block
      "orchestrator": { "harness": "claude-code", "model": "<model-id>", "effort": "high" },
      "manager":      { "harness": "claude-code", "model": "<model-id>", "effort": "medium" },
      "worker":       { "harness": "codex",       "model": "<model-id>", "effort": "medium" },
      "designer":     { "harness": "claude-code", "model": "<model-id>", "effort": "high" },
      "adversarial-reviewer": { "harness": "claude-code", "model": "<model-id>", "effort": "high" }
    },
    "concurrency": {
      // caps that bound fan-out; the orchestrator/managers dispatch within these
      "maxParallelMasters": 2,   // independent masters running off the same super base
      "maxParallelLeaves":  3,   // parallel workers within one master
      "maxSubAgents":       4    // fan-out sub-agents writing durable reports per seat
    },
    "gateDelegation": {
      // pointer to the L4 gate policy — this block names the policy; L4 enforces it
      "policy": "manager-decides-leaf-gates",  // owning agent never self-approves
      "requireReviewerVerdictAtSeams": true     // seam gates bind on a judge verdict
    }
  }
}
```

- `orchestration.roles.<role>` overrides the resolved knob block (job base < variant < settings).
- `orchestration.concurrency.*` are non-negative integer caps; `0` means unlimited, matching the
  `timeoutCaps` convention.
- `orchestration.gateDelegation` is a **pointer** to the L4 gate policy — it declares the delegation
  intent and the seam-verdict requirement; the enforcement is L4's.

## Credits

This skill and its job files adopt vocabulary and structure from the parked
`260619_agentic-control-plane` design spec — jobs as model-interpreted markdown (D6), the knob block
(D7), role + lens in one file (D10), the ambient-singleton child-in-own-MCP-process rule (D11),
per-harness job variants (D12), the judge actor rung, short-lived workers with structured handoff, the
orchestrate lens, and dev-talks-to-one-orchestrator (D15). That spec in turn credits **Archon** and the
**agent-control-plane** project for the orchestration vocabulary (D14); that credit carries forward
here.

## Relationship To Other Instructions

This skill extends — never replaces — the coordinator `AGENTS.md`, the `l-01-session-job-lifecycle`
skill (which every housed job's build spine still runs), the `w-02-light-task-workflow` skill (the task
format the series is scaffolded in), and the memory layer. Read each `jobs/<role>.md` for the housed
job's flow, and the `templates/` for the artifact shapes.
