import { AnimatePresence, motion } from "motion/react";

import { fmtWait, selectQueue } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Dot } from "../grammar/Dot";

// The home-screen attention queue (note 06): the server-ranked list of what needs the human,
// rebuilt from mc2's renderAttn UX. "Open" jumps to the item's lifecycle in the tree/detail —
// the deliberate queue↔tree coupling. Read-only: the inline resolve affordance is slice 06.
export function AttentionQueue({ onSelect }: { onSelect: (lifecycleId: string) => void }) {
  const queue = useDashboard(selectQueue);
  return (
    <section className="panel attn" data-testid="attention-queue">
      <h2>Attention · {queue.length} waiting</h2>
      {queue.length === 0 ? (
        <p className="muted">Queue clear — nothing waiting.</p>
      ) : (
        <ul className="attn__list">
          <AnimatePresence initial={false}>
            {queue.map((item) => {
              const lifecycleId = item.lifecycleId;
              return (
                <motion.li
                  key={item.id}
                  layout
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className={`attn__item attn__item--${item.severity}`}
                  data-testid="attn-item"
                >
                  <Dot variant={item.severity} />
                  <div className="attn__body">
                    <div className="attn__title">{item.title}</div>
                    {item.detail ? <div className="attn__detail">{item.detail}</div> : null}
                    <div className="attn__meta">
                      {item.lane} · {fmtWait(item.waitSeconds)}
                    </div>
                  </div>
                  {lifecycleId ? (
                    <button type="button" className="ghost" onClick={() => onSelect(lifecycleId)}>
                      Open
                    </button>
                  ) : null}
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </section>
  );
}
