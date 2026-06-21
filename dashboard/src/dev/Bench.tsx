import { useState } from "react";

import { CockpitShell } from "../cockpit/Cockpit";
import { ScenarioPlayer } from "./ScenarioPlayer";
import { SCENARIOS } from "./scenarios";

// The dev bench (note 15 / slice 5i): the exact model-C shell driven through phase-transition timelines.
// A scenario player (bottom) walks each mode's frames through the REAL store so the integrated motion is
// verifiable end-to-end — not just static frames. `?scenario=` (or the legacy `?state=`, which now selects
// a folded-in single-frame "resting" scenario) picks the mode. `?effects=off` (read in main.tsx) freezes
// animation so Playwright assertions on the settled end-state stay deterministic.
export function Bench() {
  const params = new URLSearchParams(window.location.search);
  // `?scenario=happy-build` is the legacy deep link; it aliases to the build-up timeline (5k).
  const raw = params.get("scenario") ?? params.get("state");
  const wanted = raw === "happy-build" ? "build-up" : raw;
  const [scenario, setScenario] = useState(
    () => SCENARIOS.find((entry) => entry.name === wanted) ?? SCENARIOS[0],
  );

  // The mode list as a single compact selector (was a wrapped button wall that overlapped the
  // cockpit header). Grouped: the lifecycle timelines (build-up · tear-down), then the failure-mode
  // timelines, then the folded-in single-frame resting states. `?scenario=` deep links still resolve
  // in the initial state above.
  const timelines = SCENARIOS.filter((entry) => entry.frames.length > 1);
  const lifecycle = new Set(["build-up", "tear-down"]);
  const groups = [
    { label: "Lifecycle", entries: timelines.filter((entry) => lifecycle.has(entry.name)) },
    { label: "Failure modes", entries: timelines.filter((entry) => !lifecycle.has(entry.name)) },
    { label: "Resting states", entries: SCENARIOS.filter((entry) => entry.frames.length === 1) },
  ].filter((group) => group.entries.length > 0);

  return (
    <>
      <div className="bench-overlay bench__picker">
        <label className="bench__picker-label" htmlFor="bench-scenario">
          scenario
        </label>
        <select
          id="bench-scenario"
          className="bench__select"
          value={scenario.name}
          aria-label="scenario"
          onChange={(event) =>
            setScenario(SCENARIOS.find((entry) => entry.name === event.target.value) ?? SCENARIOS[0])
          }
        >
          {groups.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.entries.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      <CockpitShell />
      <ScenarioPlayer key={scenario.name} scenario={scenario} />
    </>
  );
}
