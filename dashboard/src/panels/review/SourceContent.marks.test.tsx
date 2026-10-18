import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReviewSourceExpansion } from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import { IntentMarkerScope, type IntentMarkerScopeValue } from './intentMarkerScope';
import { SourceContent } from './SourceContent';

const captured = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'hunkMarkers.classifier.captured.json',
    ),
    'utf8',
  ),
) as {
  curated: Record<
    string,
    { file_classification: ReviewFileClassification; texts: { before: string; after: string } }
  >;
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mount(options: { far?: boolean; truncated?: boolean; mode?: 'inline' | 'split' } = {}) {
  const body = structuredClone(captured.curated['pkg/lines.txt']);
  const file = body.file_classification;
  if (options.far) {
    file.hunks[0].before.start = 2500;
    file.hunks[0].after.start = 2500;
    body.texts.before = Array.from({ length: 3000 }, (_, i) => `before ${i}`).join('\n');
    body.texts.after = Array.from({ length: 3000 }, (_, i) => `after ${i}`).join('\n');
    file.hunks = [file.hunks[0]];
  }
  const expansion: ReviewSourceExpansion = {
    path: file.path,
    status: 'modified',
    mode_change: false,
    language: 'text',
    before: {
      state: 'present',
      object_id: file.sides[0].blob,
      text: options.truncated ? 'first\n' : body.texts.before,
      byte_length: options.truncated ? 3_000_000 : body.texts.before.length,
      truncated: !!options.truncated,
      detail: 'source bytes',
    },
    after: {
      state: 'present',
      object_id: file.sides[1].blob,
      text: options.truncated ? 'first\n' : body.texts.after,
      byte_length: options.truncated ? 3_000_000 : body.texts.after.length,
      truncated: !!options.truncated,
      detail: 'source bytes',
    },
    before_code_tree_id: 'before',
    after_code_tree_id: 'after',
    currentness: 'current',
    currentness_detail: 'bound trees',
    path_bound: 'requested_generation',
    path_bound_detail: 'listed',
    admission: 'changed',
    admission_detail: 'listed',
    reference: 'source',
    command: 'git cat-file blob',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ state: 'content', expansion }),
    })),
  );
  const scope: IntentMarkerScopeValue = {
    comparison: 1,
    listed: () => true,
    partial: false,
    classify: () => null,
    peek: () => undefined,
    origin: null,
    target: null,
    returning: null,
    follow: vi.fn(),
    back: vi.fn(),
    settle: vi.fn(),
  };
  return render(
    <IntentMarkerScope.Provider value={scope}>
      <SourceContent
        repo="repo"
        master="master"
        leaf="leaf"
        entry={{ path: file.path, status: 'modified', content: 'text', mode_change: false }}
        beforeCodeTreeId="before"
        afterCodeTreeId="after"
        mode={options.mode ?? 'split'}
        markers={{ classification: file }}
      />
    </IntentMarkerScope.Provider>,
  );
}

it('lists every hunk with its ranges, class and linked invariants', async () => {
  const view = mount();
  const list = await view.findByRole('region', { name: 'Marks in this file' });
  const lines = within(list).getAllByRole('listitem');
  expect(lines).toHaveLength(3);
  expect(lines[0].textContent).toContain('before L2');
  expect(lines[0].textContent).toContain('after L2');
  expect(lines[0].textContent).toContain('linked');
  const invariants = captured.curated['pkg/lines.txt'].file_classification.hunks[0].links.map(
    (link) => link.invariant,
  );
  for (const invariant of invariants) expect(lines[0].textContent).toContain(invariant);
  expect(lines[1].textContent).toContain('attribution unknown');
  expect(lines[2].textContent).toContain('unexplained');
});

it('reaches an initially undrawn hunk by keyboard and focuses its gutter mark', async () => {
  const view = mount({ far: true });
  const list = await view.findByRole('region', { name: 'Marks in this file' });
  const button = within(list).getByRole('button');
  await view.findByTestId('diff-pane');
  await waitFor(() => expect(view.container.querySelector('.cm-editor')).not.toBeNull());
  expect(view.queryByTestId('review-hunk-mark')).toBeNull();
  const user = userEvent.setup();
  button.focus();
  await user.keyboard('{Enter}');
  await waitFor(
    () => {
      const mark = view.getByTestId('review-hunk-mark');
      expect(mark.dataset.hunk).toBe('2500:1:2500:1');
      expect(document.activeElement).toBe(mark);
    },

  );
});

it('keeps out-of-prefix hunks listed and disabled without moving the diff', async () => {
  const view = mount({ far: true, truncated: true });
  const list = await view.findByRole('region', { name: 'Marks in this file' });
  const line = within(list).getByRole('listitem');
  expect(line.textContent).toContain('before L2500');
  expect(line.textContent).toContain('linked');
  expect(line.textContent).toContain('beyond the part of the file shown here (first 2 MiB)');
  const button = within(line).getByRole('button') as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  const active = document.activeElement;
  fireEvent.click(button);
  expect(document.activeElement).toBe(active);
  expect(view.queryByTestId('review-hunk-mark')).toBeNull();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
});
