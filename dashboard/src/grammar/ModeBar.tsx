import { ToggleButton, ToggleButtonGroup } from "react-aria-components";

import { css } from "../../styled-system/css";

// The viewport switcher as a real ARIA selection group (React Aria) styled by Panda — the first
// of the layered-blueprint primitives. React Aria gives roving focus, arrow-key navigation, and
// `data-selected` / `data-focus-visible` state; Panda carries the whole CRT look through tokens +
// conditions (`_selected` / `_focusVisible`), so the visual is unchanged from the old `.modebar`.
// Single-select, never empty: exactly one view is active. (A full `Tabs`/`TabPanel` wiring with
// aria-controls to the viewport is a later option once the viewport DOM is ported.)
const bar = css({
  display: "flex",
  flexShrink: 0,
  gap: "0.3rem",
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.5rem",
});

const button = css({
  font: "inherit",
  fontSize: "0.72rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  paddingInline: "0.75rem",
  paddingBlock: "0.3rem",
  background: "bg",
  color: "muted",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  _selected: {
    color: "amber",
    borderColor: "amber",
    textShadow: "0 0 calc(5px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.4)",
  },
  _focusVisible: {
    outlineWidth: "1px",
    outlineStyle: "solid",
    outlineColor: "amber",
    outlineOffset: "1px",
  },
});

export function ModeBar<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: { id: T; label: string }[];
  value: T;
  onChange: (next: T) => void;
  label: string;
}) {
  return (
    <ToggleButtonGroup
      className={bar}
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={[value]}
      onSelectionChange={(keys) => {
        const next = [...keys][0];
        if (typeof next === "string") onChange(next as T);
      }}
      aria-label={label}
    >
      {items.map((it) => (
        <ToggleButton key={it.id} id={it.id} className={button}>
          {it.label}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
