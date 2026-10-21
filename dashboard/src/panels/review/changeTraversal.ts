// `j` and `k`: the next and previous block of the reading column, by MIK-R89 v2 rule 13's block
// identity (rule 11; MIK-R86 v2 rule 8). The stops are the drawn changed blocks of the shown subjects
// in display order (changeSetOrder.ts `blocks`), then the lane's hunks, which the reading column
// appends (W21). Locations inside the closed groups are no stops. Between the stops lie the places the
// page shows that hold no stop of their own: a family's heading, a subject's heading, a subject's two
// groups, the lane and each lane file. From a selection the keys go to the stop after or before the
// place the page shows it at, and only from nothing selected to the first block. At either end the
// selection stays and a polite message says so (MIK-R39 rule 11).
//
// The keys are the keymap owner's `review.nextChange`/`review.previousChange` chords (data/keymap):
// they act while focus is inside the review zone, or on body while nothing has focus (a cold page),
// and are routed by the owner's contract, so they are inert in inputs, textareas and contenteditable
// regions and with a modifier the chord does not name. A move only
// selects (`onSelect`); the reading column scrolls to the block and the URL takes the selection.

import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { tinykeys, type KeybindingsMap } from 'tinykeys';

import { bindingFor, useEffectiveKeymap } from '../../data/keymap/preferences';
import { routeKey, zoneForTarget } from '../../data/keymap/zones';
import { blockKey, type ReviewBlockIdentity } from './blockIdentity';
import { drawsItself, type ChangeSetModel, type SubjectNode } from './changeSetOrder';
import type { ReviewSelection } from './reviewSelection';

export type TraversalDirection = 1 | -1;

// One stop: a block, its key, and the subject (invariant selector id) whose locations hold it; a
// lane hunk has no subject. `shares`: the keys of the other entries its card draws (changeSetOrder.ts
// `draws`), which a selection names this stop by too.
export interface BlockStop {
  kind: 'block';
  key: string;
  identity: ReviewBlockIdentity;
  subject?: string;
  shares?: readonly string[];
}

// What a place that holds no stop of its own names: a family's heading, a subject's occurrence (the
// family it is listed under; its first one holds its locations), a subject's two groups (the keys of
// their locations), the lane (no path) or one lane file.
export type TraversalPlace =
  | { kind: 'family'; id: string }
  | { kind: 'subject'; id: string; family?: string; first: boolean }
  | { kind: 'folded'; keys: readonly string[] }
  | { kind: 'lane'; path?: string };

export interface PlaceStop {
  kind: 'place';
  key: string;
  place: TraversalPlace;
}

// The traversal's order: the stops, with the places between them.
export type TraversalStop = BlockStop | PlaceStop;

export type TraversalOutcome =
  | { kind: 'select'; stop: BlockStop }
  | { kind: 'end'; direction: TraversalDirection };

export const REVIEW_ZONE_SELECTOR = '[data-kbzone="review"]';
const TRAVERSAL_COMMANDS: [string, TraversalDirection][] = [
  ['review.nextChange', 1],
  ['review.previousChange', -1],
];

const place = (key: string, named: TraversalPlace): PlaceStop => ({ kind: 'place', key, place: named });
const isBlock = (stop: TraversalStop): stop is BlockStop => stop.kind === 'block';

// A subject occurrence's part of the order: its heading, its drawn changed blocks, then its groups.
function subjectStops(node: SubjectNode): TraversalStop[] {
  const folded = [...node.unchanged, ...node.notComparable].map((location) => location.key);
  const blocks = node.changed.filter(drawsItself).map(
    (location): BlockStop => ({
      kind: 'block',
      key: location.key,
      identity: location.identity,
      subject: location.subject,
      shares: location.draws.slice(1),
    }),
  );
  return [
    place(`s:${node.key}`, { kind: 'subject', id: node.row.selectorId, family: node.row.familyId, first: node.first }),
    ...blocks,
    ...(folded.length ? [place(`g:${node.key}`, { kind: 'folded', keys: folded })] : []),
  ];
}

// The model's part of the order: each family's heading, then its subjects' parts, in display order.
// Its stops are the model's `blocks`. The reading column appends `laneStops`.
export function blockStops(model: Pick<ChangeSetModel, 'groups'>): TraversalStop[] {
  return model.groups.flatMap((group) => {
    const family = group.family?.row.selectorId;
    const heading = family === undefined ? [] : [place(`f:${family}`, { kind: 'family', id: family })];
    return [...heading, ...group.subjects.flatMap(subjectStops)];
  });
}

// The lane's part: its row, then its read hunks. Given the lane's file paths in display order, each
// file's place comes before its hunks, so a file not read yet still has its place.
export function laneStops(hunks: readonly ReviewBlockIdentity[], paths?: readonly string[]): TraversalStop[] {
  const stop = (identity: ReviewBlockIdentity): BlockStop => ({ kind: 'block', key: blockKey(identity), identity });
  const lane = place('lane', { kind: 'lane' });
  if (!paths) return [lane, ...hunks.map(stop)];
  const listed = new Set(paths);
  return [
    lane,
    ...paths.flatMap((path) => [
      place(`lane:${path}`, { kind: 'lane', path }),
      ...hunks.filter((hunk) => hunk.path === path).map(stop),
    ]),
    ...hunks.filter((hunk) => !listed.has(hunk.path)).map(stop),
  ];
}

// Whether a place is where the page shows the selection: a block not among the stops in its
// subject's groups or its lane file, a lane part at its row, a subject at its occurrence under the
// family it names (else its first), a family at its heading.
function shownAt(named: TraversalPlace, selection: ReviewSelection): boolean {
  if (selection.kind === 'block') return blockShownAt(named, selection.block);
  if (selection.kind === 'lane') return named.kind === 'lane' && named.path === selection.path;
  return subjectShownAt(named, selection);
}

function blockShownAt(named: TraversalPlace, block: ReviewBlockIdentity): boolean {
  if (named.kind === 'folded') return named.keys.includes(blockKey(block));
  return named.kind === 'lane' && block.kind === 'hunk' && named.path === block.path;
}

function subjectShownAt(named: TraversalPlace, selection: Extract<ReviewSelection, { kind: 'subject' }>): boolean {
  if (selection.subject.kind === 'family') return named.kind === 'family' && named.id === selection.subject.id;
  if (named.kind !== 'subject' || named.id !== selection.subject.id) return false;
  return selection.family === undefined ? named.first : selection.family === named.family;
}

// The lane file a selection names, if any.
function lanePath(selection: ReviewSelection): string | undefined {
  if (selection.kind === 'lane') return selection.path;
  return selection.kind === 'block' && selection.block.kind === 'hunk' ? selection.block.path : undefined;
}

// Where a selection sits in the order: on its stop (a block, or an entry its stop's card draws), at its
// place, or half a step before the first hunk of a lane file that has no place of its own (at the
// lane's row when none of its hunks is read either). A selection the order holds nowhere sits at the
// column's head, before every stop (a subject drawn in no section stands there).
function positionOf(stops: readonly TraversalStop[], selection: ReviewSelection): number {
  const key = selection.kind === 'block' ? blockKey(selection.block) : undefined;
  const on = stops.findIndex((stop) => isBlock(stop) && key !== undefined && (stop.key === key || stop.shares?.includes(key)));
  if (on !== -1) return on;
  const at = stops.findIndex((stop) => !isBlock(stop) && shownAt(stop.place, selection));
  if (at !== -1) return at;
  const path = lanePath(selection);
  if (path !== undefined) {
    const first = stops.findIndex((stop) => isBlock(stop) && stop.identity.kind === 'hunk' && stop.identity.path === path);
    if (first !== -1) return first - 0.5;
    const lane = stops.findIndex((stop) => !isBlock(stop) && stop.place.kind === 'lane' && stop.place.path === undefined);
    if (lane !== -1) return lane;
  }
  return -0.5;
}

// The next stop from the selection, in display order. From no selection both keys select the first
// block (the mockup's clamp, app.js:695); from any other the stop after or before its position.
export function nextStop(
  stops: readonly TraversalStop[],
  selection: ReviewSelection | null,
  direction: TraversalDirection,
): TraversalOutcome {
  const first = stops.find(isBlock);
  if (!first) return { kind: 'end', direction };
  if (selection === null) return { kind: 'select', stop: first };
  const from = positionOf(stops, selection);
  const start = direction === 1 ? Math.floor(from) + 1 : Math.ceil(from) - 1;
  for (let index = start; index >= 0 && index < stops.length; index += direction) {
    const stop = stops[index];
    if (isBlock(stop)) return { kind: 'select', stop };
  }
  return { kind: 'end', direction };
}

export function outcomeMessage(outcome: TraversalOutcome): string {
  if (outcome.kind === 'select') return '';
  return outcome.direction === 1
    ? 'No later block; the selection stays.'
    : 'No earlier block; the selection stays.';
}

// The status of the last move, kept with the stops it was said for (INV-CK3PTB). `rows` names the
// stops (their keys, in order). A status answers one move and speaks of the stops as they were then,
// so the render gives a status out only while the same stops are shown, and once other stops have
// been shown the status is dropped for good. Nothing is updated while rendering.
function useStatusFor(rows: string): [string, (text: string) => void] {
  const [said, setSaid] = useState({ rows, text: '' });
  const shown = useRef(rows);
  useLayoutEffect(() => {
    shown.current = rows;
    setSaid((last) => (last.text === '' ? last : { rows, text: '' }));
  }, [rows]);
  const setStatus = useCallback((text: string) => {
    const next = { rows: shown.current, text };
    setSaid((last) => (last.rows === next.rows && last.text === next.text ? last : next));
  }, []);
  return [said.rows === rows ? said.text : '', setStatus];
}

// A key with no focused element: the page's body (a cold page) or the document itself.
function coldTarget(target: Element | null): boolean {
  return target === null || target === document.body || target === document.documentElement;
}

// Mounted by the review page. `zone` is the review zone's element or any element inside it; the
// chords act inside the enclosing `[data-kbzone="review"]`, and from body while nothing has focus.
export function useBlockTraversal({
  zone,
  stops,
  selection,
  onSelect,
  enabled,
}: {
  zone: RefObject<HTMLElement | null>;
  stops: readonly TraversalStop[];
  selection: ReviewSelection | null;
  onSelect: (selection: ReviewSelection) => void;
  enabled: boolean;
}): { move: (direction: TraversalDirection) => void; status: string } {
  const [status, setStatus] = useStatusFor(stops.filter(isBlock).map((stop) => stop.key).join('\n'));
  const latest = useRef({ stops, selection, onSelect });
  useLayoutEffect(() => {
    latest.current = { stops, selection, onSelect };
  });
  const move = useCallback(
    (direction: TraversalDirection) => {
      const current = latest.current;
      const outcome = nextStop(current.stops, current.selection, direction);
      setStatus(outcomeMessage(outcome));
      if (outcome.kind === 'select') current.onSelect({ kind: 'block', block: outcome.stop.identity });
    },
    [setStatus],
  );
  const keymap = useEffectiveKeymap();
  useEffect(() => {
    const node = zone.current;
    const root = node?.closest<HTMLElement>(REVIEW_ZONE_SELECTOR) ?? node;
    if (!enabled || !root) return undefined;
    const map: KeybindingsMap = {};
    for (const [commandId, direction] of TRAVERSAL_COMMANDS) {
      const binding = bindingFor(keymap, commandId);
      if (!binding) continue;
      map[binding.chord] = (event) => {
        const target = event.target as Element | null;
        const cold = coldTarget(target);
        if (!cold && !(root.contains(target) && binding.zones.includes(zoneForTarget(target)))) return;
        if (routeKey(event, target) !== 'handle') return;
        event.preventDefault();
        move(direction);
      };
    }
    // Bound on the window, so the keys also act on a cold page whose focus is on body (as `[`/`]` do);
    // anywhere else outside the review zone they do nothing.
    return tinykeys(window, map, { ignore: () => false });
  }, [enabled, keymap, move, zone]);
  return { move, status };
}
