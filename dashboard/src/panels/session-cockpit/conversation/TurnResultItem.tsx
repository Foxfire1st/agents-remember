// Turn-result / error / interrupt / notice / unknown-vendor items (design §12.2). Rendered in place
// at the end of the relevant turn, never as toast spam. An unknown-vendor item is preserved as
// labeled evidence — never guessed into a message/tool meaning (§6.2). Color is never the only
// carrier: each state carries a text label (§14.2).

import { css } from "../../../../styled-system/css";
import type { ConversationItem } from "../../../data/conversation/types";
import { MarkdownBlock } from "./MarkdownBlock";

const wrap = css({ display: "grid", gap: "0.15rem", minWidth: "0", fontSize: "0.72rem" });
const line = css({ display: "flex", gap: "0.4rem", alignItems: "baseline", minWidth: "0" });
// FB7.4/A8 — a turn boundary is a dim lowercase flow line (`· turn complete`), not a boxed uppercase
// web chip. Color still carries meaning but the word is always present (tone class sets the color).
const tagBase = css({
  flex: "none",
  fontSize: "0.68rem",
});
const tone = {
  neutral: css({ color: "muted", borderColor: "grid" }),
  error: css({ color: "alarm", borderColor: "alarm" }),
  interrupted: css({ color: "amber", borderColor: "amber" }),
  done: css({ color: "mint", borderColor: "mint" }),
};
const detail = css({ color: "muted", overflowWrap: "anywhere", minWidth: "0" });
const evidence = css({ fontSize: "0.62rem", color: "dormant", fontFamily: "mono", overflowWrap: "anywhere" });

function labelFor(item: ConversationItem): { text: string; toneKey: keyof typeof tone } {
  switch (item.kind) {
    case "error":
      return { text: "error", toneKey: "error" };
    case "turn-result":
      if (item.phase === "interrupted") return { text: "interrupted", toneKey: "interrupted" };
      if (item.phase === "failed") return { text: "turn failed", toneKey: "error" };
      return { text: "turn complete", toneKey: "done" };
    case "notice":
      return { text: "notice", toneKey: "neutral" };
    case "telemetry":
      return { text: "telemetry", toneKey: "neutral" };
    case "unknown-vendor":
      return { text: "unknown vendor event", toneKey: "neutral" };
    default:
      return { text: item.kind, toneKey: "neutral" };
  }
}

export function TurnResultItem({ item }: { item: ConversationItem }) {
  const { text, toneKey } = labelFor(item);
  return (
    <div className={wrap} data-testid="conversation-turn-result" data-kind={item.kind}>
      <div className={line}>
        <span className={`${tagBase} ${tone[toneKey]}`}>· {text}</span>
        {item.blocks.map((block) => {
          if (block.type === "unknown-vendor") {
            return (
              <span className={detail} key={block.blockId}>
                {block.vendorType}: {block.safeSummary}
              </span>
            );
          }
          return null;
        })}
      </div>
      {item.blocks.map((block) => {
        if (block.type === "markdown" || block.type === "text") {
          return (
            <div className={detail} key={block.blockId}>
              <MarkdownBlock markdown={block.type === "markdown" ? block.markdown : block.text} />
            </div>
          );
        }
        if (block.type === "unknown-vendor") {
          return (
            <span className={evidence} key={`${block.blockId}-ref`} data-testid="unknown-vendor-evidence">
              evidence {block.evidenceRef}
            </span>
          );
        }
        return null;
      })}
    </div>
  );
}
