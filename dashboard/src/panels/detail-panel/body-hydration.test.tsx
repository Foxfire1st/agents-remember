// Exact native F01 producer operands (summary plus only the body's differing fields).
// L162-F01-native-reader-operands.json; no renderer-full fixture or producer double.
import { fireEvent, render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { TaskDocNode } from '../../types/projection';
import { DetailPanel } from './DetailPanel';
import { seedTaskDocuments, stubCounters } from './test-utils';

const summary0: TaskDocNode = {
  id: 'hydrate-master',
  repository: 'agents-remember',
  title: 'hydrate-master',
  status: 'planning',
  kind: 'master',
  form: 'master',
  type: 'Code',
  statusNoteTruncated: false,
  headerNotes: [],
  headerNotesTotal: 0,
  stepsDone: 0,
  stepsTotal: 0,
  docPath:
    '/var/tmp/mik-run3/L162/reviewer-54e84918/provider-runtime/dev-ar-coordination/tasks/agents-remember/hydrate-master/task.json',
  bodyRevision: '5075f96388212c76d8d64136ca7fd2c43352d2e4eb493d12b69c056a7ebb3d08',
  createdAt: '2026-01-01T00:00',
  ageSeconds: -0.023717164993286133,
  steps: [],
  objective: '',
  requirements: [],
  codeExamples: [],
  decisions: [],
  openQuestions: [],
  references: [],
  subTasks: [],
  discardedCount: 0,
  discardedSubTasks: [],
  sections: [],
  orchestrates: [],
  seats: [],
  executionWaves: [],
};
const body0: TaskDocNode = {
  ...summary0,
  nonGoals: 'NATIVE_NON_GOALS hydrate-master',
  acceptance: 'NATIVE_ACCEPTANCE hydrate-master',
  objective: 'Native reader objective.',
  design: 'Native reader design.',
  sections: [
    {
      kind: 'freeform',
      heading: 'Old details',
      body: 'Preserved legacy notes.',
    },
  ],
};

const summary1: TaskDocNode = {
  id: 'hydrate-task',
  repository: 'agents-remember',
  title: 'hydrate-task',
  status: 'planning',
  kind: 'subTask',
  form: 'task',
  type: 'Code',
  statusNoteTruncated: false,
  headerNotes: [],
  headerNotesTotal: 0,
  stepsDone: 0,
  stepsTotal: 0,
  docPath:
    '/var/tmp/mik-run3/L162/reviewer-54e84918/provider-runtime/dev-ar-coordination/tasks/agents-remember/hydrate-task/hydrate-task.json',
  bodyRevision: '96b3824afd6a7a52ceb8574ff63774d6251ad02291b9ba016eeae0fa6088b54e',
  createdAt: '2026-01-01T00:00',
  ageSeconds: -0.025243282318115234,
  steps: [],
  objective: '',
  requirements: [],
  codeExamples: [],
  decisions: [],
  openQuestions: [],
  references: [],
  subTasks: [],
  sections: [],
  orchestrates: [],
  seats: [],
  executionWaves: [],
};
const body1: TaskDocNode = {
  ...summary1,
  nonGoals: 'NATIVE_NON_GOALS hydrate-task',
  acceptance: 'NATIVE_ACCEPTANCE hydrate-task',
  objective: 'Native reader objective.',
  design: 'Native reader design.',
  sections: [
    {
      kind: 'freeform',
      heading: 'Old details',
      body: 'Preserved legacy notes.',
    },
  ],
};

describe('native body hydration through mounted DetailPanel', () => {
  it.each(
    [
      { summary: summary0, body: body0, skeleton: 'skeleton-master' },
      { summary: summary1, body: body1, skeleton: 'skeleton-task' },
    ].flatMap((pair) => (['nonGoals', 'acceptance'] as const).map((field) => ({ ...pair, field }))),
  )(
    'hydrates $field and preserves legacy access for $summary.kind',
    async ({ summary, body, skeleton, field }) => {
      expect(summary.bodyRevision).toBe(body.bodyRevision);
      expect(summary.nonGoals).toBeUndefined();
      expect(summary.acceptance).toBeUndefined();
      seedTaskDocuments([summary]);
      stubCounters();
      const ancillaryFetch = globalThis.fetch;
      const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).startsWith('/api/task-document?')) {
          expect(new URL(String(input), 'http://localhost').searchParams.get('path')).toBe(
            summary.docPath,
          );
          return { ok: true, status: 200, json: async () => body } as Response;
        }
        return ancillaryFetch(input, init);
      });
      vi.stubGlobal('fetch', fetch);
      vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
      const view = render(<DetailPanel selectedId={`taskdoc:${summary.docPath}`} />);
      await waitFor(() => expect(view.getByText(body[field]!)).toBeTruthy());
      expect(view.getByText(body.nonGoals!)).toBeTruthy();
      expect(view.getByText(body.acceptance!)).toBeTruthy();
      expect(view.getByText(body.design!)).toBeTruthy();
      const blocks = [...view.getByTestId(skeleton).querySelectorAll('[data-block]')].map(
        (element) => element.getAttribute('data-block'),
      );
      expect(blocks.indexOf('nonGoals')).toBe(blocks.indexOf('objective') + 1);
      expect(blocks.indexOf('design')).toBe(blocks.indexOf('nonGoals') + 1);
      expect(blocks.indexOf('acceptance')).toBe(blocks.indexOf('design') + 1);
      fireEvent.click(view.getByTestId('further-open-0'));
      expect(view.getByText('Preserved legacy notes.')).toBeTruthy();
      expect(
        fetch.mock.calls.filter(([input]) => String(input).startsWith('/api/task-document?')),
      ).toHaveLength(1);
    },
  );
});
