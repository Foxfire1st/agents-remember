import { css, cva } from "../../styled-system/css";
import { fmtWait, hasLiveWorktree } from "../data/selectors";
import { servedAgeSeconds, useNowMs } from "../data/servedAges";
import { useDashboard } from "../data/store";
import { Affordance } from "../grammar/Affordance";
import { Panel } from "../grammar/Panel";
import type { EnclosureNode, LifecycleProjection } from "../types/projection";
import { GateResponder, isWorktreeGateKind } from "./GateResponder";

// The hangar (notes 01/06): persistent worktree-backed lifecycles are NEVER auto-reaped — when
// they rot, this is where the staleness surfaces for the developer (the TTL reaper is fleeting-only).
// Lists every LIVE worktree enclosure with its closeout/integration/cleanup status + the cross-ref
// lifecycle's staleness, with display-only integrate/cleanup affordances (06 POSTs).
//
// A finalized worktree keeps its enclosure CONTRACT on disk (it records the landed state for memory
// lineage) even after its directory is reaped, so the raw enclosure set only ever grows. The hangar is
// about worktrees that still physically exist / need action, so it renders a row ONLY while a worktree
// physically exists — the projection's stat'ed hasLiveWorktree truth (L11), never a cleanup-state
// proxy. Completed/abandoned enclosures drop out by that same rule (their worktrees were reaped), and
// a reopened contract (cleanup=reopened: contract-reset-awaiting-restart) stays hidden until
// worktree_start recreates its worktrees.

function isStale(enclosure: EnclosureNode, lifecycle: LifecycleProjection | undefined): boolean {
  if (enclosure.cleanup === "pending" || enclosure.integrationStatus === "completed") return true;
  return Boolean(lifecycle?.inferred);
}

const sizing = css({ flex: "1" });
const list = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.5rem" });
const row = cva({
  base: {
    display: "grid",
    gap: "0.3rem",
    padding: "0.5rem 0.6rem",
    background: "bg",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderLeftWidth: "3px",
    borderLeftColor: "dormant",
    borderRadius: "3px",
  },
  variants: { stale: { true: { borderLeftColor: "amber" } } },
});
const head = css({
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: "0.5rem",
});
const ghost = css({
  font: "inherit",
  color: "inherit",
  background: "transparent",
  borderStyle: "none",
  cursor: "pointer",
  textAlign: "left",
});
const badges = css({ display: "flex", flexWrap: "wrap", gap: "0.3rem" });
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
const actions = css({ display: "flex", gap: "0.3rem" });

export function Hangar({ onSelect }: { onSelect: (id: string) => void }) {
  const enclosures = useDashboard((s) => s.enclosures);
  const lifecycles = useDashboard((s) => s.lifecycles);
  // Served ages advance locally between emissions — the change gate (260703-L15) no longer
  // re-serves a node whose only movement is its age, so the display ticks here instead.
  const nowMs = useNowMs();
  const rows = Object.values(enclosures)
    .filter(hasLiveWorktree)
    .sort((a, b) => a.enclosure.localeCompare(b.enclosure));

  return (
    <Panel testid="hangar" title={`Hangar · ${rows.length} worktrees`} className={sizing}>
      {rows.length === 0 ? (
        <p className="muted">Hangar empty — no live persistent worktrees.</p>
      ) : (
        <ul className={list}>
          {rows.map((enclosure) => {
            const lifecycle = enclosure.lifecycleId
              ? lifecycles[enclosure.lifecycleId]
              : undefined;
            const stale = isStale(enclosure, lifecycle);
            const lifecycleId = enclosure.lifecycleId;
            return (
              <li key={enclosure.enclosure} className={row({ stale })} data-testid="hangar-row">
                <div className={head}>
                  <button
                    type="button"
                    className={ghost}
                    disabled={!lifecycleId}
                    onClick={() => lifecycleId && onSelect(lifecycleId)}
                  >
                    {enclosure.repoName} · {enclosure.taskName}
                  </button>
                  <span className="muted">
                    {fmtWait(servedAgeSeconds(lifecycle, lifecycle?.staleSeconds, nowMs))}
                  </span>
                </div>
                <div className={badges}>
                  <span className={badge}>review {enclosure.humanReviewStatus}</span>
                  <span className={badge}>closeout {enclosure.closeoutStatus}</span>
                  <span className={badge}>integrate {enclosure.integrationStatus}</span>
                  <span className={badge}>cleanup {enclosure.cleanup}</span>
                </div>
                {lifecycleId && lifecycle?.gate && isWorktreeGateKind(lifecycle.gate.kind) ? (
                  <div className={actions}>
                    <GateResponder
                      lifecycleId={lifecycleId}
                      gateNode={lifecycle.gate}
                      compact
                      testId="hangar-gate-responder"
                    />
                  </div>
                ) : enclosure.actions.length > 0 ? (
                  <div className={actions}>
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
    </Panel>
  );
}
