import { css, cva } from "../../styled-system/css";

// The Lifecycle Flow tab (task 26): the BUILD-job lifecycle in TWO regimes (dev, task 26).
//
//  • FRONT HALF (non-linear) — a one-time PROSE RUNDOWN emitted by `lifecycle_start`:
//      reframe → research → job-selection → task-file-exists? → task_doc.
//    It's prose, not per-tool hints, because the research tools (read_ar_files / grepai / cgc) fire
//    unpredictably and the `task-file-exists?` junction isn't a tool call at all.
//
//  • LINEAR HALF — from worktree_start on, the lifecycle is linear and the turn-end notification
//    RIDES the tool: it auto-fires when a gate tool (dry-run / preview / task_doc-create) is called,
//    so the agent never fires it explicitly and literally can't forget. Auto-dismiss composes: the
//    next AR tool clears it, so it only sticks when the agent actually stops.
//
// Arrow colour: mint = wired today (guidance.lifecycle_guidance chains apply→next-dry-run); amber
// dashed = this series (the prose rundown + every auto-fired notification + the linear nextStep wiring).

type Status = "current" | "proposed";

// Front-half roadmap lines emitted by lifecycle_start (the `junction` line is the one non-tool step).
const RUNDOWN: { line: string; junction?: boolean }[] = [
  { line: "reframe — restate the request as agreed work" },
  { line: "research — read_ar_files · grepai · cgc   (fire unpredictably → not hint anchors)" },
  { line: "job selection — bug / feature → build   ·   triage / research → research-only exit" },
  { line: "⟁ task file exists? (build/fix)     yes → worktree_start     no ↓", junction: true },
  { line: "task_doc — persist the chat task proposal as a durable file (+ approval), then ↓" },
];

interface Node {
  phase: string;
  tool: string;
  detail?: string;
  rides?: string; // the gate whose notification auto-fires when this tool is called
  next?: string;
  nextStatus?: Status;
}

// Head: trust-checkpoint tools that precede the rundown.
const HEAD: Node[] = [
  { phase: "1 · trust-checkpoint", tool: "context_packet", detail: "providers · drift · freshness", next: "lifecycle_start", nextStatus: "proposed" },
  { phase: "1 · trust-checkpoint", tool: "lifecycle_start", detail: "promote the lifecycle — and emit the front-half rundown ↓", next: "(prose rundown)", nextStatus: "proposed" },
];

// Linear half: from worktree_start on. `rides` = the gate tool auto-fires its notification on call.
const LINEAR: Node[] = [
  { phase: "3 · decide", tool: "worktree_start --dry-run", detail: "verify the worktree-intent dry-run (self-fix first)", rides: "worktree-intent", next: "worktree_start (apply)", nextStatus: "proposed" },
  { phase: "3→4 · build", tool: "worktree_start (apply)", detail: "open the worktree — lifecycle promotes to persistent", next: "implement + run checks", nextStatus: "proposed" },
  { phase: "4 · build", tool: "implement + run checks", detail: "edit · tests · quality wrapper", next: "worktree_closeout_preview", nextStatus: "proposed" },
  { phase: "4 · build", tool: "worktree_closeout_preview", detail: "verify the closeout preview (self-fix first)", rides: "commit / closeout-approval", next: "worktree_closeout_apply", nextStatus: "proposed" },
  { phase: "4 · build", tool: "worktree_closeout_apply", detail: "commit code · memory · ledger", next: "worktree_integrate --dry-run", nextStatus: "current" },
  { phase: "4 · build", tool: "worktree_integrate --dry-run", detail: "verify the integration dry-run", rides: "integration-approval", next: "worktree_integrate (apply)", nextStatus: "proposed" },
  { phase: "4 · build", tool: "worktree_integrate (apply)", detail: "land into the source branch", next: "memory_carryover_apply", nextStatus: "current" },
  { phase: "5 · close", tool: "memory_carryover_apply", detail: "carry parked memory home", next: "lifecycle_finalize_task --dry-run", nextStatus: "current" },
  { phase: "5 · close", tool: "lifecycle_finalize_task --dry-run", detail: "verify the landed-edge + cleanup plan", rides: "cleanup / finalization", next: "lifecycle_finalize_task (apply)", nextStatus: "proposed" },
  { phase: "5 · close", tool: "lifecycle_finalize_task (apply)", detail: "prove the edge · reclaim worktrees", next: "lifecycle_end", nextStatus: "current" },
  { phase: "5 · close", tool: "lifecycle_end", detail: "terminal" },
];

const root = css({ display: "flex", flexDirection: "column", flex: "1", minHeight: "0", overflow: "auto", padding: "0.4rem 0.2rem 2rem", gap: "0.55rem" });
const header = css({ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" });
const h2 = css({ margin: "0", fontSize: "0.95rem", letterSpacing: "0.14em", color: "amber" });
const legend = css({ display: "flex", gap: "1.1rem", fontSize: "0.72rem", color: "muted", alignItems: "center" });
const swatch = cva({ base: { display: "inline-block", width: "1.6rem", height: "0", marginRight: "0.35rem", verticalAlign: "middle", borderTopWidth: "2px" }, variants: { status: { current: { borderTopStyle: "solid", borderTopColor: "mint" }, proposed: { borderTopStyle: "dashed", borderTopColor: "amber" } } } });
const takeaway = css({ fontSize: "0.76rem", color: "ink", opacity: "0.9", borderLeftWidth: "2px", borderLeftStyle: "solid", borderLeftColor: "amber", paddingLeft: "0.6rem", maxWidth: "82ch", lineHeight: "1.45" });

const chain = css({ display: "flex", flexDirection: "column", alignItems: "stretch", maxWidth: "640px", width: "100%", marginInline: "auto", marginTop: "0.3rem" });
const startNode = css({ alignSelf: "center", fontSize: "0.74rem", color: "muted", borderWidth: "1px", borderStyle: "dotted", borderColor: "grid", borderRadius: "999px", paddingInline: "0.7rem", paddingBlock: "0.2rem" });
const toolNode = css({ display: "flex", flexDirection: "column", gap: "0.1rem", background: "bg", borderWidth: "1px", borderStyle: "solid", borderColor: "grid", borderRadius: "3px", paddingInline: "0.7rem", paddingBlock: "0.45rem" });
// A gate tool: a normal call with the notification fused as an auto-fired rider (amber left bar).
const ridesNode = css({ display: "flex", flexDirection: "column", gap: "0.1rem", background: "bg", borderWidth: "1px", borderStyle: "solid", borderColor: "grid", borderLeftWidth: "3px", borderLeftStyle: "solid", borderLeftColor: "amber", borderRadius: "3px", paddingInline: "0.7rem", paddingBlock: "0.45rem" });
const phaseTag = css({ fontSize: "0.63rem", letterSpacing: "0.08em", color: "muted", textTransform: "uppercase" });
const toolName = css({ fontFamily: "monospace", fontSize: "0.82rem", fontWeight: "600", color: "ink" });
const detailLine = css({ fontSize: "0.72rem", color: "muted" });
const ridesLine = css({ fontSize: "0.69rem", color: "amber", fontStyle: "italic", marginTop: "0.16rem" });
const connector = cva({ base: { display: "flex", alignItems: "center", gap: "0.4rem", alignSelf: "center", paddingBlock: "0.2rem", fontSize: "0.7rem", fontFamily: "monospace" }, variants: { status: { current: { color: "mint" }, proposed: { color: "amber" } } } });
const wire = cva({ base: { width: "0", height: "0.95rem", borderLeftWidth: "2px" }, variants: { status: { current: { borderLeftStyle: "solid", borderLeftColor: "mint" }, proposed: { borderLeftStyle: "dashed", borderLeftColor: "amber" } } } });
// The prose-rundown card (the front half, emitted by lifecycle_start).
const rundownCard = css({ display: "flex", flexDirection: "column", gap: "0.25rem", background: "bg", borderWidth: "1px", borderStyle: "dashed", borderColor: "amber", borderRadius: "3px", paddingInline: "0.8rem", paddingBlock: "0.55rem", marginBlock: "0.2rem" });
const rundownTitle = css({ fontSize: "0.7rem", letterSpacing: "0.06em", color: "amber", textTransform: "uppercase" });
const rundownLine = cva({ base: { fontSize: "0.74rem", color: "ink", paddingLeft: "0.6rem", borderLeftWidth: "2px", borderLeftStyle: "solid", borderLeftColor: "grid", paddingBlock: "0.08rem" }, variants: { junction: { true: { borderLeftColor: "cyan", color: "cyan", fontFamily: "monospace", fontSize: "0.72rem" }, false: {} } } });
const divider = css({ alignSelf: "center", fontSize: "0.7rem", color: "muted", fontStyle: "italic", paddingBlock: "0.3rem", textAlign: "center" });

function ToolNode({ n }: { n: Node }) {
  const Cls = n.rides ? ridesNode : toolNode;
  return (
    <div className={Cls} data-testid={n.rides ? "flow-gate" : "flow-node"}>
      <span className={phaseTag}>{n.phase}{n.rides ? " · gate" : ""}</span>
      <span className={toolName}>{n.tool}</span>
      {n.detail ? <span className={detailLine}>{n.detail}</span> : null}
      {n.rides ? (
        <span className={ridesLine}>⊘ auto-fires lifecycle_turn_end_notification · {n.rides} — rides this call (agent never fires it); next AR tool clears it</span>
      ) : null}
    </div>
  );
}

function Arrow({ to, status }: { to: string; status: Status }) {
  return (
    <div className={connector({ status })} data-edge={status}>
      <span className={wire({ status })} />
      <span>nextStep → {to}</span>
    </div>
  );
}

export function FlowTab() {
  return (
    <div className={root} data-testid="flow-tab">
      <div className={header}>
        <h2 className={h2}>LIFECYCLE FLOW · build job — the hint chain</h2>
        <div className={legend}>
          <span><span className={swatch({ status: "current" })} />wired today</span>
          <span><span className={swatch({ status: "proposed" })} />this series</span>
        </div>
      </div>
      <p className={takeaway}>
        The turn-end notification is <strong>not a tool the agent calls</strong> — it auto-fires when a
        gate tool (dry-run / preview / task_doc-create) is called, so the agent can't forget it
        (amber left-bar nodes below). The <strong>front half is non-linear</strong> (research fires
        unpredictably; <code>task-file-exists?</code> isn't a tool), so <code>lifecycle_start</code>
        emits a one-time <strong>prose rundown</strong> instead of per-tool hints. From{" "}
        <code>worktree_start</code> on it's linear; mint edges already chain
        (<code>guidance.lifecycle_guidance</code>), amber is this series.
      </p>

      <div className={chain}>
        <div className={startNode}>▸ developer request — stated in chat</div>
        <Arrow to="context_packet" status="proposed" />
        {HEAD.map((n) => (
          <div key={n.tool}>
            <ToolNode n={n} />
            {n.next ? <Arrow to={n.next} status={n.nextStatus ?? "proposed"} /> : null}
          </div>
        ))}

        <div className={rundownCard} data-testid="flow-rundown">
          <span className={rundownTitle}>front half · prose rundown from lifecycle_start (not per-tool hints)</span>
          {RUNDOWN.map((r, i) => (
            <span key={i} className={rundownLine({ junction: Boolean(r.junction) })}>{r.line}</span>
          ))}
        </div>

        <div className={divider}>↓ from here the lifecycle is linear — each gate tool auto-fires its notification ↓</div>

        {LINEAR.map((n) => (
          <div key={n.tool}>
            <ToolNode n={n} />
            {n.next ? <Arrow to={n.next} status={n.nextStatus ?? "proposed"} /> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
