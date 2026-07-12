import { css } from "../../styled-system/css";

const disclosure = css({
  flex: "none",
  display: "inline-grid",
  placeItems: "center",
  width: "1rem",
  height: "1rem",
  padding: "0",
  background: "transparent",
  color: "muted",
  borderWidth: "0",
  cursor: "pointer",
  font: "inherit",
  fontSize: "0.68rem",
  lineHeight: "1",
  _hover: { color: "ink" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});

export function TaskGroupDisclosure({
  label,
  collapsed,
  onToggle,
}: {
  label: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={disclosure}
      aria-label={`${collapsed ? "Expand" : "Collapse"} ${label} tasks`}
      aria-expanded={!collapsed}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    >
      {collapsed ? "▶" : "▼"}
    </button>
  );
}
