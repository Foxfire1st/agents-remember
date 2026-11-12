// Statements and judgments belong to the server-selected subject, not to a local roster row.
import { css } from '../../../styled-system/css';
import type {
  ReviewFamilyMember,
  ReviewKnowledgePane,
  ReviewRevisionSelection,
} from '../../data/review';
import type { ReviewSubject } from './ReviewNavigation';
import type { DiffMode as DiffLayout } from '../changeset/DiffPane';
import { IntentStatementBody } from './IntentWordDiff';
import {
  revisionMeta,
  rowWording,
  textFirstComparison,
  type AuthoredWording,
  type WordingComparison,
  type WordingField,
} from './statementWording';

const muted = css({ color: 'muted', fontSize: '0.78rem', margin: '0.35rem 0' });
// The statement's label in the mockup's label look (styles.css `.label`; d9a1ec8f): cyan, small capitals.
const labelLook = css({ color: 'cyan', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.1em', margin: '0.35rem 0' });
export function selectedRevision(
  knowledge: ReviewKnowledgePane,
  subject: ReviewSubject,
): ReviewRevisionSelection | null {
  const selection = knowledge.revision_selection;
  return selection?.record_kind === subject.kind && selection.record_id === subject.id
    ? selection
    : null;
}

function statementState(selection: ReviewRevisionSelection, wording: WordingComparison): string {
  if (selection.state === 'compared') {
    if (wording.kind === 'same_revision') return 'unchanged';
    return wording.kind === 'wording_unchanged' ? 'wording-unchanged' : 'changed';
  }
  if (selection.state === 'added' || selection.state === 'removed') return 'one-sided';
  return selection.state;
}

const FIELD_NAMES: Record<WordingField, string> = {
  statement: 'statement',
  applicability: 'applicability',
  conditions: 'conditions',
  exclusions: 'exclusions',
};

// The statement's header names the change and, where the page carried them, the revisions' display
// numbers; the revision ids are in Technical details (MIK-R89 v2 rule 6, amended 9dde2b09).
function statementLabel(
  selection: ReviewRevisionSelection,
  wording: WordingComparison,
  revisions: string | null,
): string {
  const at = revisions === null ? '' : ` · ${revisions}`;
  if (selection.state === 'unresolved') return 'Revision comparison unresolved';
  if (selection.state === 'added') return 'Added statement · recorded on the after side only';
  if (selection.state === 'removed') return 'Removed statement · recorded on the before side only';
  if (wording.kind === 'same_revision') return 'Statement unchanged · same recorded revision';
  if (wording.kind === 'wording_unchanged') return `Wording unchanged${at}`;
  if (wording.kind === 'not_comparable')
    return `Statement revised${at} · ${wording.fields.join(', ')} not carried here`;
  return `Changed ${wording.fields.map((field) => FIELD_NAMES[field]).join(', ')}${at}`;
}

// Only a text-first comparison (a tree comparison) can find one revision whose text differs.
function sameRevisionNote(oneRevision: boolean, wording: WordingComparison): string {
  return oneRevision && wording.kind === 'changed'
    ? ' · the same revision on both sides; its text differs'
    : '';
}

// The subject's member rows, kept per side: each side's row is looked up only among that side's own
// rows, because in a tree comparison one revision can carry different text on the two sides (a text
// record's revision increments only when its meaning changes, MIK-R21; review R1 F1).
export interface MemberSides {
  before: ReviewFamilyMember[];
  after: ReviewFamilyMember[];
}

const NO_ROWS: MemberSides = { before: [], after: [] };

// The two revisions' authored text, from each side's own member row when the page carried it (every
// field), else from the knowledge pane (statement and conditions) plus the comparison's own field
// changes for the rest. A field neither carries stays undefined.
function authoredSides(
  knowledge: ReviewKnowledgePane,
  selection: ReviewRevisionSelection,
  members: MemberSides,
): {
  before: AuthoredWording;
  after: AuthoredWording;
  labels: [string, string] | null;
  // Fields a side took from the comparison's joined field report rather than from a list.
  projected: WordingField[];
} {
  const own = (rows: ReviewFamilyMember[], revision?: string) =>
    rows.find((member) => member.state === 'recorded' && member.invariant_revision_id === revision);
  const beforeRow = own(members.before, selection.before_revision_id);
  const afterRow = own(members.after, selection.after_revision_id);
  // Only the selected revisions' own field rows (keyed by revision id): the page's field rows also
  // report other records' changes, which are never this subject's (architect ruling 2026-09-30T11:53).
  const selected = [selection.before_revision_id, selection.after_revision_id];
  const reported = (field: string) =>
    knowledge.field_changes.find(
      (change) => change.field === field && selected.includes(change.item_id),
    );
  const pane = (side: 'before' | 'after'): AuthoredWording => {
    const statement = knowledge[`${side}_statement`];
    const fromRow = side === 'before' ? beforeRow : afterRow;
    const change = (field: string) => {
      const found = reported(field);
      return found
        ? ((side === 'before' ? found.before_value : found.after_value) ?? null)
        : undefined;
    };
    if (fromRow) return rowWording(fromRow);
    return {
      statement: statement.state === 'present' ? statement.text : undefined,
      applicability: change('applicability'),
      conditions: knowledge[`${side}_conditions`],
      exclusions: listed(change('exclusions')),
    };
  };
  const joined = (!beforeRow || !afterRow) && reported('exclusions') !== undefined;
  return {
    before: pane('before'),
    after: pane('after'),
    labels: revisionLabels(selection, beforeRow, afterRow),
    projected: joined ? ['exclusions'] : [],
  };
}

// Display versions when both are carried and differ; one revision's own version on both sides (it is
// one number); otherwise none, so two revisions never read as one and no revision id is named here.
function revisionLabels(
  selection: ReviewRevisionSelection,
  beforeRow?: ReviewFamilyMember,
  afterRow?: ReviewFamilyMember,
): [string, string] | null {
  const before = shownVersion(beforeRow, selection.before_display_version);
  const after = shownVersion(afterRow, selection.after_display_version);
  const one = before ?? after;
  if (selection.before_revision_id === selection.after_revision_id && one) return [one, one];
  return before && after && before !== after ? [before, after] : null;
}

// A side's display version: its own member row's, else the one its revision selection carries.
function shownVersion(row: ReviewFamilyMember | undefined, carried: string | undefined): string | undefined {
  return row?.display_version ?? carried;
}

// A reported field value as the list it is compared as: absent = not carried, null = none recorded.
function listed(value: string | null | undefined): string[] | undefined {
  if (value === undefined) return undefined;
  return value === null ? [] : [value];
}

export function SelectedStatement({
  knowledge,
  subject,
  layout,
  members = NO_ROWS,
}: {
  knowledge: ReviewKnowledgePane;
  subject: ReviewSubject;
  layout: DiffLayout;
  members?: MemberSides;
}) {
  const selection = selectedRevision(knowledge, subject);
  if (!selection)
    return (
      <p className={muted} data-testid="review-center-member-unavailable">
        The selected subject's revision comparison was not supplied. No statement comparison is
        claimed.
      </p>
    );
  const sides = authoredSides(knowledge, selection, members);
  const oneRevision = selection.before_revision_id === selection.after_revision_id;
  const wording = textFirstComparison(
    sides.before,
    sides.after,
    oneRevision,
  );
  const state = statementState(selection, wording);
  // The header line: what changed, with MIK-R35's wording-layout switch beside it (9dde2b09). Who
  // introduced the record and the revision ids are Technical details' (MIK-R89 v2 rule 6).
  const label = (
    <p className={labelLook} data-testid="review-center-statement-label">
      {statementLabel(selection, wording, sides.labels && revisionMeta(...sides.labels))}
      {sameRevisionNote(oneRevision, wording)}
    </p>
  );
  return (
    <div
      data-review-content="statement"
      data-testid={`review-center-member-${state}`}
      data-revision-state={selection.state}
    >
      {['compared', 'added', 'removed'].includes(selection.state) ? (
        <IntentStatementBody
          header={label}
          knowledge={knowledge}
          selection={selection}
          wording={wording}
          sides={sides}
          projected={sides.projected}
          layout={layout}
        />
      ) : (
        <>
          {label}
          <p className={muted} data-testid="review-center-statement-unavailable">
            Before: {knowledge.before_statement.state} · after: {knowledge.after_statement.state}.
            No statement diff is available.
          </p>
        </>
      )}
    </div>
  );
}

export { SubjectJudgment } from './SubjectJudgment';
