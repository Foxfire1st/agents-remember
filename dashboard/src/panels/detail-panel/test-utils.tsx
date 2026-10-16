import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

import { dashboardStore } from '../../data/store';
import { taskReferenceKey } from '../../data/taskReference';
import type {
  TaskDocumentRef,
  TaskEntry,
  TaskSummary,
  WorkspaceProjection,
} from '../../types/projection';
import { enclosure, projection, taskEntry, taskSummary } from '../../test/fixtures/wire';
export { enclosure, taskEntry, taskSummary } from '../../test/fixtures/wire';

// Test addresses use the same qualified key as a served selection and body request.
export function taskReference(key: string): TaskDocumentRef {
  const slash = key.indexOf('/');
  if (slash < 1 || key.startsWith('/')) throw new Error(`Not a qualified task reference: ${key}`);
  return { repository: key.slice(0, slash), path: key.slice(slash + 1) };
}

export function seedProjection(over: Partial<WorkspaceProjection>) {
  dashboardStore.getState().applySnapshot(projection(over));
}

export function seedTaskEntries(tasks: TaskEntry[], over: Partial<WorkspaceProjection> = {}) {
  seedProjection({ ...over, tasks });
}

// Authored fixture entries, including the parent's served membership and counts.
export function taskTree(parent: TaskEntry, children: TaskEntry[]): TaskEntry[] {
  const counts = {
    planned: 0,
    started: 0,
    completed: 0,
    aborted: 0,
    works: 0,
    paused: 0,
    unknown: 0,
  };
  for (const child of children) {
    counts[child.stage] += 1;
    if (child.stage === 'started') counts[child.activity ?? 'unknown'] += 1;
  }
  return [
    { ...parent, children: children.map((child) => child.key), counts },
    ...children.map((child) => ({ ...child, parent: parent.key })),
  ];
}

const SERIES_MASTER_REF = taskReference('repo-a/series/task.json');
const SERIES_SLICE_REF = taskReference('repo-a/series/01_first.json');
const OTHER_MASTER_REF = taskReference('repo-a/other/task.json');

export function seedSeries(
  options: {
    sliceSummary?: Partial<TaskSummary>;
    sliceEntry?: Partial<TaskEntry>;
    worktrees?: TaskEntry['worktrees'];
  } = {},
) {
  const master = taskEntry({
    reference: SERIES_MASTER_REF,
    id: 'series',
    title: 'My Series',
    stage: 'started',
    children: [taskReferenceKey(SERIES_SLICE_REF), taskReferenceKey(OTHER_MASTER_REF)],
    counts: { planned: 0, started: 2, completed: 0, aborted: 0, works: 1, paused: 0, unknown: 1 },
    summary: taskSummary({
      form: 'master',
      kind: 'master',
      type: '',
      objective: 'Series objective text',
      sections: [
        {
          kind: 'freeform',
          heading: 'Current State',
          body: 'Status is **strong**.\n\n| Slice | Status |\n| --- | --- |\n| 01 | done |',
        },
        { kind: 'subTasks', heading: 'Sub-tasks', body: '' },
      ],
    }),
    worktrees: options.worktrees ?? [],
  });
  const slice = taskEntry({
    reference: SERIES_SLICE_REF,
    parent: taskReferenceKey(SERIES_MASTER_REF),
    id: '1',
    title: 'First slice',
    stage: 'started',
    activity: 'works',
    stepsTotal: 1,
    ...options.sliceEntry,
    summary: taskSummary({
      type: '',
      objective: 'Slice objective text',
      steps: [
        {
          id: 'S1',
          title: 'do the thing',
          status: 'pending',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      ...options.sliceSummary,
    }),
  });
  const other = taskEntry({
    reference: OTHER_MASTER_REF,
    parent: taskReferenceKey(SERIES_MASTER_REF),
    id: '2',
    title: 'Parallel series',
    stage: 'started',
    summary: taskSummary({ form: 'master', kind: 'master', type: '', objective: '' }),
  });
  seedTaskEntries([master, slice, other]);
}

export function nestedProgressSteps(): TaskSummary['steps'] {
  return Array.from({ length: 7 }, (_, stepIndex) => ({
    id: `S${stepIndex + 1}`,
    title: `Top level step ${stepIndex + 1}`,
    status: stepIndex < 6 ? 'done' : 'pending',
    noteTruncated: false,
    outcomeTruncated: false,
    substeps: Array.from({ length: 6 }, (_, substepIndex) => ({
      id: `S${stepIndex + 1}.${substepIndex + 1}`,
      title: `Nested step ${stepIndex + 1}.${substepIndex + 1}`,
      status: stepIndex < 6 || substepIndex < 4 ? 'done' : 'pending',
    })),
  }));
}

export function seedSeriesOrdering() {
  const earlier = taskEntry({
    reference: taskReference('repo-a/series/zulu_earlier.json'),
    parent: taskReferenceKey(SERIES_MASTER_REF),
    id: '01',
    title: 'Zulu earlier',
    stage: 'planned',
  });
  const later = taskEntry({
    reference: taskReference('repo-a/series/alpha_later.json'),
    parent: taskReferenceKey(SERIES_MASTER_REF),
    id: '99',
    title: 'Alpha later',
    stage: 'started',
  });
  const master = taskEntry({
    reference: SERIES_MASTER_REF,
    id: 'series',
    title: 'Ordered Series',
    children: [earlier.key, later.key],
    counts: { planned: 1, started: 1, completed: 0, aborted: 0, works: 0, paused: 0, unknown: 1 },
    summary: taskSummary({ form: 'master', kind: 'master', type: '', objective: '' }),
  });
  seedTaskEntries([master, earlier, later]);
}

export function seedPromotedLeaf() {
  const masterRef = taskReference('agents-remember/260610_browser-dashboard/task.json');
  const leafRef = taskReference(
    'agents-remember/260610_browser-dashboard/16_engine-room-stack-entry-height.json',
  );
  const master = taskEntry({
    reference: masterRef,
    id: '260610_BROWSER-DASHBOARD',
    title: 'Browser Dashboard Series',
    stage: 'started',
    children: [taskReferenceKey(leafRef)],
    summary: taskSummary({
      form: 'master',
      kind: 'master',
      type: '',
      objective: 'Parent master content.',
    }),
  });
  const leaf = taskEntry({
    reference: leafRef,
    parent: taskReferenceKey(masterRef),
    id: '16_engine-room-stack-entry-height',
    title: 'Engine Room Stack Entry Height',
    stage: 'started',
    activity: 'paused',
    stepsTotal: 1,
    worktrees: [
      enclosure({ enclosure: '/contracts/16', leafId: '16_engine-room-stack-entry-height' }),
    ],
    summary: taskSummary({
      type: '',
      objective: 'Keep a single Engine Room enclosure entry visually bounded.',
      requirements: [
        'Render the selected leaf task document, not the parent task or enclosure contract.',
      ],
      steps: [
        {
          id: 'S1',
          title: 'Fix the stack entry height',
          status: 'inProgress',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      sections: [
        { kind: 'freeform', heading: 'Notes', body: 'This is the authored leaf task document.' },
      ],
    }),
  });
  seedTaskEntries([master, leaf]);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  dashboardStore.getState().reset();
});

// What a fetch double answers: a response the page reads as JSON.
function answer(payload: unknown): Response {
  return new Response(JSON.stringify(payload));
}

function queryOf(url: string): URLSearchParams {
  return new URLSearchParams(url.split('?', 2)[1] ?? '');
}

// The body named by a qualified request is the served summary for that entry.
function taskDocumentBody(url: string): TaskSummary | undefined {
  return Object.values(dashboardStore.getState().tasks).find(
    (entry) => entry.key === queryOf(url).get('path'),
  )?.summary;
}

// ChangeSetButton fetches its counters on mount; a tiny stub keeps the doc-reader-bar tests from
// touching a real fetch (the counts are incidental — these tests assert the buttons + click target).
// `reviewSummary` optionally answers the changed-intent summary route the Intent review control reads:
// counts, partial counts and an unavailable refusal are three different answers, and the control
// opens the review for all of them. Omitted, the route falls through to the counters body (not a
// summary answer), which is what a caller that does not exercise the entry wants.
export function stubCounters(reviewSummary?: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url.startsWith('/api/task-document')) return answer(taskDocumentBody(url));
      if (reviewSummary !== undefined && url.startsWith('/api/review/intent/summary')) {
        return answer(reviewSummary);
      }
      if (url.startsWith('/api/role-launch/reports')) {
        // The server contract names the report rows; the generic catch-all
        // below is not a well-formed reports payload.
        return answer({ reports: [], total: 0, explorer: null });
      }
      return answer({
        counters: {
          code: { files: 0, insertions: 0, deletions: 0 },
          memory: { files: 0, insertions: 0, deletions: 0 },
        },
      });
    }),
  );
}

// A note the notes double is seeded with.
interface SeededNote {
  name: string;
  path: string;
  size: number;
  language: string;
}

// A requirement packet the notes double is seeded with.
interface SeededRequirement {
  name: string;
  path: string;
  address: string;
  size: number;
  sha256: string;
}

interface FetchInit {
  body?: unknown;
}

// How many entries of a folder one answer carries.
const NOTES_PAGE = 200;

// One folder of the seeded notes: the files that lie in it, one page from the offset.
function notesFolder(notes: SeededNote[], url: string) {
  const params = queryOf(url);
  const folder = params.get('path') ?? '';
  const offset = Number(params.get('offset') ?? '0');
  const prefix = folder ? `${folder}/` : '';
  const entries = notes
    .filter((note) => note.path.startsWith(prefix) && !note.path.slice(prefix.length).includes('/'))
    .map((note) => ({
      name: note.name,
      path: note.path,
      kind: 'file' as const,
      size: note.size,
      language: note.language,
    }));
  return {
    repo: 'agents-remember',
    master: 'm',
    path: folder,
    entries: entries.slice(offset, offset + NOTES_PAGE),
    total: entries.length,
    offset,
    limit: NOTES_PAGE,
    remaining: Math.max(0, entries.length - offset - NOTES_PAGE),
    exists: true,
    reason: null,
  };
}

// The seeded note a reference names. The test double mirrors the server rule: a token naming a
// seeded notes-relative path (with or without the notes/ prefix) or a root-level bare name resolves.
function noteNamedBy(reference: string, paths: string[]): string | null {
  for (const token of reference.match(/[\w./-]+\.[A-Za-z0-9]+/g) ?? []) {
    const relative = token.startsWith('notes/') ? token.slice('notes/'.length) : token;
    if (paths.includes(relative)) return relative;
    if (!token.includes('/') && paths.includes(token)) return token;
  }
  return null;
}

// Which seeded note each reference of the request names.
function noteResolutions(notes: SeededNote[], init: FetchInit | undefined) {
  const asked = JSON.parse(typeof init?.body === 'string' ? init.body : '{}') as {
    references?: string[];
  };
  const paths = notes.map((note) => note.path);
  const resolutions = (asked.references ?? []).map((reference: string) => ({
    reference,
    path: noteNamedBy(reference, paths),
  }));
  return { repo: 'agents-remember', master: 'm', resolutions };
}

// The reports of a task: the double seeds none.
function noReports() {
  return { repo: 'agents-remember', master: 'm', doc: '', reports: [], total: 0, explorer: null };
}

// A note's file: every note reads as the one seeded body.
function noteFile(body: string) {
  return { path: 'x', language: 'markdown', size: body.length, truncated: false, content: body };
}

// The requirement packets registered for the master.
function requirementsList(requirements: SeededRequirement[]) {
  return {
    repo: 'agents-remember',
    master: 'm',
    document: 'm/task.json',
    registered: requirements.length > 0,
    requirements,
  };
}

export function stubNotes(
  notes: SeededNote[],
  body = 'note body',
  requirements: SeededRequirement[] = [],
) {
  // Each address the page asks and what the double answers there; the first that matches answers.
  const routes: Array<[string, (url: string, init?: FetchInit) => unknown]> = [
    ['/api/task-document', taskDocumentBody],
    ['/api/notes/folder', (url) => notesFolder(notes, url)],
    ['/api/notes/resolve', (_url, init) => noteResolutions(notes, init)],
    ['/api/role-launch/reports', noReports],
    ['/api/notes/read', () => noteFile(body)],
    ['/api/requirements/list', () => requirementsList(requirements)],
  ];
  const fn = vi.fn(async (url: string, init?: FetchInit) => {
    const route = routes.find(([prefix]) => url.startsWith(prefix));
    return answer(route ? route[1](url, init) : {});
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

// The texts of an element's direct children: the cells a row or a row link draws.
export function cells(el: Element | null | undefined): string[] {
  return Array.from(el?.children ?? []).map((child) => child.textContent ?? '');
}
