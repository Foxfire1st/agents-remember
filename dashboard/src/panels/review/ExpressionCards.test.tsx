// MIK-R31 card states on the review page (MIK-R89 v2 W1: one card-read entry, one card). The entries
// start from the REAL served cards body of the converted scratch leaf (gitTrees.cards.captured.json,
// provenance gitTrees.capture-provenance.json); the states a real leaf did not produce (unresolved,
// unavailable, missing rationale) are derived from those real entries by changing exactly the field
// the state is about. The cards are the change-set model's locations (changeSetOrder.ts), drawn by
// `ReviewBlockCard` as the reading column draws them.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { EditorView } from '@codemirror/view';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ReviewTreeEntry, ReviewTreesResult } from '../../data/reviewTrees';
import { DiffPane } from '../changeset/DiffPane';
import { BlockCards } from './reading/blockCards.test-utils';
import { renderPage, servePage, stubInViewAtFirstPaint } from './reading/reviewPage.test-utils';
import { resetCardCachesForTests } from '../../data/reviewTrees';

const captured = <T,>(name: string): T =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as T;
const cardsBody = captured<ReviewTreesResult>('gitTrees.cards.captured.json');
const real = (id: string): ReviewTreeEntry =>
  structuredClone(cardsBody.entries!.find((entry) => entry.id === id)!);
const comparison = cardsBody.comparison!;

afterEach(() => {
  resetCardCachesForTests();
  cleanup();
  vi.unstubAllGlobals();
});

function mount(entries: ReviewTreeEntry[]) {
  return render(<BlockCards entries={entries} comparison={comparison} />);
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
    // A changed range is told by its head's counts; the mockup draws no badge for it (D401).
    expect(within(changed).getByTestId('review-block-lines').textContent).toMatch(/^\+\d+ −\d+$/);
    expect(changed.textContent).not.toContain('Changed range');
    const unchanged = card(view, 'RLZ-D43E5CF2');
    expect(within(unchanged).getByTestId('review-card-excerpt').dataset.excerpt).toBe('unchanged');
    expect(within(unchanged).queryByTestId('diff-pane')).toBeNull();
    expect(within(unchanged).getAllByTestId('file-pane')).toHaveLength(1);
    expect(unchanged.textContent).toContain('Unchanged');
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
    expect(within(proof).getByTestId('review-block-role').textContent).toBe('proof');
    expect(within(proof).getByTestId('review-card-rationale').textContent).toBe(
      real('PRF-7Q3M5K').after.facet,
    );
    expect(within(proof).getByTestId('review-card-voice').textContent).toContain('proof facet');
    expect(proof.textContent).toContain('added in this leaf');
  });
});

describe('one entry, one card (W1)', () => {
  it('keeps two entries at the same path and range as two cards, each with its own rationale', () => {
    const other = real('RLZ-D43E5CF2');
    other.id = 'RLZ-SHARED01';
    other.after = { ...other.after, rationale: 'Another entry names the same region.' };
    const view = mount([real('RLZ-D43E5CF2'), other]);
    expect(card(view, 'RLZ-D43E5CF2')).toBeTruthy();
    expect(within(card(view, 'RLZ-SHARED01')).getByTestId('review-card-rationale').textContent).toBe(
      'Another entry names the same region.',
    );
  });
});

describe('review fixes', () => {
  // Rule 4: "Expand full file" goes on the review page; "Changes only | Full context" is the one
  // control. A card offers no Full file of its own and reads no source content.
  it('offers no Full file of its own (rule 4)', () => {
    const reads: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (address: string) => {
      reads.push(address);
      return { ok: true, status: 200, json: async () => ({ state: 'refused', refusal: { code: 'x', detail: 'd', next_action: 'n' } }) } as Response;
    }));
    const view = mount([real('RLZ-CXH58B4W'), real('RLZ-D43E5CF2')]);
    for (const one of view.getAllByTestId('review-expression-card')) {
      expect(within(one).queryByTestId('review-card-full-file')).toBeNull();
      expect(one.textContent).not.toMatch(/Full file/);
    }
    expect(reads.filter((url) => url.includes('/source-content'))).toHaveLength(0);
  });

  // R89 evidence 8 counts the range: a card labelled L1697–1817 draws 121 rows. Every served excerpt
  // ends with the newline of its last line, which starts no line of its own (finding 9).
  it('draws exactly the lines of its range, never an empty row after the last one', async () => {
    const view = mount([real('RLZ-CXH58B4W'), real('RLZ-D43E5CF2')]);
    const lines = (host: Element, selector: string) =>
      EditorView.findFromDOM(host.querySelector<HTMLElement>(selector)!)!.state.doc.lines;
    const changed = within(card(view, 'RLZ-CXH58B4W')).getByTestId('diff-pane');
    await waitFor(() => expect(changed.querySelector('.cm-editor.cm-merge-b')).toBeTruthy());
    // L194–213 on both sides: 20 lines each.
    expect([lines(changed, '.cm-editor.cm-merge-a'), lines(changed, '.cm-editor.cm-merge-b')]).toEqual([20, 20]);
    const unchanged = within(card(view, 'RLZ-D43E5CF2')).getByTestId('file-pane');
    await waitFor(() => expect(unchanged.querySelector('.cm-editor')).toBeTruthy());
    // L131–157: 27 lines.
    expect(lines(unchanged, '.cm-editor')).toBe(27);
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

// D401 (the page compared with the mockup by eye): a card as the approved mockup draws it
// (notes/reference/review-page-mockup-D367: app.js:274-288, styles.css:83-96 and 202-239, diff.js).
const classes = (node: Element) => [...node.classList];

describe('the card as the mockup draws it (D401)', () => {
  // Ruling 719ade09: the head's "+N −N" in mint and alarm red at the head's 0.72rem (`.bhead .pm`),
  // each sign kept in its text; the mockup draws no badge for a changed range.
  it('draws the head counts in mint and alarm red, each with its sign, and no changed-range badge', () => {
    const view = mount([real('RLZ-CXH58B4W')]);
    const shown = card(view, 'RLZ-CXH58B4W');
    const lines = within(shown).getByTestId('review-block-lines');
    expect(lines.textContent).toMatch(/^\+\d+ −\d+$/);
    expect(classes(lines)).toContain('fs_0.72rem');
    expect(classes(within(lines).getByText(/^\+\d+$/))).toContain('c_mint');
    expect(classes(within(lines).getByText(/^−\d+$/))).toContain('c_alarm');
    expect(shown.dataset.cardChange).toBe('changed');
    expect(shown.textContent).not.toContain('Changed range');
  });

  // repair-layout's handed-over card-head assertions (ruling 719ade09), on L181 #8's page: every head
  // with counts draws each count as its own element in its colour, together at the head's 0.72rem.
  it("draws every card head's counts coloured on the page", async () => {
    const COUNTS = /\+\d+ −\d+/;
    const expectCounts = (host: HTMLElement) => {
      const added = within(host).getByText(/^\+\d+$/);
      const removed = within(host).getByText(/^−\d+$/);
      expect(classes(added)).toContain('c_mint');
      expect(classes(removed)).toContain('c_alarm');
      expect(added.parentElement).toBe(removed.parentElement);
      expect(added.parentElement?.textContent).toMatch(new RegExp(`^${COUNTS.source}$`));
      return added.parentElement!;
    };
    window.localStorage.clear();
    stubInViewAtFirstPaint();
    servePage();
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId('review-expression-card').length).toBeGreaterThan(0));
    const heads = screen.getAllByTestId('review-block-lines').filter((node) => COUNTS.test(node.textContent ?? ''));
    expect(heads.length).toBeGreaterThan(0);
    for (const head of heads) expect(classes(expectCounts(head))).toContain('fs_0.72rem');
  });

  // Ruling d9a1ec8f item 5 under MIK-R86 v2 rule 19, by the mechanism ruled for the conditions (its
  // item 7): one line per entry, "<entry id> <rationale> · <invariant> · <role>[ · <planning mark>]", its
  // overflow cut with an ellipsis and its whole text in its title; a click opens that one line in full,
  // in place. A state other than the normal one is a visible badge on the line, and a revised entry's
  // earlier rationale is a line of its own under it, "Before: …", opening the same way. Nothing an entry
  // says is in the page for a screen reader only, and nothing is a closed disclosure.
  it('introduces each entry in one line that opens in place, its states visible and its earlier rationale a line of its own', () => {
    const revised = real('RLZ-CXH58B4W');
    const earlier = 'An earlier rationale of this range.';
    revised.before = { ...revised.before, rationale: earlier };
    const said = revised.after.rationale!;
    const view = mount([revised, real('PRF-7Q3M5K')]);
    const voice = within(card(view, 'RLZ-CXH58B4W')).getByTestId('review-card-voice');
    const line = within(voice).getByTestId('review-card-voice-line');
    expect(line.textContent).toBe(`${revised.id} ${said} · ${revised.invariant} · ${revised.after.role}`);
    expect(within(line).getByTestId('review-card-rationale').textContent).toBe(said);
    expect(classes(line)).toEqual(expect.arrayContaining(['white-space_nowrap', 'tov_ellipsis', 'ov_hidden']));
    expect(line.title).toBe(line.textContent);
    // The states are badges beside the cut text; the earlier rationale is a line of its own.
    const badge = within(voice).getByTestId('review-card-revised');
    expect(badge.textContent).toBe('rationale revised');
    expect(line.contains(badge)).toBe(false);
    const before = within(voice).getByTestId('review-card-voice-before');
    expect(before.textContent).toBe(`Before: ${earlier}`);
    expect(before.title).toBe(before.textContent);
    expect(classes(before)).toEqual(expect.arrayContaining(['white-space_nowrap', 'tov_ellipsis', 'ov_hidden']));
    const added = within(card(view, 'PRF-7Q3M5K')).getByTestId('review-card-state');
    expect(added.textContent).toBe('added in this leaf');
    // Nothing is for a screen reader only, and nothing is closed.
    for (const rationales of view.getAllByTestId('review-card-rationales')) {
      expect(rationales.querySelector('.sr_true')).toBeNull();
      expect(rationales.querySelector('details')).toBeNull();
    }
    // A click opens that one line only.
    expect([line, before].map((one) => one.getAttribute('aria-expanded'))).toEqual(['false', 'false']);
    fireEvent.click(line);
    expect([line, before].map((one) => one.getAttribute('aria-expanded'))).toEqual(['true', 'false']);
    expect(classes(line)).not.toContain('white-space_nowrap');
    expect(classes(before)).toContain('white-space_nowrap');
    fireEvent.click(before);
    expect(before.getAttribute('aria-expanded')).toBe('true');
    expect(classes(before)).not.toContain('white-space_nowrap');
  });

  // The mockup's `.bnote` (styles.css:210-211): the introduction is plain lines of text the reader can
  // select (MIK-R188 takes highlighted text from the page), not buttons with the page's control look.
  // A line opens in place on a click, or on Enter or Space while focused; a click that ends with a text
  // selection inside the line leaves it as it is.
  it('draws the introduction as plain lines whose text can be selected, opening on a click or a key', () => {
    const revised = real('RLZ-CXH58B4W');
    const earlier = 'An earlier rationale of this range.';
    revised.before = { ...revised.before, rationale: earlier };
    const said = revised.after.rationale!;
    const view = mount([revised]);
    const rationales = within(card(view, 'RLZ-CXH58B4W')).getByTestId('review-card-rationales');
    for (const button of rationales.querySelectorAll('button'))
      expect([button.textContent?.includes(said), button.textContent?.includes('Before:')]).toEqual([false, false]);
    const line = within(rationales).getByTestId('review-card-voice-line');
    const before = within(rationales).getByTestId('review-card-voice-before');
    for (const one of [line, before]) {
      expect(one.tagName).not.toBe('BUTTON');
      expect([one.getAttribute('role'), one.tabIndex]).toEqual(['button', 0]);
    }
    // The keyboard opens and closes the focused line.
    fireEvent.keyDown(line, { key: 'Enter' });
    expect(line.getAttribute('aria-expanded')).toBe('true');
    fireEvent.keyDown(line, { key: ' ' });
    expect(line.getAttribute('aria-expanded')).toBe('false');
    // A click that ends with a selection inside the line does not toggle it; a plain click does.
    const selection = window.getSelection()!;
    const range = document.createRange();
    range.selectNodeContents(within(line).getByTestId('review-card-rationale'));
    selection.removeAllRanges();
    selection.addRange(range);
    fireEvent.click(line);
    expect(line.getAttribute('aria-expanded')).toBe('false');
    selection.removeAllRanges();
    fireEvent.click(line);
    expect(line.getAttribute('aria-expanded')).toBe('true');
    // The same for the earlier rationale's line.
    const inBefore = document.createRange();
    inBefore.selectNodeContents(before);
    selection.addRange(inBefore);
    fireEvent.click(before);
    expect(before.getAttribute('aria-expanded')).toBe('false');
    selection.removeAllRanges();
  });

  // The diff starts just below the introduction: where both sides are regions, the diff's own line
  // numbers show both ranges and the side lines are for a screen reader; a side that is no region
  // keeps its reason on screen.
  it('starts the diff just below the introduction, keeping a side without a region on screen', () => {
    const unresolved = real('RLZ-CXH58B4W');
    unresolved.id = 'RLZ-UNRESOLV';
    unresolved.after = { ...unresolved.after, state: 'unresolved', start_line: undefined, end_line: undefined, excerpt: undefined, reason: 'no unique symbol' };
    unresolved.change = 'undetermined';
    const view = mount([real('RLZ-CXH58B4W'), unresolved]);
    const regions = within(card(view, 'RLZ-CXH58B4W')).getByTestId('review-card-before').parentElement!;
    expect(classes(regions)).toContain('sr_true');
    const reason = within(card(view, 'RLZ-UNRESOLV')).getByTestId('review-card-after').parentElement!;
    expect(classes(reason)).not.toContain('sr_true');
  });
});

// The diff's look (D401; mockup styles.css:216-239, diff.js uniRow/sideRow): a removed row is one red
// band and an added row one green band, with − or + in the marker column; a side with no line
// opposite the other's is hatched; the words inside a band are not boxed again. Each rule of the
// review page's pane is read from its class and its selector is matched against the drawn editor, so
// the rule reaches the rows CodeMirror draws.
describe("the diff's look as the mockup draws it (D401)", () => {
  const rules = (host: HTMLElement) =>
    classes(host).flatMap((name) => {
      const rule = /^\[&_(.+)\]:([a-z-]+)_(.+)$/.exec(name);
      return rule ? [{ selector: rule[1].replaceAll('_', ' '), property: rule[2], value: rule[3] }] : [];
    });
  const ruleOf = (host: HTMLElement, property: string, value: string) =>
    rules(host).filter((rule) => rule.property === property && rule.value.includes(value));
  const reaches = (host: HTMLElement, selector: string) =>
    host.querySelectorAll(selector.replace(/::before$/, '')).length > 0;

  async function pane(layout: 'split' | 'inline') {
    const view = render(<BlockCards entries={[real('RLZ-CXH58B4W')]} comparison={comparison} layout={layout} />);
    const host = within(card(view, 'RLZ-CXH58B4W')).getByTestId('diff-pane');
    await waitFor(() => expect(host.querySelector('.cm-editor')).toBeTruthy());
    return host;
  }

  it('bands the changed rows red and green with their markers, side by side', async () => {
    const host = await pane('split');
    const removed = ruleOf(host, 'bg', 'rgba(90,_37,_37,_0.5)');
    const added = ruleOf(host, 'bg', 'rgba(37,_90,_37,_0.42)');
    expect(removed.some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-merge-a'))).toBe(true);
    expect(added.some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-merge-b'))).toBe(true);
    const minus = ruleOf(host, 'content', '"−"');
    const plus = ruleOf(host, 'content', '"+"');
    expect(minus.some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-merge-a'))).toBe(true);
    expect(plus.some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-merge-b'))).toBe(true);
    expect(ruleOf(host, 'bg', 'repeating-linear-gradient(135deg').map((rule) => rule.selector)).toContain('.cm-mergeSpacer');
    expect(ruleOf(host, 'bg', 'none').map((rule) => rule.selector)).toContain('.cm-changedText');
  });

  // A removed row's text is alarm red, as the mockup's `.del` row is (styles.css:95, 224-226; diff.js
  // uniRow/sideRow): the rule is on the row, so a token with a colour of its own keeps it. The
  // change-set page's pane is left as it is.
  it("colours a removed row's text alarm red in both views, and only on the review page", async () => {
    const split = await pane('split');
    expect(ruleOf(split, 'c', 'alarm').some((rule) => rule.selector === '.cm-merge-a .cm-changedLine' && reaches(split, rule.selector))).toBe(true);
    cleanup();
    const inline = await pane('inline');
    expect(
      ruleOf(inline, 'c', 'alarm').some((rule) => rule.selector === '.cm-deletedChunk > .cm-deletedLine' && reaches(inline, rule.selector)),
    ).toBe(true);
    cleanup();
    const page = render(<DiffPane before={'a\nb\n'} after={'a\nc\n'} language="text" mode="split" />);
    expect(ruleOf(page.getByTestId('diff-pane'), 'c', 'alarm')).toEqual([]);
  });

  it('bands a removed row red with its marker in the unified diff', async () => {
    const host = await pane('inline');
    expect(host.querySelector('.cm-deletedChunk')).toBeTruthy();
    expect(ruleOf(host, 'bg', 'rgba(90,_37,_37,_0.5)').some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-deletedChunk'))).toBe(true);
    expect(ruleOf(host, 'content', '"−"').some((rule) => reaches(host, rule.selector) && rule.selector.includes('.cm-deletedChunk'))).toBe(true);
    expect(ruleOf(host, 'content', '"+"').some((rule) => reaches(host, rule.selector))).toBe(true);
  });
});

// Rule 9, ruling fb5d04f6: a shown diff pane keeps its content and height until its new content is
// drawn, and never passes through 0. WP8 on A4-F4 (and A4-F3): while a pane was built again, what
// stood in it was an empty `div.cm-mergeView` (0 px, no editors, no lines) for a side-by-side diff,
// and a unified diff's editor without its line numbers, its code 31 px further left. Here a card's
// pane is built again for a change of view (all lines instead of changes only): what stands in the
// pane once its editor is gone is its whole last drawing, until the next editor is in it.
describe('a pane built again keeps its whole last drawing', () => {
  async function drawingWhileBuilt(layout: 'split' | 'inline') {
    const entries = [real('RLZ-CXH58B4W')];
    const view = render(<BlockCards entries={entries} comparison={comparison} layout={layout} />);
    const pane = within(card(view, 'RLZ-CXH58B4W')).getByTestId('diff-pane');
    await waitFor(() => expect(pane.querySelector('.cm-editor .cm-line')).toBeTruthy());
    const drawn = pane.firstElementChild!;
    let held: { html: string; editors: number; lines: number; numbers: number } | null = null;
    // Runs after the pane's keeper (created later), as soon as its stand-in is in the pane.
    const watcher = new MutationObserver(() => {
      const kept = pane.querySelector(':scope > [data-held-snapshot]');
      if (!kept || held) return;
      held = {
        html: kept.innerHTML,
        editors: kept.querySelectorAll('.cm-editor').length,
        lines: kept.querySelectorAll('.cm-line').length,
        numbers: kept.querySelectorAll('.cm-gutters .cm-gutterElement').length,
      };
    });
    watcher.observe(pane, { childList: true });
    const last = {
      html: drawn.innerHTML,
      editors: drawn.querySelectorAll('.cm-editor').length,
      lines: drawn.querySelectorAll('.cm-line').length,
      numbers: drawn.querySelectorAll('.cm-gutters .cm-gutterElement').length,
    };
    view.rerender(<BlockCards entries={entries} comparison={comparison} layout={layout} collapse={false} />);
    await waitFor(() => expect(held).not.toBeNull());
    await waitFor(() => expect(pane.querySelector(':scope > [data-held-snapshot]')).toBeNull());
    watcher.disconnect();
    expect(pane.querySelector('.cm-editor')).not.toBeNull();
    return { last, held: held! };
  }

  it('keeps a side-by-side pane\'s two editors, their lines, gutters and spacers', async () => {
    const { last, held } = await drawingWhileBuilt('split');
    expect(last.editors).toBe(2);
    expect(last.lines).toBeGreaterThan(0);
    expect(last.numbers).toBeGreaterThan(0);
    expect({ editors: held.editors, lines: held.lines, numbers: held.numbers }).toEqual({
      editors: last.editors,
      lines: last.lines,
      numbers: last.numbers,
    });
    expect(held.html).toBe(last.html);
  });

  it("keeps a unified pane's line numbers beside its lines", async () => {
    const { last, held } = await drawingWhileBuilt('inline');
    expect(last.numbers).toBeGreaterThan(0);
    expect({ lines: held.lines, numbers: held.numbers }).toEqual({ lines: last.lines, numbers: last.numbers });
    expect(held.html).toBe(last.html);
  });
});
