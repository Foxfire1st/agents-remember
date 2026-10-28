// MIK-R89 v2 rules 4, 6 and 10 on L181 #8 (reviewPage.test-utils.tsx): the status chip's states and
// its four-row popover, "reading…" in the chip's own place; the comparison selector's older number
// shown read-only with that number's reads; "Changes only | Full context" remembered across a
// remount; side by side from a 1,080 px content box.
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ReviewMovement, ReviewPayload } from '../../../data/review';
import { chipFacts, type ChipInput } from '../top-line/statusChipModel';
import { CODE_CONTEXT_KEY, reloadCodeContextForTests } from './codeContextPreference';
import { contentWidth, layoutForWidth, SIDE_BY_SIDE_MIN_PX } from './useReadingLayout';
import {
  isSelectionRead,
  L181_SUBJECT,
  pageReads,
  PLACE,
  renderPage,
  selectInvariant,
  waitForOpening,
  servePage,
  stubInViewAtFirstPaint,
  stubIntersection,
} from './reviewPage.test-utils';

const HASH = `#review?${new URLSearchParams({ repo: PLACE.repo, master: PLACE.master, leaf: PLACE.leaf })}`;

beforeEach(() => {
  window.localStorage.clear();
  reloadCodeContextForTests();
  window.history.replaceState(null, '', `/${HASH}`);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function opened() {
  await waitForOpening();
}

const movement = (binding_state: ReviewMovement['binding_state'], reason?: string): ReviewMovement => ({
  binding_state,
  statement: `The branch is ${binding_state}. More words follow.`,
  ...(reason ? { reason } : {}),
  moved_identities: [],
  successor_action: '',
});

function input(over: Partial<ChipInput> & { sync?: ReviewMovement; git?: ReviewMovement; stale?: boolean } = {}): ChipInput {
  const payload = {
    sync_movement: over.sync ?? null,
    external_git_movement: over.git ?? null,
    staleness: { state: over.stale ? 'stale' : 'not-measured', statement: 'The staleness owner said so. Then more.', moved: [] },
  } as unknown as Pick<ReviewPayload, 'sync_movement' | 'external_git_movement' | 'staleness'>;
  return {
    phase: 'reviewed',
    problem: null,
    payload,
    comparison: { number: 8, recorded_at: '2026-10-19T11:38:46+00:00' },
    latest: 8,
    now: new Date(2026, 9, 20, 12, 0),
    ...over,
  };
}

// A bound comparison for the task context's answer, so that a refresh carries its identity (ICR-R17).
const BOUND: NonNullable<ReviewPayload['comparison']> = {
  reference: 'r'.repeat(64),
  policy_version: 'recorded-two-snapshot-union/v1',
  binding_digest: '1'.repeat(64),
  selector_digest: 'e'.repeat(64),
  before_snapshot_digest: 'a'.repeat(64),
  after_snapshot_digest: 'b'.repeat(64),
  before_code_tree_id: '1'.repeat(40),
  after_code_tree_id: '2'.repeat(40),
  knowledge_compared: true,
};

describe('the status chip (rule 6)', () => {
  it('says what the owners measured, in their own first sentence', () => {
    expect(chipFacts(input())).toMatchObject({ tone: 'grey', label: 'branches: not measured' });
    const moved = chipFacts(input({ sync: movement('stale', 'Main moved by three commits. Rebase first.') }));
    expect(moved).toMatchObject({ tone: 'warning', label: 'branches: moved', reason: 'Main moved by three commits.' });
    expect(chipFacts(input({ stale: true }))).toMatchObject({ tone: 'warning', reason: 'The staleness owner said so.' });
    const current = chipFacts(input({ sync: movement('current'), git: movement('current') }));
    expect(current).toMatchObject({ tone: 'current', label: 'branches: current' });
    const failed = chipFacts(input({ phase: 'failed', problem: { token: 'x', code: 'down', detail: 'The host is down. Try later.' } as never }));
    expect(failed).toMatchObject({ tone: 'unavailable', label: 'unavailable', reason: 'The host is down.' });
    expect(chipFacts(input({ phase: 'loading' }))).toMatchObject({ tone: 'reading', label: 'reading…' });
    expect(chipFacts(input()).rows.map(([name]) => name)).toEqual([
      'Sync movement',
      'External Git movement',
      'Subject staleness',
      'Bound comparison',
    ]);
  });

  it("shows reading… in its own place during the task context's read, and opens its four-row popover", async () => {
    stubIntersection();
    const taskRead = (url: URL) => url.pathname === '/api/review/intent' && !url.searchParams.has('selectorId');
    const served = servePage(taskRead);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('review-status-label').textContent).toBe('reading…'));
    const chip = screen.getByTestId('review-status-chip');
    const className = chip.className;
    await act(async () => served.release());
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-status-label').textContent).toBe('branches: not measured'));
    expect(screen.getByTestId('review-status-chip')).toBe(chip);
    expect(chip.className).toBe(className);
    fireEvent.click(chip);
    const popover = await screen.findByTestId('review-status-popover');
    expect(within(popover).getAllByTestId('review-status-row')).toHaveLength(4);
    expect(within(popover).getByTestId('review-status-reason').textContent).not.toBe('');
  });

  // Rules 6 and 9, evidence 10: the chip sits right of the flexible title, so it keeps its place only
  // while nothing on its right changes width. The comparison's number arrives with the task context's
  // answer and the change set's ⊕ with the trees answer; their places are held from the first paint.
  it("holds the comparison's and the ⊕'s places from the first paint, so the chip keeps its own", async () => {
    stubIntersection();
    const served = servePage((url) => url.pathname === '/api/review/intent' && !url.searchParams.has('selectorId'));
    renderPage();
    const button = await screen.findByTestId('review-comparison-button');
    expect(button.textContent).toBe('Comparison … ▾');
    const chip = screen.getByTestId('review-status-chip');
    const anchor = screen.getByTestId('review-comparison');
    const slot = screen.getByTestId('review-change-set-add-slot');
    expect(slot.childElementCount).toBe(0);
    const label = button.firstElementChild!;
    // "Comparison #99" and the ⊕'s widest state, "✓ in tray", in their own fonts.
    expect([...label.classList]).toContain('min-w_14ch');
    expect([...slot.classList]).toEqual(expect.arrayContaining(['flex_none', 'min-w_calc(9ch_+_0.7rem_+_2px)']));
    const before = [chip, anchor, button, label, slot].map((node) => node.className);
    await act(async () => served.release());
    await opened();
    await waitFor(() => expect(button.textContent).toBe('Comparison #8 ▾'));
    expect(within(slot).getByTestId('review-change-set-add')).toBeTruthy();
    expect([
      screen.getByTestId('review-status-chip'),
      screen.getByTestId('review-comparison'),
      screen.getByTestId('review-comparison-button'),
      screen.getByTestId('review-change-set-add-slot'),
    ]).toEqual([chip, anchor, button, slot]);
    expect(button.firstElementChild).toBe(label);
    expect([chip, anchor, button, label, slot].map((node) => node.className)).toEqual(before);
  });

  // Rules 6, 9 and 11 with ICR-R17's refresh: the notice a refresh answers with (a sentence and two
  // 64-character digests) takes a row of its own under the line's controls. So it never widens the
  // line or moves the chip: one line ending in an ellipsis above 40rem, the whole notice in its title;
  // wrapped inside the line's width at 40rem and narrower.
  it("puts a refresh's generation notice on its own row under the controls, so the chip keeps its place", async () => {
    stubIntersection();
    servePage();
    // The task context's answer binds comparison 1…1, and the refresh's answer 2…2: the candidate
    // moved while the page was open.
    const passed = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (address: string | URL, init?: RequestInit) => {
        const answer = await passed(address, init);
        const url = new URL(String(address), 'http://localhost');
        if (url.pathname !== '/api/review/intent' || url.searchParams.has('selectorId')) return answer;
        const body = (await answer.json()) as { payload: ReviewPayload };
        const digit = url.searchParams.has('previousBindingDigest') ? '2' : '1';
        body.payload.comparison = { ...BOUND, binding_digest: digit.repeat(64) };
        const headers = { 'content-type': 'application/json' };
        return new Response(JSON.stringify(body), { status: 200, headers });
      }),
    );
    renderPage();
    await opened();
    const line = screen.getByTestId('review-top-line');
    const chip = screen.getByTestId('review-status-chip');
    const className = chip.className;
    const refresh = screen.getByTestId('review-refresh');
    expect(screen.queryByTestId('review-generation-notice')).toBeNull();
    fireEvent.click(refresh);
    const notice = await screen.findByTestId('review-generation-notice');
    expect(notice.dataset.generationState).toBe('superseded');
    await waitFor(() =>
      expect(screen.getByTestId('review-status-label').textContent).toBe('branches: not measured'),
    );
    expect(screen.getByTestId('review-status-chip')).toBe(chip);
    expect(chip.className).toBe(className);
    // The refresh control stays in the line beside the chip; the notice is the line's last item, a
    // whole row wide, and the line wraps at every width.
    const narrow = (name: string) => `[@media_(max-width:_40rem)]:${name}`;
    expect(refresh.parentElement).toBe(line);
    expect(notice.parentElement).toBe(line);
    expect([...line.classList]).toContain('flex-wrap_wrap');
    expect([...notice.classList]).toEqual(
      expect.arrayContaining([
        'order_1',
        'flex-b_100%',
        'min-w_0',
        'white-space_nowrap',
        'ov_hidden',
        'tov_ellipsis',
        narrow('white-space_normal'),
      ]),
    );
    // What it says is kept, in full in the DOM and in its title.
    expect(notice.textContent).toContain(`is now ${'2'.repeat(64)}`);
    expect(notice.title).toBe(notice.textContent?.replace('Comparison updated', 'Comparison updated: '));
  });

  // Failure and Recovery: a subject's failure is stated in its block; the chip keeps the branch state.
  it("keeps the task context's state when a subject's read fails, and states the failure in its slot", async () => {
    stubIntersection();
    const served = servePage(isSelectionRead);
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-status-label').textContent).toBe('branches: not measured'));
    const passed = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      if (isSelectionRead(url)) throw new TypeError('the subject read failed');
      return passed(address, init);
    }));
    await selectInvariant();
    const section = screen.getAllByTestId('review-subject-section').find((node) => node.dataset.subject === L181_SUBJECT)!;
    await waitFor(() => expect(within(section).getByTestId('review-reading-problem')).toBeTruthy());
    expect(screen.getByTestId('review-status-label').textContent).toBe('branches: not measured');
    expect(screen.getByTestId('review-status-chip').textContent).not.toMatch(/unavailable|reading…/);
    void served;
  });
});

describe('the comparison selector (rule 10)', () => {
  it('shows an older number read-only, with that number on its reads, and reads no subject there', async () => {
    stubIntersection();
    const served = servePage();
    renderPage();
    await opened();
    fireEvent.click(screen.getByTestId('review-comparison-button'));
    const options = await screen.findAllByTestId('review-comparison-option');
    expect(options.map((option) => option.dataset.number)).toEqual(['8', '7', '6', '5', '4', '3', '2', '1']);
    const before = pageReads(served.asked).length;
    fireEvent.click(options.find((option) => option.dataset.number === '7')!);
    await waitFor(() => expect(screen.getByTestId('review-older-comparison')).toBeTruthy());
    await opened();
    expect(screen.queryByTestId('review-expression-card')).toBeNull();
    const reads = pageReads(served.asked).slice(before);
    // The outline (entries), the summary and the trees are each read at #7, and none at another number.
    for (const route of ['/api/review/intent/entries', '/api/review/intent/summary', '/api/review/trees']) {
      const routeReads = reads.filter((url) => url.pathname === route);
      expect(routeReads.length).toBeGreaterThan(0);
      expect(routeReads.every((url) => url.searchParams.get('comparison') === '7')).toBe(true);
    }
    expect(reads.some((url) => url.searchParams.has('invariants'))).toBe(false);
    expect(reads.some((url) => url.searchParams.has('lane'))).toBe(true);
    expect(window.location.hash).toContain('comparison=7');
    const asked = served.asked.length;
    await selectInvariant();
    await waitFor(() => expect(screen.getByTestId('review-subject-slot').dataset.slotState).toBe('older'));
    expect(served.asked.slice(asked).some(isSelectionRead)).toBe(false);
    expect(screen.getByTestId('review-older-subject').textContent).toContain('#8');
  });

  // Rule 10: under #k the marks, the lane's classification and j/k read #k's own file list, and
  // Technical details says whose movement and limitation records it holds.
  it("reads #k's own file list under an older comparison and labels the latest's records", async () => {
    stubInViewAtFirstPaint();
    const ONLY = 'dashboard/src/panels/review/reading/only-in-7.ts';
    const served = servePage();
    const passed = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (address: string | URL, init?: RequestInit) => {
      const url = new URL(String(address), 'http://localhost');
      const answer = await passed(address, init);
      if (url.pathname !== '/api/review/trees' || url.searchParams.get('comparison') !== '7') return answer;
      const body = await answer.json();
      if (url.searchParams.has('lane')) {
        const file = { ...body.lane.unexplained_changes.files[0], path: ONLY };
        body.lane.unexplained_changes.files = [file];
        body.lane.paths = [{ path: ONLY, bucket: 'unexplained' }];
      }
      if (url.searchParams.get('file') === ONLY) body.file_classification.hunks = [
        { before: { start: 0, count: 0 }, after: { start: 1, count: 4 }, classification: 'unexplained', links: [], unknown: [] },
      ];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }));
    renderPage();
    await opened();
    fireEvent.click(screen.getByTestId('review-comparison-button'));
    const options = await screen.findAllByTestId('review-comparison-option');
    fireEvent.click(options.find((option) => option.dataset.number === '7')!);
    await waitFor(() => expect(screen.getByTestId('review-older-comparison')).toBeTruthy());
    fireEvent.click(screen.getByTestId('review-tree-lane'));
    await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.path).toBe(ONLY));
    fireEvent.wheel(screen.getByTestId('review-center-column'), { deltaY: 120 });
    await waitFor(() => expect(screen.getByTestId('review-lane-file').dataset.laneRead).toBe('ready'));
    // The classification of the path #7 changed is read at #7, and its hunk is drawn (a j/k stop).
    expect(served.asked.some((url) => url.searchParams.get('file') === ONLY && url.searchParams.get('comparison') === '7')).toBe(true);
    await waitFor(() => expect(within(screen.getByTestId('review-lane-file')).getAllByTestId('review-lane-hunk').length).toBe(1));
    const summary = screen.getByTestId('review-details').querySelector('summary')!.textContent!;
    expect(summary).toContain('comparison #7');
    expect(summary).toContain('movement and limitations of #8');
  });
});

describe('Changes only | Full context (rule 4)', () => {
  it('folds unchanged lines with three lines of context, opens the whole range, and keeps the choice', async () => {
    servePage();
    const first = renderPage();
    await opened();
    await selectInvariant();
    const excerpts = () => Array.from(document.querySelectorAll<HTMLElement>('[data-excerpt="diff"]'));
    await waitFor(() => expect(document.querySelector('[data-excerpt="diff"] .cm-collapsedLines')).not.toBeNull());
    expect(excerpts().every((node) => node.dataset.collapse === 'true')).toBe(true);
    fireEvent.click(screen.getByRole('radio', { name: 'Full context' }));
    await waitFor(() => expect(excerpts().every((node) => node.dataset.collapse === 'false')).toBe(true));
    await waitFor(() => expect(document.querySelector('[data-excerpt="diff"] .cm-collapsedLines')).toBeNull());
    expect(window.localStorage.getItem(CODE_CONTEXT_KEY)).toContain('full');
    first.unmount();
    reloadCodeContextForTests();
    renderPage();
    await opened();
    expect(screen.getByRole('radio', { name: 'Full context' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.queryByText('Expand full file')).toBeNull();
    expect(screen.queryByRole('combobox', { name: /layout/i })).toBeNull();
  });

  // Ruling d9a1ec8f item 2: the selected mode carries the mockup's amber selected state (styles.css
  // `.seg button.on`). The review's shared control look (ReviewLayout.tsx `reviewRoot`) outranks a
  // component's own `button[data-selected]` rule, so the switch is left out of it. jsdom has no
  // cascade; this reads the shared rule's selector back from its class and asks which buttons it reaches.
  it('gives whichever mode is selected the amber selected state, which the shared control look leaves alone', async () => {
    servePage();
    renderPage();
    await opened();
    const surface = screen.getByTestId('review-surface');
    const shared = [...surface.classList]
      .filter((name) => name.startsWith('[&_button:not('))
      .map((name) => name.slice(1, name.lastIndexOf(']:')).replaceAll('_', ' ').replaceAll('&', '[data-testid="review-surface"]'));
    expect(shared.length).toBeGreaterThan(0);
    const group = screen.getByTestId('review-code-context');
    for (const mode of ['Changes only', 'Full context']) {
      fireEvent.click(screen.getByRole('radio', { name: mode }));
      const selected = screen.getByRole('radio', { name: mode });
      await waitFor(() => expect(selected.getAttribute('aria-checked')).toBe('true'));
      expect(group.querySelectorAll('[data-selected]')).toEqual(expect.objectContaining({ length: 1 }));
      expect(selected.hasAttribute('data-selected')).toBe(true);
      for (const button of group.querySelectorAll('button'))
        for (const selector of shared) expect(button.matches(selector)).toBe(false);
    }
    expect([...group.classList]).toEqual(expect.arrayContaining(['[&_button[data-selected]]:c_amber']));
    expect([...group.classList].some((name) => name.startsWith('[&_button[data-selected]]:bg_color-mix(in_oklab,_token(colors.amber)'))).toBe(true);
    // The one remembered control (D230): the last choice is the stored one.
    expect(window.localStorage.getItem(CODE_CONTEXT_KEY)).toContain('full');
  });

  it('draws side by side from a 1,080 px content box', async () => {
    const node = document.createElement('div');
    node.style.paddingLeft = '10px';
    node.style.paddingRight = '10px';
    Object.defineProperty(node, 'clientWidth', { configurable: true, value: SIDE_BY_SIDE_MIN_PX + 20 });
    expect(contentWidth(node)).toBe(SIDE_BY_SIDE_MIN_PX);
    expect(layoutForWidth(contentWidth(node))).toBe('split');
    expect(layoutForWidth(SIDE_BY_SIDE_MIN_PX - 1)).toBe('inline');
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.hasAttribute('data-review-scroller') ? 1400 : 0;
    });
    servePage();
    renderPage();
    await opened();
    await selectInvariant();
    await waitFor(() => expect(document.querySelector('[data-excerpt="diff"] .cm-mergeView')).not.toBeNull());
  });
});

// D401: the top line compared with the mockup by eye (styles.css `.shell`, `.topline`, `.menu`,
// `.statuspop`, `.label`; app.js `topline`, `cmpMenu`, `statusPop`). jsdom has no layout, so these
// cases hold the declarations and the structure the look depends on; the browser leg measures it.
describe('the top line beside the mockup (D401)', () => {
  const classes = (node: Element) => [...node.classList];
  const narrow = (name: string) => `[@media_(max-width:_40rem)]:${name}`;
  // A recorded time of the current year, as the mockup writes it (statusChipModel.ts `localTime`).
  const local = (iso: string) => {
    const at = new Date(iso);
    const two = (value: number) => String(value).padStart(2, '0');
    return `${two(at.getMonth() + 1)}-${two(at.getDate())} ${two(at.getHours())}:${two(at.getMinutes())}`;
  };

  it("adds no inset of its own to the shell's above 40rem, and keeps the stacked page's", async () => {
    servePage();
    renderPage();
    const surface = await screen.findByTestId('review-surface');
    expect(classes(surface).filter((name) => /^(p|px|py|pt|pb|pl|pr|ps|pe)_/.test(name))).toEqual([]);
    expect(classes(surface)).toEqual(expect.arrayContaining([narrow('px_0.6rem'), narrow('py_0.8rem_0')]));
  });

  it("lists the comparisons under the leaf's heading, each with its number, its recorded time where a read gives it, and its state", async () => {
    // The page's current date, 2026-10-20: #8, recorded that year, is written without its year.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 20, 12, 0));
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-comparison-button').textContent).toBe('Comparison #8 ▾'));
    fireEvent.click(screen.getByTestId('review-comparison-button'));
    const menu = await screen.findByTestId('review-comparison-menu');
    expect(within(menu).getByTestId('review-comparison-heading').textContent).toBe('Comparisons of L181');
    const options = within(menu).getAllByTestId('review-comparison-option');
    const cells = options.map((option) => [...option.children].map((cell) => cell.textContent));
    expect(cells[0]).toEqual(['#8', local('2026-10-19T11:38:46+00:00'), 'latest']);
    expect(cells.slice(1)).toEqual([7, 6, 5, 4, 3, 2, 1].map((number) => [`#${number}`, '', 'read-only']));
    expect(classes(options[0])).toEqual(expect.arrayContaining(['d_grid', 'grid-tc_3rem_1fr_auto']));
    expect(options[0].getAttribute('aria-current')).toBe('true');
    expect(classes(menu.parentElement!)).toContain('w_min(20.625rem,_calc(100vw_-_1.5rem))');
    expect(within(menu).getByText(/^Choosing an older number shows that comparison read-only\.$/)).toBeTruthy();
  });

  it('opens the status popover under the comparison, with the label, the ruled rows and the quieter body', async () => {
    stubIntersection();
    servePage();
    renderPage();
    await opened();
    await waitFor(() => expect(screen.getByTestId('review-status-label').textContent).toBe('branches: not measured'));
    const button = screen.getByTestId('review-comparison-button');
    const box = (width: number) => () => ({ x: 0, y: 0, top: 0, left: 0, right: width, bottom: 20, width, height: 20, toJSON: () => ({}) }) as DOMRect;
    vi.spyOn(button, 'getBoundingClientRect').mockImplementation(box(137));
    vi.spyOn(screen.getByTestId('review-status-chip'), 'getBoundingClientRect').mockImplementation(box(216));
    fireEvent.click(screen.getByTestId('review-status-chip'));
    const dialog = await screen.findByTestId('review-status-popover');
    const popover = dialog.parentElement!;
    // Placed against the comparison's button, as the mockup's popover hangs from the comparison anchor.
    await waitFor(() => expect(popover.style.getPropertyValue('--trigger-width')).toBe('137px'));
    const heading = within(dialog).getByTestId('review-status-heading');
    expect(heading.textContent).toBe('Branch status · comparison #8');
    expect(heading.tagName).not.toBe('STRONG');
    expect(classes(heading)).toEqual(expect.arrayContaining(['c_cyan', 'fs_0.68rem', 'tt_uppercase', 'ls_0.1em']));
    expect(classes(popover)).toEqual(expect.arrayContaining(['w_min(23.75rem,_calc(100vw_-_1.5rem))', 'lh_1.5']));
    expect(classes(popover).some((name) => name.startsWith('[&_td]:bd-b_1px_solid_color-mix(in_oklab,_token(colors.grid)'))).toBe(true);
    expect(classes(popover)).toEqual(expect.arrayContaining(['[&_td]:fs_0.72rem', '[&_p]:c_muted', '[&_p]:fs_0.72rem']));
    expect(classes(popover).some((name) => name.startsWith('[&_td:first-child]:c_color-mix(in_oklab,_token(colors.muted)'))).toBe(true);
    expect(within(dialog).getAllByTestId('review-status-row')).toHaveLength(4);
  });
});
