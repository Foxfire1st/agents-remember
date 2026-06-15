import { css, cva } from "../../styled-system/css";
import { engineState, fmtWait, groupEngines } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { ProviderNode } from "../types/projection";

// note 08 semantic map: a worktree's provider stack is the podracer's twin engines — CGC (its code
// repo) + GrepAI (its memory repo). The engine room shows the workspace stack AND every worktree's
// *own* isolated stack (note 03 surfaces 1 + 4), so the lifecycle → worktree → provider connection
// is visible. State is carried by colour + silhouette: nominal amber / indexing cyan / down alarm.
const ROLE_LABEL: Record<string, string> = { code: "CGC · code", memory: "GrepAI · memory" };

const sizing = css({ flex: "1" });
const stackList = css({ display: "grid", gap: "0.8rem" });
const stack = cva({
  base: {
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "3px",
    padding: "0.5rem 0.6rem",
    borderLeftWidth: "3px",
  },
  variants: {
    scope: {
      workspace: { borderLeftColor: "amber" },
      worktree: { borderLeftColor: "cyan" },
    },
  },
});
const stackHead = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  marginBottom: "0.45rem",
});
const stackName = css({ color: "ink", fontSize: "0.82rem", letterSpacing: "0.04em" });
const stackRepo = css({ color: "muted", fontSize: "0.72rem" });
const grid = css({
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
  gap: "0.7rem",
});
const unit = css({
  display: "grid",
  gap: "0.5rem",
  padding: "0.7rem",
  background: "bg",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
});
const engineName = cva({
  base: { color: "amber", fontSize: "0.82rem", letterSpacing: "0.05em" },
  variants: { state: { nominal: {}, indexing: {}, down: { color: "alarm" } } },
});
const silhouette = cva({
  base: {
    height: "64px",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "amber",
    borderRadius: "4px",
    background: "repeating-linear-gradient(0deg, transparent 0 6px, oklch(0.82 0.16 75 / 0.1) 6px 7px)",
  },
  variants: {
    state: {
      nominal: {},
      indexing: {
        borderColor: "cyan",
        background: "linear-gradient(0deg, oklch(0.85 0.13 200 / 0.4) 0%, transparent 70%)",
      },
      down: {
        borderColor: "alarm",
        background: "oklch(0.63 0.24 25 / 0.18)",
        animation: "pulse 0.6s steps(1) infinite",
      },
    },
  },
});
const meta = css({
  display: "flex",
  flexWrap: "wrap",
  gap: "0.5rem",
  fontSize: "0.72rem",
  color: "muted",
});

export function EngineRoom() {
  const providers = useDashboard((s) => s.providers);
  const stacks = groupEngines(Object.values(providers));
  return (
    <Panel testid="engine-room" title={`Engine room · ${stacks.length} stacks`} className={sizing}>
      {stacks.length === 0 ? (
        <p className="muted">No providers reporting.</p>
      ) : (
        <div className={stackList}>
          {stacks.map((s) => (
            <div key={s.key} className={stack({ scope: s.scope })} data-testid="engine-stack">
              <div className={stackHead}>
                <span className={stackName}>{s.scope === "workspace" ? "Workspace" : s.key}</span>
                {s.repoId ? <span className={stackRepo}>{s.repoId}</span> : null}
              </div>
              <div className={grid}>
                {s.engines.map((provider) => (
                  <Engine key={provider.id} provider={provider} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Engine({ provider }: { provider: ProviderNode }) {
  const state = engineState(provider);
  return (
    <div className={unit} data-testid="engine-unit" data-state={state}>
      <div className={engineName({ state })}>{ROLE_LABEL[provider.role ?? ""] ?? provider.id}</div>
      <div className={silhouette({ state })} role="img" aria-label={`engine ${state}`} />
      <div className={meta}>
        <span>{provider.state}</span>
        <span>{provider.indexingState}</span>
        <span>snapshot {fmtWait(provider.snapshotStaleSeconds)}</span>
      </div>
    </div>
  );
}
