import { useEffect, useState } from "react";

import type { ConnState } from "../data/store";
import { useDashboard } from "../data/store";
import { connectState } from "../data/stream";
import { AttentionQueue } from "../panels/AttentionQueue";
import { DetailPanel } from "../panels/DetailPanel";
import { OperationTree } from "../panels/OperationTree";
import { SessionStrip } from "../panels/SessionStrip";

// The three-pane mission-control console (note 06/07 IA): tree | detail | side stack. Selection
// is ephemeral UI state held here (not in the data store) and shared across panels, so the
// attention queue, tree, and detail panel stay coupled. The 5c panels are inert placeholders.
export function Cockpit() {
  const conn = useDashboard((s) => s.conn);
  const metrics = useDashboard((s) => s.metrics);
  const generatedAt = useDashboard((s) => s.generatedAt);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => connectState(), []);

  return (
    <div className="cockpit">
      <div className="crt-overlay" aria-hidden="true" />
      <header className="cockpit__bar">
        <h1>AGENTS REMEMBER · MISSION CONTROL</h1>
        <ConnBadge conn={conn} />
      </header>

      <main className="console">
        <div className="console__col console__col--tree">
          <OperationTree selectedId={selectedId} onSelect={setSelectedId} />
        </div>
        <div className="console__col console__col--detail">
          <DetailPanel selectedId={selectedId} />
        </div>
        <div className="console__col console__col--side">
          <AttentionQueue onSelect={setSelectedId} />
          <SessionStrip selectedId={selectedId} onSelect={setSelectedId} />
          <PanelPlaceholder title="Engine room" note="per-worktree provider stacks — slice 5c" />
          <PanelPlaceholder title="Memory mirror" note="coverage / drift / ledger — slice 5c" />
          <PanelPlaceholder title="Event river" note="trust-tagged observer feed — slice 5c" />
          <PanelPlaceholder title="Hangar" note="stale / uncleaned worktrees — slice 5c" />
        </div>
      </main>

      <footer className="cockpit__foot">
        {metrics
          ? `${metrics.lifecycleCount} lifecycles · ${metrics.runningCount} running · ` +
            `${metrics.blockedCount} blocked · ${metrics.totalTokens} tokens`
          : "awaiting projection…"}
        {generatedAt ? ` · @ ${generatedAt}` : ""}
      </footer>
    </div>
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

function PanelPlaceholder({ title, note }: { title: string; note: string }) {
  return (
    <section className="panel panel--placeholder">
      <h2>{title}</h2>
      <p className="muted">{note}</p>
    </section>
  );
}
