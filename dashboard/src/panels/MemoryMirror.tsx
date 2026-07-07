import { css, cx } from "../../styled-system/css";
import { driftSegments, fmtWait } from "../data/selectors";
import { servedAgeSeconds, useNowMs } from "../data/servedAges";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";

// The memory mirror (mc2 harvest #2 — "a 1-to-1 mirror of the code"): a coverage/drift segmented
// bar per repo + ledger currency + the stalest-sidecar leaderboard. All read from the slice-3b
// analytics nodes — no reducer work. Ages are server-anchored (`snapshotStaleSeconds`/`ageSeconds`)
// and advanced locally between emissions (the 260703-L15 change gate — data/servedAges.ts).
const sizing = css({ flex: "1" });
const repo = css({ marginBottom: "0.7rem" });
const row = css({
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: "0.5rem",
  fontSize: "0.78rem",
});
const actionable = css({ color: "amber" });
const file = css({ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const sub = css({
  margin: "0.7rem 0 0.3rem",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "muted",
});
const list = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.15rem" });
const segbar = css({
  display: "flex",
  height: "0.75rem",
  marginTop: "0.25rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  overflow: "hidden",
  background: "bg",
});
const segBase = css({ height: "100%" });
// drift classifications are forward-compatible (the snapshot may report new ones), so map by a
// record rather than a typed cva variant; an unanticipated class renders with no fill.
const SEG_BG: Record<string, string> = {
  current: css({ background: "mint" }),
  ok: css({ background: "mint" }),
  drifted: css({ background: "amber" }),
  missingVerification: css({ background: "cyan" }),
  missing: css({ background: "alarm" }),
  orphaned: css({ background: "alarm" }),
  unsupported: css({ background: "alarm" }),
};

export function MemoryMirror() {
  const analytics = useDashboard((s) => s.analytics);
  const nowMs = useNowMs();
  const drift = analytics?.driftSnapshots ?? [];
  const ledgers = analytics?.ledgers ?? [];
  const stalest = analytics?.stalestSidecars ?? [];

  if (drift.length === 0 && ledgers.length === 0 && stalest.length === 0) {
    return (
      <Panel testid="memory-mirror" title="Memory mirror" className={sizing}>
        <p className="muted">No memory analytics yet.</p>
      </Panel>
    );
  }

  return (
    <Panel testid="memory-mirror" title="Memory mirror" className={sizing}>
      {drift.map((snapshot) => {
        const segments = driftSegments(snapshot);
        return (
          <div className={repo} key={`${snapshot.repository}:${snapshot.branch}`}>
            <div className={row}>
              <span>{snapshot.repository}</span>
              <span className={snapshot.actionableCount > 0 ? actionable : "muted"}>
                {snapshot.actionableCount} actionable ·{" "}
                {fmtWait(servedAgeSeconds(snapshot, snapshot.snapshotStaleSeconds, nowMs))}
              </span>
            </div>
            <div
              className={segbar}
              role="img"
              aria-label={`${snapshot.repository} drift: ${snapshot.actionableCount} actionable`}
            >
              {segments.map((segment) => (
                <span
                  key={segment.cls}
                  className={cx(segBase, SEG_BG[segment.cls] ?? "")}
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
          <h3 className={sub}>Ledger currency</h3>
          <ul className={list}>
            {ledgers.map((ledger) => (
              <li key={ledger.repository} className={row}>
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
          <h3 className={sub}>Stalest sidecars</h3>
          <ul className={list}>
            {stalest.slice(0, 6).map((sidecar) => (
              <li key={`${sidecar.repository}:${sidecar.onboardingFile}`} className={row}>
                <span className={file}>{sidecar.onboardingFile}</span>
                <span className="muted">
                  {fmtWait(servedAgeSeconds(sidecar, sidecar.ageSeconds, nowMs))}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}
