import { css, cx } from "../../styled-system/css";
import { caretCell } from "./lifecycle-list/columnLook";

const disclosure = css({
  padding: "0",
  background: "transparent",
  borderWidth: "0",
  cursor: "pointer",
  font: "inherit",
  textAlign: "left",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

// The caret of a parent row of the tasks tree. `held` is the parent the chosen document stands
// under: the tree is always open down to that document, so the caret says so and does not close.
export function TaskGroupDisclosure({
  label,
  collapsed,
  held = false,
  onToggle,
}: {
  label: string;
  collapsed: boolean;
  held?: boolean;
  onToggle: () => void;
}) {
  const action = `${collapsed ? "Expand" : "Collapse"} ${label} tasks`;
  return (
    <button
      type="button"
      className={cx(caretCell, disclosure)}
      aria-label={action}
      aria-expanded={!collapsed}
      title={held ? "Stays open: the chosen document is inside" : action}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        if (!held) onToggle();
      }}
    >
      {collapsed ? "▸" : "▾"}
    </button>
  );
}
