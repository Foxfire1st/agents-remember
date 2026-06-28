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
// observer-log policy, not a hard frontend display cap.
const sizing = css({ flex: "1 1 0" });
const list = css({ listStyle: "none", margin: "0", padding: "0", display: "grid", gap: "0.2rem" });
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

export function EventRiver() {
  const events = useDashboard((s) => s.events);
  const eventsHydrated = useDashboard((s) => s.eventsHydrated || s.events.length > 0);
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const analytics = useDashboard((s) => s.analytics);
  const summaryContext = buildEventSummaryContext(lifecycles, enclosures, analytics);
  const displayEvents = [...events]
    .reverse()
    .flatMap((event) =>
      eventSummaryContextReady(event, summaryContext)
        ? [{ event, summary: summarizeEvent(event, summaryContext) }]
        : [],
    )
    .filter(({ summary }) => summary.visibility !== "hidden");
  const titleCount = eventsHydrated ? events.length : "syncing";
  return (
    <Panel testid="event-river" title={`Event river · ${titleCount}`} className={sizing}>
      {!eventsHydrated ? (
        <p className="muted">Syncing event history.</p>
      ) : events.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : displayEvents.length === 0 ? (
        <p className="muted">No displayable events.</p>
      ) : (
        <ul className={list}>
          {displayEvents.map(({ event, summary }) => (
            <EventRow key={event.id} event={event} summary={summary} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function EventRow({ event, summary }: { event: ObserverEvent; summary: EventSummary }) {
  const time = formatEventTime(event.ts);
  const metaParts = [
    actorLabel(event.actor),
    trustLabel(event.trust),
    summary.context,
    ...summary.meta,
    time,
  ].filter(Boolean);
  return (
    <li className={row({ trust: event.trust })} data-testid="river-item">
      <div className={kind} title={summary.title}>
        {summary.label}
      </div>
      <div className={meta}>{metaParts.join(" · ")}</div>
    </li>
  );
}
