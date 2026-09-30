// MIK-R35 (ICR-R35@v1): the word-level intent diff in the selected intent and the family guarantee.
//
// The knowledge pane and member rows start from the REAL served invariant review of the converted
// scratch leaf (gitTrees.invariant.captured.json, a tree comparison `review:trees:1`); the second
// revision is derived from it by changing exactly the field each case is about, as
// statementWording.test.tsx does. Assertions read the accessible text (visually hidden announcements
// included, aria-hidden glyphs excluded), not only the styling.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type {
  ReviewFamilyContext,
  ReviewFamilyMember,
  ReviewFamilyRevisionContext,
  ReviewKnowledgePane,
  ReviewResult,
  ReviewSideContent,
} from '../../data/review';
import { FamilyTree } from './FamilyTree';
import {
  GuaranteeTextChange,
  IntentWordDiffScope,
  OneSidedGuaranteeLabel,
  guaranteeFact,
  guaranteeTextChange,
} from './IntentWordDiff';
import { PROSE_LAYOUT_KEY, proseLayoutStore, readProseLayout } from './intentDiffPreference';
import { SelectedStatement, type MemberSides } from './SubjectReview';
import { REWRITE_RATIO } from './wordDiff';

const captured = (name: string) =>
  (
    JSON.parse(
      readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), name), 'utf8'),
    ) as ReviewResult
  ).payload!;
const payload = captured('gitTrees.invariant.captured.json');
const familyPayload = captured('gitTrees.family.captured.json');
const selection = payload.knowledge.revision_selection!;
const subject = { kind: 'invariant' as const, id: selection.record_id };
const row = payload.family_context!.entries[0].before.members.find(
  (member) => member.invariant_revision_id === selection.before_revision_id,
)!;
const STATEMENT = row.statement!;
const [FIRST, SECOND] = row.essential_conditions;
const NEXT = 'f0f0f0f0-0000-5000-8000-000000000002';

afterEach(() => {
  cleanup();
  proseLayoutStore.getState().setLayout('inline');
  localStorage.clear();
});

// What assistive technology reads: every text node, the visually hidden announcements included and
// aria-hidden decoration excluded, with whitespace collapsed.
function spoken(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? '';
  if (node instanceof Element && node.getAttribute('aria-hidden') === 'true') return '';
  return [...node.childNodes].map(spoken).join('');
}
const read = (node: Node) => spoken(node).replace(/\s+/g, ' ').trim();

interface Options {
  before?: Partial<ReviewFamilyMember>;
  knowledge?: Partial<ReviewKnowledgePane>;
  revision?: string;
  tree?: boolean;
  // The member rows the page carried, per side (default: both sides' rows). `none`: no member row
  // was carried, so the pane and its field rows answer.
  rows?: MemberSides | 'none';
}

// The selected invariant compared with a successor that changes `change`: a new revision (r3) unless
// `revision` names the before revision itself.
function review(change: Partial<ReviewFamilyMember> = {}, options: Options = {}) {
  const revision = options.revision ?? NEXT;
  const before = { ...row, ...options.before };
  const after = {
    ...row,
    invariant_revision_id: revision,
    display_version: revision === NEXT ? 'r3' : row.display_version,
    ...change,
  };
  const knowledge: ReviewKnowledgePane = {
    ...payload.knowledge,
    revision_selection: { ...selection, after_revision_id: revision },
    before_statement: { ...payload.knowledge.before_statement, text: before.statement },
    after_statement: { ...payload.knowledge.after_statement, text: after.statement },
    before_conditions: before.essential_conditions,
    after_conditions: after.essential_conditions,
    ...options.knowledge,
  };
  const scope =
    options.tree === false
      ? { ...payload, limitations: payload.limitations.filter((t) => !t.startsWith('review:')) }
      : payload;
  return render(
    <IntentWordDiffScope payload={scope}>
      <SelectedStatement
        knowledge={knowledge}
        subject={subject}
        layout="split"
        members={
          options.rows === 'none'
            ? { before: [], after: [] }
            : (options.rows ?? { before: [before], after: [after] })
        }
      />
    </IntentWordDiffScope>,
  );
}

const label = (view: ReturnType<typeof render>) =>
  view.getByTestId('review-center-statement-label').textContent;

describe('a tree comparison statement', () => {
  it('marks the changed words of the statement in place, in one passage', () => {
    expect(payload.limitations).toContain('review:trees:1');
    const view = review({ statement: STATEMENT.replace('is refused', 'is rejected and reported') });
    expect(label(view)).toBe('Changed statement · revision r2 → r3');
    const passage = view.getByTestId('intent-diff');
    expect(passage.dataset.field).toBe('statement');
    expect(passage.dataset.layout).toBe('inline');
    const inline = view.getByTestId('intent-diff-inline');
    expect(read(inline)).toBe(
      "An unchanged path that no realization recorded in the comparison's knowledge links is " +
        'removed: refused added: rejected and reported with source_content_unresolved naming what ' +
        'each snapshot answered, and no content is read for it.',
    );
    // Struck and underlined elements, not colour alone.
    expect(inline.querySelector('del')!.textContent).toBe(' removed: refused');
    expect(inline.querySelector('ins')!.textContent).toBe(' added: rejected and reported');
    expect(view.queryByTestId('diff-pane')).toBeNull();
    expect(view.queryByTestId('review-center-statement-prose')).toBeNull();
  });

  it("shows a condition-only revision's changed condition in place and the statement once", () => {
    const edited = SECOND.replace('names what', 'lists what');
    const view = review({ essential_conditions: [FIRST, edited] });
    expect(view.queryByTestId('review-center-member-wording-unchanged')).toBeNull();
    expect(label(view)).toBe('Changed conditions · revision r2 → r3');
    expect(view.getByTestId('review-center-statement-prose').textContent).toBe(STATEMENT);
    const list = view.getByTestId('intent-diff-list');
    expect(list.dataset.field).toBe('conditions');
    const items = [...list.children] as HTMLElement[];
    expect(items.map((item) => item.dataset.item)).toEqual(['unchanged', 'changed']);
    expect(items[0].textContent).toBe(FIRST);
    expect(read(items[1])).toBe(
      'The refusal is source_content_unresolved and removed: names added: lists what each snapshot answered.',
    );
  });

  it('offers no layout control when a paired condition changed only its whitespace', () => {
    const view = review({ essential_conditions: [FIRST, `${SECOND} `] });
    expect(label(view)).toBe('Changed conditions · revision r2 → r3');
    const items = [...view.getByTestId('intent-diff-list').children] as HTMLElement[];
    expect(items.map((item) => item.dataset.item)).toEqual(['unchanged', 'changed']);
    expect(within(items[1]).getByTestId('intent-diff-whitespace-only')).toBeTruthy();
    // A layout changes nothing for a whitespace-only pair (review R1 F5, R2-1).
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('aligns conditions and exclusions by exact text: added, removed and moved items', () => {
    const added = 'No content is read for the path at either tree.';
    const [kept, dropped] = row.exclusions;
    const view = review({
      essential_conditions: [SECOND, FIRST, added],
      exclusions: ['Paths the measured change set lists.', kept],
    });
    expect(label(view)).toBe('Changed conditions, exclusions · revision r2 → r3');
    const [conditions, exclusions] = view.getAllByTestId('intent-diff-list');
    const kinds = (list: HTMLElement) =>
      [...list.children].map((item) => (item as HTMLElement).dataset.item);
    // One of the two swapped conditions is the aligned one; the other is moved, not re-paired.
    expect(kinds(conditions)).toEqual(['unchanged', 'moved', 'added']);
    expect(conditions.children[0].textContent).toBe(SECOND);
    expect(read(conditions.children[1])).toBe(`${FIRST} moved from item 1 to item 2`);
    expect(read(conditions.children[2])).toBe(`added: ${added}`);
    expect(kinds(exclusions)).toEqual(['added', 'unchanged', 'removed']);
    expect(read(exclusions.children[2])).toBe(`removed: ${dropped}`);
    expect(exclusions.children[2].querySelector('del')).not.toBeNull();
    // Nothing here is a word-diffed passage, so no layout control is offered.
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('word-diffs a changed applicability as its own passage', () => {
    const view = review({
      applicability: row.applicability!.replace('does not list', 'never lists'),
    });
    expect(label(view)).toBe('Changed applicability · revision r2 → r3');
    const field = view.getByTestId('intent-field-change');
    expect(field.dataset.field).toBe('applicability');
    expect(read(view.getByTestId('intent-diff-inline'))).toContain(
      'measured tree pair removed: does not list added: never lists as changed.',
    );
  });

  it("reads only the selected revisions' field rows, on tree and dataset reviews alike", () => {
    const fieldRow = (item_id: string) => ({
      item_id,
      item_kind: 'invariant',
      field: 'applicability',
      before_value: 'Before applicability.',
      after_value: 'After applicability.',
    });
    const statement = STATEMENT.replace('is refused', 'is rejected');
    const applicability = (view: ReturnType<typeof render>) =>
      view
        .queryByTestId('review-center-changed-fields')
        ?.querySelector('[data-field="applicability"]') ?? null;
    // No member row was carried, so the pane and the page's field rows answer. Those rows also
    // report other records' changes (keyed by their revision ids); they are never this subject's.
    for (const tree of [true, false]) {
      const other = review(
        { statement },
        { rows: 'none', tree, knowledge: { field_changes: [fieldRow('another-record-revision')] } },
      );
      expect(label(other)).toMatch(/^Changed statement · revision /);
      expect(applicability(other)).toBeNull();
      cleanup();
      const own = review(
        { statement },
        { rows: 'none', tree, knowledge: { field_changes: [fieldRow(NEXT)] } },
      );
      expect(label(own)).toMatch(/^Changed statement, applicability · revision /);
      expect(applicability(own)).not.toBeNull();
      cleanup();
    }
  });

  // Review R1 F1: one revision whose texts differ, with the subject's member row carried on one side
  // only. Each side's text comes from its own row or, without one, from the pane.
  it.each([
    ['the after row is not on the page', 'after-missing'],
    ['the before row is not on the page', 'before-missing'],
    ['the member is listed on the after side only (moved in, wording touched)', 'moved-in'],
  ] as const)('diffs one revision whose texts differ when %s', (_name, shape) => {
    const touched = STATEMENT.replace('is refused with', 'is refused, with');
    const after = { ...row, statement: touched };
    const notOnPage: ReviewFamilyMember = {
      ...row,
      state: 'content_not_on_page',
      statement: undefined,
      applicability: undefined,
      essential_conditions: [],
      exclusions: [],
    };
    const rows: MemberSides =
      shape === 'after-missing'
        ? { before: [row], after: [notOnPage] }
        : shape === 'before-missing'
          ? { before: [notOnPage], after: [after] }
          : { before: [], after: [after] };
    const view = review({ statement: touched }, { revision: row.invariant_revision_id, rows });
    expect(label(view)).toBe(
      'Changed statement · revision r2 → r2 · the same revision on both sides; its text differs',
    );
    expect(view.queryByTestId('review-center-member-unchanged')).toBeNull();
    expect(read(view.getByTestId('intent-diff-inline'))).toContain(
      'links is removed: refused added: refused, with source_content_unresolved',
    );
  });

  it('says "Statement unchanged" for one revision whose texts are the same, with or without rows', () => {
    for (const rows of [undefined, 'none'] as const) {
      const view = review({}, { revision: row.invariant_revision_id, rows });
      expect(label(view)).toBe('Statement unchanged · same recorded revision');
      expect(view.getByTestId('review-center-member-unchanged')).toBeTruthy();
      expect(view.queryByTestId('intent-diff')).toBeNull();
      cleanup();
    }
  });

  it('labels an applicability recorded on one side only as a known absence, with no diff', () => {
    const removed = review({ applicability: undefined });
    const field = removed.getByTestId('intent-field-change');
    expect(field.dataset.field).toBe('applicability');
    expect(within(field).getByTestId('intent-one-sided').textContent).toBe(
      'removed in after — after: known absent',
    );
    expect(field.textContent).toContain(row.applicability!);
    expect(removed.queryByTestId('intent-diff')).toBeNull();
    expect(removed.queryByTestId('intent-diff-layout-control')).toBeNull();
    cleanup();
    const added = review({}, { before: { applicability: undefined } });
    expect(added.getByTestId('intent-one-sided').textContent).toBe(
      'added in after — before: known absent',
    );
    expect(added.queryByTestId('intent-diff')).toBeNull();
  });

  it('shows a list the comparison only reported joined as reported, never aligned', () => {
    const exclusions = {
      item_id: row.invariant_revision_id,
      item_kind: 'invariant',
      field: 'exclusions',
      before_value: 'Unmeasured pairs; unreadable knowledge',
      after_value: 'Unmeasured pairs; unreadable or unconverted knowledge',
    };
    const view = review(
      {},
      {
        revision: row.invariant_revision_id,
        rows: 'none',
        knowledge: { field_changes: [exclusions] },
      },
    );
    expect(label(view)).toMatch(/^Changed exclusions · revision /);
    expect(view.getByTestId('intent-field-projected').textContent).toBe(
      'before: Unmeasured pairs; unreadable knowledge · after: Unmeasured pairs; unreadable or unconverted knowledge',
    );
    expect(view.queryByTestId('intent-diff-list')).toBeNull();
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('shows a whitespace change inside a changed passage as glyphs, announced in words', () => {
    const view = review({
      statement: STATEMENT.replace('path that', 'path  that').replace('is refused', 'is rejected'),
    });
    const inline = view.getByTestId('intent-diff-inline');
    const [removed] = inline.querySelectorAll('del');
    const [added] = inline.querySelectorAll('ins');
    expect(removed.querySelector('[aria-hidden="true"]')!.textContent).toBe('·');
    expect(added.querySelector('[aria-hidden="true"]')!.textContent).toBe('··');
    expect(read(inline)).toContain(
      'An unchanged path removed: 1 space added: 2 spaces that no realization',
    );
  });

  it('keeps "wording unchanged" for a new revision whose every field is identical', () => {
    const view = review();
    expect(view.getByTestId('review-center-member-wording-unchanged')).toBeTruthy();
    expect(label(view)).toBe('Wording unchanged · revision r2 → r3');
    expect(view.queryByTestId('intent-diff')).toBeNull();
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('diffs one revision whose text differs between the two sides, and says so', () => {
    const edited = STATEMENT.replace('is refused with', 'is refused, with');
    const view = review({ statement: edited }, { revision: row.invariant_revision_id });
    expect(label(view)).toBe(
      'Changed statement · revision r2 → r2 · the same revision on both sides; its text differs',
    );
    expect(read(view.getByTestId('intent-diff-inline'))).toContain(
      'links is removed: refused added: refused, with source_content_unresolved',
    );
  });

  it('renders a whitespace-only change once, labelled, with both exact texts disclosed', () => {
    const view = review({ statement: `${STATEMENT} ` });
    expect(view.queryByTestId('review-center-member-wording-unchanged')).toBeNull();
    expect(view.getByTestId('intent-diff-whitespace-only').dataset.field).toBe('statement');
    expect(view.getByTestId('intent-diff-whitespace-label').textContent).toBe(
      'whitespace-only change',
    );
    expect(
      view.getAllByTestId('intent-diff-whitespace-text').map((node) => node.textContent),
    ).toEqual([`${STATEMENT} `]);
    expect(view.getByTestId('intent-diff-exact-before').textContent).toBe(
      STATEMENT.replaceAll(' ', '·'),
    );
    expect(view.getByTestId('intent-diff-exact-after').textContent).toBe(
      `${STATEMENT.replaceAll(' ', '·')}·`,
    );
    expect(view.queryByTestId('intent-diff-inline')).toBeNull();
    // Nothing a layout would change, so no layout control (review R1 F5).
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('shows a rewrite side by side by default, says why, and can still show it inline', () => {
    expect(REWRITE_RATIO).toBe(0.5);
    const rewritten = 'Only the comparison’s own knowledge may admit an unchanged path.';
    const view = review({ statement: rewritten });
    const passage = view.getByTestId('intent-diff');
    expect(passage.dataset.layout).toBe('side-by-side');
    expect(view.getByTestId('intent-diff-rewrite').textContent).toMatch(
      /^Mostly rewritten: \d+ of 28 words changed \(\d+%\), above the 50% rewrite ratio, so it is shown side by side\./,
    );
    expect(view.getByTestId('intent-diff-before').textContent).toContain('removed: ');
    expect(view.getByTestId('intent-diff-after').textContent).toContain('added: ');
    fireEvent.click(view.getByRole('button', { name: 'Show inline' }));
    expect(view.getByTestId('intent-diff').dataset.layout).toBe('inline');
    expect(view.queryByTestId('intent-diff-rewrite')).toBeNull();
  });

  it('switches every passage between inline and side by side and keeps the choice locally', () => {
    const view = review({
      statement: STATEMENT.replace('is refused', 'is rejected'),
      applicability: row.applicability!.replace('does not list', 'never lists'),
    });
    const layouts = () => view.getAllByTestId('intent-diff').map((node) => node.dataset.layout);
    expect(layouts()).toEqual(['inline', 'inline']);
    fireEvent.change(view.getByTestId('intent-diff-layout'), {
      target: { value: 'side-by-side' },
    });
    expect(layouts()).toEqual(['side-by-side', 'side-by-side']);
    expect(localStorage.getItem(PROSE_LAYOUT_KEY)).toBe('side-by-side');
    expect(readProseLayout(localStorage)).toBe('side-by-side');
  });

  it('defaults to inline when browser storage is unavailable, and the toggle still works', () => {
    const unavailable = {
      getItem: () => {
        throw new DOMException('denied', 'SecurityError');
      },
    };
    expect(readProseLayout(unavailable)).toBe('inline');
    expect(readProseLayout(undefined)).toBe('inline');
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new DOMException('denied', 'QuotaExceededError');
    };
    try {
      proseLayoutStore.getState().setLayout('side-by-side');
      expect(proseLayoutStore.getState().layout).toBe('side-by-side');
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});

describe('one-sided, unavailable and ambiguous statements', () => {
  const absent: ReviewSideContent = { state: 'absent', language: 'text', detail: 'no revision' };
  const unresolved: ReviewSideContent = {
    state: 'unresolved',
    language: 'text',
    detail: 'the side could not be read',
  };

  it('labels an added or removed statement with its known-absent side and draws no diff', () => {
    const added = review(
      {},
      {
        knowledge: {
          revision_selection: { ...selection, state: 'added', before_revision_id: undefined },
          before_statement: absent,
        },
      },
    );
    expect(added.getByTestId('intent-one-sided').textContent).toBe(
      'added in after — before: known absent',
    );
    expect(added.getByTestId('review-center-statement-prose').textContent).toBe(STATEMENT);
    expect(added.queryByTestId('intent-diff')).toBeNull();
    cleanup();
    const removed = review(
      {},
      {
        knowledge: {
          revision_selection: { ...selection, state: 'removed', after_revision_id: undefined },
          after_statement: absent,
        },
      },
    );
    expect(removed.getByTestId('intent-one-sided').textContent).toBe(
      'removed in after — after: known absent',
    );
  });

  it('names an unreadable side as unreadable, never known absent, and never diffs against it', () => {
    const oneSided = review(
      {},
      {
        knowledge: {
          revision_selection: { ...selection, state: 'added', before_revision_id: undefined },
          before_statement: unresolved,
        },
      },
    );
    const line = oneSided.getByTestId('intent-one-sided');
    expect(line.dataset.known).toBe('false');
    expect(line.textContent).toBe(
      'recorded on the after side only — before: unresolved, not known absent (the side could not be read)',
    );
    cleanup();
    const compared = review(
      { statement: STATEMENT.replace('refused', 'rejected') },
      { knowledge: { after_statement: unresolved } },
    );
    expect(compared.getByTestId('review-after-state').dataset.sideState).toBe('unresolved');
    expect(compared.queryByTestId('intent-diff')).toBeNull();
  });

  it('draws no diff for an ambiguous or unresolved revision selection', () => {
    for (const state of ['ambiguous', 'unresolved'] as const) {
      const view = review(
        { statement: STATEMENT.replace('refused', 'rejected') },
        { knowledge: { revision_selection: { ...selection, state, after_revision_id: NEXT } } },
      );
      expect(view.getByTestId('review-center-statement-unavailable')).toBeTruthy();
      expect(view.queryByTestId('intent-diff')).toBeNull();
      cleanup();
    }
  });

  it("leaves a dataset review's statement rendering as landed", () => {
    const view = review({ statement: STATEMENT.replace('refused', 'rejected') }, { tree: false });
    expect(label(view)).toBe('Changed statement · revision r2 → r3');
    expect(view.getByTestId('diff-pane')).toBeTruthy();
    expect(view.queryByTestId('intent-diff')).toBeNull();
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });
});

describe('the family guarantee', () => {
  const entry = familyPayload.family_context!.entries[0];
  const guarantee = entry.before.guarantee!;
  const edited = guarantee.joint_guarantee.replace(
    'is refused by name.',
    'is refused by name, and the refusal says what each snapshot answered.',
  );

  it('word-diffs a changed guarantee, with the revisions as compact metadata', () => {
    const after = {
      ...guarantee,
      revision_id: NEXT,
      display_version: 'r3',
      joint_guarantee: edited,
    };
    const change = guaranteeTextChange({ ...entry, after: { ...entry.after, guarantee: after } });
    expect(change).toEqual({ before: guarantee, after });
    const view = render(<GuaranteeTextChange {...change!} />);
    expect(view.getByTestId('review-center-guarantee-label').textContent).toBe(
      'Changed guarantee · revision r2 → r3',
    );
    expect(view.getByTestId('intent-diff').dataset.field).toBe('guarantee');
    expect(view.getByTestId('intent-diff-layout-control')).toBeTruthy();
    expect(read(view.getByTestId('intent-diff-inline'))).toMatch(
      /is refused by removed: name\. added: name, and the refusal says what each snapshot answered\.$/,
    );
    expect(view.queryByTestId('diff-pane')).toBeNull();
  });

  it('diffs a guarantee whose one revision carries different text, and answers null otherwise', () => {
    const view = render(
      <GuaranteeTextChange before={guarantee} after={{ ...guarantee, joint_guarantee: edited }} />,
    );
    expect(view.getByTestId('review-center-guarantee-label').textContent).toBe(
      'Changed guarantee · revision r2 → r2 · the same revision on both sides; its text differs',
    );
    const sameRevision = { ...entry.after, guarantee: { ...guarantee, joint_guarantee: edited } };
    expect(guaranteeTextChange({ ...entry, after: sameRevision })).not.toBeNull();
    expect(guaranteeTextChange(entry)).toBeNull();
    expect(
      guaranteeTextChange({ ...entry, before: { ...entry.before, guarantee: undefined } }),
    ).toBeNull();
  });

  it('offers no layout control for a whitespace-only guarantee change', () => {
    const view = render(
      <GuaranteeTextChange
        before={guarantee}
        after={{ ...guarantee, joint_guarantee: `${guarantee.joint_guarantee} ` }}
      />,
    );
    expect(view.getByTestId('intent-diff-whitespace-only').dataset.field).toBe('guarantee');
    expect(view.queryByTestId('intent-diff-layout-control')).toBeNull();
  });

  it('labels a one-sided guarantee as a known absence only when the other side records none', () => {
    const known = render(
      <OneSidedGuaranteeLabel side="after" other={{ ...entry.before, state: 'not_recorded' }} />,
    );
    expect(known.getByText('Added guarantee')).toBeTruthy();
    expect(known.getByTestId('intent-one-sided').textContent).toBe(
      'added in after — before: known absent',
    );
    cleanup();
    const unreadable = render(
      <OneSidedGuaranteeLabel
        side="before"
        other={{ ...entry.after, state: 'unreadable', detail: 'the after tree could not be read' }}
      />,
    );
    expect(unreadable.getByText('Guarantee recorded on one side only')).toBeTruthy();
    expect(unreadable.getByTestId('intent-one-sided').dataset.known).toBe('false');
  });
});

// Review R1 F2: the Q1 rule on every tree-comparison label. The family navigator's joint guarantee and
// member tag, and the center details, never call one revision "unchanged" when its texts differ. A
// dataset review (tree = false) keeps the landed labels: there a revision is one immutable text.
describe('labels of one revision whose texts differ', () => {
  const context = familyPayload.family_context!;
  const entry = context.entries[0];
  const guarantee = entry.before.guarantee!;
  const touched = entry.after.members[0];
  const edited = `${guarantee.joint_guarantee} Edited.`;

  function navigator(after: Partial<ReviewFamilyRevisionContext>, tree: boolean) {
    const changed: ReviewFamilyContext = {
      ...context,
      entries: [{ ...entry, after: { ...entry.after, ...after } }],
    };
    return render(
      <FamilyTree
        context={changed}
        selection={null}
        onSelect={() => undefined}
        onRosterNext={() => undefined}
        query=""
        onQuery={() => undefined}
        tree={tree}
      />,
    );
  }

  it('shows both texts of one guarantee revision whose texts differ, on a tree comparison', () => {
    expect(entry.after.guarantee!.revision_id).toBe(guarantee.revision_id);
    const notes = (view: ReturnType<typeof render>) =>
      view
        .getAllByTestId('review-family-guarantee')
        .map((node) => `${node.dataset.guaranteeSide}: ${node.firstElementChild!.textContent}`);
    const differs = { guarantee: { ...guarantee, joint_guarantee: edited } };
    expect(notes(navigator(differs, true))).toEqual([
      'before: Joint guarantee · before · same revision, text differs',
      'after: Joint guarantee · after · same revision, text differs',
    ]);
    cleanup();
    expect(notes(navigator(differs, false))).toEqual(['both: Joint guarantee · unchanged']);
    cleanup();
    expect(notes(navigator({}, true))).toEqual(['both: Joint guarantee · unchanged']);
  });

  it('tags one member revision by its two texts on a tree comparison', () => {
    const tag = (view: ReturnType<typeof render>) =>
      view
        .getAllByTestId('review-family-member-open')
        .find((node) => node.dataset.revision === touched.invariant_revision_id)!.textContent;
    const withAfter = (member: ReviewFamilyMember) => ({
      members: entry.after.members.map((row) => (row === touched ? member : row)),
    });
    const reworded = withAfter({ ...touched, statement: `${touched.statement} Edited.` });
    expect(tag(navigator(reworded, true))).toMatch(/ · same revision · text differs$/);
    cleanup();
    const unread = withAfter({ ...touched, state: 'content_not_on_page', statement: undefined });
    expect(tag(navigator(unread, true))).toMatch(/ · same revision$/);
    cleanup();
    expect(tag(navigator(reworded, false))).toMatch(/ · unchanged revision$/);
    cleanup();
    expect(tag(navigator({}, true))).toMatch(/ · unchanged revision$/);
  });

  it("names the center details' guarantee fact for one revision whose texts differ", () => {
    const differs = {
      ...entry,
      after: { ...entry.after, guarantee: { ...guarantee, joint_guarantee: edited } },
    };
    expect(guaranteeFact(differs, true)).toBe('same_revision_text_changed');
    expect(guaranteeFact(differs, false)).toBe('unchanged_revision');
    expect(guaranteeFact(entry, true)).toBe('unchanged_revision');
  });
});
