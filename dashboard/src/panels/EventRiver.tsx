import { memo, useMemo, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { css, cva } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { ObserverEvent } from "../types/event";
import {
  actorLabel,
  buildEventSummaryContext,
  eventSummaryContextReady,
  formatEventTime,
  summarizeEvent,
  trustLabel,
  type EventSummary,
} from "./eventSummary";

// The Event River (right rail): the raw observer feed with trust provenance. Newest first; the
// trust class is the colour, so the feed never pretends `declared` is `observed`. Fed by the raw
// /api/events channel into the store (separate from /api/stream); retention belongs to the backend
// observer-log policy, not a hard frontend display cap. The list is virtualized (TanStack) over the
// store's bounded sliding window, so only the visible window of rows is mounted and render cost
// stays flat no matter how long the feed grows.
const sizing = css({ flex: "1 1 0" });
// The Panel hosts the scroll viewport (fill), so the virtualized list scrolls beneath the sticky
// header band instead of growing the panel.
const scroller = css({ flex: "1 1 0", minHeight: "0", overflowY: "auto" });
const list = css({ listStyle: "none", margin: "0", padding: "0", position: "relative", width: "100%" });
const row = cva({
  base: {
    display: "grid",
    gap: "0.05rem",
    paddingInline: "0.4rem",
    paddingBlock: "0.25rem",
    background: "bg",
    borderLeftWidth: "2px",
    borderLeftStyle: "solid",
    borderLeftColor: "grid",
  },
  variants: {
    trust: {
      observed: { borderLeftColor: "mint" },
      declared: { borderLeftColor: "amber" },
      inferred: { borderLeftColor: "cyan" },
      approved: { borderLeftColor: "mint" },
    },
  },
});
const kind = css({ fontSize: "0.76rem", fontWeight: "600" });
const meta = css({ fontSize: "0.68rem", color: "muted", letterSpacing: "0" });

const ROW_ESTIMATE = 40;

function EventRiverImpl() {
  const events = useDashboard((s) => s.events);
  const eventsHydrated = useDashboard((s) => s.eventsHydrated || s.events.length > 0);
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const analytics = useDashboard((s) => s.analytics);

  // Shape the displayable rows once per input change (not every render): reverse to newest-first,
  // drop rows whose summary context has not arrived yet, then drop hidden rows (e.g. heartbeats).
  const displayEvents = useMemo(() => {
    const summaryContext = buildEventSummaryContext(lifecycles, enclosures, analytics);
    return [...events]
      .reverse()
      .flatMap((event) =>
        eventSummaryContextReady(event, summaryContext)
          ? [{ event, summary: summarizeEvent(event, summaryContext) }]
          : [],
      )
      .filter(({ summary }) => summary.visibility !== "hidden");
  }, [events, lifecycles, enclosures, analytics]);

  // A state-backed ref (not useRef) so attaching the scroll element triggers a re-render and the
  // virtualizer measures it — without this the first measure sees a null element and mounts nothing.
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: displayEvents.length,
    getScrollElement: () => scrollEl,
    estimateSize: () => ROW_ESTIMATE,
    overscan: 12,
    getItemKey: (index) => displayEvents[index].event.id,
  });

  const titleCount = eventsHydrated ? events.length : "syncing";
  return (
    <Panel testid="event-river" title={`Event river · ${titleCount}`} className={sizing} fill>
      {!eventsHydrated ? (
        <p className="muted">Syncing event history.</p>
      ) : events.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : displayEvents.length === 0 ? (
        <p className="muted">No displayable events.</p>
      ) : (
        <div ref={setScrollEl} className={scroller} data-testid="event-river-scroll">
          <ul className={list} style={{ height: `${virtualizer.getTotalSize()}px` }}>
            {virtualizer.getVirtualItems().map((item) => {
              const { event, summary } = displayEvents[item.index];
              return (
                <EventRow
                  key={item.key}
                  event={event}
                  summary={summary}
                  index={item.index}
                  start={item.start}
                  measure={virtualizer.measureElement}
                />
              );
            })}
          </ul>
        </div>
      )}
    </Panel>
  );
}

// Memoized (tab-switch CPU): a persistent rail panel — the shell re-renders on every view
// switch with unchanged (here: no) props, and the memo gate skips this subtree then; the river's
// own store subscriptions still drive its updates.
export const EventRiver = memo(EventRiverImpl);

function EventRow({
  event,
  summary,
  index,
  start,
  measure,
}: {
  event: ObserverEvent;
  summary: EventSummary;
  index: number;
  start: number;
  measure: (node: Element | null) => void;
}) {
  const time = formatEventTime(event.ts);
  const metaParts = [
    actorLabel(event.actor),
    trustLabel(event.trust),
    summary.context,
    ...summary.meta,
    time,
  ].filter(Boolean);
  return (
    <li
      ref={measure}
      data-index={index}
      className={row({ trust: event.trust })}
      data-testid="river-item"
      style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${start}px)` }}
    >
      <div className={kind} title={summary.title}>
        {summary.label}
      </div>
      <div className={meta}>{metaParts.join(" · ")}</div>
    </li>
  );
}
