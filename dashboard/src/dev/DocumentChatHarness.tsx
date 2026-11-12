// DEV-ONLY harness for the MIK-R121/R122 browser tests (`/dev/document-chat-host`). It renders
// the real Operations right rail (`RightRail` from Cockpit: the rail toggle plus EventRiver /
// `DocumentChat`) against stubbed role-launch answers and a doc switcher, so a spec can walk
// the rail toggle and the document selection and watch the single shared frame follow.
// Never shipped: `App.tsx` loads `DevApp` only under `import.meta.env.DEV`.
import { useState } from 'react';

import type { TaskDocumentRef } from '../types/projection';
import { RightRail } from '../cockpit/Cockpit';
import { chatLeaf, chatMaster, chatSprint } from '../test/fixtures/documentChat';
import type { DocumentAgent } from '../cockpit/document-chat/model';

const FRAME = (extra: Record<string, unknown> = {}) => ({
  available: true,
  frameBaseUrl: window.location.origin,
  serverId: 'srv_test',
  projectsWorkspaceId: 'wks_projects',
  projectsWorkspaceDetail: null,
  ...extra,
});

const architect = (
  agentId: string,
  workspaceId: string,
  createdAt: string,
  labels: Record<string, string>,
): DocumentAgent => ({
  agentId,
  workspaceId,
  archivedAt: null,
  createdAt,
  labels,
});

const AGENTS: Record<string, DocumentAgent[]> = {
  architect: [
    architect('agent-architect', 'wks_projects', '2026-01-01', { 'ar.role': 'architect' }),
  ],
  manager: [
    architect('agent-manager', 'wks_projects', '2026-01-02', {
      'ar.role': 'manager',
      'ar.master-ref': 'repo/master/task.json',
    }),
  ],
  worker: [
    architect('agent-worker', 'wks_leaf', '2026-01-04', {
      'ar.role': 'worker',
      'ar.task-ref': 'repo/master/leaf.json',
    }),
    architect('agent-curator', 'wks_leaf', '2026-01-03', {
      'ar.role': 'curator',
      'ar.task-ref': 'repo/master/leaf.json',
    }),
  ],
};

function installFetchStub(): void {
  if ((window.fetch as unknown as { __arHarnessStub?: boolean }).__arHarnessStub) return;
  const native = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
    );
    if (url.includes('/api/role-launch/frame')) return new Response(JSON.stringify(FRAME()));
    if (url.includes('/api/role-launch/document-chats')) {
      const body = init?.body ? (JSON.parse(String(init.body)) as { role?: string }) : {};
      const agents = AGENTS[body.role ?? 'architect'] ?? [];
      return new Response(JSON.stringify({ agents }));
    }
    return native(input, init);
  }) as typeof window.fetch;
  (window.fetch as unknown as { __arHarnessStub?: boolean }).__arHarnessStub = true;
}

const DOCS: Array<{ key: string; label: string; ref: TaskDocumentRef | undefined }> = [
  { key: 'projects', label: 'Projects', ref: undefined },
  { key: 'master', label: 'Master', ref: { repository: 'repo', path: 'master/task.json' } },
  { key: 'leaf', label: 'Task', ref: { repository: 'repo', path: 'master/leaf.json' } },
  // A selected document with no agent anywhere: the frame hides and the launcher shows.
  { key: 'ghost', label: 'Agentless', ref: { repository: 'repo', path: 'master/ghost.json' } },
];

export default function DocumentChatHarness() {
  useState(installFetchStub);
  const [doc, setDoc] = useState<string>('projects');
  const [railView, setRailView] = useState<'river' | 'chat'>('chat');
  const ref = DOCS.find((entry) => entry.key === doc)?.ref;
  return (
    <div data-testid="doc-harness" data-selection={doc} data-rail={railView}>
      <div role="toolbar" aria-label="harness documents">
        {DOCS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            data-testid={`harness-doc-${entry.key}`}
            onClick={() => setDoc(entry.key)}
          >
            {entry.label}
          </button>
        ))}
        <button type="button" data-testid="harness-rail-river" onClick={() => setRailView('river')}>
          River
        </button>
        <button type="button" data-testid="harness-rail-chat" onClick={() => setRailView('chat')}>
          Chat
        </button>
      </div>
      <RightRail
        fullBleed={false}
        railEnter={{}}
        railView={railView}
        rightRailWidth={360}
        setRailView={setRailView}
        viewedTask={ref ? { taskDocumentRef: ref } : undefined}
        tasks={[chatSprint, chatMaster, chatLeaf]}
        onResize={() => {}}
      />
    </div>
  );
}
