// The word-level intent diff, drawn (MIK-R35, adopting ICR-R35@v1). `wordDiff.ts` decides what is
// marked; this module renders it inside the decluttered statement and guarantee structure of
// MIK-R31 rule 3, which it keeps: identical wording once, one-sided statements as labelled prose.
//
// * One passage per changed field (rule 1): the statement, the applicability and the guarantee as
//   prose with removed and added words marked in place; conditions and exclusions as their aligned
//   lists (rule 1a), each paired item as its own passage.
// * Inline or side by side (rule 4): one browser-local preference (`intentDiffPreference.ts`),
//   inline by default. A rewrite (above REWRITE_RATIO) is shown side by side and says why.
// * Whitespace only (rule 3): the after text once, labelled, with both exact texts disclosed.
// * One side (rule 5): the present text as labelled prose with the R06 known-absent indication; a
//   side that could not be read is named as such, never as known absent. No diff is drawn against
//   an absent or unavailable side.
// * Accessibility (rule 7): a removal is struck and an addition underlined, and each carries
//   visually hidden `removed:` / `added:` text, so the marks never depend on colour.
//
// Only a tree comparison (MIK-R25, `review:trees:<n>`) is word-diffed, and only there do labels compare
// text bytes rather than revision identities; a dataset review keeps the landed rendering
// (`TreeComparisonScope`).
import {
  Fragment,
  createContext,
  useContext,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { css } from '../../../styled-system/css';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyGuarantee,
  ReviewFamilyRevisionContext,
  ReviewKnowledgePane,
  ReviewPayload,
  ReviewRevisionSelection,
  ReviewSideContent,
} from '../../data/review';
import { guaranteeComparison } from '../../data/review';
import { treeComparisonNumber } from '../../data/reviewTrees';
import { proseLayoutStore, useProseLayout, type ProseLayout } from './intentDiffPreference';
import { KnowledgeStatements } from './KnowledgeStatements';
import type { DiffLayout } from './SourceExplorer';
import {
  guaranteeRevisionLabels,
  revisionMeta,
  type AuthoredWording,
  type WordingComparison,
  type WordingField,
} from './statementWording';
import {
  REWRITE_RATIO,
  alignLists,
  describeWhitespace,
  isWhitespace,
  textDiff,
  visibleWhitespace,
  type DiffPart,
  type ListRow,
  type TextDiff,
} from './wordDiff';

const muted = css({ color: 'muted', fontSize: '0.78rem', margin: '0.35rem 0' });
const prose = css({
  fontSize: '0.9rem',
  whiteSpace: 'pre-wrap',
  overflowWrap: 'anywhere',
  lineHeight: '1.8',
  margin: '0.2rem 0',
});
const srOnly = css({
  position: 'absolute',
  width: '1px',
  height: '1px',
  overflow: 'hidden',
  clipPath: 'inset(50%)',
  whiteSpace: 'nowrap',
});
// The fills are DiffPane's own removal and addition colours; the line is what tells them apart.
const removedMark = css({
  color: 'inherit',
  textDecorationLine: 'line-through',
  textDecorationThickness: '1px',
  background: '#5a2525aa',
  borderRadius: '2px',
  padding: '0 0.1rem',
});
const addedMark = css({
  color: 'inherit',
  textDecorationLine: 'underline',
  textDecorationThickness: '1px',
  background: '#255a25aa',
  borderRadius: '2px',
  padding: '0 0.1rem',
});
const glyphs = css({ color: 'muted' });
const sideLabel = css({
  color: 'cyan',
  fontSize: '0.68rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0.3rem 0 0',
});
const pair = css({
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(min(16rem, 100%), 1fr))',
  gap: '0.4rem 1rem',
  minWidth: 0,
});
const controls = css({ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' });
const items = css({ paddingLeft: '1.1rem', margin: '0.3rem 0', display: 'grid', gap: '0.25rem' });
const tag = css({
  border: '1px solid var(--grid)',
  color: 'muted',
  borderRadius: '2px',
  padding: '0 0.35rem',
  fontSize: '0.7rem',
  marginLeft: '0.4rem',
  whiteSpace: 'nowrap',
});
// The landed guarantee block's card (FamilyReviewCenter), so a changed guarantee reads in place.
const card = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.5rem 0.6rem',
  background: 'bg',
  minWidth: '0',
});
const exact = css({ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: '0.8rem' });
const linkButton = css({
  background: 'transparent',
  border: 'none',
  color: 'amber',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0',
  textDecoration: 'underline',
});

// Whether the renderers below read a tree comparison (MIK-R25, `review:trees:<n>`). There the intent
// wording is word-diffed and every label compares text bytes, since one text revision can carry
// different bytes on its two sides (MIK-R21, the 11:53 Q1 ruling); a dataset review keeps the landed
// rendering. The review center sets it from its payload, and the family navigator from its mount.
const TreeComparison = createContext(false);

export function TreeComparisonScope({ tree, children }: { tree: boolean; children: ReactNode }) {
  return <TreeComparison.Provider value={tree}>{children}</TreeComparison.Provider>;
}

export function IntentWordDiffScope({
  payload,
  children,
}: {
  payload: ReviewPayload;
  children: ReactNode;
}) {
  return (
    <TreeComparisonScope tree={treeComparisonNumber(payload.limitations) !== undefined}>
      {children}
    </TreeComparisonScope>
  );
}

export const useTreeComparison = (): boolean => useContext(TreeComparison);

function ProseLayoutControl() {
  const layout = useProseLayout();
  const id = useId();
  return (
    <div className={controls} data-testid="intent-diff-layout-control">
      <label className={muted} htmlFor={id}>
        Wording layout
      </label>
      <select
        id={id}
        data-testid="intent-diff-layout"
        value={layout}
        onChange={(event) =>
          proseLayoutStore
            .getState()
            .setLayout(event.target.value === 'side-by-side' ? 'side-by-side' : 'inline')
        }
      >
        <option value="inline">Inline</option>
        <option value="side-by-side">Side by side</option>
      </select>
    </div>
  );
}

// A removed or added run: struck or underlined, with its visually hidden announcement. A whitespace
// run is drawn as visible glyphs (hidden from assistive technology) and announced in words, with a
// closing space so the announcement never runs into the next word.
function Mark({ part }: { part: DiffPart }) {
  const Tag = part.kind === 'removed' ? 'del' : 'ins';
  const space = isWhitespace(part.text);
  return (
    <Tag className={part.kind === 'removed' ? removedMark : addedMark} data-part={part.kind}>
      <span
        className={srOnly}
      >{` ${part.kind}: ${space ? `${describeWhitespace(part.text)} ` : ''}`}</span>
      {space ? (
        <span aria-hidden="true" className={glyphs}>
          {visibleWhitespace(part.text)}
        </span>
      ) : (
        part.text
      )}
    </Tag>
  );
}

function Prose({
  parts,
  testid,
  side,
}: {
  parts: DiffPart[];
  testid: string;
  side?: 'before' | 'after';
}) {
  return (
    <p className={prose} data-testid={testid} data-side={side}>
      {parts.map((part, index) =>
        part.kind === 'same' ? (
          <Fragment key={index}>{part.text}</Fragment>
        ) : (
          <Mark key={index} part={part} />
        ),
      )}
    </p>
  );
}

function SideBySide({ parts }: { parts: DiffPart[] }) {
  return (
    <div className={pair} data-testid="intent-diff-side-by-side">
      <div>
        <p className={sideLabel}>Before</p>
        <Prose
          parts={parts.filter((part) => part.kind !== 'added')}
          testid="intent-diff-before"
          side="before"
        />
      </div>
      <div>
        <p className={sideLabel}>After</p>
        <Prose
          parts={parts.filter((part) => part.kind !== 'removed')}
          testid="intent-diff-after"
          side="after"
        />
      </div>
    </div>
  );
}

function WhitespaceOnly({
  before,
  after,
  field,
}: {
  before: string;
  after: string;
  field: string;
}) {
  return (
    <div data-testid="intent-diff-whitespace-only" data-field={field}>
      <p className={muted} data-testid="intent-diff-whitespace-label">
        whitespace-only change
      </p>
      <p className={prose} data-testid="intent-diff-whitespace-text">
        {after}
      </p>
      <details>
        <summary>Both exact texts, whitespace made visible</summary>
        <p className={sideLabel}>Before</p>
        <pre className={exact} data-testid="intent-diff-exact-before">
          {visibleWhitespace(before)}
        </pre>
        <p className={sideLabel}>After</p>
        <pre className={exact} data-testid="intent-diff-exact-after">
          {visibleWhitespace(after)}
        </pre>
      </details>
    </div>
  );
}

type WordsDiff = Extract<TextDiff, { kind: 'words' }>;

function rewriteReason(diff: WordsDiff): string {
  const percent = Math.round(diff.ratio * 100);
  return (
    `Mostly rewritten: ${diff.changedWords} of ${diff.longerWords} words changed (${percent}%), ` +
    `above the ${REWRITE_RATIO * 100}% rewrite ratio, so it is shown side by side.`
  );
}

// One changed text field as one passage. Its layout is the reader's preference, except that a
// rewrite is shown side by side until the reader asks for this passage inline.
function TextPassage({ before, after, field }: { before: string; after: string; field: string }) {
  const diff = useMemo(() => textDiff(before, after), [before, after]);
  const preferred = useProseLayout();
  const identity = `${before}\u0000${after}`;
  const [inlineFor, setInlineFor] = useState<string | null>(null);
  if (diff.kind === 'identical')
    return (
      <p className={prose} data-testid="intent-diff-identical" data-field={field}>
        {after}
      </p>
    );
  if (diff.kind === 'whitespace_only')
    return <WhitespaceOnly before={before} after={after} field={field} />;
  const fallback = diff.rewrite && preferred === 'inline' && inlineFor !== identity;
  const layout: ProseLayout = fallback ? 'side-by-side' : preferred;
  return (
    <div data-testid="intent-diff" data-field={field} data-layout={layout}>
      {fallback ? (
        <p className={muted} data-testid="intent-diff-rewrite">
          {rewriteReason(diff)}{' '}
          <button type="button" className={linkButton} onClick={() => setInlineFor(identity)}>
            Show inline
          </button>
        </p>
      ) : null}
      {diff.coarse ? (
        <p className={muted} data-testid="intent-diff-coarse">
          The changed region is too long to align word by word; it is marked as one removed and one
          added run.
        </p>
      ) : null}
      {layout === 'inline' ? (
        <Prose parts={diff.parts} testid="intent-diff-inline" />
      ) : (
        <SideBySide parts={diff.parts} />
      )}
    </div>
  );
}

function ListItem({ row, field }: { row: ListRow; field: string }) {
  if (row.kind === 'unchanged') return <li data-item="unchanged">{row.text}</li>;
  if (row.kind === 'changed')
    return (
      <li data-item="changed">
        <TextPassage
          before={row.beforeText}
          after={row.afterText}
          field={`${field}.${row.after + 1}`}
        />
      </li>
    );
  if (row.kind === 'moved')
    return (
      <li data-item="moved">
        {row.text}{' '}
        <span className={tag}>
          moved from item {row.before + 1} to item {row.after + 1}
        </span>
      </li>
    );
  return (
    <li data-item={row.kind}>
      <Mark part={{ kind: row.kind, text: row.text }} />
      <span className={tag} aria-hidden="true">
        {row.kind} item
      </span>
    </li>
  );
}

// An ordered list (conditions, exclusions) aligned by exact item text (rule 1a).
function ListPassage({
  before,
  after,
  field,
}: {
  before: string[];
  after: string[];
  field: string;
}) {
  const rows = useMemo(() => alignLists(before, after), [before, after]);
  return (
    <ul className={items} data-testid="intent-diff-list" data-field={field}>
      {rows.map((row, index) => (
        <ListItem key={index} row={row} field={field} />
      ))}
    </ul>
  );
}

// The R06 indication beside a one-sided text: a known absence, or a side that could not be read.
function OneSidedLine({
  present,
  known,
  otherState,
  otherDetail,
}: {
  present: 'before' | 'after';
  known: boolean;
  otherState?: string;
  otherDetail?: string;
}) {
  const other = present === 'after' ? 'before' : 'after';
  const text = known
    ? present === 'after'
      ? 'added in after — before: known absent'
      : 'removed in after — after: known absent'
    : `recorded on the ${present} side only — ${other}: ${otherState}, not known absent` +
      (otherDetail ? ` (${otherDetail})` : '');
  return (
    <p className={muted} data-testid="intent-one-sided" data-known={String(known)}>
      {text}
    </p>
  );
}

// An added or removed statement (MIK-R31 rule 3's labelled prose) with its R06 indication.
function OneSidedStatement({
  before,
  after,
  added,
  layout,
}: {
  before: ReviewSideContent;
  after: ReviewSideContent;
  added: boolean;
  layout: DiffLayout;
}) {
  const [present, other] = added ? [after, before] : [before, after];
  if (present.state !== 'present')
    return <KnowledgeStatements before={before} after={after} mode={layout} />;
  return (
    <>
      <OneSidedLine
        present={added ? 'after' : 'before'}
        known={other.state === 'absent'}
        otherState={other.state}
        otherDetail={other.detail}
      />
      <p className={prose} data-testid="review-center-statement-prose">
        {present.text}
      </p>
    </>
  );
}

const FIELD_TITLES: Record<WordingField, string> = {
  statement: 'Statement',
  applicability: 'Applicability',
  conditions: 'Conditions',
  exclusions: 'Exclusions',
};

// One changed non-statement field. A null value is the record's known absence of the field; a list
// the comparison only reported joined (`projected`) is shown as reported, since its items are not
// known here and aligning the joined text would invent a list structure.
function FieldBody({
  field,
  before,
  after,
  projected,
}: {
  field: WordingField;
  before: AuthoredWording[WordingField];
  after: AuthoredWording[WordingField];
  projected: boolean;
}) {
  if (Array.isArray(before) && Array.isArray(after))
    return projected ? (
      <p className={muted} data-testid="intent-field-projected">
        before: {before.join('; ') || 'none'} · after: {after.join('; ') || 'none'}
      </p>
    ) : (
      <ListPassage before={before} after={after} field={field} />
    );
  if (typeof before === 'string' && typeof after === 'string')
    return <TextPassage before={before} after={after} field={field} />;
  const added = typeof after === 'string';
  return (
    <>
      <OneSidedLine present={added ? 'after' : 'before'} known />
      <p className={prose}>{String(added ? after : before)}</p>
    </>
  );
}

// The layout control is offered only where it changes something: a passage of changed words (not an
// identical or a whitespace-only text, which read the same either way).
const wordsPassage = (before: string, after: string) => textDiff(before, after).kind === 'words';

function drawsPassage(
  before: AuthoredWording[WordingField],
  after: AuthoredWording[WordingField],
  projected: boolean,
): boolean {
  if (Array.isArray(before) && Array.isArray(after))
    return (
      !projected &&
      alignLists(before, after).some(
        (row) => row.kind === 'changed' && wordsPassage(row.beforeText, row.afterText),
      )
    );
  return typeof before === 'string' && typeof after === 'string' && wordsPassage(before, after);
}

// Whether any changed field of the statement area draws a word-diffed passage.
function offersLayout(
  before: ReviewSideContent,
  after: ReviewSideContent,
  changed: WordingField[],
  sides: { before: AuthoredWording; after: AuthoredWording },
  projected: WordingField[],
): boolean {
  const statement =
    changed.includes('statement') &&
    before.state === 'present' &&
    after.state === 'present' &&
    wordsPassage(before.text ?? '', after.text ?? '');
  return (
    statement ||
    changed.some(
      (field) =>
        field !== 'statement' &&
        drawsPassage(sides.before[field], sides.after[field], projected.includes(field)),
    )
  );
}

function StatementText({
  before,
  after,
  changed,
  layout,
}: {
  before: ReviewSideContent;
  after: ReviewSideContent;
  changed: boolean;
  layout: DiffLayout;
}) {
  // No word diff is drawn against a side that is not present; each side keeps its own state line.
  if (before.state !== 'present' || after.state !== 'present')
    return <KnowledgeStatements before={before} after={after} mode={layout} />;
  return changed ? (
    <TextPassage before={before.text ?? ''} after={after.text ?? ''} field="statement" />
  ) : (
    <p className={prose} data-testid="review-center-statement-prose">
      {before.text}
    </p>
  );
}

// The statement area of a tree comparison's selected invariant. Ambiguous and unresolved selections
// never reach here: the landed body says that no diff is available.
export function IntentStatementBody({
  knowledge,
  selection,
  wording,
  sides,
  projected,
  layout,
}: {
  knowledge: ReviewKnowledgePane;
  selection: ReviewRevisionSelection;
  wording: WordingComparison;
  sides: { before: AuthoredWording; after: AuthoredWording };
  projected: WordingField[];
  layout: DiffLayout;
}) {
  const { before_statement: before, after_statement: after } = knowledge;
  if (selection.state === 'added' || selection.state === 'removed')
    return (
      <OneSidedStatement
        before={before}
        after={after}
        added={selection.state === 'added'}
        layout={layout}
      />
    );
  const changed = wording.kind === 'changed' ? wording.fields : [];
  const others = changed.filter((field) => field !== 'statement');
  const passages = offersLayout(before, after, changed, sides, projected);
  return (
    <>
      {passages ? <ProseLayoutControl /> : null}
      <StatementText
        before={before}
        after={after}
        changed={changed.includes('statement')}
        layout={layout}
      />
      {others.length ? (
        <div data-testid="review-center-changed-fields">
          {others.map((field) => (
            <section key={field} data-field={field} data-testid="intent-field-change">
              <p className={sideLabel}>{FIELD_TITLES[field]}</p>
              <FieldBody
                field={field}
                before={sides.before[field]}
                after={sides.after[field]}
                projected={projected.includes(field)}
              />
            </section>
          ))}
        </div>
      ) : null}
    </>
  );
}

// The guarantee's changed text, or `null` when the landed rendering answers (identical text, one
// side, none). Two sides at the same revision whose text differs are compared by their bytes too
// (a text record's revision increments only when its meaning changes, MIK-R21).
export function guaranteeTextChange(
  entry: ReviewFamilyContextEntry,
): { before: ReviewFamilyGuarantee; after: ReviewFamilyGuarantee } | null {
  const { guarantee: before } = entry.before;
  const { guarantee: after } = entry.after;
  if (!before || !after || before.joint_guarantee === after.joint_guarantee) return null;
  return { before, after };
}

// The center details' guarantee fact: the landed comparison kind, except that on a tree comparison one
// revision whose two texts differ is never called `unchanged_revision` (review R1 F2).
export function guaranteeFact(entry: ReviewFamilyContextEntry, tree: boolean): string {
  const kind = guaranteeComparison(entry).kind;
  return tree && kind === 'unchanged_revision' && guaranteeTextChange(entry)
    ? 'same_revision_text_changed'
    : kind;
}

export function GuaranteeTextChange({
  before,
  after,
}: {
  before: ReviewFamilyGuarantee;
  after: ReviewFamilyGuarantee;
}) {
  const same = before.revision_id === after.revision_id;
  const labels: [string, string] = same
    ? [before.display_version, after.display_version]
    : guaranteeRevisionLabels(before, after);
  return (
    <div data-testid="review-center-guarantee-changed" data-word-diff="true">
      <p className={muted} data-testid="review-center-guarantee-label">
        Changed guarantee · {revisionMeta(...labels)}
        {same ? ' · the same revision on both sides; its text differs' : ''}
      </p>
      <div className={card}>
        <p className={sideLabel}>Joint guarantee</p>
        {wordsPassage(before.joint_guarantee, after.joint_guarantee) ? (
          <ProseLayoutControl />
        ) : null}
        <TextPassage
          before={before.joint_guarantee}
          after={after.joint_guarantee}
          field="guarantee"
        />
        <details>
          <summary>Revision records</summary>
          <p className={muted}>
            before revision {before.revision_id} · after revision {after.revision_id}
          </p>
        </details>
      </div>
    </div>
  );
}

// The label of a guarantee recorded on one side only, with its R06 indication.
export function OneSidedGuaranteeLabel({
  side,
  other,
}: {
  side: 'before' | 'after';
  other: ReviewFamilyRevisionContext;
}) {
  const known = other.state === 'not_recorded';
  const heading = !known
    ? 'Guarantee recorded on one side only'
    : side === 'after'
      ? 'Added guarantee'
      : 'Removed guarantee';
  return (
    <>
      <p className={muted}>{heading}</p>
      <OneSidedLine
        present={side}
        known={known}
        otherState={other.state}
        otherDetail={other.detail}
      />
    </>
  );
}
