import type { ReactNode } from "react";

import { useDashboard } from "../data/store";
import { ProgressFill } from "../grammar/ProgressFill";
import { TokenGauge } from "../grammar/TokenGauge";
import type {
  Phase,
  ProviderNode,
  TaskCodeExampleNode,
  TaskDecisionNode,
  TaskDocNode,
  TaskStepNode,
} from "../types/projection";

// The l-01 phase vocabulary, in order (mcp/.../lifecycle_state.py). The stepper marks phases
// before the current as done — mc2's Request→Close mini-map.
const PHASES: Phase[] = [
  "request",
  "trust-checkpoint",
  "reframe-research",
  "decide",
  "build",
  "close",
];

// The selected lifecycle: phase stepper, the open-gate banner (display-only — the gate control
// plane is slice 06), the task-document checklist (analytics.taskDocuments), and the token gauge.
export function DetailPanel({ selectedId }: { selectedId: string | null }) {
  const lifecycle = useDashboard((s) => (selectedId ? s.lifecycles[selectedId] : undefined));
  const analytics = useDashboard((s) => s.analytics);
  const enclosures = useDashboard((s) => s.enclosures);
  const providers = useDashboard((s) => s.providers);
  // All task docs bound to this lifecycle: one => single task; several => a multi-task series
  // (the subtask slices). Filtered in render (not a store selector) so it stays a stable read.
  const docs = selectedId
    ? (analytics?.taskDocuments ?? []).filter((doc) => doc.lifecycleId === selectedId)
    : [];

  if (!lifecycle) {
    return (
      <section className="panel detail" data-testid="detail-panel">
        <h2>Detail</h2>
        <p className="muted">Select a session to inspect its phase, gate, and tokens.</p>
      </section>
    );
  }

  const currentIdx = PHASES.indexOf(lifecycle.phase);
  const askQuestion = lifecycle.ask
    ? String((lifecycle.ask as { question?: unknown }).question ?? "awaiting input")
    : null;

  // The lifecycle → worktree → provider spine: its enclosure (worktree wrapper) and the isolated
  // engines that worktree spawned (joined by group name; CGC serves the code repo, GrepAI memory).
  const enclosure = lifecycle.enclosure ? enclosures[lifecycle.enclosure] : undefined;
  const groupName = enclosure ? (enclosure.worktreeGroup.split("/").filter(Boolean).pop() ?? "") : "";
  const engines = groupName
    ? Object.values(providers).filter((p) => p.scope === "worktree" && p.worktreeGroup === groupName)
    : [];

  return (
    <section className="panel detail" data-testid="detail-panel">
      <h2>{lifecycle.id}</h2>
      <div className="detail__where">
        {lifecycle.fleeting
          ? "fleeting · no worktree"
          : `persistent worktree · ${lifecycle.repoId ?? "—"}`}
        {lifecycle.inferred ? " · inferred" : ""}
      </div>

      <ol className="stepper" aria-label="phase">
        {PHASES.map((phase, i) => (
          <li
            key={phase}
            className={[
              "stepper__step",
              i < currentIdx ? "is-done" : "",
              i === currentIdx ? "is-current" : "",
            ]
              .filter(Boolean)
              .join(" ")}
          >
            {phase}
          </li>
        ))}
      </ol>

      {askQuestion ? (
        <div className="gate" data-testid="gate-banner">
          <div className="gate__head">
            <strong>Gate</strong> · {askQuestion}
          </div>
          <div className="gate__actions">
            {lifecycle.actions.map((action) => (
              <span
                key={action.action}
                className={`chip${action.enabled ? "" : " chip--disabled"}`}
                title={action.disabledReason ?? undefined}
              >
                {action.action}
              </span>
            ))}
          </div>
          <p className="gate__note">display-only — gate control plane is slice 06</p>
        </div>
      ) : null}

      <TaskContent docs={docs} />

      {enclosure ? (
        <div className="detail__spine">
          <div className="detail__spine-head">worktree · {groupName || enclosure.repoName}</div>
          <div className="detail__lanes">
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

      <div className="detail__tokens">
        <span className="detail__label">tokens</span>
        <TokenGauge series={lifecycle.tokenSeries} />
      </div>
    </section>
  );
}

// The task content (note: "the task contents we capture via JSON didn't show up at all"). One doc
// is a single task (its step progress); several docs bound to the same lifecycle are a multi-task
// series (the subtask slices). Step granularity beyond the counts awaits a TaskDocNode enrichment.
function TaskContent({ docs }: { docs: TaskDocNode[] }) {
  if (docs.length === 0) {
    return <p className="muted">No task document bound to this lifecycle.</p>;
  }
  if (docs.length === 1) {
    return <TaskReader doc={docs[0]} />;
  }
  return (
    <div className="detail__series">
      <div className="detail__task-head">
        <span className="badge">series</span> {docs.length} task slices
      </div>
      <ul className="detail__slices">
        {[...docs]
          .sort((a, b) => a.title.localeCompare(b.title))
          .map((doc) => (
            <li key={doc.docPath} className="detail__slice">
              <span className="detail__slice-title">{doc.title}</span>
              <span className="detail__slice-meta">
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
    <div className={`detail__lane detail__lane--${kind}`}>
      <div className="detail__lane-title">{title}</div>
      <div className="detail__lane-repo">{repo}</div>
      {engines.length > 0 ? (
        <div className="engine__meta">
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

// The task reader: the JSON task document rendered in the dashboard so you read the content here,
// not in the filesystem — objective, requirements, design, steps, proposed code, decisions,
// open questions, references. Only sections with content render.
function TaskReader({ doc }: { doc: TaskDocNode }) {
  return (
    <div className="taskdoc">
      <div className="taskdoc__head">
        <span className="badge">{doc.kind}</span>
        <span className="taskdoc__title">{doc.title}</span>
        <span className="taskdoc__status">{doc.status}</span>
        <ProgressFill completed={doc.stepsDone} total={doc.stepsTotal} label="steps done" />
      </div>
      {doc.objective ? (
        <Section title="Objective">
          <p className="taskdoc__p">{doc.objective}</p>
        </Section>
      ) : null}
      {doc.requirements.length > 0 ? (
        <Section title="Requirements">
          <Bullets items={doc.requirements} />
        </Section>
      ) : null}
      {doc.design ? (
        <Section title="Design">
          <p className="taskdoc__p">{doc.design}</p>
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
    <section className="taskdoc__section">
      <h3 className="taskdoc__h">{title}</h3>
      {children}
    </section>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="taskdoc__bullets">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function StepList({ steps }: { steps: TaskStepNode[] }) {
  return (
    <ol className="detail__steps">
      {steps.map((step) => (
        <li key={step.id} className={`detail__step is-${step.status}`}>
          <span className="detail__step-mark" aria-hidden="true" />
          <span className="detail__step-title">{step.title}</span>
          {step.substeps.length > 0 ? (
            <ul className="detail__substeps">
              {step.substeps.map((sub) => (
                <li key={sub.id} className={`detail__substep is-${sub.status}`}>
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
    <div className="taskdoc__code">
      <div className="taskdoc__code-head">{example.title}</div>
      <div className="taskdoc__code-meta">covers: {example.distinctChange}</div>
      <div className="taskdoc__code-meta">why: {example.why}</div>
      {example.snippet ? <pre className="taskdoc__snippet">{example.snippet}</pre> : null}
    </div>
  );
}

function DecisionList({ items }: { items: TaskDecisionNode[] }) {
  return (
    <ul className="taskdoc__decisions">
      {items.map((item) => (
        <li key={`${item.at}:${item.decision}`}>
          <div className="taskdoc__decision">{item.decision}</div>
          <div className="taskdoc__decision-meta">
            {item.at} — {item.rationale}
          </div>
        </li>
      ))}
    </ul>
  );
}
