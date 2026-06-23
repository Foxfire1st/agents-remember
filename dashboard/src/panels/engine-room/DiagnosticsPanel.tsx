import { css } from "../../../styled-system/css";
import { fmtWait } from "../../data/selectors";
import { Affordance } from "../../grammar/Affordance";
import type { CommitRefNode, EngineProcessNode, GateNode } from "../../types/projection";
import { GateResponder, isWorktreeGateKind } from "../GateResponder";
import {
  actionRow,
  diagKey,
  diagPanel,
  diagRow,
  diagValue,
  factChip,
  missingNotice,
  missingTitle,
  phaseLineList,
  sectionLabel,
} from "./engineRoomStyles";

function CommitRow({ label, refNode }: { label: string; refNode: CommitRefNode }) {
  const ref = [refNode.branch, refNode.commit?.slice(0, 8)].filter(Boolean).join(" @ ");
  const flags = [
    refNode.exists === false ? "absent" : undefined,
    refNode.dirty ? "dirty" : undefined,
    refNode.behindSource ? `${refNode.behindSource} behind` : undefined,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className={diagRow}>
      <span className={diagKey}>{label}</span>
      <span className={diagValue}>
        <span className={factChip({ factState: refNode.factState })}>{refNode.factState}</span>{" "}
        {ref || "—"}
        {flags ? ` (${flags})` : ""}
      </span>
    </div>
  );
}

export function DiagnosticsPanel({
  node,
  lifecycleId,
  gateNode,
}: {
  node: EngineProcessNode;
  lifecycleId?: string;
  gateNode?: GateNode;
}) {
  // 5k F3 — during the power-down (cleanup-pending / abandon) the providers are being torn down, so the
  // diagnostics must not keep reading "ok": show "powering down" and de-emphasize the (now-stale) provider
  // lines. Derived from `phase` on the frontend (the live runtime is pre-05m, so it sends no power-down signal).
  const poweringDown = node.phase === "cleanup-pending" || node.phase === "abandoned";
  const setupLine = poweringDown
    ? ["powering down", node.currentPhase].filter(Boolean).join(" · ")
    : [
        node.setupState,
        node.heartbeatAgeSeconds !== undefined ? `heartbeat ${fmtWait(node.heartbeatAgeSeconds)}` : undefined,
        node.currentPhase,
      ]
        .filter(Boolean)
        .join(" · ");
  return (
    <div className={diagPanel} data-testid="diagnostics">
      <span className={sectionLabel}>Diagnostics</span>
      {node.summary ? (
        <p className={css({ color: "ink", fontSize: "0.74rem", margin: "0" })}>{node.summary}</p>
      ) : null}
      <div className={diagRow}>
        <span className={diagKey}>Phase</span>
        <span className={diagValue}>{node.phase}</span>
      </div>
      <div className={diagRow}>
        <span className={diagKey}>Health</span>
        <span className={diagValue}>{node.health}</span>
      </div>
      {node.nextAction ? (
        <div className={diagRow}>
          <span className={diagKey}>Next</span>
          <span className={diagValue}>{node.nextAction}</span>
        </div>
      ) : null}
      <CommitRow label="Code source" refNode={node.codeSource} />
      <CommitRow label="Code worktree" refNode={node.codeWorktree} />
      {node.memorySource ? <CommitRow label="Memory source" refNode={node.memorySource} /> : null}
      {node.memoryWorktree ? <CommitRow label="Memory worktree" refNode={node.memoryWorktree} /> : null}
      {setupLine ? (
        <div className={diagRow}>
          <span className={diagKey}>Provider setup</span>
          <span className={diagValue}>{setupLine}</span>
        </div>
      ) : null}
      {node.completedPhases.length > 0 || node.failedPhases.length > 0 ? (
        <ul className={phaseLineList}>
          {node.completedPhases.map((line) => (
            <li key={line} className={css({ color: poweringDown ? "muted" : "mint" })}>
              {poweringDown ? "◦" : "✓"} {line}
            </li>
          ))}
          {node.failedPhases.map((line) => (
            <li key={line} className={css({ color: "alarm" })}>
              ✗ {line}
            </li>
          ))}
        </ul>
      ) : null}
      {node.seedFallback ? (
        <div className={diagRow}>
          <span className={diagKey}>Seed</span>
          <span className={diagValue}>reroute → reindex fallback</span>
        </div>
      ) : null}
      {lifecycleId && gateNode && isWorktreeGateKind(gateNode.kind) ? (
        <div className={actionRow}>
          <GateResponder
            lifecycleId={lifecycleId}
            gateNode={gateNode}
            compact
            testId="engine-gate-responder"
          />
        </div>
      ) : node.actions.length > 0 ? (
        <div className={actionRow}>
          {node.actions.map((action) => (
            <Affordance key={action.action} action={action} />
          ))}
        </div>
      ) : null}
      {node.missingFacts.length > 0 ? (
        <div className={missingNotice} data-testid="missing-facts">
          <span className={missingTitle}>Missing observability</span>
          {node.missingFacts.map((fact) => (
            <span key={fact}>· {fact}</span>
          ))}
        </div>
      ) : null}
      {node.sourceFiles.length > 0 ? (
        <div className={diagRow}>
          <span className={diagKey}>Sources</span>
          <span className={css({ color: "muted", fontSize: "0.64rem", wordBreak: "break-all" })}>
            {node.sourceFiles.join("  ·  ")}
          </span>
        </div>
      ) : null}
    </div>
  );
}
