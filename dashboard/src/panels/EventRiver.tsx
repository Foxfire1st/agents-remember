import { css, cva } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { ObserverEvent } from "../types/event";

// The Event River (right rail): the raw observer feed with trust provenance. Newest first; the
// trust class is the colour, so the feed never pretends `declared` is `observed`. Fed by the raw
// /api/events channel into the store's bounded ring buffer (separate from /api/stream).
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
const meta = css({ fontSize: "0.68rem", color: "muted", letterSpacing: "0.02em" });

export function EventRiver() {
  const events = useDashboard((s) => s.events);
  const recent = events.slice(-60).reverse();
  return (
    <Panel testid="event-river" title={`Event river · ${events.length}`} className={sizing}>
      {recent.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : (
        <ul className={list}>
          {recent.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function EventRow({ event }: { event: ObserverEvent }) {
  const ref = event.lifecycleId ?? event.repoId ?? event.enclosure ?? "";
  const time = event.ts ? event.ts.slice(11, 19) : "—"; // defensive: never crash the feed
  return (
    <li className={row({ trust: event.trust })} data-testid="river-item">
      <div className={kind}>{event.kind}</div>
      <div className={meta}>
        {event.trust} · {event.actor}
        {ref ? ` · ${ref}` : ""} · {time}
      </div>
    </li>
  );
}
