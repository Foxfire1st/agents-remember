// DEV-ONLY fake Paseo host for the MIK-R121 browser test (`/dev/fake-paseo-host`). It is never
// shipped: `App.tsx` loads `DevApp` only under `import.meta.env.DEV`, where Rollup drops this
// branch from the production bundle.
//
// The page models a Paseo application with two workspaces and runs the REAL plugin client part
// (`startClientPart` → `installBridge`) against a stubbed SDK, so the dashboard side under test
// talks to genuine bridge code. Navigation follows the native truth (pinned @getpaseo/server
// 0.11.0-beta.2 `resolveNavigateToAgent`): an open names the agent alone and the application
// resolves its workspace from its own session. Display uses native-faithful markup: visible
// selected agent tabs on wide layouts, and below the desktop breakpoint the switcher trigger
// with the mounted agent chat scroll (`nativeID agent-chat-scroll-native-virtualized`).
//
//   /dev/fake-paseo-host?open=agent:<id>&arPage=document   first-visit deep link, as the frame loads it
//   /dev/fake-paseo-host?stall=1                          `openScreen` never applies: the message
//                                                         path cannot bring the frame to any agent
import { useEffect, useRef, useState } from "react";

import type { BridgeClient } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/bridge";
import { currentPage } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/page";
import { startClientPart } from "../../../mcp/src/agents_remember/package_data/paseo_plugin/client/start";

interface FakeAgent {
  id: string;
  workspaceId: string;
}

const WORKSPACES = ["wks_projects", "wks_leaf"];
const AGENTS: FakeAgent[] = [
  { id: "agent-architect", workspaceId: "wks_projects" },
  { id: "agent-manager", workspaceId: "wks_projects" },
  { id: "agent-worker", workspaceId: "wks_leaf" },
  { id: "agent-curator", workspaceId: "wks_leaf" },
];

interface OpenEntry {
  params: Record<string, string>;
  result: string;
}

function directory<T>(entries: T[]): { entries: T[]; pageInfo: { hasMore: boolean; nextCursor: string | null } } & {
  subscription: {
    subscribe(observer: { snapshot(value: { entries: T[]; pageInfo: { hasMore: boolean; nextCursor: string | null } }): void }): () => void;
    release(): Promise<void>;
  };
} {
  const value = { entries, pageInfo: { hasMore: false, nextCursor: null as string | null } };
  return {
    ...value,
    subscription: {
      subscribe(observer) {
        observer.snapshot(value);
        return () => {};
      },
      release: async () => {},
    },
  };
}

interface FakeHooks {
  stalled: () => boolean;
  workspaceOf: () => string;
  setWorkspace: (next: string) => void;
  setSelected: (next: string | null) => void;
  appendOpen: (entry: OpenEntry) => void;
}

function createFakeClient(hooks: FakeHooks): BridgeClient {
  return {
    openScreen: (input) => {
      const params = { ...(input.params ?? {}) } as Record<string, string>;
      delete params.at;
      // The application answers asynchronously; the selection watcher sees the tab switch.
      window.setTimeout(() => {
        let result: string;
        if (hooks.stalled()) {
          result = "stalled-by-mode";
        } else if (params.workspaceId && !params.agentId && !WORKSPACES.includes(params.workspaceId)) {
          result = "stalled-unknown-workspace";
        } else {
          // Native navigation truth (pinned @getpaseo/server 0.11.0-beta.2
          // resolveNavigateToAgent): the application resolves the agent's workspace from its
          // own session and navigates there with the agent target. The fake models that, not
          // a workspace-scoped open that can only succeed where the frame stands.
          if (params.agentId) {
            const agent = AGENTS.find((known) => known.id === params.agentId);
            if (!agent) result = "stalled-unknown-agent";
            else {
              hooks.setWorkspace(agent.workspaceId);
              hooks.setSelected(agent.id);
              result = "shown";
            }
          } else if (params.workspaceId) {
            hooks.setWorkspace(params.workspaceId);
            hooks.setSelected(null);
            result = "shown";
          } else {
            result = "stalled-unknown-agent";
          }
        }
        hooks.appendOpen({ params, result });
      }, 0);
    },
    paseo: {
      projects: {
        list: async () => ({ projects: [] }),
        subscribe: () => () => {},
      },
      agents: {
        list: async () => directory(AGENTS.map((agent) => ({ agent: { id: agent.id, provider: "fake", status: "idle" as const, pendingPermissions: [] as unknown[], workspaceId: agent.workspaceId } }))),
        ref: (agentId: string) => {
          const agent = AGENTS.find((known) => known.id === agentId);
          const handle = {
            archivedAt: null as unknown,
            workspaceId: null as string | null,
            refresh: async () => {
              if (!agent) throw new Error(`Agent not found: ${agentId}`);
              handle.archivedAt = null;
              handle.workspaceId = agent.workspaceId;
              return { agent };
            },
          };
          return handle;
        },
      },
      workspaces: {
        list: async () => directory(WORKSPACES.map((id) => ({ id, projectId: "p0", name: id }))),
        ref: (workspaceId: string) => ({
          refresh: async () => (WORKSPACES.includes(workspaceId) ? { id: workspaceId } : null),
        }),
      },
    },
    addComposerPill: () => ({ update() {}, remove() {} }),
  };
}

function initialAgent(): string | null {
  const open = new URLSearchParams(window.location.search).get("open");
  const agent = open?.startsWith("agent:") ? open.slice("agent:".length) : null;
  // A first-visit deep link lands on the agent when it lives in the booted workspace.
  return agent && AGENTS.some((known) => known.id === agent && known.workspaceId === "wks_projects") ? agent : null;
}

export default function FakePaseoHost() {
  const [workspace, setWorkspaceState] = useState<string>("wks_projects");
  const [selected, setSelectedState] = useState<string | null>(initialAgent);
  const [log, setLog] = useState<OpenEntry[]>([]);
  const workspaceRef = useRef(workspace);
  const stallRef = useRef(new URLSearchParams(window.location.search).get("stall") === "1");
  // Below the desktop breakpoint the native tab strip is replaced by the switcher trigger and
  // the mounted agent chat: no agent tabs exist to select.
  const compact = new URLSearchParams(window.location.search).get("compact") === "1";
  const setWorkspace = (next: string) => {
    workspaceRef.current = next;
    setWorkspaceState(next);
  };

  useEffect(() => {
    const page = currentPage();
    if (!page) return () => {};
    const hooks = {
      stalled: () => stallRef.current,
      workspaceOf: () => workspaceRef.current,
      setWorkspace,
      setSelected: setSelectedState,
      appendOpen: (entry: OpenEntry) => setLog((entries) => [...entries, entry]),
    };
    const stop = startClientPart(page, createFakeClient(hooks), async () => [
      { dashboardOrigin: window.location.origin, frameBaseUrl: window.location.origin },
    ]);
    return stop;
  }, []);

  return (
    <div data-testid="fake-paseo-host" data-workspace={workspace} data-agent={selected ?? "none"}>
      <div data-testid="fake-host-workspace">{workspace}</div>
      <div data-testid="fake-host-agent">{selected ?? "opening…"}</div>
      <div data-testid="fake-open-log">{JSON.stringify(log)}</div>
      <div role="tablist" aria-label="fake native chats">
        {compact ? (
          <>
            <button type="button" data-testid="workspace-tab-switcher-trigger" aria-label="tabs">
              {selected ?? "tabs"}
            </button>
            {selected ? <div id="agent-chat-scroll-native-virtualized">{selected}</div> : <div>opening…</div>}
          </>
        ) : (
          AGENTS.filter((agent) => agent.workspaceId === workspace).map((agent) => (
            <button
              key={agent.id}
              type="button"
              role="tab"
              data-testid={`workspace-tab-agent_${agent.id}`}
              aria-selected={selected === agent.id}
            >
              {agent.id}
            </button>
          ))
        )}
      </div>
    </div>
  );
}
