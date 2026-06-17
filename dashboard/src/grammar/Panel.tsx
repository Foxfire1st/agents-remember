import type { ReactNode } from "react";

import { css, cva, cx } from "../../styled-system/css";

// The shared panel chrome (slice 5d primitive): a self-scrolling box with a sticky header band, so
// rows scroll UNDER the opaque header and never into a gap above it — no `.rail > .panel`
// descendant coupling. Pass `title` for the common `<h2>` head, or `head` for a custom band (the
// lifecycle list bundles its pivot in there). `className` carries per-slot sizing from the rail /
// viewport (e.g. flex / max-height).
// `fill` switches the box from the default self-scrolling block to a bounded flex column whose own
// overflow is hidden — so a panel that hosts its own internal layout (the Engine Room's 3-zone grid)
// fills the slot at a fixed height and lets its columns scroll on their own, instead of the grid
// sizing to its tallest column's content and the whole panel scrolling between selections.
const shell = cva({
  base: {
    background: "bgPanel",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "grid",
    borderRadius: "3px",
    minHeight: "0",
    paddingInline: "0.8rem",
    paddingBottom: "0.7rem",
  },
  variants: {
    fill: {
      false: { display: "block", overflow: "auto" },
      true: { display: "flex", flexDirection: "column", overflow: "hidden" },
    },
  },
  defaultVariants: { fill: false },
});

// Sticky band: bleeds over the horizontal padding for a full-width bg; its own padding-top is the
// top spacing, so with the container's padding-top:0 it rests flush at the very top.
const band = css({
  position: "sticky",
  top: "0",
  zIndex: "2",
  marginInline: "-0.8rem",
  marginBottom: "0.4rem",
  paddingInline: "0.8rem",
  paddingTop: "0.7rem",
  paddingBottom: "0.3rem",
  background: "bgPanel",
});

export function Panel({
  title,
  head,
  className,
  fill = false,
  testid,
  children,
}: {
  title?: ReactNode;
  head?: ReactNode;
  className?: string;
  fill?: boolean;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <section className={cx(shell({ fill }), className)} data-testid={testid}>
      <div className={band}>{head ?? <h2>{title}</h2>}</div>
      {children}
    </section>
  );
}
