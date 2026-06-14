import { useEffect, useMemo, useRef } from "react";

import { useDashboard } from "../data/store";
import { mountConstel, type ConstelHandle } from "../topology/constel";
import { buildTopology } from "../topology/model";

// Topology — the radial constellation hero (mc2 harvest #4), ported to a React-wrapped <canvas>.
// React owns the projection→model adapter (a pure, memoised build over the store maps) + mount;
// the renderer (constel.ts) stays imperative. Clicking a lifecycle node couples back into
// Operations (the queue↔tree↔topology selection coupling). Halts to a static frame under
// `?effects=off` (the determinism flag).
export function Topology({ onSelect }: { onSelect: (id: string) => void }) {
  const lifecycles = useDashboard((s) => s.lifecycles);
  const enclosures = useDashboard((s) => s.enclosures);
  const providers = useDashboard((s) => s.providers);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  const model = useMemo(
    () => buildTopology(Object.values(lifecycles), Object.values(enclosures), Object.values(providers)),
    [lifecycles, enclosures, providers],
  );

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
    const wrap = wrapRef.current;
    const tip = tipRef.current;
    if (!canvas || !wrap || !tip) return;
    const handle = mountConstel({ canvas, wrap, tip }, modelRef.current, {
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
    <section className="panel topology-shell" data-testid="topology">
      <h2>Topology · workspace constellation</h2>
      <div className="constel-wrap" ref={wrapRef}>
        <canvas ref={canvasRef} className="constel" data-testid="topology-canvas" />
        <div className="constel-tip" ref={tipRef} aria-hidden="true" />
        <div className="constel-legend">
          <span>
            <i className="legdot legdot--ok" /> ok
          </span>
          <span>
            <i className="legdot legdot--warn" /> waiting
          </span>
          <span>
            <i className="legdot legdot--crit" /> blocked
          </span>
          <span>
            <i className="legdot legdot--idle" /> indexing / dormant
          </span>
        </div>
      </div>
    </section>
  );
}
