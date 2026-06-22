// Slice 5i — the scenario-player transport (ported from public/_proto/podstage.html's player). It owns
// the cursor / play timer / loop and, on every seek, applies the current FULL frame to the real store so
// the real cockpit animates the diff. No incremental SVG mutation — complete frames, so seek = apply
// frame t (animates from wherever the cockpit is), play = walk on a timer, loop = wrap.
import { useEffect, useState } from "react";

import { dashboardStore } from "../data/store";
import type { Scenario, ScenarioFrame } from "./scenarios";

// The driver: apply a complete frame. Reset the event tail, then replay the frame's events (if any), so
// the river reflects the frame rather than accumulating across the whole timeline.
function applyFrame(frame: ScenarioFrame) {
  const store = dashboardStore.getState();
  store.applySnapshot(frame.projection);
  dashboardStore.setState({ events: [] });
  for (const event of frame.events ?? []) store.pushEvent(JSON.stringify(event));
}

const DEFAULT_FRAME_MS = 1600;

export function ScenarioPlayer({ scenario }: { scenario: Scenario }) {
  const [cur, setCur] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const frames = scenario.frames;

  // a new scenario resets the player to its first frame. The Bench keys this component by scenario name, so
  // this runs once per scenario. Reset the SHARED store FIRST (bumps `gen`) so the engine-room canvas remounts
  // clean and the previous mode's exiting failure overlays can't orphan + bleed through the dropdown switch.
  useEffect(() => {
    dashboardStore.getState().reset();
    setCur(0);
    setPlaying(false);
  }, [scenario]);

  // seek — apply the current frame; the real cockpit animates the diff
  useEffect(() => {
    applyFrame(frames[cur]);
  }, [frames, cur]);

  // play — advance on a timer; wrap when looping, else stop at the end
  useEffect(() => {
    if (!playing) return;
    const id = window.setTimeout(() => {
      setCur((c) => {
        if (c + 1 < frames.length) return c + 1;
        if (loop) return 0;
        setPlaying(false);
        return c;
      });
    }, frames[cur].durMs ?? DEFAULT_FRAME_MS);
    return () => window.clearTimeout(id);
  }, [playing, cur, loop, frames]);

  const seek = (next: number) => {
    setPlaying(false);
    setCur(Math.max(0, Math.min(frames.length - 1, next)));
  };

  return (
    <div className="player" data-testid="scenario-player">
      <div className="player__caption" data-testid="player-caption">
        {frames[cur].caption}
      </div>
      <div className="player__controls">
        <button type="button" title="reset" data-testid="player-reset" onClick={() => seek(0)}>
          ⏮
        </button>
        <button type="button" title="previous beat" onClick={() => seek(cur - 1)}>
          ◀
        </button>
        <button
          type="button"
          title={playing ? "pause" : "play"}
          data-testid="player-play"
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <button type="button" title="next beat" onClick={() => seek(cur + 1)}>
          ▶▌
        </button>
        <button
          type="button"
          title="loop"
          className={loop ? "is-on" : ""}
          aria-pressed={loop}
          onClick={() => setLoop((l) => !l)}
        >
          ⟳
        </button>
        <input
          type="range"
          className="player__scrub"
          min={0}
          max={frames.length - 1}
          value={cur}
          aria-label="scrub beats"
          onChange={(event) => seek(Number(event.target.value))}
        />
        <span className="player__count">
          {cur + 1}/{frames.length}
        </span>
      </div>
    </div>
  );
}
