import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import { HeaderStrip } from "./HeaderStrip";

// The SessionStage container (260715-FEUI-L2 S5, spec §1.2): the fixed layer order —
// HeaderStrip (always) → the reserved WorkingLine slot (rendered by L6) → the surface (the PTY,
// L6) → the composer (L5). This leaf ships the container + HeaderStrip; the PTY/composer
// placeholders keep L1's keyboard-zone markers alive so the zone contract stays testable.

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
const handoffNote = css({
  fontSize: "0.7rem",
  color: "amber",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "color-mix(in oklch, token(colors.amber) 45%, transparent)",
  borderRadius: "2px",
  paddingInline: "0.4rem",
  paddingBlock: "0.15rem",
  flexShrink: 0,
});
// The reserved WorkingLine slot — zero-height until L6 renders the turn theater into it.
const workingLineSlot = css({ flexShrink: 0, minHeight: "0" });
const emptyIdentity = css({ fontSize: "0.78rem", color: "muted" });

export function SessionStage({
  focused,
  cockpit,
  handoff,
  headerExtra,
  children,
}: {
  focused: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
  /** One-line focus-handoff note (F17): the previously focused seat retired/landed. */
  handoff: string | null;
  /** View-owned header chips (the ~80-col floor hint) — rendered after the strip. */
  headerExtra?: React.ReactNode;
  /** The surface + composer placeholders (owned by SessionsView so L1's zone markers persist). */
  children: React.ReactNode;
}) {
  return (
    <div className={stage} data-testid="session-stage">
      <header className={headerRow} data-stage-header tabIndex={-1}>
        {focused ? (
          <span className={headerHost}>
            <HeaderStrip session={focused} cockpit={cockpit} />
          </span>
        ) : (
          <span className={emptyIdentity} data-testid="stage-empty-identity">
            no focused session — pick one on the rail, or launch from Chats (cockpit launcher: L5)
          </span>
        )}
        {headerExtra}
      </header>
      {handoff ? (
        <div className={handoffNote} role="status" data-testid="stage-handoff-note">
          {handoff}
        </div>
      ) : null}
      <div data-slot="working-line" className={workingLineSlot} data-testid="stage-working-line-slot">
        {/* Reserved: the WorkingLine (turn theater — verb, ~elapsed, ⏹ stop) is rendered by L6. */}
      </div>
      {children}
    </div>
  );
}
