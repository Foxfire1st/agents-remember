import { css } from "../../../styled-system/css";
import type { CommitRefNode, EngineProcessNode, ProviderBootNode } from "../../types/projection";
import {
  conduit,
  couplerBar,
  engineMeta,
  engineName,
  engineSilhouette,
  engineState,
  nodeBox,
  nodeBranch,
  nodeCommit,
  nodeLabel,
  sectionLabel,
} from "./engineRoomStyles";

type ConduitState =
  | "nominal"
  | "complete"
  | "running"
  | "blocked"
  | "failed"
  | "stale"
  | "skipped"
  | "planned"
  | "unknown";

type RuntimeState = "nominal" | "configured" | "indexing" | "down" | "unknown";

const mapWrap = css({
  display: "grid",
  gap: "0.45rem",
  padding: "0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  background: "bg",
});

const row = css({
  display: "grid",
  gridTemplateColumns: "1fr 1.4rem minmax(0, 1fr) 1.4rem auto",
  gap: "0.4rem",
  alignItems: "center",
});

const couplerRow = css({ display: "flex", alignItems: "center", gap: "0.5rem" });
const couplerLine = css({ flex: "1", height: "1px", background: "token(colors.amber)", opacity: "0.4" });
const engineWrap = css({ display: "flex", alignItems: "center", gap: "0.4rem" });
const memoryNote = css({ color: "muted", fontSize: "0.72rem", paddingInline: "0.2rem" });

function conduitState(value: string): ConduitState {
  switch (value) {
    case "nominal":
    case "complete":
    case "running":
    case "blocked":
    case "failed":
    case "stale":
    case "skipped":
    case "planned":
      return value;
    default:
      return "unknown";
  }
}

function runtimeState(value: string | undefined): RuntimeState {
  switch (value) {
    case "nominal":
    case "configured":
    case "indexing":
    case "down":
      return value;
    default:
      return "unknown";
  }
}

function edgeState(node: EngineProcessNode, kind: string): ConduitState {
  return conduitState(node.edges.find((edge) => edge.kind === kind)?.state ?? "unknown");
}

function CommitNode({ label, refNode }: { label: string; refNode: CommitRefNode }) {
  return (
    <div className={nodeBox({ factState: refNode.factState })}>
      <span className={nodeLabel}>{label}</span>
      <span className={nodeBranch}>{refNode.branch ?? "—"}</span>
      {refNode.commit ? (
        <span className={nodeCommit}>
          @ {refNode.commit.slice(0, 8)}
          {refNode.dirty ? " · dirty" : ""}
          {refNode.behindSource ? ` · ${refNode.behindSource} behind` : ""}
        </span>
      ) : null}
    </div>
  );
}

function EngineUnit({
  engine,
  role,
}: {
  engine: ProviderBootNode | undefined;
  role: "code" | "memory";
}) {
  const label = role === "code" ? "CGC" : "GrepAI";
  const runtime = runtimeState(engine?.runtimeState);
  return (
    <div className={engineWrap}>
      <span
        className={engineSilhouette({ runtimeState: runtime })}
        role="img"
        aria-label={`${label} engine ${engine ? runtime : "not started"}`}
      />
      <span className={engineMeta}>
        <span className={engineName}>{label}</span>
        <span className={engineState}>{engine ? runtime : "not started"}</span>
      </span>
    </div>
  );
}

export function EnclosureProcessMap({ node }: { node: EngineProcessNode }) {
  const codeEngine = node.providers.find((provider) => provider.role === "code");
  const memoryEngine = node.providers.find((provider) => provider.role === "memory");
  const externalMemory = node.memoryMode === "external" && node.memorySource && node.memoryWorktree;
  return (
    <div className={mapWrap} data-testid="process-map">
      <span className={sectionLabel}>Official line → enclosure</span>
      <div className={row} data-testid="code-lane">
        <CommitNode label="Code source" refNode={node.codeSource} />
        <span className={conduit({ state: edgeState(node, "worktree-add") })} aria-hidden="true" />
        <CommitNode label="Code worktree" refNode={node.codeWorktree} />
        <span className={conduit({ state: edgeState(node, "cgc-seed") })} aria-hidden="true" />
        <EngineUnit engine={codeEngine} role="code" />
      </div>
      <div className={couplerRow}>
        <span className={couplerLine} aria-hidden="true" />
        <span className={couplerBar}>contract · {node.taskId}</span>
        <span className={couplerLine} aria-hidden="true" />
      </div>
      {externalMemory && node.memorySource && node.memoryWorktree ? (
        <div className={row} data-testid="memory-lane">
          <CommitNode label="Memory source" refNode={node.memorySource} />
          <span className={conduit({ state: edgeState(node, "ledger-map") })} aria-hidden="true" />
          <CommitNode label="Memory worktree" refNode={node.memoryWorktree} />
          <span className={conduit({ state: edgeState(node, "grepai-clone") })} aria-hidden="true" />
          <EngineUnit engine={memoryEngine} role="memory" />
        </div>
      ) : (
        <div className={memoryNote} data-testid="memory-lane-absent">
          Memory: {node.memoryMode} — no external memory lane
        </div>
      )}
    </div>
  );
}
