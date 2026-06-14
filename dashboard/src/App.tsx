import { useEffect } from "react";

import type { ConnState } from "./data/store";
import { useDashboard } from "./data/store";
import { connectState } from "./data/stream";

// Slice 5a step 4: the minimal live shell — connection status + a raw projection view,
// proving the SSE stream end-to-end (live or --sim). The eight cockpit panels are slice 5b.
export default function App() {
  const conn = useDashboard((s) => s.conn);
  const lifecycles = useDashboard((s) => s.lifecycles);
  const providers = useDashboard((s) => s.providers);
  const metrics = useDashboard((s) => s.metrics);
  const generatedAt = useDashboard((s) => s.generatedAt);

  useEffect(() => connectState(), []);

  return (
    <div className="cockpit">
      <div className="crt-overlay" aria-hidden="true" />
      <header className="cockpit__bar">
        <h1>AGENTS REMEMBER · MISSION CONTROL</h1>
        <ConnBadge conn={conn} />
      </header>

      <main className="cockpit__body">
        <section>
          <h2>Lifecycles · {Object.keys(lifecycles).length}</h2>
          <ul className="raw-list" data-testid="lifecycles">
            {Object.values(lifecycles).map((lifecycle) => (
              <li key={lifecycle.id}>
                <span className={`dot dot--${lifecycle.state}`} aria-hidden="true" />
                {lifecycle.id} · {lifecycle.state} · {lifecycle.phase} · {lifecycle.tokens} tok
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2>Providers · {Object.keys(providers).length}</h2>
          <ul className="raw-list">
            {Object.values(providers).map((provider) => (
              <li key={provider.id}>
                {provider.id} · {provider.state} · {provider.indexingState}
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="cockpit__foot">
        {metrics
          ? `${metrics.lifecycleCount} lifecycles · ${metrics.totalTokens} tokens`
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
