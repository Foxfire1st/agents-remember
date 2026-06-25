import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { css, cva } from "../../styled-system/css";
import { postGateDecision } from "../data/actions";
import { fmtWait, selectQueue } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";
import type { AttentionItem, TaskDocNode } from "../types/projection";

// The home-screen attention queue (note 06): the server-ranked list of what needs the human,
// rebuilt from mc2's renderAttn UX. "Open" jumps to the item's lifecycle in the detail view — the
// deliberate queue↔detail coupling. Read-only: the inline resolve affordance is slice 06.
const sizing = css({ flex: "0 1 auto", maxHeight: "42%" });
const list = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.35rem" });
const item = cva({
  base: {
    display: "flex",
    alignItems: "flex-start",
    gap: "0.5rem",
    paddingInline: "0.5rem",
    paddingBlock: "0.4rem",
    background: "bg",
    borderLeftWidth: "3px",
    borderLeftStyle: "solid",
    borderLeftColor: "grid",
  },
  variants: {
    severity: {
      alarm: { borderLeftColor: "alarm" },
      warn: { borderLeftColor: "amber" },
      info: { borderLeftColor: "cyan" },
    },
  },
});
const bodyCol = css({ flex: "1", minWidth: "0" });
const head = css({ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" });
const heading = css({ margin: "0" });
const itemTitle = css({ fontWeight: "600" });
const detail = css({ color: "ink", opacity: "0.85", fontSize: "0.82rem" });
const meta = css({ color: "muted", fontSize: "0.75rem", letterSpacing: "0.04em" });
const ghost = css({
  font: "inherit",
  color: "inherit",
  background: "transparent",
  borderStyle: "none",
  cursor: "pointer",
  textAlign: "left",
});
const clearButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "amber",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  borderRadius: "2px",
  paddingInline: "0.45rem",
  paddingBlock: "0.16rem",
  cursor: "pointer",
  _disabled: { opacity: 0.5, cursor: "default" },
});

const EMPTY_TASK_DOCS: readonly TaskDocNode[] = [];

function taskForAttention(item: AttentionItem, docs: readonly TaskDocNode[]): TaskDocNode | undefined {
  if (!item.lifecycleId) return undefined;
  const matches = docs.filter((doc) => doc.lifecycleId === item.lifecycleId);
  return matches.find((doc) => doc.kind !== "master") ?? matches[0];
}

function titleForAttention(item: AttentionItem, doc: TaskDocNode | undefined): string {
  if (!doc) return item.title;
  return doc.id ? `Task ${doc.id}: ${doc.title}` : doc.title;
}

function detailForAttention(item: AttentionItem, doc: TaskDocNode | undefined): string | undefined {
  if (!doc) return item.detail;
  return [item.title, item.detail].filter(Boolean).join(" · ");
}

export function AttentionQueue({ onSelect }: { onSelect: (lifecycleId: string) => void }) {
  const queue = useDashboard(selectQueue);
  const docs = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_TASK_DOCS);
  const [clearing, setClearing] = useState(false);
  const gateItems = queue.filter(
    (item): item is AttentionItem & { gateId: string } =>
      item.kind === "gate-open" && Boolean(item.gateId),
  );
  const clearGates = () => {
    if (clearing || gateItems.length === 0) return;
    setClearing(true);
    void Promise.all(
      gateItems.map((item) =>
        postGateDecision(item.lifecycleId ?? null, "cancel", {
          gateId: item.gateId,
          note: "Cleared from attention queue.",
        }),
      ),
    ).finally(() => setClearing(false));
  };
  const panelHead = (
    <div className={head}>
      <h2 className={heading}>Attention · {queue.length} waiting</h2>
      {gateItems.length > 0 ? (
        <button
          type="button"
          className={clearButton}
          onClick={clearGates}
          disabled={clearing}
          data-testid="attn-clear"
        >
          {clearing ? "Clearing" : "Clear"}
        </button>
      ) : null}
    </div>
  );
  return (
    <Panel
      testid="attention-queue"
      head={panelHead}
      className={sizing}
    >
      {queue.length === 0 ? (
        <p className="muted">Queue clear — nothing waiting.</p>
      ) : (
        <ul className={list}>
          <AnimatePresence initial={false}>
            {queue.map((q) => {
              const lifecycleId = q.lifecycleId;
              const doc = taskForAttention(q, docs);
              const displayTitle = titleForAttention(q, doc);
              const displayDetail = detailForAttention(q, doc);
              return (
                <motion.li
                  key={q.id}
                  layout
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className={item({ severity: q.severity })}
                  data-testid="attn-item"
                >
                  <Dot variant={q.severity} />
                  <div className={bodyCol}>
                    <div className={itemTitle}>{displayTitle}</div>
                    {displayDetail ? <div className={detail}>{displayDetail}</div> : null}
                    <div className={meta}>
                      {q.lane} · {fmtWait(q.waitSeconds)}
                    </div>
                  </div>
                  {lifecycleId ? (
                    <button type="button" className={ghost} onClick={() => onSelect(lifecycleId)}>
                      Open
                    </button>
                  ) : null}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Panel>
  );
}
