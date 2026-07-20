// Terminal diagnostics drawer (design §12.6, §14.1; finding A7). Closed by default and on a fresh
// profile. It stays mounted for xterm/scrollback continuity but, when closed, is `inert` and absent
// from the accessibility tree with its height collapsed — closing it never touches the conversation,
// draft, queue, interaction, or native process. Vendor output is FRAMED as diagnostic content: an
// inset container, a label, and a muted border so vendor colors can never read as app chrome. For
// controlled sessions the hosted PTY is read-only (input disabled); it supplies no timeline items.

import { css } from "../../../../styled-system/css";
import type { OpenSession } from "../../../data/sessions";
import { PtySurface } from "../PtySurface";

const shell = css({
  flexShrink: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  transition: "none", // keyboard/programmatic toggles never animate (§15.1)
});
const closed = css({ height: "0", borderWidth: "0" });
const openShell = css({
  height: "clamp(8rem, 30vh, 22rem)",
  resize: "vertical",
  marginTop: "0.3rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  // The inset background differentiates the diagnostic frame from the app stage (A7).
  background: "color-mix(in oklch, token(colors.bg) 60%, token(colors.grid))",
});
const header = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  flexShrink: 0,
  paddingInline: "0.5rem",
  paddingBlock: "0.2rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
});
const label = css({
  fontSize: "0.62rem",
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "muted",
});
const caption = css({ fontSize: "0.62rem", color: "dormant", fontStyle: "italic" });
const closeButton = css({
  font: "inherit",
  fontSize: "0.64rem",
  marginLeft: "auto",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  cursor: "pointer",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
});
// The vendor frame: the PTY renders inside this inset so its own colors stay contained.
const vendorFrame = css({
  flex: "1",
  minHeight: "0",
  margin: "0.35rem",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.grid) 70%, transparent)",
  borderRadius: "2px",
  overflow: "hidden",
  display: "flex",
});

export function TerminalDiagnosticsDrawer({
  focused,
  open,
  onClose,
}: {
  focused?: OpenSession;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <section
      className={`${shell} ${open ? openShell : closed}`}
      // Closed = inert + removed from the a11y tree, even while mounted for state continuity (§14.1).
      inert={!open ? true : undefined}
      aria-hidden={!open}
      aria-label="Terminal diagnostics"
      data-testid="terminal-diagnostics-drawer"
      data-open={open ? "true" : "false"}
    >
      {open ? (
        <>
          <div className={header}>
            <span className={label}>Terminal diagnostics</span>
            <span className={caption}>diagnostic stream · read only · not conversation history</span>
            <button
              type="button"
              className={closeButton}
              onClick={onClose}
              data-testid="terminal-diagnostics-close"
            >
              close
            </button>
          </div>
          <div className={vendorFrame} data-testid="terminal-diagnostics-frame">
            <PtySurface focused={focused} readOnly />
          </div>
        </>
      ) : null}
    </section>
  );
}
