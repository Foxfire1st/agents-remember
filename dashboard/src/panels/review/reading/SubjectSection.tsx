// One subject of the reading column (MIK-R89 v2 rules 2, 5; W2, W3; mockup `open`, `selected`): its
// heading line (id, short title, recorded effect, change badge, ⊕ on the invariant by its text id and
// the comparison), its one meta line, its changed blocks in the in-subject order, and its two closed
// groups. The statement word diff, the recorded judgment and the subject's facts come with its own
// read, so they show in its slot once it is selected and that read has answered (W3, W19): the content
// between the meta line and the code, the rest after the code (rules 1 and 6, amended 9dde2b09). A
// family is a compact heading and member summary list, with its own read's slot after the members
// (its state, guarantee change, steps and judgment). It holds no source blocks; selecting a member
// opens that invariant's own content (MIK-R211 rule 10).
import { css } from '../../../../styled-system/css';
import { AddToTray, addToTrayHost } from '../../../tray/AddToTray';
import { useStaleInvariants } from '../../../data/staleInvariants';
import { blockKey } from '../blockIdentity';
import { EFFECT_RANK, type FamilyHeading, type SubjectGroup, type SubjectNode } from '../changeSetOrder';
import { ReviewBlockCard } from '../ExpressionCards';
import { SORT_LABELS } from '../reviewSelection';
import { StaleMark } from '../tree/TreeRows';
import { SlotPlaceContext, SubjectMeta, type LocationFacts } from '../ReviewWorkspace';
import { groupDomId, subjectDomId } from './blockDom';
import { leadClause } from './leadClause';
import { LocationGroup } from './LocationGroup';
import { useReading, type ReviewPlace } from './readingContext';
import { badge, muted, sectionHead, subjectBox, subjectId, sublabel } from './readingStyles';
import { invariantPick } from '../../../tray/pickBuilders';

const titleButton = css({
  background: 'transparent',
  border: 'none',
  padding: 0,
  font: 'inherit',
  color: 'inherit',
  cursor: 'pointer',
  textAlign: 'left',
  _hover: { color: 'amber' },
  _focusVisible: { outlineWidth: '1px', outlineStyle: 'solid', outlineColor: 'amber', outlineOffset: '2px' },
});

// The heading's short title: the statement's lead clause, bold and unboxed as the mockup draws it
// (MIK-R89 v2 rule 1, 9dde2b09 and d9a1ec8f); one line, cut with an ellipsis if still too long. The
// whole title is its tooltip and the statement below holds the full text. `data-subject-title` keeps
// the button out of the page's shared control look (ReviewLayout).
const shortTitle = css({
  minWidth: 0,
  maxWidth: '100%',
  '& button': {
    display: 'block',
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontSize: '1rem',
    fontWeight: 700,
  },
});

const familyHead = css({ paddingBlock: '1.2rem 0.2rem', scrollMarginTop: '3rem' });

// A row's title without its leading id: the tray chip writes "<id> · <title>" (MIK-R188 rule 2).
function ownTitle(row: { id: string; title: string }): string {
  return row.title.startsWith(row.id) ? row.title.slice(row.id.length).trim() || row.title : row.title;
}

function sortPhrase(sort: ReturnType<typeof useReading>['sort']): string {
  return sort === 'priority' ? 'enforcement before presentation, largest first' : `sorted by ${SORT_LABELS[sort]}`;
}

function EffectBadges({ effects }: { effects: string[] }) {
  return (
    <>
      {effects.map((effect, index) => (
        // The recorded steps in attempt order, so one effect can repeat (MIK-R43).
        <span key={`${index}:${effect}`} className={badge} data-hot={EFFECT_RANK[effect] === 0 ? 'true' : undefined} data-testid="review-subject-effect">
          {effect}
        </span>
      ))}
    </>
  );
}

function CardsState({ node }: { node: SubjectNode }) {
  if (node.cards === 'unread')
    return <p className={muted} data-testid="review-subject-cards-unread">Code locations have not been loaded.</p>;
  if (node.cards === 'reading')
    return (
      <p className={muted} role="status" data-testid="review-subject-cards-reading">
        Reading this subject&apos;s code and test locations…
      </p>
    );
  if (node.cards === 'unavailable')
    return (
      <p className={muted} data-testid="review-subject-cards-unavailable">
        This subject&apos;s code locations could not be read; see the summary above.
      </p>
    );
  return null;
}

function Blocks({ node }: { node: SubjectNode }) {
  const reading = useReading();
  const { comparison } = reading;
  if (!comparison || node.cards !== 'ready') return <CardsState node={node} />;
  return (
    <>
      <div className={sublabel}>
        Changed locations <span>{node.changed.length} · {sortPhrase(reading.sort)}</span>
      </div>
      {node.changed.length === 0 ? (
        <p className={muted} data-testid="review-subject-no-changed">
          No changed location is recorded for this subject.
        </p>
      ) : null}
      {node.changed.map((location) => (
        <ReviewBlockCard
          key={location.key}
          node={location}
          place={reading.place}
          comparison={comparison}
          planning={reading.planning}
          layout={reading.layout}
          collapse={reading.collapse}
          selected={reading.selectedBlock === location.key}
          onSelect={() => reading.select({ kind: 'block', block: location.identity })}
        />
      ))}
      {(['unchanged', 'not-comparable'] as const).map((kind) => {
        const id = groupDomId(node.key, kind);
        return (
          <LocationGroup
            key={kind}
            subjectKey={node.key}
            kind={kind}
            nodes={kind === 'unchanged' ? node.unchanged : node.notComparable}
            open={reading.openGroups.has(id)}
            onToggle={() => reading.toggleGroup(id)}
          />
        );
      })}
    </>
  );
}

// The meta line's locations, once the cards are read: the three groups' sizes and the proofs among
// them by currentness.
function locationFacts(node: SubjectNode): LocationFacts | undefined {
  if (node.cards !== 'ready') return undefined;
  const proofs: Record<string, number> = {};
  for (const location of [...node.changed, ...node.unchanged, ...node.notComparable])
    if (location.role === 'proof') proofs[location.currentness] = (proofs[location.currentness] ?? 0) + 1;
  return { changed: node.changed.length, unchanged: node.unchanged.length, notComparable: node.notComparable.length, proofs };
}

// Under the heading: the meta line, or the selected subject's slot, which draws its own (9dde2b09).
function SubjectHead({
  subject,
  slot,
  locations,
}: {
  subject: { kind: 'invariant'; id: string };
  slot: React.ReactNode | null;
  locations?: LocationFacts;
}) {
  if (slot === null) return locations ? <SubjectMeta subject={subject} locations={locations} /> : null;
  return <SlotPlaceContext.Provider value={{ part: 'content', locations }}>{slot}</SlotPlaceContext.Provider>;
}

function selectedSubject(reading: ReturnType<typeof useReading>, kind: 'invariant' | 'family', id: string): boolean {
  const { selection } = reading;
  return selection?.kind === 'subject' && selection.subject.kind === kind && selection.subject.id === id;
}

// D576: 'true' while the latest move changed this invariant and its detail is not read again.
function useStaleAttribute(place: ReviewPlace, id: string): 'true' | undefined {
  return useStaleInvariants(place.repo, place.master, place.leaf).has(id) ? 'true' : undefined;
}

// `ruled`: set on a later section of the column, with the rule above it (readingStyles.ts `subjectBox`).
export function SubjectSection({ node, family, ruled }: { node: SubjectNode; family?: string; ruled?: '' }) {
  const reading = useReading();
  const { row } = node;
  const subject = { kind: 'invariant' as const, id: row.selectorId };
  const stale = useStaleAttribute(reading.place, row.selectorId);
  const selectedKey = reading.selection?.kind === 'block' ? blockKey(reading.selection.block) : null;
  const selected =
    selectedSubject(reading, 'invariant', row.selectorId) ||
    (selectedKey !== null && node.changed.some((location) => location.key === selectedKey));
  if (!node.first)
    return (
      <p className={muted} data-testid="review-subject-elsewhere" data-subject={row.selectorId}>
        {row.id} · {row.title} — its locations are shown at its first place above.
      </p>
    );
  const slot = reading.slot(subject);
  return (
    <article
      className={`${subjectBox} ${addToTrayHost}`}
      id={subjectDomId(node.key)}
      data-testid="review-subject-section"
      data-subject={row.selectorId}
      data-family={family}
      data-selected={selected ? 'true' : undefined}
      data-stale={stale}
      data-ruled={ruled}
    >
      <div className={sectionHead}>
        <StaleMark stale={stale === 'true'} />
        <span className={subjectId}>{row.id}</span>
        <h3 className={shortTitle}>
          <button
            type="button"
            className={titleButton}
            data-testid="review-subject-select"
            data-subject-title=""
            title={row.title}
            onClick={() => reading.select({ kind: 'subject', subject, ...(family ? { family } : {}) })}
          >
            {leadClause(ownTitle(row))}
          </button>
        </h3>
        <EffectBadges effects={node.effects} />
        {row.badge ? (
          <span className={badge} data-testid="review-subject-change" data-change={row.badge}>
            {row.badge}
          </span>
        ) : null}
        {reading.comparison ? (
          <AddToTray
            pick={invariantPick({
              repository: reading.place.repository,
              kind: 'invariant',
              id: row.id,
              title: ownTitle(row),
              // The memory side that holds the record; the server reads its revision there (MIK-R188
              // rule 2): the row's walked revision does not say which side it is of.
              memoryTree: (row.badge === 'removed' ? reading.comparison.memory_base : reading.comparison.memory_candidate).tree,
              comparison: reading.comparison,
            })}
            label={`invariant ${row.id}`}
          />
        ) : null}
      </div>
      <SubjectHead subject={subject} slot={slot} locations={locationFacts(node)} />
      <Blocks node={node} />
      <AfterCode slot={slot} />
    </article>
  );
}

function FamilyHeadingLine({ heading }: { heading: FamilyHeading }) {
  const reading = useReading();
  const { row } = heading;
  const subject = { kind: 'family' as const, id: row.selectorId };
  return (
    <div
      className={`${familyHead} ${addToTrayHost}`}
      id={subjectDomId(`family:${row.selectorId}`)}
      data-testid="review-family-heading"
      data-family={row.selectorId}
      data-selected={selectedSubject(reading, 'family', row.selectorId) ? 'true' : undefined}
    >
      <div className={sectionHead}>
        <span className={subjectId}>{row.id}</span>
        <h3>
          <button
            type="button"
            className={titleButton}
            data-testid="review-family-select"
            data-subject-title=""
            onClick={() => reading.select({ kind: 'subject', subject })}
          >
            Family {row.title}
          </button>
        </h3>
        {heading.reason ? <span className={badge}>{heading.reason}</span> : null}
        {heading.kept ? <span className={muted}>kept · read for {heading.kept}</span> : null}
        {reading.comparison ? (
          <AddToTray
            pick={invariantPick({
              repository: reading.place.repository,
              kind: 'family',
              id: row.id,
              title: ownTitle(row),
              memoryTree: (row.badge === 'removed' ? reading.comparison.memory_base : reading.comparison.memory_candidate).tree,
              comparison: reading.comparison,
            })}
            label={`family ${row.id}`}
          />
        ) : null}
      </div>
    </div>
  );
}

const AFTER_CODE = { part: 'after-code' } as const;

// A selected invariant's reading beyond its content, after its code.
function AfterCode({ slot }: { slot: React.ReactNode | null }) {
  return slot === null ? null : <SlotPlaceContext.Provider value={AFTER_CODE}>{slot}</SlotPlaceContext.Provider>;
}

const overviewList = css({ listStyle: 'none', padding: 0, margin: 0 });
const overviewRow = css({
  display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: '0.5rem',
  paddingBlock: '0.5rem', borderBottomWidth: '1px', borderBottomStyle: 'solid', borderBottomColor: 'grid',
  '& button': { flex: '1 1 18rem', minWidth: 0 },
});

// Summary rows are served outline facts, never code-location counts inferred from an unread card.
function locationSources(subjects: readonly SubjectNode[]): Map<string, SubjectNode> {
  return new Map(subjects.filter((node) => node.first).map((node) => [node.row.selectorId, node]));
}

function MemberLocationCounts({ node }: { node: SubjectNode | undefined }) {
  if (!node?.counts) return null;
  return (
    <span className={muted} data-testid="review-member-location-counts">
      {node.counts.changed} changed · {node.counts.unchanged} unchanged · {node.counts.not_comparable} not comparable
    </span>
  );
}

function FamilyCounts({ group, sources }: { group: SubjectGroup; sources: ReadonlyMap<string, SubjectNode> }) {
  const ids = [...new Set(group.subjects.map((node) => node.row.selectorId))];
  const known = ids.every((id) => sources.get(id)?.counts !== undefined);
  const changed = known ? ids.reduce((total, id) => total + sources.get(id)!.counts!.changed, 0) : undefined;
  return (
    <p className={muted} data-testid="review-family-counts">
      {ids.length} {ids.length === 1 ? 'invariant' : 'invariants'}
      {changed === undefined ? null : ` · ${changed} changed ${changed === 1 ? 'location' : 'locations'}`}
    </p>
  );
}

function MemberSummaries({ group, sources }: { group: SubjectGroup; sources: ReadonlyMap<string, SubjectNode> }) {
  const reading = useReading();
  const family = group.family?.row.selectorId;
  return (
    <ul className={overviewList} data-testid="review-member-summaries">
      {group.subjects.map((node) => (
        <li key={node.key} className={overviewRow} data-testid="review-member-summary" data-subject={node.row.selectorId}>
          <button
            type="button"
            className={titleButton}
            data-subject-title=""
            onClick={() => reading.select({
              kind: 'subject', subject: { kind: 'invariant', id: node.row.selectorId },
              ...(family ? { family } : {}),
            })}
          >
            <span className={subjectId}>{node.row.id}</span>{' · '}{ownTitle(node.row)}
          </button>
          {node.row.badge ? <span className={badge} data-change={node.row.badge}>{node.row.badge}</span> : null}
          <EffectBadges effects={node.effects} />
          <MemberLocationCounts node={sources.get(node.row.selectorId)} />
        </li>
      ))}
    </ul>
  );
}

// A family shows its guarantee and the member changes; selecting a member opens its own code.
const FAMILY_STATE = { part: 'state' } as const;

export function FamilyOverview({ group, subjects }: { group: SubjectGroup; subjects: readonly SubjectNode[] }) {
  const reading = useReading();
  const sources = locationSources(subjects);
  const id = group.family?.row.selectorId;
  const slot = id ? reading.slot({ kind: 'family', id }) : null;
  return (
    <section data-testid="review-family-overview" data-family={id}>
      {group.family ? <FamilyHeadingLine heading={group.family} /> : null}
      <FamilyCounts group={group} sources={sources} />
      <MemberSummaries group={group} sources={sources} />
      {/* The family read's own slot after its members, as an invariant's facts follow its code. */}
      {slot === null ? null : <SlotPlaceContext.Provider value={FAMILY_STATE}>{slot}</SlotPlaceContext.Provider>}
    </section>
  );
}

export function ComparisonOverview({ groups }: { groups: readonly SubjectGroup[] }) {
  const sources = locationSources(groups.flatMap((group) => group.subjects));
  return (
    <div data-testid="review-comparison-overview">
      {groups.some((group) => group.family || group.subjects.length > 0) ? (
        <p className={muted}>Select a family for its overview or an invariant for its code and changes.</p>
      ) : null}
      {groups.map((group) => (
        <section key={group.key}>
          {group.family ? <>
            <FamilyHeadingLine heading={group.family} />
            <FamilyCounts group={group} sources={sources} />
          </> : null}
          <MemberSummaries group={group} sources={sources} />
        </section>
      ))}
    </div>
  );
}
