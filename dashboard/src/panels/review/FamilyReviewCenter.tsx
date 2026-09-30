import { Fragment } from 'react';

import { css } from '../../../styled-system/css';
import type {
  ReviewFamilyContextEntry,
  ReviewFamilyGuarantee,
  ReviewFamilyMember,
  ReviewFamilySideName,
  ReviewPayload,
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
import { FAMILY_SIDES, guaranteeComparison } from '../../data/review';
import {
  treeComparisonNumber,
  useReviewTreeEntries,
  type ReviewTreesRead,
} from '../../data/reviewTrees';
import { ExpressionCards } from './ExpressionCards';
import { LeafKnowledgeChanges } from './LeafKnowledgeChanges';
import { revisionMeta } from './statementWording';
import { planningMarks } from './worklistGroups';
import { cardScope } from './focusedCards';
import { DiffPane } from '../changeset/DiffPane';
import type { DiffLayout } from './SourceExplorer';
import { ReviewExpressions } from './ReviewExpressions';
import { KnowledgeStatements } from './KnowledgeStatements';
import { SelectedStatement, SubjectEvidence, selectedRevision } from './SubjectReview';
import type { ReviewSubject } from './ReviewNavigation';
import { RosterLine, RosterNext, emptyRosterSentence, type FamilySelection } from './FamilyTree';

const shell = css({
  background: 'bgPanel',
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '1.1rem',
  minWidth: '0',
  display: 'grid',
  gap: '1.2rem',
  alignContent: 'start',
});

const sectionLabel = css({
  color: 'cyan',
  fontSize: '0.72rem',
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  margin: '0 0 0.25rem',
});

const card = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  padding: '0.5rem 0.6rem',
  background: 'bg',
  minWidth: '0',
});

const muted = css({ color: 'muted', fontSize: '0.8rem', margin: '0.2rem 0' });

const prose = css({
  whiteSpace: 'pre-wrap',
  margin: '0.2rem 0',
  fontSize: '0.9rem',
  lineHeight: '1.8',
});

const rows = css({ margin: '0.2rem 0', paddingLeft: '1.1rem' });

const linkButton = css({
  background: 'transparent',
  border: 'none',
  color: 'amber',
  cursor: 'pointer',
  font: 'inherit',
  padding: '0',
  textDecoration: 'underline',
  _focusVisible: { outline: '1px solid var(--amber)', outlineOffset: '2px' },
});

function GuaranteeBlock({ guarantee, side }: { guarantee: ReviewFamilyGuarantee; side?: string }) {
  return (
    <div
      className={card}
      data-testid="review-center-guarantee"
      data-revision={guarantee.revision_id}
    >
      <p className={sectionLabel}>Joint guarantee{side ? ` · ${side}` : ''}</p>
      <p className={prose}>{guarantee.joint_guarantee}</p>
      <details>
        <summary>Guarantee record</summary>
        <p className={muted}>
          family {guarantee.family_id}
          {side ? ` · ${side} snapshot` : ''} · revision {guarantee.revision_id} · version{' '}
          {guarantee.display_version} · state at origin: {guarantee.state_at_origin}
          {guarantee.acceptance_ref ? ` · accepted by ${guarantee.acceptance_ref}` : ''}
        </p>
      </details>
    </div>
  );
}

function GuaranteeComparisonBlock({
  entry,
  layout,
}: {
  entry: ReviewFamilyContextEntry;
  layout: DiffLayout;
}) {
  const comparison = guaranteeComparison(entry);
  if (comparison.kind === 'unrecorded') {
    return (
      <p className={muted} data-testid="review-center-guarantee-unrecorded">
        no guarantee is compared here: neither snapshot selected a family revision for this family.
        The composition&apos;s own statement is: {comparison.detail}
      </p>
    );
  }
  if (comparison.kind === 'one_sided') {
    const other = comparison.side === 'before' ? 'after' : 'before';
    return (
      <div data-testid="review-center-guarantee-one-sided" data-side={comparison.side}>
        <p className={muted}>
          {comparison.side === 'after' ? 'Added guarantee' : 'Removed guarantee'} · recorded on{' '}
          {comparison.side} only · no {other} revision
        </p>
        <GuaranteeBlock guarantee={comparison.guarantee} side={comparison.side} />
      </div>
    );
  }
  if (comparison.kind === 'unchanged_revision') {
    return (
      <div data-testid="review-center-guarantee-unchanged">
        <p className={muted}>Guarantee unchanged · same recorded revision</p>
        <GuaranteeBlock guarantee={comparison.guarantee} />
      </div>
    );
  }
  if (comparison.kind === 'identical_text') {
    // MIK-R31 rule 3: the guarantee is the family's one authored text field, so identical text is
    // "wording unchanged", shown once, with the revisions as compact metadata and the IDs in details.
    const { before, after } = comparison;
    return (
      <div data-testid="review-center-guarantee-identical-text">
        <p className={muted}>
          Wording unchanged · {revisionMeta(...guaranteeRevisionLabels(before, after))}
        </p>
        <GuaranteeBlock guarantee={after} />
        <details>
          <summary>Revision records</summary>
          <p className={muted}>
            before revision {before.revision_id} · after revision {after.revision_id}
          </p>
        </details>
      </div>
    );
  }
  return (
    <div data-testid="review-center-guarantee-changed">
      <p className={muted}>
        the family guarantee changed between {comparison.before.revision_id} and{' '}
        {comparison.after.revision_id}:
      </p>
      <DiffPane
        before={comparison.before.joint_guarantee}
        after={comparison.after.joint_guarantee}
        language="text"
        mode={layout}
        collapse={false}
      />
      <p className={muted}>
        before revision {comparison.before.revision_id} · after revision{' '}
        {comparison.after.revision_id}
      </p>
    </div>
  );
}

// The compact revision labels: the display versions, or the revisions' own short identities when
// the two display versions read alike (two authored revisions must never read as one).
function guaranteeRevisionLabels(
  before: ReviewFamilyGuarantee,
  after: ReviewFamilyGuarantee,
): [string, string] {
  return before.display_version === after.display_version
    ? [before.revision_id.slice(0, 8), after.revision_id.slice(0, 8)]
    : [before.display_version, after.display_version];
}

function MemberIdentity({ member }: { member: ReviewFamilyMember }) {
  if (!member.display_label && !member.display_version) return null;
  const label = [member.display_label, member.display_version].filter(Boolean).join(' · ');
  return (
    <p className={muted}>
      {label}
      {member.lifecycle ? ` · ${member.lifecycle}` : ''}
    </p>
  );
}

function MemberFacts({ member }: { member: ReviewFamilyMember }) {
  return (
    <>
      {member.applicability ? (
        <p className={muted}>recorded applicability: {member.applicability}</p>
      ) : null}
      {member.essential_conditions.length ? (
        <p className={muted}>essential conditions: {member.essential_conditions.join('; ')}</p>
      ) : null}
      {member.exclusions.length ? (
        <p className={muted}>exclusions: {member.exclusions.join('; ')}</p>
      ) : null}
    </>
  );
}

function MemberStatement({ member }: { member: ReviewFamilyMember }) {
  if (member.state !== 'recorded') {
    return (
      <p className={muted} data-testid="review-center-member-not-on-page">
        this page did not carry the revision content of member {member.invariant_revision_id}, so no
        statement for it may be shown here: {member.detail}
      </p>
    );
  }
  return (
    <div data-testid="review-center-member-statement" data-member={member.member_id}>
      <MemberIdentity member={member} />
      <p className={prose}>{member.statement}</p>
      <MemberFacts member={member} />
    </div>
  );
}

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
    <ul className={rows} data-testid="review-center-expressions">
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
    <ul className={rows} data-testid="review-center-attribution">
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
  const noFamily = payload.family_context?.state === 'no_family_recorded';
  return (
    <section className={shell} data-testid="review-center-unselected">
      <h2 className={sectionLabel}>
        {selected ? (noFamily ? 'No recorded family' : 'Selected intent') : 'Source review'}
      </h2>
      {selected ? (
        <KnowledgeStatements
          before={payload.knowledge.before_statement}
          after={payload.knowledge.after_statement}
        />
      ) : (
        <p className={muted}>
          Source-only view. Inspect the changed files below; no intent attribution is claimed.
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
    "the comparison's own measured change set, which the source explorer below reports separately; each row " +
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
  return (
    <div className={card} data-testid="review-center-family-members">
      <h3 className={sectionLabel} data-testid="review-center-member-heading">
        {memberContextHeading(entry)}
      </h3>
      <p className={muted}>
        {distinct.length} member revisions shown{membersComplete(entry) ? '' : ' · partial roster'}
      </p>
      <details>
        <summary>Member and roster counts</summary>
        <p className={muted} data-testid="review-center-member-counts">
          {memberContextCounts(entry, distinct.length)}
        </p>
        <p className={muted} data-testid="review-center-member-distinct">
          {distinct.length} distinct member revision(s) among the loaded membership contexts
        </p>
        {FAMILY_SIDES.map((side) => (
          <Fragment key={side}>
            <RosterLine side={entry[side]} testid="review-center-roster" />
          </Fragment>
        ))}
      </details>
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
            {member.state === 'recorded' ? (
              <div className={prose}>{member.statement}</div>
            ) : (
              <div className={muted}>statement not carried on this page ({member.state})</div>
            )}
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

function FamilyCenter({
  entry,
  layout,
  listed,
  listedPartial,
  onOpenMember,
  onOpenPath,
  onRosterNext,
  expressions,
  knowledge,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  expressions: React.ReactNode;
  knowledge: React.ReactNode;
  payload: ReviewPayload;
  layout: DiffLayout;
  listed: Set<string>;
  listedPartial: boolean;
  onOpenMember: (memberRevisionId: string) => void;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  const members = [...entry.before.members, ...entry.after.members];
  const distinct = [...new Map(members.map((m) => [m.invariant_revision_id, m])).values()];
  return (
    <section className={shell} data-testid="review-center-family" data-family={entry.family_id}>
      <div>
        <h2 className={sectionLabel}>Family {entry.display_label ?? entry.family_id}</h2>
        <details>
          <summary>Family comparison details</summary>
          <p
            className={muted}
            data-testid="review-center-family-state"
            data-family-state={entry.state}
          >
            {entry.state}: {entry.detail}
          </p>
          <p className={muted} data-testid="review-center-family-selection">
            {entry.selection.state}: {entry.selection.statement}
          </p>
        </details>
      </div>
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <FamilyMemberContext
        entry={entry}
        distinct={distinct}
        onOpenMember={onOpenMember}
        onRosterNext={onRosterNext}
      />
      {expressions}
      <details>
        <summary>Realization resolution details</summary>
        <FamilyExpressionExcerpts
          entry={entry}
          membership={carriedMembership(entry)}
          listed={listed}
          listedPartial={listedPartial}
          onOpenPath={onOpenPath}
        />
      </details>
      <FamilyEvidence entry={entry} payload={payload} />
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
        guarantee: {entry ? guaranteeComparison(entry).kind : 'no family guarantee selected'}
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
      <li data-fact="assessment">
        authored judgment: {payload.evidence.assessment_state}. No member, membership or guarantee
        change creates one.
      </li>
    </ul>
  );
}

function MemberCenter({
  entry,
  member,
  layout,
  payload,
  listed,
  onOpenPath,
  onRosterNext,
  expressions,
  knowledge,
  subject,
}: {
  entry?: ReviewFamilyContextEntry;
  member?: ReviewFamilyMember;
  layout: DiffLayout;
  payload: ReviewPayload;
  listed: Set<string>;
  subject: ReviewSubject;
  expressions: React.ReactNode;
  knowledge: React.ReactNode;
  onOpenPath: (path: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <section
      className={shell}
      data-testid="review-center-member"
      data-family={entry?.family_id}
      data-subject={subject.id}
    >
      <div>
        <h2 className={sectionLabel}>{member?.display_label ?? 'Selected invariant'}</h2>
        <p className={muted} data-testid="review-center-member-family">
          {memberContextLabel(entry, payload)}
        </p>
      </div>
      {entry ? <GuaranteeComparisonBlock entry={entry} layout={layout} /> : null}
      <div className={card}>
        <h3 className={sectionLabel}>Selected intent</h3>
        <SelectedStatement
          knowledge={payload.knowledge}
          subject={subject}
          layout={layout}
          members={subjectRows(entry, subject)}
        />
      </div>
      {expressions}
      <details>
        <summary>Statement, membership and realization details</summary>
        <IndependentFacts entry={entry} payload={payload} subject={subject} />
        {member ? (
          <div className={card}>
            <h3 className={sectionLabel}>Selected roster revision context</h3>
            <MemberStatement member={member} />
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
      </details>
      <div className={card}>
        <SubjectEvidence payload={payload} subject={subject} />
      </div>
      {knowledge}
      {entry ? (
        <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
      ) : null}
    </section>
  );
}

function subjectRows(
  entry: ReviewFamilyContextEntry | undefined,
  subject: ReviewSubject,
): ReviewFamilyMember[] {
  return entry
    ? [...entry.before.members, ...entry.after.members].filter(
        (row) => row.invariant_id === subject.id,
      )
    : [];
}

function UnavailableMember({
  entry,
  member,
  layout,
  onRosterNext,
}: {
  entry: ReviewFamilyContextEntry;
  member: ReviewFamilyMember;
  layout: DiffLayout;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
}) {
  return (
    <section className={shell} data-testid="review-center-member-context">
      <h2 className={sectionLabel}>Member revision context</h2>
      <GuaranteeComparisonBlock entry={entry} layout={layout} />
      <MemberStatement member={member} />
      <p className={muted} data-testid="review-center-evidence-unavailable">
        This page did not supply the member's invariant identity. Continue the roster to open its
        own review; no assessment absence is claimed.
      </p>
      <RosterNext entry={entry} onRosterNext={onRosterNext} testid="review-center-roster-next" />
    </section>
  );
}

interface FamilyReviewCenterProps {
  payload: ReviewPayload;
  subject?: ReviewSubject;
  selection: FamilySelection | null;
  layout: DiffLayout;
  onLayout: (next: DiffLayout) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  onRosterNext: (familyId: string, side: ReviewFamilySideName, continuation: string) => void;
  openPath: string | null | undefined;
  onOpenPath: (path: string | null) => void;
  onOpenFromCenter: (path: string) => void;
  // The leaf's tree view (MIK-R25), read once per task context by the workspace; `null` for a
  // dataset review, which renders exactly as before.
  leafTrees?: ReviewTreesRead | null;
}

export function FamilyReviewCenter(props: FamilyReviewCenterProps) {
  const { payload, subject, selection, layout, onRosterNext, onOpenMember } = props;
  const { entry, member, members, listed, linksIncomplete } = centerSelection(
    payload,
    selection,
    subject,
  );
  const addressedSubject = member && !member.invariant_id ? undefined : subject;
  const invariant = addressedSubject?.kind === 'invariant';
  const expressions = (
    <CenterExpressions
      {...props}
      entry={entry}
      members={members}
      subject={addressedSubject}
      linksIncomplete={linksIncomplete}
    />
  );
  const common = {
    payload,
    layout,
    listed,
    onOpenPath: props.onOpenFromCenter,
    onRosterNext,
    expressions,
    knowledge: knowledgePanel(props.leafTrees, payload),
  };

  return (
    <div
      data-testid="review-center"
      data-selection-kind={
        invariant ? 'member' : selection === null ? 'none' : member ? 'member' : 'family'
      }
    >
      {invariant ? (
        <MemberCenter {...common} entry={entry} member={member} subject={addressedSubject} />
      ) : entry === undefined ? (
        <>
          <UnselectedCenter
            payload={payload}
            knowledge={knowledgePanel(props.leafTrees, payload, true)}
          />
          {expressions}
        </>
      ) : member ? (
        <>
          <UnavailableMember
            entry={entry}
            member={member}
            layout={layout}
            onRosterNext={onRosterNext}
          />
          {expressions}
        </>
      ) : (
        <FamilyCenter
          {...common}
          entry={entry}
          listedPartial={payload.source.inventory.partial}
          onOpenMember={(revision) => onOpenMember(entry.family_id, revision)}
        />
      )}
    </div>
  );
}

// The worklist the cards' planning marks come from, only when the leaf-wide read answered for the
// very comparison this payload was composed over (review F11); otherwise the cards carry no mark.
function pinnedWorklist(leafTrees: ReviewTreesRead | null | undefined, payload: ReviewPayload) {
  if (leafTrees?.phase !== 'trees') return undefined;
  const same = leafTrees.trees.comparison?.number === treeComparisonNumber(payload.limitations);
  return same ? leafTrees.trees.worklist : undefined;
}

function knowledgePanel(
  leafTrees: ReviewTreesRead | null | undefined,
  payload: ReviewPayload,
  open = false,
): React.ReactNode {
  if (leafTrees?.phase !== 'trees') return null;
  return (
    <LeafKnowledgeChanges
      trees={leafTrees.trees}
      reviewComparison={treeComparisonNumber(payload.limitations)}
      open={open}
    />
  );
}

// The code and test expressions of the selection: focused cards for a tree comparison (MIK-R31),
// the landed file view for a dataset review, which never asks for cards.
function CenterExpressions({
  payload,
  entry,
  members,
  subject,
  linksIncomplete,
  layout,
  onLayout,
  fullFile,
  onFullFile,
  openPath,
  onOpenPath,
  onOpenFromCenter,
  leafTrees = null,
}: FamilyReviewCenterProps & {
  entry?: ReviewFamilyContextEntry;
  members?: ReviewFamilyMember[];
  linksIncomplete: boolean;
}) {
  const cardRead = useReviewTreeEntries(
    payload.candidate.repository_id,
    payload.candidate.master,
    payload.candidate.leaf_id,
    treeComparisonNumber(payload.limitations),
    cardInvariants(entry, subject),
  );
  if (cardRead === null)
    return (
      <ReviewExpressions
        payload={payload}
        members={members}
        subject={subject}
        linksIncomplete={linksIncomplete}
        layout={layout}
        onLayout={onLayout}
        fullFile={fullFile}
        onFullFile={onFullFile}
        openPath={openPath}
        onOpenPath={onOpenPath}
      />
    );
  return (
    <ExpressionCards
      payload={payload}
      read={cardRead}
      seed={subject?.kind === 'invariant' ? subject.id : undefined}
      planning={planningMarks(pinnedWorklist(leafTrees, payload))}
      scope={cardScope(entry)}
      layout={layout}
      onLayout={onLayout}
      fullFile={fullFile}
      onFullFile={onFullFile}
      openPath={openPath}
      onOpenPath={(path) => (path === null ? onOpenPath(null) : onOpenFromCenter(path))}
    />
  );
}

// The invariants whose entries make the cards: every member of the selected family (both sides),
// or the selected invariant alone when it has no family on this page. Sorted, so one selection is
// one read.
function cardInvariants(
  entry: ReviewFamilyContextEntry | undefined,
  subject: ReviewSubject | undefined,
): string[] {
  const keys = new Set<string>();
  for (const row of entry ? [...entry.before.members, ...entry.after.members] : [])
    if (row.invariant_id) keys.add(row.invariant_id);
  if (subject?.kind === 'invariant') keys.add(subject.id);
  return [...keys].sort();
}

function centerSelection(
  payload: ReviewPayload,
  selection: FamilySelection | null,
  subject?: ReviewSubject,
) {
  const entry = payload.family_context?.entries.find(
    (candidate) => candidate.family_id === selection?.familyId,
  );
  const all = entry ? [...entry.before.members, ...entry.after.members] : [];
  const member = all.find(
    (candidate) => candidate.invariant_revision_id === selection?.memberRevisionId,
  );
  const members =
    member && !member.invariant_id
      ? undefined
      : subject?.kind === 'invariant'
        ? all.filter((row) => row.invariant_id === subject.id)
        : entry
          ? all
          : undefined;
  const listed = new Set(payload.source.inventory.entries.map((row) => row.path));
  const linksIncomplete = entry ? !membersComplete(entry) : false;
  return { entry, member, members, listed, linksIncomplete };
}

function FamilyEvidence({
  entry,
  payload,
}: {
  entry: ReviewFamilyContextEntry;
  payload: ReviewPayload;
}) {
  return (
    <div className={card} data-testid="review-family-assessment">
      <SubjectEvidence payload={payload} subject={{ kind: 'family', id: entry.family_id }} />
    </div>
  );
}

function memberContextLabel(
  entry: ReviewFamilyContextEntry | undefined,
  payload: ReviewPayload,
): string {
  if (entry) return `${entry.display_label ?? entry.family_id} · Member review`;
  return payload.family_context?.state === 'no_family_recorded'
    ? 'No recorded family'
    : 'Family context unavailable';
}
