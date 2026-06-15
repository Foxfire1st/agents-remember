import { useEffect, useState } from "react";

import { css, cva, cx } from "../../styled-system/css";
import { selectQueue } from "../data/selectors";
import type { ConnState } from "../data/store";
import { dashboardStore, useDashboard } from "../data/store";
import { connectEvents, connectState } from "../data/stream";
import { ModeBar } from "../grammar/ModeBar";
import { AttentionQueue } from "../panels/AttentionQueue";
import { DetailPanel } from "../panels/DetailPanel";
import { EngineRoom } from "../panels/EngineRoom";
import { EventRiver } from "../panels/EventRiver";
import { Hangar } from "../panels/Hangar";
import { LifecycleList } from "../panels/LifecycleList";
import { MemoryMirror } from "../panels/MemoryMirror";
import { Topology } from "../panels/Topology";

// The cockpit shell (model C, slice 5c): persistent command chrome that never hides the alarms —
// a top status bar, a left rail (attention queue + lifecycle list = the master-caution, always
// visible), a switchable centre viewport (Operations / Engine Room / Memory / Topology / Hangar),
// and a persistent right rail (the event river ticker). The mode bar selects the viewport.
// Selection is ephemeral UI state held here and shared across panels and views.
type View = "operations" | "engine" | "memory" | "topology" | "hangar";

const VIEWS: { id: View; label: string }[] = [
  { id: "operations", label: "Operations" },
  { id: "engine", label: "Engine Room" },
  { id: "memory", label: "Memory" },
  { id: "topology", label: "Topology" },
  { id: "hangar", label: "Hangar" },
];

// Shell layout (slice 5d: co-located Panda css). The shell pins to the viewport so the top + mode
// bars stay fixed and the rails / viewport scroll internally; the marker classes (cockpit--shell /
// shell__body / rail / viewport) are kept so the per-panel descendant rules + tests still resolve.
const shell = css({
  position: "relative",
  display: "flex",
  flexDirection: "column",
  height: "100vh",
  minHeight: "0",
  overflow: "hidden",
  padding: "0.6rem 0.8rem",
  gap: "0.6rem",
});
const topbar = css({
  display: "flex",
  flexShrink: 0,
  alignItems: "center",
  justifyContent: "space-between",
  gap: "1rem",
  borderBottomWidth: "1px",
  borderBottomStyle: "solid",
  borderBottomColor: "grid",
  paddingBottom: "0.5rem",
});
const title = css({
  margin: "0",
  fontSize: "0.9rem",
  letterSpacing: "0.18em",
  color: "amber",
  textShadow: "0 0 calc(6px * var(--glow-strength)) oklch(0.82 0.16 75 / 0.5)",
});
const statusRow = css({
  display: "flex",
  alignItems: "center",
  gap: "0.9rem",
  fontSize: "0.78rem",
});
const dim = css({ color: "muted" });
const caution = cva({
  base: { letterSpacing: "0.06em", color: "muted" },
  variants: {
    sev: {
      clear: { color: "mint" },
      info: { color: "cyan" },
      warn: { color: "amber" },
      alarm: { color: "alarm", animation: "pulse 0.6s steps(1) infinite" },
    },
  },
});
const connBadge = cva({
  base: { fontWeight: "600", letterSpacing: "0.1em" },
  variants: {
    state: {
      connecting: { color: "amber" },
      live: { color: "mint" },
      "signal-lost": { color: "alarm", animation: "pulse 0.5s steps(1) infinite" },
    },
  },
});
const body = css({
  display: "grid",
  gridTemplateColumns: "minmax(300px, 1fr) minmax(420px, 2.2fr) minmax(260px, 0.95fr)",
  gridTemplateRows: "minmax(0, 1fr)",
  gap: "0.7rem",
  flex: "1",
  minHeight: "0",
});
const rail = css({
  display: "flex",
  flexDirection: "column",
  gap: "0.7rem",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the rail itself does not scroll — each panel scrolls on its own
});
const viewport = css({
  display: "flex",
  flexDirection: "column",
  minWidth: "0",
  minHeight: "0",
  overflow: "hidden", // the viewport does not scroll — its panel scrolls on its own
});

// Cockpit wires the live SSE streams, then renders the presentational shell. The shell is split
// out so the dev gallery (/dev/bench) renders the exact same surface against fixture state.
export function Cockpit() {
  useEffect(() => connectState(), []);
  useEffect(() => connectEvents((line) => dashboardStore.getState().pushEvent(line)), []);
  return <CockpitShell />;
}

export function CockpitShell() {
  const [view, setView] = useState<View>("operations");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Open a node AND surface it in Operations: the attention queue / topology / hangar all jump
  // into the detail view, so a cross-view click lands where you can inspect it.
  const open = (id: string) => {
    setSelectedId(id);
    setView("operations");
  };

  return (
    <div className={cx(shell, "cockpit--shell")}>
      <div className="crt-overlay" aria-hidden="true" />
      <TopBar />
      <div className={cx(body, "shell__body")}>
        <aside className={cx(rail, "rail rail--left")}>
          <AttentionQueue onSelect={open} />
          <LifecycleList selectedId={selectedId} onSelect={open} />
        </aside>
        <main className={cx(viewport, "viewport")} data-view={view}>
          <ViewBody view={view} selectedId={selectedId} onOpen={open} />
        </main>
        <aside className={cx(rail, "rail rail--right")}>
          <EventRiver />
        </aside>
      </div>
      <ModeBar items={VIEWS} value={view} onChange={setView} label="Views" />
    </div>
  );
}

function ViewBody({
  view,
  selectedId,
  onOpen,
}: {
  view: View;
  selectedId: string | null;
  onOpen: (id: string) => void;
}) {
  switch (view) {
    case "engine":
      return <EngineRoom />;
    case "memory":
      return <MemoryMirror />;
    case "topology":
      return <Topology onSelect={onOpen} />;
    case "hangar":
      return <Hangar onSelect={onOpen} />;
    case "operations":
    default:
      return <DetailPanel selectedId={selectedId} />;
  }
}

function TopBar() {
  const conn = useDashboard((s) => s.conn);
  const metrics = useDashboard((s) => s.metrics);
  const generatedAt = useDashboard((s) => s.generatedAt);
  const queue = useDashboard(selectQueue);
  const topSeverity = queue[0]?.severity ?? "clear"; // queue is severity-sorted server-side

  return (
    <header className={topbar}>
      <h1 className={title}>AGENTS REMEMBER · MISSION CONTROL</h1>
      <div className={statusRow}>
        <span className={caution({ sev: topSeverity })} data-testid="caution">
          ⚠ {queue.length} waiting
        </span>
        {metrics ? (
          <span className={dim}>
            {metrics.runningCount} running · {metrics.blockedCount} blocked · {metrics.totalTokens}{" "}
            tok
          </span>
        ) : null}
        {generatedAt ? <span className={dim}>@ {generatedAt.slice(11, 19)}</span> : null}
        <ConnBadge conn={conn} />
      </div>
    </header>
  );
}

function ConnBadge({ conn }: { conn: ConnState }) {
  const label =
    conn === "live" ? "● LIVE" : conn === "signal-lost" ? "✶ SIGNAL LOST" : "… CONNECTING";
  return (
    <span className={connBadge({ state: conn })} data-testid="conn">
      {label}
    </span>
  );
}
