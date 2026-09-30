// MIK-R34 at the real renderers: the per-hunk intent markers of the landed source-content view (the
// explorer's opened file, `Expand full file`, a card's and the lane's full file) and of the lane's
// focused windows. Every classification is a REAL served per-file body of MIK-L32's lane fixture world
// (hunkMarkers.capture-provenance.json), and every diff draws the exact blob texts that body names;
// only `fetch` is stubbed. The workspace's marker scope is a plain value here, so each case asserts
// what the reader sees and what a followed marker asks the workspace to select.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  ReviewChangedFile,
  ReviewResult,
  ReviewSourceExpansion,
  ReviewSourceInventory,
  ReviewSourceSide,
} from '../../data/review';
import type { ReviewFileClassification } from '../../data/reviewLane';
import type { ReviewTreesResult } from '../../data/reviewTrees';
import { ExpressionCards } from './ExpressionCards';
import { expressionCards } from './focusedCards';
import { type IntentMarkerScopeValue, IntentMarkerScope } from './intentMarkerScope';
import { LaneFileFocus } from './LaneFileFocus';
import { SourceContent, type SourceMarkers } from './SourceContent';

type Scenario =
  'precuration' | 'curated' | 'before_unread' | 'after_unread' | 'both_unread' | 'partial';
type Body = {
  file_classification: ReviewFileClassification;
  texts: Record<'before' | 'after', string | null>;
};
const bodies = JSON.parse(
  readFileSync(
    path.join(
      path.dirname(new URL(import.meta.url).pathname),
      'hunkMarkers.classifier.captured.json',
    ),
    'utf8',
  ),
) as Record<Scenario, Record<string, Body>>;
const BEFORE_TREE = 'b'.repeat(40);
const AFTER_TREE = 'a'.repeat(40);
const task = { repo: 'agents-remember', master: 'm', leaf: 'l' };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function side(file: ReviewFileClassification, name: 'before' | 'after', text: string | null) {
  const recorded = file.sides.find((one) => one.side === name)!;
  if (text === null) return { state: 'absent', detail: 'no entry', truncated: false } as const;
  return {
    state: 'present',
    text,
    detail: 'the complete content',
    object_id: recorded.blob,
    byte_length: text.length,
    truncated: false,
  } satisfies ReviewSourceSide;
}

// The landed source-content answer for one fixture file: its exact blob texts and object ids.
function expansionOf(body: Body, over: Partial<ReviewSourceExpansion> = {}): ReviewSourceExpansion {
  const file = body.file_classification;
  return {
    path: file.path,
    status: file.status as ReviewSourceExpansion['status'],
    mode_change: false,
    language: 'text',
    before: side(file, 'before', body.texts.before),
    after: side(file, 'after', body.texts.after),
    before_code_tree_id: BEFORE_TREE,
    after_code_tree_id: AFTER_TREE,
    currentness: 'current',
    currentness_detail: 'the bound pair',
    path_bound: 'requested_generation',
    path_bound_detail: 'listed',
    admission: 'changed',
    admission_detail: 'changed',
    reference: 'review:source-content',
    command: 'git cat-file blob',
    ...over,
  };
}

const requested: URL[] = [];

// The transport: the content read answers `expansion`; a lane file read answers `file` (only the lane
// focus asks it -- a view handed its classification never does).
function serve(expansion: ReviewSourceExpansion, file?: Body) {
  requested.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (address: string) => {
      const url = new URL(address, 'http://localhost');
      requested.push(url);
      const body = url.pathname.endsWith('/source-content')
        ? { state: 'content', operation: 'read', repository_id: task.repo, expansion }
        : url.searchParams.get('file') && file
          ? { state: 'trees', file_classification: file.file_classification }
          : null;
      if (body === null) throw new Error(`Unexpected request: ${address}`);
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
}

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

function entryOf(file: ReviewFileClassification): ReviewChangedFile {
  return {
    path: file.path,
    status: file.status as ReviewChangedFile['status'],
    content: 'text',
    mode_change: false,
  };
}

function open(
  scenario: Scenario,
  file: string,
  options: {
    scope?: IntentMarkerScopeValue | null;
    layout?: 'split' | 'inline';
    markers?: SourceMarkers;
    expansion?: Partial<ReviewSourceExpansion>;
  } = {},
) {
  const body = bodies[scenario][file];
  serve(expansionOf(body, options.expansion));
  const scope = options.scope === undefined ? scopeOf() : options.scope;
  const view = render(
    <IntentMarkerScope.Provider value={scope}>
      <SourceContent
        {...task}
        entry={entryOf(body.file_classification)}
        beforeCodeTreeId={BEFORE_TREE}
        afterCodeTreeId={AFTER_TREE}
        mode={options.layout ?? 'split'}
        markers={options.markers ?? { classification: body.file_classification }}
      />
    </IntentMarkerScope.Provider>,
  );
  return { view, scope, body };
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

describe('the marks of a full diff', () => {
  it("marks each owner hunk on the owner's side lines, a deletion in the before editor", async () => {
    const { view } = open('curated', 'pkg/lines.txt');
    // The whole file is one displayed region holding three owner hunks: each keeps its own mark.
    expect((await marks(view, 3)).map(described)).toEqual([
      ['2 intents', 'L2', 'L2', 'after editor'],
      ['attribution unknown', 'L4', 'L4', 'after editor'],
      ['unexplained', 'L6', 'none (after L5)', 'before editor'],
    ]);
    // Placed from the handed classification: no classification is read again.
    expect(requested.map((url) => url.pathname)).toEqual(['/api/review/intent/source-content']);
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
    const scope = scopeOf();
    const { view, body } = open('curated', 'pkg/lines.txt', { scope });
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
      { path: 'pkg/lines.txt', pane: 'file', hunk: '2:1:2:1' },
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
    await view.findByTestId('file-pane');
    expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it("gives a file one attribution-unknown note when neither side's knowledge is readable", async () => {
    const { view } = open('both_unread', 'pkg/a.py');
    const mark = await view.findByTestId('review-file-mark');
    expect(mark.dataset.mark).toBe('attribution_unknown');
    await view.findByTestId('diff-pane');
    expect(view.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it('marks nothing outside a tree comparison, for an unchanged file, or for other content', async () => {
    // A dataset review provides no scope: the landed view, and no classification is asked for.
    const dataset = open('curated', 'pkg/lines.txt', { scope: null, markers: {} }).view;
    await dataset.findByTestId('diff-pane');
    expect(dataset.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
    expect(dataset.queryByTestId('review-marks-state')).toBeNull();
    cleanup();
    // An unchanged realization-linked file has no hunks: nothing is classified or marked.
    const classify = vi.fn(() => null);
    const unchanged = open('curated', 'pkg/lines.txt', {
      scope: scopeOf({ classify }),
      markers: {},
      expansion: { status: 'unchanged' },
    }).view;
    await unchanged.findByTestId('diff-pane');
    expect([classify.mock.calls.length, unchanged.queryAllByTestId('review-hunk-mark')]).toEqual([
      0,
      [],
    ]);
    cleanup();
    // A classification of other bytes than the drawn ones places no mark, and says why.
    const body = bodies.curated['pkg/lines.txt'];
    const other = open('curated', 'pkg/lines.txt', {
      expansion: { after: { ...expansionOf(body).after, object_id: 'f'.repeat(40) } },
    }).view;
    expect((await other.findByTestId('review-marks-state')).dataset.marksState).toBe(
      'other-content',
    );
    expect(other.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
    cleanup();
    // The before object alone differing is other content too.
    const otherBefore = open('curated', 'pkg/lines.txt', {
      expansion: { before: { ...expansionOf(body).before, object_id: 'e'.repeat(40) } },
    }).view;
    expect((await otherBefore.findByTestId('review-marks-state')).dataset.marksState).toBe(
      'other-content',
    );
    expect(otherBefore.queryAllByTestId('review-hunk-mark')).toHaveLength(0);
  });

  it('says why a changed file a partial inventory does not list carries no marks', async () => {
    // Review R1 F5: the file may have changed, and its classification is not read -- said so.
    const classify = vi.fn(() => null);
    const partial = open('curated', 'pkg/lines.txt', {
      scope: scopeOf({ classify, listed: () => false, partial: true }),
      markers: {},
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
      scope: scopeOf({ listed: () => false, partial: false }),
      markers: {},
    }).view;
    await complete.findByTestId('diff-pane');
    expect(complete.queryByTestId('review-marks-state')).toBeNull();
  });

  it('names the hunks past a bounded text instead of dropping them silently', async () => {
    const body = bodies.curated['pkg/lines.txt'];
    const bounded = (name: 'before' | 'after') => ({
      ...expansionOf(body)[name],
      text: body.texts[name]!.split('\n').slice(0, 3).join('\n') + '\n',
      truncated: true,
    });
    const { view } = open('curated', 'pkg/lines.txt', {
      expansion: { before: bounded('before'), after: bounded('after') },
    });
    expect((await marks(view, 1))[0].textContent).toBe('2 intents');
    expect(view.getByTestId('review-marks-past-text').textContent).toContain('2 hunk(s)');
  });
});

describe('the return to a followed marker', () => {
  it('reopens its list, brings its hunk into view and focuses it', async () => {
    const scrolled = vi.fn();
    vi.stubGlobal('Element', Element);
    Element.prototype.scrollIntoView = scrolled;
    const settle = vi.fn();
    const scope = scopeOf({
      returning: { path: 'pkg/lines.txt', pane: 'file', hunk: '4:1:4:1' },
      settle,
    });
    const { view } = open('curated', 'pkg/lines.txt', { scope });
    const four = (await marks(view, 3))[1];
    await waitFor(() => expect(settle).toHaveBeenCalled());
    expect(document.activeElement).toBe(four);
    expect(four.getAttribute('aria-expanded')).toBe('true');
    expect(view.getByTestId('review-hunk-mark-panel').dataset.hunk).toBe('4:1:4:1');
    expect(scrolled.mock.contexts.some((host) => (host as Element).contains(four))).toBe(true);
    delete (Element.prototype as Partial<Element>).scrollIntoView;
  });

  it('keeps the returned mark focused while the pane settles, never against the reader', async () => {
    // Review R2-F1: a redraw after the reveal leaves focus on the body; the short hold gives it back.
    const scope = scopeOf({ returning: { path: 'pkg/lines.txt', pane: 'file', hunk: '4:1:4:1' } });
    const { view } = open('curated', 'pkg/lines.txt', { scope });
    const four = (await marks(view, 3))[1];
    await waitFor(() => expect(document.activeElement).toBe(four));
    four.blur();
    expect(document.activeElement).toBe(document.body);
    await waitFor(() => expect(document.activeElement).toBe(four));
    // The reader's own key ends the hold: focus the reader leaves on the body stays there.
    fireEvent.keyDown(document.body, { key: 'Escape' });
    four.blur();
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(document.activeElement).toBe(document.body);
  });

  it('does not reopen a pane the marker was not followed from', async () => {
    const scope = scopeOf({
      returning: { path: 'pkg/lines.txt', pane: 'lane-full', hunk: '4:1:4:1' },
    });
    const { view } = open('curated', 'pkg/lines.txt', { scope });
    await marks(view, 3);
    expect(view.queryByTestId('review-hunk-mark-panel')).toBeNull();
    expect(scope.settle).not.toHaveBeenCalled();
  });
});

describe("the lane's focused windows", () => {
  const inventory = (file: string): ReviewSourceInventory => ({
    state: 'measured',
    entries: [{ path: file, status: 'modified', content: 'text', mode_change: false }],
    listed_total: 1,
    detail: 'measured',
    partial: false,
    command: 'git diff',
    before_code_tree_id: BEFORE_TREE,
    after_code_tree_id: AFTER_TREE,
    unrepresentable_paths: [],
  });

  function focus(scope: IntentMarkerScopeValue) {
    const body = bodies.curated['pkg/lines.txt'];
    serve(expansionOf(body), body);
    return render(
      <IntentMarkerScope.Provider value={scope}>
        <LaneFileFocus
          task={{ ...task, comparison: 1 }}
          path="pkg/lines.txt"
          destination="unknown"
          inventory={inventory('pkg/lines.txt')}
          layout="split"
          gate={{ state: 'none', detail: 'not part of this check' }}
        />
      </IntentMarkerScope.Provider>,
    );
  }

  it('marks every hunk a window draws: the focused one and each neighbour its context shows', async () => {
    // The Unknown destination opens lines.txt on its line 4 replace; three context lines draw the
    // line 2 replace and the line 6 delete in the same window (MIK-L32 review F5).
    const view = focus(scopeOf());
    const windows = await view.findAllByTestId('review-lane-hunk');
    expect(windows.map((one) => one.dataset.before)).toEqual(['L4']);
    await waitFor(() =>
      expect(within(windows[0]).queryAllByTestId('review-hunk-mark')).toHaveLength(3),
    );
    expect(
      inHunkOrder(within(windows[0]).getAllByTestId('review-hunk-mark')).map(described),
    ).toEqual([
      ['2 intents', 'L2', 'L2', 'after editor'],
      ['attribution unknown', 'L4', 'L4', 'after editor'],
      ['unexplained', 'L6', 'none (after L5)', 'before editor'],
    ]);
    // A neighbour's list opens in its window, and follows from that window's pane.
    const scope = scopeOf();
    cleanup();
    const again = focus(scope);
    const window = (await again.findAllByTestId('review-lane-hunk'))[0];
    await waitFor(() =>
      expect(within(window).queryAllByTestId('review-hunk-mark')).toHaveLength(3),
    );
    fireEvent.click(inHunkOrder(within(window).getAllByTestId('review-hunk-mark'))[0]);
    fireEvent.click(within(window).getAllByTestId('review-hunk-mark-occurrence')[0]);
    expect(vi.mocked(scope.follow).mock.lastCall![0]).toEqual({
      path: 'pkg/lines.txt',
      pane: 'lane-window:4:1:4:1',
      hunk: '2:1:2:1',
    });
  });

  it('says what the full file shows of the hunks a focused view does not draw', async () => {
    // Twenty-two hunks of the focused class, past the six lines the content read returns: twenty
    // are listed (each past the text), two are named as not drawn.
    const body = bodies.curated['pkg/lines.txt'];
    const many = (bucket: 'attributed' | 'unexplained') => ({
      ...body,
      file_classification: {
        ...body.file_classification,
        bucket,
        hunks: Array.from({ length: 22 }, (_, index) => ({
          before: { start: 100 + 2 * index, count: 1 },
          after: { start: 100 + 2 * index, count: 1 },
          classification: 'attribution_unknown' as const,
          links: [],
          unknown: [],
        })),
      },
    });
    const note = async (bucket: 'attributed' | 'unexplained') => {
      serve(expansionOf(body), many(bucket));
      const view = render(
        <IntentMarkerScope.Provider value={scopeOf()}>
          <LaneFileFocus
            task={{ ...task, comparison: 1 }}
            path="pkg/lines.txt"
            destination="unknown"
            inventory={inventory('pkg/lines.txt')}
            layout="split"
            gate={{ state: 'none', detail: 'not part of this check' }}
          />
        </IntentMarkerScope.Provider>,
      );
      const text = (await view.findByTestId('review-lane-hunks-more')).textContent;
      cleanup();
      return text;
    };
    expect(await note('attributed')).toBe(
      '2 more hunk(s) of this class are not drawn here. Full file draws every changed region of ' +
        'the text its read returns, each owner hunk with its own mark.',
    );
    // A confirmed-unregistered file's hunks carry no marks of their own: the note says so.
    expect(await note('unexplained')).toContain('under the one mark this file carries.');
  });

  it('reopens the full file a marker was followed from, with its list and focus', async () => {
    const settle = vi.fn();
    const view = focus(
      scopeOf({ returning: { path: 'pkg/lines.txt', pane: 'lane-full', hunk: '2:1:2:1' }, settle }),
    );
    const full = await view.findByTestId('review-lane-full-file');
    expect(full.getAttribute('aria-expanded')).toBe('true');
    const expansion = await view.findByTestId('review-source-expansion');
    await waitFor(() => expect(settle).toHaveBeenCalled());
    const two = inHunkOrder(within(expansion).getAllByTestId('review-hunk-mark'))[0];
    expect([document.activeElement === two, two.getAttribute('aria-expanded')]).toEqual([
      true,
      'true',
    ]);
    // The lane's one classification read serves the windows and the full file.
    expect(requested.filter((url) => url.searchParams.get('file'))).toHaveLength(1);
  });
});

describe("a card's full file", () => {
  // The REAL served cards of the MIK-L31 scratch leaf (gitTrees.capture-provenance.json): two cards
  // of review_source_admission.py, at two ranges.
  const cardsBody = JSON.parse(
    readFileSync(
      path.join(path.dirname(new URL(import.meta.url).pathname), 'gitTrees.cards.captured.json'),
      'utf8',
    ),
  ) as ReviewTreesResult;
  const payload = (
    JSON.parse(
      readFileSync(
        path.join(path.dirname(new URL(import.meta.url).pathname), 'gitTrees.family.captured.json'),
        'utf8',
      ),
    ) as ReviewResult
  ).payload!;
  const entries = ['RLZ-CXH58B4W', 'RLZ-D43E5CF2'].map((id) =>
    cardsBody.entries!.find((entry) => entry.id === id)!,
  );

  function cards(scope: IntentMarkerScopeValue, openPath: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          state: 'refused',
          refusal: { code: 'x', detail: 'not part of this check', next_action: 'n' },
        }),
      })),
    );
    return render(
      <IntentMarkerScope.Provider value={scope}>
        <ExpressionCards
          payload={payload}
          read={{ phase: 'trees', trees: { ...cardsBody, entries } }}
          planning={new Map()}
          layout="split"
          onLayout={() => undefined}
          fullFile={false}
          onFullFile={() => undefined}
          openPath={openPath}
          onOpenPath={() => undefined}
        />
      </IntentMarkerScope.Provider>,
    );
  }

  it('reopens the card a marker was followed from, not the first card of its path', () => {
    const [first, second] = expressionCards(entries);
    expect(first.path).toBe(second.path);
    const back = cards(
      scopeOf({ returning: { path: second.path, pane: `card:${second.key}`, hunk: '1:1:1:1' } }),
      second.path,
    );
    const [one, two] = back.getAllByTestId('review-expression-card');
    expect(within(two).getByTestId('review-card-full-file-content')).toBeTruthy();
    expect(within(one).queryByTestId('review-card-full-file-content')).toBeNull();
    cleanup();
    // Without a return, the path opens in its first card, as it always did.
    const plain = cards(scopeOf(), first.path);
    const [landed] = plain.getAllByTestId('review-expression-card');
    expect(within(landed).getByTestId('review-card-full-file-content')).toBeTruthy();
  });
});

describe("a card's excerpt", () => {
  // The REAL before_unread classification of pkg/a.py: the before memory tree cannot be read, and the
  // after side's re-recorded ranges link the edit at line 2. The card is the one the entries read serves
  // for such a comparison: no before blob (the side is unavailable), the after range's text (lines 1-2).
  const body = bodies.before_unread['pkg/a.py'];
  const file = body.file_classification;
  const link = file.hunks[0].links[0];
  const payload = (
    JSON.parse(
      readFileSync(
        path.join(path.dirname(new URL(import.meta.url).pathname), 'gitTrees.family.captured.json'),
        'utf8',
      ),
    ) as ReviewResult
  ).payload!;
  type EntrySide = NonNullable<ReviewTreesResult['entries']>[number]['before'];
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
        <ExpressionCards
          payload={payload}
          read={{
            phase: 'trees',
            trees: { ...emptyTrees, entries: entry(afterBlob, options.before, options.change) },
          }}
          planning={new Map()}
          layout="split"
          onLayout={() => undefined}
          fullFile={false}
          onFullFile={() => undefined}
          openPath={undefined}
          onOpenPath={() => undefined}
        />
      </IntentMarkerScope.Provider>,
    );
  }
  const emptyTrees: ReviewTreesResult = {
    state: 'trees',
    repository_id: task.repo,
    master: task.master,
    leaf_id: task.leaf,
    knowledge_sides: [],
  };

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
    await new Promise((resolve) => setTimeout(resolve, 50));
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
