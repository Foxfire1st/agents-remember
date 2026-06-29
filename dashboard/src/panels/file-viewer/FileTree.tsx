// Render one Headless Tree (from useFilesTree). The library owns async loading, keyboard
// nav, and selection state; we render getItems() as indented buttons. Click fully controlled
// here (select + folder toggle + file open) — the library's own onClick is overridden so a
// folder never double-toggles. A code file with a sidecar shows a marker.
import type { CSSProperties } from "react";

import { css, cva, cx } from "../../../styled-system/css";
import type { DirEntry, Scope } from "../../data/files";
import { useFilesTree } from "./useFilesTree";

const container = css({
  flex: "1",
  minHeight: "0",
  overflow: "auto",
  paddingBlock: "0.25rem",
  outline: "none",
});
const itemBtn = cva({
  base: {
    display: "flex",
    alignItems: "center",
    gap: "0.3rem",
    width: "100%",
    border: "none",
    background: "transparent",
    color: "ink",
    font: "inherit",
    fontSize: "0.76rem",
    fontFamily: "var(--font-mono)",
    textAlign: "left",
    paddingBlock: "0.08rem",
    paddingInline: "0.3rem",
    cursor: "pointer",
    _hover: { background: "color-mix(in oklab, var(--amber) 12%, transparent)" },
    _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "-1px" },
  },
  variants: {
    selected: { true: { background: "color-mix(in oklab, var(--amber) 20%, transparent)" } },
  },
});
const glyph = css({ width: "1ch", flexShrink: 0, color: "muted" });
const dot = css({ marginLeft: "auto", color: "cyan", fontSize: "0.7rem" });

export function FileTree({
  repo,
  scope,
  side,
  onOpen,
}: {
  repo: string;
  scope: Scope;
  side: "code" | "onboarding";
  onOpen: (entry: DirEntry) => void;
}) {
  const tree = useFilesTree(repo, scope, side, onOpen);

  return (
    <div {...tree.getContainerProps(`${side} files`)} className={container} data-testid={`tree-${side}`}>
      {tree.getItems().map((item) => {
        const data = item.getItemData();
        const folder = item.isFolder();
        const props = item.getProps(); // spread for a11y/keyboard; our onClick below overrides theirs
        return (
          <button
            {...props}
            key={item.getId()}
            className={cx(itemBtn({ selected: item.isSelected() }), props.className as string | undefined)}
            style={{
              ...(props.style as CSSProperties),
              paddingLeft: `${item.getItemMeta().level * 0.85 + 0.3}rem`,
            }}
            onClick={() => {
              tree.setSelectedItems([item.getId()]);
              if (folder) {
                if (item.isExpanded()) item.collapse();
                else item.expand();
              } else if (data) {
                onOpen(data);
              }
            }}
          >
            <span className={glyph} aria-hidden>
              {folder ? (item.isExpanded() ? "▾" : "▸") : ""}
            </span>
            <span>{item.getItemName()}</span>
            {side === "code" && data?.hasSidecar ? (
              <span className={dot} aria-hidden title="has onboarding">
                ◖
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
