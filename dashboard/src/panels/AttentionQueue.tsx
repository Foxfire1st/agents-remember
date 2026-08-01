import { AnimatePresence, motion } from "motion/react";
import { memo, useState } from "react";

import { css, cva } from "../../styled-system/css";
import { postAttentionDismiss } from "../data/actions";
import { fmtWait, selectQueue } from "../data/selectors";
import { servedAgeSeconds, useNowMs } from "../data/servedAges";
import { dashboardStore, useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";
import type { AttentionItem, TaskDocNode } from "../types/projection";

// The home-screen attention queue: the server-ranked list of what needs the human,
// rebuilt from mc2's renderAttn UX. "Open" jumps to the item's lifecycle in the detail view — the
// deliberate queue↔detail coupling. Lifecycle-bound rows are dismissable: a
// per-item "Dismiss" and "Clear all" both POST a current acknowledgement. Lifecycle rows are scoped
// by lifecycle id, gate rows are consumed by cancelling/deleting the gate, and actionable-drift rows
// are repo-level one-shot signals anchored by the drift snapshot timestamp.
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
// The Dot is decorative (`aria-hidden`), so the severity has to be spoken by its wrapper. The
// wrapper needs a ROLE that can carry a name: `aria-label` on a bare span names a `generic`, which
// ARIA prohibits and which no screen reader announces (axe-core reports `aria-prohibited-attr` at
// `serious`), so the label reached nobody. `img` is the role for a mark that means something — it
// puts "Severity: warn" in the accessibility tree with the glyph hidden underneath it.
// (LifecycleList's equivalent span escapes the same bug only because it sits inside React Aria's
// `role="option"`, whose name-from-content absorbs the label.)
// `flexShrink` because the wrapper, not the Dot, is the flex item.
const severityMark = css({ flexShrink: "0", lineHeight: "1.35" });
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
const dismissButton = css({
  font: "inherit",
  fontSize: "0.7rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.12rem",
  cursor: "pointer",
  _hover: { color: "ink", borderColor: "muted" },
  _disabled: { opacity: 0.5, cursor: "default" },
});
const actionsCol = css({ display: "flex", alignItems: "center", gap: "0.35rem" });

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

function dismissPayload(item: AttentionItem) {
  return {
    itemId: item.id,
    kind: item.kind,
    lifecycleId: item.lifecycleId ?? null,
    gateId: item.gateId,
  };
}

function canDismiss(item: AttentionItem): boolean {
  return Boolean(
    item.lifecycleId ||
      (item.kind === "gate-open" && item.gateId) ||
      item.kind === "actionable-drift",
  );
}

function AttentionQueueImpl({
  onSelect,
  active = true,
}: {
  onSelect: (lifecycleId: string) => void;
  active?: boolean;
}) {
  const queue = useDashboard(selectQueue);
  const docs = useDashboard((state) => state.analytics?.taskDocuments ?? EMPTY_TASK_DOCS);
  // Wait times advance locally between emissions — the change gate no longer
  // re-serves the queue every tick just because the waits aged. A hidden kept-alive rail freezes
  // this clock; it has no visible age labels to refresh.
  const nowMs = useNowMs(10_000, active);
  const [clearing, setClearing] = useState(false);
  const [dismissing, setDismissing] = useState<ReadonlySet<string>>(() => new Set());
  const dismissableQueue = queue.filter(canDismiss);
  const dismissItem = (item: AttentionItem) => {
    if (!canDismiss(item) || dismissing.has(item.id)) return;
    dashboardStore.getState().suppressAttention([item.id]);
    setDismissing((prev) => new Set(prev).add(item.id));
    void postAttentionDismiss(dismissPayload(item))
      .then((status) => {
        if (status !== "dismissed") dashboardStore.getState().releaseAttention([item.id]);
      })
      .finally(() =>
        setDismissing((prev) => {
          const next = new Set(prev);
          next.delete(item.id);
          return next;
        }),
      );
  };
  const clearAll = () => {
    if (clearing || dismissableQueue.length === 0) return;
    const ids = dismissableQueue.map((item) => item.id);
    dashboardStore.getState().suppressAttention(ids);
    setClearing(true);
    void Promise.all(
      dismissableQueue.map(async (item) => ({
        id: item.id,
        status: await postAttentionDismiss(dismissPayload(item)),
      })),
    )
      .then((results) => {
        const failed = results.filter((result) => result.status !== "dismissed").map((result) => result.id);
        if (failed.length > 0) dashboardStore.getState().releaseAttention(failed);
      })
      .finally(() => setClearing(false));
  };
  const panelHead = (
    <div className={head}>
      <h2 className={heading}>Attention · {queue.length} waiting</h2>
      {dismissableQueue.length > 0 ? (
        <button
          type="button"
          className={clearButton}
          onClick={clearAll}
          disabled={clearing}
          data-testid="attn-clear"
        >
          {clearing ? "Clearing" : "Clear all"}
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
              const dismissable = canDismiss(q);
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
                  <span
                    className={severityMark}
                    role="img"
                    aria-label={`Severity: ${q.severity}`}
                    title={`Severity: ${q.severity}`}
                    data-testid="attn-severity"
                  >
                    <Dot variant={q.severity} />
                  </span>
                  <div className={bodyCol}>
                    <div className={itemTitle}>{displayTitle}</div>
                    {displayDetail ? <div className={detail}>{displayDetail}</div> : null}
                    <div className={meta}>
                      {q.lane} · {fmtWait(servedAgeSeconds(q, q.waitSeconds, nowMs))}
                    </div>
                  </div>
                  <div className={actionsCol}>
                    {lifecycleId ? (
                      <button type="button" className={ghost} onClick={() => onSelect(lifecycleId)}>
                        Open
                      </button>
                    ) : null}
                    {dismissable ? (
                      <button
                        type="button"
                        className={dismissButton}
                        onClick={() => dismissItem(q)}
                        disabled={dismissing.has(q.id)}
                        data-testid="attn-dismiss"
                        aria-label={`Dismiss ${displayTitle}`}
                      >
                        Dismiss
                      </button>
                    ) : null}
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Panel>
  );
}

// Memoized (tab-switch CPU): a persistent rail panel — the shell re-renders on every view
// switch with unchanged props, and the memo gate skips this subtree then; the queue's own store
// subscriptions still drive its updates.
export const AttentionQueue = memo(AttentionQueueImpl);
