import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import "./styles/tokens.css";
// The scoped WebTUI skin: after index.css so the layer-order statement there
// (reset, base, effects, webtui, tokens, recipes, utilities) governs where these rules land.
import "./styles/webtui.css";

// Determinism flag: `?effects=off` or the calm-cockpit toggle freezes CRT
// animation so screenshots and visual assertions are stable.
const params = new URLSearchParams(window.location.search);
if (params.get("effects") === "off" || window.localStorage.getItem("calm-cockpit") === "1") {
  document.documentElement.dataset.effects = "off";
}

// Performance-timeline janitor: React 19 DEV builds emit a
// performance.measure per component render (~200/s in this cockpit) and the timeline never
// evicts them — a days-old tab reached 2.3 GB on 1.08M PerformanceMeasure entries. Trimming
// is safe for profiling: DevTools Performance recordings capture live dispatch, not this
// buffer, so only retrospective console queries see the window. The DEV guard makes this
// dead code in production builds — which also never emit the measures (production React
// drops the instrumentation).
const MEASURE_TRIM_INTERVAL_MS = 60_000;
if (import.meta.env.DEV) {
  window.setInterval(() => {
    performance.clearMeasures();
    performance.clearMarks();
  }, MEASURE_TRIM_INTERVAL_MS);
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root element");

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <App />
    </MotionConfig>
  </StrictMode>,
);
