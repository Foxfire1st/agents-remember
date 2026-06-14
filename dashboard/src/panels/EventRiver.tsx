import { useDashboard } from "../data/store";
import type { ObserverEvent } from "../types/event";

// The Event River (right rail): the raw observer feed with trust provenance. Newest first; the
// trust class is the colour, so the feed never pretends `declared` is `observed`. Fed by the raw
// /api/events channel into the store's bounded ring buffer (a separate connection from /api/stream).
export function EventRiver() {
  const events = useDashboard((s) => s.events);
  const recent = events.slice(-60).reverse();
  return (
    <section className="panel river" data-testid="event-river">
      <h2>Event river · {events.length}</h2>
      {recent.length === 0 ? (
        <p className="muted">No events yet.</p>
      ) : (
        <ul className="river__list">
          {recent.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      )}
    </section>
  );
}

function EventRow({ event }: { event: ObserverEvent }) {
  const ref = event.lifecycleId ?? event.repoId ?? event.enclosure ?? "";
  const time = event.ts ? event.ts.slice(11, 19) : "—"; // defensive: never crash the feed
  return (
    <li className={`river__item river__item--${event.trust}`} data-testid="river-item">
      <div className="river__kind">{event.kind}</div>
      <div className="river__meta">
        {event.trust} · {event.actor}
        {ref ? ` · ${ref}` : ""} · {time}
      </div>
    </li>
  );
}
