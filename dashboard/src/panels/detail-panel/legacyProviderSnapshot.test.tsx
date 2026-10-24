// A payload from a server older than the provider removal (MIK-R199 rules 1 and 2, evidence 1).
//
// The served projection no longer carries `providers[]`, `analytics.setupProgress` or
// `analytics.setupSummaries`, and it no longer publishes the `provider-down` / `failed-setup`
// attention kinds. A tab can still be handed a payload that carries all of them — a stale server,
// a replayed snapshot — so the claim under test is not "the server stopped sending them" but
// "nothing on this side reads them": the store keeps no provider map, the Tasks view renders the
// same HTML with or without the legacy records, and no retired item reaches the bell.
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { dashboardStore } from '../../data/store';
import type {
  Analytics,
  AttentionItem,
  TaskEntry,
  WorkspaceProjection,
} from '../../types/projection';
import { analytics, attentionItem, enclosure, projection, taskEntry, taskSummary } from '../../test/fixtures/wire';
import { AttentionQueue } from '../AttentionQueue';
import { DetailPanel } from './DetailPanel';

// ── the superseded shapes, declared so the payload needs no assertion ─────────
// Each is the field set the server used to send. They are declared here rather than read from
// `types/projection.ts` because that mirror no longer has them — which is the whole point: the
// payload below is a shape the CURRENT contract cannot express, and it is fed to the store as a
// value, not cast into one.
interface LegacyProviderNode {
  id: string;
  indexingState: string;
  ok: boolean;
  role: string;
  scope: string;
  state: string;
  watcherUp: boolean;
  worktreeGroup?: string;
}
interface LegacySetupProgress {
  completedCount: number;
  currentPhase: string;
  failedPhases: string[];
  group: string;
  heartbeatAgeSeconds: number;
  state: string;
}
interface LegacySetupSummary {
  action: string;
  generatedAt: string;
  ok: boolean;
  ready: boolean;
  resultCounts: Record<string, number>;
  snapshotStaleSeconds: number;
  state: string;
}
type LegacyAnalytics = Analytics & {
  setupProgress: LegacySetupProgress[];
  setupSummaries: LegacySetupSummary[];
};
type LegacyProjection = Omit<WorkspaceProjection, 'analytics'> & {
  analytics: LegacyAnalytics;
  providers: LegacyProviderNode[];
};

const LEGACY_PROVIDERS: LegacyProviderNode[] = [
  {
    id: 'codegraphcontext-code',
    indexingState: 'indexed',
    ok: true,
    role: 'code',
    scope: 'workspace',
    state: 'ready',
    watcherUp: true,
  },
  {
    id: 'grepai-memory',
    indexingState: 'indexed',
    ok: true,
    role: 'memory',
    scope: 'worktree',
    state: 'ready',
    watcherUp: true,
    worktreeGroup: 'legacy-ar',
  },
];

const LEGACY_SETUP_PROGRESS: LegacySetupProgress[] = [
  {
    completedCount: 4,
    currentPhase: 'grepai-clone',
    failedPhases: ['cgc-seed'],
    group: 'legacy-ar',
    heartbeatAgeSeconds: 9,
    state: 'failed',
  },
];

const LEGACY_SETUP_SUMMARIES: LegacySetupSummary[] = [
  {
    action: 'provider_setup',
    generatedAt: '2026-06-14T08:58:00+00:00',
    ok: false,
    ready: false,
    resultCounts: { configured: 1, failed: 1 },
    snapshotStaleSeconds: 180,
    state: 'failed',
  },
];

// The two attention kinds the server retired with the providers.
const RETIRED_ATTENTION: AttentionItem[] = [
  attentionItem({
    id: 'provider-down:codegraphcontext-code',
    kind: 'provider-down',
    severity: 'alarm',
    lane: 'repo',
    title: 'Code index is down',
    detail: 'stopped',
  }),
  attentionItem({
    id: 'failed-setup:legacy-ar',
    kind: 'failed-setup',
    severity: 'alarm',
    lane: 'worktree',
    title: 'Provider setup needs attention',
    detail: 'cgc index',
  }),
];

const OWNER = enclosure({
  enclosure: '/tasks/legacy/enclosures/legacy/series-contract.md',
  enclosureId: '/tasks/legacy/enclosures/legacy/series-contract.md',
  repoName: 'agents-remember',
  taskName: 'legacy',
  worktreeGroup: '/worktrees/agents-remember/legacy-ar',
  cleanup: 'pending',
  codeWorktreeExists: true,
  memoryWorktreeExists: true,
});

const LEAF: TaskEntry = taskEntry({
  reference: { repository: 'agents-remember', path: 'tasks/legacy/01_leaf.json' },
  id: 'LEGACY-1',
  title: 'Legacy leaf',
  stage: 'started',
  activity: 'paused',
  worktrees: [OWNER],
  summary: taskSummary({ type: '', objective: 'The leaf objective' }),
});

afterEach(() => {
  cleanup();
  dashboardStore.getState().reset();
});

// The one place the legacy payload is applied. It is handed over as a VALUE — the extra keys are
// declared on `LegacyProjection` and never cast in — and it is exactly what a stale server's
// snapshot looks like arriving at the store's one entry point.
function applyLegacy(): void {
  dashboardStore.getState().applySnapshot(legacy());
}

function clean(): WorkspaceProjection {
  return projection({
    tasks: [LEAF],
    enclosures: [OWNER],
    activeWorktreeGroups: ['legacy-ar'],
  });
}

function legacy(): LegacyProjection {
  const base = projection({
    tasks: [LEAF],
    enclosures: [OWNER],
    activeWorktreeGroups: ['legacy-ar'],
    analytics: analytics({ attentionQueue: RETIRED_ATTENTION }),
  });
  return {
    ...base,
    providers: LEGACY_PROVIDERS,
    analytics: {
      ...base.analytics,
      setupProgress: LEGACY_SETUP_PROGRESS,
      setupSummaries: LEGACY_SETUP_SUMMARIES,
    },
  };
}

function spineHtml(): string {
  const view = render(<DetailPanel selectedId={`taskdoc:${LEAF.key}`} />);
  const html = view.getByTestId('worktree-spine').innerHTML;
  const text = view.container.textContent ?? '';
  view.unmount();
  for (const word of ['CGC', 'GrepAI', 'codegraphcontext', 'grepai', 'no isolated engine']) {
    expect(text, word).not.toContain(word);
  }
  return html;
}

describe('a snapshot from before the provider removal', () => {
  it('leaves the store with no provider map and no setup buckets', () => {
    applyLegacy();
    const state = dashboardStore.getState();
    expect('providers' in state).toBe(false);
    // The analytics object rides through whole (it is a wire object the server owns), so the
    // legacy keys are still ON it — what must be true is that nothing derived from them is kept.
    expect(state.activeWorktreeGroups).toEqual(['legacy-ar']);
    expect(Object.values(state.enclosures)).toHaveLength(1);
  });

  it('renders the same worktree spine as a snapshot without the legacy records', () => {
    applyLegacy();
    const fromLegacy = spineHtml();
    dashboardStore.getState().reset();
    dashboardStore.getState().applySnapshot(clean());
    const fromClean = spineHtml();
    expect(fromLegacy).toBe(fromClean);
    // and the spine still says what the enclosure says
    expect(fromClean).toContain('/worktrees/agents-remember/legacy-ar');
    expect(fromClean).toContain('agents-remember');
    expect(fromClean).toContain('ar-agents-remember');
    expect(fromClean).toContain('worktree present · pending cleanup');
  });

  it('publishes no alert for the retired attention kinds', () => {
    applyLegacy();
    const view = render(<AttentionQueue />);
    const bell = view.getByTestId('attention-bell');
    expect(bell.getAttribute('aria-label')).toBe('Attention: nothing waiting');
    expect(bell.textContent).toBe('');
    view.unmount();
  });
});
