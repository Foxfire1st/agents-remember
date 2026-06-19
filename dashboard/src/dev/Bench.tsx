import { useEffect, useState } from "react";

import { CockpitShell } from "../cockpit/Cockpit";
import { dashboardStore } from "../data/store";
import { TerminalSocketContext } from "../data/terminal";
import { GALLERY } from "./fixtures";
import { mockTerminalSocketFactory } from "./mockTerminalSocket";

// The gallery (note 15): the exact model-C shell rendered against a hand-authored fixture state,
// picked from a compact selector (the old full-width tab strip blocked the cockpit's top). It
// hydrates the real store (no live stream) so the screenshot-annotate review loop captures every
// panel in every grammar state; the selection is mirrored into `?state=` so a state stays shareable.
// `?effects=off` (read in main.tsx) freezes animation so screenshots / Playwright assertions are
// deterministic.
export function Bench() {
  const initial = new URLSearchParams(window.location.search).get("state") ?? GALLERY[0].name;
  const [stateName, setStateName] = useState(initial);
  const fixture = GALLERY.find((entry) => entry.name === stateName) ?? GALLERY[0];

  useEffect(() => {
    const store = dashboardStore.getState();
    store.applySnapshot(fixture.projection);
    dashboardStore.setState({ events: [] });
    for (const event of fixture.events ?? []) store.pushEvent(JSON.stringify(event));
  }, [fixture]);

  const selectState = (name: string) => {
    setStateName(name);
    window.history.replaceState(null, "", `/dev/bench?state=${name}`);
  };

  return (
    // The dev mock socket makes the Chats view (slice 6e) render a live-looking terminal with no
    // backend; production has no provider, so the real cockpit uses a same-origin WebSocket.
    <TerminalSocketContext.Provider value={mockTerminalSocketFactory}>
      <div className="bench-overlay">
        <label className="bench__picker">
          state
          <select
            aria-label="gallery state"
            value={stateName}
            onChange={(event) => selectState(event.target.value)}
          >
            {GALLERY.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <CockpitShell />
    </TerminalSocketContext.Provider>
  );
}
