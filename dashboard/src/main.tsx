import { MotionConfig } from "motion/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";
import "./styles/tokens.css";
// The scoped WebTUI skin (260715-FEUI-L1 S1): after index.css so the layer-order statement there
// (reset, base, effects, webtui, tokens, recipes, utilities) governs where these rules land.
import "./styles/webtui.css";

// Determinism flag (D5, note 15): `?effects=off` or the calm-cockpit toggle freezes CRT
// animation so screenshots and visual assertions are stable.
const params = new URLSearchParams(window.location.search);
if (params.get("effects") === "off" || window.localStorage.getItem("calm-cockpit") === "1") {
  document.documentElement.dataset.effects = "off";
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
