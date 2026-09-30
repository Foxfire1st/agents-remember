// MIK-R31 card states and grouping. The entries start from the REAL served cards body of the
// converted scratch leaf (gitTrees.cards.captured.json, provenance gitTrees.capture-provenance.json);
// the states a real leaf did not produce (unresolved, unavailable, missing rationale, shared range)
// are derived from those real entries by changing exactly the field the state is about.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ReviewPayload, ReviewResult } from '../../data/review';
import type { ReviewTreeEntry, ReviewTreesResult } from '../../data/reviewTrees';
import { ExpressionCards } from './ExpressionCards';
import { cardCounts, cardScope, expressionCards, type CardScope } from './focusedCards';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const cardsBody = captured<ReviewTreesResult>('gitTrees.cards.captured.json');
const payload = captured<ReviewResult>('gitTrees.family.captured.json').payload as ReviewPayload;
const real = (id: string): ReviewTreeEntry =>
  structuredClone(cardsBody.entries!.find((entry) => entry.id === id)!);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// The workspace owns the open path; this harness holds it the same way.
function Harness({
  entries,
  seed,
  scope,
}: {
  entries: ReviewTreeEntry[];
  seed?: string;
  scope?: CardScope;
}) {
  const [openPath, setOpenPath] = useState<string | null | undefined>(undefined);
  return (
    <ExpressionCards
      payload={payload}
      read={{ phase: 'trees', trees: { ...cardsBody, entries } }}
      seed={seed}
      planning={new Map()}
      layout="split"
      onLayout={() => undefined}
      fullFile={false}
      onFullFile={() => undefined}
      openPath={openPath}
      onOpenPath={setOpenPath}
      scope={scope}
    />
  );
}

function mount(entries: ReviewTreeEntry[], seed?: string, scope?: CardScope) {
  return render(<Harness entries={entries} seed={seed} scope={scope} />);
}

const card = (view: ReturnType<typeof mount>, entries: string) =>
  view.getAllByTestId('review-expression-card').find((node) => node.dataset.entries === entries)!;

describe('each card state', () => {
  it('draws a changed range as its real diff and an unchanged range once, labelled unchanged', () => {
    const view = mount([real('RLZ-CXH58B4W'), real('RLZ-D43E5CF2')]);
    const changed = card(view, 'RLZ-CXH58B4W');
    expect(changed.dataset.cardChange).toBe('changed');
    expect(within(changed).getByTestId('review-card-excerpt').dataset.excerpt).toBe('diff');
    expect(within(changed).getByTestId('diff-pane')).toBeTruthy();
    expect(changed.textContent).toContain('Changed range');
    const unchanged = card(view, 'RLZ-D43E5CF2');
    expect(within(unchanged).getByTestId('review-card-excerpt').dataset.excerpt).toBe('unchanged');
    expect(within(unchanged).queryByTestId('diff-pane')).toBeNull();
    expect(within(unchanged).getAllByTestId('file-pane')).toHaveLength(1);
    expect(unchanged.textContent).toContain('Unchanged');
    // The unchanged card is excluded from the changed count.
    const section = view.getByTestId('review-expression-cards');
    expect([section.dataset.changedCount, section.dataset.unchangedCount]).toEqual(['1', '1']);
  });

  it('shows an unresolved side with its reason and never a guessed range or a diff', () => {
    const entry = real('RLZ-CXH58B4W');
    entry.after = {
      ...entry.after,
      state: 'unresolved',
      start_line: undefined,
      end_line: undefined,
      excerpt: undefined,
      reason: 'the symbol does not resolve uniquely in this blob',
    };
    entry.change = 'undetermined';
    const view = mount([entry]);
    const shown = card(view, 'RLZ-CXH58B4W');
    expect(within(shown).getByTestId('review-card-after').dataset.sideState).toBe('unresolved');
    expect(within(shown).getByTestId('review-card-after').textContent).toContain(
      'unresolved: the symbol does not resolve uniquely',
    );
    expect(shown.dataset.afterRange).toBe('unresolved');
    expect(within(shown).queryByTestId('diff-pane')).toBeNull();
    expect(within(shown).getByTestId('review-card-excerpt').dataset.excerpt).toBe('separate');
    expect(view.getByTestId('review-expression-cards').dataset.changedCount).toBe('0');
  });

  it('labels an unavailable side unavailable, distinct from a file absent on that side', () => {
    const unavailable = real('RLZ-CXH58B4W');
    unavailable.before = {
      ...unavailable.before,
      state: 'unavailable',
      start_line: undefined,
      end_line: undefined,
      excerpt: undefined,
      reason: 'the code tree 804b3fd2 cannot be read',
    };
    unavailable.change = 'undetermined';
    const absent = real('RLZ-D43E5CF2');
    absent.id = 'RLZ-ABSENT00';
    absent.before = {
      ...absent.before,
      state: 'absent',
      start_line: undefined,
      end_line: undefined,
    };
    absent.change = 'changed';
    absent.after = { ...absent.after, start_line: 300, end_line: 326 };
    const view = mount([unavailable, absent]);
    const side = within(card(view, 'RLZ-CXH58B4W')).getByTestId('review-card-before');
    expect(side.dataset.sideState).toBe('unavailable');
    expect(side.textContent).toContain('unavailable: the code tree 804b3fd2 cannot be read');
    const missing = within(card(view, 'RLZ-ABSENT00')).getByTestId('review-card-before');
    expect(missing.dataset.sideState).toBe('absent');
    expect(missing.textContent).toContain('no file on this side');
    expect(missing.textContent).not.toContain('unavailable');
  });

  it('names a missing rationale as a gap and never writes text of its own', () => {
    const entry = real('RLZ-D43E5CF2');
    entry.after = { ...entry.after, rationale: undefined };
    entry.before = { ...entry.before, rationale: undefined };
    const view = mount([entry]);
    const shown = card(view, 'RLZ-D43E5CF2');
    expect(within(shown).queryByTestId('review-card-rationale')).toBeNull();
    expect(within(shown).getByTestId('review-card-rationale-missing').textContent).toBe(
      'No authored rationale is recorded for RLZ-D43E5CF2.',
    );
  });

  it('gives a proof entry its facet in place of a role and a rationale', () => {
    const view = mount([real('PRF-7Q3M5K')]);
    const proof = card(view, 'PRF-7Q3M5K');
    expect(proof.dataset.cardKind).toBe('proof');
    expect(proof.textContent).toContain('Test proof · facet');
    expect(within(proof).getByTestId('review-card-rationale').textContent).toBe(
      real('PRF-7Q3M5K').after.facet,
    );
    expect(within(proof).getByTestId('review-card-voice').textContent).toContain('proof facet');
    expect(proof.textContent).toContain('added in this leaf');
  });
});

describe('grouping by (path, range)', () => {
  it('keeps two regions of one file as two cards with their own rationale', () => {
    const cards = expressionCards([real('RLZ-9EC7B6PN'), real('RLZ-D43E5CF2')]);
    expect(cards.map((one) => one.path)).toEqual([cards[0].path, cards[0].path]);
    expect(cards).toHaveLength(2);
    expect(cards[0].after.rationale).not.toBe(cards[1].after.rationale);
  });

  it('shares one card between members at the same path and range, each with its own rationale', () => {
    const other = real('RLZ-D43E5CF2');
    other.id = 'RLZ-SHARED01';
    other.invariant = 'INV-ZZZZZZZZ';
    other.after = { ...other.after, rationale: 'Another member names the same region.' };
    const view = mount([real('RLZ-D43E5CF2'), other]);
    const shared = card(view, 'RLZ-D43E5CF2,RLZ-SHARED01');
    const voices = within(shared).getAllByTestId('review-card-voice');
    expect(voices.map((voice) => voice.dataset.invariant)).toEqual([
      'INV-9ECAFVTQ',
      'INV-ZZZZZZZZ',
    ]);
    expect(within(shared).getAllByTestId('review-card-rationale')[1].textContent).toBe(
      'Another member names the same region.',
    );
    expect(view.getAllByTestId('review-expression-card')).toHaveLength(1);
  });

  it('never merges entries whose side did not resolve, and counts unchanged apart from changed', () => {
    const one = real('RLZ-D43E5CF2');
    one.after = { ...one.after, state: 'unresolved', start_line: undefined, end_line: undefined };
    one.change = 'undetermined';
    const two = structuredClone(one);
    two.id = 'RLZ-OTHER001';
    const cards = expressionCards([one, two, real('RLZ-CXH58B4W'), real('RLZ-NM6160PK')]);
    expect(cards).toHaveLength(4);
    expect(cardCounts(cards)).toEqual({ cards: 4, changed: 1, unchanged: 1, undetermined: 2 });
  });

  it('orders by the family order, with the selected member first', () => {
    const entries = cardsBody.entries!;
    const byId = expressionCards(entries).map((one) => one.entries[0].invariant);
    expect(byId).toEqual([...byId].sort());
    const seed = real('RLZ-NM6160PK').invariant_key;
    expect(expressionCards(entries, seed)[0].entries[0].id).toBe('RLZ-NM6160PK');
  });
});

describe('review fixes', () => {
  it('opens one full file, in the card that asked, with one read (F1)', () => {
    const reads: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string) => {
        reads.push(address);
        return {
          ok: true,
          status: 200,
          json: async () => ({
            state: 'refused',
            refusal: { code: 'x', detail: 'd', next_action: 'n' },
          }),
        } as Response;
      }),
    );
    const view = mount([real('RLZ-CXH58B4W'), real('RLZ-D43E5CF2')]);
    const [first, second] = view.getAllByTestId('review-expression-card');
    expect(first.dataset.path).toBe(second.dataset.path);
    fireEvent.click(within(second).getByTestId('review-card-full-file'));
    expect(view.getAllByTestId('review-card-full-file-content')).toHaveLength(1);
    expect(within(second).getByTestId('review-card-full-file-content')).toBeTruthy();
    expect(within(first).queryByTestId('review-card-full-file-content')).toBeNull();
    expect(reads.filter((url) => url.includes('/source-content'))).toHaveLength(1);
    fireEvent.click(within(second).getByTestId('review-card-full-file'));
    expect(view.queryAllByTestId('review-card-full-file-content')).toHaveLength(0);
  });

  it("says a bounded roster's cards cover only the loaded members and points to the walk (F2)", () => {
    const walk = captured<ReviewResult>('familyReview.walkFinal.captured.json').payload!;
    const bounded = walk.family_context!.entries.find((entry) => cardScope(entry) !== undefined)!;
    const scope = cardScope(bounded)!;
    const sides = [bounded.before, bounded.after];
    expect(scope.total).toBe(Math.max(...sides.map((side) => side.members_total)));
    expect(scope.loaded).toBeLessThan(scope.total);
    // R2-5: counted on one side, so the loaded count can never exceed the total even when the two
    // sides load disjoint members.
    const disjoint = structuredClone(bounded);
    disjoint.before.members_total = 2;
    disjoint.after.members_total = 2;
    disjoint.before.members = bounded.after.members.slice(0, 2);
    disjoint.after.members = bounded.after.members.slice(2, 4).map((member) => ({
      ...member,
      state: 'content_not_on_page' as const,
    }));
    const across = cardScope(disjoint)!;
    expect(across.loaded).toBeLessThanOrEqual(across.total);
    expect(across.total).toBe(2);
    const complete = walk.family_context!.entries.find((entry) => entry !== bounded)!;
    expect(cardScope(complete)).toBeUndefined();
    const view = mount([real('RLZ-CXH58B4W')], undefined, scope);
    const line = view.getByTestId('review-cards-scope').textContent ?? '';
    expect(line).toContain(`the loaded ${scope.loaded} of ${scope.total} members`);
    expect(line).toContain('roster walk');
  });

  it('marks an entry that is not current on its side with its MIK-R03 state (F4)', () => {
    const entry = real('RLZ-D43E5CF2');
    entry.after = {
      ...entry.after,
      currentness: 'stale',
      currentness_reason: 'the range content differs from the recorded content',
    };
    const view = mount([entry]);
    const mark = within(card(view, 'RLZ-D43E5CF2')).getByTestId('review-card-currentness');
    expect(mark.dataset.currentness).toBe('stale');
    expect(mark.textContent).toBe('RLZ-D43E5CF2 stale on the after side');
    const current = mount([real('RLZ-NM6160PK')]);
    expect(
      within(card(current, 'RLZ-NM6160PK')).queryByTestId('review-card-currentness'),
    ).toBeNull();
  });
});
