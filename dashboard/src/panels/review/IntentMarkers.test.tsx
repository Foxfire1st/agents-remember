// MIK-R34 at the renderer the review page draws its marks on: a card's excerpt (ReviewBlockCard's
// DiffPane, MIK-R89 v2 W1). A card has no Full file of its own (rule 4), so the cases that once used the
// landed full-file view draw a card whose excerpt is the fixture file's whole text on both sides. Every
// classification is a REAL served per-file body of MIK-L32's lane fixture world
// (hunkMarkers.capture-provenance.json), and every excerpt draws the exact blob texts that body names;
// nothing is fetched. The workspace's marker scope is a plain value here, so each case asserts what the
// reader sees and what a followed marker asks the workspace to select.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ReviewFileClassification } from '../../data/reviewLane';
import type { ReviewTreeEntry, ReviewTreesResult } from '../../data/reviewTrees';
import { blockIdentityOf, blockKey } from './blockIdentity';
import { BlockCards } from './reading/blockCards.test-utils';
import { type IntentMarkerScopeValue, IntentMarkerScope } from './intentMarkerScope';
import { resetCardCachesForTests } from '../../data/reviewTrees';

type Scenario =
  'precuration' | 'curated' | 'before_unread' | 'after_unread' | 'both_unread' | 'partial';
type Body = {
  file_classification: ReviewFileClassification;
  texts: Record<'before' | 'after', string | null>;
};
const captured = <T,>(name: string): T =>
  JSON.parse(readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8')) as T;
const bodies = captured<Record<Scenario, Record<string, Body>>>('hunkMarkers.classifier.captured.json');
// The comparison a card read names (only its trees enter the blocks' identities).
const comparison = captured<ReviewTreesResult>('gitTrees.cards.captured.json').comparison!;

afterEach(() => {
  resetCardCachesForTests();
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

type EntrySide = NonNullable<ReviewTreesResult['entries']>[number]['before'];

const lineCount = (text: string) => {
  const lines = text.split('\n');
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines.length;
};

// One side of a card whose excerpt is the whole file: its exact blob text from line 1, or no file.
function wholeSide(body: Body, name: 'before' | 'after'): EntrySide {
  const file = body.file_classification;
  const text = body.texts[name];
  if (text === null) return { state: 'absent', path: file.path, reason: 'no file at the path on this side' };
  return {
    state: 'resolved',
    path: file.path,
    blob: file.sides.find((one) => one.side === name)!.blob,
    start_line: 1,
    end_line: lineCount(text),
    excerpt: text,
    role: 'primary-authority',
    rationale: 'The whole file.',
  };
}

// A changed card over the fixture file's whole text on both sides.
function wholeFileEntry(body: Body, sides: { before?: Partial<EntrySide>; after?: Partial<EntrySide> } = {}): ReviewTreeEntry {
  const file = body.file_classification;
  const link = file.hunks.flatMap((hunk) => hunk.links)[0];
  return {
    id: 'RLZ-WHOLEFILE',
    kind: 'realization',
    invariant: link?.invariant ?? 'INV-AAAAAA',
    invariant_key: link?.invariant_key ?? 'invariant-key',
    change: 'changed',
    before: { ...wholeSide(body, 'before'), ...sides.before } as EntrySide,
    after: { ...wholeSide(body, 'after'), ...sides.after } as EntrySide,
  };
}

const paneOf = (entry: ReviewTreeEntry) => `card-excerpt:${blockKey(blockIdentityOf(entry, comparison))}`;

function scopeOf(over: Partial<IntentMarkerScopeValue> = {}): IntentMarkerScopeValue {
  return {
    comparison: 1,
    listed: () => true,
    partial: false,
    classify: vi.fn(() => null),
    peek: () => undefined,
    origin: null,
    target: null,
    returning: null,
    follow: vi.fn(),
    back: vi.fn(),
    settle: vi.fn(),
    ...over,
  };
}

// The card of one fixture file, its classification served by the scope's one read. `returning` names a
// hunk of this card's pane that a return is bringing the reader back to.
function open(
  scenario: Scenario,
  file: string,
  options: {
    scope?: Partial<IntentMarkerScopeValue>;
    layout?: 'split' | 'inline';
    sides?: { before?: Partial<EntrySide>; after?: Partial<EntrySide> };
    returning?: string;
  } = {},
) {
  const body = bodies[scenario][file];
  const entry = wholeFileEntry(body, options.sides);
  const pane = paneOf(entry);
  const scope = scopeOf({
    classify: vi.fn(() => Promise.resolve({ phase: 'ready' as const, value: body.file_classification })),
    ...(options.returning ? { returning: { path: file, pane, hunk: options.returning } } : {}),
    ...options.scope,
  });
  const view = render(
    <IntentMarkerScope.Provider value={scope}>
      <BlockCards entries={[entry]} comparison={comparison} layout={options.layout ?? 'split'} collapse={false} />
    </IntentMarkerScope.Provider>,
  );
  return { view, scope, body, pane };
}

// The marks in the owner's hunk order (document order puts the before editor's marks first).
const inHunkOrder = (shown: HTMLElement[]) =>
  [...shown].sort((one, other) => hunkOrder(one) - hunkOrder(other));
const hunkOrder = (mark: HTMLElement) => Number(mark.dataset.hunk!.split(':')[0]);

async function marks(view: ReturnType<typeof render>, count: number) {
  await waitFor(() => expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(count));
  return inHunkOrder(view.getAllByTestId('review-hunk-mark'));
}

const described = (mark: HTMLElement) => [
  mark.textContent,
  mark.dataset.before,
  mark.dataset.after,
  mark.closest('.cm-merge-a') ? 'before editor' : 'after editor',
];

describe('the marks of a diff', () => {
  it("marks each owner hunk on the owner's side lines, a deletion in the before editor", async () => {
    const { view, scope } = open('curated', 'pkg/lines.txt');
    // The whole file is one displayed region holding three owner hunks: each keeps its own mark.
    expect((await marks(view, 3)).map(described)).toEqual([
      ['2 intents', 'L2', 'L2', 'after editor'],
      ['attribution unknown', 'L4', 'L4', 'after editor'],
      ['unexplained', 'L6', 'none (after L5)', 'before editor'],
    ]);
    // One classification read of the file, through the scope.
    expect(vi.mocked(scope.classify).mock.calls.map(([asked]) => asked)).toEqual(['pkg/lines.txt']);
    // A phone-width gutter shows each mark's compact text; the label stays its text and name.
    const shownMarks = await marks(view, 3);
    expect(shownMarks.map((mark) => [mark.dataset.compact, mark.textContent])).toEqual([
      ['2', '2 intents'],
      ['?', 'attribution unknown'],
      ['!', 'unexplained'],
    ]);
    expect(shownMarks[0].getAttribute('aria-label')).toBe('2 intents · hunk before L2, after L2');
    // The marks are controls: their gutter is exposed to assistive technology, the line numbers
    // stay hidden as CodeMirror hides them.
    const shown = view.getAllByTestId('review-hunk-mark');
    expect(shown.filter((mark) => mark.closest('[aria-hidden="true"]'))).toEqual([]);
    const numbers = view.container.querySelector('.cm-lineNumbers');
    expect(numbers?.closest('[aria-hidden="true"]')).toBeTruthy();
  });

  it('marks every hunk on an after line inline, the deletion below its removed lines', async () => {
    const { view } = open('curated', 'pkg/lines.txt', { layout: 'inline' });
    const shown = await marks(view, 3);
    expect(shown.map((mark) => mark.closest('.cm-merge-a'))).toEqual([null, null, null]);
    expect(shown.map((mark) => mark.closest('.cm-editor'))).toEqual(
      Array(3).fill(view.container.querySelector('.cm-editor')),
    );
  });

  it('lists a hunk’s intents with their family occurrences and follows one to its position', async () => {
    const { view, body, scope, pane } = open('curated', 'pkg/lines.txt');
    const [two] = await marks(view, 3);
    fireEvent.click(two);
    expect(two.getAttribute('aria-expanded')).toBe('true');
    const panel = view.getByTestId('review-hunk-mark-panel');
    const entries = within(panel).getAllByTestId('review-hunk-mark-entry');
    expect(entries.map((entry) => entry.dataset.invariant)).toEqual(['INV-AAAAAA', 'INV-BBBBBB']);
    const occurrences = within(panel).getAllByTestId('review-hunk-mark-occurrence');
    expect(occurrences.map((one) => [one.dataset.state, one.textContent])).toEqual([
      ['member', 'FAM-F00001 r1 · member (before, r1)'],
      ['removed_or_reassigned', 'FAM-F00002 r1 · removed or reassigned in after (before, r1)'],
      ['before_only', 'FAM-F00003 r1 · before-only (before, r1)'],
      ['confirmed_no_family', 'No recorded family (before, r1)'],
    ]);
    // Intersection only: the list says what "listed" means.
    expect(panel.textContent).toContain('asserts no coverage, correctness or preservation');
    const link = body.file_classification.hunks[0].links[0];
    fireEvent.click(occurrences[2]);
    expect(scope.follow).toHaveBeenLastCalledWith(
      { path: 'pkg/lines.txt', pane, hunk: '2:1:2:1' },
      {
        invariant: 'INV-AAAAAA',
        invariantKey: link.invariant_key,
        familyKey: link.families[2].family_key,
        memberRevisionKey: link.invariant_revision_key,
        family: 'FAM-F00003',
        state: 'before_only',
      },
    );
    fireEvent.click(occurrences[3]);
    expect(vi.mocked(scope.follow).mock.lastCall![1]).toEqual({
      invariant: 'INV-BBBBBB',
      invariantKey: body.file_classification.hunks[0].links[1].invariant_key,
      familyKey: undefined,
      memberRevisionKey: undefined,
      family: undefined,
      state: 'confirmed_no_family',
      reason: undefined,
    });
  });

  it('lists a proof entry as a test with its facet, apart from the intents', async () => {
    const { view } = open('precuration', 'tests/test_a.py');
    const [mark] = await marks(view, 1);
    expect(mark.textContent).toBe('INV-AAAAAA · test');
    fireEvent.click(mark);
    const panel = view.getByTestId('review-hunk-mark-panel');
    expect(
      within(panel)
        .getAllByTestId('review-hunk-mark-group')
        .map((group) => group.dataset.group),
    ).toEqual(['tests']);
    expect(within(panel).getByTestId('review-hunk-mark-facet').textContent).toBe(
      'facet: land returns its value.',
    );
  });

  it("keeps the readable side's marks and names the unread side's changed lines unknown", async () => {
    const { view } = open('before_unread', 'pkg/a.py');
    const [edit, insertion] = await marks(view, 2);
    expect([edit.textContent, insertion.textContent]).toEqual(['1 intent · unknown', 'INV-CCCCCC']);
    fireEvent.click(edit);
    const unknown = view.getByTestId('review-hunk-mark-unknown');
    expect([unknown.dataset.side, unknown.textContent]).toEqual([
      'before',
      expect.stringContaining('before: attribution unknown — the before knowledge is unavailable'),
    ]);
    // An unknown membership opens without a family; the list says so.
    cleanup();
    const unknownFamily = open('partial', 'pkg/lines.txt').view;
    fireEvent.click((await marks(unknownFamily, 3))[0]);
    expect(unknownFamily.getByTestId('review-hunk-mark-occurrence').textContent).toBe(
      'Attribution unknown (before, r1)',
    );
  });
});

describe('a file without per-hunk marks', () => {
  it('gives a confirmed-unregistered file one file-level unexplained mark', async () => {
    const { view } = open('precuration', 'pkg/new.py');
    const mark = await view.findByTestId('review-file-mark');
    expect([mark.dataset.mark, mark.textContent]).toEqual([
      'unexplained',
      expect.stringContaining('one mark for the whole file'),
    ]);
    await view.findByTestId('diff-pane');
    expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it("gives a file one attribution-unknown note when neither side's knowledge is readable", async () => {
    const { view } = open('both_unread', 'pkg/a.py');
    const mark = await view.findByTestId('review-file-mark');
    expect(mark.dataset.mark).toBe('attribution_unknown');
    await view.findByTestId('diff-pane');
    expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  // An unchanged file's card is "never reads, nor speaks for, an unchanged file's card" below.
  it('marks nothing for other content on either side, and says why', async () => {
    // A classification of other bytes than the drawn ones places no mark, and says why.
    const other = open('curated', 'pkg/lines.txt', { sides: { after: { blob: 'f'.repeat(40) } } }).view;
    expect((await other.findByTestId('review-marks-state')).dataset.marksState).toBe('other-content');
    expect(other.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
    cleanup();
    // The before object alone differing is other content too.
    const otherBefore = open('curated', 'pkg/lines.txt', { sides: { before: { blob: 'e'.repeat(40) } } }).view;
    expect((await otherBefore.findByTestId('review-marks-state')).dataset.marksState).toBe('other-content');
    expect(otherBefore.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it('says why a changed file a partial inventory does not list carries no marks', async () => {
    // Review R1 F5: the file may have changed, and its classification is not read -- said so.
    const classify = vi.fn(() => null);
    const partial = open('curated', 'pkg/lines.txt', {
      scope: { classify, listed: () => false, partial: true },
    }).view;
    const note = await partial.findByTestId('review-marks-state');
    expect([note.dataset.marksState, note.textContent]).toEqual([
      'unlisted',
      expect.stringContaining('the change inventory is partial and does not list this path'),
    ]);
    expect(classify).not.toHaveBeenCalled();
    cleanup();
    // A complete inventory that does not list a path: the file is unchanged, with nothing to mark.
    const complete = open('curated', 'pkg/lines.txt', {
      scope: { listed: () => false, partial: false },
    }).view;
    await complete.findByTestId('diff-pane');
    expect(complete.queryByTestId('review-marks-state')).toBeNull();
  });
});

describe('the return to a followed marker', () => {
  it('reopens its list, brings its hunk into view and focuses it', async () => {
    const scrolled = vi.fn();
    vi.stubGlobal('Element', Element);
    Element.prototype.scrollIntoView = scrolled;
    const settle = vi.fn();
    const { view } = open('curated', 'pkg/lines.txt', { returning: '4:1:4:1', scope: { settle } });
    const four = (await marks(view, 3))[1];
    await waitFor(() => expect(settle).toHaveBeenCalled());
    expect(document.activeElement).toBe(four);
    expect(four.getAttribute('aria-expanded')).toBe('true');
    expect(view.getByTestId('review-hunk-mark-panel').dataset.hunk).toBe('4:1:4:1');
    expect(scrolled.mock.contexts.some((host) => (host as Element).contains(four))).toBe(true);
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });

  it('keeps the returned mark focused while the pane settles, never against the reader', async () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] });
    // Review R2-F1: a redraw after the reveal leaves focus on the body; the short hold gives it back.
    const { view } = open('curated', 'pkg/lines.txt', { returning: '4:1:4:1' });
    const four = (await marks(view, 3))[1];
    await act(async () => { await vi.advanceTimersByTimeAsync(32); });
    expect(document.activeElement).toBe(four);
    four.blur();
    expect(document.activeElement).toBe(document.body);
    await act(async () => { vi.advanceTimersToNextFrame(); });
    expect(document.activeElement).toBe(four);
    // The reader's own key ends the hold: focus the reader leaves on the body stays there.
    fireEvent.keyDown(document.body, { key: 'Escape' });
    four.blur();
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(document.activeElement).toBe(document.body);
  });

  it('does not reopen a pane the marker was not followed from', async () => {
    const { view, scope } = open('curated', 'pkg/lines.txt', {
      scope: { returning: { path: 'pkg/lines.txt', pane: 'lane-full', hunk: '4:1:4:1' } },
    });
    await marks(view, 3);
    expect(view.queryByTestId('review-hunk-mark-panel')).toBeNull();
    expect(scope.settle).not.toHaveBeenCalled();
  });
});

describe("a card's excerpt", () => {
  // The REAL before_unread classification of pkg/a.py: the before memory tree cannot be read, and the
  // after side's re-recorded ranges link the edit at line 2. The card is the one the entries read serves
  // for such a comparison: no before blob (the side is unavailable), the after range's text (lines 1-2).
  const body = bodies.before_unread['pkg/a.py'];
  const file = body.file_classification;
  const link = file.hunks[0].links[0];
  const unreadable: EntrySide = {
    state: 'unavailable',
    path: file.path,
    reason: 'the before memory tree cannot be read',
  };
  const lines12 = (side: 'before' | 'after', blob: string | undefined): EntrySide => ({
    state: 'resolved',
    path: file.path,
    blob,
    start_line: 1,
    end_line: 2,
    excerpt: body.texts[side]!.split('\n').slice(0, 2).join('\n') + '\n',
    role: 'primary-authority',
    rationale: 'It is the rule.',
  });
  const entry = (
    afterBlob: string | undefined,
    before: EntrySide = unreadable,
    change: 'changed' | 'unchanged' | 'undetermined' = 'undetermined',
  ): ReviewTreesResult['entries'] => [
    {
      id: link.id,
      kind: 'realization',
      invariant: link.invariant,
      invariant_key: link.invariant_key,
      change,
      before,
      after: lines12('after', afterBlob),
    },
  ];

  function card(
    afterBlob: string | undefined,
    options: {
      before?: EntrySide;
      change?: 'changed' | 'unchanged' | 'undetermined';
      scope?: Partial<IntentMarkerScopeValue>;
    } = {},
  ) {
    const scope = scopeOf({
      classify: vi.fn(() => Promise.resolve({ phase: 'ready' as const, value: file })),
      ...options.scope,
    });
    return render(
      <IntentMarkerScope.Provider value={scope}>
        <BlockCards entries={entry(afterBlob, options.before, options.change) ?? []} comparison={comparison} />
      </IntentMarkerScope.Provider>,
    );
  }

  it.each(
    (
      [
        {
          excerpt: 'unchanged',
          change: 'unchanged',
          before: lines12('before', file.sides[0].blob),
        },
        { excerpt: 'diff', change: 'changed', before: lines12('before', file.sides[0].blob) },
        { excerpt: 'separate', change: 'undetermined', before: unreadable },
      ] as const
    ).flatMap((variant) =>
      (['ready', 'unavailable'] as const).map((outcome) => ({ ...variant, outcome })),
    ),
  )(
    'shows marks loading in the $excerpt excerpt until $outcome (R57 rule 5)',
    async ({ excerpt: variant, change, before, outcome }) => {
      type ClassificationRead = Exclude<
        Awaited<ReturnType<IntentMarkerScopeValue['classify']>>,
        null
      >;
      let finish!: (read: ClassificationRead) => void;
      const pending = new Promise<ClassificationRead>((resolve) => {
        finish = resolve;
      });
      const classify = vi.fn(() => pending);
      const view = card(file.sides[1].blob, {
        before,
        change,
        scope: { classify },
      });
      const excerpt = await view.findByTestId('review-card-excerpt');
      expect(excerpt.dataset.excerpt).toBe(variant);
      expect(within(excerpt).getByTestId('review-marks-state').dataset.marksState).toBe('loading');
      expect(within(excerpt).getByTestId('review-marks-state').textContent).toBe(
        'intent marks loading…',
      );
      expect(within(excerpt).queryAllByTestId('review-hunk-mark')).toHaveLength(0);
      expect(classify).toHaveBeenCalledExactlyOnceWith(file.path);

      await act(async () => {
        finish(
          outcome === 'ready'
            ? { phase: 'ready', value: file }
            : {
                phase: 'unavailable',
                problem: {
                  token: 'unreadable',
                  code: 'marks-read-failed',
                  detail: 'The classification read failed.',
                },
              },
        );
      });
      if (outcome === 'ready') {
        await waitFor(() =>
          expect(within(excerpt).queryAllByTestId('review-hunk-mark').length).toBeGreaterThan(0),
        );
        expect(within(excerpt).queryByTestId('review-marks-state')).toBeNull();
      } else {
        const note = within(excerpt).getByTestId('review-marks-state');
        expect(note.dataset.marksState).toBe('unavailable');
        expect(note.textContent).toBe(
          'intent marks unavailable · marks-read-failed: The classification read failed.',
        );
        expect(within(excerpt).queryAllByTestId('review-hunk-mark')).toHaveLength(0);
      }
      expect(classify).toHaveBeenCalledExactlyOnceWith(file.path);
    },
  );

  it("keeps the drawn side's marks when the other memory side cannot be read (review R1 F1)", async () => {
    const view = card(file.sides[1].blob);
    const excerpt = await view.findByTestId('review-card-excerpt');
    expect(excerpt.dataset.excerpt).toBe('separate');
    await waitFor(() =>
      expect(within(excerpt).queryAllByTestId('review-hunk-mark')).toHaveLength(1),
    );
    const [mark] = within(excerpt).getAllByTestId('review-hunk-mark');
    expect([mark.dataset.hunk, mark.textContent]).toEqual(['2:1:2:1', '1 intent · unknown']);
    expect(within(excerpt).queryByTestId('review-marks-state')).toBeNull();
  });

  it('places no mark when either drawn side is other content, though the other matches', async () => {
    // Both sides drawn (a changed range): the before side is the classification's, the after is not.
    const view = card('f'.repeat(40), {
      before: lines12('before', file.sides[0].blob),
      change: 'changed',
    });
    const excerpt = await view.findByTestId('review-card-excerpt');
    expect(excerpt.dataset.excerpt).toBe('diff');
    expect((await within(excerpt).findByTestId('review-marks-state')).dataset.marksState).toBe(
      'other-content',
    );
    expect(within(excerpt).queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it("never reads, nor speaks for, an unchanged file's card", async () => {
    // One blob on both sides: the file did not change. Even under a partial inventory that does not
    // list it, its card asks nothing and says nothing.
    const classify = vi.fn(() => null);
    const blob = file.sides[1].blob;
    const view = card(blob, {
      before: lines12('after', blob),
      change: 'unchanged',
      scope: { classify, listed: () => false, partial: true },
    });
    const excerpt = await view.findByTestId('review-card-excerpt');
    // The excerpt has rendered; flush its effects before asserting no classification was requested.
    await act(async () => {});
    expect(within(excerpt).queryByTestId('review-marks-state')).toBeNull();
    expect(classify).not.toHaveBeenCalled();
  });

  it('places no mark on a drawn side of other content, and says why', async () => {
    const view = card('f'.repeat(40));
    const excerpt = await view.findByTestId('review-card-excerpt');
    expect((await within(excerpt).findByTestId('review-marks-state')).dataset.marksState).toBe(
      'other-content',
    );
    expect(within(excerpt).queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });
});
