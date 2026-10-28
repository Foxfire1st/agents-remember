// The review page's tree (MIK-R86 v2 rule 4; MIK-R89 v2 rules 3, 5, 7, 9 and 11), top to bottom:
//   - the heading "Change set" with the leaf's counts and the one "All changes" link (W14);
//   - one control line: the filter, Changed | All, and the one Sort control;
//   - the one scroller: each shown family heading (and, after shown families, "Without a family"),
//     its subjects, each subject's changed locations and its two group rows; then, pinned to the
//     tree's bottom, the Worklist row and "Code without invariant", with the lane's file rows after
//     them.
//
// The tree renders the change-set model it is given (changeSetOrder.ts), so it and the reading column
// show one order. It selects nothing on its own: `selection` is the page's, `onSelect` asks for a
// change. A click keeps the tree's scroll position; a selection made elsewhere (j/k, the column, the
// URL) is revealed by `scrollTop` alone (treeScroll.ts). The tree's height comes from CSS: it fills
// the height its parent gives it, and its one scroller is `overscroll-behavior: contain`.
import { type RefObject, useLayoutEffect, useRef } from 'react';

import { cx } from '../../../../styled-system/css';
import type { LaneDestinationName } from '../../../data/reviewLane';
import { CodeFactsScrollAnchor, useCodeFactsNotice } from '../codeFacts';
import {
  filterTree,
  type ChangeSetModel,
  type FilteredGroup,
  type FilteredSubject,
  type SubjectNode,
} from '../changeSetOrder';
import { TreePinned, type TreeLane } from '../ReviewDestinations';
import type { ReviewSelection } from '../reviewSelection';
import { blockKey } from '../blockIdentity';
import { treeViewStore, type TreeView } from '../triageViewPreference';
import { revealTreeRow, TREE_SCROLL_SELECTOR } from '../treeScroll';
import { SortMenu, sortFallback } from './SortMenu';
import {
  FamilyRow,
  GroupRows,
  LocationRow,
  SubjectRow,
  isCurrentFamily,
  isCurrentLocation,
  isCurrentSubject,
  type TreeGroupName,
} from './TreeRows';
import * as styles from './treeStyles';

export interface ReviewTreeProps {
  // The change-set model of the shown comparison, in the stored sort and view.
  model: ChangeSetModel;
  // The page's selection; null opens unselected (MIK-R89 v2 rule 2).
  selection: ReviewSelection | null;
  onSelect: (selection: ReviewSelection) => void;
  // A group row: open that subject's group in the reading column and scroll the column to it.
  onOpenGroup: (subject: SubjectNode, group: TreeGroupName) => void;
  // The filter text (tree only, W12); the page keeps it so the way back restores it.
  query: string;
  queryPending?: boolean;
  queryProblem?: string;
  onQuery: (query: string) => void;
  // The heading's file count: the leaf's changed files (read 1), "…" while read.
  changedFiles?: number | '…' | 'unavailable';
  // The one "All changes" link and its count (MIK-R89 v2 rule 7).
  allChanges: number | string;
  onOpenAllChanges: () => void;
  // "Code without invariant" (ReviewDestinations.tsx `treeLane`) and its "Full page" links.
  lane: TreeLane;
  onOpenLane: (destination: LaneDestinationName) => void;
  // The Worklist row (MIK-R86 v1 rule 21): its count text, and whether its view is shown.
  worklist: { count: string; open: boolean };
  onOpenWorklist: () => void;
  // Polite lines: the j/k end message (changeTraversal.ts `useBlockTraversal`), the walk's notice,
  // the catalogue's state. Each takes space only while it has text.
  status?: readonly (string | null | undefined)[];
  // The leaf-wide code facts' count, for their notice and the scroll anchor (codeFacts.ts).
  codeFacts?: { count: number | undefined; comparison?: number };
  // Sort and view changes; by default the stored preferences.
  onView?: (view: TreeView) => void;
  // The invariants the latest move changed whose detail has not been read again (D576), by key.
  stale?: ReadonlySet<string>;
  className?: string;
}

function headCounts(model: ChangeSetModel, files: ReviewTreeProps['changedFiles']): string {
  const parts: string[] = [];
  if (model.counts.invariantsChanged !== undefined) parts.push(`${model.counts.invariantsChanged} inv.`);
  if (files === 'unavailable') parts.push('file count unavailable');
  else if (files !== undefined) parts.push(files === 1 ? '1 file' : `${files} files`);
  return parts.join(' · ');
}

function ViewSwitch({ view, enabled, onView }: { view: TreeView; enabled: boolean; onView: (view: TreeView) => void }) {
  return (
    <div className={styles.segment} role="group" aria-label="View" data-testid="review-tree-view">
      {(['changed', 'all'] as const).map((value) => (
        <button
          key={value}
          type="button"
          className={styles.control}
          aria-pressed={view === value}
          disabled={!enabled}
          onClick={() => onView(value)}
        >
          {value === 'changed' ? 'Changed' : 'All'}
        </button>
      ))}
    </div>
  );
}

function rowOf(scroller: HTMLElement | null, key: string): HTMLElement | null {
  return Array.from(scroller?.querySelectorAll<HTMLElement>('[data-row-key]') ?? []).find(
    (element) => element.dataset.rowKey === key,
  ) ?? null;
}

// The row a selection names, for the reveal after a selection made outside the tree. An entry is
// shown by the row of the location that draws it (changeSetOrder.ts `draws`).
function selectionRowKey(model: ChangeSetModel, selection: ReviewSelection | null): string | undefined {
  if (!selection) return undefined;
  if (selection.kind === 'block') {
    if (selection.block.kind !== 'entry') return `lane:${selection.block.path}`;
    const key = blockKey(selection.block);
    const row = model.subjects.flatMap((node) => node.changed).find((location) => location.draws.includes(key));
    return `b:${row?.key ?? key}`;
  }
  if (selection.kind === 'lane') return selection.path === undefined ? undefined : `lane:${selection.path}`;
  if (selection.subject.kind === 'family') return `f:${selection.subject.id}`;
  const node = model.groups
    .flatMap((group) => group.subjects)
    .find((subject) => isCurrentSubject(selection, subject));
  return node ? `s:${node.key}` : undefined;
}

type Select = (key: string, next: ReviewSelection) => void;

function TreeHead({ model, props }: { model: ChangeSetModel; props: ReviewTreeProps }) {
  const onView = props.onView ?? ((view: TreeView) => treeViewStore.getState().setView(view));
  return (
    <>
      <div className={styles.head}>
        <h2 className={styles.heading}>Change set</h2>
        <span className={styles.headCounts} data-testid="review-tree-counts">
          {headCounts(model, props.changedFiles)}
        </span>
        {/* Its count is the total the counts beside it show (pageParts.tsx passes one number for both):
            spoken, not drawn, so the counts stay whole at 1440 (treeStyles.ts `head`). */}
        <button type="button" className={styles.headLink} data-testid="review-all-changes" onClick={props.onOpenAllChanges}>
          All changes<span className={styles.unseen}> {props.allChanges}</span>
        </button>
      </div>
      <div className={styles.tools} data-testid="review-tree-controls">
        <input
          aria-label="Filter the change set"
          data-testid="review-family-filter"
          type="search"
          placeholder="Filter"
          title="Matches id, title, symbol, path, role and effect"
          value={props.query}
          className={styles.input}
          onChange={(event) => props.onQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') props.onQuery('');
          }}
        />
        <ViewSwitch view={model.view} enabled={model.outlined} onView={onView} />
        <SortMenu sorts={model.sorts} sort={model.sort} requested={model.requested} />
      </div>
    </>
  );
}

function SubjectItem({
  item,
  nested,
  selection,
  select,
  onOpenGroup,
  stale,
}: {
  item: FilteredSubject;
  nested: boolean;
  selection: ReviewSelection | null;
  select: Select;
  onOpenGroup: ReviewTreeProps['onOpenGroup'];
  stale?: ReadonlySet<string>;
}) {
  const { node, locations, groups } = item;
  return (
    <li data-testid="review-family-member">
      <SubjectRow
        node={node}
        nested={nested}
        current={isCurrentSubject(selection, node)}
        stale={stale?.has(node.row.selectorId)}
        onSelect={() =>
          select(`s:${node.key}`, {
            kind: 'subject',
            subject: { kind: 'invariant', id: node.row.selectorId },
            ...(node.row.familyId ? { family: node.row.familyId } : {}),
          })
        }
      />
      <ul className={styles.list}>
        {locations.map((location) => (
          <li key={location.key}>
            <LocationRow
              node={location}
              nested={nested}
              current={isCurrentLocation(selection, location)}
              onSelect={() => select(`b:${location.key}`, { kind: 'block', block: location.identity })}
            />
          </li>
        ))}
        <GroupRows node={node} groups={groups} nested={nested} onOpenGroup={onOpenGroup} />
      </ul>
    </li>
  );
}

// "Without a family" follows the shown families (MIK-R86 v1 rule 4, kept by v2 rule 4: "every shown
// family …, then the row 'Without a family'"). With no family shown it would set nothing apart, so it
// is not drawn, as the reading column does (SubjectSection.tsx `labelUnlisted`).
function GroupItem({
  shown,
  labelUnlisted,
  selection,
  select,
  onOpenGroup,
  stale,
}: {
  shown: FilteredGroup;
  labelUnlisted: boolean;
  selection: ReviewSelection | null;
  select: Select;
  onOpenGroup: ReviewTreeProps['onOpenGroup'];
  stale?: ReadonlySet<string>;
}) {
  const family = shown.group.family;
  return (
    <li
      data-testid={family ? 'review-family' : 'review-unlisted'}
      data-family={family?.row.selectorId}
      data-family-kept={family?.kept ? 'true' : undefined}
    >
      {family ? (
        <FamilyRow
          family={family}
          current={isCurrentFamily(selection, family)}
          onSelect={() =>
            select(`f:${family.row.selectorId}`, { kind: 'subject', subject: { kind: 'family', id: family.row.selectorId } })
          }
        />
      ) : labelUnlisted ? (
        <p className={styles.label}>Without a family</p>
      ) : null}
      <ul className={styles.list}>
        {shown.subjects.map((item) => (
          <SubjectItem
            key={item.node.key}
            item={item}
            nested={family !== undefined}
            selection={selection}
            select={select}
            onOpenGroup={onOpenGroup}
            stale={stale}
          />
        ))}
      </ul>
    </li>
  );
}

// D576: how many invariants the latest move changed and are not read again yet -- the marks' own
// set, so the count and the marks cannot disagree. Spoken and drawn only while there is one.
function StaleLine({ stale }: { stale?: ReadonlySet<string> }) {
  const count = stale?.size ?? 0;
  if (!count) return null;
  return (
    <p className={styles.status} role="status" aria-live="polite" data-testid="review-stale-invariants" data-count={count}>
      {`${count} ${count === 1 ? 'invariant' : 'invariants'} changed by the latest move, marked until read again.`}
    </p>
  );
}

// A click leaves the tree where it is; any other selection is revealed by scrollTop alone. The
// returned function marks the row a click selects.
function useRevealSelection(root: RefObject<HTMLElement | null>, target: string | undefined): (key: string) => void {
  const clicked = useRef<string | undefined>(undefined);
  useLayoutEffect(() => {
    const fromClick = clicked.current === target;
    clicked.current = undefined;
    if (target === undefined || fromClick) return;
    revealTreeRow(rowOf(root.current?.querySelector<HTMLElement>(TREE_SCROLL_SELECTOR) ?? null, target));
  }, [root, target]);
  return (key) => {
    clicked.current = key;
  };
}

// MIK-R64 v2 rule 5: the line says once what the code facts made real. A count of 0 made nothing
// real: it is spoken and not drawn (the mockup draws no such line); a count above 0 is drawn.
function CodeFactsLine({ count, comparison }: { count: number | undefined; comparison?: number }) {
  const notice = useCodeFactsNotice(count, comparison);
  const drawn = notice !== '' && (count ?? 0) > 0;
  return (
    <p className={drawn ? styles.status : styles.unseen} aria-live="polite" data-testid="review-code-facts-status">
      {notice}
    </p>
  );
}

function filteredRows(props: ReviewTreeProps) {
  return props.queryPending || props.queryProblem ? { query: props.query, groups: [], empty: false } : filterTree(props.model, props.query);
}
function treeStatus(props: ReviewTreeProps): string {
  const { model } = props;
  return [props.queryProblem, props.queryPending ? 'Reading all locations for this filter…' : undefined,
    sortFallback(model.sorts, model.sort, model.requested), ...(props.status ?? [])].filter(Boolean).join(' ');
}

export function ReviewTree(props: ReviewTreeProps) {
  const { model, selection, onSelect } = props;
  const root = useRef<HTMLElement>(null);
  const shown = filteredRows(props);
  const status = treeStatus(props);
  const clicked = useRevealSelection(root, selectionRowKey(model, selection));
  const families = shown.groups.some((group) => group.group.family !== undefined);
  const select: Select = (key, next) => {
    clicked(key);
    onSelect(next);
  };
  return (
    <aside
      ref={root}
      className={cx(styles.aside, props.className)}
      data-testid="review-tree"
      aria-label="Change set"
      data-tree-order={model.sort}
      data-tree-view={model.view}
    >
      <TreeHead model={model} props={props} />
      {status ? (
        <p className={styles.status} role="status" aria-live="polite" data-testid="review-change-status">
          {status}
        </p>
      ) : null}
      <CodeFactsLine count={props.codeFacts?.count} comparison={props.codeFacts?.comparison} />
      <StaleLine stale={props.stale} />
      <div className={styles.scroller} data-review-tree-panel="" data-testid="review-tree-scroll">
        <CodeFactsScrollAnchor root={root} count={props.codeFacts?.count} comparison={props.codeFacts?.comparison}>
          {model.noChange ? <p className={styles.empty}>This leaf changed no recorded meaning.</p> : null}
          <ul
            className={cx(styles.list, styles.branches)}
            data-testid="review-family-list"
            data-tree-order={model.sort}
            data-tree-view={model.view}
          >
            {shown.groups.map((group) => (
              <GroupItem
                key={group.group.key}
                shown={group}
                labelUnlisted={families}
                selection={selection}
                select={select}
                onOpenGroup={props.onOpenGroup}
                stale={props.stale}
              />
            ))}
          </ul>
          {shown.empty && !props.queryPending ? <p className={styles.empty}>Nothing matches “{props.query}”.</p> : null}
          <TreePinned
            lane={props.lane}
            query={props.query}
            selection={selection}
            onSelect={(next) => select(next.kind === 'lane' && next.path !== undefined ? `lane:${next.path}` : '', next)}
            onOpenLane={props.onOpenLane}
            worklist={props.worklist}
            onOpenWorklist={props.onOpenWorklist}
          />
        </CodeFactsScrollAnchor>
      </div>
    </aside>
  );
}
