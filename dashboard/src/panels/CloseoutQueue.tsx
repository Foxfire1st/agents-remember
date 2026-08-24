import { memo } from "react";

import { css } from "../../styled-system/css";
import { sameTaskDocumentRef } from "../data/taskIdentity";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { CloseoutCandidateNode, CloseoutQueueNode, TaskDocumentRef } from "../types/projection";

// Closeout projection — an effective read of disposable exact-current scheduling state.
// The dashboard renders member classification and typed repair evidence verbatim; it never
// treats a stale or unreadable artifact as admitting lifecycle authority.
const list = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.35rem" });
const row = css({
  display: "flex",
  gap: "0.5rem",
  paddingBlock: "0.35rem",
  paddingInline: "0.5rem",
  borderLeftWidth: "3px",
  borderLeftStyle: "solid",
  borderLeftColor: "grid",
  background: "bg",
});
const problemRow = css({ borderLeftColor: "alarm" });
const name = css({ fontWeight: "600", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const meta = css({ color: "muted", fontSize: "0.75rem" });
const reasons = css({ color: "ink", opacity: "0.8", fontSize: "0.78rem" });
const groupHead = css({ margin: "0.6rem 0 0.2rem", fontSize: "0.8rem", letterSpacing: "0.04em", color: "muted" });

function CandidateRow({ candidate }: { candidate: CloseoutCandidateNode }) {
  const title = `${candidate.classification} · ${candidate.priority}`;
  return (
    <li className={row} data-testid="closeout-candidate">
      <span className={name}>{candidate.taskDocumentRef.path}</span>
      <span className={meta}>{title}</span>
      {candidate.reasons.length > 0 && (
        <span className={reasons} data-testid="closeout-reasons">
          {candidate.reasons.join("; ")}
        </span>
      )}
    </li>
  );
}

function Queue({ queue }: { queue: CloseoutQueueNode }) {
  return (
    <section data-testid="closeout-queue">
      <h3 className={groupHead}>
        {queue.sprintRef.path}
        <span className={meta}>
          rev {queue.revision} · {queue.serviceCondition}
          {queue.sourceClassification ? ` · ${queue.sourceClassification}` : ""}
        </span>
      </h3>
      {queue.sourceProblems.map((problem) => (
        <div className={`${row} ${problemRow}`} data-testid="closeout-problem" key={`${problem.kind}/${problem.address}/${problem.errorType}`}>
          <span className={name}>{problem.errorType}</span>
          <span className={reasons}>{problem.repairAction}</span>
        </div>
      ))}
      <ul className={list}>
        {queue.members.map((candidate) => (
          <CandidateRow key={candidate.generationId} candidate={candidate} />
        ))}
      </ul>
    </section>
  );
}

function CloseoutQueueImpl({ sprintRef }: { sprintRef?: TaskDocumentRef } = {}) {
  const queues = useDashboard((state) => state.closeoutQueues);
  // On the sprint page the panel is scoped to the viewed sprint; without a ref it stays the
  // workspace-wide queue (L12-R5: the panel is mounted, not dead code).
  const visible = sprintRef
    ? queues.filter((queue) => sameTaskDocumentRef(queue.sprintRef, sprintRef))
    : queues;
  if (visible.length === 0) return null;
  return (
    <Panel title="Closeout queue">
      {visible.map((queue) => (
        <Queue key={`${queue.sprintRef.repository}/${queue.sprintRef.path}`} queue={queue} />
      ))}
    </Panel>
  );
}

export const CloseoutQueue = memo(CloseoutQueueImpl);
