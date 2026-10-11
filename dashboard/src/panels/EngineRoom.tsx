import { motion } from "motion/react";
import { memo, type ReactNode, useMemo, useRef, useState } from "react";
import { useStoreWithEqualityFn } from "zustand/traditional";

import { css } from "../../styled-system/css";
import {
  selectQueue,
} from "../data/selectors";
import { stableEquals } from "../data/servedAges";
import { dashboardStore, useDashboard } from "../data/store";
import { Panel } from "../grammar/Panel";
import type { EngineProcessNode, LedgerNode } from "../types/projection";
import { BootTimeline } from "./engine-room/BootTimeline";
import { buildEngineRoomModel } from "./engine-room/buildEngineRoomModel";
import { DiagnosticsPanel } from "./engine-room/DiagnosticsPanel";
import { EnclosureProcessMap } from "./engine-room/EnclosureProcessMap";
import { EnclosureStackList } from "./engine-room/EnclosureStackList";
import { useElementVisible } from "./engine-room/useElementVisible";
import { useShouldAnimate } from "./engine-room/useShouldAnimate";
import {
  emptyState,
  healthDot,
  phaseChip,
  roomCaution,
  roomGrid,
  roomHeader,
  roomHeaderMeta,
  roomHeaderName,
  roomHeaderNext,
  roomHeaderSpacer,
  roomShell,
  roomStage,
  roomZone,
} from "./engine-room/styles";

const sizing = css({ flex: "1" });

// The human-gated lifecycle phases (T12–T18): a landing beat is in flight and waiting on a person.
// Their phase chip pulses (gated) so the room reads as a machine being synced / landed / retired.
const LIFECYCLE_PHASES = new Set([
  "sync-needed",
  "closeout-pending",
  "commit-approval-pending",
  "integration-pending",
  "integration-blocked",
  "cleanup-pending",
]);

// §4.2 header: the selected enclosure's identity + health + phase + next action, plus the
// master-caution mirror. Slice 5f: the phase chip pulses while a human-gated lifecycle beat
// (sync / closeout / integration / cleanup, T12–T18) is in flight — gated, instant under data-effects=off.
function EngineRoomHeader({ node }: { node: EngineProcessNode }) {
  const animate = useShouldAnimate();
  const queue = useDashboard(selectQueue);
  const sev = queue[0]?.severity ?? "clear";
  const phaseActive = LIFECYCLE_PHASES.has(node.phase);
  // Stilled while the room's cockpit layer is display:none (kept mounted): an infinite Motion
  // pulse on a hidden subtree would keep the frameloop + per-frame style writes alive for nothing.
  const headerRef = useRef<HTMLDivElement>(null);
  const visible = useElementVisible(headerRef);
  const pulse = animate && phaseActive && visible;
  const label = node.leafId || node.taskName;
  return (
    <div ref={headerRef} className={roomHeader} data-testid="engine-room-header" data-phase-active={phaseActive}>
      <span className={roomHeaderName}>{label}</span>
      <span className={roomHeaderMeta}>
        <span className={healthDot({ health: node.health })} aria-hidden="true" />
        <span>{node.health}</span>
        <motion.span
          className={phaseChip({ health: node.health })}
          animate={pulse ? { opacity: [1, 0.5, 1] } : { opacity: 1 }}
          transition={pulse ? { duration: 1.4, repeat: Infinity, ease: "easeInOut" } : { duration: 0 }}
        >
          {node.phase}
        </motion.span>
        {node.leafId ? <span>{node.taskName}</span> : null}
        <span>{node.repoName}</span>
      </span>
      {node.nextAction ? <span className={roomHeaderNext}>→ {node.nextAction}</span> : null}
      <span className={roomHeaderSpacer} />
      <span className={roomCaution({ sev })} data-testid="engine-room-caution">
        ⚠ {queue.length} waiting
      </span>
    </div>
  );
}

// Stable empty references so the analytics slice selectors never hand back a fresh `[]` (a new
// identity per call would churn the zustand snapshot — the Cockpit shell uses the same pattern).
const EMPTY_ENGINE_PROCESSES: EngineProcessNode[] = [];
const EMPTY_LEDGERS: LedgerNode[] = [];

function EngineRoomImpl() {
  // Slice selectors, NOT the whole analytics object: analytics deltas are wholesale
  // replacements, so the old whole-object subscription re-rendered the room (~580 DOM nodes) on ANY
  // analytics change. stableEquals — the change gate's own equality (data/servedAges) — keeps the
  // previous slice's identity when a delta left it untouched: no re-render, no model rebuild.
  const engineProcesses = useStoreWithEqualityFn(
    dashboardStore,
    (state) => state.analytics?.engineProcesses ?? EMPTY_ENGINE_PROCESSES,
    stableEquals,
  );
  const ledgers = useStoreWithEqualityFn(
    dashboardStore,
    (state) => state.analytics?.ledgers ?? EMPTY_LEDGERS,
    stableEquals,
  );
  const lifecycles = useDashboard((state) => state.lifecycles);
  // store generation — bumped by reset() (dev-bench scenario switch). Keying the canvas by it forces a clean
  // remount across a switch so a previous mode's exiting overlay can't orphan; constant (0) in production.
  const gen = useDashboard((state) => state.gen);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);

  // The model is a pure join of the two slices — rebuild only when one of them actually changes,
  // not on every render. Keyed on the identity-stable records/slices (mergeKeyed preserves the
  // records; the stableEquals selectors above preserve the slices); Object.values allocates inside.
  const model = useMemo(
    () =>
      buildEngineRoomModel(engineProcesses, Object.values(lifecycles)),
    [engineProcesses, lifecycles],
  );
  const selected =
    model.processes.find((view) => view.node.worktreeGroup === selectedGroup) ??
    model.processes[0];
  // the OFFICIAL coupler popover reads the repo's main ledger (5h); the worktree coupler reads node.ledgerRows
  const officialLedger = selected
    ? ledgers.find((ledger) => ledger.repository === selected.node.repoName)
    : undefined;

  let body: ReactNode;
  if (!selected) {
    body = (
      <p className={emptyState} data-testid="engine-room-empty">
        No worktree enclosures are active.
      </p>
    );
  } else {
    // §4.2 full-width 3-zone room: header over [stack list | pod stage | boot + diagnostics].
    body = (
      <div className={roomShell}>
        <EngineRoomHeader node={selected.node} />
        <div className={roomGrid}>
          <EnclosureStackList
            views={model.processes}
            selectedKey={selected.enclosureKey}
            onSelect={setSelectedGroup}
          />
          <div className={roomStage} data-testid="pod-stage">
            <EnclosureProcessMap
              key={gen}
              node={selected.node}
              gateNode={selected.gate}
              officialLedger={officialLedger}
            />
          </div>
          <div className={roomZone} data-testid="engine-room-diagnostics">
            <BootTimeline node={selected.node} />
            <DiagnosticsPanel
              node={selected.node}
              lifecycleId={selected.lifecycle?.id}
              gateNode={selected.gate}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <Panel
      testid="engine-room"
      title={`Engine room · ${model.processes.length} ${model.processes.length === 1 ? "enclosure" : "enclosures"}`}
      className={sizing}
      fill
    >
      {body}
    </Panel>
  );
}

// Memoized (tab-switch CPU): the room is a keep-alive cockpit layer — the shell re-renders
// on every view switch with unchanged (here: no) props, and the memo gate skips this whole subtree
// then; the room's own slice selectors still drive its updates.
export const EngineRoom = memo(EngineRoomImpl);
