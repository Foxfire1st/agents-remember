import { css } from "../../../styled-system/css";
import { useActiveConversation } from "../../data/conversation/store";
import type { QueuedSubmit } from "../../data/sessionCockpitStore";
import { useConversationInterrupt } from "./conversation/useConversationControls";

// Server-authoritative cockpit queue projection. The privacy-scoped route does not
// disclose other sources, so every count still says "yours" and no row invents a server position.
// Each item carries both visual jobs from the spec: a highlighted queued-user block and the dim
// delivery row. This is still the queue layer — it does not pretend the future UA-1 transcript exists.
//
// Head-row steer: the HEAD row (and only the head — the authority queue dispatches in
// order) offers a steer action while the seat is interrupt-capable. Steer fires the landed
// exact-turn interrupt (data/conversation/client requestInterrupt, same-id reconcile via
// useConversationControls); once the turn settles the authority dispatches the queued head itself —
// the row is NEVER withdrawn or duplicated by steering. Visibility gates on the WIRE capability
// (`capabilities.controls.interrupt.state === "supported"` — codex/pi today, claude when its
// backend slice lands), never a hardcoded harness list: no evidence → honest absence.

/** The steer action's honest consequence copy — what it does, in its own words. */
const QUEUE_STEER_DETAIL = "interrupts the current turn and runs this now";
const QUEUE_STEER_PENDING = "interrupting…";

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
const steerButton = css({
  flexShrink: 0,
  font: "inherit",
  letterSpacing: "0.03em",
  paddingInline: "0.4rem",
  borderRadius: "2px",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "amber",
  color: "amber",
  background: "transparent",
  cursor: "pointer",
  _hover: { background: "color-mix(in oklch, token(colors.amber) 10%, transparent)" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  _disabled: { opacity: 0.55, cursor: "default" },
});

export function QueuePreview({
  queue,
  sessionId,
}: {
  queue: readonly QueuedSubmit[];
  sessionId?: string;
}) {
  // Steer capability is WIRE evidence (never a hardcoded list): the active-conversation
  // projection's interrupt capability must say `supported` — anything less hides the action.
  const interruptCapability = useActiveConversation((state) =>
    sessionId === undefined
      ? undefined
      : state.bySession[sessionId]?.capabilities?.controls.interrupt,
  );
  // The exact-turn interrupt lifecycle (request → same-id reconcile, announcer-voiced
  // acknowledgement vs settlement). `available` additionally requires a WORKING turn with a
  // resolvable id — steering an idle seat is meaningless because the queue drains on its own.
  const interrupt = useConversationInterrupt(sessionId);
  const steerAvailable = interruptCapability?.state === "supported" && interrupt.available;

  const live = queue.filter((entry) => entry.state === "queued");
  if (live.length === 0) return null;
  return (
    <section className={root} aria-label={`${live.length} queued messages of yours`} data-testid="queue-preview">
      <div className={head}>
        <span>{live.length} queued · yours</span>
        <span>alt+↑ edit last</span>
      </div>
      {live.map((entry, index) => (
        <div className={item} key={entry.requestId} data-testid="queue-preview-item">
          <div className={queuedBlock} data-testid="queued-user-block">
            &gt; {entry.text}
          </div>
          <div className={row}>
            <span aria-hidden="true">↳</span>
            <span className={rowPreview}>{entry.preview}</span>
            <span className={delivery}>queued · withdrawable before dispatch</span>
            {index === 0 && steerAvailable ? (
              <button
                type="button"
                className={steerButton}
                disabled={interrupt.pending}
                onClick={interrupt.onStop}
                title={QUEUE_STEER_DETAIL}
                aria-label={`steer — ${QUEUE_STEER_DETAIL}`}
                data-testid="queue-steer"
              >
                {interrupt.pending ? QUEUE_STEER_PENDING : "steer"}
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </section>
  );
}
