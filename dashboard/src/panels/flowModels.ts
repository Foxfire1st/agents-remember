// The flow-model registry for the FlowTab canvas: each model is one drawable lifecycle design,
// rendered by FlowTab's segment renderer and switched by its nav. Models are STATIC design
// artifacts (no store reads) — the canvas is where a lifecycle is drawn, reviewed with the
// developer, and kept in step with the doctrine (visuals ride every doctrine change). The drawn
// truth is the unified l-01-agent-lifecycles skill: ROUTER + per-role lifecycles; the retired
// FRAME and BUILD-JOB models died with the l-01/l-02 convergence. `current` = wired today;
// `proposed` = doctrine not yet exercised end-to-end.

export type Status = "current" | "proposed";

export interface FlowStart {
  kind: "start";
  label: string;
  next?: string;
  nextStatus?: Status;
}

export interface FlowNode {
  kind: "node";
  phase: string;
  tool: string;
  detail?: string;
  /** The gate whose notification/approval rides this call (renders the amber left-bar + rider line). */
  rides?: string;
  /** Override the rider line (the default describes the auto-fired turn-end notification). */
  ridesNote?: string;
  next?: string;
  nextStatus?: Status;
}

export interface FlowRundown {
  kind: "rundown";
  title: string;
  lines: { line: string; junction?: boolean }[];
}

export interface FlowDivider {
  kind: "divider";
  label: string;
}

export type FlowSegment = FlowStart | FlowNode | FlowRundown | FlowDivider;

export interface FlowModel {
  id: string;
  /** Short nav label. */
  label: string;
  /** Header title after "LIFECYCLE FLOW · ". */
  title: string;
  takeaway: string;
  segments: FlowSegment[];
}

// --- the router (the unified skill's spine) ------------------------------------------------------

const ROUTER: FlowModel = {
  id: "router",
  label: "Router",
  title: "router — one skill, one lifecycle per agent type",
  takeaway:
    "l-01-agent-lifecycles routes every session by EXACTLY three conditions: AR_SPAWN_ROLE set → " +
    "run that role's lifecycle; else a role brief as first message → that role (the brief IS the " +
    "session start); else the session is developer-facing → the ORCHESTRATOR. Edge cases are " +
    "decided: an unresolvable role value falls through to the brief; a brief that never arrives " +
    "means announce-and-wait, never improvise. The invariant ladder binds every path: approved " +
    "task doc → branch (intent) → worktree only where something is built — and chat is never a " +
    "build route.",
  segments: [
    { kind: "start", label: "▸ a session boots — which lifecycle am I?", next: "condition 1", nextStatus: "current" },
    {
      kind: "rundown",
      title: "the three conditions — in order, no fourth entry",
      lines: [
        { line: "1 · AR_SPAWN_ROLE set → run roles/<value>.md   (designer = the same hat in another chair)" },
        { line: "⟁ unresolvable value → fall through to condition 2 · no brief arrives → announce on the inbox and WAIT", junction: true },
        { line: "2 · first message is a role brief (templates/*-brief.md shape, or `ROLE BRIEF — <role>`) → that role" },
        { line: "3 · otherwise: developer-facing session → roles/orchestrator.md (solo = the jobs with hats collapsed)" },
      ],
    },
    { kind: "divider", label: "— the event loop (orchestrator): route each event into a job —" },
    {
      kind: "rundown",
      title: "jobs — Design · Portfolio · Orchestrate (+ research-only exit)",
      lines: [
        { line: "no task doc for the ask → JOB D — pull the designer hat (no git surface)" },
        { line: "docs exist, coherence/order in question → JOB P — bulwark · reshape · the planner master (no git surface)" },
        { line: "approved series ready to implement → JOB O — super-branch INTENT (a branch, not a worktree) → dispatch" },
        { line: "no code change → research-only exit — chat is the right medium" },
      ],
    },
    {
      kind: "rundown",
      title: "the invariant ladder — every path, every role",
      lines: [
        { line: "task doc (approved) → branch (intent) → worktree (only where something is built)" },
        { line: "⟁ chat is never a build route — small code work takes the minimal w-02 artifact", junction: true },
        { line: "hat-collapse: flat run → the orchestrator wears the manager hat · session scale → hands-on build" },
      ],
    },
  ],
};

// --- orchestrator (260703-ORCH) ----------------------------------------------------------------

const ORCHESTRATOR: FlowModel = {
  id: "orchestrator",
  label: "Orchestrator",
  title: "orchestrator — the event loop, drawn on its biggest run (Job O)",
  takeaway:
    "The developer-facing lifecycle is an EVENT LOOP over durable portfolio state: every session " +
    "(resumption is the common case) opens with the trust checkpoint + PORTFOLIO ORIENTATION, then " +
    "routes the event — Design (the hat), Portfolio, Orchestrate, or a research-only exit. Below is " +
    "the biggest shape, an orchestrated run: streamline → portfolio gate → super-branch INTENT (a " +
    "branch, not a worktree) → dependency-ordered dispatch → decide each master-handover gate → " +
    "integrate per-edge in a transient worktree → super-exit review → developer handover. Human " +
    "review concentrates at the SUPER gate; the orchestrator closes with grounded self-improvement " +
    "proposals — never automated self-modification.",
  segments: [
    { kind: "start", label: "▸ event: \"orchestrate these masters\" — after trust checkpoint + portfolio orientation", next: "profile-fit check", nextStatus: "proposed" },
    {
      kind: "rundown",
      title: "seat · profile — before any analysis",
      lines: [
        { line: "profile-fit check — right harness/model/effort for the orchestrator job?" },
        { line: "⟁ wrong profile? → spawn_agent_session(orchestrator) + conversation-handover packet → takeover", junction: true },
        { line: "seat = FIRST coordination leaf (task_doc, no enclosure) · chat attached by leaf id" },
      ],
    },
    {
      kind: "rundown",
      title: "portfolio phase · streamline before sequencing (non-linear)",
      lines: [
        { line: "route-coherence scan — route indexes · onboarding · grepai · cgc; sub-agents write durable reports" },
        { line: "conflict / regression scan — planned-vs-planned AND planned-vs-past (the integrity bulwark)" },
        { line: "reshape proposals — leaf moves (planning-status only) · foundation-master extraction · mixed masters first-or-last" },
        { line: "⟁ interleaved leaf-level cross-deps? → reshape master boundaries — NEVER interleave dispatch", junction: true },
        { line: "dependency DAG — must be expressible at MASTER granularity" },
      ],
    },
    { kind: "node", phase: "portfolio · gate", tool: "portfolio plan gate", detail: "developer approves the reshaped portfolio + DAG + dispatch order — one wholesale review", rides: "plan-approval", ridesNote: "⊘ the streamlining output is a PROPOSAL — no silent rewrites of dev-accepted tasks", next: "create super integration branch", nextStatus: "proposed" },
    { kind: "node", phase: "topology", tool: "super-branch INTENT", detail: "creates a BRANCH off main, nothing more — masters base off IT; the orchestrator worktree exists only per integration edge", next: "dispatch loop", nextStatus: "proposed" },
    { kind: "divider", label: "↺ dependency-ordered dispatch loop — send out the next READY master's manager ↺" },
    { kind: "node", phase: "dispatch", tool: "spawn_agent_session (manager)", detail: "manager-brief.md · AR_SPAWN_ROLE=manager · qualified leaf key · base = the CURRENT super tip", next: "monitor", nextStatus: "proposed" },
    { kind: "node", phase: "monitor", tool: "monitor + steer", detail: "turn reports · nudges · escalations · spirit test on deltas · a wrong deliverable REOPENS its leaf (task_reopen) — never a redo sibling", next: "master handover", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "decide master-handover-approval", detail: "the manager RAISED it with the verdict attached — the ORCHESTRATOR decides (own ambient identity; owner-never-self-approves holds; the policy may require the verdict)", rides: "master-handover-approval", ridesNote: "⊘ seam 1 of 2 — happy path through the orchestrator; a handover it cannot honestly decide escalates to the developer", next: "integrate master → super", nextStatus: "proposed" },
    { kind: "node", phase: "integrate", tool: "integrate master → super (C-11)", detail: "orchestrator WORKTREE with super as source · merge/carry-over · memory single-siding · ledger maps every commit", next: "↺ next ready master — until the DAG is drained", nextStatus: "proposed" },
    { kind: "divider", label: "↓ DAG drained — the super branch holds the accumulated change set ↓" },
    { kind: "node", phase: "seam 2", tool: "super-exit adversarial review", detail: "wholesale verdict on the super branch: completion vs tasks · quality · onboarding-vs-code", rides: "super-exit seam", ridesNote: "⊘ adversarial review seam 2 of 2 — before the orchestrator hands over to the developer", next: "developer handover", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "developer review — super level", detail: "whole-behavior review (UX judged wholesale) · ⟁ rejected? → decompose feedback into fix leaves ↺ reactive dispatch", rides: "integration-approval", next: "super → main PR", nextStatus: "proposed" },
    { kind: "node", phase: "land", tool: "super → main PR + carry-over", detail: "remote merge · memory carried to main-memory · push (git-workflow.md tail)", next: "close + propose", nextStatus: "current" },
    { kind: "node", phase: "close", tool: "self-improvement report", detail: "did x/y/z · hit a/b/c · a,b solved on the spot · c PROPOSES this change — grounded in the accumulated backdrop", next: "lifecycle_end", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "lifecycle_end", detail: "terminal — durable notes/reports remain the record" },
  ],
};

// --- manager (one per master) ------------------------------------------------------------------

const MANAGER: FlowModel = {
  id: "manager",
  label: "Manager",
  title: "manager — one master, leaf loop → master-exit handover",
  takeaway:
    "Spawned by the orchestrator with a manager brief (its entire session start). Owns exactly one " +
    "master: dispatches a fresh worker per leaf, reviews turn reports, decides DELEGATED leaf gates " +
    "(plan · closeout — the owning agent never self-approves), owns the leaf lifecycle end-to-end " +
    "(worktree_start → closeout → integrate → finalize), and REOPENS a leaf whose deliverable came " +
    "out wrong. At master exit it spawns the reviewer, then RAISES master-handover-approval with " +
    "the verdict attached — the ORCHESTRATOR decides it. In a flat run the orchestrator wears this " +
    "hat. Escalation: to the orchestrator, never straight to the developer.",
  segments: [
    { kind: "start", label: "▸ spawned by the orchestrator — manager-brief.md pasted + submitted (the brief is the session start)", next: "seat", nextStatus: "proposed" },
    {
      kind: "rundown",
      title: "seat · intake",
      lines: [
        { line: "seat = own coordination leaf (task_doc, no enclosure) · chat attached — the dev can walk in any time" },
        { line: "read the master task_doc + leaf docs · order leaves (parallel where safe — C-11 reconcile absorbs a moved base)" },
        { line: "default behavior stands: fulfill the task, fill small blanks — no extra creative-liberty prompting either way" },
        { line: "⟁ plan delta beyond filling blanks? → escalate to the ORCHESTRATOR — managers don't reshape plans (no bird's-eye)", junction: true },
      ],
    },
    { kind: "divider", label: "↺ leaf dispatch loop — next leaf ↺" },
    { kind: "node", phase: "dispatch", tool: "spawn_agent_session (worker)", detail: "fresh session · worker-brief.md pasted + submitted · AR_SPAWN_ROLE=worker · the worker edits inside the worktrees the brief names", next: "monitor worker", nextStatus: "proposed" },
    { kind: "node", phase: "monitor", tool: "monitor worker", detail: "turn-report artifacts expected at every hand-off · inactivity or missing artifact → rate-limited stdin nudge · escalation intake via inbox", next: "review artifact", nextStatus: "proposed" },
    { kind: "node", phase: "review", tool: "review artifact vs task_doc", detail: "completion vs requirements/steps · checks green · onboarding same-pass · ⟁ wrong deliverable? → task_reopen the SAME leaf, reshape — never a redo sibling", next: "delegated gates", nextStatus: "proposed" },
    { kind: "node", phase: "gate", tool: "delegated leaf gates (plan · closeout)", detail: "decidedBy: manager lifecycle · decidedVia: orchestration · appended, dashboard-visible", rides: "delegated gate", ridesNote: "⊘ policy-configured delegation — the OWNING agent never self-approves; a distinct configured role may", next: "integrate leaf → master", nextStatus: "proposed" },
    { kind: "node", phase: "integrate", tool: "integrate leaf → master branch (C-11)", detail: "ff-only / replay per c-09 · a durable gate here is integration-approval — HUMAN-pinned · ↺ next leaf until done", next: "master-exit review", nextStatus: "current" },
    { kind: "divider", label: "↓ all leaves landed on the master integration branch ↓" },
    { kind: "node", phase: "seam 1", tool: "master-exit adversarial review", detail: "spawn reviewer (AR_SPAWN_ROLE=reviewer) · verdict: completion · quality · onboarding-vs-code · ⟁ blocked? → fix leaves ↺", rides: "master-exit seam", next: "handover to orchestrator", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "RAISE master-handover-approval + packet", detail: "gate raised with the verdict attached (evidenceRefs) · packet via inbox + push · the ORCHESTRATOR decides the gate", rides: "master-handover-approval", ridesNote: "⊘ delegable, never human-pinned — human review concentrates at the super gate", next: "seat stays reachable", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "seat remains", detail: "chat + coordination leaf stay reachable until the series retires" },
  ],
};

// --- worker (one per leaf, short-lived) ----------------------------------------------------------

const WORKER: FlowModel = {
  id: "worker",
  label: "Worker",
  title: "worker — one leaf, one session, one report",
  takeaway:
    "Self-contained and short-lived: the brief IS the session start (it replaces the front half the " +
    "spawner already ran). The worker orients with paired reads, builds exactly the leaf plan with " +
    "same-pass onboarding, gets the checks green, and ends at the MANDATORY turn report. It owns no " +
    "lifecycle machinery — closeout, integration, finalization, and gates belong to the owning " +
    "seat — and it never git-commits. A plan delta beyond blank-filling escalates one rung to the " +
    "owning seat; the spirit test lives with the orchestrator, not here.",
  segments: [
    { kind: "start", label: "▸ spawned on a leaf — worker-brief.md pasted + submitted (the brief is the session start)", next: "orient", nextStatus: "current" },
    {
      kind: "rundown",
      title: "the worker loop — roles/worker.md, self-contained",
      lines: [
        { line: "intake — the brief + leaf task_doc + predecessor report; never a transcript" },
        { line: "orient — paired reads of the files you will touch (read_ar_files = official baseline; native reads in the worktree)" },
        { line: "build — exactly the leaf plan · same-pass c-05 onboarding · local build_route_indexes · NEVER git commit" },
        { line: "checks green — the brief-prescribed focused suite + full wrapper" },
        { line: "⟁ blocked, or a plan delta beyond blank-filling? → escalate ONE rung to the owning seat — never the developer", junction: true },
      ],
    },
    { kind: "node", phase: "handover", tool: "turn-report artifact", detail: "MANDATORY templated report — what/issues/solved/left · evidence tally · respawn state · written even when blocked", next: "session ends", nextStatus: "current" },
    { kind: "node", phase: "close", tool: "session ends", detail: "terminal state = checks green + report; the owning seat runs closeout → integrate → finalize" },
  ],
};

// --- comms & escalation ---------------------------------------------------------------------------

const COMMS: FlowModel = {
  id: "comms",
  label: "Comms",
  title: "comms & escalation — channels, nudges, the ladder",
  takeaway:
    "Three channels compose: the inbox is the durable, dashboard-visible QUEUE; stdin push is the " +
    "DELIVERY for AR-hosted sessions (no poll loops); turn-report artifacts are the REPORTING that " +
    "survives compaction and session death. Nudges ride trustworthy inactivity signals (the reform " +
    "series). Escalation ladders worker → manager → orchestrator → developer with no skipping, and " +
    "the spirit test decides autonomy at every level. One handover-packet schema serves master " +
    "handover, role takeover, and worker respawn.",
  segments: [
    {
      kind: "rundown",
      title: "channels — inbox (queue) · stdin push (delivery) · artifacts (reporting) · chats (walk-in)",
      lines: [
        { line: "inbox — operator_inbox generalized to agent→agent addressing; every message durable + dashboard-visible" },
        { line: "stdin push — echo-confirmed PTY injection delivers queued messages to hosted sessions (poll = fallback only)" },
        { line: "turn-report artifacts — templated, durable; the orchestrator's own reports are the most important in the system" },
        { line: "chats — every seat has a leaf-attached chat; the developer can walk into any conversation at any level" },
      ],
    },
    { kind: "node", phase: "message", tool: "inbox post", detail: "sender writes the durable row (addressed by lifecycle/agent identity)", next: "push delivery", nextStatus: "proposed" },
    { kind: "node", phase: "message", tool: "stdin push delivery", detail: "serving injects the message (or a mail hint) into the target PTY · delivery status recorded", next: "target acts → artifact / response", nextStatus: "proposed" },
    { kind: "node", phase: "message", tool: "response / artifact", detail: "the reply is itself an inbox row or a turn-report artifact — never an untracked side channel", next: "(loop)", nextStatus: "proposed" },
    { kind: "divider", label: "— the nudge loop (needs trustworthy staleness — the attention/river reform series) —" },
    {
      kind: "rundown",
      title: "nudge policy",
      lines: [
        { line: "inactivity (heartbeat-aware, provably-stopped) OR turn end without an artifact → manager stdin nudge" },
        { line: "nudges are rate-limited and logged as events — observable, never spammy" },
      ],
    },
    { kind: "divider", label: "— the escalation ladder — no level skipped —" },
    {
      kind: "rundown",
      title: "escalation · worker → manager → orchestrator → developer",
      lines: [
        { line: "each level resolves within its own view first; only a stumped orchestrator raises to the developer" },
        { line: "workers/managers: fulfill the task, fill small blanks — plan deltas ESCALATE; no spirit judgment below the bird's-eye" },
        { line: "⟁ spirit test — ORCHESTRATOR-ONLY: within the spirit of accepted plans → act + decision-log entry", junction: true },
        { line: "⟁ against the spirit → JOINT decision with the developer (the unanticipated-wrench case)", junction: true },
      ],
    },
    { kind: "node", phase: "handover", tool: "handover packet (one schema)", detail: "master-complete handover · role takeover (profile-fit) · worker respawn — request, decisions, constraints, links, open questions", next: "receiver onboards from state, not transcript", nextStatus: "proposed" },
  ],
};

// --- designer (task design as its own job) --------------------------------------------------------

const DESIGNER: FlowModel = {
  id: "designer",
  label: "Designer",
  title: "designer — the hat the orchestrator pulls",
  takeaway:
    "Task design is its own job, worn as a HAT: it cannot sit in a coordination leaf because the " +
    "task is what it exists to create — the orchestrator runs roles/designer.md inline, at the " +
    "front of the pipeline AND mid-flight, helping the developer think a master through — the tasks/AGENTS.md doctrine (meta-questioning, reframe before execution, " +
    "evidence-first) given a distinct, optimized shape. It shares the orchestrator's bird's-eye " +
    "toolkit (route indexes · onboarding · grepai · cgc · blast radius) but is SCOPED to one master — " +
    "collisions with other or FUTURE masters can slip. That residual risk is owned downstream: at " +
    "portfolio streamlining the ORCHESTRATOR doubles as the designer's adversarial reviewer.",
  segments: [
    { kind: "start", label: "▸ developer intent, no task doc yet — the orchestrator pulls the designer hat (Job D)", next: "co-design loop", nextStatus: "current" },
    {
      kind: "rundown",
      title: "co-design loop · the tasks/AGENTS.md doctrine, as a job",
      lines: [
        { line: "meta-question the ask — surface request · deeper objective · highest-leverage framing" },
        { line: "evidence-first — route indexes · onboarding · grepai · cgc; sub-agents write durable reports" },
        { line: "blast-radius analysis WITHIN the master's scope — routes touched · invariants · regressions" },
        { line: "⟁ assumptions / truth gaps only the developer can resolve → ask — never fill silently", junction: true },
      ],
    },
    { kind: "node", phase: "frame", tool: "reframe agreement", detail: "the developer agrees the frame before structure exists", rides: "reframe", ridesNote: "⊘ material scope/intent/sequencing changes are played back and WAIT for confirmation (tasks/AGENTS.md)", next: "task_doc authoring", nextStatus: "proposed" },
    { kind: "node", phase: "author", tool: "task_doc authoring", detail: "master + leaves · requirements · steps · code examples (w-02 shape) · leaves scoped around routes/areas", next: "declare the limits", nextStatus: "current" },
    { kind: "node", phase: "limits", tool: "designer limits note", detail: "master-scoped bird's-eye: cross-master and FUTURE-master collisions can slip — declared on the doc, never hidden", next: "handover → portfolio", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "join the portfolio", detail: "at streamlining the ORCHESTRATOR adversarially reviews the design — planned-vs-planned AND planned-vs-past", next: "(orchestrator · portfolio phase)", nextStatus: "proposed" },
  ],
};

// --- adversarial reviewer (the two seams) ---------------------------------------------------------

const REVIEWER: FlowModel = {
  id: "reviewer",
  label: "Reviewer",
  title: "adversarial reviewer — verdicts are evidence, not decisions",
  takeaway:
    "Spawned at exactly two seams: MASTER-EXIT (before a manager hands its integration branch to the " +
    "orchestrator) and SUPER-EXIT (before the orchestrator hands the super branch to the developer). " +
    "It reviews the accumulated change set through three lenses — completion vs task docs, code " +
    "quality per tools.md, and onboarding-vs-code — fanning out sub-agents that write durable " +
    "reports. Its verdict is a templated artifact that attaches to the handover gate as JUDGE " +
    "evidence; the decider decides — the ORCHESTRATOR at master-exit (master-handover-approval), " +
    "the DEVELOPER at super-exit. A blocking verdict must decompose into fix leaves — findings, " +
    "never prose complaints.",
  segments: [
    { kind: "start", label: "▸ spawned at a seam (AR_SPAWN_ROLE=reviewer) — master-exit or super-exit + change-set context", next: "intake", nextStatus: "proposed" },
    { kind: "node", phase: "intake", tool: "scope the review", detail: "integration branch diff · the master's/portfolio's task docs · the seam's rubric", next: "three-lens review", nextStatus: "proposed" },
    {
      kind: "rundown",
      title: "three-lens review — sub-agents fan out, reports are durable",
      lines: [
        { line: "completion — every requirement/step addressed vs the task docs; deltas justified in decision logs" },
        { line: "code quality — the resolved tools.md suite (lint · typecheck · tests · complexity); regressions vs the past" },
        { line: "onboarding-vs-code — changed files' sidecars updated in the same pass · drift clean · route overviews current" },
        { line: "⟁ refute-or-confirm posture — findings must survive an attempt to refute them", junction: true },
      ],
    },
    { kind: "node", phase: "verdict", tool: "verdict artifact", detail: "templated · findings ranked · explicit pass/block recommendation · durable under the series notes", next: "attach as judge evidence", nextStatus: "proposed" },
    { kind: "node", phase: "seam", tool: "attach to the handover gate", detail: "JUDGE evidence on the gate record — the decider (per the gate delegation policy) decides; the reviewer never does", rides: "judge evidence", ridesNote: "⊘ verdicts are evidence, not decisions — requireReviewerVerdictAtSeams binds delegated seam decisions to the verdict", next: "pass or block", nextStatus: "proposed" },
    { kind: "node", phase: "outcome", tool: "⟁ block? → decomposable fix leaves", detail: "a blocking verdict names concrete, leaf-shaped findings the owning manager/orchestrator can dispatch — never prose-only", next: "session ends", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "session ends", detail: "short-lived by design — the verdict artifact and sub-agent reports remain the record" },
  ],
};

export const FLOW_MODELS: FlowModel[] = [ROUTER, DESIGNER, ORCHESTRATOR, MANAGER, WORKER, REVIEWER, COMMS];
