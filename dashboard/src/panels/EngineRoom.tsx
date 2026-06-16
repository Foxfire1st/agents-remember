import { type ReactNode, useState } from "react";

import { css } from "../../styled-system/css";
import { type EngineStack, engineState as engineRuntime } from "../data/selectors";
import { useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { ProviderNode } from "../types/projection";
import { BootTimeline } from "./engine-room/BootTimeline";
import { buildEngineRoomModel } from "./engine-room/buildEngineRoomModel";
import { DiagnosticsPanel } from "./engine-room/DiagnosticsPanel";
import { EnclosureProcessMap } from "./engine-room/EnclosureProcessMap";
import { EnclosureStackList } from "./engine-room/EnclosureStackList";
import {
  detailColumn,
  emptyState,
  engineSilhouette,
  officialStrip,
  roomLayout,
  sectionLabel,
} from "./engine-room/engineRoomStyles";

const sizing = css({ flex: "1" });
const engineChip = css({ display: "flex", alignItems: "center", gap: "0.35rem" });
const engineChipLabel = css({ color: "muted", fontSize: "0.7rem" });
const fallbackWrap = css({ display: "grid", gap: "0.6rem" });
const fallbackStackBox = css({
  display: "grid",
  gap: "0.4rem",
  padding: "0.5rem 0.6rem",
  border: "1px solid token(colors.grid)",
  borderRadius: "3px",
  borderLeftWidth: "3px",
  borderLeftColor: "cyan",
});
const fallbackEngines = css({ display: "flex", gap: "0.7rem", flexWrap: "wrap" });

function engineLabel(provider: ProviderNode): string {
  return provider.role === "memory" ? "GrepAI" : "CGC";
}

function OfficialStrip({ engines }: { engines: ProviderNode[] }) {
  return (
    <div className={officialStrip} data-testid="official-strip">
      <span className={sectionLabel}>Official line · workspace</span>
      {engines.map((engine) => (
        <span key={engine.id} className={engineChip}>
          <span
            className={engineSilhouette({ runtimeState: engineRuntime(engine) })}
            role="img"
            aria-label={`${engineLabel(engine)} engine ${engineRuntime(engine)}`}
          />
          <span className={engineChipLabel}>
            {engineLabel(engine)} · {engineRuntime(engine)}
          </span>
        </span>
      ))}
    </div>
  );
}

function FallbackStacks({ stacks }: { stacks: EngineStack[] }) {
  return (
    <div className={fallbackWrap} data-testid="engine-room-fallback">
      <span className={sectionLabel}>Provider stacks (no enclosure process surface)</span>
      {stacks.map((stack) => (
        <div key={stack.key} className={fallbackStackBox} data-testid="engine-stack">
          <span className={css({ color: "ink", fontSize: "0.78rem" })}>{stack.key}</span>
          <div className={fallbackEngines}>
            {stack.engines.map((engine) => (
              <span key={engine.id} className={engineChip} data-testid="engine-unit">
                <span
                  className={engineSilhouette({ runtimeState: engineRuntime(engine) })}
                  role="img"
                  aria-label={`${engineLabel(engine)} engine ${engineRuntime(engine)}`}
                />
                <span className={engineChipLabel}>{engineLabel(engine)}</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EngineRoom() {
  const analytics = useDashboard((state) => state.analytics);
  const providers = useDashboard((state) => state.providers);
  const lifecycles = useDashboard((state) => state.lifecycles);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);

  const model = buildEngineRoomModel(
    analytics?.engineProcesses ?? [],
    Object.values(providers),
    Object.values(lifecycles),
  );
  const selected =
    model.processes.find((view) => view.node.worktreeGroup === selectedGroup) ??
    model.processes[0];

  let body: ReactNode;
  if (model.usesFallback) {
    body = <FallbackStacks stacks={model.fallbackStacks} />;
  } else if (!selected) {
    body = (
      <p className={emptyState} data-testid="engine-room-empty">
        No worktree enclosures are active.
      </p>
    );
  } else {
    body = (
      <div className={roomLayout}>
        <EnclosureStackList
          views={model.processes}
          selectedKey={selected.enclosureKey}
          onSelect={setSelectedGroup}
        />
        <div className={detailColumn}>
          <EnclosureProcessMap node={selected.node} />
          <BootTimeline node={selected.node} />
          <DiagnosticsPanel node={selected.node} />
        </div>
      </div>
    );
  }

  return (
    <Panel
      testid="engine-room"
      title={`Engine room · ${model.processes.length} ${model.processes.length === 1 ? "enclosure" : "enclosures"}`}
      className={sizing}
    >
      {model.workspaceEngines.length > 0 ? <OfficialStrip engines={model.workspaceEngines} /> : null}
      {body}
    </Panel>
  );
}
