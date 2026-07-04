// The flow-model registry for the FlowTab canvas (orchestration L0): each model is one drawable
// lifecycle/interaction design, rendered by FlowTab's segment renderer and switched by its nav.
// Models are STATIC design artifacts (no store reads) — the canvas is where a lifecycle is drawn,
// reviewed with the developer, and only then built. `current` edges/pieces are wired today; `proposed`
// is the orchestration series (mirrors the original task-26 mint/amber legend).

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

// --- build job (task 26 — the original model, preserved verbatim) -----------------------------

const BUILD_JOB: FlowModel = {
  id: "build-job",
  label: "Build job",
  title: "build job — the hint chain",
  takeaway:
    "The turn-end notification is not a tool the agent calls — it auto-fires when a gate tool " +
    "(dry-run / preview / task_doc-create) is called, so the agent can't forget it (amber left-bar " +
    "nodes below). The front half is non-linear (research fires unpredictably; task-file-exists? " +
    "isn't a tool), so lifecycle_start emits a one-time prose rundown instead of per-tool hints. " +
    "From worktree_start on it's linear; mint edges already chain (guidance.lifecycle_guidance), " +
    "amber is this series. Historically this drawing is the \"Eierlegende Wollmilchsau\": one " +
    "implicit role doing a bit of everything minus orchestration, the developer wearing the " +
    "orchestrator + manager hats throughout — see the FRAME model for the decomposition.",
  segments: [
    { kind: "start", label: "▸ developer request — stated in chat", next: "context_packet", nextStatus: "proposed" },
    { kind: "node", phase: "1 · trust-checkpoint", tool: "context_packet", detail: "providers · drift · freshness", next: "lifecycle_start", nextStatus: "proposed" },
    { kind: "node", phase: "1 · trust-checkpoint", tool: "lifecycle_start", detail: "promote the lifecycle — and emit the front-half rundown ↓", next: "(prose rundown)", nextStatus: "proposed" },
    {
      kind: "rundown",
      title: "front half · prose rundown from lifecycle_start (not per-tool hints)",
      lines: [
        { line: "reframe — restate the request as agreed work" },
        { line: "research — read_ar_files · grepai · cgc   (fire unpredictably → not hint anchors)" },
        { line: "job selection — bug / feature → build   ·   triage / research → research-only exit" },
        { line: "⟁ task file exists? (build/fix)     yes → worktree_start     no ↓", junction: true },
        { line: "task_doc — persist the chat task proposal as a durable file (+ approval), then ↓" },
      ],
    },
    { kind: "divider", label: "↓ from here the lifecycle is linear — each gate tool auto-fires its notification ↓" },
    { kind: "node", phase: "3 · decide", tool: "worktree_start --dry-run", detail: "verify the worktree-intent dry-run (self-fix first)", rides: "worktree-intent", next: "worktree_start (apply)", nextStatus: "proposed" },
    { kind: "node", phase: "3→4 · build", tool: "worktree_start (apply)", detail: "open the worktree — lifecycle promotes to persistent", next: "implement + run checks", nextStatus: "proposed" },
    { kind: "node", phase: "4 · build", tool: "implement + run checks", detail: "edit · tests · quality wrapper", next: "worktree_closeout_preview", nextStatus: "proposed" },
    { kind: "node", phase: "4 · build", tool: "worktree_closeout_preview", detail: "verify the closeout preview (self-fix first)", rides: "commit / closeout-approval", next: "worktree_closeout_apply", nextStatus: "proposed" },
    { kind: "node", phase: "4 · build", tool: "worktree_closeout_apply", detail: "commit code · memory · ledger", next: "worktree_integrate --dry-run", nextStatus: "current" },
    { kind: "node", phase: "4 · build", tool: "worktree_integrate --dry-run", detail: "verify the integration dry-run", rides: "integration-approval", next: "worktree_integrate (apply)", nextStatus: "proposed" },
    { kind: "node", phase: "4 · build", tool: "worktree_integrate (apply)", detail: "land into the source branch", next: "memory_carryover_apply", nextStatus: "current" },
    { kind: "node", phase: "5 · close", tool: "memory_carryover_apply", detail: "carry parked memory home", next: "lifecycle_finalize_task --dry-run", nextStatus: "current" },
    { kind: "node", phase: "5 · close", tool: "lifecycle_finalize_task --dry-run", detail: "verify the landed-edge + cleanup plan", rides: "cleanup / finalization", next: "lifecycle_finalize_task (apply)", nextStatus: "proposed" },
    { kind: "node", phase: "5 · close", tool: "lifecycle_finalize_task (apply)", detail: "prove the edge · reclaim worktrees", next: "lifecycle_end", nextStatus: "current" },
    { kind: "node", phase: "5 · close", tool: "lifecycle_end", detail: "terminal" },
  ],
};

// --- orchestrator (260703-ORCH) ----------------------------------------------------------------

const ORCHESTRATOR: FlowModel = {
  id: "orchestrator",
  label: "Orchestrator",
  title: "orchestrator — portfolio → super integration branch",
  takeaway:
    "Developer-requested, never self-spawning. The opening move is the PORTFOLIO PHASE: streamline " +
    "the requested masters (coherence before sequencing) using the memory substrate — route indexes, " +
    "onboarding, grepai, cgc — with sub-agents writing durable reports. Only a master-granular " +
    "dependency DAG proceeds to dispatch: masters base off the SUPER branch, so every integration " +
    "accumulates. The developer reviews wholesale at the super seam, and the orchestrator closes with " +
    "grounded self-improvement proposals — proposals, never automated self-modification.",
  segments: [
    { kind: "start", label: "▸ developer request — \"orchestrate these masters\" (orchestration skill)", next: "profile-fit check", nextStatus: "proposed" },
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
    { kind: "node", phase: "topology", tool: "super integration branch", detail: "based off main · orchestrator-owned · masters will base off IT (accumulative change set)", next: "dispatch loop", nextStatus: "proposed" },
    { kind: "divider", label: "↺ dependency-ordered dispatch loop — send out the next READY master's manager ↺" },
    { kind: "node", phase: "dispatch", tool: "spawn_agent_session (manager)", detail: "job file + master context packet · seat leaf + chat · knobs from settings/job (harness · model · effort)", next: "monitor", nextStatus: "proposed" },
    { kind: "node", phase: "monitor", tool: "monitor + steer", detail: "turn-report artifacts · nudges · escalation intake · spirit test on plan deltas (within spirit → act + log; against → raise)", next: "master handover", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "master handover packet", detail: "integration branch ref · change-set summary · MASTER-EXIT adversarial verdict · carry-over state", rides: "master-exit seam", ridesNote: "⊘ adversarial review seam 1 of 2 — before the manager hands over to the orchestrator", next: "integrate master → super", nextStatus: "proposed" },
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
    "Spawned by the orchestrator with the master's context packet. Owns exactly one master series: " +
    "dispatches a fresh worker per leaf, reviews turn-report artifacts, decides DELEGATED leaf gates " +
    "(attributed — the owning agent never self-approves), and integrates leaves into the master " +
    "integration branch via C-11. It exits through the master-exit adversarial seam and hands the " +
    "completed integration branch to the orchestrator. Escalation ladders up — a stumped manager " +
    "raises to the orchestrator, never straight to the developer.",
  segments: [
    { kind: "start", label: "▸ spawned by the orchestrator — job file + master context packet (auto-submitted)", next: "seat", nextStatus: "proposed" },
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
    { kind: "node", phase: "dispatch", tool: "spawn_agent_session (worker)", detail: "fresh session on the leaf · context packet pasted + submitted · worker attaches the leaf worktree", next: "monitor worker", nextStatus: "proposed" },
    { kind: "node", phase: "monitor", tool: "monitor worker", detail: "turn-report artifacts expected at every hand-off · inactivity or missing artifact → rate-limited stdin nudge · escalation intake via inbox", next: "review artifact", nextStatus: "proposed" },
    { kind: "node", phase: "review", tool: "review artifact vs task_doc", detail: "completion vs requirements/steps · checks green · onboarding refreshed in the same pass", next: "delegated gates", nextStatus: "proposed" },
    { kind: "node", phase: "gate", tool: "delegated leaf gates (plan · closeout)", detail: "decidedBy: manager lifecycle · decidedVia: orchestration · appended, dashboard-visible", rides: "delegated gate", ridesNote: "⊘ policy-configured delegation (L4) — the OWNING agent never self-approves; a distinct configured role may", next: "integrate leaf → master", nextStatus: "proposed" },
    { kind: "node", phase: "integrate", tool: "integrate leaf → master branch (C-11)", detail: "ff-only / replay per c-09 · ↺ next leaf until the master's leaves are done", next: "master-exit review", nextStatus: "current" },
    { kind: "divider", label: "↓ all leaves landed on the master integration branch ↓" },
    { kind: "node", phase: "seam 1", tool: "master-exit adversarial review", detail: "spawn reviewer · verdict artifact: completion vs task docs · tools.md quality · onboarding-vs-code · ⟁ blocked? → fix leaves ↺", rides: "master-exit seam", next: "handover to orchestrator", nextStatus: "proposed" },
    { kind: "node", phase: "handover", tool: "handover packet → orchestrator", detail: "inbox post (durable) + stdin push · integration branch ref · change-set summary · verdict ref · carry-over state", next: "seat stays reachable", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "seat remains", detail: "chat + coordination leaf stay reachable until the series retires" },
  ],
};

// --- worker (one per leaf, short-lived) ----------------------------------------------------------

const WORKER: FlowModel = {
  id: "worker",
  label: "Worker",
  title: "worker — one leaf, fresh session, mandatory turn report",
  takeaway:
    "Short-lived by design: one worker per leaf, fresh context, onboarded from the context packet and " +
    "the task_doc — never from a transcript. It runs the normal l-01 build spine inside the leaf " +
    "worktree (the spine is unchanged; the job lens specializes it). Its closeout gate is decided by " +
    "the MANAGER (delegated, attributed). Every hand-off leaves a turn-report artifact — a missing " +
    "report gets nudged. A stumped worker escalates to its manager via the inbox; never to the " +
    "developer directly.",
  segments: [
    { kind: "start", label: "▸ spawned on a leaf — context packet pasted + submitted (auto-start)", next: "worktree_attach", nextStatus: "proposed" },
    { kind: "node", phase: "attach", tool: "worktree_attach", detail: "leaf enclosure → resumes the leaf's persistent lifecycle · own harness + MCP process (ambient singleton)", next: "l-01 build spine", nextStatus: "current" },
    {
      kind: "rundown",
      title: "l-01 build — the normal spine, unchanged (job lens: worker)",
      lines: [
        { line: "implement per the leaf plan · onboarding refreshed in the SAME pass (c-05)" },
        { line: "checks green before every incremental commit (tools.md · lint · typecheck · tests)" },
        { line: "⟁ stumped for good? → escalate to the MANAGER (inbox, push-delivered) — never straight to the dev", junction: true },
      ],
    },
    { kind: "node", phase: "closeout", tool: "worktree_closeout_preview", detail: "dry-run first, self-fix, then hand off", rides: "delegated closeout", ridesNote: "⊘ decided by the MANAGER (delegated, attributed) — not the developer; human review waits at the master/super seams", next: "worktree_closeout_apply", nextStatus: "proposed" },
    { kind: "node", phase: "closeout", tool: "worktree_closeout_apply", detail: "commit code · memory · ledger", next: "integrate leaf → master", nextStatus: "current" },
    { kind: "node", phase: "integrate", tool: "worktree_integrate", detail: "land into the master integration branch (the recorded source branch)", next: "turn report", nextStatus: "current" },
    { kind: "node", phase: "handover", tool: "turn-report artifact", detail: "MANDATORY templated report: what/issues/solved/left · missing → nudge · the respawn-onboarding state for a successor", next: "session ends", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "session ends", detail: "short-lived by design — continuity lives in task_doc + report, not the transcript" },
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
  title: "designer — co-think a master task with the developer",
  takeaway:
    "Task design becomes its own registered job. Before orchestration there was one implicit " +
    "do-it-all role; now the DESIGNER owns the front of the pipeline: helping the developer think a " +
    "master through — the tasks/AGENTS.md doctrine (meta-questioning, reframe before execution, " +
    "evidence-first) given a distinct, optimized shape. It shares the orchestrator's bird's-eye " +
    "toolkit (route indexes · onboarding · grepai · cgc · blast radius) but is SCOPED to one master — " +
    "collisions with other or FUTURE masters can slip. That residual risk is owned downstream: at " +
    "portfolio streamlining the ORCHESTRATOR doubles as the designer's adversarial reviewer.",
  segments: [
    { kind: "start", label: "▸ developer intent — \"help me think this task through\" (designer job)", next: "co-design loop", nextStatus: "proposed" },
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

// --- the frame (contact points — the consistent runtime every job plugs into) --------------------

const FRAME: FlowModel = {
  id: "frame",
  label: "Frame",
  title: "the frame — context → job → wrap-up (the consistent runtime)",
  takeaway:
    "Decomposing the Wollmilchsau narrows the LIFECYCLE proper down to thin CONTACT POINTS every job " +
    "shares: context intake → job selection + execution → wrap-up. The frame is the consistent " +
    "container that houses the different jobs — deliberately thin compared to its payloads, but it " +
    "is what makes them one runtime: same trust checkpoint, same observability, same gates and " +
    "escalation seams, same durable-artifact obligations, same close. A job's meat lives in its own " +
    "drawing; the frame only guarantees the sockets it plugs into.",
  segments: [
    { kind: "start", label: "▸ any request — every session enters through the same frame", next: "context", nextStatus: "current" },
    { kind: "node", phase: "frame · context", tool: "trust checkpoint", detail: "context_packet — providers · drift · freshness · lifecycle_start (observable from the first call)", next: "job selection", nextStatus: "current" },
    {
      kind: "rundown",
      title: "frame · job selection — the container picks its payload",
      lines: [
        { line: "profile-fit — the job registry names the wanted harness/model/effort; wrong profile → takeover spawn" },
        { line: "lenses today: research · triage · bug · feature   ·   jobs this series: designer · orchestrator · manager · worker · reviewer" },
        { line: "⟁ from here the JOB's own flow takes over — see its drawing; the frame stays thin", junction: true },
      ],
    },
    { kind: "node", phase: "frame · execution", tool: "housed job runs", detail: "the frame guarantees only the contact points: observability (task_doc · events) · gates · escalation ladder · durable artifacts", next: "wrap-up", nextStatus: "proposed" },
    { kind: "node", phase: "frame · wrap-up", tool: "lifecycle wrap-up", detail: "turn-report / notes durable · land per the job's path (closeout · integration · handover) · lifecycle_end", next: "(next session — same frame, any payload)", nextStatus: "current" },
    { kind: "divider", label: "one thin frame · many payloads — a consistent runtime is what makes jobs composable" },
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
    "evidence; the gate's decider (manager / orchestrator / developer per policy) decides. A " +
    "blocking verdict must decompose into fix leaves — findings, never prose complaints.",
  segments: [
    { kind: "start", label: "▸ spawned at a seam — master-exit or super-exit (reviewer job + change-set context)", next: "intake", nextStatus: "proposed" },
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
    { kind: "node", phase: "seam", tool: "attach to the handover gate", detail: "JUDGE evidence on the gate record — the decider (per L4 policy) decides; the reviewer never does", rides: "judge evidence", ridesNote: "⊘ verdicts are evidence, not decisions — a policy may REQUIRE a verdict before a delegated decision is valid", next: "pass or block", nextStatus: "proposed" },
    { kind: "node", phase: "outcome", tool: "⟁ block? → decomposable fix leaves", detail: "a blocking verdict names concrete, leaf-shaped findings the owning manager/orchestrator can dispatch — never prose-only", next: "session ends", nextStatus: "proposed" },
    { kind: "node", phase: "close", tool: "session ends", detail: "short-lived by design — the verdict artifact and sub-agent reports remain the record" },
  ],
};

export const FLOW_MODELS: FlowModel[] = [BUILD_JOB, FRAME, DESIGNER, ORCHESTRATOR, MANAGER, WORKER, REVIEWER, COMMS];
