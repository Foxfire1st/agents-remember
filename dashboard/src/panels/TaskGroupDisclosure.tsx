import { css, cx } from "../../styled-system/css";
import { caretCell } from "./lifecycle-list/columnLook";

// The glyph's cell is 1ch (about 7px) wide; a press beside it used to land on the row and open
// the task's page instead. The transparent ::before widens what the caret answers to across the
// row's gap on both sides (0.3rem, inside the row's 0.31rem gap) and the row's height, without
// moving the glyph or the marks beside it.
const disclosure = css({
  position: "relative",
  padding: "0",
  background: "transparent",
  borderWidth: "0",
  cursor: "pointer",
  font: "inherit",
  textAlign: "left",
  _before: { content: '""', position: "absolute", inset: "-0.2rem -0.3rem" },
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

// The caret of a parent row of the tasks tree. A click always opens or closes it (D309), also over
// the chosen document; the list decides what the toggle does to a parent that holds it.
export function TaskGroupDisclosure({
  label,
  collapsed,
  onToggle,
}: {
  label: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const action = `${collapsed ? "Expand" : "Collapse"} ${label} tasks`;
  return (
    <button
      type="button"
      className={cx(caretCell, disclosure)}
      aria-label={action}
      aria-expanded={!collapsed}
      title={action}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      {collapsed ? "▸" : "▾"}
    </button>
  );
}
