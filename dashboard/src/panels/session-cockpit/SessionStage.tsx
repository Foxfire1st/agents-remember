import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { HeaderStrip } from "./HeaderStrip";

// The SessionStage container: the fixed layer order —
// HeaderStrip (always) → the reserved WorkingLine slot → the surface (the PTY)
// → the composer. The container ships with HeaderStrip; the PTY/composer
// placeholders keep the keyboard-zone markers alive so the zone contract stays testable.

const stage = css({
  display: "flex",
  flexDirection: "column",
  flex: "1",
  minHeight: "0",
  minWidth: "0",
  gap: "0.4rem",
});
const headerRow = css({
  display: "flex",
  alignItems: "baseline",
  gap: "0.5rem",
  minWidth: "0",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingBottom: "0.3rem",
  _focusVisible: { outlineWidth: "1px", outlineStyle: "solid", outlineColor: "amber", outlineOffset: "1px" },
});
const headerHost = css({ flex: "1", minWidth: "0" });
// The focus-handoff note is screen-reader-only — the
// announcement (accessibility) survives, the visible banner-per-closed-chat does not.
const handoffNote = css({
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: "0",
  margin: "-1px",
  overflow: "hidden",
  clipPath: "inset(50%)",
  whiteSpace: "nowrap",
  border: "0",
});
const emptyIdentity = css({ fontSize: "0.78rem", color: "muted" });

export function SessionStage({
  focused,
  cockpit,
  handoff,
  headerExtra,
  headerActions,
  controlPopover,
  children,
}: {
  focused: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
  /** One-line focus-handoff note: the previously focused seat retired/landed. */
  handoff: string | null;
  /** View-owned header chips (the ~80-col floor hint) — rendered after the strip. */
  headerExtra?: React.ReactNode;
  /** Stage-level action buttons (inspector/rail toggles) live on the
      title row's right — the StatusLine bar they sat on is gone. */
  headerActions?: React.ReactNode;
  /** Controlled ModelEffortControl popover state (palette commands open the same popover). */
  controlPopover?: { open: boolean; onOpenChange: (open: boolean) => void };
  /** The surface + composer placeholders (owned by SessionsView so the zone markers persist).
      The WorkingLine slot lives HERE too, between the
      conversation and the composer — the stage's top chrome no longer reserves it. */
  children: React.ReactNode;
}) {
  return (
    <div className={stage} data-testid="session-stage">
      <header className={headerRow} data-stage-header tabIndex={-1}>
        {focused ? (
          <span className={headerHost}>
            <HeaderStrip session={focused} cockpit={cockpit} controlPopover={controlPopover} />
          </span>
        ) : (
          <span className={emptyIdentity} data-testid="stage-empty-identity">
            no focused session — pick one on the rail, or run “Launch session…” from the palette
            (ctrl+k)
          </span>
        )}
        {headerExtra}
        {headerActions ? (
          <span
            style={{ flex: "none", display: "inline-flex", gap: "0.4rem" }}
            data-testid="stage-header-actions"
          >
            {headerActions}
          </span>
        ) : null}
      </header>
      {handoff ? (
        <div className={handoffNote} role="status" data-testid="stage-handoff-note">
          {handoff}
        </div>
      ) : null}
      {children}
    </div>
  );
}
