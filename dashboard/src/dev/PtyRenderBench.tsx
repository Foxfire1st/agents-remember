import { useEffect, useMemo, useRef, useState } from "react";

import { TerminalSocketContext } from "../data/terminal";
import { Terminal } from "../panels/Terminal";
// `window.__ptyBench` is read by perf/cockpit.perf.spec.ts from the driver tsconfig project, so
// the probe shapes and the `window` augmentation are declared once in benchProbes.ts.
import type { PtyFrameStats, PtySerializeProbe } from "./benchProbes";
import { benchLineLogSocketFactory, RUNNER_LINE_LOG_STREAM } from "./lineLogFixture";

// The PTY renderer measurement harness (260715-FEUI-L6 S1, master OQ-B): N concurrent REAL
// Terminal panes (the exact component the cockpit mounts) fed a synthetic runner line-log,
// measured via requestAnimationFrame deltas. `/dev/pty-bench?panes=12&renderer=webgl&rate=20
// &seconds=10&settle=3`. Results land on `window.__ptyBench` AND in the DOM for scraping; the
// decision record (numbers + verdict) lives in the L6 worker report and PtySurface.PTY_RENDERER.
// The same page runs the @xterm/addon-serialize evaluation (`?probe=serialize`): a raw xterm is
// filled with `lines` buffer lines and one serialize() pass is timed — the reattach-cost fact.

function measureFrames(seconds: number): Promise<Omit<PtyFrameStats, "renderer" | "panes" | "linesPerSecondPerPane" | "seconds">> {
  return new Promise((resolve) => {
    const deltas: number[] = [];
    let last = performance.now();
    const end = last + seconds * 1000;
    const tick = (now: number) => {
      deltas.push(now - last);
      last = now;
      if (now < end) requestAnimationFrame(tick);
      else {
        const sorted = [...deltas].sort((a, b) => a - b);
        const sum = deltas.reduce((total, value) => total + value, 0);
        resolve({
          frames: deltas.length,
          meanMs: sum / Math.max(1, deltas.length),
          p95Ms: sorted[Math.floor(sorted.length * 0.95)] ?? 0,
          maxMs: sorted[sorted.length - 1] ?? 0,
          longFrames: deltas.filter((value) => value > 33.4).length,
        });
      }
    };
    requestAnimationFrame(tick);
  });
}

async function runSerializeProbe(lines: number): Promise<PtySerializeProbe> {
  const [{ Terminal: Xterm }, { SerializeAddon }] = await Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-serialize"),
  ]);
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-10000px;width:800px;height:400px;";
  document.body.appendChild(host);
  const term = new Xterm({ scrollback: Math.max(lines, 5000) });
  const addon = new SerializeAddon();
  term.loadAddon(addon);
  term.open(host);
  await new Promise<void>((resolve) => {
    let written = 0;
    for (let i = 0; i < lines; i += 1) {
      term.write(`${RUNNER_LINE_LOG_STREAM[i % RUNNER_LINE_LOG_STREAM.length]}\r\n`, () => {
        written += 1;
        if (written === lines) resolve();
      });
    }
  });
  const startSerialize = performance.now();
  const snapshot = addon.serialize();
  const serializeMs = performance.now() - startSerialize;
  // Restore cost: write the snapshot back into a fresh terminal (the reattach path).
  const restoreTerm = new Xterm({ scrollback: Math.max(lines, 5000) });
  const restoreHost = document.createElement("div");
  restoreHost.style.cssText = host.style.cssText;
  document.body.appendChild(restoreHost);
  restoreTerm.open(restoreHost);
  const startRestore = performance.now();
  await new Promise<void>((resolve) => restoreTerm.write(snapshot, () => resolve()));
  const restoreMs = performance.now() - startRestore;
  term.dispose();
  restoreTerm.dispose();
  host.remove();
  restoreHost.remove();
  return { bufferLines: lines, serializedBytes: snapshot.length, serializeMs, restoreMs };
}

export function PtyRenderBench() {
  const params = new URLSearchParams(window.location.search);
  const panes = Number.parseInt(params.get("panes") ?? "12", 10);
  const renderer = params.get("renderer") === "webgl" ? ("webgl" as const) : ("dom" as const);
  const rate = Number.parseInt(params.get("rate") ?? "20", 10);
  const seconds = Number.parseInt(params.get("seconds") ?? "10", 10);
  const settle = Number.parseInt(params.get("settle") ?? "3", 10);
  const probe = params.get("probe");
  const probeLines = Number.parseInt(params.get("lines") ?? "5000", 10);

  const factory = useMemo(() => benchLineLogSocketFactory(rate), [rate]);
  const [result, setResult] = useState<string>("running…");
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    window.__ptyBench = { done: false };
    const run = async () => {
      try {
        if (probe === "serialize") {
          const serialize = await runSerializeProbe(probeLines);
          window.__ptyBench = { done: true, serialize };
          setResult(JSON.stringify(serialize, null, 2));
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, settle * 1000));
        const frames = await measureFrames(seconds);
        const stats: PtyFrameStats = {
          renderer,
          panes,
          linesPerSecondPerPane: rate,
          seconds,
          ...frames,
        };
        window.__ptyBench = { done: true, stats };
        setResult(JSON.stringify(stats, null, 2));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        window.__ptyBench = { done: true, error: message };
        setResult(`error: ${message}`);
      }
    };
    void run();
  }, [panes, probe, probeLines, rate, renderer, seconds, settle]);

  return (
    <TerminalSocketContext.Provider value={factory}>
      <div style={{ padding: "8px", fontFamily: "monospace", fontSize: "12px" }}>
        <div>
          pty-bench · renderer={renderer} · panes={panes} · {rate} lines/s/pane · {seconds}s window
        </div>
        <pre data-testid="pty-bench-results" style={{ whiteSpace: "pre-wrap" }}>
          {result}
        </pre>
        {probe !== "serialize" ? (
          <div
            style={{
              display: "grid",
              // minmax(0, 1fr): xterm's canvas must never inflate the track to min-content —
              // panes get REALLY squeezed, which is also what the R8 floor verification needs.
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: "6px",
            }}
          >
            {Array.from({ length: panes }, (_, index) => (
              <div key={index} style={{ height: "220px", display: "flex", minWidth: 0 }}>
                <Terminal
                  sessionId={`bench-${index}`}
                  renderer={renderer}
                  onResizeCols={(cols) => {
                    window.__ptyBenchCols = { ...window.__ptyBenchCols, [`bench-${index}`]: cols };
                  }}
                />
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </TerminalSocketContext.Provider>
  );
}
