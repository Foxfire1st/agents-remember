import { useState, type ReactNode } from "react";

import { css, cva, cx } from "../../styled-system/css";
import { postGateDecision, type GateDecisionStatus } from "../data/actions";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import { ProgressFill } from "../grammar/ProgressFill";
import { TokenGauge } from "../grammar/TokenGauge";
import type {
  GateNode,
  Phase,
  ProviderNode,
  TaskCodeExampleNode,
  TaskDecisionNode,
  TaskDocNode,
  TaskStepNode,
} from "../types/projection";

// The l-01 phase vocabulary, in order (mcp/.../lifecycle_state.py). The stepper marks phases before
// the current as done — mc2's Request→Close mini-map.
const PHASES: Phase[] = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
];

const sizing = css({ flex: "1" });
const where = css({ fontSize: "0.76rem", color: "muted", marginBottom: "0.4rem" });

const stepper = css({
  listStyle: "none",
  margin: "0.4rem 0",
  padding: "0",
  display: "flex",
  flexWrap: "wrap",
  gap: "0.3rem",
});
const step = cva({
  base: {
    fontSize: "0.72rem",
    letterSpacing: "0.04em",
    paddingInline: "0.4rem",
    paddingBlock: "0.15rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "2px",
    color: "oklch(0.6 0.02 250)",
  },
  variants: {
    state: {
      todo: {},
      done: { color: "cyan", borderColor: "cyan" },
      current: {
        color: "amber",
        borderColor: "amber",
        textShadow: "0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
      },
    },
  },
});

const gate = css({
  margin: "0.5rem 0",
  padding: "0.5rem 0.6rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "3px",
  background: "oklch(0.82 0.16 75 / 0.08)",
});
const gateActions = css({ display: "flex", gap: "0.3rem", margin: "0.4rem 0 0.2rem" });
const gateButton = css({
  fontSize: "0.72rem",
  paddingInline: "0.5rem",
  paddingBlock: "0.15rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "cyan",
  borderRadius: "10px",
  color: "cyan",
  background: "transparent",
  cursor: "pointer",
  font: "inherit",
  _hover: { background: "oklch(0.7 0.1 200 / 0.12)" },
  _disabled: { borderColor: "grid", color: "oklch(0.6 0.02 250)", cursor: "default" },
});
const gateNote = css({
  margin: "0.2rem 0 0",
  fontSize: "0.72rem",
  color: "oklch(0.65 0.02 250)",
  fontStyle: "italic",
});

const badge = css({
  fontSize: "0.68rem",
  paddingInline: "0.35rem",
  paddingBlock: "0.05rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  color: "muted",
});

const series = css({ margin: "0.5rem 0" });
const taskHead = css({ fontSize: "0.82rem" });
const slices = css({ listStyle: "none", margin: "0.3rem 0 0", padding: "0", display: "grid", gap: "0.15rem" });
const slice = css({
  display: "flex",
  justifyContent: "space-between",
  gap: "0.5rem",
  fontSize: "0.78rem",
  paddingInline: "0.4rem",
  paddingBlock: "0.2rem",
  background: "bg",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
});
const sliceMeta = css({ color: "muted", fontSize: "0.72rem" });

const spine = css({ margin: "0.6rem 0" });
const spineHead = css({
  fontSize: "0.72rem",
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "muted",
  marginBottom: "0.3rem",
});
const lanes = css({ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" });
const lane = cva({
  base: {
    display: "grid",
    gap: "0.25rem",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "3px",
    padding: "0.4rem 0.5rem",
    borderLeftWidth: "2px",
  },
  variants: { kind: { code: { borderLeftColor: "amber" }, memory: { borderLeftColor: "cyan" } } },
});
const laneTitle = css({ fontSize: "0.76rem", color: "ink" });
const laneRepo = css({ fontSize: "0.74rem", color: "muted" });
const laneMeta = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.5rem",
  fontSize: "0.72rem",
  color: "muted",
});

const tokensRow = css({ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.5rem" });
const label = css({
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "muted",
});

const stepsList = css({ listStyle: "none", margin: "0.45rem 0 0", padding: "0", display: "grid", gap: "0.25rem" });
const stepRow = css({
  display: "grid",
  gridTemplateColumns: "auto 1fr",
  alignItems: "baseline",
  gap: "0.45rem",
  fontSize: "0.82rem",
});
const stepMarkBase = css({
  width: "0.62em",
  height: "0.62em",
  alignSelf: "center",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
});
// step status is data-driven; map by record so an unknown status renders as a neutral mark.
const STEP_MARK: Record<string, string> = {
  done: css({ background: "mint", borderColor: "mint" }),
  inProgress: css({ background: "amber", borderColor: "amber" }),
  blocked: css({ background: "alarm", borderColor: "alarm" }),
};
const STEP_TITLE: Record<string, string> = { done: css({ color: "oklch(0.6 0.02 250)" }) };
const substeps = css({
  gridColumn: "2",
  listStyle: "none",
  margin: "0.15rem 0 0",
  padding: "0",
  display: "grid",
  gap: "0.1rem",
  fontSize: "0.76rem",
  color: "muted",
});
const SUBSTEP: Record<string, string> = { inProgress: css({ color: "amber" }) };

const taskdoc = css({ display: "grid", gap: "0.75rem" });
const taskdocHead = css({ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" });
const taskdocTitle = css({ fontWeight: "600" });
const taskdocStatus = css({ color: "cyan", fontSize: "0.8rem" });
const taskdocSection = css({ display: "grid", gap: "0.3rem" });
const taskdocH = css({
  margin: "0",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "amber",
});
const taskdocP = css({ margin: "0", maxWidth: "78ch", fontSize: "0.86rem", lineHeight: "1.55" });
const taskdocBullets = css({
  margin: "0",
  paddingLeft: "1.1rem",
  maxWidth: "78ch",
  display: "grid",
  gap: "0.2rem",
  fontSize: "0.84rem",
  lineHeight: "1.45",
});
const taskdocCode = css({
  display: "grid",
  gap: "0.2rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  padding: "0.45rem 0.55rem",
});
const taskdocCodeHead = css({ color: "amber", fontSize: "0.82rem" });
const taskdocCodeMeta = css({ fontSize: "0.78rem", color: "muted" });
const taskdocSnippet = css({
  margin: "0.2rem 0 0",
  padding: "0.5rem 0.6rem",
  background: "bg",
  borderRadius: "2px",
  overflow: "auto",
  fontSize: "0.78rem",
  lineHeight: "1.45",
});
const taskdocDecisions = css({
  listStyle: "none",
  margin: "0",
  padding: "0",
  display: "grid",
  gap: "0.4rem",
  maxWidth: "78ch",
});
const taskdocDecision = css({ fontSize: "0.84rem" });
const taskdocDecisionMeta = css({ fontSize: "0.76rem", color: "muted" });

// The selected lifecycle: phase stepper, the Gate Review drawer (slice 6c — POSTs a developer
// decision to /api/actions, server-enforced at closeout) or the proto-gate ask banner fallback, the
// task-document content (analytics.taskDocuments), the lifecycle → worktree → provider spine, and tokens.
export function DetailPanel({ selectedId }: { selectedId: string | null }) {
  const lifecycle = useDashboard((s) => (selectedId ? s.lifecycles[selectedId] : undefined));
  const analytics = useDashboard((s) => s.analytics);
  const enclosures = useDashboard((s) => s.enclosures);
  const providers = useDashboard((s) => s.providers);
  const docs = selectedId
    ? (analytics?.taskDocuments ?? []).filter((doc) => doc.lifecycleId === selectedId)
    : [];

  if (!lifecycle) {
    return (
      <Panel testid="detail-panel" title="Detail" className={sizing}>
        <p className="muted">Select a session to inspect its phase, gate, and tokens.</p>
      </Panel>
    );
  }

  const currentIdx = PHASES.indexOf(lifecycle.phase);
  const askQuestion = lifecycle.ask
    ? String((lifecycle.ask as { question?: unknown }).question ?? "awaiting input")
    : null;

  const enclosure = lifecycle.enclosure ? enclosures[lifecycle.enclosure] : undefined;
  const groupName = enclosure ? (enclosure.worktreeGroup.split("/").filter(Boolean).pop() ?? "") : "";
  const engines = groupName
    ? Object.values(providers).filter((p) => p.scope === "worktree" && p.worktreeGroup === groupName)
    : [];

  return (
    <Panel testid="detail-panel" title={lifecycle.id} className={sizing}>
      <div className={where}>
        {lifecycle.fleeting
          ? "fleeting · no worktree"
          : `persistent worktree · ${lifecycle.repoId ?? "—"}`}
        {lifecycle.inferred ? " · inferred" : ""}
      </div>

      <ol className={stepper} aria-label="phase">
        {PHASES.map((phase, i) => (
          <li
            key={phase}
            className={step({ state: i < currentIdx ? "done" : i === currentIdx ? "current" : "todo" })}
          >
            {phase}
          </li>
        ))}
      </ol>

      {lifecycle.gate ? (
        <GateReview lifecycleId={lifecycle.id} gateNode={lifecycle.gate} />
      ) : askQuestion ? (
        <div className={gate} data-testid="gate-banner">
          <div>
            <strong>Gate</strong> · {askQuestion}
          </div>
          <p className={gateNote}>awaiting the agent — no durable gate opened yet</p>
        </div>
      ) : null}

      <TaskContent docs={docs} />

      {enclosure ? (
        <div className={spine}>
          <div className={spineHead}>worktree · {groupName || enclosure.repoName}</div>
          <div className={lanes}>
            <SpineLane
              kind="code"
              title="code → CGC"
              repo={enclosure.repoName}
              engines={engines.filter((engine) => engine.role === "code")}
            />
            <SpineLane
              kind="memory"
              title="memory → GrepAI"
              repo={`ar-${enclosure.repoName}`}
              engines={engines.filter((engine) => engine.role === "memory")}
            />
          </div>
        </div>
      ) : null}

      <div className={tokensRow}>
        <span className={label}>tokens</span>
        <TokenGauge series={lifecycle.tokenSeries} />
      </div>
    </Panel>
  );
}

const GATE_STATUS_TEXT: Record<GateDecisionStatus, string> = {
  idle: "your decision is recorded server-side and enforced at closeout",
  posting: "recording…",
  recorded: "decision recorded",
  "no-open-gate": "no open gate (already decided?)",
  error: "could not reach the server — retry",
};

// The Gate Review drawer (slice 6c): the durable gate's decision verbs as real buttons that POST to
// /api/actions (6b records the developer/dashboard decision; closeout enforces it). Status is honest —
// posting → recorded / no-open-gate / error — never a fake "sent".
function GateReview({ lifecycleId, gateNode }: { lifecycleId: string; gateNode: GateNode }) {
  const [status, setStatus] = useState<GateDecisionStatus>("idle");
  const decide = async (verb: string) => {
    setStatus("posting");
    setStatus(await postGateDecision(lifecycleId, verb));
  };
  return (
    <div className={gate} data-testid="gate-review">
      <div>
        <strong>Gate</strong> · {gateNode.kind} · {gateNode.state}
      </div>
      <div className={gateActions}>
        {gateNode.decisions.map((verb) => (
          <button
            key={verb}
            type="button"
            className={gateButton}
            disabled={status === "posting"}
            onClick={() => decide(verb)}
            data-testid={`gate-${verb}`}
          >
            {verb}
          </button>
        ))}
      </div>
      <p className={gateNote} data-testid="gate-status">
        {GATE_STATUS_TEXT[status]}
      </p>
    </div>
  );
}

function TaskContent({ docs }: { docs: TaskDocNode[] }) {
  if (docs.length === 0) {
    return <p className="muted">No task document bound to this lifecycle.</p>;
  }
  if (docs.length === 1) {
    return <TaskReader doc={docs[0]} />;
  }
  return (
    <div className={series}>
      <div className={taskHead}>
        <span className={badge}>series</span> {docs.length} task slices
      </div>
      <ul className={slices}>
        {[...docs]
          .sort((a, b) => a.title.localeCompare(b.title))
          .map((doc) => (
            <li key={doc.docPath} className={slice}>
              <span>{doc.title}</span>
              <span className={sliceMeta}>
                {doc.stepsDone}/{doc.stepsTotal} · {doc.status}
              </span>
            </li>
          ))}
      </ul>
    </div>
  );
}

function SpineLane({
  kind,
  title,
  repo,
  engines,
}: {
  kind: "code" | "memory";
  title: string;
  repo: string;
  engines: ProviderNode[];
}) {
  return (
    <div className={lane({ kind })}>
      <div className={laneTitle}>{title}</div>
      <div className={laneRepo}>{repo}</div>
      {engines.length > 0 ? (
        <div className={laneMeta}>
          {engines.map((engine) => (
            <span key={engine.id}>{engine.state}</span>
          ))}
        </div>
      ) : (
        <span className="muted">no isolated engine recorded</span>
      )}
    </div>
  );
}

function TaskReader({ doc }: { doc: TaskDocNode }) {
  return (
    <div className={taskdoc}>
      <div className={taskdocHead}>
        <span className={badge}>{doc.kind}</span>
        <span className={taskdocTitle}>{doc.title}</span>
        <span className={taskdocStatus}>{doc.status}</span>
        <ProgressFill completed={doc.stepsDone} total={doc.stepsTotal} label="steps done" />
      </div>
      {doc.objective ? (
        <Section title="Objective">
          <p className={taskdocP}>{doc.objective}</p>
        </Section>
      ) : null}
      {doc.requirements.length > 0 ? (
        <Section title="Requirements">
          <Bullets items={doc.requirements} />
        </Section>
      ) : null}
      {doc.design ? (
        <Section title="Design">
          <p className={taskdocP}>{doc.design}</p>
        </Section>
      ) : null}
      {doc.steps.length > 0 ? (
        <Section title="Implementation steps">
          <StepList steps={doc.steps} />
        </Section>
      ) : null}
      {doc.codeExamples.length > 0 ? (
        <Section title="Proposed code">
          {doc.codeExamples.map((example) => (
            <CodeExample key={example.id} example={example} />
          ))}
        </Section>
      ) : null}
      {doc.decisions.length > 0 ? (
        <Section title="Decision log">
          <DecisionList items={doc.decisions} />
        </Section>
      ) : null}
      {doc.openQuestions.length > 0 ? (
        <Section title="Open questions">
          <Bullets items={doc.openQuestions} />
        </Section>
      ) : null}
      {doc.references.length > 0 ? (
        <Section title="References">
          <Bullets items={doc.references} />
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={taskdocSection}>
      <h3 className={taskdocH}>{title}</h3>
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className={taskdocBullets}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function StepList({ steps }: { steps: TaskStepNode[] }) {
  return (
    <ol className={stepsList}>
      {steps.map((s) => (
        <li key={s.id} className={stepRow}>
          <span className={cx(stepMarkBase, STEP_MARK[s.status] ?? "")} aria-hidden="true" />
          <span className={STEP_TITLE[s.status] ?? ""}>{s.title}</span>
          {s.substeps.length > 0 ? (
            <ul className={substeps}>
              {s.substeps.map((sub) => (
                <li key={sub.id} className={SUBSTEP[sub.status] ?? ""}>
                  {sub.title}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function CodeExample({ example }: { example: TaskCodeExampleNode }) {
  return (
    <div className={taskdocCode}>
      <div className={taskdocCodeHead}>{example.title}</div>
      <div className={taskdocCodeMeta}>covers: {example.distinctChange}</div>
      <div className={taskdocCodeMeta}>why: {example.why}</div>
      {example.snippet ? <pre className={taskdocSnippet}>{example.snippet}</pre> : null}
    </div>
  );
}

function DecisionList({ items }: { items: TaskDecisionNode[] }) {
  return (
    <ul className={taskdocDecisions}>
      {items.map((item) => (
        <li key={`${item.at}:${item.decision}`}>
          <div className={taskdocDecision}>{item.decision}</div>
          <div className={taskdocDecisionMeta}>
            {item.at} — {item.rationale}
          </div>
        </li>
      ))}
    </ul>
  );
}
