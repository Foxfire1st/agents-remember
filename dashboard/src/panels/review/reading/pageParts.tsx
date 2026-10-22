// The review page's parts drawn from its one model (MIK-R89 v2 rules 2–7 and 10): the tree, the
// reading column's shared context with the selected subject's slot, the page-level notes and the top
// line's chip and comparison selector. ReviewSurface.tsx composes them in layout B.
import { useContext, useMemo, useRef } from 'react';

import { css } from '../../../../styled-system/css';

import type { ReviewEntry, ReviewPayload } from '../../../data/review';
import type { IntentSummaryRead } from '../../../data/reviewIntentSummary';
import type { ReviewTreesRead } from '../../../data/reviewTrees';
import { useDashboard } from '../../../data/store';
import type { DiffMode } from '../../changeset/DiffPane';
import { dirName } from '../../detail-panel/model';
import { blockKey, type ReviewBlockIdentity, type ReviewSubjectRef } from '../blockIdentity';
import { ReviewChangeSetNavigation } from '../changeSetNavigation';
import { codeChangesCount, matchingTreeRead } from '../codeFacts';
import { MeaningFieldGuard } from '../MeaningEffects';
import type { FamilySelection } from '../FamilyTree';
import { treeLane } from '../ReviewDestinations';
import { outlineUnavailableReason } from '../ReviewNavigation';
import { problemOf } from '../ReviewOutcome';
import type { ReviewSelection } from '../reviewSelection';
import { rosterContinuation, SelectedSlot, selectedContext } from '../ReviewWorkspace';
import { ReviewTree } from '../tree/ReviewTree';
import { ComparisonSelector } from '../top-line/ComparisonSelector';
import { StatusChip } from '../top-line/StatusChip';
import { chipFacts, type ChipInput } from '../top-line/statusChipModel';
import { DeliveredLine } from '../ReviewScopeHeader';
import { planningMarks } from '../worklistGroups';
import { worklistAnswer } from '../worklistState';
import { changeSetPick } from '../../../tray/pickBuilders';
import { useCodeContext } from './codeContextPreference';
import type { ReadingContextValue } from './readingContext';
import type { Crumb } from './ReadingColumn';
import { muted } from './readingStyles';
import type { PageReads, ReviewPageState } from './useReviewPage';
import { readSubjectOf } from './useReviewSelection';

export interface PageProps {
  reads: PageReads;
  page: ReviewPageState;
}

const sameSubject = (left: ReviewSubjectRef, right: ReviewSubjectRef | undefined) =>
  right !== undefined && left.kind === right.kind && left.id === right.id;

// The task's title from the dashboard's task projection (rule 6); none when the list does not hold it.
export function useTaskTitle(repo: string, master: string, leaf: string): string | undefined {
  return useDashboard(
    (state) =>
      Object.values(state.tasks).find(
        (task) => task.id === leaf && task.reference?.repository === repo && dirName(task.reference.path) === master,
      )?.title,
  );
}

function familyMember(page: ReviewPageState, familyId: string, memberRevisionId: string) {
  const family = page.walked?.entries.find((entry) => entry.family_id === familyId);
  return [...(family?.before.members ?? []), ...(family?.after.members ?? [])].find(
    (row) => row.invariant_revision_id === memberRevisionId,
  );
}

// A member row of a family's roster opens that member's invariant at its row of the family, keeping
// the tree the reader walked (MIK-R39).
function openMember({ reads, page }: PageProps, familyId: string, memberRevisionId: string): void {
  const context: FamilySelection = { familyId, memberRevisionId };
  const member = familyMember(page, familyId, memberRevisionId);
  if (!member?.invariant_id) {
    reads.workspace.setChosen(context);
    return;
  }
  page.select(
    { kind: 'subject', subject: { kind: 'invariant', id: member.invariant_id }, family: familyId },
    { keepTree: true, context },
  );
}

function rosterNext({ reads, page }: PageProps, shown: ReviewPayload, family: string, continuation: string): void {
  rosterContinuation(shown, family, continuation, {
    page: (next) => reads.setPage({ of: 'family_members', continuation: next }),
    selectFamily: (id, next) =>
      page.select(
        { kind: 'subject', subject: { kind: 'family', id } },
        { keepTree: true, page: { of: 'family_members', continuation: next }, context: { familyId: id } },
      ),
  });
}

function slotChosen(reads: PageReads): FamilySelection | null {
  const { shown, reading, workspace, navigation } = reads;
  if (reading || !shown) return workspace.chosen;
  return selectedContext(shown, workspace.chosen, navigation.subject?.id);
}

// The selected subject's slot, under its heading in the column.
function PageSlot({ props, layout }: { props: PageProps; layout: DiffMode }) {
  const { reads, page } = props;
  const { changeSet } = page;
  const older = changeSet.older && changeSet.resolved !== undefined ? { latest: changeSet.resolved } : null;
  return (
    <SelectedSlot
      shown={reads.shown}
      reading={reads.reading}
      subject={reads.navigation.subject}
      chosen={slotChosen(reads)}
      layout={layout}
      leafTrees={changeSet.leafTrees}
      catalogue={reads.navigation.catalogue}
      laneRead={changeSet.lane}
      onOpenMember={(familyId, revision) => openMember(props, familyId, revision)}
      onRosterNext={(family, _side, continuation) => {
        if (reads.shown) rosterNext(props, reads.shown, family, continuation);
      }}
      older={older}
    />
  );
}

// The repository id the tray's references name: the trees answer's own (WP7 request 3), else the
// target's name until that answer is in.
function repositoryOf(repo: string, trees: ReviewTreesRead | null, number: number | undefined): string {
  return matchingTreeRead(trees, number)?.repository_id ?? repo;
}

function selectedBlockKey(selection: ReviewSelection | null): string | null {
  return selection?.kind === 'block' ? blockKey(selection.block) : null;
}

export function useReadingValue(props: PageProps, layout: DiffMode): ReadingContextValue {
  const { reads, page } = props;
  const { workspace } = reads;
  const { changeSet, selection, select } = page;
  const context = useCodeContext();
  const worklist = matchingTreeRead(changeSet.leafTrees, changeSet.number)?.worklist;
  const planning = useMemo(() => planningMarks(worklist), [worklist]);
  const familyMembers = useMemo(() => familyMembersOf(changeSet.model), [changeSet.model]);
  // The slot sits under the subject the selection reads (a block's invariant); under an older
  // comparison it says where that subject's statement is read instead.
  const slotSubject = readSubjectOf(selection, changeSet.cards, false);
  return {
    place: { repo: reads.repo, master: reads.master, leaf: reads.leaf, repository: repositoryOf(reads.repo, changeSet.leafTrees, changeSet.number) },
    comparison: changeSet.cards.state === 'ready' ? changeSet.cards.comparison : changeSet.comparison,
    payload: reads.frame,
    planning,
    layout,
    collapse: context === 'changes',
    sort: changeSet.model.sort,
    selection,
    selectedBlock: selectedBlockKey(selection),
    openGroups: workspace.openGroups,
    toggleGroup: workspace.toggleGroup,
    select: (next) => select(next, { keepTree: true }),
    slot: (subject) => (sameSubject(subject, slotSubject) ? <PageSlot props={props} layout={layout} /> : null),
    familyMembers,
  };
}

// Each family's member selector ids in the model's order.
function familyMembersOf(model: ReviewPageState['changeSet']['model']): Map<string, string[]> {
  const members = new Map<string, string[]>();
  for (const group of model.groups)
    if (group.family) members.set(group.family.row.selectorId, group.subjects.map((node) => node.row.selectorId));
  return members;
}

// Whether the model draws a section (an invariant's first place, or a family's heading) for the
// subject, which then holds its slot.
function hasSection(model: ReviewPageState['changeSet']['model'], subject: ReviewSubjectRef): boolean {
  if (subject.kind === 'family') return model.groups.some((group) => group.family?.row.selectorId === subject.id);
  return model.subjects.some((node) => node.row.selectorId === subject.id);
}

const orphanBox = css({ marginBlock: '0.6rem', paddingBottom: '0.4rem', borderBottomWidth: '1px', borderBottomStyle: 'solid', borderBottomColor: 'grid' });

// A selected subject the page draws no section for (a stale link, a catalogue that failed or lists
// nothing): its answer or its failure, with its staleness sentences, stands at the head of the
// column under its own heading, so a read is never shown nowhere.
export function OrphanSlot({ props, layout }: { props: PageProps; layout: DiffMode }) {
  const { reads, page } = props;
  const subject = readSubjectOf(page.selection, page.changeSet.cards, false);
  if (!subject || hasSection(page.changeSet.model, subject)) return null;
  const entry = reads.navigation.catalogue.entries?.find(
    (row) => row.selector_kind === subject.kind && row.selector_id === subject.id,
  );
  return (
    <section className={orphanBox} data-testid="review-selected-subject" data-subject={`${subject.kind}:${subject.id}`}>
      <h3 className={css({ margin: '0.2rem 0', fontSize: '0.85rem' })}>
        {subject.kind} {entry?.label ?? subject.id}
      </h3>
      <p className={muted}>This subject has no section in the change set on this page; its own reading follows.</p>
      <PageSlot props={props} layout={layout} />
    </section>
  );
}

function changedTotal(summary: IntentSummaryRead): number | '…' | 'unavailable' {
  if (summary.phase === 'loading') return '…';
  return summary.attribution?.changed_total ?? 'unavailable';
}

function worklistCount(read: ReviewTreesRead | null, number: number | undefined): string {
  if (read === null || read.phase === 'loading') return '…';
  return worklistAnswer(matchingTreeRead(read, number)?.worklist) ?? 'not available';
}

export function PageTree({ props, traversal }: { props: PageProps; traversal: string }) {
  const { reads, page } = props;
  const { workspace, navigation } = reads;
  // While a generation's catalogue is read, the tree says so, and no earlier titles stand in it.
  const loading = navigation.catalogue.loading ? 'Reading families and invariants for this comparison…' : undefined;
  const status = [traversal, page.walk?.notice, loading ?? outlineUnavailableReason(navigation.catalogue)];
  const { changeSet } = page;
  const open = useContext(ReviewChangeSetNavigation);
  const total = changedTotal(changeSet.summary);
  return (
    <ReviewTree
      model={changeSet.model}
      selection={page.selection}
      onSelect={(next) => page.select(next, { keepTree: true })}
      onOpenGroup={page.openGroup}
      query={workspace.query}
      onQuery={workspace.setQuery}
      changedFiles={total}
      allChanges={total}
      onOpenAllChanges={() => open?.('all')}
      lane={treeLane(changeSet.lane, page.lanes.lines)}
      onOpenLane={(destination) => open?.(destination)}
      worklist={{ count: worklistCount(changeSet.leafTrees, changeSet.number), open: workspace.worklist }}
      onOpenWorklist={() => workspace.setWorklist(!workspace.worklist)}
      status={status}
      codeFacts={{ count: codeChangesCount(navigation.catalogue.outline, changeSet.leafTrees, changeSet.number), comparison: changeSet.number }}
    />
  );
}

// The page-level notes in their reserved place at the top of the column: the task context's own
// outcome, its required-field failure while nothing is selected (MIK-R43 rule 9: the no-subject
// view's answer without meaning_effects is invalid; a selected subject's slot carries its own
// guard), a link's selection outside the comparison and an older comparison's read-only line. A
// missing outline's reason is the tree's status line (WP5).
export function PageNotes({
  page,
  frame,
  outcome,
}: {
  page: ReviewPageState;
  frame: ReviewPayload | null;
  outcome: React.ReactNode;
}) {
  const { changeSet } = page;
  return (
    <>
      {outcome}
      {frame && page.selection === null ? <MeaningFieldGuard payload={frame} /> : null}
      {page.outsideLine ? (
        <p className={muted} role="status" data-testid="review-selection-outside">
          {page.outsideLine}
        </p>
      ) : null}
      {changeSet.older && changeSet.resolved !== undefined ? (
        <p className={muted} data-testid="review-older-comparison">
          Comparison #{changeSet.number} is shown read-only; statements and judgments are read on comparison #
          {changeSet.resolved}.{' '}
          <button type="button" data-testid="review-latest-comparison" onClick={() => page.chooseComparison(changeSet.resolved ?? 0)}>
            Show #{changeSet.resolved}
          </button>
        </p>
      ) : null}
      <DeliveredLine delivered={changeSet.delivered} onOpen={page.chooseDelivered} />
    </>
  );
}

// The top line's chip and comparison selector (rules 6 and 10).
// The chip's read is the task context's only (Failure and Recovery): while a subject is read, its
// pending state and its failure are its slot's, and the chip keeps the task context's last state.
function taskContextRead(reads: PageReads): Pick<ChipInput, 'phase' | 'problem'> {
  const subjectRead = reads.instead === null && reads.navigation.subject !== undefined;
  if (!subjectRead) return { phase: reads.read.phase, problem: problemOf(reads.read) };
  return { phase: reads.frame === null && reads.read.phase === 'loading' ? 'loading' : 'reviewed', problem: null };
}

export function TopLineControls({ props }: { props: PageProps }) {
  const { reads, page } = props;
  const { changeSet } = page;
  // The reader's current date: a recorded time of this year is written without its year.
  const now = new Date();
  const facts = chipFacts({
    ...taskContextRead(reads),
    payload: reads.frame,
    comparison: changeSet.comparison,
    latest: changeSet.resolved ?? null,
    now,
  });
  const repository = repositoryOf(reads.repo, changeSet.leafTrees, changeSet.number);
  // The status popover hangs from the comparison's button, as the mockup's does (D401).
  const comparisonButton = useRef<HTMLButtonElement>(null);
  return (
    <>
      <StatusChip facts={facts} comparison={changeSet.number ?? null} anchorRef={comparisonButton} />
      <ComparisonSelector
        leaf={reads.leaf}
        shown={changeSet.number ?? null}
        resolved={changeSet.resolved ?? null}
        delivered={changeSet.delivered}
        recordedAt={changeSet.comparison?.recorded_at ?? null}
        now={now}
        latest={changeSet.latest ?? null}
        onChoose={page.chooseComparison}
        onChooseDelivered={page.chooseDelivered}
        pick={changeSet.comparison ? changeSetPick({ repository, comparison: changeSet.comparison }) : null}
        buttonRef={comparisonButton}
      />
    </>
  );
}

// A subject's text id (INV-…, or a family's id): the page row's, else the catalogue's label. The
// selector id is a storage key; it is named only for a subject no read has labelled (finding 2).
function subjectTextId(
  model: ReviewPageState['changeSet']['model'],
  entries: readonly ReviewEntry[] | undefined,
  subject: ReviewSubjectRef,
): string {
  const row =
    subject.kind === 'family'
      ? model.groups.find((group) => group.family?.row.selectorId === subject.id)?.family?.row
      : model.subjects.find((node) => node.row.selectorId === subject.id)?.row;
  if (row && row.id !== subject.id) return row.id;
  const entry = entries?.find((one) => one.selector_kind === subject.kind && one.selector_id === subject.id);
  return entry?.label ?? subject.id;
}

const LANE = 'Code without invariant';
const fileName = (path: string) => path.slice(path.lastIndexOf('/') + 1);
const rangeLabel = ({ start, end }: { start: number; end: number }) => (start === end ? `L${start}` : `L${start}–${end}`);

// A block's crumb: the subject whose section holds its card (the location that draws it, when it
// shares its card with others), and its file's name with its after range; a lane hunk's, the lane and
// its file's name (mockup app.js `updateCrumb`: a range for a block's after side only).
function blockCrumb(base: string, model: ReviewPageState['changeSet']['model'], block: ReviewBlockIdentity): Crumb {
  if (block.kind === 'hunk') return { base, subject: LANE, detail: fileName(block.path) };
  const key = blockKey(block);
  const holder = model.blocks.find((node) => node.draws.includes(key));
  const subject = holder ? model.subjects.find((node) => node.changed.includes(holder))?.row.id : undefined;
  const range = block.side === 'after' && block.range ? ` ${rangeLabel(block.range)}` : '';
  return { base, subject, detail: `${fileName(block.path)}${range}` };
}

// The reading head's crumb (ruling 9498d6e4; mockup app.js `updateCrumb`): the change set and its
// comparison, then what is selected, by the selection (MIK-R89 v2 rule 9), not by the scroll.
export function crumbOf(page: ReviewPageState, entries?: readonly ReviewEntry[]): Crumb {
  const base = `Change set · comparison #${page.changeSet.number ?? '…'}`;
  const { selection } = page;
  if (!selection) return { base };
  if (selection.kind === 'lane') return { base, subject: LANE, detail: selection.path ? fileName(selection.path) : undefined };
  if (selection.kind === 'subject') return { base, subject: subjectTextId(page.changeSet.model, entries, selection.subject) };
  return blockCrumb(base, page.changeSet.model, selection.block);
}
