import { useEffect, useState } from "react";

import { selectQueue } from "../data/selectors";
import type { ConnState } from "../data/store";
import { dashboardStore, useDashboard } from "../data/store";
import { connectEvents, connectState } from "../data/stream";
import { AttentionQueue } from "../panels/AttentionQueue";
import { DetailPanel } from "../panels/DetailPanel";
import { EngineRoom } from "../panels/EngineRoom";
import { EventRiver } from "../panels/EventRiver";
import { Hangar } from "../panels/Hangar";
import { LifecycleList } from "../panels/LifecycleList";
import { MemoryMirror } from "../panels/MemoryMirror";
import { Topology } from "../panels/Topology";

// The cockpit shell (model C, slice 5c): persistent command chrome that never hides the alarms —
// a top status bar, a left rail (attention queue + sessions = the master-caution, always visible),
// a switchable centre viewport (Operations / Engine Room / Memory / Topology / Hangar), and a
// persistent right rail (the event river ticker). The mode bar selects the viewport. Selection is
// ephemeral UI state held here and shared across panels and views (the queue↔tree↔topology coupling).
type View = "operations" | "engine" | "memory" | "topology" | "hangar";

const VIEWS: { id: View; label: string }[] = [
  { id: "operations", label: "Operations" },
  { id: "engine", label: "Engine Room" },
  { id: "memory", label: "Memory" },
  { id: "topology", label: "Topology" },
  { id: "hangar", label: "Hangar" },
];

// Cockpit wires the live SSE streams, then renders the presentational shell. The shell is split
// out so the dev gallery (/dev/bench) renders the exact same surface against fixture state
// without opening a stream.
export function Cockpit() {
  useEffect(() => connectState(), []);
  useEffect(() => connectEvents((line) => dashboardStore.getState().pushEvent(line)), []);
  return <CockpitShell />;
}

export function CockpitShell() {
  const [view, setView] = useState<View>("operations");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Open a node AND surface it in Operations: the attention queue / topology / hangar all jump
  // into the tree+detail view, so a cross-view click lands where you can inspect it.
  const open = (id: string) => {
    setSelectedId(id);
    setView("operations");
  };

  return (
    <div className="cockpit cockpit--shell">
      <div className="crt-overlay" aria-hidden="true" />
      <TopBar />
      <div className="shell__body">
        <aside className="rail rail--left">
          <AttentionQueue onSelect={open} />
          <LifecycleList selectedId={selectedId} onSelect={open} />
        </aside>
        <main className="viewport" data-view={view}>
          <ViewBody view={view} selectedId={selectedId} onOpen={open} />
        </main>
        <aside className="rail rail--right">
          <EventRiver />
        </aside>
      </div>
      <nav className="modebar" aria-label="views">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            aria-pressed={v.id === view}
            className={v.id === view ? "is-active" : ""}
            onClick={() => setView(v.id)}
          >
            {v.label}
          </button>
        ))}
      </nav>
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
    <header className="topbar">
      <h1>AGENTS REMEMBER · MISSION CONTROL</h1>
      <div className="topbar__status">
        <span className={`caution caution--${topSeverity}`} data-testid="caution">
          ⚠ {queue.length} waiting
        </span>
        {metrics ? (
          <span className="topbar__metrics">
            {metrics.runningCount} running · {metrics.blockedCount} blocked · {metrics.totalTokens}{" "}
            tok
          </span>
        ) : null}
        {generatedAt ? <span className="topbar__clock">@ {generatedAt.slice(11, 19)}</span> : null}
        <ConnBadge conn={conn} />
      </div>
    </header>
  );
}

function ConnBadge({ conn }: { conn: ConnState }) {
  const label =
    conn === "live" ? "● LIVE" : conn === "signal-lost" ? "✶ SIGNAL LOST" : "… CONNECTING";
  return (
    <span className={`status status--${conn}`} data-testid="conn">
      {label}
    </span>
  );
}
