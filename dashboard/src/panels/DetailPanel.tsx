import { useDashboard } from "../data/store";
import { ProgressFill } from "../grammar/ProgressFill";
import { TokenGauge } from "../grammar/TokenGauge";
import type { Phase } from "../types/projection";

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
  const taskDoc = useDashboard((s) =>
    selectedId ? s.analytics?.taskDocuments.find((doc) => doc.lifecycleId === selectedId) : undefined,
  );

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

  return (
    <section className="panel detail" data-testid="detail-panel">
      <h2>{lifecycle.id}</h2>

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

      {taskDoc ? (
        <div className="detail__task">
          <div className="detail__task-head">
            {taskDoc.title} · {taskDoc.status}
          </div>
          <ProgressFill completed={taskDoc.stepsDone} total={taskDoc.stepsTotal} label="steps done" />
          {taskDoc.currentStep ? <div className="muted">→ {taskDoc.currentStep}</div> : null}
        </div>
      ) : null}

      <div className="detail__tokens">
        <span className="detail__label">tokens</span>
        <TokenGauge series={lifecycle.tokenSeries} />
      </div>
    </section>
  );
}
