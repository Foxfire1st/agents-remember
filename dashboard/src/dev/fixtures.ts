// Hand-authored gallery scenarios use the sampled native wire builders. They vary worktree facts
// and attention items without keeping a second required-field list or inventing task activity
// from session events.

import { ENGINE_ROOM_SCENARIOS, OFFICIAL_LEDGER } from '../panels/engine-room/fixtures';
import type { ObserverEvent } from '../types/event';
import type { EnclosureNode, WorkspaceProjection } from '../types/projection';
import {
  EMPTY_ANALYTICS,
  enclosure as servedEnclosure,
  observerEvent as servedObserverEvent,
  projection as servedProjection,
} from '../test/fixtures/wire';

function project(over: Partial<WorkspaceProjection> = {}): WorkspaceProjection {
  return servedProjection({
    version: 2,
    generatedAt: '2026-06-14T09:01:00+00:00',
    ...over,
  });
}

function enclosure(
  over: Partial<EnclosureNode> & Pick<EnclosureNode, 'enclosure' | 'repoName' | 'taskName'>,
): EnclosureNode {
  return servedEnclosure({
    taskId: over.enclosure,
    enclosureId: over.enclosure,
    leafId: over.taskName,
    taskRoot: over.enclosure,
    lifecycleId: '',
    worktreeGroup: over.enclosure,
    humanReviewStatus: 'pending',
    closeoutStatus: 'pending',
    integrationStatus: 'not-started',
    cleanup: 'pending',
    // Live by default: the tasks surface renders a leaf only while a worktree exists (L11).
    codeWorktreeExists: true,
    memoryWorktreeExists: true,
    actions: [],
    ...over,
  });
}

const evt = (
  id: string,
  kind: string,
  trust: ObserverEvent['trust'],
  actor: ObserverEvent['actor'],
  over: Partial<ObserverEvent> = {},
): ObserverEvent =>
  servedObserverEvent({ id, kind, trust, actor, ts: '2026-06-14T09:00:30+00:00', ...over });

export interface GalleryEntry {
  name: string;
  projection: WorkspaceProjection;
  events?: ObserverEvent[];
}

// Wrap one engine-room scenario (its processes) into a full WorkspaceProjection — the shared
// mapping behind both the GALLERY entries and the slice-5i scenario-player frames. The scenario
// type is read off the fixture export itself, so a shape change there is this file's compile
// error rather than a second declaration to keep in step.
export function engineRoomProjection(
  scenario: (typeof ENGINE_ROOM_SCENARIOS)[number],
): WorkspaceProjection {
  return project({
    analytics: {
      ...EMPTY_ANALYTICS,
      engineProcesses: scenario.processes,
      ledgers: [OFFICIAL_LEDGER], // the official coupler resolves its repo's ledger from analytics.ledgers
    },
  });
}

export const GALLERY: GalleryEntry[] = [
  { name: 'calm', projection: project() },
  { name: 'blocked', projection: project() },
  {
    name: 'alarm',
    projection: project({
      analytics: {
        ...EMPTY_ANALYTICS,
        attentionQueue: [
          {
            id: 'actionable-drift:repo-b',
            kind: 'actionable-drift',
            severity: 'alarm',
            lane: 'repo',
            title: '3 actionable drift',
            waitSeconds: 7200,
            repoId: 'repo-b',
            itemClass: 'operational',
            dismissible: true,
          },
          {
            id: 'blocked-start:wt-b',
            kind: 'blocked-start',
            severity: 'warn',
            lane: 'worktree',
            title: 'Worktree start blocked',
            detail: 'no exact ledger mapping for the selected code base commit',
            itemClass: 'operational',
            dismissible: true,
            waitSeconds: 40,
            enclosure: 'wt-b',
          },
        ],
      },
    }),
  },
  {
    name: 'full',
    events: [
      evt('e3', 'tool.completed', 'observed', 'model', { enclosure: 'build-001' }),
      evt('e5', 'correction.recorded', 'inferred', 'system', { enclosure: 'old-003' }),
    ],
    projection: project({
      enclosures: [
        enclosure({
          enclosure: 'wt-a',
          repoName: 'agents-remember',
          taskName: 'browser-dashboard',
          lifecycleId: 'build-001',
          closeoutStatus: 'completed',
          cleanup: 'pending',
          actions: [
            { action: 'integrate', enabled: true },
            { action: 'cleanup', enabled: false, disabledReason: 'integration not complete' },
          ],
        }),
        enclosure({
          enclosure: 'wt-a2',
          repoName: 'agents-remember',
          taskName: 'gate-control',
          lifecycleId: 'plan-002',
        }),
        enclosure({
          enclosure: 'wt-b',
          repoName: 'repo-b',
          taskName: 'fission-spike',
          lifecycleId: 'old-003',
          integrationStatus: 'completed',
          closeoutStatus: 'completed',
          cleanup: 'pending',
          actions: [{ action: 'cleanup', enabled: true }],
        }),
      ],
      analytics: {
        ...EMPTY_ANALYTICS,
        driftSnapshots: [
          {
            repository: 'agents-remember',
            branch: 'main',
            counts: { current: 372, drifted: 4, missingVerification: 2 },
            actionableCount: 6,
            snapshotStaleSeconds: 900,
          },
          {
            repository: 'repo-b',
            branch: 'main',
            counts: { current: 88 },
            actionableCount: 0,
            snapshotStaleSeconds: 120,
          },
        ],
        ledgers: [
          {
            repository: 'agents-remember',
            closeoutCount: 95,
            lastVerifiedCodeCommit: 'c041ff5fade1',
            baseCodeCommit: '85af25823437',
            rows: [
              { codeCommit: 'c041ff5fade1', memoryCommit: 'dd436bcae902' },
              { codeCommit: '08e9221abcd0', memoryCommit: 'd60a0511ef34' },
            ],
          },
          {
            repository: 'repo-b',
            closeoutCount: 12,
            lastVerifiedCodeCommit: 'abc1234def56',
            baseCodeCommit: 'abc1234def56',
            rows: [{ codeCommit: 'abc1234def56', memoryCommit: 'fed4321cba98' }],
          },
        ],
        attentionQueue: [
          {
            id: 'actionable-drift:agents-remember',
            kind: 'actionable-drift',
            severity: 'warn',
            lane: 'repo',
            title: '6 actionable drift',
            waitSeconds: 900,
            itemClass: 'operational',
            dismissible: true,
            repoId: 'agents-remember',
          },
        ],
      },
    }),
  },
  { name: 'empty', projection: project() },
  // The boot-step frames are hidden from the bench strip (slice 5i's scenario player plays the build-up
  // instead); the fixtures stay in ENGINE_ROOM_SCENARIOS for that reuse + the render tests.
  ...ENGINE_ROOM_SCENARIOS.filter((scenario) => !scenario.name.startsWith('engine-boot-')).map(
    (scenario) => ({
      name: scenario.name,
      projection: engineRoomProjection(scenario),
    }),
  ),
];
