import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";

import { TerminalSocketContext } from "../data/terminal";
import {
  installCockpitScenarioFetch,
  resetCockpitScenario,
  type CockpitScenarioDefinition,
} from "./cockpitScenarios";
import { createMockTerminalSocketFactory } from "./mockTerminalSocket";

const EMPTY_COCKPIT_STATE: CockpitScenarioDefinition = {
  name: "non-cockpit-scenario",
  label: "Non-cockpit scenario",
  caption: "Clear transient Chats-cockpit state before mounting the ordinary gallery shell",
  kind: "launch-happy",
  rows: [],
  socket: "live",
};

/** Dev-only authority boundary: install transport before descendant passive effects start polling. */
export function CockpitScenarioHarness({
  scenario,
  children,
}: {
  scenario: CockpitScenarioDefinition;
  children: ReactNode;
}) {
  const [ready, setReady] = useState(false);

  // Install the replacement authority in the layout phase, before any reset can wake a client.
  useLayoutEffect(() => {
    return installCockpitScenarioFetch(scenario);
  }, [scenario]);

  // The keyed prior shell's passive cleanups run before this passive mount effect. Only then is it
  // safe to clear registries/hydrate and mount subscribers for the new scenario.
  useEffect(() => {
    resetCockpitScenario(scenario);
    setReady(true);
  }, [scenario]);

  if (!ready) return null;

  return (
    <TerminalSocketContext.Provider
      value={createMockTerminalSocketFactory({
        dropAfterOpen: scenario.socket === "dropped",
        emitBanner: false,
      })}
    >
      {children}
    </TerminalSocketContext.Provider>
  );
}

/**
 * Selector target for legacy Engine Room/resting scenarios. It performs the same passive,
 * post-descendant-unmount reset as a cockpit target, but deliberately installs no fake HTTP
 * authority. Without this branch, cockpit state survives when the selector leaves Chats.
 */
export function CockpitScenarioExitBoundary({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    resetCockpitScenario(EMPTY_COCKPIT_STATE);
    setReady(true);
  }, []);

  return ready ? children : null;
}
