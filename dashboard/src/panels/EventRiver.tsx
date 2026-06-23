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
  const time = event.ts ? event.ts.slice(11, 19) : "—"; // defensive: never crash the feed
  const read = readPacketSummary(event);
  const ref = read?.repo || event.lifecycleId || event.repoId || event.enclosure || "";
  return (
    <li className={row({ trust: event.trust })} data-testid="river-item">
      <div className={kind} title={read?.title}>
        {read?.label ?? event.kind}
      </div>
      <div className={meta}>
        {event.trust} · {event.actor}
        {ref ? ` · ${ref}` : ""} · {time}
      </div>
    </li>
  );
}

// read.packet → "Read: <basename>" (+ "+N more" for a batch), the full path(s) on hover, and the
// read's repo (data.repoId — the repo the files belong to). The river is otherwise generic; this is
// the one per-kind treatment, kept defensive so a malformed packet never crashes the feed.
function readPacketSummary(
  event: ObserverEvent,
): { label: string; title: string; repo: string } | null {
  if (event.kind !== "read.packet") return null;
  const data = event.data ?? {};
  const files = Array.isArray(data.files) ? (data.files as Array<{ path?: unknown }>) : [];
  const paths = files
    .map((f) => (typeof f?.path === "string" ? f.path : ""))
    .filter((p) => p.length > 0);
  const repo = typeof data.repoId === "string" ? data.repoId : "";
  const firstName = paths.length > 0 ? (paths[0].split("/").pop() ?? paths[0]) : "(no file)";
  const label = paths.length > 1 ? `Read: ${firstName} +${paths.length - 1} more` : `Read: ${firstName}`;
  return { label, title: paths.join("\n"), repo };
}
