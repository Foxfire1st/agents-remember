import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles/tokens.css";

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
    <App />
  </StrictMode>,
);
