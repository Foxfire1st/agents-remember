import { driftSegments, fmtWait } from "../data/selectors";
import { useDashboard } from "../data/store";

// The memory mirror (mc2 harvest #2 — "a 1-to-1 mirror of the code"): a coverage/drift segmented
// bar per repo + ledger currency + the stalest-sidecar leaderboard. All read from the slice-3b
// analytics nodes — no reducer work. Ages are server-computed (`snapshotStaleSeconds`/`ageSeconds`).
export function MemoryMirror() {
  const analytics = useDashboard((s) => s.analytics);
  const drift = analytics?.driftSnapshots ?? [];
  const ledgers = analytics?.ledgers ?? [];
  const stalest = analytics?.stalestSidecars ?? [];

  if (drift.length === 0 && ledgers.length === 0 && stalest.length === 0) {
    return (
      <section className="panel mirror" data-testid="memory-mirror">
        <h2>Memory mirror</h2>
        <p className="muted">No memory analytics yet.</p>
      </section>
    );
  }

  return (
    <section className="panel mirror" data-testid="memory-mirror">
      <h2>Memory mirror</h2>

      {drift.map((snapshot) => {
        const segments = driftSegments(snapshot);
        return (
          <div className="mirror__repo" key={`${snapshot.repository}:${snapshot.branch}`}>
            <div className="mirror__row">
              <span>{snapshot.repository}</span>
              <span className={snapshot.actionableCount > 0 ? "mirror__actionable" : "muted"}>
                {snapshot.actionableCount} actionable · {fmtWait(snapshot.snapshotStaleSeconds)}
              </span>
            </div>
            <div
              className="segbar"
              role="img"
              aria-label={`${snapshot.repository} drift: ${snapshot.actionableCount} actionable`}
            >
              {segments.map((segment) => (
                <span
                  key={segment.cls}
                  className={`segbar__seg seg--${segment.cls}`}
                  style={{ width: `${segment.pct}%` }}
                  title={`${segment.cls}: ${segment.count}`}
                />
              ))}
            </div>
          </div>
        );
      })}

      {ledgers.length > 0 ? (
        <>
          <h3 className="mirror__sub">Ledger currency</h3>
          <ul className="mirror__list">
            {ledgers.map((ledger) => (
              <li key={ledger.repository} className="mirror__row">
                <span>{ledger.repository}</span>
                <span className="muted">
                  {ledger.closeoutCount} closeouts · @{ledger.lastVerifiedCodeCommit.slice(0, 7)}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {stalest.length > 0 ? (
        <>
          <h3 className="mirror__sub">Stalest sidecars</h3>
          <ul className="mirror__list">
            {stalest.slice(0, 6).map((sidecar) => (
              <li key={`${sidecar.repository}:${sidecar.onboardingFile}`} className="mirror__row">
                <span className="mirror__file">{sidecar.onboardingFile}</span>
                <span className="muted">{fmtWait(sidecar.ageSeconds)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
