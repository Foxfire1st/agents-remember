import type { ReactNode } from "react";

import { css, cx } from "../../styled-system/css";

// The shared panel chrome (slice 5d primitive): a self-scrolling box with a sticky header band, so
// rows scroll UNDER the opaque header and never into a gap above it — no `.rail > .panel`
// descendant coupling. Pass `title` for the common `<h2>` head, or `head` for a custom band (the
// lifecycle list bundles its pivot in there). `className` carries per-slot sizing from the rail /
// viewport (e.g. flex / max-height).
const shell = css({
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  minHeight: "0",
  overflow: "auto",
  paddingInline: "0.8rem",
  paddingBottom: "0.7rem",
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
  testid,
  children,
}: {
  title?: ReactNode;
  head?: ReactNode;
  className?: string;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <section className={cx(shell, className)} data-testid={testid}>
      <div className={band}>{head ?? <h2>{title}</h2>}</div>
      {children}
    </section>
  );
}
