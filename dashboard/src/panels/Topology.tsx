import { useEffect, useMemo, useRef } from "react";

import { css, cva } from "../../styled-system/css";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import { mountConstel, type ConstelHandle } from "../topology/constel";
import { activeTopologyInputs, buildTopology } from "../topology/model";

// Topology — the radial constellation hero (mc2 harvest #4), ported to a React-wrapped <canvas>.
// React owns the projection→model adapter (a pure, memoised build over the store maps) + mount;
// the renderer (constel.ts) stays imperative, driving the canvas + tip via refs. Clicking a
// lifecycle node couples back into Operations. Halts to a static frame under `?effects=off`.
const sizing = css({ display: "flex", flexDirection: "column", flex: "1" });
const wrap = css({
  position: "relative",
  flex: "1",
  minHeight: "340px",
  marginTop: "0.4rem",
  overflow: "hidden",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  background: "radial-gradient(120% 100% at 50% 40%, oklch(0.2 0.02 250) 0%, var(--bg) 80%)",
});
// position:absolute takes the canvas out of flow so its DPR-scaled buffer height can't feed back
// into the wrap height (an indefinite-height ancestor would otherwise drive a ResizeObserver
// doubling loop). width/height:100% are kept so the replaced <canvas> still fills the wrap — as an
// absolutely-positioned element, height:100% resolves against the positioned wrap rather than
// collapsing to the buffer's intrinsic size (which would clip + off-center it). Wrap is position:relative.
const canvasBox = css({ position: "absolute", top: "0", left: "0", width: "100%", height: "100%", display: "block" });
const tip = css({
  position: "absolute",
  transform: "translate(-50%, calc(-100% - 10px))",
  paddingInline: "0.45rem",
  paddingBlock: "0.2rem",
  background: "bgPanel",
  borderWidth: "1px",
  borderStyle: "solid",
  borderColor: "grid",
  borderRadius: "3px",
  fontSize: "0.7rem",
  color: "ink",
  whiteSpace: "nowrap",
  pointerEvents: "none",
  opacity: "0",
  transition: "opacity 0.1s ease",
  zIndex: "2",
});
const legend = css({
  position: "absolute",
  left: "0",
  bottom: "0",
  display: "flex",
  flexWrap: "wrap",
  gap: "0.8rem",
  paddingInline: "0.6rem",
  paddingBlock: "0.4rem",
  fontSize: "0.7rem",
  color: "muted",
  pointerEvents: "none",
});
const legdot = cva({
  base: {
    display: "inline-block",
    width: "0.5em",
    height: "0.5em",
    borderRadius: "full",
    marginRight: "0.3em",
    verticalAlign: "middle",
  },
  variants: {
    tone: {
      ok: { background: "cyan" },
      warn: { background: "amber" },
      crit: { background: "alarm" },
      idle: { background: "dormant" },
    },
  },
});

export function Topology({ onSelect }: { onSelect: (id: string) => void }) {
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const providers = useDashboard((s) => s.providers);
  const activeWorktreeGroups = useDashboard((s) => s.activeWorktreeGroups);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  // Bound the constellation to active work: filter the store's all-time enclosures/lifecycles
  // down to the live worktree groups before building the radial model (the shared store maps
  // keep history for other views).
  const model = useMemo(() => {
    const active = activeTopologyInputs(
      Object.values(lifecycles),
      Object.values(enclosures),
      activeWorktreeGroups,
    );
    return buildTopology(active.lifecycles, active.enclosures, Object.values(providers));
  }, [lifecycles, enclosures, providers, activeWorktreeGroups]);

  // Keep the latest onSelect + model in refs so the mount effect stays mount-once.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const modelRef = useRef(model);
  modelRef.current = model;
  const handleRef = useRef<ConstelHandle | null>(null);

  // Mount the renderer ONCE — pushing new models via update() keeps the rAF loop running, instead
  // of remounting on every projection tick (which reset the animation: T, comets, provider spin).
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapEl = wrapRef.current;
    const tipEl = tipRef.current;
    if (!canvas || !wrapEl || !tipEl) return;
    const handle = mountConstel({ canvas, wrap: wrapEl, tip: tipEl }, modelRef.current, {
      onSelect: (id) => onSelectRef.current(id),
    });
    handleRef.current = handle;
    return () => {
      handle.destroy();
      handleRef.current = null;
    };
  }, []);

  // Push data updates into the running renderer.
  useEffect(() => {
    handleRef.current?.update(model);
  }, [model]);

  return (
    <Panel testid="topology" title="Topology · workspace constellation" className={sizing} fill>
      <div className={wrap} ref={wrapRef}>
        <canvas ref={canvasRef} className={canvasBox} data-testid="topology-canvas" />
        <div className={tip} ref={tipRef} aria-hidden="true" />
        <div className={legend}>
          <span>
            <i className={legdot({ tone: "ok" })} /> ok
          </span>
          <span>
            <i className={legdot({ tone: "warn" })} /> waiting
          </span>
          <span>
            <i className={legdot({ tone: "crit" })} /> blocked
          </span>
          <span>
            <i className={legdot({ tone: "idle" })} /> indexing / dormant
          </span>
        </div>
      </div>
    </Panel>
  );
}
