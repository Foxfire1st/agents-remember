import { lazy, Suspense } from "react";

import { Cockpit } from "./cockpit/Cockpit";

// Hand-rolled routing (D6): production serves only `/`. The dev harness (`/dev/*`) lazy import
// lives inside the `import.meta.env.DEV` ternary, so in a production build it is a statically
// dead branch — Rollup drops the chunk and mc2 + the gallery never ship.
const DevApp = import.meta.env.DEV ? lazy(() => import("./dev/DevApp")) : null;

export default function App() {
  if (DevApp && window.location.pathname.startsWith("/dev/")) {
    return (
      <Suspense fallback={null}>
        <DevApp />
      </Suspense>
    );
  }
  return <Cockpit />;
}
