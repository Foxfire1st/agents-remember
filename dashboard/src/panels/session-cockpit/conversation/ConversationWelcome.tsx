import { css } from "../../../../styled-system/css";
import type { ConversationProcessState } from "../../../data/conversation/types";

const welcome = css({
  width: "min(100%, 52rem)",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "1rem",
  paddingInline: "clamp(0.75rem, 3vw, 2.5rem)",
  color: "ink",
  textAlign: "center",
});
const signal = css({
  width: "min(100%, 34rem)",
  display: "flex",
  alignItems: "center",
  gap: "0.8rem",
  color: "amber",
});
const signalLine = css({
  height: "1px",
  flex: "1",
  background:
    "linear-gradient(90deg, transparent, color-mix(in oklch, token(colors.amber) 72%, transparent))",
});
const signalLineReverse = css({
  height: "1px",
  flex: "1",
  background:
    "linear-gradient(90deg, color-mix(in oklch, token(colors.amber) 72%, transparent), transparent)",
});
const signalNode = css({
  fontSize: "0.9rem",
  lineHeight: "1",
  textShadow: "0 0 calc(8px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.6)",
});
const mission = css({
  color: "muted",
  fontSize: "0.66rem",
  letterSpacing: "0.2em",
  textTransform: "uppercase",
});
const wordmark = css({
  color: "amber",
  fontSize: "clamp(1.45rem, 3vw, 2.65rem)",
  lineHeight: "1",
  letterSpacing: "0.18em",
  textTransform: "uppercase",
  textShadow: "0 0 calc(10px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.32)",
});
const promise = css({
  margin: "0",
  color: "ink",
  fontSize: "0.86rem",
  letterSpacing: "0.04em",
});
const guide = css({
  width: "100%",
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(10rem, 1fr))",
  gap: "1rem",
  padding: "0",
  margin: "0.65rem 0 0",
  listStyle: "none",
  textAlign: "left",
});
const guideItem = css({
  minWidth: "0",
  display: "grid",
  gridTemplateColumns: "2.2rem minmax(0, 1fr)",
  gap: "0.45rem",
  paddingBlockStart: "0.65rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
});
const guideIndex = css({
  color: "amber",
  fontSize: "0.64rem",
  letterSpacing: "0.08em",
});
const guideCopy = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.18rem",
  minWidth: "0",
});
const guideVerb = css({
  color: "ink",
  fontSize: "0.7rem",
  letterSpacing: "0.12em",
  textTransform: "uppercase",
});
const guideDetail = css({
  color: "muted",
  fontSize: "0.7rem",
  lineHeight: "1.45",
});
const readyLine = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "0.45rem",
  marginBlockStart: "0.4rem",
  color: "muted",
  fontSize: "0.66rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
});
const dotShape = { width: "0.45rem", height: "0.45rem", borderRadius: "full" } as const;
// Mint + glow is the "fresh-online / live" token (panda.config.ts) and the seat grammar's `ready`
// color; only a genuinely connected process may wear it. The other dots are flat — no glow — so a
// dead or unproven link can never borrow the live silhouette even at a glance.
const dotLive = css(dotShape, {
  background: "mint",
  boxShadow: "0 0 calc(7px * var(--glow-strength)) oklch(0.88 0.16 165 / 0.5)",
});
const dotStarting = css(dotShape, { background: "cyan" });
const dotFault = css(dotShape, { background: "alarm" });
const dotDormant = css(dotShape, { background: "dormant" });
const dotUnknown = css(dotShape, { background: "muted" });
const firstMove = css({
  margin: "0",
  color: "muted",
  fontSize: "0.72rem",
});
const down = css({ color: "amber", paddingInlineEnd: "0.35rem" });

const PILLARS = [
  ["01", "Agents", "Orchestrate attention"],
  ["02", "Tasks", "Commit intent"],
  ["03", "Memory", "Preserve truth"],
] as const;

/**
 * The link line is the only claim this welcome makes about the world, so it may say nothing the
 * projection has not proven. An open SSE stream proves the browser can hear the daemon; it proves
 * NOTHING about the harness bridge — so the word and the dot are derived from the canonical process
 * state (§6.5) and from nothing else. Words mirror the wire vocabulary rather than remapping it
 * (same doctrine as stateGrammar's `exited`/`stale`). This line previously
 * printed "<harness> link ready" beside a mint live dot for starting/disconnected/exited/failed
 * alike, because it took no readiness input at all.
 */
const LINK_STATES: Record<ConversationProcessState, { word: string; dot: string }> = {
  connected: { word: "link ready", dot: dotLive },
  starting: { word: "link starting", dot: dotStarting },
  disconnected: { word: "link disconnected", dot: dotFault },
  exited: { word: "link exited", dot: dotDormant },
  failed: { word: "link failed", dot: dotFault },
};
/** No status frame applied yet ⇒ mirror the uncertainty; "unknown" is a state, "ready" is a lie. */
const UNKNOWN_LINK = { word: "link state unknown", dot: dotUnknown } as const;

export function ConversationWelcome({
  harness,
  processState,
}: {
  harness: string;
  /** Canonical `status.process.state`; omitted/undefined ⇒ the projection has no status yet. */
  processState?: ConversationProcessState;
}) {
  const link = processState === undefined ? UNKNOWN_LINK : LINK_STATES[processState];
  return (
    <section
      className={welcome}
      aria-label="Agents Remember welcome"
      data-testid="conversation-welcome"
    >
      <div className={signal} aria-hidden="true">
        <span className={signalLine} />
        <span className={signalNode}>◇</span>
        <span className={signalLineReverse} />
      </div>
      <div className={mission}>Mission control</div>
      <div className={wordmark}>Agents Remember</div>
      <p className={promise}>One mission. Many agents. Coherence survives.</p>
      <ol className={guide} aria-label="Product pillars">
        {PILLARS.map(([index, verb, detail]) => (
          <li className={guideItem} key={index}>
            <span className={guideIndex}>{index}</span>
            <span className={guideCopy}>
              <span className={guideVerb}>{verb}</span>
              <span className={guideDetail}>{detail}</span>
            </span>
          </li>
        ))}
      </ol>
      <div
        className={readyLine}
        data-testid="conversation-welcome-harness"
        data-link-state={processState ?? "unknown"}
      >
        <span className={link.dot} aria-hidden="true" data-testid="conversation-welcome-link-dot" />
        <span>
          {harness} {link.word}
        </span>
      </div>
      <p className={firstMove}>
        <span className={down} aria-hidden="true">
          ↓
        </span>
        Write the first transmission below.
      </p>
    </section>
  );
}
