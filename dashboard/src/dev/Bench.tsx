import { useEffect } from "react";

import { CockpitShell } from "../cockpit/Cockpit";
import { dashboardStore } from "../data/store";
import { TerminalSocketContext } from "../data/terminal";
import { GALLERY } from "./fixtures";
import { mockTerminalSocketFactory } from "./mockTerminalSocket";

// The gallery (note 15): the exact model-C shell rendered against a hand-authored fixture state,
// switchable by `?state=`. It hydrates the real store (no live stream) so the screenshot-annotate
// review loop captures every panel in every grammar state. `?effects=off` (read in main.tsx)
// freezes animation so screenshots / Playwright assertions are deterministic.
export function Bench() {
  const params = new URLSearchParams(window.location.search);
  const stateName = params.get("state") ?? GALLERY[0].name;
  const fixture = GALLERY.find((entry) => entry.name === stateName) ?? GALLERY[0];

  useEffect(() => {
    const store = dashboardStore.getState();
    store.applySnapshot(fixture.projection);
    dashboardStore.setState({ events: [] });
    for (const event of fixture.events ?? []) store.pushEvent(JSON.stringify(event));
  }, [fixture]);

  return (
    // The dev mock socket makes the Chats view (slice 6e) render a live-looking terminal with no
    // backend; production has no provider, so the real cockpit uses a same-origin WebSocket.
    <TerminalSocketContext.Provider value={mockTerminalSocketFactory}>
      <nav className="bench__nav bench-overlay" aria-label="gallery states">
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
      <CockpitShell />
    </TerminalSocketContext.Provider>
  );
}
