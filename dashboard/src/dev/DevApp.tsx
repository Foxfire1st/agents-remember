import { FlowTab } from "../panels/FlowTab";
import { Bench } from "./Bench";
import { SprintGraphPage } from "./sprintGraphPage";
import { PtyRenderBench } from "./PtyRenderBench";
import { Reference } from "./Reference";

import "./dev.css";

// The dev harness router (DEV-only, lazy-loaded from App so it is dead-code-eliminated in the
// production bundle). `/dev/bench` = the component gallery; `/dev/reference` = the mc2 mount;
// `/dev/flows` = the lifecycle-design canvas (orchestration L0; all models in `panels/flowModels.ts`)
// — kept out of the cockpit mode bar per task 29, reachable here for design sittings.
export default function DevApp() {
  const path = window.location.pathname;
  if (path.startsWith("/dev/reference")) return <Reference />;
  if (path.startsWith("/dev/pty-bench")) return <PtyRenderBench />;
  if (path.startsWith("/dev/sprint-graph")) return <SprintGraphPage />;
  if (path.startsWith("/dev/bench")) return <Bench />;
  if (path.startsWith("/dev/flows")) {
    return (
      <div className="cockpit">
        <FlowTab initialModel={new URLSearchParams(window.location.search).get("model") ?? undefined} />
      </div>
    );
  }
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
        <li>
          <a href="/dev/flows">/dev/flows</a> — lifecycle flow canvas (router · designer · orchestrator · manager · worker · reviewer · comms)
        </li>
        <li>
          <a href="/dev/sprint-graph">/dev/sprint-graph</a> — sprint execution graph page (L12 mounted-UI evidence)
        </li>
      </ul>
    </div>
  );
}
