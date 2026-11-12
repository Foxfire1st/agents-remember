import { css } from "../../../styled-system/css";
import { Affordance } from "../../grammar/Affordance";
import type { CommitRefNode, EngineProcessNode } from "../../types/projection";
import {
  actionRow,
  diagKey,
  diagPanel,
  diagRow,
  diagValue,
  factChip,
  missingNotice,
  missingTitle,
  sectionLabel,
} from "./styles";

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

export function DiagnosticsPanel({ node }: { node: EngineProcessNode }) {
  return (
    <div className={diagPanel} data-testid="diagnostics">
      <span className={sectionLabel}>Diagnostics</span>
      <OptionalDiagRows node={node} />
      <div className={diagRow}>
        <span className={diagKey}>Phase</span>
        <span className={diagValue}>{node.phase}</span>
      </div>
      <div className={diagRow}>
        <span className={diagKey}>Health</span>
        <span className={diagValue}>{node.health}</span>
      </div>
      <CommitRow label="Code source" refNode={node.codeSource} />
      <CommitRow label="Code worktree" refNode={node.codeWorktree} />
      {node.sourceLineage ? (
        <div className={diagRow} title={node.sourceLineage.summary}>
          <span className={diagKey}>Source lineage</span>
          <span className={diagValue}>{node.sourceLineage.state}</span>
        </div>
      ) : null}
      <DiagnosticActionRow node={node} />
      {node.missingFacts.length > 0 ? <MissingFacts facts={node.missingFacts} /> : null}
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

function MissingFacts({ facts }: { facts: string[] }) {
  return (
    <div className={missingNotice} data-testid="missing-facts">
      <span className={missingTitle}>Missing observability</span>
      {facts.map((fact) => (
        <span key={fact}>· {fact}</span>
      ))}
    </div>
  );
}

function OptionalDiagRows({ node }: { node: EngineProcessNode }) {
  return (
    <>
      {node.summary ? (
        <p className={css({ color: "ink", fontSize: "0.74rem", margin: "0" })}>{node.summary}</p>
      ) : null}
      {node.nextAction ? (
        <div className={diagRow}>
          <span className={diagKey}>Next</span>
          <span className={diagValue}>{node.nextAction}</span>
        </div>
      ) : null}
      {node.memorySource ? <CommitRow label="Memory source" refNode={node.memorySource} /> : null}
      {node.memoryWorktree ? <CommitRow label="Memory worktree" refNode={node.memoryWorktree} /> : null}
    </>
  );
}

function DiagnosticActionRow({ node }: { node: EngineProcessNode }) {
  if (node.actions.length > 0) {
    return (
      <div className={actionRow}>
        {node.actions.map((action) => (
          <Affordance key={action.action} action={action} />
        ))}
      </div>
    );
  }
  return null;
}
