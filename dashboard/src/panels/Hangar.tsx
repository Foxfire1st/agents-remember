import { fmtWait } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Affordance } from "../grammar/Affordance";
import type { EnclosureNode, LifecycleProjection } from "../types/projection";

// The hangar (notes 01/06): persistent worktree-backed lifecycles are NEVER auto-reaped — when
// they rot, this is where the staleness surfaces for the developer to step in (the TTL reaper is
// fleeting-only). Lists every worktree enclosure with its closeout/integration/cleanup status +
// the cross-ref lifecycle's staleness, with display-only integrate/cleanup affordances (06 POSTs).
function isStale(enclosure: EnclosureNode, lifecycle: LifecycleProjection | undefined): boolean {
  if (enclosure.cleanup === "pending" || enclosure.integrationStatus === "completed") return true;
  return Boolean(lifecycle?.inferred);
}

export function Hangar({ onSelect }: { onSelect: (id: string) => void }) {
  const enclosures = useDashboard((s) => s.enclosures);
  const lifecycles = useDashboard((s) => s.lifecycles);
  const rows = Object.values(enclosures).sort((a, b) => a.enclosure.localeCompare(b.enclosure));

  return (
    <section className="panel hangar" data-testid="hangar">
      <h2>Hangar · {rows.length} worktrees</h2>
      {rows.length === 0 ? (
        <p className="muted">Hangar empty — no persistent worktrees.</p>
      ) : (
        <ul className="hangar__list">
          {rows.map((enclosure) => {
            const lifecycle = enclosure.lifecycleId
              ? lifecycles[enclosure.lifecycleId]
              : undefined;
            const stale = isStale(enclosure, lifecycle);
            return (
              <li
                key={enclosure.enclosure}
                className={`hangar__row${stale ? " hangar__row--stale" : ""}`}
                data-testid="hangar-row"
              >
                <div className="hangar__head">
                  <button
                    type="button"
                    className="ghost"
                    disabled={!enclosure.lifecycleId}
                    onClick={() => enclosure.lifecycleId && onSelect(enclosure.lifecycleId)}
                  >
                    {enclosure.repoName} · {enclosure.taskName}
                  </button>
                  <span className="muted">{fmtWait(lifecycle?.staleSeconds)}</span>
                </div>
                <div className="hangar__badges">
                  <span className="badge">review {enclosure.humanReviewStatus}</span>
                  <span className="badge">closeout {enclosure.closeoutStatus}</span>
                  <span className="badge">integrate {enclosure.integrationStatus}</span>
                  <span className="badge">cleanup {enclosure.cleanup}</span>
                </div>
                {enclosure.actions.length > 0 ? (
                  <div className="hangar__actions">
                    {enclosure.actions.map((action) => (
                      <Affordance key={action.action} action={action} />
                    ))}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
