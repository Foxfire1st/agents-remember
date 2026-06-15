import { Bench } from "./Bench";
import { Reference } from "./Reference";

import "./dev.css";

// The dev harness router (DEV-only, lazy-loaded from App so it is dead-code-eliminated in the
// production bundle). `/dev/bench` = the component gallery; `/dev/reference` = the mc2 mount.
export default function DevApp() {
  const path = window.location.pathname;
  if (path.startsWith("/dev/reference")) return <Reference />;
  if (path.startsWith("/dev/bench")) return <Bench />;
  return (
    <div className="cockpit">
      <header className="cockpit__bar">
        <h1>DEV HARNESS</h1>
      </header>
      <ul className="raw-list">
        <li>
          <a href="/dev/bench">/dev/bench</a> — component gallery (every panel × grammar state)
        </li>
        <li>
          <a href="/dev/reference">/dev/reference</a> — mc2 design endpoint, read-only
        </li>
      </ul>
    </div>
  );
}
