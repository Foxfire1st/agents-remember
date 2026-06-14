import { fmtWait } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";

// Active lifecycles (note 06): phase, repo, age. Fleeting sessions are visually bare vs
// persistent (worktree-backed) ones — the distinction comes from the projection's `fleeting`,
// not UI inference. Age is the server-computed `staleSeconds`, never render time.
export function SessionStrip({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const lifecycles = useDashboard((s) => s.lifecycles);
  const sessions = Object.values(lifecycles);
  return (
    <section className="panel strip" data-testid="session-strip">
      <h2>Sessions · {sessions.length}</h2>
      {sessions.length === 0 ? (
        <p className="muted">No active sessions.</p>
      ) : (
        <ul className="strip__list">
          {sessions.map((lifecycle) => (
            <li key={lifecycle.id}>
              <button
                type="button"
                className={[
                  "strip__item",
                  lifecycle.fleeting ? "strip__item--fleeting" : "",
                  lifecycle.id === selectedId ? "is-selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => onSelect(lifecycle.id)}
              >
                <Dot variant={lifecycle.state} />
                <span className="strip__id">{lifecycle.id}</span>
                <span className="strip__phase">{lifecycle.phase}</span>
                <span className="strip__meta">
                  {lifecycle.repoId ?? "—"} · {fmtWait(lifecycle.staleSeconds)}
                  {lifecycle.inferred ? " · inferred" : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
