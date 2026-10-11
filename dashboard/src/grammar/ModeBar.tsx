import { ToggleButton, ToggleButtonGroup } from "react-aria-components";

import { css } from "../../styled-system/css";
import { viewTabButton as button } from "./viewTabLook";

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
  "@media (max-width: 40rem)": {
    gap: "0.2rem",
    "& button": { paddingInline: "0.35rem", letterSpacing: "0" },
  },
  borderTopWidth: "1px",
  borderTopStyle: "solid",
  borderTopColor: "grid",
  paddingTop: "0.5rem",
});

// `button` is the one shared view-tab definition in ./viewTabLook: the tabs
// take their whole look from it, and the role launcher's controls take their
// height, border, corner radius, font size, letter spacing and letter case
// from the same definition.

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
