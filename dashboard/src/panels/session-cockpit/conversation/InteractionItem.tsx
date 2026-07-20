// Interaction item (design §12.2, §12.4). The historical prompt and its resolved answer stay in the
// timeline; the LIVE pending prompt is owned and announced by the existing InteractionBar, so this
// timeline copy is non-live (aria-live="off" at the row) and never becomes an answer surface. Native
// or PTY text can never count as an interaction answer.

import { css } from "../../../../styled-system/css";
import type { ConversationContentBlock, ConversationItem } from "../../../data/conversation/types";
import { MarkdownBlock } from "./MarkdownBlock";

const wrap = css({
  display: "grid",
  gap: "0.25rem",
  minWidth: "0",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  padding: "0.35rem 0.5rem",
});
const label = css({ fontSize: "0.6rem", letterSpacing: "0.06em", textTransform: "uppercase", color: "amber" });
const options = css({ display: "grid", gap: "0.15rem", fontSize: "0.74rem", color: "muted" });
const option = css({ display: "flex", gap: "0.35rem", alignItems: "baseline" });
const optionMark = css({ color: "cyan", flex: "none" });
const phaseLine = css({ fontSize: "0.66rem", color: "muted" });

function phaseText(item: ConversationItem): string {
  switch (item.phase) {
    case "waiting":
    case "pending":
      return "waiting for answer";
    case "completed":
      return "answered";
    case "failed":
      return "failed";
    default:
      return item.phase;
  }
}

function ChoicesBlock({ block }: { block: ConversationContentBlock }) {
  if (block.type !== "choices") return null;
  return (
    <div className={options}>
      {block.options.map((choice) => (
        <div className={option} key={choice.optionId}>
          <span className={optionMark} aria-hidden="true">
            ▸
          </span>
          <span>
            {choice.label}
            {choice.description ? ` — ${choice.description}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}

export function InteractionItem({ item }: { item: ConversationItem }) {
  return (
    <div className={wrap} data-testid="conversation-interaction" aria-live="off">
      <span className={label}>interaction</span>
      {item.blocks.map((block) => {
        if (block.type === "markdown" || block.type === "text") {
          return (
            <MarkdownBlock
              key={block.blockId}
              markdown={block.type === "markdown" ? block.markdown : block.text}
            />
          );
        }
        if (block.type === "choices") return <ChoicesBlock key={block.blockId} block={block} />;
        return null;
      })}
      <span className={phaseLine} data-testid="interaction-phase">
        {phaseText(item)}
      </span>
    </div>
  );
}
