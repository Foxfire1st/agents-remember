import { css } from "../../../styled-system/css";
import type { QueuedSubmit } from "../../data/sessionCockpitStore";

// Server-authoritative cockpit queue projection (FEUI-L5 R3/R5). The privacy-scoped route does not
// disclose other sources, so every count still says "yours" and no row invents a server position.
// Each item carries both visual jobs from the spec: a highlighted queued-user block and the dim
// delivery row. This is still the queue layer — it does not pretend the future UA-1 transcript exists.

const root = css({
  display: "grid",
  gap: "0.3rem",
  flexShrink: 0,
  paddingBlock: "0.25rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
});
const head = css({
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: "0.5rem",
  color: "amber",
  fontSize: "0.66rem",
  letterSpacing: "0.04em",
});
const item = css({ display: "grid", gap: "0.1rem" });
const queuedBlock = css({
  minWidth: "0",
  paddingInline: "0.45rem",
  paddingBlock: "0.22rem",
  background: "color-mix(in oklch, token(colors.amber) 8%, transparent)",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "amber",
  color: "ink",
  fontSize: "0.74rem",
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  maxHeight: "2.7em",
  overflow: "hidden",
});
const row = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.45rem",
  minWidth: "0",
  paddingInline: "0.45rem",
  color: "muted",
  fontSize: "0.66rem",
});
const rowPreview = css({
  flex: "1",
  minWidth: "0",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
});
const delivery = css({ flexShrink: 0, color: "amber" });

export function QueuePreview({ queue }: { queue: readonly QueuedSubmit[] }) {
  const live = queue.filter((entry) => entry.state === "queued");
  if (live.length === 0) return null;
  return (
    <section className={root} aria-label={`${live.length} queued messages of yours`} data-testid="queue-preview">
      <div className={head}>
        <span>{live.length} queued · yours</span>
        <span>alt+↑ edit last</span>
      </div>
      {live.map((entry) => (
        <div className={item} key={entry.requestId} data-testid="queue-preview-item">
          <div className={queuedBlock} data-testid="queued-user-block">
            &gt; {entry.text}
          </div>
          <div className={row}>
            <span aria-hidden="true">↳</span>
            <span className={rowPreview}>{entry.preview}</span>
            <span className={delivery}>queued · withdrawable before dispatch</span>
          </div>
        </div>
      ))}
    </section>
  );
}
