import { AnimatePresence, motion } from "motion/react";

import { css, cva } from "../../styled-system/css";
import { fmtWait, selectQueue } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";
import { Panel } from "../grammar/Panel";

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

export function AttentionQueue({ onSelect }: { onSelect: (lifecycleId: string) => void }) {
  const queue = useDashboard(selectQueue);
  return (
    <Panel
      testid="attention-queue"
      title={`Attention · ${queue.length} waiting`}
      className={sizing}
    >
      {queue.length === 0 ? (
        <p className="muted">Queue clear — nothing waiting.</p>
      ) : (
        <ul className={list}>
          <AnimatePresence initial={false}>
            {queue.map((q) => {
              const lifecycleId = q.lifecycleId;
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
                    <div className={itemTitle}>{q.title}</div>
                    {q.detail ? <div className={detail}>{q.detail}</div> : null}
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
