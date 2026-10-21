// The selected subject's own reading in the reading column (MIK-R89 v2 rules 2, 9; W3, W19), the
// walked tree (MIK-R39) and the focus rule of a selection's answer.
//
// The page opens with nothing selected. A selection reads its subject (`/api/review/intent?selector…`)
// and the subject's slot -- directly under its heading in the reading column -- states that read: the
// requested subject's pending state, the owner's failure or refusal, or its answer. The answer is
// drawn content first, in the mockup's order (MIK-R89 v2 rules 1 and 6, amended 9dde2b09): one meta
// line, the statement's word diff, the recorded steps and judgment; the rest of the read follows the
// subject's code, and its provenance is Technical details'. Only that slot changes with the read; the
// page, the tree and every other section stay mounted and in place, and nothing of the previous
// subject's reading is shown under the new subject's heading (INV-8VQR13WX).
//
// The family tree is the tree the reader has walked (MIK-R39, `walkedTree.ts`): selecting a row it
// shows adds the selected subject's families and removes none; any other selection starts it afresh.
import { createContext, Fragment, useContext, useLayoutEffect, useRef } from 'react';

import { css } from '../../../styled-system/css';
import type { ReviewPayload } from '../../data/review';
import type { ReviewFamilyContextEntry, ReviewFamilySideName, ReviewRevisionSelection } from '../../data/reviewFamily';
import type { LaneRead, ReviewUnexplainedLane } from '../../data/reviewLane';
import type { ReviewTreesRead } from '../../data/reviewTrees';
import type { ReviewCatalogueRead } from '../../data/useReviewCatalogue';
import { carriedPage, FAMILY_SIDES } from '../../data/review';
import type { DiffMode } from '../changeset/DiffPane';
import { FamilyReviewCenter, type CenterPart } from './FamilyReviewCenter';
import type { FamilySelection, WalkedTree } from './FamilyTree';
import { InvariantTargetState, useInvariantTargetState } from './MarkerTargetState';
import type { ReviewNavigationState, ReviewSubject } from './ReviewNavigation';
import { ReviewMainFacts } from './ReviewRecordPanes';
import { ReviewChangeSetNavigation } from './changeSetNavigation';
import { revealTreeRow } from './treeScroll';
import { selectedRevision } from './SubjectReview';
import { heldSkeleton } from './reading/readingStyles';
import { keptFamilies, subjectTitle, type TreeIntent, type Walk, type WalkedSubject } from './walkedTree';
import type { FocusRequest, WorkspaceState } from './workspaceState';

export { useWorkspaceState, type WorkspaceState } from './workspaceState';

const muted = css({ color: 'muted', fontSize: '0.75rem', margin: '0.35rem 0' });
const slotBox = css({ marginBlock: '0.8rem 0.4rem', display: 'grid', gap: '0.6rem', minWidth: 0 });
// The selected subject's answer before its first read (D383): no read of the opening gives its size,
// so skeleton lines stand at a default height, about a statement and its judgment.
const firstReadSkeleton = css({ height: '12rem' });
const statusBox = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'grid',
  borderRadius: '3px',
  background: 'bgPanel',
  padding: '0.7rem 0.8rem',
  '& h4': { margin: '0 0 0.3rem', fontSize: '0.75rem', color: 'cyan' },
});

// The subject on screen whose review has not answered: still being read (`problem` null), or its
// read failed or was refused (`problem` is the owner's own block, labelled with this subject). It is
// bound to the read cycle's key for that question.
export interface ReadingStatus {
  key: string;
  subject: string;
  label: string;
  problem: React.ReactNode | null;
}

function ReadingStatusBlock({ reading }: { reading: ReadingStatus }) {
  if (reading.problem !== null)
    return (
      <section
        className={statusBox}
        data-testid="review-reading-problem"
        data-problem-key={reading.key}
        data-problem-subject={reading.subject}
      >
        <h4>{reading.label} could not be read</h4>
        {reading.problem}
        <p className={muted}>The page and the tree remain available; choose another subject or ask again.</p>
      </section>
    );
  return (
    <section
      className={statusBox}
      data-testid="review-reading-pending"
      data-pending-key={reading.key}
      data-pending-subject={reading.subject}
      aria-busy="true"
      role="status"
    >
      <h4>Reading {reading.label}…</h4>
    </section>
  );
}

// The selection to mark. `shown` are the families a mark may sit on: the answer's own for the reading
// area, the walked tree's for the tree (a kept family's row is a row of the tree, not of the answer).
export function selectedContext(
  payload: ReviewPayload,
  chosen: FamilySelection | null,
  selectorId?: string,
  shown: ReviewFamilyContextEntry[] = payload.family_context?.entries ?? [],
): FamilySelection | null {
  if (chosen && shown.some((entry) => entry.family_id === chosen.familyId)) return chosen;
  return initialContext(payload, payload.family_context?.entries ?? [], selectorId);
}

function initialContext(
  payload: ReviewPayload,
  entries: NonNullable<ReviewPayload['family_context']>['entries'],
  selectorId?: string,
): FamilySelection | null {
  const family = entries.find((entry) => entry.family_id === selectorId) ?? entries[0];
  if (!family) return null;
  const selected = selectedRevision(payload.knowledge, { kind: 'invariant', id: selectorId ?? '' });
  const revision = selected?.after_revision_id ?? selected?.before_revision_id;
  return { familyId: family.family_id, memberRevisionId: revision };
}

type CatalogueEntries = Pick<ReviewCatalogueRead, 'entries'>;

// What the family tree draws (MIK-R39): the walked families, which of them are kept and for which
// subject they were last read, and what to say of an answer that composed no family context.
export function walkedTreeOf(walk: Walk, payload: ReviewPayload, catalogue?: CatalogueEntries): WalkedTree {
  const context = payload.family_context;
  const composed = context?.state === 'recorded' || context?.state === 'partial';
  return {
    entries: walk.families.map((family) => family.entry),
    kept: new Map(
      keptFamilies(walk).map(({ entry, readFor, answer }) => [entry.family_id, readForLabel(readFor, answer, catalogue)]),
    ),
    notice: walk.notice,
    subjectState: composed
      ? null
      : `For the selected subject: ${emptyFamilyDescription(context?.state ?? 'absent')}`,
  };
}

function readForLabel(subject: WalkedSubject | undefined, answer: ReviewPayload, catalogue?: CatalogueEntries): string {
  if (!subject) return 'an earlier subject';
  const entry = catalogue?.entries?.find(
    (row) => row.selector_kind === subject.kind && row.selector_id === subject.id,
  );
  return `${subject.kind} ${entry?.label ?? subjectTitle(answer, subject)}`;
}

function emptyFamilyDescription(state: string): string {
  if (state === 'no_family_recorded') return 'The selected invariant has no recorded family membership.';
  if (state === 'absent') return 'No family context was supplied for this comparison. Attribution is unknown.';
  if (state === 'no_subject_selected') return 'No recorded intent is selected. Source review remains available.';
  return 'Family context could not be read for this comparison. Source review remains available.';
}

// A family's roster continuation (MIK-R39): a kept family's cursor was published for an earlier
// subject and the server continues it only for a subject whose context holds the family, so a kept
// family is selected with its continuation.
export function rosterContinuation(
  payload: ReviewPayload,
  family: string,
  continuation: string,
  actions: {
    page: (continuation: string) => void;
    selectFamily: (family: string, continuation: string) => void;
  },
): void {
  const inContext = (payload.family_context?.entries ?? []).some((entry) => entry.family_id === family);
  if (inContext) actions.page(continuation);
  else actions.selectFamily(family, continuation);
}

// A followed marker's unknown membership (MIK-R34), at the head of the subject's reading. The family
// context's own sentences are Technical details'; the meta line's chip names an abnormal read.
function MarkerTargetNote({
  payload,
  subject,
}: {
  payload: ReviewPayload;
  subject?: ReviewNavigationState['subject'];
}) {
  const markerTarget = useInvariantTargetState(subject, payload.family_context);
  return markerTarget ? <InvariantTargetState target={markerTarget} context={payload.family_context} /> : null;
}

// ---- the subject's meta line (MIK-R89 v2 rules 1 and 6, amended 9dde2b09; mockup `selected`) ----

// A subject's locations as its section draws them: the three groups' sizes and its proofs by their
// MIK-R03 currentness.
export interface LocationFacts {
  changed: number;
  unchanged: number;
  notComparable: number;
  proofs: Readonly<Record<string, number>>;
}

// Where the slot is drawn: under the subject's heading (`content`), after its code (`after-code`), or
// whole where no code follows; with its section's locations for the meta line.
interface SlotPlace {
  part: CenterPart;
  locations?: LocationFacts;
}

export const SlotPlaceContext = createContext<SlotPlace>({ part: 'whole' });

const metaLine = css({ color: 'muted', fontSize: '0.75rem', margin: '0.2rem 0', lineHeight: '1.6' });
const metaChip = css({
  borderWidth: '1px',
  borderStyle: 'solid',
  borderColor: 'amber',
  color: 'amber',
  background: 'transparent',
  borderRadius: '2px',
  paddingInline: '0.4rem',
  font: 'inherit',
  fontSize: '0.7rem',
  whiteSpace: 'nowrap',
  '&:is(button)': { cursor: 'pointer' },
});

const PROOF_ORDER = ['stale', 'unverifiable', 'not measured', 'current'];

function locationsText(locations: LocationFacts): string[] {
  const { changed, unchanged, notComparable } = locations;
  const proofs = PROOF_ORDER.map((state) => [state, locations.proofs[state] ?? 0] as const).filter(([, count]) => count > 0);
  const total = proofs.reduce((sum, [, count]) => sum + count, 0);
  const counted = total === 1 ? '1 proof' : `${total} proofs`;
  const proof =
    total === 0
      ? 'no proof'
      : proofs.length === 1 && proofs[0][0] === 'current'
        ? `${counted}, all current`
        : `${counted}: ${proofs.map(([state, count]) => `${count} ${state}`).join(', ')}`;
  return [
    `${changed + unchanged + notComparable} locations: ${changed} changed, ${unchanged} unchanged, ${notComparable} not comparable`,
    proof,
  ];
}

// A display version (`r3`) as its number.
function versionNumber(version: string | undefined): number | undefined {
  const match = /^r?(\d+)$/.exec(version ?? '');
  return match ? Number(match[1]) : undefined;
}

// The subject's two display versions as the read carried them: a family's guarantees', an
// invariant's own member rows' (each row is one revision), else its revision selection's.
function displayVersions(payload: ReviewPayload, subject: ReviewSubject): (string | undefined)[] {
  const entries = payload.family_context?.entries ?? [];
  if (subject.kind === 'family') {
    const entry = entries.find((one) => one.family_id === subject.id);
    return [entry?.before.guarantee?.display_version, entry?.after.guarantee?.display_version];
  }
  return invariantVersions(entries, selectedRevision(payload.knowledge, subject));
}

function invariantVersions(
  entries: readonly ReviewFamilyContextEntry[],
  selection: ReviewRevisionSelection | null,
): (string | undefined)[] {
  if (!selection) return [undefined, undefined];
  const rows = entries.flatMap((entry) => [...entry.before.members, ...entry.after.members]);
  const version = (revision: string | undefined, carried: string | undefined) =>
    rows.find((row) => revision !== undefined && row.invariant_revision_id === revision && row.display_version)
      ?.display_version ?? carried;
  return [
    version(selection.before_revision_id, selection.before_display_version),
    version(selection.after_revision_id, selection.after_display_version),
  ];
}

// "revision n → m" where the read carried the revisions' display versions; nothing otherwise (the ids
// are Technical details').
function revisionNumbers(payload: ReviewPayload, subject: ReviewSubject): string | null {
  const [from, to] = displayVersions(payload, subject).map(versionNumber);
  if (from !== undefined && to !== undefined) return from === to ? `revision ${to}` : `revision ${from} → ${to}`;
  const one = to ?? from;
  return one === undefined ? null : `revision ${one}`;
}

// The first roster of the subject's families that this read did not carry whole, with its cursor.
interface RosterContinuation {
  family: string;
  side: ReviewFamilySideName;
  continuation: string;
  remaining: number;
  total: number;
}

function rosterContinuationOf(entries: readonly ReviewFamilyContextEntry[]): RosterContinuation | null {
  for (const entry of entries)
    for (const side of FAMILY_SIDES) {
      const page = entry[side].page;
      if (page && !page.complete && page.continuation !== undefined)
        return { family: entry.family_id, side, continuation: page.continuation, remaining: page.counts.primary_items_remaining, total: page.counts.primary_items_total };
    }
  return null;
}

// The family part of the meta line: an invariant's families by name when they were read whole; for an
// abnormal read (not read, unavailable, partial, a roster not carried whole) one short chip, whose
// full sentence is in Technical details; nothing for no family (9dde2b09).
interface FamilyPart {
  names: string[];
  chip?: { text: string; state: string; next: RosterContinuation | null };
}

function familyPartOf(payload: ReviewPayload, subject: ReviewSubject): FamilyPart {
  const context = payload.family_context;
  if (!context || context.state === 'no_subject_selected')
    return { names: [], chip: { text: 'family context not read', state: context?.state ?? 'absent', next: null } };
  if (context.state === 'no_family_recorded') return { names: [] };
  const family = subject.kind === 'family';
  const entries = family ? context.entries.filter((entry) => entry.family_id === subject.id) : context.entries;
  const next = rosterContinuationOf(entries);
  if (!readWhole(context.state, entries, next)) return { names: [], chip: familyChip(context.state, next) };
  return { names: family ? [] : entries.map((entry) => entry.display_label ?? entry.family_id) };
}

// A family context read in full: recorded, every family recorded, every roster carried whole.
function readWhole(state: string, entries: readonly ReviewFamilyContextEntry[], next: RosterContinuation | null): boolean {
  return state === 'recorded' && next === null && entries.every((entry) => entry.state === 'recorded');
}

function familyChip(state: string, next: RosterContinuation | null): NonNullable<FamilyPart['chip']> {
  const remaining = next ? ` · ${next.remaining} of ${next.total} not shown` : '';
  return { text: `${CHIP_WORDS[state] ?? 'family context partial'}${remaining}`, state, next };
}

const CHIP_WORDS: Record<string, string> = {
  unavailable: 'family context unavailable',
  recorded: 'family roster partial',
  partial: 'family context partial',
};

// A change the reviewed task did not write: the subject's revision moved, and this leaf recorded no
// judgment and no meaning step about it (as a change a sync carried in). The chip names its source,
// the after revision's writer as its tree's history files record it, or says that none is recorded
// or could be read (MIK-R89 v2 rules 1 and 6, 9dde2b09). A revision this leaf wrote has no chip.
function carriedChipOf(payload: ReviewPayload, subject: ReviewSubject): CarriedChip | null {
  const selection = selectedRevision(payload.knowledge, subject);
  if (!selection || !movedUnjudged(payload, subject, selection)) return null;
  const writer = selection.after_writer;
  if (writer?.owners.includes(payload.candidate.leaf_id)) return null;
  return { text: carriedText(writer), detail: writer?.detail ?? 'The read names no writer of the revision.' };
}

// The revision moved, and this leaf recorded neither a judgment nor a meaning step about it.
function movedUnjudged(payload: ReviewPayload, subject: ReviewSubject, selection: ReviewRevisionSelection): boolean {
  if (selection.before_revision_id === selection.after_revision_id) return false;
  if (payload.judgment?.state !== 'none') return false;
  const steps = payload.meaning_effects?.records.find((record) => record.record === subject.id);
  return !steps || steps.state === 'none';
}

interface CarriedChip {
  text: string;
  detail: string;
}

function carriedText(writer: ReviewRevisionSelection['after_writer']): string {
  if (!writer) return 'carried in · source not read';
  if (writer.owners.length > 0) return `carried in from ${writer.owners.join(', ')}`;
  return writer.state === 'unavailable' ? 'carried in · source unavailable' : 'carried in · source not recorded';
}

// The meta line. Before the subject's read answers it holds what the column knows (the kind, the
// locations); its answer adds the family part, the revision numbers and a carried change's chip.
export function SubjectMeta({
  subject,
  locations,
  payload,
  onRosterNext,
}: {
  subject: ReviewSubject;
  locations?: LocationFacts;
  payload?: ReviewPayload;
  onRosterNext?: (family: string, side: string, continuation: string) => void;
}) {
  const family = payload ? familyPartOf(payload, subject) : { names: [] };
  const revision = payload ? revisionNumbers(payload, subject) : null;
  const carried = payload ? carriedChipOf(payload, subject) : null;
  const parts: React.ReactNode[] = [subject.kind, ...family.names];
  const { chip } = family;
  if (chip) {
    const next = chip.next;
    parts.push(
      next && onRosterNext ? (
        <button
          type="button"
          className={metaChip}
          data-testid="review-subject-family-chip"
          data-family-state={chip.state}
          title="Continue the roster walk"
          onClick={() => onRosterNext(next.family, next.side, next.continuation)}
        >
          {chip.text}
        </button>
      ) : (
        <span className={metaChip} data-testid="review-subject-family-chip" data-family-state={chip.state}>
          {chip.text}
        </span>
      ),
    );
  }
  if (revision) parts.push(revision);
  if (locations) parts.push(...locationsText(locations));
  if (carried)
    parts.push(
      <span className={metaChip} data-testid="review-subject-carried" title={carried.detail}>
        {carried.text}
      </span>,
    );
  return (
    <p className={metaLine} data-testid="review-subject-meta">
      {parts.map((part, index) => (
        <Fragment key={index}>
          {index > 0 ? ' · ' : ''}
          {part}
        </Fragment>
      ))}
    </p>
  );
}

function RosterPageNote({ payload }: { payload: ReviewPayload }) {
  const page = carriedPage(payload);
  if (page?.collection !== 'family_members') return null;
  return (
    <details data-disclosure="provenance">
      <summary>Roster page details</summary>
      <p className={muted} data-testid="review-roster-walk">
        Roster page · {page.scope.join(' · ')}
      </p>
    </details>
  );
}

export interface SelectedSlotProps {
  // The answer for the subject on screen, or null while it is pending or could not be read.
  shown: ReviewPayload | null;
  reading: ReadingStatus | null;
  subject?: ReviewNavigationState['subject'];
  chosen: FamilySelection | null;
  layout: DiffMode;
  leafTrees: ReviewTreesRead | null;
  catalogue?: ReviewNavigationState['catalogue'];
  laneRead: LaneRead<ReviewUnexplainedLane> | null;
  onOpenMember: (familyId: string, memberRevisionId: string) => void;
  onRosterNext: (family: string, side: string, continuation: string) => void;
  // An older comparison shown read-only (W17): no subject is read, and the slot says where it is.
  older: { latest: number } | null;
}

// The rest of an answered read, after the subject's code.
function SlotAfterCode({ props, center }: { props: SelectedSlotProps; center: (part: CenterPart) => React.ReactNode }) {
  if (props.older || props.reading || !props.shown) return null;
  return (
    <div className={slotBox} data-testid="review-subject-after-code">
      {center('after-code')}
      <SlotFacts payload={props.shown} laneRead={props.laneRead} />
    </div>
  );
}

function SlotFacts({ payload, laneRead }: { payload: ReviewPayload; laneRead: SelectedSlotProps['laneRead'] }) {
  return (
    <>
      <ReviewMainFacts payload={payload} laneRead={laneRead} />
      <RosterPageNote payload={payload} />
    </>
  );
}

// The slot's state before its answer: an older comparison's line, or the read's pending state or
// failure, under the meta line.
function SlotWaiting({ props, meta }: { props: SelectedSlotProps; meta: React.ReactNode }) {
  const { reading } = props;
  if (props.older)
    return (
      <div className={slotBox} data-testid="review-subject-slot" data-slot-state="older">
        {meta}
        <p className={muted} data-testid="review-older-subject">
          statement and judgment are shown on the latest comparison #{props.older.latest}
        </p>
      </div>
    );
  return (
    <div className={slotBox} data-testid="review-subject-slot" data-slot-state={reading?.problem ? 'unavailable' : 'pending'}>
      {meta}
      {reading ? <ReadingStatusBlock reading={reading} /> : null}
      {reading?.problem ? null : (
        <div className={`${heldSkeleton} ${firstReadSkeleton}`} data-testid="review-subject-slot-skeleton" aria-hidden="true" />
      )}
    </div>
  );
}

// The selected subject's slot: under its heading its meta line and its content, and after its code
// the rest of its read (SlotPlaceContext); whole where no code follows.
export function SelectedSlot(props: SelectedSlotProps) {
  const { shown } = props;
  const place = useContext(SlotPlaceContext);
  const openChanges = useContext(ReviewChangeSetNavigation);
  const center = (part: CenterPart) =>
    shown ? (
      <FamilyReviewCenter
        payload={shown}
        subject={props.subject}
        selection={props.chosen}
        layout={props.layout}
        onOpenFromCenter={(path) => openChanges?.('all', path)}
        onOpenMember={props.onOpenMember}
        onRosterNext={props.onRosterNext}
        leafTrees={props.leafTrees}
        outline={props.catalogue?.outline}
        part={part}
      />
    ) : null;
  if (place.part === 'after-code') return <SlotAfterCode props={props} center={center} />;
  const answered = !props.older && !props.reading && shown !== null;
  const meta = props.subject ? (
    <SubjectMeta
      subject={props.subject}
      locations={place.locations}
      payload={answered ? shown : undefined}
      onRosterNext={props.onRosterNext}
    />
  ) : null;
  if (!answered) return <SlotWaiting props={props} meta={meta} />;
  return (
    <div className={slotBox} data-testid="review-subject-slot" data-slot-state="answered">
      {meta}
      <MarkerTargetNote payload={shown} subject={props.subject} />
      {center(place.part)}
      {place.part === 'whole' ? <SlotFacts payload={shown} laneRead={props.laneRead} /> : null}
    </div>
  );
}

// The answer of a selection moves focus to the selected tree row, without native scrolling and
// revealed only inside the tree's own scroller (INV-QZC5AZ), if the reader has not moved focus since
// (INV-MCVSM81V). A selection that needs no new answer moves it when the selection itself is made,
// and a request left by a refresh only brings the selected row into view. The page never moves for it.
// A failed or refused read is an answer too: focus lands on the selected row once, and the request
// stays for the retry, whose answer lands it again where the retry control has gone (OR-R042).
export function useSelectionFocus(
  payload: ReviewPayload | null,
  state: WorkspaceState,
  reading: ReadingStatus | null,
  root: React.RefObject<HTMLElement | null>,
): void {
  const { focusSelection, treeIntent } = state;
  const pending = reading !== null;
  const unavailable = reading !== null && reading.problem !== null;
  const seen = useRef(payload);
  useLayoutEffect(() => {
    const request = focusSelection.current;
    const answered = seen.current !== payload;
    seen.current = payload;
    if (!request || treeIntent.id <= request.after) return;
    // Only a render made after the request can answer it: the answer a late effect of an earlier
    // render sees was on screen before the reader asked.
    if (answered) request.answered = true;
    if (unavailable) landFailureFocus(request, root.current, treeIntent);
    if (pending) return;
    if (request.scrollOnly && !request.answered) return;
    focusSelection.current = null;
    landFocus(request, root.current, treeIntent);
  }, [payload, pending, unavailable, treeIntent, focusSelection, root]);
}

function landFailureFocus(request: FocusRequest, root: HTMLElement | null, intent: TreeIntent): void {
  if (request.scrollOnly || request.failureLanded) return;
  request.failureLanded = true;
  landFocus(request, root, intent);
}

function landFocus(request: FocusRequest, root: HTMLElement | null, intent: TreeIntent): void {
  const node = selectionNode(root);
  if (request.scrollOnly) {
    revealTreeRow(node);
    return;
  }
  // A reader who moved focus while the subject was pending keeps it: only focus still on the
  // selecting control, or lost with an unmounted node (`body`), is moved to the selection.
  if (!selectionFocusAvailable(request) || !node) return;
  node.focus({ preventScroll: true });
  if (intent.keep || node.closest('[data-review-tree-panel]')) revealTreeRow(node);
}

function selectionFocusAvailable(request: FocusRequest): boolean {
  const active = document.activeElement;
  return active === null || active === document.body || active === request.from;
}

// What a selection focuses: an intent marker's unknown-membership target as its own state (MIK-R34,
// review F3), else the tree's current row.
function selectionNode(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  return (
    root.querySelector<HTMLElement>('[data-target-state-focus]') ??
    root.querySelector<HTMLElement>('[data-tree-node][aria-current="true"]')
  );
}
