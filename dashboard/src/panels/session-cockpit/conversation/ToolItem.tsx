// Tool item (design §12.2, §12.4). One stable-ID row that recomposes in place across start ->
// progress -> result (the reducer upserts the same itemId; a failure never spawns a second row).
// Verb-phrase head with a phase accent; output head/tail clamps behind a real disclosure button;
// reads do not auto-expand. Diff content routes to DiffBlock.

import { useState } from "react";

import { css } from "../../../../styled-system/css";
import type {
  ConversationContentBlock,
  ConversationItem,
  ConversationItemPhase,
} from "../../../data/conversation/types";
import { DiffBlock } from "./DiffBlock";
import { ClampButton, sourceLineCount, useClampIds } from "./primitives";

const OUTPUT_THRESHOLD_LINES = 12;

// FB7.4 — Claude Code / Toad tool grammar: a `●` gutter glyph (phase-colored) + verb + a dim
// lowercase phase word. No boxed uppercase tag; color is never the only carrier (the word stays).
const wrap = css({ display: "grid", gap: "0.2rem", minWidth: "0" });
const head = css({ display: "flex", alignItems: "baseline", gap: "0.5rem", minWidth: "0", fontSize: "0.8rem" });
const gutterDot = css({ flex: "none", fontSize: "0.66rem", lineHeight: "1" });
const verb = css({ fontFamily: "mono", color: "ink", overflowWrap: "anywhere", minWidth: "0" });
const phaseWord = css({ flex: "none", fontSize: "0.7rem", color: "muted" });
// Toad ShellResult idiom: a faint wash with a colored LEFT rule (the `└` relationship), indented
// under the head — not a four-sided web box.
const outputRegion = css({
  maxInlineSize: "100%",
  overflowX: "auto",
  borderRadius: "0",
  borderLeftWidth: "2px",
  borderLeftStyle: "solid",
  borderLeftColor: "color-mix(in oklch, token(colors.grid) 65%, transparent)",
  background: "color-mix(in oklch, token(colors.grid) 22%, transparent)",
  marginInlineStart: "2ch",
});
const outputPre = css({ margin: "0", padding: "0.3rem 0.45rem", fontFamily: "mono", fontSize: "0.7rem", whiteSpace: "pre-wrap", overflowWrap: "anywhere", lineHeight: "1.4", color: "muted" });
const clampedPre = css({ maxHeight: "10rem", overflow: "hidden" });

// Static per-phase accent classes (color is never the only carrier — the phase word is always shown).
const phaseClass: Record<ConversationItemPhase, string> = {
  pending: css({ color: "muted", borderColor: "grid" }),
  streaming: css({ color: "cyan", borderColor: "cyan" }),
  waiting: css({ color: "amber", borderColor: "amber" }),
  completed: css({ color: "mint", borderColor: "mint" }),
  failed: css({ color: "alarm", borderColor: "alarm" }),
  interrupted: css({ color: "amber", borderColor: "amber" }),
  unknown: css({ color: "dormant", borderColor: "dormant" }),
};

function verbPhrase(item: ConversationItem): string {
  const input = item.blocks.find((block) => block.type === "tool-input");
  if (input !== undefined && input.type === "tool-input") return input.summary;
  return item.kind === "tool-result" ? "tool result" : "tool call";
}

function outputClampState(
  text: string,
  expanded: boolean,
): { clampable: boolean; hiddenLines: number; shown: string } {
  const total = sourceLineCount(text);
  const clampable = total > OUTPUT_THRESHOLD_LINES;
  return {
    clampable,
    hiddenLines: clampable && !expanded ? Math.max(0, total - OUTPUT_THRESHOLD_LINES) : 0,
    shown:
      clampable && !expanded
        ? text.split("\n").slice(0, OUTPUT_THRESHOLD_LINES).join("\n")
        : text,
  };
}

function OutputBlock({ block }: { block: ConversationContentBlock }) {
  const { regionId } = useClampIds();
  const [expanded, setExpanded] = useState(false);
  if (block.type !== "tool-output") return null;
  const text = block.text ?? "";
  const { clampable, hiddenLines, shown } = outputClampState(text, expanded);
  if (text.length === 0) return null;
  return (
    <>
      <div className={outputRegion} tabIndex={-1} role="group" aria-label="tool output">
        <pre id={regionId} className={`${outputPre} ${clampable && !expanded ? clampedPre : ""}`.trim()}>
          {shown}
        </pre>
      </div>
      {clampable ? (
        <ClampButton
          expanded={expanded}
          onToggle={() => setExpanded((value) => !value)}
          controlsId={regionId}
          hiddenLines={hiddenLines}
        />
      ) : null}
    </>
  );
}

export function ToolItem({ item }: { item: ConversationItem }) {
  return (
    <div className={wrap} data-testid="conversation-tool">
      <div className={head}>
        <span className={`${gutterDot} ${phaseClass[item.phase]}`} aria-hidden="true">
          ●
        </span>
        <span className={verb} title={verbPhrase(item)}>
          {verbPhrase(item)}
        </span>
        <span className={phaseWord} data-testid="tool-phase">
          {item.phase}
        </span>
      </div>
      {item.blocks.map((block) => {
        if (block.type === "diff") {
          return (
            <DiffBlock
              key={block.blockId}
              path={block.path}
              unified={block.unified}
              oldText={block.oldText}
              newText={block.newText}
            />
          );
        }
        if (block.type === "tool-output") return <OutputBlock key={block.blockId} block={block} />;
        return null;
      })}
    </div>
  );
}
