import { useEffect, useState } from "react";

import { dashboardStore } from "../data/store";
import { AttentionQueue } from "../panels/AttentionQueue";
import { DetailPanel } from "../panels/DetailPanel";
import { OperationTree } from "../panels/OperationTree";
import { SessionStrip } from "../panels/SessionStrip";
import { GALLERY } from "./fixtures";

// The gallery (note 15): every panel rendered against a fixture grammar-state, switchable by
// `?state=`. It hydrates the real store from a fixture (no live stream) and renders the panel
// set — the surface the screenshot-annotate review loop captures. The `?effects=off` flag (read
// in main.tsx) freezes animation so screenshots/Playwright assertions are deterministic.
export function Bench() {
  const params = new URLSearchParams(window.location.search);
  const stateName = params.get("state") ?? GALLERY[0].name;
  const fixture = GALLERY.find((entry) => entry.name === stateName) ?? GALLERY[0];
  const [selectedId, setSelectedId] = useState<string | null>(
    fixture.projection.lifecycles[0]?.id ?? null,
  );

  useEffect(() => {
    dashboardStore.getState().applySnapshot(fixture.projection);
  }, [fixture]);

  return (
    <div className="cockpit bench">
      <header className="cockpit__bar">
        <h1>BENCH · {fixture.name}</h1>
        <nav className="bench__nav">
          {GALLERY.map((entry) => (
            <a
              key={entry.name}
              href={`/dev/bench?state=${entry.name}`}
              className={entry.name === stateName ? "is-active" : ""}
            >
              {entry.name}
            </a>
          ))}
        </nav>
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
        </div>
      </main>
    </div>
  );
}
