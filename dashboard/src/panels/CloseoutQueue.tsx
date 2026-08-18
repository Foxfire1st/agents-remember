import { memo } from "react";

import { css } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { CloseoutCandidateNode, CloseoutQueueNode } from "../types/projection";

// Closeout queue — the projected, read-only scheduling surface (L8-R4/R5/R6). One ordered
// list per sprint: the active atomic blocker first, then every declared candidate with its
// queue state, grade, and the exact reasons it is not selectable. The dashboard never infers
// readiness from titles, numbering, or labels; it renders the queue's recorded facts verbatim.
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
const blockerRow = css({ borderLeftColor: "alarm" });
const name = css({ fontWeight: "600", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" });
const meta = css({ color: "muted", fontSize: "0.75rem" });
const reasons = css({ color: "ink", opacity: "0.8", fontSize: "0.78rem" });
const groupHead = css({ margin: "0.6rem 0 0.2rem", fontSize: "0.8rem", letterSpacing: "0.04em", color: "muted" });

function CandidateRow({ candidate }: { candidate: CloseoutCandidateNode }) {
  const title = `${candidate.candidateState}${candidate.gradePriority ? ` · ${candidate.gradePriority}` : ""}`;
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
      <h3 className={groupHead}>{queue.sprintRef.path}</h3>
      {queue.activeBlocker && (
        <div className={`${row} ${blockerRow}`} data-testid="closeout-blocker">
          <span className={name}>blocker: {queue.activeBlocker.master.path}</span>
          <span className={meta}>{queue.activeBlocker.rationale}</span>
        </div>
      )}
      <ul className={list}>
        {queue.candidates.map((candidate) => (
          <CandidateRow key={`${candidate.taskDocumentRef.repository}/${candidate.taskDocumentRef.path}`} candidate={candidate} />
        ))}
      </ul>
    </section>
  );
}

function CloseoutQueueImpl() {
  const queues = useDashboard((state) => state.closeoutQueues);
  if (queues.length === 0) return null;
  return (
    <Panel title="Closeout queue">
      {queues.map((queue) => (
        <Queue key={`${queue.sprintRef.repository}/${queue.sprintRef.path}`} queue={queue} />
      ))}
    </Panel>
  );
}

export const CloseoutQueue = memo(CloseoutQueueImpl);
