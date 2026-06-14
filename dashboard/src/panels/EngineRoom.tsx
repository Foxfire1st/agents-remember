import { engineState, fmtWait, groupEngines } from "../data/selectors";
import { useDashboard } from "../data/store";
import type { ProviderNode } from "../types/projection";

// note 08 semantic map: a worktree's provider stack is the podracer's twin engines — CGC (its code
// repo) + GrepAI (its memory repo), bound by the coordination runtime. The engine room shows the
// workspace stack AND every worktree's *own* isolated stack (note 03 surfaces 1 + 4), so the
// lifecycle → worktree → provider connection is visible — not just main's providers. State is
// carried by colour + silhouette: nominal amber / indexing cyan fill / down red alarm.
const ROLE_LABEL: Record<string, string> = { code: "CGC · code", memory: "GrepAI · memory" };

export function EngineRoom() {
  const providers = useDashboard((s) => s.providers);
  const stacks = groupEngines(Object.values(providers));
  return (
    <section className="panel engine" data-testid="engine-room">
      <h2>Engine room · {stacks.length} stacks</h2>
      {stacks.length === 0 ? (
        <p className="muted">No providers reporting.</p>
      ) : (
        <div className="engine__stacks">
          {stacks.map((stack) => (
            <div
              key={stack.key}
              className={`engine__stack engine__stack--${stack.scope}`}
              data-testid="engine-stack"
            >
              <div className="engine__stack-head">
                <span className="engine__stack-name">
                  {stack.scope === "workspace" ? "Workspace" : stack.key}
                </span>
                {stack.repoId ? <span className="engine__stack-repo">{stack.repoId}</span> : null}
              </div>
              <div className="engine__grid">
                {stack.engines.map((provider) => (
                  <Engine key={provider.id} provider={provider} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Engine({ provider }: { provider: ProviderNode }) {
  const state = engineState(provider);
  return (
    <div
      className={`engine__unit engine__unit--${state}`}
      data-testid="engine-unit"
      data-state={state}
    >
      <div className="engine__name">{ROLE_LABEL[provider.role ?? ""] ?? provider.id}</div>
      <div className="engine__silhouette" role="img" aria-label={`engine ${state}`} />
      <div className="engine__meta">
        <span>{provider.state}</span>
        <span>{provider.indexingState}</span>
        <span>snapshot {fmtWait(provider.snapshotStaleSeconds)}</span>
      </div>
    </div>
  );
}
