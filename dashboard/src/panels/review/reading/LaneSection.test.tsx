// MIK-R89 v2 rule 7 and MIK-R86 v2 rule 14 (amendment item 10): an added lane file longer than 30
// lines shows its first 30 and "N more added lines · open the file in the diff viewer", which opens
// that file on the change-set page. The page lists a filter's files from the lane read's destination
// of that name (reviewChangeSet.ts `laneFiles`) and falls back to the first file when the asked path
// is not listed; so the link asks for the filter whose destination lists the file: Unexplained for a
// file no recorded entry claims, Unknown attribution for a file of unknown attribution.
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import type { ReviewLaneFile, ReviewUnexplainedLane } from '../../../data/reviewLane';
import { laneFiles as filterFiles, type ReviewChangeContext } from '../../changeset/reviewChangeSet';
import { ReviewChangeSetNavigation, type OpenReviewChanges } from '../changeSetNavigation';
import { l181Answer } from '../changeSetOrder.test-utils';
import { LaneSection } from './LaneSection';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const PLACE = { repo: 'agents-remember', master: '260928_maintained-invariant-knowledge', leaf: '260928-MIK-L181', repository: 'agents-remember' };
const UNEXPLAINED = 'dashboard/src/panels/review/reading/added.ts';
const UNKNOWN = 'dashboard/src/panels/review/reading/unknown.ts';
const TEXT = Array.from({ length: 45 }, (_, index) => `export const line${index + 1} = ${index + 1};`).join('\n');

const file = (path: string, bucket: ReviewLaneFile['bucket']): ReviewLaneFile => ({
  path,
  status: 'added',
  content: 'text',
  bucket,
  reason: bucket === 'unexplained' ? 'no recorded entry claims it' : 'an entry supplies no range at this content',
  counts: { hunks: 1, linked: 0, unexplained: bucket === 'unexplained' ? 1 : 0, attribution_unknown: bucket === 'unexplained' ? 0 : 1 },
  unknown_reasons: [],
});
const destination = (files: ReviewLaneFile[]) => ({ files, bucket_files: files.length, attributed_files: 0, hunks: files.length, non_text: 0 });

// One file of each bucket, both added and 45 lines long.
const lane: ReviewUnexplainedLane = {
  state: 'measured',
  detail: 'composed for the lane link',
  changed_total: 2,
  attributed: 0,
  unexplained: 1,
  attribution_unknown: 1,
  unexplained_changes: destination([file(UNEXPLAINED, 'unexplained')]),
  unknown_attribution: destination([file(UNKNOWN, 'attribution_unknown')]),
  unmeasured: [],
  paths: [
    { path: UNEXPLAINED, bucket: 'unexplained' },
    { path: UNKNOWN, bucket: 'attribution_unknown' },
  ],
};

function serveText() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string | URL) => {
      const path = new URL(String(address), 'http://localhost').searchParams.get('path');
      const body = { scope: 'comparison', kind: 'code', path, language: 'typescript', before: null, after: { content: TEXT } };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
}

it.each([
  ['no recorded entry claims', UNEXPLAINED, 'unexplained'],
  ['of unknown attribution', UNKNOWN, 'unknown'],
] as const)('opens a long added file %s at its own path, in the filter that lists it', async (_bucket, path, filter) => {
  serveText();
  const open = vi.fn<OpenReviewChanges>();
  render(
    <ReviewChangeSetNavigation.Provider value={open}>
      <LaneSection
        read={{ phase: 'ready', value: lane }}
        place={PLACE}
        comparison={l181Answer.comparison!}
        layout="inline"
        collapse
        select={{ selection: null, onSelect: () => undefined }}
      />
    </ReviewChangeSetNavigation.Provider>,
  );
  const block = screen.getAllByTestId('review-lane-file').find((node) => node.dataset.path === path)!;
  await waitFor(() => expect(block.dataset.laneRead).toBe('ready'));
  const more = within(block).getByTestId('review-lane-more');
  expect(more.textContent).toContain('15 more added lines');

  fireEvent.click(more);
  expect(open).toHaveBeenCalledTimes(1);
  expect(open).toHaveBeenCalledWith(filter, path);
  // The change-set page's list for that filter holds the file, so it opens that file and no other.
  const context = { lane: { phase: 'ready', value: lane } } as unknown as ReviewChangeContext;
  expect((filterFiles(context, filter) ?? []).map((listed) => listed.path)).toContain(path);
});
