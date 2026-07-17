import { useId, useState, type KeyboardEvent } from "react";

import { css } from "../../../styled-system/css";
import type { OpenSession } from "../../data/sessions";
import type { PerSessionCockpit } from "../../data/sessionCockpitStore";
import type { AgentPickupNode, SupervisorHeartbeat } from "../../types/projection";
import { BusPane } from "./BusPane";
import { CapabilitiesPane } from "./CapabilitiesPane";
import { EvidencePane } from "./EvidencePane";
import { InspectorNote, InspectorSection, inspectorPane } from "./InspectorPrimitives";

export { setLedgerEntryLine } from "./EvidencePane";

// L7 tab host only. Each pane owns one evidence domain, while this component owns the accessible
// tab interaction and nothing else; keeping it composition-only prevents the already dense
// SessionsView route from absorbing another feature's logic.

type InspectorTab = "evidence" | "capabilities" | "bus";

const TABS: readonly { id: InspectorTab; label: string }[] = [
  { id: "evidence", label: "Evidence" },
  { id: "capabilities", label: "Capabilities" },
  { id: "bus", label: "Bus" },
];
const EMPTY_PICKUPS: readonly AgentPickupNode[] = [];

const tabList = css({
  position: "sticky",
  top: "0",
  zIndex: "1",
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  gap: "2px",
  paddingBottom: "0.5rem",
  background: "bgPanel",
});
const tab = css({
  minWidth: "0",
  font: "inherit",
  fontSize: "0.66rem",
  paddingInline: "0.25rem",
  paddingBlock: "0.2rem",
  color: "muted",
  background: "transparent",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "2px",
  cursor: "pointer",
  overflow: "hidden",
  textOverflow: "ellipsis",
  _hover: { color: "amber", borderColor: "amber" },
  _focusVisible: { outline: "1px solid token(colors.amber)", outlineOffset: "1px" },
  "&[aria-selected='true']": { color: "ink", borderColor: "cyan", background: "bg" },
});
const panel = css({ minWidth: "0" });

export function SeatInspector({
  session,
  cockpit,
  pickups = EMPTY_PICKUPS,
  heartbeat = null,
}: {
  session: OpenSession | undefined;
  cockpit: PerSessionCockpit | undefined;
  pickups?: readonly AgentPickupNode[];
  heartbeat?: SupervisorHeartbeat | null;
}) {
  const [active, setActive] = useState<InspectorTab>("evidence");
  const uid = useId();

  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, current: number) => {
    let next: number | null = null;
    if (event.key === "ArrowRight") next = (current + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    if (next === null) return;
    event.preventDefault();
    setActive(TABS[next].id);
    const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]',
    );
    tabs?.[next]?.focus();
  };

  return (
    <div data-testid="seat-inspector">
      <div className={tabList} role="tablist" aria-label="Inspector evidence domains">
        {TABS.map((item, index) => (
          <button
            key={item.id}
            id={`${uid}-${item.id}-tab`}
            type="button"
            role="tab"
            className={tab}
            aria-selected={active === item.id}
            aria-controls={`${uid}-${item.id}-panel`}
            tabIndex={active === item.id ? 0 : -1}
            onClick={() => setActive(item.id)}
            onKeyDown={(event) => moveTab(event, index)}
            data-testid={`inspector-tab-${item.id}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div
        id={`${uid}-evidence-panel`}
        className={panel}
        role="tabpanel"
        aria-labelledby={`${uid}-evidence-tab`}
        hidden={active !== "evidence"}
        data-testid="inspector-panel-evidence"
      >
        <EvidencePane session={session} cockpit={cockpit} />
      </div>
      <div
        id={`${uid}-capabilities-panel`}
        className={panel}
        role="tabpanel"
        aria-labelledby={`${uid}-capabilities-tab`}
        hidden={active !== "capabilities"}
        data-testid="inspector-panel-capabilities"
      >
        {session ? (
          <CapabilitiesPane session={session} cockpit={cockpit} />
        ) : (
          <div className={inspectorPane} data-testid="capabilities-pane">
            <InspectorSection title="Exact-session capabilities">
              <InspectorNote testId="inspector-capabilities-no-focus">
                No focused seat. Live model, effort, selection, and cache facts require an exact
                session; no fleet-wide capability claim is available.
              </InspectorNote>
            </InspectorSection>
          </div>
        )}
      </div>
      <div
        id={`${uid}-bus-panel`}
        className={panel}
        role="tabpanel"
        aria-labelledby={`${uid}-bus-tab`}
        hidden={active !== "bus"}
        data-testid="inspector-panel-bus"
      >
        <BusPane session={session} pickups={pickups} heartbeat={heartbeat} />
      </div>
    </div>
  );
}
