// MIK-R33 in the family tree: badges, breakdown, order and j/k traversal over the store-authored
// comparison's served bodies (triage.capture-provenance.json) -- the comparison whose badge facts
// mcp/tests/test_review_change_kinds.py asserts. The tree is the real component inside a reviewer
// keymap zone; selecting a node updates the selection as the workspace does.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resolveKeymap, writeKeymapPreferences } from '../../data/keymap/preferences';
import type { ReviewFamilyContext, ReviewResult } from '../../data/review';
import { FamilyTree, type FamilySelection } from './FamilyTree';
import type { MarkTarget } from './hunkMarkers';
import { IntentMarkerScope, type IntentMarkerScopeValue } from './intentMarkerScope';
import { TREE_ORDER_KEY, readTreeOrder, treeOrderStore } from './triageOrderPreference';

const captured = (name: string): ReviewResult =>
  JSON.parse(
    readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
  ) as ReviewResult;
const contextOf = (name: string): ReviewFamilyContext => captured(name).payload!.family_context!;

const family = contextOf('triage.family.captured.json');
const page = contextOf('triage.familyPage.captured.json');
const shared = contextOf('triage.shared.captured.json');
const dataset = contextOf('familyReview.complete.captured.json');

let selected: FamilySelection[] = [];

function Harness({
  context,
  zone = true,
  comparison = true,
}: {
  context: ReviewFamilyContext;
  zone?: boolean;
  comparison?: boolean;
}) {
  const [selection, setSelection] = useState<FamilySelection | null>(null);
  const [query, setQuery] = useState('');
  const tree = (
    <FamilyTree
      context={context}
      selection={selection}
      onSelect={(next) => {
        selected.push(next);
        setSelection(next);
      }}
      onRosterNext={() => undefined}
      query={query}
      onQuery={setQuery}
      embedded
      tree={comparison}
    />
  );
  if (!zone) return tree;
  return (
    <div data-kbzone="review" data-testid="zone">
      {tree}
      <textarea data-testid="notes" />
      <div contentEditable data-testid="editable" />
      {/* A focusable, non-editable control in the terminal zone (its host in the cockpit). */}
      <button type="button" data-kbzone="pty" data-testid="pty">
        terminal
      </button>
    </div>
  );
}

const J = { key: 'j', code: 'KeyJ' };
const K = { key: 'k', code: 'KeyK' };

// The record a tree node shows: the invariant in a member's statement, the family's label.
const recordOf = (node: Element | null) =>
  node?.textContent?.match(/INV-[A-Z]{6}|FAM-F\d{5}/)?.[0] ?? null;
// A node's accessible name and description, as the role query computes them (whitespace collapsed).
const flat = (text: string) => text.replace(/\s+/g, ' ').trim();
function announcement(node: HTMLElement): { name: string; description: string } {
  let name = '';
  let description = '';
  within(node.parentElement!).getAllByRole('button', {
    name: (computed, element) => {
      if (element === node) name = computed;
      return true;
    },
    description: (computed, element) => {
      if (element === node) description = computed;
      return true;
    },
  });
  return { name: flat(name), description: flat(description) };
}

const current = (container: HTMLElement) =>
  recordOf(container.querySelector('[data-tree-node][aria-current="true"]'));

function press(times: number, key: typeof J, container: HTMLElement): (string | null)[] {
  const visited: (string | null)[] = [];
  for (let step = 0; step < times; step += 1) {
    fireEvent.keyDown(document.activeElement ?? container, key);
    visited.push(current(container));
  }
  return visited;
}

beforeEach(() => {
  selected = [];
  localStorage.clear();
  treeOrderStore.getState().setOrder('triage');
});
afterEach(cleanup);

describe('change-kind badges', () => {
  it('shows every member occurrence its delivered kind and marks, and the family its guarantee', () => {
    const view = render(<Harness context={family} />);
    const kinds = Object.fromEntries(
      view
        .getAllByTestId('review-family-member-open')
        .map((node) => [
          recordOf(node),
          `${node.dataset.changePrimary}:${within(node).getByTestId('review-change-badge').dataset.changeMarks}`,
        ]),
    );
    expect(kinds).toEqual({
      'INV-AAAAAA': 'intent:implementation unknown',
      'INV-BBBBBB': 'implementation:',
      'INV-CCCCCC': 'implementation:',
      'INV-DDDDDD': 'implementation:test unknown',
      'INV-EEEEEE': 'unchanged:',
      'INV-FFFFFF': 'unchanged:',
      'INV-GGGGGG': 'unknown:',
      'INV-HHHHHH': 'intent:membership',
      'INV-KKKKKK': 'unchanged:',
    });
    // Both revision rows of the revised member carry the occurrence's badge.
    const revised = view
      .getAllByTestId('review-family-member-open')
      .filter((node) => recordOf(node) === 'INV-AAAAAA');
    expect(revised.map((node) => node.dataset.changePrimary)).toEqual(['intent', 'intent']);
    expect(view.getByTestId('review-guarantee-change').dataset.changeKind).toBe('unchanged');
    expect(view.getByTestId('review-family-breakdown').textContent).toBe(
      'Members by change: 2 intent · 3 impl · 0 membership · 1 unknown of 9',
    );
  });

  it('shows the shared member membership where it joined and unchanged elsewhere', () => {
    const view = render(<Harness context={shared} />);
    const kinds = view.getAllByTestId('review-family').map((node) => [
      recordOf(within(node).getByTestId('review-family-open')),
      within(node)
        .getAllByTestId('review-family-member-open')
        .find((member) => recordOf(member) === 'INV-FFFFFF')?.dataset.changePrimary,
      within(node).getByTestId('review-guarantee-change').dataset.changeKind,
    ]);
    expect(kinds).toEqual([
      ['FAM-F00001', 'unchanged', 'unchanged'],
      ['FAM-F00002', 'membership', 'intent'],
    ]);
  });

  it('never calls a reworded revision unchanged: intent, noted, beside a genuinely unchanged member', () => {
    const view = render(<Harness context={shared} />);
    const joined = view.getAllByTestId('review-family')[1];
    const nodes = within(joined).getAllByTestId('review-family-member-open');
    const reworded = nodes.find((node) => recordOf(node) === 'INV-PPPPPP')!;
    const unchanged = nodes.find((node) => recordOf(node) === 'INV-EEEEEE')!;
    expect(reworded.dataset.changePrimary).toBe('intent');
    expect(within(reworded).getByTestId('review-change-mark').textContent).toBe(
      'same revision; text differs',
    );
    expect(unchanged.dataset.changePrimary).toBe('unchanged');
    expect(within(unchanged).queryByTestId('review-change-mark')).toBeNull();
  });

  it("says why an unknown is unknown, visibly and in the node's description, never its name", () => {
    const view = render(<Harness context={family} />);
    const unknown = view
      .getAllByTestId('review-family-member-open')
      .find((node) => recordOf(node) === 'INV-GGGGGG')!;
    const why = within(unknown).getByTestId('review-change-why');
    expect(why.textContent).toMatch(
      /^change kind unknown: RLZ-G00001 supplies no range on the (before|after) side of pkg\/a\.py: it is recorded at blob 0000000000, but the \w+ blob is \w{10} \(recorded_blob_mismatch/,
    );
    expect(why.textContent).toMatch(/\(and 1 more\)$/);
    // The name is the subject only (review R3-1); the reason is described, once.
    const { name, description } = announcement(unknown);
    expect(name).toMatch(/INV-GGGGGG/);
    expect(name).not.toMatch(/unknown/);
    expect(description).toMatch(/change kind unknown: RLZ-G00001/);
    expect(description.match(/change kind unknown/g)).toHaveLength(1);
    // Only members with an unknown carry a reason line: G (primary), and A and D, whose after-side
    // entries in the changed files are unresolved (the unconditional mark).
    const withReason = view
      .getAllByTestId('review-change-why')
      .map((line) => recordOf(line.closest('[data-tree-node]')));
    expect(new Set(withReason)).toEqual(new Set(['INV-AAAAAA', 'INV-DDDDDD', 'INV-GGGGGG']));
  });

  it('states each fact once per node: the text note and the guarantee badge are not repeated', () => {
    const view = render(<Harness context={shared} />);
    const [first, joined] = view.getAllByTestId('review-family');
    const reworded = within(joined)
      .getAllByTestId('review-family-member-open')
      .find((node) => recordOf(node) === 'INV-PPPPPP')!;
    expect(reworded.textContent).toContain('same revision; text differs');
    expect(reworded.textContent).not.toContain('same revision · text differs');
    // The unchanged guarantee is stated by the badge; the block keeps only its label.
    expect(within(first).getByTestId('review-guarantee-change').dataset.changeKind).toBe(
      'unchanged',
    );
    const block = within(first).getByTestId('review-family-guarantee');
    expect(block.textContent).toMatch(/^Joint guarantee/);
    expect(block.textContent).not.toContain('Joint guarantee · unchanged');
  });

  it('never reads an undescribed member as unchanged, and says why (SYNTHETIC facts)', () => {
    const [first, joined] = shared.entries;
    const reworded = joined.change_kinds!.members.find(
      (one) => one.invariant === 'INV-PPPPPP',
    )!.member_id;
    const undescribed: ReviewFamilyContext = {
      ...shared,
      entries: [
        first,
        {
          ...joined,
          change_kinds: {
            ...joined.change_kinds!,
            members: joined.change_kinds!.members.filter((one) => one.member_id !== reworded),
          },
        },
      ],
    };
    const view = render(<Harness context={undescribed} />);
    const node = view
      .getAllByTestId('review-family-member-open')
      .find((one) => recordOf(one) === 'INV-PPPPPP')!;
    expect(node.dataset.changePrimary).toBe('unknown');
    expect(within(node).getByTestId('review-change-why').textContent).toBe(
      'change kind unknown: no change facts were delivered for this member',
    );
    expect(view.getAllByTestId('review-family-breakdown')[1].textContent).toMatch(
      /0 intent · 0 impl · 1 membership · 1 unknown of 3$/,
    );
  });

  it('says why an unknown guarantee is unknown (SYNTHETIC facts)', () => {
    const [entry] = family.entries;
    const detail = 'the after memory tree has family records that do not parse: FAM-F00001-x.json';
    const unknown: ReviewFamilyContext = {
      ...family,
      entries: [
        {
          ...entry,
          change_kinds: { ...entry.change_kinds!, guarantee: 'unknown', guarantee_detail: detail },
        },
      ],
    };
    const view = render(<Harness context={unknown} />);
    const row = view.getByTestId('review-family-open');
    expect(within(row).getByTestId('review-guarantee-change').dataset.changeKind).toBe('unknown');
    expect(within(row).getByTestId('review-change-why').textContent).toBe(
      `guarantee unknown: ${detail}`,
    );
    // An unknown guarantee is itself a stop.
    expect(row.dataset.changePrimary).toBe('unknown');
  });

  it('adds nothing to a dataset review: no badge, no breakdown, no controls, landed order', () => {
    const view = render(<Harness context={dataset} comparison={false} />);
    expect(view.queryByTestId('review-change-badge')).toBeNull();
    expect(view.queryByTestId('review-family-breakdown')).toBeNull();
    expect(view.queryByTestId('review-triage-controls')).toBeNull();
    const before = view
      .getAllByTestId('review-family-member-open')
      .map((node) => node.dataset.revision);
    fireEvent.keyDown(view.getAllByTestId('review-family-open')[0], J);
    expect(selected).toEqual([]);
    expect(
      view.getAllByTestId('review-family-member-open').map((node) => node.dataset.revision),
    ).toEqual(before);
  });
});

describe('triage order', () => {
  it('lists changes first and keeps unchanged siblings; the control switches to authored order', () => {
    const view = render(<Harness context={family} />);
    const order = () => view.getAllByTestId('review-family-member-open').map(recordOf);
    expect(order()).toEqual([
      'INV-AAAAAA',
      'INV-AAAAAA',
      'INV-HHHHHH',
      'INV-BBBBBB',
      'INV-CCCCCC',
      'INV-DDDDDD',
      'INV-GGGGGG',
      'INV-EEEEEE',
      'INV-FFFFFF',
      'INV-KKKKKK',
    ]);
    fireEvent.click(view.getByTestId('review-tree-order'));
    expect(view.getByTestId('review-tree-order').dataset.order).toBe('authored');
    expect(order()).toEqual([
      'INV-AAAAAA',
      'INV-AAAAAA',
      'INV-BBBBBB',
      'INV-CCCCCC',
      'INV-DDDDDD',
      'INV-EEEEEE',
      'INV-FFFFFF',
      'INV-GGGGGG',
      'INV-KKKKKK',
      'INV-HHHHHH',
    ]);
    // A browser-local preference, read back on the next load.
    expect(localStorage.getItem(TREE_ORDER_KEY)).toBe('authored');
    expect(readTreeOrder(localStorage)).toBe('authored');
  });

  it('falls back to triage order when storage is unavailable', () => {
    const throwing = {
      getItem: () => {
        throw new Error('storage is disabled');
      },
    };
    expect(readTreeOrder(throwing)).toBe('triage');
    expect(readTreeOrder(undefined)).toBe('triage');
  });
});

describe('next and previous change', () => {
  it('visits every non-unchanged occurrence in displayed order and stays at either end', () => {
    const view = render(<Harness context={family} />);
    view.getAllByTestId('review-family-open')[0].focus();
    // The family row's guarantee is unchanged, so it is not a stop; the revised member is one stop.
    expect(press(6, J, view.container)).toEqual([
      'INV-AAAAAA',
      'INV-HHHHHH',
      'INV-BBBBBB',
      'INV-CCCCCC',
      'INV-DDDDDD',
      'INV-GGGGGG',
    ]);
    expect(recordOf(document.activeElement)).toBe('INV-GGGGGG');
    // At the end the selection is brought into view, where the sticky bar's status is read.
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    fireEvent.keyDown(document.activeElement!, J);
    expect(current(view.container)).toBe('INV-GGGGGG');
    expect(view.getByTestId('review-change-status').textContent).toMatch(/No later change/);
    expect(scrolled.mock.contexts).toEqual([
      view.container.querySelector('[data-tree-node][aria-current="true"]'),
    ]);
    // jsdom has no scrollIntoView of its own; the stub is removed again.
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    expect(view.getByTestId('review-change-status').getAttribute('aria-live')).toBe('polite');
    // The status sits in the triage bar that stays in view (sticky) while the tree scrolls.
    expect(
      view.getByTestId('review-triage-bar').contains(view.getByTestId('review-change-status')),
    ).toBe(true);
    expect(press(5, K, view.container)).toEqual([
      'INV-DDDDDD',
      'INV-CCCCCC',
      'INV-BBBBBB',
      'INV-HHHHHH',
      'INV-AAAAAA',
    ]);
    // Backward lands on the occurrence's first row, the before revision.
    const revisedRows = view
      .getAllByTestId('review-family-member-open')
      .filter((node) => recordOf(node) === 'INV-AAAAAA');
    expect(revisedRows).toHaveLength(2);
    expect(document.activeElement).toBe(revisedRows[0]);
    expect(revisedRows[0].getAttribute('aria-current')).toBe('true');
    fireEvent.keyDown(document.activeElement!, K);
    expect(current(view.container)).toBe('INV-AAAAAA');
    expect(view.getByTestId('review-change-status').textContent).toMatch(/No earlier change/);
    // The visible controls move the same way.
    fireEvent.click(view.getByTestId('review-next-change'));
    expect(current(view.container)).toBe('INV-HHHHHH');
    fireEvent.click(view.getByTestId('review-previous-change'));
    expect(current(view.container)).toBe('INV-AAAAAA');
  });

  it('visits only the families an active filter shows, including a changed guarantee row', () => {
    const view = render(<Harness context={shared} />);
    fireEvent.change(view.getByTestId('review-family-filter'), {
      target: { value: 'Two, revised' },
    });
    expect(view.getAllByTestId('review-family')).toHaveLength(1);
    // Focus on a control that is not a tree node: with nothing selected, next starts at the top.
    view.getByTestId('review-next-change').focus();
    expect(press(3, J, view.container)).toEqual(['FAM-F00002', 'INV-PPPPPP', 'INV-FFFFFF']);
    fireEvent.keyDown(document.activeElement!, J);
    expect(view.getByTestId('review-change-status').textContent).toMatch(/No later change/);
    expect(selected.every((one) => one.familyId === shared.entries[1].family_id)).toBe(true);
  });

  it('stops at the continuation control past the returned members of a partial family', () => {
    const view = render(<Harness context={page} />);
    const stops = new Set(
      view
        .getAllByTestId('review-family-member-open')
        .filter((node) => node.dataset.changePrimary !== 'unchanged')
        .map((node) => node.dataset.occurrence),
    ).size;
    view.getByTestId('review-family-open').focus();
    press(stops, J, view.container);
    const last = current(view.container);
    fireEvent.keyDown(document.activeElement!, J);
    // The selection stays; focus moves to the continuation control; the message says why.
    expect(current(view.container)).toBe(last);
    expect(document.activeElement?.getAttribute('data-testid')).toBe('review-family-roster-next');
    expect(view.getByTestId('review-change-status').textContent).toMatch(
      /Further members of FAM-F00001 are not yet returned/,
    );
    expect(view.getByTestId('review-family-breakdown').dataset.partial).toBe('true');
    // From the control, next moves on: this tree has nothing after the family.
    fireEvent.keyDown(document.activeElement!, J);
    expect(view.getByTestId('review-change-status').textContent).toMatch(/No later change/);
    expect(current(view.container)).toBe(last);
  });

  it('is inert in text fields, contenteditable regions, the terminal zone and outside the reviewer', () => {
    const view = render(<Harness context={family} />);
    // jsdom does not implement `isContentEditable`; a browser reports it for this element.
    Object.defineProperty(view.getByTestId('editable'), 'isContentEditable', { value: true });
    const targets = [
      view.getByTestId('review-family-filter'),
      view.getByTestId('notes'),
      view.getByTestId('editable'),
      view.getByTestId('pty'),
    ];
    for (const target of targets) {
      target.focus();
      fireEvent.keyDown(target, J);
      fireEvent.keyDown(target, K);
    }
    // A modified key is not the chord either.
    fireEvent.keyDown(view.getAllByTestId('review-family-open')[0], { ...J, ctrlKey: true });
    expect(selected).toEqual([]);
    cleanup();
    const outside = render(<Harness context={family} zone={false} />);
    outside.getAllByTestId('review-family-open')[0].focus();
    fireEvent.keyDown(document.activeElement!, J);
    expect(selected).toEqual([]);
  });

  it('is registered with the keymap owner, listed for the ? reference and rebindable', () => {
    const bindings = resolveKeymap(null).bindings.filter((entry) => entry.zones.includes('review'));
    expect(bindings.map(({ commandId, label }) => [commandId, label])).toEqual([
      ['review.nextChange', 'j'],
      ['review.previousChange', 'k'],
    ]);
    writeKeymapPreferences({ bindings: { 'review.nextChange': 'N' } });
    try {
      const view = render(<Harness context={family} />);
      view.getAllByTestId('review-family-open')[0].focus();
      fireEvent.keyDown(document.activeElement!, J);
      expect(selected).toEqual([]);
      fireEvent.keyDown(document.activeElement!, { key: 'n', code: 'KeyN' });
      expect(current(view.container)).toBe('INV-AAAAAA');
      expect(view.getByTestId('review-next-change').textContent).toBe('↓ Next change (N)');
    } finally {
      writeKeymapPreferences({});
    }
  });
});

// -- MIK-L33 x MIK-L34 (merge round): one statement per fact on a named family's member ------------
//
// SYNTHETIC facts over the store-authored body: INV-GGGGGG's change kind is unknown (its served
// reason), and its membership is made unknown too, so both lines are drawn. The followed marker's
// target is a SYNTHETIC scope value; the real follow, over MIK-L34's re-captured comparison, is in
// ReviewSurface.triage.test.tsx.
describe('one statement per fact on a member', () => {
  const [entry] = family.entries;
  const G = entry.change_kinds!.members.find((one) => one.invariant === 'INV-GGGGGG')!;
  const row = [...entry.before.members, ...entry.after.members].find(
    (one) => one.member_id === G.member_id,
  )!;
  const reason = 'the after memory tree has family records that do not parse: FAM-F00001-x.json';
  const context: ReviewFamilyContext = {
    ...family,
    entries: [
      {
        ...entry,
        change_kinds: {
          ...entry.change_kinds!,
          members: entry.change_kinds!.members.map((one) =>
            one.member_id === G.member_id
              ? { ...one, membership: 'unknown', membership_reasons: [reason] }
              : one,
          ),
        },
      },
    ],
  };
  const target: MarkTarget = {
    invariant: 'INV-GGGGGG',
    invariantKey: row.invariant_id ?? '',
    familyKey: entry.family_id,
    memberRevisionKey: row.invariant_revision_id,
    family: 'FAM-F00001',
    state: 'membership_unknown',
    reason: 'not every family record of the after knowledge could be read, so whether …',
  };
  const scope = (followed: MarkTarget | null): IntentMarkerScopeValue => ({
    comparison: 1,
    listed: () => false,
    partial: false,
    classify: () => null,
    peek: () => undefined,
    origin: followed ? { path: 'pkg/a.py', pane: 'after', hunk: '1:1:1:1' } : null,
    target: followed,
    returning: null,
    follow: () => undefined,
    back: () => undefined,
    settle: () => undefined,
  });
  const nodeOf = (view: ReturnType<typeof render>) =>
    view.getAllByTestId('review-family-member-open').find((one) => recordOf(one) === 'INV-GGGGGG')!;
  const lines = (node: HTMLElement) =>
    [...node.querySelectorAll<HTMLElement>('[data-testid^="review-change-"]')]
      .filter((one) => one.dataset.testid !== 'review-change-mark')
      .map((one) => one.dataset.testid);
  // The subject a node should be named by: its statement, then its side tag (from the fixture row).
  const subjectOf = (node: HTMLElement) => {
    const [statement, side] = node.getAttribute('aria-labelledby')!.split(' ');
    const shown = document.getElementById(statement)!;
    expect(shown.parentElement).toBe(node);
    return flat(`${shown.textContent} ${document.getElementById(side)!.textContent}`);
  };
  const described = (node: HTMLElement) =>
    node
      .getAttribute('aria-describedby')!
      .split(' ')
      .map((id) => document.getElementById(id)!.dataset.testid);

  it('draws the change kind, its reason, then the membership, and says which is unknown', () => {
    const view = render(
      <IntentMarkerScope.Provider value={scope(null)}>
        <Harness context={context} />
      </IntentMarkerScope.Provider>,
    );
    const node = nodeOf(view);
    expect(lines(node)).toEqual([
      'review-change-badge',
      'review-change-why',
      'review-change-membership-why',
    ]);
    expect(within(node).getByTestId('review-change-why').textContent).toMatch(
      /^change kind unknown: RLZ-G00001/,
    );
    expect(within(node).getByTestId('review-change-membership-why').textContent).toBe(
      `membership unknown: ${reason}`,
    );
    expect(described(node)).toEqual([
      'review-change-badge',
      'review-change-why',
      'review-change-membership-why',
    ]);
    expect(within(node).queryByTestId('review-member-target-tag')).toBeNull();
    // Named by its subject (statement and side tag), described by its facts in the ruled order.
    expect(announcement(node)).toEqual({
      name: subjectOf(node),
      description: expect.stringMatching(
        new RegExp(
          `^unknown change kind unknown: RLZ-G00001 [\\s\\S]* membership unknown: ${reason}$`,
        ),
      ),
    });
  });

  it("puts a followed marker's Attribution unknown on the membership line, drawn first, described after the kind", () => {
    const view = render(
      <IntentMarkerScope.Provider value={scope(target)}>
        <Harness context={context} />
      </IntentMarkerScope.Provider>,
    );
    const node = nodeOf(view);
    // One membership statement: the tag rides on it; MIK-L34's own row note is not drawn.
    expect(within(node).queryByTestId('review-member-target-state')).toBeNull();
    expect(lines(node)).toEqual([
      'review-change-badge',
      'review-change-membership-why',
      'review-change-why',
    ]);
    const membership = within(node).getByTestId('review-change-membership-why');
    expect(membership.dataset.memberState).toBe('membership_unknown');
    expect(membership.textContent).toBe(
      `Attribution unknown opened from an intent marker · membership unknown: ${reason}`,
    );
    expect(described(node)).toEqual([
      'review-change-badge',
      'review-change-why',
      'review-change-membership-why',
    ]);
    // The node names its subject only; its description gives the kind, its reason, then the tagged
    // membership, each once and with its own label.
    const { name, description } = announcement(node);
    expect(name).toBe(subjectOf(node));
    expect(name).not.toMatch(/Attribution unknown|membership|change kind/);
    expect(description).toMatch(
      new RegExp(
        `^unknown change kind unknown: RLZ-G00001 [\\s\\S]* Attribution unknown opened from an intent marker · membership unknown: ${reason}$`,
      ),
    );
    expect(description.match(/Attribution unknown/g)).toHaveLength(1);
  });

  it("keeps MIK-L34's own note where no change facts are delivered (a dataset review)", () => {
    const [landed] = dataset.entries;
    const member = landed.after.members[0];
    const view = render(
      <IntentMarkerScope.Provider
        value={scope({
          ...target,
          familyKey: landed.family_id,
          memberRevisionKey: member.invariant_revision_id,
        })}
      >
        <Harness context={dataset} comparison={false} />
      </IntentMarkerScope.Provider>,
    );
    const note = view.getByTestId('review-member-target-state');
    expect(note.textContent).toBe(`Attribution unknown ${target.reason}`);
    expect(view.queryByTestId('review-change-membership-why')).toBeNull();
    expect(view.queryByTestId('review-change-badge')).toBeNull();
    // The dataset row names its subject and is described by the note; a row the marker did not
    // open has no description.
    const row = note.closest<HTMLElement>('[data-tree-node]')!;
    expect(announcement(row)).toEqual({
      name: subjectOf(row),
      description: `Attribution unknown ${target.reason}`,
    });
    const other = view.getAllByTestId('review-family-member-open').find((one) => one !== row)!;
    expect(announcement(other)).toEqual({ name: subjectOf(other), description: '' });
  });
});
