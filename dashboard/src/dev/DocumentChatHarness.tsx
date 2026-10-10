// DEV-ONLY harness for the MIK-R121 browser test (`/dev/document-chat-host`). It renders the real
// `DocumentChat` (document page: Operations right rail) against stubbed role-launch answers and a
// doc switcher, so the spec can select the master and the task and watch the shared frame follow.
// Never shipped: `App.tsx` loads `DevApp` only under `import.meta.env.DEV`.
import { useState } from "react";

import type { TaskDocumentRef } from "../types/terminalCatalog";
import { DocumentChat } from "../cockpit/document-chat/DocumentChat";
import { chatLeaf, chatMaster, chatSeries, chatSprint } from "../test/fixtures/documentChat";
import type { DocumentAgent } from "../cockpit/document-chat/model";

const FRAME = (extra: Record<string, unknown> = {}) => ({
  available: true,
  frameBaseUrl: window.location.origin,
  serverId: "srv_test",
  projectsWorkspaceId: "wks_projects",
  projectsWorkspaceDetail: null,
  ...extra,
});

const architect = (agentId: string, workspaceId: string, createdAt: string, labels: Record<string, string>): DocumentAgent => ({
  agentId, workspaceId, archivedAt: null, createdAt, labels,
});

const AGENTS: Record<string, DocumentAgent[]> = {
  architect: [architect("agent-architect", "wks_projects", "2026-01-01", { "ar.role": "architect" })],
  manager: [architect("agent-manager", "wks_projects", "2026-01-02", { "ar.role": "manager", "ar.master-ref": "repo/master/task.json" })],
  worker: [
    architect("agent-worker", "wks_leaf", "2026-01-04", { "ar.role": "worker", "ar.task-ref": "repo/master/leaf.json" }),
    architect("agent-curator", "wks_leaf", "2026-01-03", { "ar.role": "curator", "ar.task-ref": "repo/master/leaf.json" }),
  ],
};

function installFetchStub(): void {
  if ((window.fetch as unknown as { __arHarnessStub?: boolean }).__arHarnessStub) return;
  const native = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    if (url.includes("/api/role-launch/frame")) return new Response(JSON.stringify(FRAME()));
    if (url.includes("/api/role-launch/document-chats")) {
      const body = init?.body ? (JSON.parse(String(init.body)) as { role?: string }) : {};
      const agents = AGENTS[body.role ?? "architect"] ?? [];
      return new Response(JSON.stringify({ agents }));
    }
    return native(input, init);
  }) as typeof window.fetch;
  (window.fetch as unknown as { __arHarnessStub?: boolean }).__arHarnessStub = true;
}

const DOCS: Array<{ key: string; label: string; ref: TaskDocumentRef | undefined }> = [
  { key: "projects", label: "Projects", ref: undefined },
  { key: "master", label: "Master", ref: { repository: "repo", path: "master/task.json" } },
  { key: "leaf", label: "Task", ref: { repository: "repo", path: "master/leaf.json" } },
];

export default function DocumentChatHarness() {
  useState(installFetchStub);
  const [doc, setDoc] = useState<string>("projects");
  const ref = DOCS.find((entry) => entry.key === doc)?.ref;
  return (
    <div data-testid="doc-harness" data-selection={doc}>
      <div role="toolbar" aria-label="harness documents">
        {DOCS.map((entry) => (
          <button key={entry.key} type="button" data-testid={`harness-doc-${entry.key}`} onClick={() => setDoc(entry.key)}>
            {entry.label}
          </button>
        ))}
      </div>
      <DocumentChat active taskDocumentRef={ref} taskDocuments={[chatSprint, chatMaster, chatLeaf]} series={chatSeries} />
    </div>
  );
}
