import { useContext } from 'react';

import { css } from '../../../styled-system/css';
import { GuaranteeComparisonBlock } from './FamilyGuarantees';
import {
  FamilyMemberStatement,
  memberStatementPairs,
} from './family-member-statement/FamilyMemberStatement';
import { shell, sectionLabel, card, muted, rows, linkButton } from './familyCenterStyles';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyMember,
  ReviewFamilySideName,
  ReviewPayload,
  ReviewOutline,
  ReviewSourceLocation,
} from '../../data/review';
import {
  carriedMembership,
  familyExpressionExcerpts,
  type FamilyMembershipRow,
  type FamilyExpressionCollection,
  type FamilyExpressionExcerpt,
  type FamilyExcerptOccurrence,
} from './familyExpressions';
import { FAMILY_SIDES } from '../../data/review';
import { treeComparisonNumber, type ReviewTreesRead } from '../../data/reviewTrees';
import { MemberFamilyLabel, useInvariantTargetState } from './MarkerTargetState';
import { IntentWordDiffScope, guaranteeFact } from './IntentWordDiff';
import { useProseLayout } from './intentDiffPreference';
import { SubjectKnowledge } from './SubjectWorklistIdentity';
import { familyTriage, memberChanges, orderMemberRows, outlineGroups } from './changeTriage';
import {
  FamilyBreakdown,
  MemberChangeBadge,
  useMemberChange,
  GuaranteeChangeBadge,
} from './ChangeBadges';
import { useTreeOrder } from './triageOrderPreference';
import type { ReviewSort } from './reviewSelection';
import type { DiffMode as DiffLayout } from '../changeset/DiffPane';

import { KnowledgeStatements } from './KnowledgeStatements';
import { MeaningBlock, MeaningFieldGuard } from './MeaningEffects';
import { ReadingContext } from './reading/readingContext';
import {
  SelectedStatement,
  SubjectJudgment,
  selectedRevision,
  type MemberSides,
} from './SubjectReview';
import type { ReviewSubject } from './ReviewNavigation';
import { RosterLine, RosterNext, emptyRosterSentence, type FamilySelection } from './FamilyTree';
import { tokenWords } from './stateWords';

function RealizationClaims({ member }: { member: ReviewFamilyMember }) {
  if (!member.sources.length) {
    return (
      <p className={muted} data-testid="review-center-expressions">
        No realization claim has loaded for this member revision. Any roster continuation below
        reaches source links that are not yet loaded.
      </p>
    );
  }
  return (
    <ul className={rows} data-review-content="rationale" data-testid="review-center-expressions">
      {member.sources.map((claim) => (
        <li key={claim.claim_id}>
          claim {claim.claim_id} · role: {claim.role}
          {claim.path ? (
            <>
              {' '}
              · recorded address: <code>{claim.path}</code>
              {claim.resolution ? ` · this read resolved it as ${claim.resolution}` : ''}
            </>
          ) : (
            ' · this read observed no address for it'
          )}
          <div className={muted}>{claim.rationale}</div>
          <div className={muted}>{claim.detail}</div>
        </li>
      ))}
    </ul>
  );
}

function unlistedPathNote(listedPartial: boolean): string {
  return listedPartial
    ? 'this path is not among the paths this inventory listed, and that inventory is partial: absence from its list is not a measurement that the path did not change.'
    : "this path is not a changed path of the comparison's measured change set.";
}

function AttributedPaths({
  locations,
  member,
  listed,
  listedPartial,
  onOpenPath,
}: {
  locations: ReviewSourceLocation[];
  member: ReviewFamilyMember;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  const mine = locations.filter(
    (location) => location.invariant_revision_id === member.invariant_revision_id,
  );
  if (!mine.length) {
    return (
      <p className={muted} data-testid="review-center-attribution">
        The primary comparison page returned no source location for this member revision.
      </p>
    );
  }
  return (
    <ul className={rows} data-review-content="rationale" data-testid="review-center-attribution">
      {mine.map((location) => (
        <li key={`${location.claim_id}:${location.path}`} data-change-state={location.change_state}>
          {listed.has(location.path) ? (
            <>
              <button
                type="button"
                className={linkButton}
                data-testid="review-center-open-path"
                data-path={location.path}
                onClick={() => onOpenPath(location.path)}
              >
                {location.path}
              </button>{' '}
              · listed among this comparison&apos;s measured changed paths
            </>
          ) : (
            <code>{location.path}</code>
          )}{' '}
          · role: {location.role ?? 'unclassified (no role recorded)'} · {location.change_state}
          {location.before_only ? ' · before-only' : ''} · {location.resolution}
          {!listed.has(location.path) ? (
            <div className={muted}>{unlistedPathNote(listedPartial)}</div>
          ) : null}
          {location.rationale ? <div className={muted}>{location.rationale}</div> : null}
          {location.reached_via.length ? (
            <div className={muted}>reached via: {location.reached_via.join(', ')}</div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function UnselectedCenter({
  payload,
  knowledge,
}: {
  payload: ReviewPayload;
  knowledge: React.ReactNode;
}) {
  const selected = payload.knowledge.selection_state === 'subject_selected';
  return (
    <section className={shell} data-testid="review-center-unselected">
      {selected ? null : <h2 className={sectionLabel}>Source review</h2>}
      <MeaningFieldGuard payload={payload} />
      {selected ? (
        <KnowledgeStatements
          before={payload.knowledge.before_statement}
          after={payload.knowledge.after_statement}
        />
      ) : (
        <p className={muted}>
          Source-only view. Inspect the changed files in All changes; no intent attribution is
          claimed.
        </p>
      )}
      {knowledge}
    </section>
  );
}

function familyExpressionVerdict(collection: FamilyExpressionCollection): string {
  const { rows, excerpts, membershipRows, distinctRevisions } = collection;
  const distinct = excerpts.length;
  const removed = rows - distinct;
  const sides = collection.bySide.map((entry) => `${entry.side} ${entry.rows}`).join(' + ');
  if (rows === 0) {
    return (
      `No changed expression excerpt is present in the loaded claim scope. ${collection.resolved} ` +
      `loaded realization claim(s) resolved to their recorded bytes; ${collection.unmeasured} ` +
      'loaded claim(s) remain unmeasured. These loaded facts do not establish that unreturned expressions are unchanged.'
    );
  }
  const collapse =
    removed === 0
      ? `${rows} changed expression row(s) across the family's ${membershipRows} carried membership row(s) ` +
        `(${sides}; ${distinctRevisions} distinct member revision(s)) are ${distinct} distinct excerpt(s): ` +
        'no two rows named one address with the same recorded bytes.'
      : `${rows} changed expression row(s) across the family's ${membershipRows} carried membership row(s) ` +
        `(${sides}; ${distinctRevisions} distinct member revision(s)) collapse to ${distinct} distinct ` +
        `excerpt(s): ${removed} row(s) named an address and recorded bytes another row already named, and ` +
        'each such row is listed once below with every membership row that recorded it.';
  const membership =
    collection.membershipRowsWithoutChanged === 0
      ? `every one of the ${membershipRows} carried membership row(s) recorded at least one changed expression.`
      : `${collection.membershipRowsWithChanged} of the ${membershipRows} carried membership row(s) recorded a ` +
        `changed expression and ${collection.membershipRowsWithoutChanged} recorded none; the roster above keeps ` +
        'all of them. A member with no loaded changed expression may still have unreturned claims.';
  const notion =
    "Changed here is the read's own resolution of the address each claim names, on the side that resolved " +
    "it: the recorded bytes were not the bytes at that address in that side's tree — the stale state " +
    '(recorded_blob_mismatch) and the unresolved states (path_absent, unsupported_locator, entry_not_blob). ' +
    'A resolution is a fact about one side, so the same member revision can resolve an address on one side ' +
    "and mismatch it on the other: every row below prints what each side's read made of its address — the " +
    "resolution that side's claim carried — so an address both sides carried prints both readings whenever " +
    "they differ, and an address only one side carried prints that side's alone. This is the realization " +
    'resolution, not ' +
    "the comparison's own measured change set, which All changes opens separately; each row " +
    'says where its address stands in that set.';
  const unmeasured =
    collection.unmeasured === 0
      ? 'no loaded claim was left unmeasured.'
      : `${collection.unmeasured} claim(s) this read did not measure (recorded_object_unavailable, not_requested) ` +
        'are counted apart and are not called changed.';
  return `${collapse} ${membership} ${notion} ${unmeasured}`;
}

function FamilyExpressionRow({
  excerpt,
  listed,
  listedPartial,
  onOpenPath,
}: {
  excerpt: FamilyExpressionExcerpt;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  return (
    <li
      data-testid="review-center-family-expression"
      data-path={excerpt.path}
      data-dedup-key={excerpt.key}
      data-collapsed-rows={excerpt.rows}
      data-membership-rows={excerpt.occurrences.length}
      data-member-revisions={excerpt.revisions.join(',')}
      data-sides={excerpt.readingsBySide.map((entry) => entry.side).join(',')}
    >
      {listedOrPlainPath(excerpt.path, onOpenPath, listed)} · role(s): {excerpt.roles.join(', ')}
      <div className={muted} data-testid="review-center-family-expression-resolution">
        this read resolved it as:{' '}
        {excerpt.readingsBySide
          .map((entry) => `${entry.side} ${entry.resolutions.join(' and ')}`)
          .join(' · ')}
      </div>
      <div className={muted}>
        recorded under {excerpt.occurrences.length} carried membership row(s) over{' '}
        {excerpt.revisions.length} distinct member revision(s):{' '}
        {excerpt.occurrences.map((occurrence) => occurrenceName(occurrence)).join(', ')} ·{' '}
        {excerpt.rows} changed expression row(s) collapsed into this excerpt
      </div>
      <div className={muted}>
        recorded source identity: {excerpt.recorded ?? 'not recorded'} · observed source identity:{' '}
        {excerpt.readingsBySide
          .map((entry) => `${entry.side} ${entry.observed.join(' and ') || 'not observed'}`)
          .join(' · ')}
      </div>
      {excerpt.details.map((detail) => (
        <div className={muted} key={detail}>
          {detail}
        </div>
      ))}
      {listed.has(excerpt.path) ? (
        <div className={muted}>
          this path is a changed path of the comparison&apos;s measured change set.
        </div>
      ) : (
        <div className={muted}>{unlistedPathNote(listedPartial)}</div>
      )}
    </li>
  );
}

function occurrenceName(occurrence: FamilyExcerptOccurrence): string {
  const label = occurrence.label === undefined ? '' : `${occurrence.label} · `;
  return `${label}${occurrence.revision} (${occurrence.side})`;
}

function listedOrPlainPath(path: string, onOpenPath: (path: string) => void, listed: Set<string>) {
  if (!listed.has(path)) return <code>{path}</code>;
  return (
    <button
      type="button"
      className={linkButton}
      data-testid="review-center-family-expression-open"
      data-path={path}
      onClick={() => onOpenPath(path)}
    >
      {path}
    </button>
  );
}

function FamilyExpressionExcerpts({
  entry,
  membership,
  listed,
  listedPartial,
  onOpenPath,
}: {
  entry: ReviewFamilyContextEntry;
  membership: FamilyMembershipRow[];
  listed: Set<string>;
  listedPartial: boolean;
  onOpenPath: (path: string) => void;
}) {
  const collection = familyExpressionExcerpts(membership);
  return (
    <div className={card} data-testid="review-center-family-expressions">
      <h3 className={sectionLabel}>Changed expression excerpts in this family</h3>
      <p className={muted} data-testid="review-center-family-expressions-scope">
        {membersComplete(entry)
          ? 'The selected family roster is fully loaded; the facts below describe its loaded claims.'
          : 'Source links are not yet fully loaded or their scope is unavailable. Only loaded claim facts are described; use any roster continuation above to reach the rest.'}
      </p>
      <p className={muted} data-testid="review-center-family-expressions-verdict">
        {familyExpressionVerdict(collection)}
      </p>
      {collection.excerpts.length ? (
        <ul className={rows}>
          {collection.excerpts.map((excerpt) => (
            <FamilyExpressionRow
              key={excerpt.key}
              excerpt={excerpt}
              listed={listed}
              listedPartial={listedPartial}
              onOpenPath={onOpenPath}
            />
          ))}
        </ul>
      ) : (
        <p className={muted} data-testid="review-center-family-expressions-none">
          No changed expression excerpt is present among the loaded claims. This does not establish
          that unavailable or unreturned expressions are unchanged.
        </p>
      )}
    </div>
  );
}
function membersComplete(entry: ReviewFamilyContextEntry): boolean {
  return FAMILY_SIDES.map((side) => entry[side]).every(
    (side) =>
      side.state === 'not_recorded' ||
      (side.state === 'recorded' &&
        side.page?.complete &&
        side.members.length === side.members_total &&
        side.members.every((member) => member.state === 'recorded')),
  );
}

function memberContextHeading(entry: ReviewFamilyContextEntry): string {
  if (entry.state === 'recorded' && membersComplete(entry)) {
    return 'Complete recorded member context';
  }
  return 'Recorded member context (partial)';
}

function memberContextCounts(entry: ReviewFamilyContextEntry, carriedCarried: number): string {
  const recorded = FAMILY_SIDES.map((side) => entry[side]).filter(
    (side) => side.state === 'recorded',
  );
  if (recorded.length === 0)
    return `no snapshot records a family revision for this family: ${entry.detail}`;
  const measured = recorded.reduce((total, side) => total + side.members_total, 0);
  const perSide = recorded.map((side) => `${side.side} ${side.members_total}`).join(' + ');
  const head = `${measured} recorded membership row(s) measured by the read across ${recorded.length} recorded side(s) (${perSide}); loaded context contains ${carriedCarried} member row(s) of them`;
  return membersComplete(entry)
    ? head
    : `${head} · the continuations beside the bounded rosters reach the rest`;
}

function FamilyMemberContext({
  entry,
  distinct,
  onOpenMember,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  distinct: ReviewFamilyMember[];
  onOpenMember: (memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const statements = memberStatementPairs(entry);
  return (
    <div className={card} data-testid="review-center-family-members">
      <h3 className={sectionLabel} data-testid="review-center-member-heading">
        {memberContextHeading(entry)}
      </h3>
      <p className={muted}>
        {distinct.length} member revisions shown{membersComplete(entry) ? '' : ' · partial roster'}
      </p>
      <details data-disclosure="provenance">
        <summary>Member and roster counts</summary>
        <p className={muted} data-testid="review-center-member-distinct">
          {distinct.length} distinct member revision(s) among the loaded membership contexts
        </p>
      </details>
      <p className={muted} data-testid="review-center-member-counts">
        {memberContextCounts(entry, distinct.length)}
      </p>
      {FAMILY_SIDES.map((side) => (
        <div key={side} data-review-content="roster-state">
          <RosterLine side={entry[side]} testid="review-center-roster" />
        </div>
      ))}
      <ul className={rows}>
        {distinct.map((member) => (
          <li key={member.invariant_revision_id}>
            <button
              type="button"
              className={linkButton}
              data-testid="review-center-open-member"
              data-revision={member.invariant_revision_id}
              onClick={() => onOpenMember(member.invariant_revision_id)}
            >
              {member.display_label ?? member.invariant_revision_id}
            </button>
            <MemberMeaning entry={entry} member={member} />
            <FamilyMemberStatement
              pair={statements.get(member.invariant_id ?? '')}
              member={member}
            />
          </li>
        ))}
      </ul>
      {distinct.length === 0 ? (
        <p className={muted} data-testid="review-center-family-empty">
          {emptyRosterSentence(entry)}
        </p>
      ) : null}
      <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
    </div>
  );
}

// The tree's displayed member order for one family as a rank per member row: the outline
// group's member sequence under the store's order — the same grouping the sidebar draws
// (outlineGroups), so the center list and the tree cannot drift apart. Undefined when the
// outline does not cover the family; the caller keeps its own order then.
function displayedMemberRanks(
  entry: ReviewFamilyContextEntry,
  walked: readonly ReviewFamilyContextEntry[],
  outline: ReviewOutline | undefined,
  order: ReviewSort,
): ((row: ReviewFamilyMember) => number) | undefined {
  if (!outline || outline.state !== 'outlined') return undefined;
  const group = outlineGroups(outline, [], walked, order).find(
    (one) => one.family?.selectorId === entry.family_id,
  );
  if (!group) return undefined;
  const position = new Map(group.members.map((row, index) => [row.selectorId, index]));
  return (row) =>
    row.invariant_id === undefined
      ? Number.MAX_SAFE_INTEGER
      : (position.get(row.invariant_id) ?? Number.MAX_SAFE_INTEGER);
}

// The family's member revisions in the order the center lists them: the tree's displayed order
// through the outline where the comparison served one, else the entry's own order.
function orderedCenterMembers(
  entry: ReviewFamilyContextEntry,
  walked: readonly ReviewFamilyContextEntry[],
  outline: ReviewOutline | undefined,
  order: ReviewSort,
): ReviewFamilyMember[] {
  const members = [...entry.before.members, ...entry.after.members];
  const rows = [...new Map(members.map((m) => [m.invariant_revision_id, m])).values()].map(
    (member) => ({
      member,
    }),
  );
  const displayed = displayedMemberRanks(entry, walked, outline, order);
  if (displayed) rows.sort((a, b) => displayed(a.member) - displayed(b.member));
  return (displayed ? rows : orderMemberRows(entry, rows, order)).map(({ member }) => member);
}

// The members in the order the page's model draws them under this family; members the model does not
// draw (the Changed view hides them) follow in their own order.
function inModelOrder(members: ReviewFamilyMember[], order: readonly string[] | undefined): ReviewFamilyMember[] {
  if (!order?.length) return members;
  const rank = new Map(order.map((id, index) => [id, index]));
  const at = (member: ReviewFamilyMember) => rank.get(member.invariant_id ?? '') ?? Number.MAX_SAFE_INTEGER;
  return [...members].sort((a, b) => at(a) - at(b));
}

// The two parts of a selected subject's reading (MIK-R89 v2 rules 1 and 6, amended 9dde2b09): its
// content under its heading, in the mockup's order (the word diff, the recorded steps and the recorded
// judgment), and the rest after its code. `whole` draws both, the content first, where no code
// follows (a subject without a section of its own).
export type CenterPart = 'whole' | 'content' | 'after-code';

// The parts stand unframed in the column: no box and no "Selected …" heading (9dde2b09).
const flow = css({ display: 'grid', gap: '1rem', minWidth: 0, alignContent: 'start' });

// The content keeps the mockup's reading measure, a narrower column than the code (styles.css
// `.statement { max-width: 92ch }`; MIK-R89 v2 rule 1, d9a1ec8f item 6). Side by side spreads the
// wording's two columns over the column, so it lifts the measure (MIK-R35 rule 4).
const measured = css({ '&[data-measure="reading"]': { maxWidth: '92ch' } });
const useMeasure = (): 'reading' | 'wide' => (useProseLayout() === 'side-by-side' ? 'wide' : 'reading');

// A family's content: its guarantee's word diff, its recorded steps and its recorded judgment. Its
// state sentences and the record's provenance are Technical details' (rule 6).
function FamilyContent({
  entry,
  layout,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  layout: DiffLayout;
  payload: ReviewPayload;
}) {
  const measure = useMeasure();
  return (
    <section
      className={`${flow} ${measured}`}
      data-testid="review-center-family"
      data-family={entry.family_id}
      data-measure={measure}
    >
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <MeaningBlock payload={payload} record={entry.family_id} />
      <FamilyJudgment entry={entry} payload={payload} />
    </section>
  );
}

// A family's members and their resolution, after its members' code.
function FamilyDetails({
  entry,
  listed,
  listedPartial,
  onOpenMember,
  onOpenPath,
  onRosterNext,
  knowledge,
  payload,
  outline,
}: {
  entry: ReviewFamilyContextEntry;
  knowledge: React.ReactNode;
  payload: ReviewPayload;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenMember: (memberRevisionId: string) => void;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  // The entries answer's tree outline, when served: the member list follows the tree's order.
  outline?: ReviewOutline;
}) {
  const storeOrder = useTreeOrder();
  const walked = payload.family_context?.entries ?? [];
  // On the review page the list is the change-set model's order for this family (MIK-R89 v2 rule 3,
  // one order; INV-719D48); elsewhere the outline's.
  const modelOrder = useContext(ReadingContext)?.familyMembers.get(entry.family_id);
  const distinct = inModelOrder(orderedCenterMembers(entry, walked, outline, storeOrder), modelOrder);
  return (
    <section className={flow} data-testid="review-center-family-details" data-family={entry.family_id}>
      <GuaranteeChangeBadge kinds={entry.change_kinds} />
      <FamilyBreakdown triage={familyTriage(entry)} />
      <FamilyMemberContext
        entry={entry}
        distinct={distinct}
        onOpenMember={onOpenMember}
        onRosterNext={onRosterNext}
      />
      <section data-review-content="resolution">
        <p>Realization resolution details</p>
        <FamilyExpressionExcerpts
          entry={entry}
          membership={carriedMembership(entry)}
          listed={listed}
          listedPartial={listedPartial}
          onOpenPath={onOpenPath}
        />
      </section>
      {knowledge}
    </section>
  );
}

function IndependentFacts({
  entry,
  payload,
  subject,
}: {
  entry?: ReviewFamilyContextEntry;
  payload: ReviewPayload;
  subject: ReviewSubject;
}) {
  const selected = selectedRevision(payload.knowledge, subject);
  if (!selected)
    return (
      <p className={muted} data-testid="review-center-facts">
        The selected subject's comparison is unavailable.
      </p>
    );
  const revisions = new Set([...selected.before_retained, ...selected.after_retained]);
  const locations = payload.source.locations.filter(
    (row) => row.invariant_revision_id && revisions.has(row.invariant_revision_id),
  );
  const members = entry
    ? [...entry.before.members, ...entry.after.members].filter(
        (row) => row.invariant_id === subject.id,
      )
    : [];
  return (
    <ul className={rows} data-testid="review-center-facts">
      <li data-fact="guarantee">
        guarantee: {entry ? guaranteeFact(entry) : 'no family guarantee selected'}
      </li>
      <li data-fact="statement">
        member statement: {selected.state} · before {selected.before_revision_id ?? 'not selected'}{' '}
        → after {selected.after_revision_id ?? 'not selected'}
      </li>
      <li data-fact="membership">
        membership: {members.length} member rows on the displayed family sides ·{' '}
        {members.reduce((n, row) => n + row.sources.length, 0)} recorded realization claim(s) on
        those rows
      </li>
      <li data-fact="source">
        source attribution: {locations.length} location record(s) name this member's retained
        revisions
      </li>
      <li data-fact="judgment">
        No member, membership or guarantee change creates an authored judgment. Read Judgment/proofs
        below for the selected subject.
      </li>
    </ul>
  );
}

function SharedMembership({ member }: { member?: ReviewFamilyMember }) {
  if (!member?.other_family_revision_ids.length) return null;
  return (
    <p className={muted}>
      Shared member · other family revisions: {member.other_family_revision_ids.join(', ')}
    </p>
  );
}

// The invariant's family line only when a followed marker's membership is unknown (MIK-R34): the
// family's name, or its chip, is the meta line's, and no family reads as nothing (9dde2b09).
function MemberTargetLine({
  entry,
  payload,
  subject,
}: {
  entry?: ReviewFamilyContextEntry;
  payload: ReviewPayload;
  subject: ReviewSubject;
}) {
  const target = useInvariantTargetState(subject, payload.family_context);
  if (!target) return null;
  return (
    <p className={muted} data-testid="review-center-member-family">
      <MemberFamilyLabel subject={subject} payload={payload} entry={entry} />
    </p>
  );
}

// An invariant's content: its statement's word diff under the header that holds the wording-layout
// switch, its recorded steps and its recorded judgment.
function MemberContent({
  entry,
  layout,
  payload,
  subject,
}: {
  entry?: ReviewFamilyContextEntry;
  layout: DiffLayout;
  payload: ReviewPayload;
  subject: ReviewSubject;
}) {
  const measure = useMeasure();
  return (
    <section
      className={`${flow} ${measured}`}
      data-testid="review-center-member"
      data-family={entry?.family_id}
      data-subject={subject.id}
      data-measure={measure}
    >
      <MemberTargetLine entry={entry} payload={payload} subject={subject} />
      <SelectedStatement
        knowledge={payload.knowledge}
        subject={subject}
        layout={layout}
        members={subjectRows(entry, subject)}
      />
      <MeaningBlock payload={payload} record={subject.id} />
      <SubjectJudgment payload={payload} subject={subject} />
    </section>
  );
}

// What else the invariant's read carries, after its code: the family's guarantee it was selected
// under, its membership and realization facts, its worklist items and its roster continuation.
function MemberDetails({
  entry,
  member,
  layout,
  payload,
  listed,
  onOpenPath,
  onRosterNext,
  knowledge,
  subject,
}: {
  entry?: ReviewFamilyContextEntry;
  member?: ReviewFamilyMember;
  layout: DiffLayout;
  payload: ReviewPayload;
  listed: Set<string>;
  subject: ReviewSubject;
  knowledge: React.ReactNode;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <section
      className={flow}
      data-testid="review-center-member-details"
      data-family={entry?.family_id}
      data-subject={subject.id}
    >
      {entry ? <GuaranteeComparisonBlock entry={entry} layout={layout} /> : null}
      <SharedMembership member={member} />
      {entry && member ? <MemberMeaning entry={entry} member={member} /> : null}
      <section data-review-content="resolution">
        <p>Statement, membership and realization details</p>
        <IndependentFacts entry={entry} payload={payload} subject={subject} />
        {member ? (
          <div className={card}>
            <h3 className={sectionLabel}>Selected roster revision context</h3>
            <RealizationClaims member={member} />
            <AttributedPaths
              locations={payload.source.locations}
              member={member}
              listed={listed}
              listedPartial={payload.source.inventory.partial}
              onOpenPath={onOpenPath}
            />
          </div>
        ) : null}
      </section>
      {knowledge}
      {entry ? (
        <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
      ) : null}
    </section>
  );
}

// The subject's rows per side: a row's side is kept, so each side's text is read from its own row.
function subjectRows(
  entry: ReviewFamilyContextEntry | undefined,
  subject: ReviewSubject,
): MemberSides {
  const own = (side: ReviewFamilySideName) =>
    entry ? entry[side].members.filter((row) => row.invariant_id === subject.id) : [];
  return { before: own('before'), after: own('after') };
}

function UnavailableMember({
  entry,
  member,
  layout,
  onRosterNext,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
  layout: DiffLayout;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  payload: ReviewPayload;
}) {
  return (
    <section className={shell} data-testid="review-center-member-context">
      <h2 className={sectionLabel}>Member revision context</h2>
      <MeaningFieldGuard payload={payload} />
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <p data-review-content="statement">{member.statement ?? member.detail}</p>
      <p className={muted} data-testid="review-center-judgment-unavailable">
        This page did not supply the member's invariant identity. Continue the roster to open its
        own review; no judgment absence is claimed.
      </p>
      <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
    </section>
  );
}

// The selected subject's own reading. Its code locations are not here: they are the subject's
// blocks in its section of the reading column, from the page's one card read (MIK-R89 v2 W4).
interface FamilyReviewCenterProps {
  payload: ReviewPayload;
  subject?: ReviewSubject;
  selection: FamilySelection | null;
  layout: DiffLayout;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  onOpenFromCenter: (path: string) => void;
  // The leaf's measured tree view, read once per task context by the workspace.
  leafTrees?: ReviewTreesRead | null;
  outline?: ReviewOutline;
  // Which part of the reading is drawn here; `whole` when not given.
  part?: CenterPart;
}

// The center of one review. A tree comparison's intent wording is word-diffed inside it (MIK-R35).
export function FamilyReviewCenter(props: FamilyReviewCenterProps) {
  return (
    <IntentWordDiffScope payload={props.payload}>
      <ReviewCenterBody {...props} />
    </IntentWordDiffScope>
  );
}

function ReviewCenterBody(props: FamilyReviewCenterProps) {
  const { payload, subject, selection, layout, onRosterNext } = props;
  const { entry, member, listed } = centerSelection(payload, selection);
  const addressedSubject = member && !member.invariant_id ? undefined : subject;
  const invariant = addressedSubject?.kind === 'invariant';
  const common = {
    payload,
    layout,
    listed,
    onOpenPath: props.onOpenFromCenter,
    onRosterNext,
    knowledge: (
      <SubjectKnowledge
        read={props.leafTrees}
        outline={props.outline}
        subject={addressedSubject}
        familyId={entry?.family_id}
        comparison={treeComparisonNumber(payload.limitations)}
      />
    ),
  };

  return (
    <SelectedCenter
      props={props}
      common={common}
      entry={entry}
      member={member}
      addressedSubject={addressedSubject}
      invariant={invariant}
    />
  );
}
function SelectedCenter({
  props,
  common,
  entry,
  member,
  addressedSubject,
  invariant,
}: {
  props: FamilyReviewCenterProps;
  common: {
    payload: ReviewPayload;
    layout: DiffLayout;
    listed: Set<string>;
    onOpenPath: (path: string) => void;
    onRosterNext: FamilyReviewCenterProps['onRosterNext'];
    knowledge: React.ReactNode;
  };
  entry?: ReviewFamilyContextEntry;
  member?: ReviewFamilyMember;
  addressedSubject?: ReviewSubject;
  invariant: boolean;
}) {
  const { payload, selection, layout, onRosterNext, onOpenMember } = props;
  const part = props.part ?? 'whole';
  if (invariant)
    return (
      <CenterParts
        part={part}
        kind="member"
        content={<MemberContent entry={entry} layout={layout} payload={payload} subject={addressedSubject!} />}
        details={<MemberDetails {...common} entry={entry} member={member} subject={addressedSubject!} />}
      />
    );
  if (entry !== undefined && !member)
    return (
      <CenterParts
        part={part}
        kind="family"
        content={<FamilyContent entry={entry} layout={layout} payload={payload} />}
        details={
          <FamilyDetails
            {...common}
            entry={entry}
            listedPartial={payload.source.inventory.partial}
            onOpenMember={(revision) => onOpenMember(entry.family_id, revision)}
            outline={props.outline}
          />
        }
      />
    );
  // A subject with no family entry, or a member whose identity this page did not supply: its own
  // state, drawn whole with the content.
  if (part === 'after-code') return null;
  return (
    <div
      data-testid="review-center"
      data-selection-kind={selection === null ? 'none' : member ? 'member' : 'family'}
    >
      {entry === undefined ? (
        <UnselectedCenter payload={payload} knowledge={common.knowledge} />
      ) : (
        <UnavailableMember
          entry={entry}
          member={member!}
          layout={layout}
          payload={payload}
          onRosterNext={onRosterNext}
        />
      )}
    </div>
  );
}

// The content and the details of one reading, as the part asks.
function CenterParts({
  part,
  kind,
  content,
  details,
}: {
  part: CenterPart;
  kind: 'member' | 'family';
  content: React.ReactNode;
  details: React.ReactNode;
}) {
  return (
    <>
      {part === 'after-code' ? null : (
        <div data-testid="review-center" data-selection-kind={kind}>
          {content}
        </div>
      )}
      {part === 'content' ? null : details}
    </>
  );
}

function MemberMeaning({
  entry,
  member,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
}) {
  const state = useMemberChange(
    entry.change_kinds ? memberChanges(entry) : undefined,
    member.member_id,
    entry.family_id,
    member.invariant_revision_id,
  );
  return (
    <div data-review-content="change-facts">
      <MemberChangeBadge state={state} />
      {state?.change.unknown_reasons.slice(1).map((reason) => (
        <p key={reason} className={muted}>
          change kind unknown: {reason}
        </p>
      ))}
      {state?.change.membership_reasons.slice(1).map((reason) => (
        <p key={reason} className={muted}>
          membership unknown: {reason}
        </p>
      ))}
      <p className={muted}>
        {[...entry.before.members].some((one) => one.member_id === member.member_id)
          ? 'before'
          : ''}
        {[...entry.after.members].some((one) => one.member_id === member.member_id)
          ? ' · after'
          : ''}{' '}
        · {tokenWords(member.state)}
      </p>
    </div>
  );
}

function centerSelection(payload: ReviewPayload, selection: FamilySelection | null) {
  const entry = payload.family_context?.entries.find(
    (candidate) => candidate.family_id === selection?.familyId,
  );
  const all = entry ? [...entry.before.members, ...entry.after.members] : [];
  const member = all.find(
    (candidate) => candidate.invariant_revision_id === selection?.memberRevisionId,
  );
  const listed = new Set(payload.source.inventory.entries.map((row) => row.path));
  return { entry, member, listed };
}

function FamilyJudgment({
  entry,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  payload: ReviewPayload;
}) {
  return (
    <div data-testid="review-family-judgment">
      <SubjectJudgment payload={payload} subject={{ kind: 'family', id: entry.family_id }} />
    </div>
  );
}
